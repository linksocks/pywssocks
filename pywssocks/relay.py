from typing import Dict, Optional
import asyncio
import logging
import socket
import uuid
import struct

from websockets.asyncio.connection import Connection

from .message import (
    ConnectResponseMessage,
    DataMessage,
    DisconnectMessage,
    pack_message,
    ConnectMessage,
    parse_message,
)

_default_logger = logging.getLogger(__name__)


class UDPProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler with send and receive queues"""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.transport: Optional[socket.socket] = None
        self.recv_queue = asyncio.Queue()
        self._ready = asyncio.Event()
        self._log = logger or _default_logger

    def connection_made(self, transport: socket.socket):
        self.transport = transport
        self._ready.set()

    def datagram_received(self, data, addr):
        self.recv_queue.put_nowait((data, addr))

    def error_received(self, exc):
        logging.error(f"UDP Protocol error: {exc}")

    def connection_lost(self, exc):
        if exc:
            logging.error(f"UDP connection lost with error: {exc}")
        self._ready.clear()

    async def wait_ready(self):
        await self._ready.wait()

    async def send(self, data, addr):
        await self.wait_ready()
        if self.transport:
            self.transport.sendto(data, addr)

    async def receive(self):
        return await self.recv_queue.get()


class Relay:
    """A relay that handles stream transport between SOCKS5 and WebSocket"""

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        buffer_size: int = 32768,
        upstream_proxy: Optional[str] = None,
        upstream_username: Optional[str] = None,
        upstream_password: Optional[str] = None,
    ):
        """
        Args:
            logger: Optional logger instance for relay operations. If not provided,
                   a default logger will be used.
            buffer_size: Size of the buffer used for data transfer. Defaults to 32KB.
            upstream_proxy: Optional SOCKS5 proxy to use for outgoing connections (format: host:port)
            upstream_username: Optional username for upstream SOCKS5 proxy authentication
            upstream_password: Optional password for upstream SOCKS5 proxy authentication
        """
        self._log = logger or _default_logger
        self._buf_size = buffer_size
        self._upstream_proxy = upstream_proxy
        self._upstream_username = upstream_username
        self._upstream_password = upstream_password

        # Map channel_id to message queues
        self._message_queues: Dict[str, asyncio.Queue] = {}

        # Map channel_id to associated UDP client hostname and port
        self._udp_client_addrs: Dict[str, str] = {}

        # Map channel_id to TCP tasks
        self._tcp_tasks: Dict[str, asyncio.Task] = {}

        # Map channel_id to UDP tasks
        self._udp_tasks: Dict[str, asyncio.Task] = {}

        # Map channel_id to connection success status
        self._connection_success_map: Dict[str, bool] = {}

    def set_connection_success(self, channel_id: str) -> None:
        """Set the connection success status for a channel.

        Args:
            channel_id: The channel ID to mark as successfully connected.
        """
        self._connection_success_map[channel_id] = True

    def disconnect_channel(self, channel_id: str, delay: float = 5.0) -> None:
        """Disconnect and cleanup resources for a specific channel.

        Args:
            channel_id: The channel ID to disconnect and cleanup.
            delay: Delay in seconds before cleaning up message queue to handle
                   late-arriving packets. Defaults to 5.0 seconds.
        """
        self._log.debug(f"Disconnecting channel: {channel_id}")

        # Cancel TCP tasks if they exist
        if channel_id in self._tcp_tasks:
            task = self._tcp_tasks.pop(channel_id)
            if not task.done():
                task.cancel()

        # Cancel UDP tasks if they exist
        if channel_id in self._udp_tasks:
            task = self._udp_tasks.pop(channel_id)
            if not task.done():
                task.cancel()

        # Clean up UDP client addresses
        if channel_id in self._udp_client_addrs:
            del self._udp_client_addrs[channel_id]

        # Clean up connection success status
        if channel_id in self._connection_success_map:
            del self._connection_success_map[channel_id]

        # Delay cleanup of message queue to handle late-arriving packets
        if delay > 0 and channel_id in self._message_queues:
            asyncio.create_task(self._delayed_queue_cleanup(channel_id, delay))
        elif channel_id in self._message_queues:
            del self._message_queues[channel_id]
            self._log.debug(f"Cleaned up channel: {channel_id}")

    async def _delayed_queue_cleanup(self, channel_id: str, delay: float) -> None:
        """Cleanup message queue after a delay to handle late-arriving packets.

        Args:
            channel_id: The channel ID to cleanup.
            delay: Delay in seconds before cleanup.
        """
        await asyncio.sleep(delay)
        if channel_id in self._message_queues:
            del self._message_queues[channel_id]
            self._log.debug(f"Cleaned up channel after delay: {channel_id}")

    def log_message(self, msg, direction: str) -> None:
        """Log WebSocket message details at debug level.

        Args:
            msg: The message object to log
            direction: Direction of message flow ('send' or 'recv')
            label: Custom label for the log entry
        """
        # Only log if debug level is enabled
        if not self._log.isEnabledFor(logging.DEBUG):
            return

        # Create a copy of message data for logging
        try:
            import json
            import copy
            from uuid import UUID

            msg_copy = copy.deepcopy(msg.__dict__)

            # Convert all UUID objects to strings
            for key, value in msg_copy.items():
                if isinstance(value, UUID):
                    msg_copy[key] = str(value)

            # Remove sensitive fields and add data length
            if "data" in msg_copy:
                msg_copy["data_length"] = len(msg_copy["data"])
                del msg_copy["data"]

            if "token" in msg_copy:
                msg_copy["token"] = "..."

            self._log.debug(
                f"WebSocket message TYPE={msg.__class__.__name__} DIRECTION={direction} MSG={json.dumps(msg_copy)}"
            )
        except Exception as e:
            self._log.error(f"Failed to log message: {str(e)}")

    async def _refuse_socks_request(
        self,
        socks_socket: socket.socket,
        reason: int = 0x03,
    ):
        """Refuse SOCKS5 client request"""

        # SOCKS5_REPLY = {
        #     0x00: "succeeded",
        #     0x01: "general SOCKS server failure",
        #     0x02: "connection not allowed by ruleset",
        #     0x03: "network unreachable",
        #     0x04: "host unreachable",
        #     0x05: "connection refused",
        #     0x06: "TTL expired",
        #     0x07: "command not supported",
        #     0x08: "address type not supported",
        #     0x09: "to 0xFF unassigned"
        # }

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

        connect_id = str(uuid.uuid4())
        self._log.debug(f"Starting SOCKS request handling for connect_id: {connect_id}")

        try:
            loop = asyncio.get_event_loop()

            # Authentication negotiation
            self._log.debug(
                f"Starting SOCKS authentication for connect_id: {connect_id}"
            )
            data = await loop.sock_recv(socks_socket, 2)

            version, nmethods = struct.unpack("!BB", data)
            methods = await loop.sock_recv(socks_socket, nmethods)

            if socks_username and socks_password:
                # Require username/password authentication
                if 0x02 not in methods:
                    await loop.sock_sendall(
                        socks_socket, struct.pack("!BB", 0x05, 0xFF)
                    )
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
                    await loop.sock_sendall(
                        socks_socket, struct.pack("!BB", 0x01, 0x01)
                    )
                    return
                await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x01, 0x00))
            else:
                # No authentication required
                await loop.sock_sendall(socks_socket, struct.pack("!BB", 0x05, 0x00))

            self._log.debug(
                f"SOCKS authentication completed for connect_id: {connect_id}"
            )

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

            request_msg = ConnectMessage(
                protocol=protocol,
                channel_id=uuid.UUID(connect_id),
                address=None,
                port=None,
            )

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

                request_msg.address = target_addr
                request_msg.port = target_port

            # Send connection request to server
            self.log_message(request_msg, "send")
            await websocket.send(pack_message(request_msg))

            # Use asyncio.shield to prevent timeout cancellation causing queue cleanup
            response_future = asyncio.shield(connect_queue.get())
            try:
                # Wait for client connection result
                response = await asyncio.wait_for(response_future, timeout=10)
                response_msg = (
                    parse_message(response) if isinstance(response, bytes) else response
                )
                if not isinstance(response_msg, ConnectResponseMessage):
                    raise ValueError("Unexpected response message type")
            except asyncio.TimeoutError:
                # Ensure cleanup on timeout
                response_future.cancel()
                self._log.error("Connection response timeout.")
                # Return connection failure response to SOCKS client (0x04 = Host unreachable)
                await loop.sock_sendall(
                    socks_socket,
                    bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                )
                return

            if not response_msg.success:
                # Connection failed, return failure response to SOCKS client
                error_msg = response_msg.error or "Connection failed"
                self._log.error(f"Target connection failed: {error_msg}.")
                # Return connection failure response to SOCKS client (0x04 = Host unreachable)
                await loop.sock_sendall(
                    socks_socket,
                    bytes([0x05, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                )
                return

            if protocol == "tcp":
                # TCP connection successful, return success response
                self._log.debug(
                    f"Remote successfully connected to {target_addr}:{target_port}."
                )
                await loop.sock_sendall(
                    socks_socket,
                    bytes([0x05, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
                )
                await self._handle_socks_tcp_forward(
                    websocket, socks_socket, str(response_msg.channel_id)
                )
            else:
                # Create UDP socket for local communication
                self._log.debug(f"Remote is ready to accept udp connection request.")
                udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp_socket.bind(("127.0.0.1", 0))  # Bind to random port
                udp_socket.setblocking(False)

                try:
                    # Get the UDP socket's bound address and port
                    _, bound_port = udp_socket.getsockname()

                    # Send UDP binding information back to SOCKS client
                    # Use the same IP as the TCP connection for the response
                    bind_ip = socket.inet_aton("127.0.0.1")
                    bind_port_bytes = struct.pack("!H", bound_port)
                    reply = (
                        struct.pack("!BBBB", 0x05, 0x00, 0x00, 0x01)
                        + bind_ip
                        + bind_port_bytes
                    )

                    loop = asyncio.get_event_loop()
                    await loop.sock_sendall(socks_socket, reply)
                    self._log.debug(f"UDP association established on port {bound_port}")

                    await self._handle_socks_udp_forward(
                        websocket,
                        socks_socket,
                        udp_socket,
                        str(response_msg.channel_id),
                    )
                finally:
                    udp_socket.close()
        except Exception as e:
            self._log.error(
                f"Error handling SOCKS request: {e.__class__.__name__}: {e}."
            )
            try:
                reply = struct.pack("!BBBB", 0x05, 0x01, 0x00, 0x01)
                reply += socket.inet_aton("0.0.0.0") + struct.pack("!H", 0)
                await loop.sock_sendall(socks_socket, reply)
            except:
                pass
        finally:
            socks_socket.close()
            if connect_id in self._message_queues:
                del self._message_queues[connect_id]

    async def _handle_network_connection(
        self, websocket: Connection, request_msg: ConnectMessage
    ):
        protocol = request_msg.protocol
        if protocol == "tcp":
            return await self._handle_tcp_connection(websocket, request_msg)
        elif protocol == "udp":
            return await self._handle_udp_connection(websocket, request_msg)

    async def _handle_tcp_connection(
        self, websocket: Connection, request_msg: ConnectMessage
    ):
        """Connect to remote tcp socket send response to websocket."""

        remote_socket = None
        loop = asyncio.get_running_loop()

        try:
            # Validate required fields
            if request_msg.address is None or request_msg.port is None:
                raise ValueError("Missing address or port in connection request")
            else:
                self._log.debug(
                    f"Attempting TCP connection to {request_msg.address}:{request_msg.port}."
                )

            if self._upstream_proxy:
                # Connect through upstream SOCKS5 proxy
                self._log.debug(
                    f"Connecting to {request_msg.address}:{request_msg.port} through upstream proxy {self._upstream_proxy}"
                )
                remote_socket = await self._connect_via_socks5(
                    request_msg.address, request_msg.port
                )
            else:
                # Direct connection
                # Determine address family and resolved address based on address format
                connect_address = request_msg.address
                try:
                    socket.inet_pton(socket.AF_INET6, request_msg.address)
                    addr_family = socket.AF_INET6
                except socket.error:
                    try:
                        socket.inet_pton(socket.AF_INET, request_msg.address)
                        addr_family = socket.AF_INET
                    except socket.error:
                        # Try to resolve hostname
                        try:
                            addrinfo = socket.getaddrinfo(
                                request_msg.address,
                                request_msg.port,
                                proto=socket.IPPROTO_TCP,
                            )
                            addr_family = addrinfo[0][0]
                            # Use the resolved address from getaddrinfo to avoid
                            # address family mismatch on Windows (WinError 10022)
                            connect_address = addrinfo[0][4][0]
                        except socket.gaierror as e:
                            raise Exception(f"Failed to resolve address: {e}")

                remote_socket = socket.socket(addr_family, socket.SOCK_STREAM)
                remote_socket.setblocking(False)
                self._log.debug(
                    f"Attempting direct TCP connection to: {connect_address}:{request_msg.port} (original: {request_msg.address})"
                )
                await loop.sock_connect(
                    remote_socket, (connect_address, request_msg.port)
                )

            response_msg = ConnectResponseMessage(
                success=True, channel_id=request_msg.channel_id
            )
            self.log_message(response_msg, "send")
            await websocket.send(pack_message(response_msg))

            await self._handle_remote_tcp_forward(
                websocket, remote_socket, str(request_msg.channel_id)
            )

        except Exception as e:
            self._log.error(
                f"Failed to process connection request: {e.__class__.__name__}: {e}."
            )
            response_msg = ConnectResponseMessage(
                success=False, channel_id=request_msg.channel_id, error=str(e)
            )
            self.log_message(response_msg, "send")
            await websocket.send(pack_message(response_msg))

        finally:
            if remote_socket:
                remote_socket.close()
            # Send disconnect message to notify the other side
            try:
                disconnect_msg = DisconnectMessage(channel_id=request_msg.channel_id)
                self.log_message(disconnect_msg, "send")
                await websocket.send(pack_message(disconnect_msg))
            except Exception:
                pass
            # Use delayed cleanup to handle late-arriving packets
            self.disconnect_channel(str(request_msg.channel_id))

    async def _handle_udp_connection(
        self, websocket: Connection, request_msg: ConnectMessage
    ):
        """Connect to remote udp socket send response to websocket."""

        local_socket = None

        try:
            # Create local UDP socket
            local_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            local_socket.bind(("0.0.0.0", 0))  # Bind to random port
            local_socket.setblocking(False)

            # Get the UDP socket's bound address and port
            _, bound_port = local_socket.getsockname()

            response_msg = ConnectResponseMessage(
                success=True, channel_id=request_msg.channel_id, error=None
            )
            self.log_message(response_msg, "send")
            await websocket.send(pack_message(response_msg))

            await self._handle_remote_udp_forward(
                websocket, local_socket, str(request_msg.channel_id)
            )
        except Exception as e:
            self._log.error(
                f"Failed to process connection request: {e.__class__.__name__}: {e}."
            )
            response_msg = ConnectResponseMessage(
                success=False, channel_id=request_msg.channel_id, error=str(e)
            )
            self.log_message(response_msg, "send")
            await websocket.send(pack_message(response_msg))
        finally:
            if local_socket:
                local_socket.close()
            # Send disconnect message to notify the other side
            try:
                disconnect_msg = DisconnectMessage(channel_id=request_msg.channel_id)
                self.log_message(disconnect_msg, "send")
                await websocket.send(pack_message(disconnect_msg))
            except Exception:
                pass
            # Use delayed cleanup to handle late-arriving packets
            self.disconnect_channel(str(request_msg.channel_id))

    async def _udp_to_websocket(
        self, websocket: Connection, udp_socket: socket.socket, channel_id: str
    ):
        """Handle UDP to WebSocket forwarding"""

        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            UDPProtocol, sock=udp_socket
        )
        try:
            while True:
                data, addr = await protocol.recv_queue.get()
                msg = DataMessage(
                    protocol="udp",
                    channel_id=uuid.UUID(channel_id),
                    data=data,
                    compression=0,
                    address=addr[0],
                    port=addr[1],
                )
                self.log_message(msg, "send")
                await websocket.send(pack_message(msg))
        finally:
            transport.close()

    async def _websocket_to_udp(self, udp_socket: socket.socket, queue: asyncio.Queue):
        """Handle WebSocket to UDP forwarding"""
        while True:
            msg_data = await queue.get()
            if isinstance(msg_data, DataMessage):
                binary_data = msg_data.data
                target_addr = (msg_data.target_addr, msg_data.target_port)
                await self._sendto(udp_socket, binary_data, target_addr)
                self._log.debug(
                    f"Sent UDP data to: addr={target_addr} size={len(binary_data)}."
                )

    async def _handle_remote_udp_forward(
        self, websocket: Connection, local_socket: socket.socket, channel_id: str
    ):
        """Read from remote udp socket and send to websocket, and vice versa."""

        queue = self._message_queues[channel_id]

        # Create tasks for both directions of communication
        udp_to_ws = asyncio.create_task(
            self._udp_to_websocket(websocket, local_socket, channel_id)
        )
        ws_to_udp = asyncio.create_task(self._websocket_to_udp(local_socket, queue))

        # Store tasks for potential cancellation
        self._udp_tasks[channel_id] = udp_to_ws

        tasks = [udp_to_ws, ws_to_udp]
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

    async def _tcp_to_websocket(
        self, websocket: Connection, tcp_socket: socket.socket, channel_id: str
    ):
        """Handle TCP to WebSocket forwarding"""
        loop = asyncio.get_running_loop()
        while True:
            data = await loop.sock_recv(
                tcp_socket,
                min(self._buf_size, 65535),  # Max TCP packet size
            )
            if not data:  # Connection closed
                break

            msg = DataMessage(
                protocol="tcp",
                channel_id=uuid.UUID(channel_id),
                data=data,
                compression=0,
            )
            self.log_message(msg, "send")
            await websocket.send(pack_message(msg))

    async def _websocket_to_tcp(self, tcp_socket: socket.socket, queue: asyncio.Queue):
        """Handle WebSocket to TCP forwarding"""
        loop = asyncio.get_running_loop()
        while True:
            msg_data = await queue.get()
            if isinstance(msg_data, DataMessage):
                binary_data = msg_data.data
                await loop.sock_sendall(tcp_socket, binary_data)
                self._log.debug(f"Sent TCP data to target: size={len(binary_data)}.")

    async def _handle_remote_tcp_forward(
        self, websocket: Connection, remote_socket: socket.socket, channel_id: str
    ):
        """Read from remote tcp socket and send to websocket, and vice versa."""

        queue = self._message_queues[channel_id]

        # Create tasks for both directions of communication
        tcp_to_ws = asyncio.create_task(
            self._tcp_to_websocket(websocket, remote_socket, channel_id)
        )
        ws_to_tcp = asyncio.create_task(self._websocket_to_tcp(remote_socket, queue))

        # Store tasks for potential cancellation
        self._tcp_tasks[channel_id] = tcp_to_ws

        tasks = [tcp_to_ws, ws_to_tcp]
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

    async def _handle_socks_tcp_forward(
        self, websocket: Connection, socks_socket: socket.socket, channel_id: str
    ) -> None:
        """Read from websocket and send to socks socket, and vice versa."""

        try:
            message_queue = asyncio.Queue()
            self._message_queues[channel_id] = message_queue

            socks_socket.setblocking(False)

            # Create tasks for both directions of communication
            socks_to_ws = asyncio.create_task(
                self._tcp_to_websocket(websocket, socks_socket, channel_id)
            )
            ws_to_socks = asyncio.create_task(
                self._websocket_to_tcp(socks_socket, message_queue)
            )

            # Store tasks for potential cancellation
            self._tcp_tasks[channel_id] = socks_to_ws

            tasks = [socks_to_ws, ws_to_socks]
            try:
                done, pending = await asyncio.wait(
                    [socks_to_ws, ws_to_socks], return_when=asyncio.FIRST_COMPLETED
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

        finally:
            # Send disconnect message when connection is closed
            disconnect_msg = DisconnectMessage(channel_id=uuid.UUID(channel_id))
            try:
                self.log_message(disconnect_msg, "send")
                await websocket.send(pack_message(disconnect_msg))
            except:
                pass

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
            message_queue = asyncio.Queue()
            self._message_queues[channel_id] = message_queue

            # Create tasks for monitoring TCP connection and handling UDP data
            tcp_monitor = asyncio.create_task(self._monitor_socks_tcp(socks_socket))
            socks_udp_to_ws = asyncio.create_task(
                self._socks_udp_to_websocket(websocket, udp_socket, channel_id)
            )
            ws_to_socks_udp = asyncio.create_task(
                self._websocket_to_socks_udp(udp_socket, channel_id)
            )

            # Store tasks for potential cancellation
            self._udp_tasks[channel_id] = socks_udp_to_ws

            tasks = [tcp_monitor, socks_udp_to_ws, ws_to_socks_udp]
            try:
                done, pending = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
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

        finally:
            # Send disconnect message when connection is closed
            disconnect_msg = DisconnectMessage(channel_id=uuid.UUID(channel_id))
            try:
                self.log_message(disconnect_msg, "send")
                await websocket.send(pack_message(disconnect_msg))
            except:
                pass
            if channel_id in self._udp_client_addrs:
                del self._udp_client_addrs[channel_id]

    async def _monitor_socks_tcp(self, socks_socket: socket.socket):
        """Monitor TCP connection for closure"""
        loop = asyncio.get_running_loop()
        while True:
            data = await loop.sock_recv(socks_socket, 1)
            if not data:  # Connection closed
                break

    async def _socks_udp_to_websocket(
        self, websocket: Connection, udp_socket: socket.socket, channel_id: str
    ):
        """Handle SOCKS UDP to WebSocket forwarding"""

        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            UDPProtocol, sock=udp_socket
        )
        try:
            while True:
                data, addr = await protocol.recv_queue.get()

                self._udp_client_addrs[channel_id] = addr

                # Parse SOCKS UDP header
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
                        self._log.debug(
                            "Can not parse UDP packet from associated port."
                        )
                        continue

                    msg = DataMessage(
                        protocol="udp",
                        channel_id=uuid.UUID(channel_id),
                        data=payload,
                        compression=1,
                        target_addr=target_addr,
                        target_port=target_port,
                    )
                    self.log_message(msg, "send")
                    await websocket.send(pack_message(msg))
                    self._log.debug(
                        f"Sent UDP data to WebSocket: channel={channel_id}, size={len(payload)}"
                    )
                else:
                    self._log.debug("UDP packet too small, ignoring.")
                    continue
        finally:
            transport.close()

    async def _websocket_to_socks_udp(self, udp_socket: socket.socket, channel_id: str):
        """Handle WebSocket to SOCKS UDP forwarding"""
        queue = self._message_queues[channel_id]
        while True:
            msg_data = await queue.get()
            if isinstance(msg_data, DataMessage):
                binary_data = msg_data.data
                from_addr = msg_data.address
                from_port = msg_data.port

                if from_addr is None or from_port is None:
                    self._log.warning("Missing address or port in UDP data message")
                    continue

                # Construct SOCKS UDP header
                udp_header = bytearray([0, 0, 0])  # RSV + FRAG

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

                # Send to UDP
                addr = self._udp_client_addrs.get(channel_id, None)
                if not addr:
                    if not addr:  # Skip if no client address available
                        self._log.warning(
                            f"Dropping UDP packet: no socks client address available."
                        )
                        continue
                await self._sendto(udp_socket, bytes(udp_header), addr)
                self._log.debug(
                    f"Sent UDP data to target: addr={addr} size={len(binary_data)}"
                )

    async def _sendto(self, sock: socket.socket, data, address):
        loop = asyncio.get_running_loop()

        # Use fallback for Python <= 3.10
        if hasattr(loop, "sock_sendto"):
            return await loop.sock_sendto(sock, data, address)
        else:
            return sock.sendto(data, address)

    async def _connect_via_socks5(
        self, target_addr: str, target_port: int
    ) -> socket.socket:
        """Connect to target through upstream SOCKS5 proxy.

        Args:
            target_addr: Target address to connect to
            target_port: Target port to connect to

        Returns:
            Connected socket through SOCKS5 proxy

        Raises:
            Exception: If connection fails
        """
        if not self._upstream_proxy:
            raise ValueError("No upstream proxy configured")

        loop = asyncio.get_running_loop()
        host, port = self._upstream_proxy.split(":")
        port = int(port)

        # Create socket and connect to proxy
        try:
            # Try IPv6 first
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.setblocking(False)
            await loop.sock_connect(sock, (host, port))
        except:
            # Fallback to IPv4
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            await loop.sock_connect(sock, (host, port))

        try:
            # Initial handshake
            if self._upstream_username and self._upstream_password:
                # Auth required
                await loop.sock_sendall(sock, bytes([0x05, 0x01, 0x02]))
            else:
                # No auth
                await loop.sock_sendall(sock, bytes([0x05, 0x01, 0x00]))

            # Read response
            response = await loop.sock_recv(sock, 2)
            if len(response) < 2:
                raise Exception("Handshake response too short")
            if response[0] != 0x05:
                raise Exception(f"Invalid SOCKS version: {response[0]}")

            # Handle authentication if required
            if response[1] == 0x02:
                if not self._upstream_username or not self._upstream_password:
                    raise Exception(
                        "Proxy requires authentication but no credentials provided"
                    )

                # Username/Password auth
                auth = bytearray([0x01])
                auth.append(len(self._upstream_username))
                auth.extend(self._upstream_username.encode())
                auth.append(len(self._upstream_password))
                auth.extend(self._upstream_password.encode())
                await loop.sock_sendall(sock, auth)

                # Read auth response
                auth_response = await loop.sock_recv(sock, 2)
                if len(auth_response) < 2:
                    raise Exception("Auth response too short")
                if auth_response[0] != 0x01 or auth_response[1] != 0x00:
                    raise Exception("Authentication failed")
            elif response[1] != 0x00:
                raise Exception(f"Unsupported auth method: {response[1]}")

            # Send connect request
            request = bytearray([0x05, 0x01, 0x00])

            # Add address
            try:
                # Try parsing as IPv4
                addr_bytes = socket.inet_aton(target_addr)
                request.append(0x01)  # IPv4
                request.extend(addr_bytes)
            except socket.error:
                try:
                    # Try parsing as IPv6
                    addr_bytes = socket.inet_pton(socket.AF_INET6, target_addr)
                    request.append(0x04)  # IPv6
                    request.extend(addr_bytes)
                except socket.error:
                    # Treat as domain name
                    domain_bytes = target_addr.encode()
                    request.append(0x03)  # Domain
                    request.append(len(domain_bytes))
                    request.extend(domain_bytes)

            # Add port
            request.extend(target_port.to_bytes(2, "big"))
            await loop.sock_sendall(sock, request)

            # Read response
            response = await loop.sock_recv(sock, 4)
            if len(response) < 4:
                raise Exception("Connect response too short")

            if response[0] != 0x05:
                raise Exception(f"Invalid SOCKS version: {response[0]}")
            if response[1] != 0x00:
                raise Exception(f"Connection failed: {response[1]}")

            # Skip the rest of the response (bound address and port)
            if response[3] == 0x01:  # IPv4
                await loop.sock_recv(sock, 4 + 2)
            elif response[3] == 0x03:  # Domain
                domain_len = (await loop.sock_recv(sock, 1))[0]
                await loop.sock_recv(sock, domain_len + 2)
            elif response[3] == 0x04:  # IPv6
                await loop.sock_recv(sock, 16 + 2)

            return sock

        except Exception as e:
            sock.close()
            raise Exception(f"SOCKS5 connection failed: {str(e)}")
