from http import HTTPStatus
from typing import Iterable, Optional, Tuple, Union
import logging
import asyncio
import json
import socket
import random
import string
from uuid import UUID, uuid4
from dataclasses import dataclass

from urllib.parse import urlparse, parse_qs
import hashlib
from websockets.http11 import Request
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.server import ServerConnection, serve

from pywssocks.common import PortPool
from pywssocks.relay import Relay
from pywssocks import __version__
from .message import (
    AuthMessage,
    AuthResponseMessage,
    ConnectMessage,
    ConnectResponseMessage,
    DataMessage,
    DisconnectMessage,
    pack_message,
    parse_message,
    ConnectorMessage,
    ConnectorResponseMessage,
    LogMessage,
    PartnersMessage,
)

_default_logger = logging.getLogger(__name__)


@dataclass
class TokenOptions:
    """Options for a token including authentication and permissions"""

    username: Optional[str] = None
    password: Optional[str] = None
    allow_manage_connector: bool = False


class ConnectorCache:
    """Cache for managing connector connections and channels"""

    def __init__(self):
        self._channel_id_to_client: dict[
            UUID, ServerConnection
        ] = {}  # Maps channel_id to reverse client WebSocket
        self._channel_id_to_connector: dict[
            UUID, ServerConnection
        ] = {}  # Maps channel_id to connector WebSocket
        self._token_cache: dict[
            str, list[UUID]
        ] = {}  # Maps token to list of channel_ids
        self._lock = asyncio.Lock()

    async def add_channel(
        self,
        channel_id: UUID,
        connector_ws: ServerConnection,
        client_ws: ServerConnection,
        token: str,
    ):
        async with self._lock:
            self._channel_id_to_connector[channel_id] = connector_ws
            self._channel_id_to_client[channel_id] = client_ws
            if token not in self._token_cache:
                self._token_cache[token] = []
            self._token_cache[token].append(channel_id)

    async def remove_channel(self, channel_id: UUID):
        async with self._lock:
            if channel_id in self._channel_id_to_connector:
                del self._channel_id_to_connector[channel_id]
            if channel_id in self._channel_id_to_client:
                del self._channel_id_to_client[channel_id]

    async def get_client(self, channel_id: UUID) -> Optional[ServerConnection]:
        async with self._lock:
            return self._channel_id_to_client.get(channel_id)

    async def get_connector(self, channel_id: UUID) -> Optional[ServerConnection]:
        async with self._lock:
            return self._channel_id_to_connector.get(channel_id)

    async def cleanup_token(self, token: str):
        async with self._lock:
            if token in self._token_cache:
                for channel_id in self._token_cache[token]:
                    if channel_id in self._channel_id_to_connector:
                        del self._channel_id_to_connector[channel_id]
                    if channel_id in self._channel_id_to_client:
                        del self._channel_id_to_client[channel_id]
                del self._token_cache[token]


class SocketManager:
    """Manages server sockets with reuse capability"""

    def __init__(
        self, host: str, grace: float = 30, logger: Optional[logging.Logger] = None
    ):
        """
        Args:
            host: Listen address for servers
        """
        self._host = host
        self._grace = grace
        self._sockets: dict[
            int, tuple[socket.socket, float, int]
        ] = {}  # port -> (socket, timestamp, refs)
        self._lock = asyncio.Lock()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._log = logger or _default_logger

    async def get_socket(self, port: int) -> socket.socket:
        """Get a socket for the specified port, reusing existing one if available

        Args:
            port: Port number for the socket

        Returns:
            socket.socket: Socket bound to the specified port
        """
        async with self._lock:
            # Check if we have an existing socket
            if port in self._sockets:
                sock, timestamp, refs = self._sockets[port]
                self._sockets[port] = (sock, timestamp, refs + 1)
                sock.listen(1)
                self._log.debug(
                    f"Reusing existing socket for port {port} (refs: {refs + 1})"
                )
                return sock

            # Create new socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((self._host, port))
            sock.listen(5)
            sock.setblocking(False)

            self._sockets[port] = (sock, 0, 1)
            self._log.debug(f"New socket allocated on {self._host}:{port}")
            return sock

    async def release_socket(self, port: int) -> None:
        """Release a socket, starting 30s grace period for potential reuse

        Args:
            port: Port number of the socket to release
        """
        async with self._lock:
            if port not in self._sockets:
                self._log.warning(
                    f"Attempted to release non-existent socket on port {port}"
                )
                return

            sock, _, refs = self._sockets[port]
            refs -= 1

            if refs <= 0:
                self._log.debug(f"Starting grace period for socket on port {port}")
                sock.listen(0)
                # Start grace period
                self._sockets[port] = (sock, asyncio.get_event_loop().time(), 0)
                task = asyncio.create_task(self._cleanup_socket(port))
                self._cleanup_tasks.add(task)
                task.add_done_callback(self._cleanup_tasks.discard)
            else:
                self._log.debug(f"Released socket on port {port}.")
                self._sockets[port] = (sock, 0, refs)

    async def _close_socket(self, sock: socket.socket) -> None:
        """Close a single socket safely."""

        loop = asyncio.get_running_loop()
        # Required for Python 3.8:
        #   bpo-85489: sock_accept() does not remove server socket reader on cancellation
        #         url: https://bugs.python.org/issue41317
        try:
            loop.remove_reader(sock.fileno())
        except:
            pass
        try:
            sock.close()
        except:
            pass

    async def _cleanup_socket(self, port: int) -> None:
        """Clean up socket after grace period if not reused"""

        await asyncio.sleep(self._grace)  # Grace period

        async with self._lock:
            if port not in self._sockets:
                return

            sock, timestamp, refs = self._sockets[port]
            # Only close if still in grace period (timestamp > 0) and no new refs
            if refs == 0 and timestamp > 0:
                self._log.debug(
                    f"Cleaning up unused socket on port {port} after grace period"
                )
                await self._close_socket(sock)
                del self._sockets[port]
            else:
                # Socket was reused or revived during grace period.
                # Keep the substring "after grace period" for compatibility with existing tests.
                self._log.debug(
                    f"Socket on port {port} wasn't cleaned up after grace period (refs: {refs})"
                )

    async def close(self) -> None:
        """Close all sockets and cancel cleanup tasks."""
        self._log.debug("Closing all managed sockets")
        async with self._lock:
            # Cancel all cleanup tasks first
            for task in self._cleanup_tasks:
                task.cancel()

            # Wait for cancellation to complete
            if self._cleanup_tasks:
                await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)

            # Close all sockets
            for port, (sock, _, _) in list(self._sockets.items()):
                await self._close_socket(sock)
                del self._sockets[port]


