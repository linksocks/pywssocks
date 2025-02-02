from typing import Optional
import asyncio
import socket
import json
import logging
from urllib.parse import urlparse, urlunparse

import click
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.client import ClientConnection, connect

from pywssocks.common import init_logging
from pywssocks.relay import Relay
from pywssocks import __version__

logger = logging.getLogger(__name__)


class WSSocksClient(Relay):
    def __init__(
        self,
        token: str,
        ws_url="ws://localhost:8765",
        reverse: bool = False,
        socks_host: str = "127.0.0.1",
        socks_port: int = 1080,
        socks_username: Optional[str] = None,
        socks_password: Optional[str] = None,
        **kw,
    ) -> None:
        """Initialize WebSocket SOCKS5 client

        Args:
            ws_url: WebSocket server address
            token: Authentication token
            socks_host: Local SOCKS5 server listen address
            socks_port: Local SOCKS5 server listen port
            socks_username: SOCKS5 authentication username
            socks_password: SOCKS5 authentication password
        """
        super().__init__(**kw)

        self._ws_url: str = self.convert_ws_path(ws_url)
        self._token: str = token
        self._reverse: bool = reverse

        self._socks_host: str = socks_host
        self._socks_port: int = socks_port
        self._socks_username: Optional[str] = socks_username
        self._socks_password: Optional[str] = socks_password

        self._socks_server: Optional[socket.socket] = None
        self._websocket: Optional[ClientConnection] = None

    def convert_ws_path(self, url: str) -> str:
        # Process ws_url
        parsed = urlparse(url)
        # Convert http(s) to ws(s)
        scheme = parsed.scheme
        if scheme == "http":
            scheme = "ws"
        elif scheme == "https":
            scheme = "wss"

        # Add default path if not present or only has trailing slash
        path = parsed.path
        if not path or path == "/":
            path = "/socket"

        return urlunparse(
            (scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
        )

    async def _message_dispatcher(self, websocket: ClientConnection) -> None:
        """Global WebSocket message dispatcher"""

        try:
            while True:
                msg = await websocket.recv()
                data = json.loads(msg)
                if data["type"] == "data":
                    channel_id = data.get("channel_id", None)
                    connect_id = data.get("connect_id", None)
                    if channel_id and channel_id in self._message_queues:
                        await self._message_queues[channel_id].put(data)
                    else:
                        logger.warning(f"Received data for unknown channel: {channel_id}")
                elif data["type"] == "connect":
                    logger.debug(f"Received network connection request: {data}")
                    asyncio.create_task(self._handle_network_connection(websocket, data))
                elif data["type"] == "connect_response":
                    logger.debug(f"Received network connection response: {data}")
                    connect_id = data["connect_id"]
                    if connect_id in self._message_queues:
                        await self._message_queues[connect_id].put(data)
                else:
                    logger.warning(f"Received unknown message type: {data['type']}.")
        except ConnectionClosed:
            logger.error("WebSocket connection closed.")
        except Exception as e:
            logger.error(f"Message dispatcher error: {e.__class__.__name__}: {e}.")

    async def _run_socks_server(self) -> None:
        """Run local SOCKS5 server"""
        
        if self._socks_server:
            return
        
        try:
            socks_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            socks_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            socks_server.bind((self._socks_host, self._socks_port))
            socks_server.listen(5)
            socks_server.setblocking(False)

            self._socks_server = socks_server
            logger.info(f"SOCKS5 server started on {self._socks_host}:{self._socks_port}")

            loop = asyncio.get_event_loop()
            while True:
                try:
                    client_sock, addr = await loop.sock_accept(socks_server)
                    logger.debug(f"Accepted SOCKS5 connection from {addr}")
                    asyncio.create_task(self._handle_socks_request(client_sock))
                except Exception as e:
                    logger.error(f"Error accepting SOCKS connection: {e}")
                    await asyncio.sleep(0.1)
        
        except Exception as e:
            logger.error(f"SOCKS server error: {e}")
        finally:
            if self._socks_server:
                self._socks_server.close()

    async def _handle_socks_request(self, socks_socket: socket.socket) -> None:
        """Handle SOCKS5 client request"""

        loop = asyncio.get_event_loop()
        wait_start = loop.time()
        while loop.time() - wait_start < 10:
            if self._websocket:
                await super()._handle_socks_request(
                    self._websocket, socks_socket, self._socks_username, self._socks_password
                )
                break
            await asyncio.sleep(0.1)
        else:
            logger.debug(
                f"No valid websockets connection after waiting 10s, refusing socks request."
            )
            await self._refuse_socks_request(socks_socket)
            return

    async def _start_forward(self) -> None:
        """Connect to WebSocket server in forward proxy mode"""

        try:
            asyncio.create_task(self._run_socks_server())
            while True:
                try:
                    async with connect(self._ws_url) as websocket:
                        self._websocket = websocket

                        await websocket.send(
                            json.dumps({"type": "auth", "reverse": False, "token": self._token})
                        )
                        auth_response = await websocket.recv()
                        auth_data = json.loads(auth_response)

                        if not auth_data.get("success"):
                            logger.error("Authentication failed.")
                            return

                        logger.info("Authentication successful for forward proxy.")

                        tasks = [
                            asyncio.create_task(self._message_dispatcher(websocket)),
                            asyncio.create_task(self._heartbeat_handler(websocket)),
                        ]

                        try:
                            done, pending = await asyncio.wait(
                                tasks, return_when=asyncio.FIRST_COMPLETED
                            )
                            for task in pending:
                                task.cancel()
                        finally:
                            for task in tasks:
                                if not task.done():
                                    task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                except ConnectionClosed:
                    logger.error("WebSocket connection closed. Retrying in 5 seconds...")
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error(
                        f"Connection error: {e.__class__.__name__}: {e}. Retrying in 5 seconds..."
                    )
                    await asyncio.sleep(5)
                finally:
                    self._websocket = None
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")

    async def _start_reverse(self) -> None:
        """Connect to WebSocket server in reverse proxy mode"""

        try:
            while True:
                try:
                    async with connect(self._ws_url) as websocket:
                        # Send authentication information
                        await websocket.send(
                            json.dumps({"type": "auth", "reverse": True, "token": self._token})
                        )

                        # Wait for authentication response
                        auth_response = await websocket.recv()
                        auth_data = json.loads(auth_response)

                        if not auth_data.get("success"):
                            logger.error("Authentication failed.")
                            return

                        logger.info("Authentication successful for reverse proxy.")

                        # Start message dispatcher and heartbeat tasks
                        tasks = [
                            asyncio.create_task(self._message_dispatcher(websocket)),
                            asyncio.create_task(self._heartbeat_handler(websocket)),
                        ]

                        # Wait for first task to complete (may be due to error or connection close)
                        done, pending = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED
                        )

                        # Cancel other running tasks
                        for task in pending:
                            task.cancel()

                        # Wait for cancelled tasks to complete
                        await asyncio.gather(*pending, return_exceptions=True)

                        # Check if any tasks threw exceptions
                        for task in done:
                            try:
                                task.result()
                            except Exception as e:
                                logger.error(
                                    f"Task failed with error: {e.__class__.__name__}: {e}."
                                )

                except ConnectionClosed:
                    logger.error("WebSocket connection closed. Retrying in 5 seconds...")
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error(
                        f"Connection error: {e.__class__.__name__}: {e}. Retrying in 5 seconds..."
                    )
                    await asyncio.sleep(5)

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
            return

    async def _heartbeat_handler(self, websocket: ClientConnection) -> None:
        """Handle WebSocket heartbeat"""

        try:
            while True:
                try:
                    # Wait for server ping
                    pong_waiter = await websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=10)
                    logger.debug("Heartbeat: Sent ping, received pong.")
                except asyncio.TimeoutError:
                    logger.warning("Heartbeat: Pong timeout.")
                    break
                except ConnectionClosed:
                    logger.warning("WebSocket connection closed, stopping heartbeat.")
                    break
                except Exception as e:
                    logger.error(f"Heartbeat error: {e.__class__.__name__}: {e}.")
                    break

                # Wait 30 seconds before sending next heartbeat
                await asyncio.sleep(30)

        except Exception as e:
            logger.error(f"Heartbeat handler error: {e.__class__.__name__}: {e}.")
        finally:
            # Ensure logging when heartbeat handler exits
            logger.info("Heartbeat handler stopped.")

    async def start(self):
        """Start client"""
        logger.info(f"Pywssocks Client {__version__} is connecting: {self._ws_url}")
        if self._reverse:
            await self._start_reverse()
        else:
            await self._start_forward()


