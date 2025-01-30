import asyncio
import logging
import socket
import json
from typing import Dict
import uuid

from websockets.asyncio.connection import Connection
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

class Relay:
    def __init__(self, buffer_size: int = 32768):
        self._buf_size = buffer_size
        
        # Map channel_id to message queues
        self._message_queues: Dict[str, asyncio.Queue] = {}
        
        # Map channel_id to TCP socket objects
        self._channels: Dict[str, socket.socket] = {}

        # Map channel_id to UDP socket objects
        self._udp_channels: Dict[str, socket.socket] = {}
    
    async def _handle_network_connection(self, websocket: Connection, request_data: dict):
        """Handle remote connection request"""

        channel_id = str(uuid.uuid4()) # channel_id is the message_queue index on our side.
        connect_id = request_data["connect_id"] # connect_id is the message_queue index on the connector side.
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
        self, websocket: Connection, request_data: dict, channel_id: str, connect_id: str
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

        await self._handle_remote_tcp_forward(websocket, remote_sock, channel_id)

    async def _handle_udp_association(
        self, websocket: Connection, request_data: dict, channel_id: str, connect_id: str
    ):
        """Handle UDP forwarding request"""

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.bind(("0.0.0.0", 0))  # Bind to random port

        self._message_queues[channel_id] = asyncio.Queue()
        self._udp_channels[channel_id] = udp_sock

        response_data = {
            "type": "connect_response",
            "success": True,
            "channel_id": channel_id,
            "connect_id": connect_id,
            "protocol": "udp",
        }
        await websocket.send(json.dumps(response_data))

        await self._handle_udp_forward(websocket, udp_sock, channel_id)

    async def _handle_udp_forward(
        self, websocket: Connection, udp_sock: socket.socket, channel_id: str
    ):
        """Handle UDP data forwarding"""

        try:
            # udp_sock.setblocking(False)
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

    async def _handle_remote_tcp_forward(
        self, websocket: Connection, remote_socket: socket.socket, channel_id: str
    ):
        """Handle TCP data forwarding"""

        try:
            remote_socket.setblocking(False)
            while True:
                try:
                    # Read data from remote server
                    try:
                        data = remote_socket.recv(self._buf_size)
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
                        remote_socket.send(binary_data)
                        logger.debug(
                            f"Sent TCP data to remote server: channel={channel_id}, size={len(binary_data)}."
                        )
                    except asyncio.TimeoutError:
                        continue
                except OSError:
                    break
                except Exception as e:
                    logger.error(f"TCP forwarding error: {e.__class__.__name__}: {e}.")
                    break

        finally:
            remote_socket.close()
            if channel_id in self._channels:
                del self._channels[channel_id]
            if channel_id in self._message_queues:
                del self._message_queues[channel_id]
                
    async def _handle_socks_tcp_forward(
        self, websocket: Connection, socks_socket: socket.socket, channel_id: str
    ) -> None:
        """Handle TCP data forwarding"""

        try:
            message_queue = asyncio.Queue()
            self._message_queues[channel_id] = message_queue

            while True:
                try:
                    # Read data from SOCKS client
                    try:
                        data = socks_socket.recv(self._buf_size)
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
                        socks_socket.send(binary_data)
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
            socks_socket.close()
            if channel_id in self._message_queues:
                del self._message_queues[channel_id]
                
    async def _handle_socks_udp_associate(
        self, websocket: Connection, socks_socket: socket.socket, channel_id: str
    ):
        """Handle UDP forwarding association"""

        try:
            # Keep TCP connection until client disconnects
            while True:
                try:
                    # Check if TCP connection is closed
                    data = await asyncio.get_event_loop().sock_recv(socks_socket, 1)
                    if not data:
                        break
                except:
                    break
        finally:
            socks_socket.close()