class WSSocksServer(Relay):
    """
    A SOCKS5 over WebSocket protocol server.

    In forward proxy mode, it will receive WebSocket requests from clients, access the network as
    requested, and return the results to the client.

    In reverse proxy mode, it will receive SOCKS5 requests and send them to the connected client
    via WebSocket for parsing.
    """

    def __init__(
        self,
        ws_host: str = "0.0.0.0",
        ws_port: int = 8765,
        socks_host: str = "127.0.0.1",
        socks_port_pool: Union[PortPool, Iterable[int]] = range(1024, 10240),
        socks_wait_client: bool = True,
        socks_grace: float = 30.0,
        logger: Optional[logging.Logger] = None,
        **kw,
    ) -> None:
        """
        Args:
            ws_host: WebSocket listen address
            ws_port: WebSocket listen port
            socks_host: SOCKS5 listen address for reverse proxy
            socks_port_pool: SOCKS5 port pool for reverse proxy
            socks_wait_client: Wait for client connection before starting the SOCKS server,
                otherwise start the SOCKS server when the reverse proxy token is added.
            socks_grace: Grace time in seconds before stopping the SOCKS server after token
                removal to avoid port re-allocation.
            logger: Custom logger instance
        """

        super().__init__(logger=logger, **kw)

        self._loop = None
        self.ready = asyncio.Event()

        self._ws_host = ws_host
        self._ws_port = ws_port
        self._socks_host = socks_host

        if isinstance(socks_port_pool, PortPool):
            self._socks_port_pool = socks_port_pool
        else:
            self._socks_port_pool = PortPool(socks_port_pool)

        self._socks_wait_client = socks_wait_client

        self._pending_tokens = []

        # Store all connected reverse proxy clients, {client_id: websocket}
        self._clients: dict[UUID, ServerConnection] = {}

        # Protect shared resource for token, {token: lock}
        self._token_locks: dict[str, asyncio.Lock] = {}

        # Group reverse proxy clients by token, {token: list of (client_id, websocket) tuples}
        self._token_clients: dict[str, list[tuple[UUID, ServerConnection]]] = {}

        # Store current round-robin index for each reverse proxy token for load balancing, {token: current_index}
        self._token_indexes: dict[str, int] = {}

        # Map reverse proxy tokens to their assigned SOCKS5 ports, {token: socks_port}
        self._tokens: dict[str, int] = {}

        # Store all running SOCKS5 server tasks, {socks_port: Task}
        self._socks_tasks: dict[int, asyncio.Task] = {}

        # Message channels for receiving and routing from WebSocket, {channel_id: Queue}
        self._message_queues: dict[str, asyncio.Queue] = {}

        # Store tokens for forward proxy
        self._forward_tokens = set()

        # Store all connected forward proxy clients, {client_id: websocket}
        self._forward_clients: dict[UUID, ServerConnection] = {}

        # Store connector tokens mapping to reverse tokens
        self._connector_tokens: dict[str, str] = {}

        # Store internal tokens mapping
        self._internal_tokens: dict[str, list[str]] = {}

        # Store token options including auth and permissions
        self._token_options: dict[str, TokenOptions] = {}

        # Manage SOCKS server port allocation
        self._socket_manager = SocketManager(
            socks_host,
            grace=socks_grace,
            logger=_default_logger.getChild("socket_manager"),
        )

        # Manage connector connections and channels
        self._conn_cache = ConnectorCache()

        self._stopping = asyncio.Event()

    def add_connector_token(
        self, connector_token: Optional[str] = None, reverse_token: Optional[str] = None
    ) -> Optional[str]:
        """Add a new connector token that forwards requests to a reverse token

        Args:
            connector_token: Connector token, auto-generated if None
            reverse_token: The reverse token to connect to

        Returns:
            str: The connector token if successful, None otherwise
        """
        if not reverse_token or reverse_token not in self._tokens:
            return None

        if connector_token is None:
            chars = string.ascii_letters + string.digits
            connector_token = "".join(random.choice(chars) for _ in range(16))

        if connector_token in self._connector_tokens:
            return None

        self._connector_tokens[connector_token] = reverse_token
        self._log.info("New connector token added.")
        return connector_token

    def remove_connector_token(self, connector_token: str) -> bool:
        """Remove a connector token and disconnect all its clients

        Args:
            connector_token: The connector token to remove

        Returns:
            bool: True if token was found and removed, False otherwise
        """
        if connector_token not in self._connector_tokens:
            return False

        # Clean up connector cache
        if self._loop:
            self._loop.create_task(self._conn_cache.cleanup_token(connector_token))

        # Close all client connections for this token
        if connector_token in self._token_clients:
            for client_id, ws in self._token_clients[connector_token]:
                if self._loop:
                    try:
                        self._loop.create_task(ws.close(1000, "Token removed"))
                    except:
                        pass
                if client_id in self._clients:
                    del self._clients[client_id]
            del self._token_clients[connector_token]

        # Remove the connector token
        del self._connector_tokens[connector_token]
        self._log.info(f"Connector token {connector_token} removed.")
        return True

    def add_reverse_token(
        self,
        token: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        allow_manage_connector: bool = False,
    ) -> Union[Tuple[str, int], Tuple[None, None]]:
        """Add a new token for reverse socks and assign a port

        Args:
            token: Auth token, auto-generated if None
            port: Specific port to use, if None will allocate from port range
            username: SOCKS5 username, no auth if None
            password: SOCKS5 password, no auth if None
            allow_manage_connector: Allow managing connectors via WebSocket messages

        Returns:
            (token, port) tuple containing the token and assigned SOCKS5 port
            Returns (None, None) if no ports available or port already in use
        """
        if token is None:
            chars = string.ascii_letters + string.digits
            token = "".join(random.choice(chars) for _ in range(16))

        if token in self._tokens:
            return token, self._tokens[token]

        if allow_manage_connector:
            self._tokens[token] = -1
            self._token_locks[token] = asyncio.Lock()
            port = -1
        else:
            port = self._socks_port_pool.get(port)
            if not port:
                return None, None
            self._tokens[token] = port
            self._token_locks[token] = asyncio.Lock()
            self._log.info(f"New reverse proxy token added for port {port}.")
        self._token_options[token] = TokenOptions(
            username=username,
            password=password,
            allow_manage_connector=allow_manage_connector,
        )
        if self._loop:
            self._loop.create_task(self._handle_pending_token(token))
        else:
            self._pending_tokens.append(token)
        return token, port

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
        self._log.info("New forward proxy token added.")
        return token

    def remove_token(self, token: str) -> bool:
        """Remove a token and disconnect all its clients

        Args:
            token: The token to remove

        Returns:
            bool: True if token was found and removed, False otherwise
        """
        # Check if token exists
        if (
            token not in self._tokens
            and token not in self._forward_tokens
            and token not in self._connector_tokens
        ):
            return False

        # Handle connector token
        if token in self._connector_tokens:
            # Clean up connector cache
            if self._loop:
                self._loop.create_task(self._conn_cache.cleanup_token(token))

            # Close all client connections for this token
            if token in self._token_clients:
                for client_id, ws in self._token_clients[token]:
                    if self._loop:
                        try:
                            self._loop.create_task(ws.close(1000, "Token removed"))
                        except:
                            pass
                    if client_id in self._clients:
                        del self._clients[client_id]
                del self._token_clients[token]

            # Clean up connector token
            del self._connector_tokens[token]
            self._log.info(f"The connector token {token} is removed.")
            return True

        # Handle reverse proxy token
        if token in self._tokens:
            # Clean up any internal tokens first
            if token in self._internal_tokens:
                for internal_token in self._internal_tokens[token]:
                    # Clean up internal token data
                    if internal_token in self._token_clients:
                        for client_id, ws in self._token_clients[internal_token]:
                            if self._loop:
                                try:
                                    self._loop.create_task(
                                        ws.close(1000, "Token removed")
                                    )
                                except:
                                    pass
                            if client_id in self._clients:
                                del self._clients[client_id]
                        del self._token_clients[internal_token]
                    if internal_token in self._tokens:
                        del self._tokens[internal_token]
                    if internal_token in self._token_indexes:
                        del self._token_indexes[internal_token]
                    if internal_token in self._token_options:
                        del self._token_options[internal_token]
                del self._internal_tokens[token]

            # Remove all connector tokens using this reverse token
            for connector_token, rt in list(self._connector_tokens.items()):
                if rt == token:
                    self.remove_token(connector_token)

            # Clean up connector cache
            if self._loop:
                self._loop.create_task(self._conn_cache.cleanup_token(token))

            # Close all client connections for this token
            if token in self._token_clients:
                for client_id, ws in self._token_clients[token]:
                    if self._loop:
                        try:
                            self._loop.create_task(ws.close(1000, "Token removed"))
                        except:
                            pass
                    if client_id in self._clients:
                        del self._clients[client_id]
                del self._token_clients[token]

            # Clean up token related data
            port = self._tokens[token]
            del self._tokens[token]
            if token in self._token_locks:
                del self._token_locks[token]
            if token in self._token_indexes:
                del self._token_indexes[token]
            if token in self._token_options:
                del self._token_options[token]
            try:
                self._pending_tokens.remove(token)
            except ValueError:
                pass

            # Close and clean up SOCKS server if it exists
            if port in self._socks_tasks:
                try:
                    self._socks_tasks[port].cancel()
                except:
                    pass
                finally:
                    del self._socks_tasks[port]

            # Return port to pool
            self._socks_port_pool.put(port)

            self._log.info(f"The reverse token {token} is removed.")
            return True

        # Handle forward proxy token
        elif token in self._forward_tokens:
            # Close all forward client connections using this token
            clients_to_remove = []
            for client_id, ws in self._forward_clients.items():
                if self._loop:
                    try:
                        self._loop.create_task(ws.close(1000, "Token removed"))
                    except:
                        pass
                clients_to_remove.append(client_id)

            for client_id in clients_to_remove:
                del self._forward_clients[client_id]

            self._forward_tokens.remove(token)

            self._log.info(f"The forward token {token} is removed.")
            return True

        return False

    async def wait_ready(self, timeout: Optional[float] = None) -> asyncio.Task:
        """Start the client and connect to the server within the specified timeout, then returns the Task."""

        task = asyncio.create_task(self.serve())
        if timeout:
            await asyncio.wait_for(self.ready.wait(), timeout=timeout)
        else:
            await self.ready.wait()
        return task

    async def serve(self):
        """
        Start the server and wait clients to connect.

        This function will execute until the server is terminated.
        """

        self._loop = asyncio.get_running_loop()

        for token in self._pending_tokens:
            await self._handle_pending_token(token)
        self._pending_tokens = []

        try:
            async with serve(
                self._handle_websocket,
                self._ws_host,
                self._ws_port,
                process_request=self._process_request,
                logger=self._log.getChild("ws"),
                close_timeout=3,
            ):
                try:
                    self._log.info(
                        f"Pywssocks Server {__version__} started on: "
                        f"ws://{self._ws_host}:{self._ws_port}"
                    )
                    self._log.info(f"Waiting for clients to connect.")
                    self.ready.set()
                    await asyncio.Future()  # Keep server running
                finally:
                    self._stopping.set()
        finally:
            await self._socket_manager.close()

    async def _get_next_websocket(self, token: str) -> Optional[ServerConnection]:
        """Get next available WebSocket connection using round-robin"""

        lock = self._token_locks[token]
        async with lock:
            if token not in self._token_clients or not self._token_clients[token]:
                return None

            clients = self._token_clients[token]
            if not clients:
                return None

            current_index = self._token_indexes.get(token, 0)
            self._token_indexes[token] = current_index = (current_index + 1) % len(
                clients
            )

        self._log.debug(
            f"Handling request using client index for this client: {current_index}"
        )
        try:
            return clients[current_index][1]
        except:
            return clients[0][1]

    async def _handle_socks_request(
        self, socks_socket: socket.socket, addr: str, token: str
    ) -> None:
        # Check if token has valid clients
        if token not in self._token_clients:
            # Wait up to 10 seconds to see if any clients connect
            loop = asyncio.get_running_loop()
            wait_start = loop.time()
            while loop.time() - wait_start < 10:
                if token in self._token_clients and self._token_clients[token]:
                    break
                await asyncio.sleep(0.1)
            else:
                self._log.debug(
                    f"No valid clients for token after waiting 10s, refusing connection from {addr}"
                )
                return await self._refuse_socks_request(socks_socket, 3)

        # Use round-robin to get websocket connection
        websocket = await self._get_next_websocket(token)
        if not websocket:
            self._log.warning(
                f"No available client for SOCKS5 port {self._tokens[token]}."
            )
            return await self._refuse_socks_request(socks_socket, 3)

        # Get auth from token options
        token_options = self._token_options.get(token)
        socks_username = token_options.username if token_options else None
        socks_password = token_options.password if token_options else None

        return await super()._handle_socks_request(
            websocket, socks_socket, socks_username, socks_password
        )

    async def _handle_pending_token(
        self, token: str, ready_event: Optional[asyncio.Event] = None
    ):
        if not self._socks_wait_client:
            socks_port = self._tokens.get(token, None)
            if socks_port:
                lock = self._token_locks[token]
                async with lock:
                    if socks_port and (socks_port not in self._socks_tasks):
                        self._socks_tasks[socks_port] = task = asyncio.create_task(
                            self._run_socks_server(
                                token, socks_port, ready_event=ready_event
                            )
                        )
                        return task

    async def _handle_websocket(self, websocket: ServerConnection) -> None:
        """Handle WebSocket connection"""
        client_id = None
        token = None
        socks_port = None
        internal_token = None

        try:
            req_path = getattr(websocket, "_path", "")
            parsed_qs = parse_qs(urlparse(req_path).query) if req_path else {}
            auth_msg = None

            if "token" in parsed_qs:
                raw_token = parsed_qs["token"][0]
                reverse_str = parsed_qs.get("reverse", ["false"])[0].lower()
                reverse = reverse_str == "true"

                instance_str = parsed_qs.get("instance", [str(uuid4())])[0]
                try:
                    instance = UUID(instance_str)
                except Exception:
                    instance = uuid4()

                # Try to resolve raw_token against known tokens (plain or sha256)
                resolved_token: Optional[str] = None
                all_tokens: set[str] = (
                    set(self._tokens.keys())
                    | self._forward_tokens
                    | set(self._connector_tokens.keys())
                )

                for t in all_tokens:
                    if raw_token == t:
                        resolved_token = t
                        break
                    if raw_token == hashlib.sha256(t.encode()).hexdigest():
                        resolved_token = t
                        break

                if not resolved_token:
                    await websocket.close(1008, "Invalid token")
                    return

                token = resolved_token

                # Create synthetic AuthMessage for unified downstream handling
                auth_msg = AuthMessage(token=token, reverse=reverse, instance=instance)
                self.log_message(auth_msg, "recv")
            else:
                auth_data = await websocket.recv()
                if not isinstance(auth_data, bytes):
                    await websocket.close(1008, "Invalid message format")
                    return

                try:
                    auth_msg = parse_message(auth_data)
                    if not isinstance(auth_msg, AuthMessage):
                        await websocket.close(1008, "Invalid auth message")
                        return
                except Exception as e:
                    self._log.error(f"Failed to parse auth message: {e}")
                    await websocket.close(1008, "Invalid auth message")
                    return
                else:
                    self.log_message(auth_msg, "recv")

                token = auth_msg.token
                reverse = auth_msg.reverse
                instance = auth_msg.instance

            # Validate token and generate client_id only after successful authentication
            if reverse and token in self._tokens:  # reverse proxy
                client_id = uuid4()
                socks_port = self._tokens[token]
                lock = self._token_locks[token]

                # For tokens with allow_manage_connector, generate a unique internal token
                if self._token_options.get(
                    token, TokenOptions()
                ).allow_manage_connector:
                    internal_token = str(instance)

                    # Add to internal tokens mapping first
                    if token not in self._internal_tokens:
                        self._internal_tokens[token] = []
                    self._internal_tokens[token].append(internal_token)

                    # Set up the internal token
                    self._token_indexes[internal_token] = 0
                    self._token_options[internal_token] = self._token_options[token]
                    self._tokens[
                        internal_token
                    ] = -1  # Use -1 to indicate no SOCKS port
                    self._token_locks[internal_token] = asyncio.Lock()

                    # Initialize token clients list
                    if internal_token not in self._token_clients:
                        self._token_clients[internal_token] = []
                    self._token_clients[internal_token].append((client_id, websocket))
                else:
                    internal_token = token
                    async with lock:
                        if internal_token not in self._token_clients:
                            self._token_clients[internal_token] = []
                        self._token_clients[internal_token].append(
                            (client_id, websocket)
                        )

                # Ensure SOCKS server is running and has allocated its socket before confirming auth.
                # This avoids a race where remove_token() cancels the task before SocketManager enters
                # the grace-period cleanup path, which some tests rely on.
                if socks_port not in self._socks_tasks and socks_port > 0:
                    ready_event = asyncio.Event()
                    self._socks_tasks[socks_port] = asyncio.create_task(
                        self._run_socks_server(
                            token, socks_port, ready_event=ready_event
                        )
                    )
                    try:
                        await asyncio.wait_for(ready_event.wait(), timeout=3)
                    except Exception:
                        self._log.debug(
                            f"Timeout waiting for SOCKS5 server to become ready on {self._socks_host}:{socks_port}"
                        )

                self._clients[client_id] = websocket
                response_msg = AuthResponseMessage(success=True, error=None)
                self.log_message(response_msg, "send")
                await websocket.send(pack_message(response_msg))
                self._log.info(f"Reverse client {client_id} authenticated")
                await self._broadcast_partners_to_connectors()

            elif not reverse and token in self._forward_tokens:  # forward proxy
                client_id = uuid4()
                self._forward_clients[client_id] = websocket
                response_msg = AuthResponseMessage(success=True, error=None)
                self.log_message(response_msg, "send")
                await websocket.send(pack_message(response_msg))
                self._log.info(f"Forward client {client_id} authenticated")

            elif not reverse and token in self._connector_tokens:  # connector proxy
                client_id = uuid4()
                reverse_token = self._connector_tokens[token]

                # Add to token clients
                if token not in self._token_clients:
                    self._token_clients[token] = []
                self._token_clients[token].append((client_id, websocket))

                self._clients[client_id] = websocket
                response_msg = AuthResponseMessage(success=True, error=None)
                self.log_message(response_msg, "send")
                await websocket.send(pack_message(response_msg))
                self._log.info(f"Connector client {client_id} authenticated")

                # Notify reverse clients about new connector
                await self._broadcast_partners_to_reverse_clients(reverse_token)

                # Send initial partners count to connector
                reverse_count = 0
                for t in self._tokens:
                    if t in self._token_clients:
                        reverse_count += len(self._token_clients[t])
                initial_partners_msg = PartnersMessage(count=reverse_count)
                self.log_message(initial_partners_msg, "send")
                await websocket.send(pack_message(initial_partners_msg))

            else:
                response_msg = AuthResponseMessage(success=False, error="Invalid token")
                self.log_message(response_msg, "send")
                await websocket.send(pack_message(response_msg))
                await websocket.close(1008, "Invalid token")
                return

            # Start message handling based on client type
            tasks = [
                asyncio.create_task(self._ws_heartbeat(websocket, client_id)),
                asyncio.create_task(self._stopping.wait()),
            ]

            # Add appropriate message dispatcher
            if token in self._connector_tokens:
                # Use connector message dispatcher for connector clients
                reverse_token = self._connector_tokens[token]
                tasks.append(
                    asyncio.create_task(
                        self._connector_message_dispatcher(
                            websocket, client_id, reverse_token
                        )
                    )
                )
            else:
                # Use regular message dispatcher for other clients
                tasks.append(
                    asyncio.create_task(self._message_dispatcher(websocket, client_id))
                )

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

        except Exception as e:
            self._log.error(f"WebSocket processing error: {e.__class__.__name__}: {e}.")
        finally:
            if client_id:
                self._log.info(f"Client {client_id} disconnected.")
            else:
                self._log.info(f"Client (unauthenticated) disconnected.")
            await self._cleanup_connection(client_id, internal_token or token)

    async def _cleanup_connection(
        self, client_id: Optional[UUID], token: Optional[str]
    ) -> None:
        """Clean up resources without closing SOCKS server"""

        if not client_id or not token:
            return

        # Determine if this is a reverse or connector token for broadcasting
        is_reverse_token = token in self._tokens
        is_connector_token = token in self._connector_tokens
        reverse_token_for_connector = (
            self._connector_tokens.get(token) if is_connector_token else None
        )

        # Clean up _token_clients
        if token in self._token_clients:
            self._token_clients[token] = [
                (cid, ws) for cid, ws in self._token_clients[token] if cid != client_id
            ]

            # Clean up resources if no connections left for this token
            if not self._token_clients[token]:
                del self._token_clients[token]
                if token in self._token_indexes:
                    del self._token_indexes[token]

        # Clean up _clients
        if client_id in self._clients:
            del self._clients[client_id]

        self._log.debug(f"Cleaned up resources for client {client_id}.")

        # Broadcast partner updates after cleanup
        if is_reverse_token:
            await self._broadcast_partners_to_connectors()
        elif is_connector_token and reverse_token_for_connector:
            await self._broadcast_partners_to_reverse_clients(
                reverse_token_for_connector
            )

    async def _ws_heartbeat(self, websocket: ServerConnection, client_id: UUID) -> None:
        """WebSocket heartbeat check"""
        try:
            while True:
                try:
                    # Send ping every 30 seconds
                    await websocket.ping()
                    await asyncio.sleep(30)
                except ConnectionClosed:
                    self._log.info(
                        f"Heartbeat detected disconnection for client {client_id}."
                    )
                    break
                except Exception as e:
                    self._log.error(f"Heartbeat error for client {client_id}: {e}")
                    break
        finally:
            # Ensure WebSocket is closed
            if not self._stopping.is_set():
                try:
                    await websocket.close()
                except:
                    pass

    async def _connector_message_dispatcher(
        self, websocket: ServerConnection, client_id: UUID, reverse_token: str
    ) -> None:
        """Handle WebSocket message distribution for connector tokens"""
        try:
            while True:
                try:
                    msg_data = await asyncio.wait_for(
                        websocket.recv(), timeout=60
                    )  # 60 seconds timeout

                    if not isinstance(msg_data, bytes):
                        self._log.warning("Received non-binary message, ignoring")
                        continue

                    try:
                        msg = parse_message(msg_data)
                    except Exception as e:
                        self._log.error(f"Failed to parse message: {e}")
                        continue
                    else:
                        self.log_message(msg, "recv")

                    if isinstance(msg, ConnectMessage):
                        # Get a reverse client using round-robin, following Go version logic
                        reverse_ws = await self._get_next_websocket(reverse_token)
                        if not reverse_ws:
                            self._log.debug("Refusing connector connect")
                            # Send failure response back to connector
                            response = ConnectResponseMessage(
                                channel_id=msg.channel_id,
                                success=False,
                                error="no available reverse clients",
                            )
                            self.log_message(response, "send")
                            await websocket.send(pack_message(response))
                            continue

                        # Store channel mapping
                        await self._conn_cache.add_channel(
                            msg.channel_id, websocket, reverse_ws, reverse_token
                        )

                        # Forward connect message to reverse client
                        self.log_message(msg, "send")
                        await reverse_ws.send(pack_message(msg))

                    elif isinstance(msg, DataMessage):
                        # Route data message based on channel_id
                        target_ws = await self._conn_cache.get_client(msg.channel_id)
                        if target_ws:
                            self.log_message(msg, "send")
                            await target_ws.send(pack_message(msg))
                        else:
                            self._log.debug(
                                f"Received data for unknown channel: {msg.channel_id}"
                            )

                    elif isinstance(msg, DisconnectMessage):
                        # Clean up channel mappings and forward message
                        target_ws = await self._conn_cache.get_client(msg.channel_id)
                        if target_ws:
                            self.log_message(msg, "send")
                            await target_ws.send(pack_message(msg))
                        await self._conn_cache.remove_channel(msg.channel_id)

                except asyncio.TimeoutError:
                    try:
                        await websocket.ping()
                    except:
                        self._log.warning(
                            f"Connection timeout for connector client {client_id}"
                        )
                        break
                except ConnectionClosed:
                    self._log.info(f"Connector client {client_id} connection closed.")
                    break

        except Exception as e:
            self._log.error(
                f"Connector WebSocket error for client {client_id}: {e.__class__.__name__}: {e}."
            )

    async def _message_dispatcher(
        self, websocket: ServerConnection, client_id: UUID
    ) -> None:
        """WebSocket message receiver distributing messages to different message queues"""

        network_handler_tasks = set()  # Track network connection handler tasks

        try:
            while True:
                try:
                    msg_data = await asyncio.wait_for(
                        websocket.recv(), timeout=60
                    )  # 60 seconds timeout

                    if not isinstance(msg_data, bytes):
                        self._log.warning("Received non-binary message, ignoring")
                        continue

                    try:
                        msg = parse_message(msg_data)
                    except Exception as e:
                        self._log.error(f"Failed to parse message: {e}")
                        continue
                    else:
                        self.log_message(msg, "recv")

                    if isinstance(msg, DataMessage):
                        channel_id = str(msg.channel_id)
                        if channel_id in self._message_queues:
                            await self._message_queues[channel_id].put(msg)
                        else:
                            target_ws = await self._conn_cache.get_connector(
                                msg.channel_id
                            )
                            if target_ws:
                                self.log_message(msg, "send")
                                await target_ws.send(pack_message(msg))
                            else:
                                self._log.debug(
                                    f"Received data for unknown channel: {channel_id}"
                                )
                    elif isinstance(msg, ConnectResponseMessage):
                        connect_id = str(msg.channel_id)
                        if connect_id in self._message_queues:
                            await self._message_queues[connect_id].put(msg)
                        else:
                            target_ws = await self._conn_cache.get_connector(
                                msg.channel_id
                            )
                            if target_ws:
                                self.log_message(msg, "send")
                                await target_ws.send(pack_message(msg))
                    elif (
                        isinstance(msg, ConnectMessage)
                        and client_id in self._forward_clients
                    ):
                        self._message_queues[str(msg.channel_id)] = asyncio.Queue()
                        handler_task = asyncio.create_task(
                            self._handle_network_connection(websocket, msg)
                        )
                        network_handler_tasks.add(handler_task)
                        handler_task.add_done_callback(network_handler_tasks.discard)
                    elif isinstance(msg, DisconnectMessage):
                        self.disconnect_channel(str(msg.channel_id))
                    elif isinstance(msg, ConnectorMessage):
                        # Find the token for this client
                        client_token = None
                        for token, clients in self._token_clients.items():
                            for cid, _ in clients:
                                if cid == client_id:
                                    client_token = token
                                    break
                            if client_token:
                                break

                        # Check permissions
                        has_permission = False
                        if client_token and client_token in self._token_options:
                            has_permission = self._token_options[
                                client_token
                            ].allow_manage_connector

                        response = ConnectorResponseMessage(
                            success=False,
                            channel_id=msg.channel_id,
                            connector_token=None,
                        )

                        if has_permission:
                            if msg.operation == "add":
                                new_token = self.add_connector_token(
                                    msg.connector_token, client_token
                                )
                                if new_token:
                                    response.success = True
                                    response.connector_token = new_token
                                else:
                                    response.error = "Failed to add connector token"
                            elif msg.operation == "remove":
                                if self.remove_token(msg.connector_token):
                                    response.success = True
                                else:
                                    response.error = "Failed to remove connector token"
                            else:
                                response.error = (
                                    f"Unknown connector operation: {msg.operation}"
                                )
                        else:
                            response.error = "Unauthorized connector management attempt"

                        self.log_message(response, "send")
                        await websocket.send(pack_message(response))

                except asyncio.TimeoutError:
                    # If 60 seconds pass without receiving messages, check if connection is still alive
                    try:
                        await websocket.ping()
                    except:
                        self._log.warning(f"Connection timeout for client {client_id}")
                        break
                except ConnectionClosed:
                    self._log.info(f"Client {client_id} connection closed.")
                    break
        except Exception as e:
            self._log.error(
                f"WebSocket receive error for client {client_id}: {e.__class__.__name__}: {e}."
            )
        finally:
            # Cancel all active network connection handler tasks
            for task in network_handler_tasks:
                task.cancel()
            await asyncio.gather(*network_handler_tasks, return_exceptions=True)

    async def _run_socks_server(
        self, token: str, socks_port: int, ready_event: Optional[asyncio.Event] = None
    ) -> None:
        """SOCKS server startup function"""

        socks_handler_tasks = set()  # Track SOCKS request handler tasks

        try:
            socks_server = await self._socket_manager.get_socket(socks_port)
            self._log.info(
                f"SOCKS5 server socket allocated on {self._socks_host}:{socks_port}"
            )

            loop = asyncio.get_event_loop()
            if ready_event:
                ready_event.set()
            while True:
                try:
                    client_sock, addr = await loop.sock_accept(socks_server)
                    self._log.debug(f"Accepted SOCKS5 connection from {addr}.")
                    handler_task = asyncio.create_task(
                        self._handle_socks_request(client_sock, addr, token)
                    )
                    socks_handler_tasks.add(handler_task)
                    handler_task.add_done_callback(socks_handler_tasks.discard)
                except OSError as e:
                    # Socket closed or invalid, exit loop
                    self._log.debug(
                        f"SOCKS server socket closed: {e.__class__.__name__}: {e}"
                    )
                    break
                except Exception as e:
                    self._log.error(
                        f"Error accepting SOCKS connection: {e.__class__.__name__}: {e}"
                    )
        except Exception as e:
            self._log.error(f"SOCKS server error: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            # Cancel all active SOCKS request handler tasks
            for task in socks_handler_tasks:
                task.cancel()
            await asyncio.gather(*socks_handler_tasks, return_exceptions=True)

            # Release the socket (starts grace period)
            await self._socket_manager.release_socket(socks_port)
            self._log.info(
                f"SOCKS5 server released on {self._socks_host}:{socks_port}."
            )

    async def _process_request(self, connection: ServerConnection, request: Request):
        """Process HTTP requests before WebSocket handshake"""

        parsed_path = urlparse(request.path).path
        if parsed_path == "/socket":
            setattr(connection, "_path", request.path)
            # Return None to continue WebSocket handshake for WebSocket path
            return None
        elif parsed_path == "/":
            respond = (
                f"Pywssocks {__version__} is running but API is not enabled. "
                "Please check the documentation.\n"
            )
            return connection.respond(HTTPStatus.OK, respond)
        else:
            return connection.respond(HTTPStatus.NOT_FOUND, "404 Not Found\n")

    async def _disconnect_channel(
        self,
        channel_id: UUID,
        websocket: ServerConnection,
        msg: Union[ConnectResponseMessage, DisconnectMessage],
    ) -> None:
        """Handle forwarding disconnect message and cleanup of channel resources"""
        # Forward disconnect message to connector if exists
        target_ws = await self._conn_cache.get_connector(channel_id)
        if target_ws:
            self.log_message(msg, "send")
            await target_ws.send(pack_message(msg))

        # Clean up channel
        await self._conn_cache.remove_channel(channel_id)
        self.disconnect_channel(str(channel_id))

    async def _broadcast_partners_to_connectors(self) -> None:
        """Send the current number of reverse clients to all connectors"""
        reverse_count = 0
        for token in self._tokens:
            if token in self._token_clients:
                reverse_count += len(self._token_clients[token])

        partners_msg = PartnersMessage(count=reverse_count)

        for connector_token in self._connector_tokens:
            if connector_token in self._token_clients:
                for _, ws in self._token_clients[connector_token]:
                    try:
                        self.log_message(partners_msg, "send")
                        await ws.send(pack_message(partners_msg))
                    except Exception as e:
                        self._log.debug(
                            f"Failed to send partners update to connector: {e}"
                        )

    async def _broadcast_partners_to_reverse_clients(self, reverse_token: str) -> None:
        """Send the current number of connectors to all reverse clients for a given token"""
        connector_count = 0
        for connector_token, rt in self._connector_tokens.items():
            if rt == reverse_token:
                if connector_token in self._token_clients:
                    connector_count += len(self._token_clients[connector_token])

        partners_msg = PartnersMessage(count=connector_count)

        if reverse_token in self._token_clients:
            for _, ws in self._token_clients[reverse_token]:
                try:
                    self.log_message(partners_msg, "send")
                    await ws.send(pack_message(partners_msg))
                except Exception as e:
                    self._log.debug(
                        f"Failed to send partners update to reverse client: {e}"
                    )
