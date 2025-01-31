from typing import Optional, Tuple, Union
import logging
import asyncio
import json
import struct
import socket
import random
import string
import uuid

from websockets.exceptions import ConnectionClosed
from websockets.asyncio.server import ServerConnection, serve
import click

from pywssocks.common import PortPool, init_logging
from pywssocks.relay import Relay

logger = logging.getLogger(__name__)


class WSSocksServer(Relay):
    def __init__(
        self,
        ws_host: str = "0.0.0.0",
        ws_port: int = 8765,
        socks_host: str = "127.0.0.1",
        socks_port_pool: Union[PortPool, range] = range(1024, 10240),
        **kw,
    ) -> None:
        """Initialize WebSocket SOCKS5 Server

        Args:
            ws_host: WebSocket listen address
            ws_port: WebSocket listen port
            socks_host: SOCKS5 listen address for reverse proxy
            socks_port_pool: SOCKS5 port pool for reverse proxy
        """

        super().__init__(**kw)

        self._ws_host = ws_host
        self._ws_port = ws_port
        self._socks_host = socks_host

        if isinstance(socks_port_pool, PortPool):
            self._socks_port_pool = socks_port_pool
        else:
            self._socks_port_pool = PortPool(socks_port_pool)

        # Store all connected reverse proxy clients, {client_id: websocket_connection}
        self._clients: dict[int, ServerConnection] = {}

        # Group reverse proxy clients by token, {token: list of (client_id, websocket) tuples}
        self._token_clients: dict[str, list[tuple[int, ServerConnection]]] = {}

        # Store current round-robin index for each reverse proxy token for load balancing, {token: current_index}
        self._token_indexes: dict[str, int] = {}

        # Map reverse proxy tokens to their assigned SOCKS5 ports, {token: socks_port}
        self._tokens: dict[str, int] = {}

        # Store all running SOCKS5 server instances, {socks_port: socks_socket}
        self._socks_servers: dict[int, socket.socket] = {}

        # Message channels for receiving and routing from WebSocket, {channel_id: Queue}
        self._message_queues: dict[str, asyncio.Queue] = {}

        # Store SOCKS5 auth credentials, {token: (username, password)}
        self._socks_auth: dict[str, tuple[str, str]] = {}

        # Store SOCKS5 related async tasks, {socks_port: Task}
        self._socks_tasks: dict[int, asyncio.Task] = {}

        # Store tokens for forward proxy
        self._forward_tokens = set()

        # Store all connected WebSocket clients, {client_id: websocket_connection}
        self._forward_clients: dict[int, ServerConnection] = {}

    def add_reverse_token(
        self,
        token: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Union[Tuple[str, int], Tuple[None, None]]:
        """Add a new token for reverse socks and assign a port

        Args:
            token: Auth token, auto-generated if None
            port: Specific port to use, if None will allocate from port range
            username: SOCKS5 username, no auth if None
            password: SOCKS5 password, no auth if None

        Returns:
            (token, port) tuple containing the token and assigned SOCKS5 port
            Returns (None, None) if no ports available or port already in use
        """
        # If token is None, generate a random token
        if token is None:
            chars = string.ascii_letters + string.digits
            token = "".join(random.choice(chars) for _ in range(16))

        if token in self._tokens:
            return token, self._tokens[token]

        port = self._socks_port_pool.get(port)
        if port:
            self._tokens[token] = port
            if username is not None and password is not None:
                self._socks_auth[token] = (username, password)
            return token, port
        else:
            return None, None

    def add_forward_token(self, token: Optional[str] = None) -> str:
        """Add a new token for forward socks proxy

        Args:
            token: Auth token, auto-generated if None

        Returns:
            token string
        """
        if token is None:
            chars = string.ascii_letters + string.digits
            token = "".join(random.choice(chars) for _ in range(16))

        self._forward_tokens.add(token)
        return token

    def _get_next_websocket(self, token: str) -> Optional[ServerConnection]:
        """Get next available WebSocket connection using round-robin"""

        if token not in self._token_clients or not self._token_clients[token]:
            return None

        clients = self._token_clients[token]
        if not clients:
            return None

        current_index = self._token_indexes.get(token, 0)
        self._token_indexes[token] = (current_index + 1) % len(clients)

        return clients[current_index][1]

    async def _handle_socks_request(self, socks_socket: socket.socket, token: str) -> None:
        # Use round-robin to get websocket connection
        websocket = self._get_next_websocket(token)
        if not websocket:
            logger.warning(f"No available client for SOCKS5 port {self._tokens[token]}.")
            socks_socket.close()
            return
        auth = self._socks_auth.get(token, None)
        if auth:
            socks_username, socks_password = auth
        else:
            socks_username = socks_password = None
        return await super()._handle_socks_request(
            websocket, socks_socket, socks_username, socks_password
        )

    async def _handle_websocket(self, websocket: ServerConnection) -> None:
        """Handle WebSocket connection"""
        client_id = None
        token = None
        socks_port = None
        try:
            auth_message = await websocket.recv()
            auth_data = json.loads(auth_message)

            if auth_data.get("type", None) != "auth":
                await websocket.close(1008, "Invalid auth message")
                return

            token = auth_data.get("token", None)
            reverse = auth_data.get("reverse", None)

            # Validate token
            if reverse == True and token in self._tokens:  # reverse proxy
                socks_port = self._tokens[token]
                client_id = id(websocket)

                if token not in self._token_clients:
                    self._token_clients[token] = []
                self._token_clients[token].append((client_id, websocket))
                self._clients[client_id] = websocket

                await websocket.send(json.dumps({"type": "auth_response", "success": True}))
                logger.info(f"Reverse client {client_id} authenticated")

                # Ensure SOCKS server is running
                if socks_port not in self._socks_servers:
                    asyncio.create_task(self._run_socks_server(token, socks_port))

            elif reverse == False and token in self._forward_tokens:  # forward proxy
                client_id = id(websocket)
                self._forward_clients[client_id] = websocket

                await websocket.send(json.dumps({"type": "auth_response", "success": True}))
                logger.info(f"Forward client {client_id} authenticated")

            else:
                await websocket.send(json.dumps({"type": "auth_response", "success": False}))
                await websocket.close(1008, "Invalid token")
                return

            receiver_task = asyncio.create_task(self._message_dispatcher(websocket, client_id))
            heartbeat_task = asyncio.create_task(self._ws_heartbeat(websocket, client_id))

            done, pending = await asyncio.wait(
                [receiver_task, heartbeat_task], return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except ConnectionClosed:
            logger.info(f"Client {client_id} disconnected.")
        except Exception as e:
            logger.error(f"WebSocket processing error: {e.__class__.__name__}: {e}.")
        finally:
            await self._cleanup_connection(client_id, token)

    async def _cleanup_connection(self, client_id: Optional[int], token: Optional[str]) -> None:
        """Clean up resources without closing SOCKS server"""

        if not client_id or not token:
            return

        # Clean up connection in token_clients
        if token in self._token_clients:
            self._token_clients[token] = [
                (cid, ws) for cid, ws in self._token_clients[token] if cid != client_id
            ]

            # Clean up resources if no connections left for this token
            if not self._token_clients[token]:
                del self._token_clients[token]
                if token in self._token_indexes:
                    del self._token_indexes[token]

        # Clean up client connection
        if client_id in self._clients:
            del self._clients[client_id]

        # Clean up related message queues
        queues_to_remove = [
            queue_id for queue_id in self._message_queues if queue_id.startswith(f"{client_id}_")
        ]
        for queue_id in queues_to_remove:
            del self._message_queues[queue_id]

        logger.debug(f"Cleaned up resources for client {client_id}.")

    async def _ws_heartbeat(self, websocket: ServerConnection, client_id: int) -> None:
        """WebSocket heartbeat check"""
        try:
            while True:
                try:
                    # Send ping every 30 seconds
                    await websocket.ping()
                    await asyncio.sleep(30)
                except ConnectionClosed:
                    logger.info(f"Heartbeat detected disconnection for client {client_id}.")
                    break
                except Exception as e:
                    logger.error(f"Heartbeat error for client {client_id}: {e}")
                    break
        finally:
            # Ensure WebSocket is closed
            try:
                await websocket.close()
            except:
                pass

    async def _message_dispatcher(self, websocket: ServerConnection, client_id: int) -> None:
        """WebSocket message receiver distributing messages to different message queues"""

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=60)  # 60 seconds timeout
                    data = json.loads(msg)

                    if data["type"] == "data":
                        channel_id = data["channel_id"]
                        logger.debug(f"Received data for channel: {channel_id}")
                        if channel_id in self._message_queues:
                            await self._message_queues[channel_id].put(data)
                        else:
                            logger.debug(f"Received data for unknown channel: {channel_id}")
                    elif data["type"] == "connect_response":
                        logger.debug(f"Received network connection response: {data}")
                        connect_id = data["connect_id"]
                        if connect_id in self._message_queues:
                            await self._message_queues[connect_id].put(data)
                    elif data["type"] == "connect" and client_id in self._forward_clients:
                        logger.debug(f"Received network connection request: {data}")
                        asyncio.create_task(self._handle_network_connection(websocket, data))
                except asyncio.TimeoutError:
                    # If 60 seconds pass without receiving messages, check if connection is still alive
                    try:
                        await websocket.ping()
                    except:
                        logger.warning(f"Connection timeout for client {client_id}")
                        break
                except ConnectionClosed:
                    logger.info(f"Client {client_id} connection closed.")
                    break
        except Exception as e:
            logger.error(
                f"WebSocket receive error for client {client_id}: {e.__class__.__name__}: {e}."
            )

    async def _run_socks_server(self, token: str, socks_port: int) -> None:
        """Modified SOCKS server startup function"""

        # If server is already running, return immediately
        if socks_port in self._socks_servers:
            return

        socks_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        socks_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        socks_server.bind((self._socks_host, socks_port))
        socks_server.listen(5)
        socks_server.setblocking(False)

        self._socks_servers[socks_port] = socks_server
        logger.debug(f"SOCKS5 server started on {self._socks_host}:{socks_port}.")

        loop = asyncio.get_event_loop()
        while True:
            try:
                client_sock, addr = await loop.sock_accept(socks_server)
                logger.debug(f"Accepted SOCKS5 connection from {addr}.")

                # Check if token has valid clients
                if token in self._token_clients and self._token_clients[token]:
                    asyncio.create_task(self._handle_socks_request(client_sock, token))
                else:
                    # Wait up to 10 seconds to see if any clients connect
                    wait_start = loop.time()
                    while loop.time() - wait_start < 10:
                        if token in self._token_clients and self._token_clients[token]:
                            asyncio.create_task(self._handle_socks_request(client_sock, token))
                            break
                        await asyncio.sleep(0.1)
                    else:
                        logger.debug(
                            f"No valid clients for token after waiting 10s, closing connection from {addr}"
                        )
                        client_sock.close()
            except BlockingIOError:
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error accepting SOCKS connection: {e.__class__.__name__}: {e}.")
                await asyncio.sleep(0.1)

    async def start(self):
        """Start WebSocket server"""

        async with serve(self._handle_websocket, self._ws_host, self._ws_port):
            logger.info(f"WebSocket server started on: ws://{self._ws_host}:{self._ws_port}")
            logger.info(f"Waiting for a client to connect.")
            await asyncio.Future()  # Keep server running


@click.command()
@click.option("--ws-host", "-H", default="0.0.0.0", help="WebSocket server listen address")
@click.option("--ws-port", "-P", default=8765, help="WebSocket server listen port")
@click.option(
    "--token",
    "-t",
    default=None,
    help="Specify auth token, auto-generate if not provided",
)
@click.option("--reverse", "-r", is_flag=True, default=False, help="Use reverse socks5 proxy")
@click.option(
    "--socks-host",
    "-h",
    default="127.0.0.1",
    help="SOCKS5 server listen address for reverse proxy",
)
@click.option(
    "--socks-port",
    "-p",
    default=1080,
    help="SOCKS5 server listen port for reverse proxy, auto-generate if not provided",
)
@click.option("--socks-username", "-n", default=None, help="SOCKS5 username for authentication")
@click.option("--socks-password", "-w", default=None, help="SOCKS5 password for authentication")
@click.option("--debug", "-d", is_flag=True, default=False, help="Show debug logs")
def _server_cli(
    ws_host,
    ws_port,
    token,
    reverse,
    socks_host,
    socks_port,
    socks_username,
    socks_password,
    debug,
):
    """Start SOCKS5 over WebSocket proxy server"""

    init_logging(level=logging.DEBUG if debug else logging.INFO)

    # Create server instance
    server = WSSocksServer(
        ws_host=ws_host,
        ws_port=ws_port,
        socks_host=socks_host,
    )

    # Add token based on mode
    if reverse:
        token, port = server.add_reverse_token(token, socks_port, socks_username, socks_password)
        if port:
            logger.info(f"Configuration:")
            logger.info(f"  Mode: reverse proxy (SOCKS5 on server -> client -> network)")
            logger.info(f"  Token: {token}")
            logger.info(f"  SOCKS5 port: {port}")
            if socks_username and socks_password:
                logger.info(f"  SOCKS5 auth: enabled (username: {socks_username})")
        else:
            logger.error(f"Cannot allocate SOCKS5 port: {socks_host}:{socks_port}")
            return
    else:
        token = server.add_forward_token(token)
        logger.info(f"Configuration:")
        logger.info(f"  Mode: forward proxy (SOCKS5 on client -> server -> network)")
        logger.info(f"  Token: {token}")

    # Start server
    asyncio.run(server.start())


if __name__ == "__main__":
    _server_cli()