@click.command()
@click.option("--token", "-t", required=True, help="Authentication token")
@click.option("--url", "-u", default="ws://localhost:8765", help="WebSocket server address")
@click.option("--reverse", "-r", is_flag=True, default=False, help="Use reverse socks5 proxy")
@click.option(
    "--socks-host",
    "-h",
    default="127.0.0.1",
    help="SOCKS5 server listen address for forward proxy",
)
@click.option(
    "--socks-port",
    "-p",
    default=1080,
    help="SOCKS5 server listen port for forward proxy, auto-generate if not provided",
)
@click.option("--socks-username", "-n", help="SOCKS5 authentication username")
@click.option("--socks-password", "-w", help="SOCKS5 authentication password")
@click.option("--debug", "-d", is_flag=True, default=False, help="Show debug logs")
def _client_cli(
    token: str,
    url: str,
    reverse: bool,
    socks_host: str,
    socks_port: int,
    socks_username: Optional[str],
    socks_password: Optional[str],
    debug: bool,
):
    """Start SOCKS5 over WebSocket proxy client"""

    init_logging(level=logging.DEBUG if debug else logging.INFO)

    # Start server
    client = WSSocksClient(
        ws_url=url,
        token=token,
        reverse=reverse,
        socks_host=socks_host,
        socks_port=socks_port,
        socks_username=socks_username,
        socks_password=socks_password,
    )
    asyncio.run(client.start())


if __name__ == "__main__":
    _client_cli()
