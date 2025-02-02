from typing import Dict, Optional
import asyncio
import logging
import socket
import json
import uuid
import struct

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
        
        # Map channel_id to associated UDP socket objects
        self._udp_channels: Dict[str, socket.socket] = {}

    async def _refuse_socks_request(
        self,
        socks_socket: socket.socket,
        reason: int = 0x03,
    ):
        """
        Refuse SOCKS5 client request
        
        SOCKS5_REPLY = {
            0x00: "succeeded",
            0x01: "general SOCKS server failure",
            0x02: "connection not allowed by ruleset",
            0x03: "network unreachable",
            0x04: "host unreachable",
            0x05: "connection refused",
            0x06: "TTL expired",
            0x07: "command not supported",
            0x08: "address type not supported",
            0x09: "to 0xFF unassigned"
        }
        """
        
        loop = asyncio.get_event_loop()
        data = await loop.sock_recv(socks_socket, 1024)
        if not data or data[0] != 0x05:
            return
        await loop.sock_sendall(socks_socket, bytes([0x05, 0x00]))
        data = await loop.sock_recv(socks_socket, 1024)
        if not data or len(data) < 7:
            return
        await loop.sock_sendall(
            socks_socket,
            bytes([0x05, reason, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        )

    async def _handle_socks_request(
        self,
        websocket: Connection,
        socks_socket: socket.socket,
        socks_username: Optional[str] = None,
        socks_password: Optional[str] = None,
    ) -> None:
        """Handle SOCKS5 client request"""

        client_id = id(websocket)
        connect_id = f"{client_id}_{str(uuid.uuid4())}"
        logger.debug(f"Starting SOCKS request handling for connect_id: {connect_id}")

        try:
            socks_socket.setblocking(False)
            loop = asyncio.get_event_loop()

            # Authentication negotiation
            logger.debug(f"Starting SOCKS authentication for connect_id: {connect_id}")
            data = await loop.sock_recv(socks_socket, 2)
            version, nmethods = struct.unpack("!BB", data)
            methods = await loop.sock_recv(socks_socket, nmethods)

            if socks_username and socks_password:
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

                if username != socks_username or password != socks_password:
                    await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x01, 0x01))
                    return
                await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x01, 0x00))
            else:
                # No authentication required
                await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x05, 0x00))

            logger.debug(f"SOCKS authentication completed for connect_id: {connect_id}")

            # Check WebSocket connection by attempting a ping
            try:
                await websocket.ping()
            except ConnectionClosed:
                logger.debug(f"WebSocket closed for connect_id: {connect_id}")
                # Return connection failure response to SOCKS client (0x03 = Network unreachable)
                await loop.sock_sendall(
                    socks_socket,
                    bytes([0x05, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                )
                return

            # Get request details
            header = await loop.sock_recv(socks_socket, 4)
            version, cmd, _, atyp = struct.unpack("!BBBB", header)

            if cmd == 0x01:  # CONNECT
                protocol = "tcp"
            elif cmd == 0x03:  # UDP ASSOCIATE
                protocol = "udp"
            else:
                socks_socket.close()
                return

            # Create a temporary queue for connection response
            connect_queue = asyncio.Queue()
            self._message_queues[connect_id] = connect_queue

            request_data = {
                "type": "connect",
                "connect_id": connect_id,
                "protocol": protocol,
            }

            if protocol == "tcp":
                # Parse target address
                if atyp == 0x01:  # IPv4
                    addr_bytes = await loop.sock_recv(socks_socket, 4)
                    target_addr = socket.inet_ntoa(addr_bytes)
                elif atyp == 0x03:  # Domain name
                    addr_len = (await loop.sock_recv(socks_socket, 1))[0]
                    addr_bytes = await loop.sock_recv(socks_socket, addr_len)
                    target_addr = addr_bytes.decode()
                elif atyp == 0x04:  # IPv6
                    addr_bytes = await loop.sock_recv(socks_socket, 16)
                    target_addr = socket.inet_ntop(socket.AF_INET6, addr_bytes)
                else:
                    socks_socket.close()
                    return

                # Get port
                port_bytes = await loop.sock_recv(socks_socket, 2)
                target_port = struct.unpack("!H", port_bytes)[0]

                request_data["address"] = target_addr
                request_data["port"] = target_port

            # Send connection request to server
            await websocket.send(json.dumps(request_data))

            # Use asyncio.shield to prevent timeout cancellation causing queue cleanup
            response_future = asyncio.shield(connect_queue.get())
            try:
                # Wait for client connection result
                response = await asyncio.wait_for(response_future, timeout=10)
                response_data = json.loads(response) if isinstance(response, str) else response
            except asyncio.TimeoutError:
                # Ensure cleanup on timeout
                response_future.cancel()
                logger.error("Connection response timeout.")
                # Return connection failure response to SOCKS client (0x04 = Host unreachable)
                await loop.sock_sendall(
                    socks_socket,
                    bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                )

            if not response_data.get("success", False):
                # Connection failed, return failure response to SOCKS client
                error_msg = response_data.get("error", "Connection failed")
                logger.error(f"Target connection failed: {error_msg}.")
                # Return connection failure response to SOCKS client (0x04 = Host unreachable)
                await loop.sock_sendall(
                    socks_socket,
                    bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                )
                return

            if protocol == "tcp":
                # TCP connection successful, return success response
                await loop.sock_sendall(
                    socks_socket,
                    bytes([0x05, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                )
                await self._handle_socks_tcp_forward(
                    websocket, socks_socket, response_data["channel_id"]
                )
            else:
                # Create UDP socket for local communication
                udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp_socket.bind(("127.0.0.1", 0))  # Bind to random port
                udp_socket.setblocking(False)

                # Get the UDP socket's bound address and port
                _, bound_port = udp_socket.getsockname()

                # Send UDP binding information back to SOCKS client
                # Use the same IP as the TCP connection for the response
                bind_ip = socket.inet_aton("127.0.0.1")
                bind_port_bytes = struct.pack("!H", bound_port)
                reply = struct.pack("!BBBB", 0x05, 0x00, 0x00, 0x01) + bind_ip + bind_port_bytes

                loop = asyncio.get_event_loop()
                await loop.sock_sendall(socks_socket, reply)
                logger.debug(f"UDP association established on port {bound_port}")

                await self._handle_socks_udp_forward(
                    websocket, socks_socket, udp_socket, response_data["channel_id"]
                )
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

    async def _handle_network_connection(self, websocket: Connection, request_data: dict):
        protocol = request_data.get("protocol", None)
        if protocol == "tcp":
            return await self._handle_tcp_connection(websocket, request_data)
        elif protocol == "udp":
            return await self._handle_udp_connection(websocket, request_data)

    async def _handle_tcp_connection(self, websocket: Connection, request_data: dict):
        """Connect to remote tcp socket send response to websocket."""

        # channel_id is the message_queue index on our side
        channel_id = str(uuid.uuid4())

        # connect_id is the message_queue index on the connector side
        connect_id = request_data["connect_id"]

        try:
            # Determine address family based on address format
            try:
                socket.inet_pton(socket.AF_INET6, request_data["address"])
                addr_family = socket.AF_INET6
            except socket.error:
                try:
                    socket.inet_pton(socket.AF_INET, request_data["address"])
                    addr_family = socket.AF_INET
                except socket.error:
                    # Try to resolve hostname
                    try:
                        addrinfo = socket.getaddrinfo(
                            request_data["address"], request_data["port"], proto=socket.IPPROTO_TCP
                        )
                        addr_family = addrinfo[0][0]  # Use the first returned address family
                    except socket.gaierror as e:
                        raise Exception(f"Failed to resolve address: {e}")

            remote_sock = socket.socket(addr_family, socket.SOCK_STREAM)
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

        except Exception as e:
            logger.error(f"Failed to process connection request: {e.__class__.__name__}: {e}.")
            response_data = {
                "type": "connect_response",
                "success": False,
                "error": str(e),
                "connect_id": connect_id,
            }
            await websocket.send(json.dumps(response_data))

    async def _handle_udp_connection(self, websocket: Connection, request_data: dict):
        """Connect to remote udp socket send response to websocket."""

        # channel_id is the message_queue index on our side
        channel_id = str(uuid.uuid4())

        # connect_id is the message_queue index on the connector side
        connect_id = request_data["connect_id"]
        
        # Create local UDP socket
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.bind(("0.0.0.0", 0))  # Bind to random port
        udp_socket.setblocking(False)

        # Get the UDP socket's bound address and port
        _, bound_port = udp_socket.getsockname()

        self._message_queues[channel_id] = asyncio.Queue()

        response_data = {
            "type": "connect_response",
            "success": True,
            "channel_id": channel_id,
            "connect_id": connect_id,
            "protocol": "udp",
        }
        await websocket.send(json.dumps(response_data))

        await self._handle_remote_udp_forward(websocket, udp_socket, channel_id)

    async def _handle_remote_udp_forward(
        self, websocket: Connection, local_socket: socket.socket, channel_id: str
    ):
        """Read from remote udp socket and send to websocket, and vice versa."""

        try:
            local_socket.setblocking(False)
            while True:
                try:
                    # Read data from local UDP socket
                    try:
                        data, addr = local_socket.recvfrom(min(self._buf_size, 65507))  # Max UDP packet size
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
                        target_addr = (msg_data["target_addr"], msg_data["target_port"])
                        local_socket.sendto(binary_data, target_addr)
                        logger.debug(
                            f"Sent UDP data to target: channel={channel_id}, size={len(binary_data)}."
                        )
                    except asyncio.TimeoutError:
                        continue

                except Exception as e:
                    logger.error(f"UDP forwarding error: {e.__class__.__name__}: {e}.")
                    break

        finally:
            local_socket.close()
            if channel_id in self._udp_channels:
                del self._udp_channels[channel_id]
            if channel_id in self._message_queues:
                del self._message_queues[channel_id]

    async def _handle_remote_tcp_forward(
        self, websocket: Connection, remote_socket: socket.socket, channel_id: str
    ):
        """Read from remote tcp socket and send to websocket, and vice versa."""

        try:
            remote_socket.setblocking(False)
            while True:
                try:
                    # Read data from remote server
                    try:
                        data = remote_socket.recv(min(self._buf_size, 65535))  # Max TCP packet size
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
        """Read from websocket and send to socks socket, and vice versa."""

        try:
            message_queue = asyncio.Queue()
            self._message_queues[channel_id] = message_queue

            socks_socket.setblocking(False)

            while True:
                try:
                    # Read data from SOCKS client
                    try:
                        data = socks_socket.recv(min(self._buf_size, 65535))  # Max TCP packet size
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
            socks_socket.close()

    async def _handle_socks_udp_forward(
        self,
        websocket: Connection,
        socks_socket: socket.socket,
        udp_socket: socket.socket,
        channel_id: str,
    ):
        """Read from websocket and send to a associated UDP socket, and vice versa."""
        try:
            # Store UDP socket and message queue
            self._message_queues[channel_id] = asyncio.Queue()
            self._udp_channels[channel_id] = udp_socket
            
            addr = None  # Store socks connector address
            socks_socket.setblocking(False)

            while True:
                try:
                    # Check if TCP connection is closed (indicates client termination)
                    try:
                        data = socks_socket.recv(1)
                        if not data:
                            break
                    except (BlockingIOError, ConnectionError):
                        pass

                    # Handle UDP data from local client
                    try:
                        data, addr = udp_socket.recvfrom(min(self._buf_size, 65507))
                        if data:
                            # First 3 bytes are reserved + fragment ID + address type
                            # Skip SOCKS UDP header and get actual data
                            if len(data) > 3:  # Minimal UDP header
                                header = data[0:3]
                                atyp = data[3]
                                if atyp == 0x01:  # IPv4
                                    addr_size = 4
                                    addr_bytes = data[4:8]
                                    target_addr = socket.inet_ntoa(addr_bytes)
                                    port_bytes = data[8:10]
                                    target_port = int.from_bytes(port_bytes, "big")
                                    payload = data[10:]
                                elif atyp == 0x03:  # Domain
                                    addr_len = data[4]
                                    addr_bytes = data[5 : 5 + addr_len]
                                    target_addr = addr_bytes.decode()
                                    port_bytes = data[5 + addr_len : 7 + addr_len]
                                    target_port = int.from_bytes(port_bytes, "big")
                                    payload = data[7 + addr_len :]
                                else:
                                    continue

                                msg = {
                                    "type": "data",
                                    "protocol": "udp",
                                    "channel_id": channel_id,
                                    "data": payload.hex(),
                                    "target_addr": target_addr,
                                    "target_port": target_port,
                                }
                                await websocket.send(json.dumps(msg))
                                logger.debug(
                                    f"Sent UDP data to WebSocket: channel={channel_id}, size={len(payload)}"
                                )
                    except BlockingIOError:
                        pass

                    # Handle data from WebSocket
                    try:
                        msg_data = await asyncio.wait_for(
                            self._message_queues[channel_id].get(), timeout=0.1
                        )
                        binary_data = bytes.fromhex(msg_data["data"])

                        if not addr:  # Skip if no client address available
                            logger.warning(f"Dropping UDP packet: no socks user address available.")
                            continue
                            
                        # Construct SOCKS UDP header
                        udp_header = bytearray([0, 0, 0])  # RSV + FRAG
                        from_addr = msg_data["address"]
                        from_port = msg_data["port"]

                        try:
                            # Try parsing as IPv4
                            addr_bytes = socket.inet_aton(from_addr)
                            udp_header.append(0x01)  # ATYP = IPv4
                            udp_header.extend(addr_bytes)
                        except socket.error:
                            # Treat as domain name
                            domain_bytes = from_addr.encode()
                            udp_header.append(0x03)  # ATYP = Domain
                            udp_header.append(len(domain_bytes))
                            udp_header.extend(domain_bytes)

                        udp_header.extend(from_port.to_bytes(2, "big"))
                        udp_header.extend(binary_data)

                        # Send to UDP client
                        udp_socket.sendto(bytes(udp_header), addr)
                        logger.debug(
                            f"Sent UDP data to client: channel={channel_id}, size={len(binary_data)}"
                        )
                    except asyncio.TimeoutError:
                        continue

                except Exception as e:
                    logger.error(f"UDP forwarding error: {e.__class__.__name__}: {e}")
                    break
        finally:
            udp_socket.close()
            socks_socket.close()
            if channel_id in self._udp_channels:
                del self._udp_channels[channel_id]
            if channel_id in self._message_queues:
                del self._message_queues[channel_id]
