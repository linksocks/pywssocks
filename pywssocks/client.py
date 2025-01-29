import asyncio
import socket
import json
import logging
from typing import Optional
import uuid

import click
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.client import ClientConnection, connect


from pywssocks.common import init_logging

logger = logging.getLogger(__name__)


class WSSocksClient:
    def __init__(self, ws_url="ws://localhost:8765", token: Optional[str] = None):
        """Initialize WebSocket SOCKS5 client

        Args:
            ws_url: WebSocket server address
            token: Authentication token
        """

        self._ws_url = ws_url
        self._token = token

        # Map channel_id to TCP socket objects, {channel_id: socket}
        self._channels = {}

        # Map channel_id to UDP socket objects, {channel_id: socket}

        self._udp_channels = {}

        # Map channel_id to message queues, {channel_id: asyncio.Queue}
        self._message_queues = {}

    async def _handle_remote_connection(self, websocket: ClientConnection, request_data: dict):
        """Handle remote connection request"""

        channel_id = str(uuid.uuid4())
        connect_id = request_data["connect_id"]
        cmd = request_data.get("cmd", 0x01)  # Get SOCKS command type

        try:
            if cmd == 0x01:  # CONNECT
                await self._handle_tcp_connection(websocket, request_data, channel_id, connect_id)
            elif cmd == 0x03:  # UDP ASSOCIATE
                await self._handle_udp_association(websocket, request_data, channel_id, connect_id)
            else:
                raise Exception(f"Unsupported SOCKS5 command: {cmd}")

        except Exception as e:
            logger.error(f"Failed to process connection request: {e.__class__.__name__}: {e}.")
            response_data = {
                "type": "connect_response",
                "success": False,
                "error": str(e),
                "connect_id": connect_id,
            }
            await websocket.send(json.dumps(response_data))

    async def _handle_tcp_connection(
        self, websocket: ClientConnection, request_data: dict, channel_id: str, connect_id: str
    ):
        """Handle TCP connection request"""

        remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote_sock.settimeout(10)
        logger.debug(
            f"Attempting TCP connection to: {request_data['address']}:{request_data['port']}"
        )
        remote_sock.connect((request_data["address"], request_data["port"]))

        self._message_queues[channel_id] = asyncio.Queue()
        self._channels[channel_id] = remote_sock

        response_data = {
            "type": "connect_response",
            "success": True,
            "channel_id": channel_id,
            "connect_id": connect_id,
            "protocol": "tcp",
        }
        await websocket.send(json.dumps(response_data))

        await self.handle_tcp_forward(websocket, remote_sock, channel_id)

    async def _handle_udp_association(self, websocket: ClientConnection, request_data: dict, channel_id: str, connect_id: str):
        """Handle UDP forwarding request"""

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.bind(("0.0.0.0", 0))  # Bind to random port
        bound_address, bound_port = udp_sock.getsockname()

        self._message_queues[channel_id] = asyncio.Queue()
        self._udp_channels[channel_id] = udp_sock

        response_data = {
            "type": "connect_response",
            "success": True,
            "channel_id": channel_id,
            "connect_id": connect_id,
            "protocol": "udp",
            "bound_address": bound_address,
            "bound_port": bound_port,
        }
        await websocket.send(json.dumps(response_data))

        await self.handle_udp_forward(websocket, udp_sock, channel_id)

    async def handle_udp_forward(self, websocket: ClientConnection, udp_sock: socket.socket, channel_id: str):
        """Handle UDP data forwarding"""

        try:
            udp_sock.setblocking(False)
            while True:
                try:
                    # Read data from UDP socket
                    try:
                        data, addr = udp_sock.recvfrom(65507)  # UDP maximum packet size
                        if data:
                            msg = {
                                "type": "data",
                                "protocol": "udp",
                                "channel_id": channel_id,
                                "data": data.hex(),
                                "address": addr[0],
                                "port": addr[1],
                            }
                            await websocket.send(json.dumps(msg))
                            logger.debug(
                                f"Sent UDP data to WebSocket: channel={channel_id}, size={len(data)}."
                            )
                    except BlockingIOError:
                        pass

                    # Receive data from WebSocket server
                    try:
                        msg_data = await asyncio.wait_for(
                            self._message_queues[channel_id].get(), timeout=0.1
                        )
                        binary_data = bytes.fromhex(msg_data["data"])
                        target_addr = (msg_data["address"], msg_data["port"])
                        udp_sock.sendto(binary_data, target_addr)
                        logger.debug(
                            f"Sent UDP data to target: channel={channel_id}, size={len(binary_data)}."
                        )
                    except asyncio.TimeoutError:
                        continue

                except Exception as e:
                    logger.error(f"UDP forwarding error: {e.__class__.__name__}: {e}.")
                    break

        finally:
            udp_sock.close()
            if channel_id in self._udp_channels:
                del self._udp_channels[channel_id]
            if channel_id in self._message_queues:
                del self._message_queues[channel_id]

    async def handle_tcp_forward(self, websocket: ClientConnection, remote_sock: socket.socket, channel_id: str):
        """Handle TCP data forwarding"""

        try:
            remote_sock.setblocking(False)
            while True:
                try:
                    # Read data from remote server
                    try:
                        data = remote_sock.recv(4096)
                        if data:
                            msg = {
                                "type": "data",
                                "protocol": "tcp",
                                "channel_id": channel_id,
                                "data": data.hex(),
                            }
                            await websocket.send(json.dumps(msg))
                    except BlockingIOError:
                        pass

                    # Receive data from WebSocket server
                    try:
                        msg_data = await asyncio.wait_for(
                            self._message_queues[channel_id].get(), timeout=0.1
                        )
                        binary_data = bytes.fromhex(msg_data["data"])
                        remote_sock.send(binary_data)
                        logger.debug(
                            f"Sent TCP data to remote server: channel={channel_id}, size={len(binary_data)}."
                        )
                    except asyncio.TimeoutError:
                        continue

                except Exception as e:
                    logger.error(f"TCP forwarding error: {e.__class__.__name__}: {e}.")
                    break

        finally:
            remote_sock.close()
            if channel_id in self._channels:
                del self._channels[channel_id]
            if channel_id in self._message_queues:
                del self._message_queues[channel_id]

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
                    logger.debug(f"Received connection request: {data}")
                    await self._connect_queue.put(data)
                else:
                    logger.warning(f"Received unknown message type: {data['type']}.")
        except ConnectionClosed:
            logger.error("WebSocket connection closed.")
        except Exception as e:
            logger.error(f"Message dispatcher error: {e.__class__.__name__}: {e}.")

    async def _connect(self):
        """Connect to WebSocket server"""

        try:
            while True:
                try:
                    async with connect(self._ws_url) as websocket:
                        # Send authentication information
                        await websocket.send(json.dumps({"type": "auth", "token": self._token}))

                        # Wait for authentication response
                        auth_response = await websocket.recv()
                        auth_data = json.loads(auth_response)

                        if not auth_data.get("success"):
                            logger.error("Authentication failed")
                            return

                        logger.info("Authentication successful.")

                        # Create connection request queue
                        self._connect_queue = asyncio.Queue()

                        # Start message dispatcher and heartbeat tasks
                        tasks = [
                            asyncio.create_task(self._message_dispatcher(websocket)),
                            asyncio.create_task(self._heartbeat_handler(websocket)),
                            asyncio.create_task(self._process_connection_requests(websocket)),
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
                    logger.debug("Heartbeat: Sent ping, received pong")
                except asyncio.TimeoutError:
                    logger.warning("Heartbeat: Pong timeout")
                    break
                except ConnectionClosed:
                    logger.warning("WebSocket connection closed, stopping heartbeat")
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
            logger.info("Heartbeat handler stopped")

    async def _process_connection_requests(self, websocket: ClientConnection):
        """Process connection requests in separate task"""

        try:
            while True:
                # Handle new connection request
                request_data = await self._connect_queue.get()
                asyncio.create_task(self._handle_remote_connection(websocket, request_data))
        except Exception as e:
            logger.error(f"Connection request processing error: {e.__class__.__name__}: {e}.")
            raise

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

        logger.info("Cleaned up all existing connections")

    def start(self):
        """Start client"""
        asyncio.run(self._connect())


@click.command()
@click.option("--token", "-t", required=True, help="Authentication token")
@click.option("--url", "-u", default="ws://localhost:8765", help="WebSocket server address")
@click.option("--debug", "-d", is_flag=True, default=False, help="Show debug logs")
def _client_cli(token: str, url: str, debug: bool):
    """Start SOCKS5 over WebSocket proxy client"""

    init_logging(level=logging.DEBUG if debug else logging.INFO)

    # Start server
    client = WSSocksClient(ws_url=url, token=token)
    client.start()


if __name__ == "__main__":
    _client_cli()
