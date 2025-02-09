from typing import Optional
import asyncio
import socket
import json
import logging
from urllib.parse import urlparse, urlunparse

from websockets.exceptions import ConnectionClosed
from websockets.asyncio.client import ClientConnection, connect

from pywssocks.relay import Relay
from pywssocks import __version__


class WSSocksClient(Relay):
    """
    A SOCKS5 over WebSocket protocol client.

    In reverse proxy mode, it will receive requests from the client, access the network
    as requested, and return the results to the server.

    In forward proxy mode, it will receive SOCKS5 requests and send them to the connected
    server for parsing via WebSocket.
    """

    def __init__(
        self,
        token: str,
        ws_url: str = "ws://localhost:8765",
        reverse: bool = False,
        socks_host: str = "127.0.0.1",
        socks_port: int = 1080,
        socks_username: Optional[str] = None,
        socks_password: Optional[str] = None,
        socks_wait_server: bool = True,
        reconnect: bool = True,
        reconnect_interval: float = 5,
        logger: Optional[logging.Logger] = None,
        **kw,
    ) -> None:
        """
        Args:
            ws_url: WebSocket server address
            token: Authentication token
            socks_host: Local SOCKS5 server listen address
            socks_port: Local SOCKS5 server listen port
            socks_username: SOCKS5 authentication username
            socks_password: SOCKS5 authentication password
            socks_wait_server: Wait for server connection before starting the SOCKS server,
                otherwise start the SOCKS server when the client starts
            reconnect: Automatically reconnect to the server
            reconnect_interval: Interval between reconnection trials
            logger: Custom logger instance
        """
        super().__init__(logger=logger, **kw)

        self._ws_url: str = self._convert_ws_path(ws_url)
        self._token: str = token
        self._reverse: bool = reverse
        self._reconnect: bool = reconnect
        self._reconnect_interval: float = reconnect_interval

        self._socks_host: str = socks_host
        self._socks_port: int = socks_port
        self._socks_username: Optional[str] = socks_username
        self._socks_password: Optional[str] = socks_password
        self._socks_wait_server: bool = socks_wait_server

        self._socks_server: Optional[socket.socket] = None
        self._websocket: Optional[ClientConnection] = None

        self.connected = asyncio.Event()
        self.disconnected = asyncio.Event()

    async def wait_ready(self, timeout: Optional[float] = None) -> asyncio.Task:
        """Start the client and connect to the server within the specified timeout, then returns the Task."""

        task = asyncio.create_task(self.connect())
        if timeout:
            await asyncio.wait_for(self.connected.wait(), timeout=timeout)
        else:
            await self.connected.wait()
        return task

    async def connect(self) -> None:
        """
        Start the client and connect to the server.

        This function will execute until the client is terminated.
        """
        self._log.info(
            f"Pywssocks Client {__version__} is connecting to: {self._ws_url}"
        )
        if self._reverse:
            await self._start_reverse()
        else:
            await self._start_forward()

    def _convert_ws_path(self, url: str) -> str:
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
                        self._log.warning(
                            f"Received data for unknown channel: {channel_id}"
                        )
                elif data["type"] == "connect":
                    self._log.debug(f"Received network connection request: {data}")
                    asyncio.create_task(
                        self._handle_network_connection(websocket, data)
                    )
                elif data["type"] == "connect_response":
                    self._log.debug(f"Received network connection response: {data}")
                    connect_id = data["connect_id"]
                    if connect_id in self._message_queues:
                        await self._message_queues[connect_id].put(data)
                else:
                    self._log.warning(f"Received unknown message type: {data['type']}.")
        except ConnectionClosed:
            self._log.error("WebSocket connection closed.")
        except Exception as e:
            self._log.error(f"Message dispatcher error: {e.__class__.__name__}: {e}.")

    async def _run_socks_server(
        self, ready_event: Optional[asyncio.Event] = None
    ) -> None:
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
            self._log.info(
                f"SOCKS5 server started on {self._socks_host}:{self._socks_port}"
            )

            loop = asyncio.get_event_loop()
            if ready_event:
                ready_event.set()
            while True:
                try:
                    client_sock, addr = await loop.sock_accept(socks_server)
                    self._log.debug(f"Accepted SOCKS5 connection from {addr}")
                    asyncio.create_task(self._handle_socks_request(client_sock))
                except Exception as e:
                    self._log.error(
                        f"Error accepting SOCKS connection: {e.__class__.__name__}: {e}"
                    )
        except Exception as e:
            self._log.error(f"SOCKS server error: {e.__class__.__name__}: {e}")
        finally:
            if self._socks_server:
                try:
                    loop.remove_reader(self._socks_server.fileno())
                except:
                    pass
                self._socks_server.close()

    async def _handle_socks_request(self, socks_socket: socket.socket) -> None:
        """Handle SOCKS5 client request"""

        loop = asyncio.get_event_loop()
        wait_start = loop.time()
        while loop.time() - wait_start < 10:
            if self._websocket:
                await super()._handle_socks_request(
                    self._websocket,
                    socks_socket,
                    self._socks_username,
                    self._socks_password,
                )
                break
            await asyncio.sleep(0.1)
        else:
            self._log.debug(
                f"No valid websockets connection after waiting 10s, refusing socks request."
            )
            await self._refuse_socks_request(socks_socket)
            return

    async def _start_forward(self) -> None:
        """Connect to WebSocket server in forward proxy mode"""

        try:
            if not self._socks_wait_server:
                asyncio.create_task(self._run_socks_server())
            while True:
                try:
                    async with connect(
                        self._ws_url, logger=self._log.getChild("ws")
                    ) as websocket:
                        self._websocket = websocket

                        socks_ready = asyncio.Event()
                        socks_server_task = asyncio.create_task(
                            self._run_socks_server(ready_event=socks_ready)
                        )

                        await websocket.send(
                            json.dumps(
                                {"type": "auth", "reverse": False, "token": self._token}
                            )
                        )
                        auth_response = await websocket.recv()
                        auth_data = json.loads(auth_response)

                        if not auth_data.get("success"):
                            self._log.error("Authentication failed.")
                            return

                        self._log.info("Authentication successful for forward proxy.")

                        # Wait for either socks server to be ready or to fail
                        done, _ = await asyncio.wait(
                            [
                                asyncio.create_task(socks_ready.wait()),
                                socks_server_task,
                            ],
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        # Check if socks server task failed
                        if socks_server_task in done:
                            socks_server_task.result()  # This will raise any exception that occurred

                        tasks = [
                            asyncio.create_task(self._message_dispatcher(websocket)),
                            asyncio.create_task(self._heartbeat_handler(websocket)),
                        ]

                        self.connected.set()
                        self.disconnected.clear()

                        try:
                            done, pending = await asyncio.wait(
                                tasks, return_when=asyncio.FIRST_COMPLETED
                            )
                            for task in done:
                                try:
                                    task.result()
                                except Exception as e:
                                    if not isinstance(e, asyncio.CancelledError):
                                        self._log.error(
                                            f"Task failed with error: {e.__class__.__name__}: {e}."
                                        )
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                except ConnectionClosed:
                    if self._reconnect:
                        self._log.error(
                            "WebSocket connection closed. Retrying in 5 seconds..."
                        )
                        await asyncio.sleep(self._reconnect_interval)
                    else:
                        self._log.error("WebSocket connection closed. Exiting...")
                        break
                except Exception as e:
                    if self._reconnect:
                        self._log.error(
                            f"Connection error: {e.__class__.__name__}: {e}. Retrying in 5 seconds..."
                        )
                        await asyncio.sleep(self._reconnect_interval)
                    else:
                        self._log.error(
                            f"Connection error: {e.__class__.__name__}: {e}. Exiting..."
                        )
                        break
                finally:
                    self._websocket = None
                    self.connected.clear()
                    self.disconnected.set()
        except KeyboardInterrupt:
            self._log.info("Received keyboard interrupt, shutting down...")
            return

    async def _start_reverse(self) -> None:
        """Connect to WebSocket server in reverse proxy mode"""

        try:
            while True:
                try:
                    async with connect(
                        self._ws_url, logger=self._log.getChild("ws")
                    ) as websocket:
                        # Send authentication information
                        await websocket.send(
                            json.dumps(
                                {"type": "auth", "reverse": True, "token": self._token}
                            )
                        )

                        # Wait for authentication response
                        auth_response = await websocket.recv()
                        auth_data = json.loads(auth_response)

                        if not auth_data.get("success"):
                            self._log.error("Authentication failed.")
                            return

                        self._log.info("Authentication successful for reverse proxy.")

                        # Start message dispatcher and heartbeat tasks
                        tasks = [
                            asyncio.create_task(self._message_dispatcher(websocket)),
                            asyncio.create_task(self._heartbeat_handler(websocket)),
                        ]

                        self.connected.set()
                        self.disconnected.clear()

                        try:
                            done, pending = await asyncio.wait(
                                tasks, return_when=asyncio.FIRST_COMPLETED
                            )
                            for task in done:
                                try:
                                    task.result()
                                except Exception as e:
                                    if not isinstance(e, asyncio.CancelledError):
                                        self._log.error(
                                            f"Task failed with error: {e.__class__.__name__}: {e}."
                                        )
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)

                except ConnectionClosed:
                    if self._reconnect:
                        self._log.error(
                            "WebSocket connection closed. Retrying in 5 seconds..."
                        )
                        await asyncio.sleep(self._reconnect_interval)
                    else:
                        self._log.error("WebSocket connection closed. Exiting...")
                        break
                except Exception as e:
                    if self._reconnect:
                        self._log.error(
                            f"Connection error: {e.__class__.__name__}: {e}. Retrying in 5 seconds..."
                        )
                        await asyncio.sleep(self._reconnect_interval)
                    else:
                        self._log.error(
                            f"Connection error: {e.__class__.__name__}: {e}. Exiting..."
                        )
                        break
                finally:
                    self.connected.clear()
                    self.disconnected.set()

        except KeyboardInterrupt:
            self._log.info("Received keyboard interrupt, shutting down...")
            return

    async def _heartbeat_handler(self, websocket: ClientConnection) -> None:
        """Handle WebSocket heartbeat"""

        try:
            while True:
                try:
                    # Wait for server ping
                    pong_waiter = await websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=10)
                    self._log.debug("Heartbeat: Sent ping, received pong.")
                except asyncio.TimeoutError:
                    self._log.warning("Heartbeat: Pong timeout.")
                    break
                except ConnectionClosed:
                    self._log.warning(
                        "WebSocket connection closed, stopping heartbeat."
                    )
                    break
                except Exception as e:
                    self._log.error(f"Heartbeat error: {e.__class__.__name__}: {e}.")
                    break

                # Wait 30 seconds before sending next heartbeat
                await asyncio.sleep(30)

        except Exception as e:
            self._log.error(f"Heartbeat handler error: {e.__class__.__name__}: {e}.")
        finally:
            # Ensure logging when heartbeat handler exits
            self._log.debug("Heartbeat handler stopped.")
