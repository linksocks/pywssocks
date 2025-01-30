import asyncio
import socket
import json
import logging
from typing import Dict, Optional
import uuid
import struct

import click
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.client import ClientConnection, connect


from pywssocks.common import init_logging
from pywssocks.relay import Relay

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
    ):
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
        
        self._ws_url: str = ws_url
        self._token: str = token
        self._reverse: bool = reverse

        self._socks_host: str = socks_host
        self._socks_port: int = socks_port
        self._socks_username: Optional[str] = socks_username
        self._socks_password: Optional[str] = socks_password

        self._socks_server: Optional[socket.socket] = None
        self._websocket: Optional[ClientConnection] = None

    async def _message_dispatcher(self, websocket: ClientConnection):
        """Global WebSocket message dispatcher"""

        try:
            while True:
                msg = await websocket.recv()
                data = json.loads(msg)
                if data["type"] == "data":
                    channel_id = data["channel_id"]
                    if channel_id in self._message_queues:
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

    async def _run_socks_server(self):
        """Run local SOCKS5 server"""
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

    async def _handle_socks_request(self, socks_socket: socket.socket):
        """Handle SOCKS5 client request"""

        if not self._websocket:
            logger.error("Fail to handle socks request without valid Websocket connection.")
            return
        try:
            socks_socket.setblocking(False)
            loop = asyncio.get_event_loop()

            # Authentication negotiation
            data = await loop.sock_recv(socks_socket, 2)
            version, nmethods = struct.unpack("!BB", data)
            methods = await loop.sock_recv(socks_socket, nmethods)

            if self._socks_username and self._socks_password:
                # Require username/password authentication
                if 0x02 not in methods:
                    await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x05, 0xFF))
                    return
                await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x05, 0x02))
                
                # Perform username/password authentication
                auth_version = (await loop.sock_recv(socks_socket, 1))[0]
                if auth_version != 0x01:
                    return
                
                ulen = (await loop.sock_recv(socks_socket, 1))[0]
                username = (await loop.sock_recv(socks_socket, ulen)).decode()
                plen = (await loop.sock_recv(socks_socket, 1))[0]
                password = (await loop.sock_recv(socks_socket, plen)).decode()
                
                if username != self._socks_username or password != self._socks_password:
                    await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x01, 0x01))
                    return
                await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x01, 0x00))
            else:
                # No authentication required
                await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x05, 0x00))

            # Get request details
            header = await loop.sock_recv(socks_socket, 4)
            version, cmd, _, atyp = struct.unpack("!BBBB", header)

            if cmd not in (0x01, 0x03):  # Only support CONNECT and UDP ASSOCIATE
                socks_socket.close()
                return

            # Parse target address
            if atyp == 0x01:  # IPv4
                addr_bytes = await loop.sock_recv(socks_socket, 4)
                target_addr = socket.inet_ntoa(addr_bytes)
            elif atyp == 0x03:  # Domain name
                addr_len = (await loop.sock_recv(socks_socket, 1))[0]
                addr_bytes = await loop.sock_recv(socks_socket, addr_len)
                target_addr = addr_bytes.decode()
            else:
                socks_socket.close()
                return

            # Get port
            port_bytes = await loop.sock_recv(socks_socket, 2)
            target_port = struct.unpack("!H", port_bytes)[0]

            connect_id = str(uuid.uuid4())
            
            # Create a temporary queue for connection response
            connect_queue = asyncio.Queue()
            self._message_queues[connect_id] = connect_queue

            request_data = {
                "type": "connect",
                "address": target_addr,
                "port": target_port,
                "connect_id": connect_id,
                "cmd": cmd,
            }

            try:
                # Send connection request to server
                await self._websocket.send(json.dumps(request_data))

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
                            socks_socket,
                            bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                        )
                        return

                    # Handle different responses based on command type
                    if cmd == 0x01:  # CONNECT
                        # TCP connection successful, return success response
                        await loop.sock_sendall(
                            socks_socket,
                            bytes([0x05, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                        )
                        await self._handle_socks_tcp_forward(
                            self._websocket, socks_socket, response_data["channel_id"]
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
                        await loop.sock_sendall(socks_socket, reply)

                        # Keep TCP connection until client disconnects
                        await self._handle_socks_udp_associate(
                            self._websocket, socks_socket, response_data["channel_id"]
                        )

                except asyncio.TimeoutError:
                    # Ensure cleanup on timeout
                    response_future.cancel()
                    logger.error("Connection response timeout.")
                    await loop.sock_sendall(
                        socks_socket,
                        bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                    )
                except Exception as e:
                    logger.error(f"Error handling SOCKS request: {e.__class__.__name__}: {e}.")
                    try:
                        await loop.sock_sendall(
                            socks_socket,
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
                await loop.sock_sendall(socks_socket, reply)
            except:
                pass
        finally:
            socks_socket.close()
            if connect_id and connect_id in self._message_queues:
                del self._message_queues[connect_id]

    async def _start_forward(self):
        """Connect to WebSocket server in forward proxy mode"""

        try:
            while True:
                try:
                    async with connect(self._ws_url) as websocket:
                        self._websocket = websocket

                        await websocket.send(json.dumps({"type": "auth", "reverse": False, "token": self._token}))
                        auth_response = await websocket.recv()
                        auth_data = json.loads(auth_response)

                        if not auth_data.get("success"):
                            logger.error("Authentication failed.")
                            return

                        logger.info("Authentication successful for forward proxy.")

                        tasks = [
                            asyncio.create_task(self._message_dispatcher(websocket)),
                            asyncio.create_task(self._heartbeat_handler(websocket)),
                            asyncio.create_task(self._run_socks_server()),
                        ]

                        done, pending = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED
                        )

                        for task in pending:
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

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        finally:
            await self._cleanup_connections()

    async def _start_reverse(self):
        """Connect to WebSocket server in reverse proxy mode"""

        try:
            while True:
                try:
                    async with connect(self._ws_url) as websocket:
                        # Send authentication information
                        await websocket.send(json.dumps({"type": "auth", "reverse": True, "token": self._token}))

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

                        try:
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

                            logger.warning("Connection lost, cleaning up...")

                        finally:
                            # Clean up existing connections
                            await self._cleanup_connections()

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
            await self._cleanup_connections()
            return

    async def _heartbeat_handler(self, websocket: ClientConnection):
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

    async def _cleanup_connections(self):
        """Clean up all existing connections"""

        # Clean up TCP connections
        for channel_id, sock in self._channels.items():
            try:
                sock.close()
            except:
                pass
        self._channels.clear()

        # Clean up UDP connections
        for channel_id, sock in self._udp_channels.items():
            try:
                sock.close()
            except:
                pass
        self._udp_channels.clear()

        # Clean up message queues
        self._message_queues.clear()

        logger.info("Cleaned up all existing connections.")

    async def start(self):
        """Start client"""
        if self._reverse:
            await self._start_reverse()
        else:
            await self._start_forward()


@click.command()
@click.option("--token", "-t", required=True, help="Authentication token")
@click.option("--url", "-u", default="ws://localhost:8765", help="WebSocket server address")
@click.option("--reverse", "-r", is_flag=True, default=False, help="Use reverse socks5 proxy")
@click.option(
    "--socks-host", "-h", default="127.0.0.1", help="SOCKS5 server listen address for forward proxy"
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
    debug: bool
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
