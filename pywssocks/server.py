from typing import Optional, Union
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

logger = logging.getLogger(__name__)

class WSSocksServer:    
    def __init__(
        self,
        ws_host: str = "0.0.0.0",
        ws_port: int = 8765,
        socks_host: str = "127.0.0.1",
        socks_port_pool: Union[PortPool, range] = range(1024, 10240),
    ) -> None:
        """Initialize WebSocket SOCKS5 Server

        Args:
            ws_host: WebSocket listen address
            ws_port: WebSocket listen port
            socks_host: SOCKS5 listen address
            socks_port_pool: SOCKS5 port pool
        """

        self._ws_host = ws_host
        self._ws_port = ws_port
        self._socks_host = socks_host
        
        if isinstance(socks_port_pool, PortPool):
            self._socks_port_pool = socks_port_pool
        else:
            self._socks_port_pool = PortPool(socks_port_pool)

        # Store all connected WebSocket clients, {client_id: websocket_connection}
        self._clients: dict[int, ServerConnection] = {}

        # Group WebSocket clients by token, {token: list of (client_id, websocket) tuples}
        self._token_clients: dict[str, list[tuple[int, ServerConnection]]] = {}

        # Store current round-robin index for each token for load balancing, {token: current_index}
        self._token_indexes: dict[str, int] = {}

        # Map tokens to their assigned SOCKS5 ports, {token: socks_port}
        self._tokens: dict[str, int] = {}

        # Store all running SOCKS5 server instances, {socks_port: server_socket}
        self._socks_servers: dict[int, socket.socket] = {}

        # Message queues for data transfer between WebSocket and SOCKS5, {channel_id/connect_id: Queue}
        self._message_queues: dict[str, asyncio.Queue] = {}

        # Store SOCKS5 related async tasks, {socks_port: Task}
        self._socks_tasks: dict[int, asyncio.Task] = {}

    def add_token(self, token: Optional[str] = None, port: Optional[int] = None):
        """Add a new token for reverse socks and assign a port

        Args:
            token: Auth token, auto-generated if None
            port: Specific port to use, if None will allocate from port range

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
            return token, port
        else:
            return None, None

    def _get_next_websocket(self, token: str) -> ServerConnection | None:
        """Get next available WebSocket connection using round-robin"""

        if token not in self._token_clients or not self._token_clients[token]:
            return None

        clients = self._token_clients[token]
        if not clients:
            return None

        current_index = self._token_indexes.get(token, 0)
        self._token_indexes[token] = (current_index + 1) % len(clients)

        return clients[current_index][1]

    async def _handle_socks_request(self, client_sock: socket.socket, token: str):
        """Handle SOCKS5 client request and establish connection through WebSocket"""

        # Use round-robin to get websocket connection
        websocket = self._get_next_websocket(token)
        if not websocket:
            logger.warning(f"No available client for SOCKS5 port {self._tokens[token]}.")
            client_sock.close()
            return

        client_id = id(websocket)
        connect_id = f"{client_id}_{str(uuid.uuid4())}"
        logger.debug(f"Starting SOCKS request handling for connect_id: {connect_id}")

        try:
            # Set to non-blocking mode
            client_sock.setblocking(False)
            loop = asyncio.get_event_loop()

            # Authentication method negotiation
            logger.debug(f"Starting SOCKS authentication for connect_id: {connect_id}")
            data = await loop.sock_recv(client_sock, 2)
            version, nmethods = struct.unpack("!BB", data)
            methods = await loop.sock_recv(client_sock, nmethods)
            await loop.sock_sendall(client_sock, struct.pack("!BB", 0x05, 0x00))
            logger.debug(f"SOCKS authentication completed for connect_id: {connect_id}")

            # Check WebSocket connection by attempting a ping
            try:
                await websocket.ping()
            except ConnectionClosed:
                logger.debug(f"WebSocket closed for connect_id: {connect_id}")
                # Return connection failure response to SOCKS client (0x04 = Host unreachable)
                await loop.sock_sendall(
                    client_sock, bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                )
                return

            # Get request details
            header = await loop.sock_recv(client_sock, 4)
            version, cmd, _, atyp = struct.unpack("!BBBB", header)
            logger.debug(f"Received SOCKS request - version: {version}, cmd: {cmd}, atyp: {atyp}")

            if cmd not in (0x01, 0x03):  # Support CONNECT and UDP ASSOCIATE commands
                logger.debug(f"Unsupported SOCKS command: {cmd}")
                client_sock.close()
                return

            # Parse target address
            if atyp == 0x01:  # IPv4
                addr_bytes = await loop.sock_recv(client_sock, 4)
                target_addr = socket.inet_ntoa(addr_bytes)
            elif atyp == 0x03:  # Domain name
                addr_len = (await loop.sock_recv(client_sock, 1))[0]
                addr_bytes = await loop.sock_recv(client_sock, addr_len)
                target_addr = addr_bytes.decode()
            else:
                client_sock.close()
                return

            # Get port number
            port_bytes = await loop.sock_recv(client_sock, 2)
            target_port = struct.unpack("!H", port_bytes)[0]

            # Create a temporary queue for connection response
            connect_queue = asyncio.Queue()
            self._message_queues[connect_id] = connect_queue

            # Construct forwarding request
            request_data = {
                "type": "connect",
                "address": target_addr,
                "port": target_port,
                "connect_id": connect_id,
                "cmd": cmd,  # Add command type
            }

            try:
                # Send request to WebSocket client
                await websocket.send(json.dumps(request_data))

                # Use asyncio.shield to prevent timeout cancellation causing queue cleanup
                response_future = asyncio.shield(connect_queue.get())
                try:
                    # Wait for client connection result
                    response = await asyncio.wait_for(response_future, timeout=10)
                    response_data = json.loads(response) if isinstance(response, str) else response

                    if not response_data.get("success", False):
                        # Connection failed, return failure response to SOCKS client
                        error_msg = response_data.get("error", "Connection failed")
                        logger.error(f"Target connection failed: {error_msg}.")
                        await loop.sock_sendall(
                            client_sock,
                            bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                        )
                        return

                    # Handle different responses based on command type
                    if cmd == 0x01:  # CONNECT
                        # TCP connection successful, return success response
                        await loop.sock_sendall(
                            client_sock,
                            bytes([0x05, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                        )
                        await self._handle_tcp_forward(
                            client_sock, websocket, response_data["channel_id"]
                        )
                    else:  # UDP ASSOCIATE
                        # Get UDP binding information
                        bound_addr = response_data.get("bound_address", "0.0.0.0")
                        bound_port = response_data.get("bound_port", 0)

                        # Construct UDP binding response
                        bind_ip = socket.inet_aton(bound_addr)
                        bind_port_bytes = struct.pack("!H", bound_port)
                        reply = (
                            struct.pack("!BBBB", 0x05, 0x00, 0x00, 0x01) + bind_ip + bind_port_bytes
                        )
                        await loop.sock_sendall(client_sock, reply)

                        # Keep TCP connection until client disconnects
                        await self._handle_udp_associate(
                            client_sock, websocket, response_data["channel_id"]
                        )

                except asyncio.TimeoutError:
                    # Ensure cleanup on timeout
                    response_future.cancel()
                    logger.error("Connection response timeout.")
                    await loop.sock_sendall(
                        client_sock,
                        bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    )
                except Exception as e:
                    logger.error(f"Error handling SOCKS request: {e.__class__.__name__}: {e}.")
                    try:
                        await loop.sock_sendall(
                            client_sock,
                            bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                        )
                    except:
                        pass
            finally:
                # Ensure cleanup in all cases
                if connect_id in self._message_queues:
                    del self._message_queues[connect_id]
        except Exception as e:
            logger.error(f"Error handling SOCKS request: {e.__class__.__name__}: {e}.")
            try:
                reply = struct.pack("!BBBB", 0x05, 0x01, 0x00, 0x01)
                reply += socket.inet_aton("0.0.0.0") + struct.pack("!H", 0)
                await loop.sock_sendall(client_sock, reply)
            except:
                pass
        finally:
            client_sock.close()
            if connect_id and connect_id in self._message_queues:
                del self._message_queues[connect_id]

    async def _handle_udp_associate(
        self, client_sock: socket.socket, websocket: ServerConnection, channel_id: str
    ):
        """Handle UDP forwarding association"""

        try:
            # Keep TCP connection until client disconnects
            while True:
                try:
                    # Check if TCP connection is closed
                    data = await asyncio.get_event_loop().sock_recv(client_sock, 1)
                    if not data:
                        break
                except:
                    break
        finally:
            client_sock.close()

    async def _handle_tcp_forward(
        self, client_sock: socket.socket, websocket: ServerConnection, channel_id: str
    ) -> None:
        """Handle TCP data forwarding"""

        try:
            message_queue = asyncio.Queue()
            self._message_queues[channel_id] = message_queue

            while True:
                try:
                    # Read data from SOCKS client
                    try:
                        data = client_sock.recv(4096)
                        if not data:  # Connection closed
                            break
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "data",
                                    "protocol": "tcp",
                                    "channel_id": channel_id,
                                    "data": data.hex(),
                                }
                            )
                        )
                    except BlockingIOError:
                        pass
                    except ConnectionClosed:
                        # Exit when WebSocket connection is closed
                        break
                    except Exception as e:
                        logger.error(f"Send data error: {e.__class__.__name__}: {e}.")
                        break

                    # Receive data from WebSocket client
                    try:
                        msg_data = await asyncio.wait_for(message_queue.get(), timeout=0.1)
                        binary_data = bytes.fromhex(msg_data["data"])
                        client_sock.send(binary_data)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"Receive data error: {e.__class__.__name__}: {e}.")
                        break

                except Exception as e:
                    logger.error(f"Data forwarding error: {e.__class__.__name__}: {e}.")
                    break

        finally:
            # Clean up resources
            client_sock.close()
            if channel_id in self._message_queues:
                del self._message_queues[channel_id]

    async def _handle_websocket(self, websocket: ServerConnection) -> None:
        """Handle WebSocket connection"""

        client_id = None
        token = None
        socks_port = None
        try:
            auth_message = await websocket.recv()
            auth_data = json.loads(auth_message)

            if auth_data["type"] != "auth" or auth_data["token"] not in self._tokens:
                await websocket.close(1008, "Invalid token")
                return

            token = auth_data["token"]
            socks_port = self._tokens[token]
            client_id = id(websocket)

            if token not in self._token_clients:
                self._token_clients[token] = []
            self._token_clients[token].append((client_id, websocket))
            self._clients[client_id] = websocket

            await websocket.send(json.dumps({"type": "auth_response", "success": True}))

            logger.info(
                f"Client {client_id} authenticated, using SOCKS5 server on: {self._socks_host}:{socks_port}"
            )

            # Ensure SOCKS server is running
            if socks_port not in self._socks_servers:
                asyncio.create_task(self._run_socks_server(token, socks_port))

            receiver_task = asyncio.create_task(self._ws_message_receiver(websocket, client_id))
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

    async def _cleanup_connection(self, client_id: Optional[int], token: Optional[str]):
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

    async def _ws_message_receiver(self, websocket: ServerConnection, client_id: int) -> None:
        """Separated WebSocket message receiver"""

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=60)  # 60 seconds timeout
                    msg_data = json.loads(msg)

                    if msg_data["type"] == "data":
                        channel_id = msg_data["channel_id"]
                        if channel_id in self._message_queues:
                            await self._message_queues[channel_id].put(msg_data)
                    elif msg_data["type"] == "connect_response":
                        connect_id = msg_data["connect_id"]
                        if connect_id in self._message_queues:
                            await self._message_queues[connect_id].put(msg_data)
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

    async def _run_socks_server(self, token: str, socks_port: int):
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

    async def _start(self):
        """Start WebSocket server"""

        async with serve(self._handle_websocket, self._ws_host, self._ws_port):
            logger.info(f"WebSocket server started on: ws://{self._ws_host}:{self._ws_port}")
            await asyncio.Future()  # Keep server running


@click.command()
@click.option("--ws-host", "-H", default="0.0.0.0", help="WebSocket server listen address")
@click.option("--ws-port", "-P", default=8765, help="WebSocket server listen port")
@click.option("--socks-host", "-h", default="127.0.0.1", help="SOCKS5 server listen address")
@click.option(
    "--socks-port",
    "-p",
    default=1080,
    help="SOCKS5 server listen port, auto-generate if not provided",
)
@click.option(
    "--token", "-t", default=None, help="Specify auth token, auto-generate if not provided"
)
@click.option("--debug", "-d", is_flag=True, default=False, help="Show debug logs")
def _server_cli(ws_host, ws_port, socks_host, socks_port, token, debug):
    """Start SOCKS5 over WebSocket proxy server"""

    init_logging(level=logging.DEBUG if debug else logging.INFO)

    # Create server instance with single port range
    server = WSSocksServer(
        ws_host=ws_host,
        ws_port=ws_port,
        socks_host=socks_host,
    )

    # Add token with specific port
    token, port = server.add_token(token, socks_port)

    if port:
        logger.info(f"Configuration:")
        logger.info(f"  Token: {token}")
        logger.info(f"  SOCKS5 port: {port}")

        # Start server
        asyncio.run(server._start())
    else:
        logger.error("Cannot allocate SOCKS5 port.")


if __name__ == "__main__":
    _server_cli()
