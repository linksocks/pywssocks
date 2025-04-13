import uuid
import gzip
import io
import struct
from dataclasses import dataclass
from typing import Optional
from enum import IntEnum

# Protocol version
PROTOCOL_VERSION = 0x01


# Binary message types
class BinaryType(IntEnum):
    AUTH = 0x01
    AUTH_RESPONSE = 0x02
    CONNECT = 0x03
    DATA = 0x04
    CONNECT_RESPONSE = 0x05
    DISCONNECT = 0x06
    CONNECTOR = 0x07
    CONNECTOR_RESPONSE = 0x08


# Protocol types
class BinaryProtocol(IntEnum):
    TCP = 0x01
    UDP = 0x02


# Binary connector operations
class BinaryConnectorOperation(IntEnum):
    ADD = 0x01
    REMOVE = 0x02


# Type strings
TYPE_AUTH = "auth"
TYPE_AUTH_RESPONSE = "auth_response"
TYPE_CONNECT = "connect"
TYPE_DATA = "data"
TYPE_CONNECT_RESPONSE = "connect_response"
TYPE_DISCONNECT = "disconnect"
TYPE_CONNECTOR = "connector"
TYPE_CONNECTOR_RESPONSE = "connector_response"

# Compression flags
DATA_COMPRESSION_NONE = 0x00
DATA_COMPRESSION_GZIP = 0x01


@dataclass
class BaseMessage:
    def get_type(self) -> str:
        raise NotImplementedError


@dataclass
class AuthMessage(BaseMessage):
    token: str
    reverse: bool
    instance: uuid.UUID

    def get_type(self) -> str:
        return TYPE_AUTH


@dataclass
class AuthResponseMessage(BaseMessage):
    success: bool
    error: Optional[str] = None

    def get_type(self) -> str:
        return TYPE_AUTH_RESPONSE


@dataclass
class ConnectMessage(BaseMessage):
    protocol: str
    channel_id: uuid.UUID
    address: Optional[str] = None
    port: Optional[int] = None

    def get_type(self) -> str:
        return TYPE_CONNECT


@dataclass
class ConnectResponseMessage(BaseMessage):
    success: bool
    channel_id: uuid.UUID
    error: Optional[str] = None

    def get_type(self) -> str:
        return TYPE_CONNECT_RESPONSE


@dataclass
class DataMessage(BaseMessage):
    protocol: str
    channel_id: uuid.UUID
    data: bytes
    compression: int = DATA_COMPRESSION_NONE
    address: Optional[str] = None
    port: Optional[int] = None
    target_addr: Optional[str] = None
    target_port: Optional[int] = None

    def get_type(self) -> str:
        return TYPE_DATA


@dataclass
class DisconnectMessage(BaseMessage):
    channel_id: uuid.UUID

    def get_type(self) -> str:
        return TYPE_DISCONNECT


@dataclass
class ConnectorMessage(BaseMessage):
    channel_id: uuid.UUID
    connector_token: str
    operation: str

    def get_type(self) -> str:
        return TYPE_CONNECTOR


@dataclass
class ConnectorResponseMessage(BaseMessage):
    success: bool
    channel_id: uuid.UUID
    error: Optional[str] = None
    connector_token: Optional[str] = None

    def get_type(self) -> str:
        return TYPE_CONNECTOR_RESPONSE


# Helper functions for protocol conversion
def protocol_to_bytes(protocol: str) -> int:
    if protocol == "tcp":
        return BinaryProtocol.TCP
    elif protocol == "udp":
        return BinaryProtocol.UDP
    else:
        return 0


def bytes_to_protocol(b: int) -> str:
    if b == BinaryProtocol.TCP:
        return "tcp"
    elif b == BinaryProtocol.UDP:
        return "udp"
    else:
        return ""


def operation_to_bytes(operation: str) -> int:
    if operation == "add":
        return BinaryConnectorOperation.ADD
    elif operation == "remove":
        return BinaryConnectorOperation.REMOVE
    else:
        return 0


def bytes_to_operation(b: int) -> str:
    if b == BinaryConnectorOperation.ADD:
        return "add"
    elif b == BinaryConnectorOperation.REMOVE:
        return "remove"
    else:
        return ""


def pack_message(msg: BaseMessage) -> bytes:
    # Start with version
    result = bytearray([PROTOCOL_VERSION])

    if isinstance(msg, AuthMessage):
        result.append(BinaryType.AUTH)
        result.append(len(msg.token))
        result.extend(msg.token.encode())
        result.append(1 if msg.reverse else 0)
        result.extend(msg.instance.bytes)

    elif isinstance(msg, AuthResponseMessage):
        result.append(BinaryType.AUTH_RESPONSE)
        result.append(1 if msg.success else 0)
        if not msg.success and msg.error:
            result.append(len(msg.error))
            result.extend(msg.error.encode())

    elif isinstance(msg, ConnectMessage):
        result.append(BinaryType.CONNECT)
        result.append(protocol_to_bytes(msg.protocol))
        result.extend(msg.channel_id.bytes)
        if msg.protocol == "tcp":
            if msg.address is None or msg.port is None:
                raise ValueError("TCP connect message requires address and port")
            result.append(len(msg.address))
            result.extend(msg.address.encode())
            result.extend(struct.pack(">H", msg.port))

    elif isinstance(msg, ConnectResponseMessage):
        result.append(BinaryType.CONNECT_RESPONSE)
        result.append(1 if msg.success else 0)
        result.extend(msg.channel_id.bytes)
        if not msg.success and msg.error is not None:
            result.append(len(msg.error))
            result.extend(msg.error.encode())

    elif isinstance(msg, DataMessage):
        result.append(BinaryType.DATA)
        result.append(protocol_to_bytes(msg.protocol))
        result.extend(msg.channel_id.bytes)

        # Handle compression
        compressed_data = msg.data
        compression = msg.compression
        if compression == DATA_COMPRESSION_GZIP:
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                gz.write(msg.data)
            compressed_data = buf.getvalue()

        result.append(compression)
        result.extend(struct.pack(">I", len(compressed_data)))
        result.extend(compressed_data)

        if msg.protocol == "udp":
            result.append(len(msg.address) if msg.address else 0)
            if msg.address:
                result.extend(msg.address.encode())
            result.extend(struct.pack(">H", msg.port if msg.port is not None else 0))
            result.append(len(msg.target_addr) if msg.target_addr else 0)
            if msg.target_addr:
                result.extend(msg.target_addr.encode())
            result.extend(struct.pack(">H", msg.target_port if msg.target_port is not None else 0))

    elif isinstance(msg, DisconnectMessage):
        result.append(BinaryType.DISCONNECT)
        result.extend(msg.channel_id.bytes)

    elif isinstance(msg, ConnectorMessage):
        result.append(BinaryType.CONNECTOR)
        result.extend(msg.channel_id.bytes)
        result.append(len(msg.connector_token))
        result.extend(msg.connector_token.encode())
        result.append(operation_to_bytes(msg.operation))

    elif isinstance(msg, ConnectorResponseMessage):
        result.append(BinaryType.CONNECTOR_RESPONSE)
        result.extend(msg.channel_id.bytes)
        result.append(1 if msg.success else 0)
        if not msg.success and msg.error:
            result.append(len(msg.error))
            result.extend(msg.error.encode())
        elif msg.success and msg.connector_token:
            result.append(len(msg.connector_token))
            result.extend(msg.connector_token.encode())

    else:
        raise ValueError("Unsupported message type for binary serialization")

    return bytes(result)


def parse_message(data: bytes) -> BaseMessage:
    if len(data) < 2:  # Version + Type
        raise ValueError("Message too short")

    version = data[0]
    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol version: {version}")

    msg_type = data[1]
    payload = data[2:]

    if msg_type == BinaryType.AUTH:
        if len(payload) < 1:
            raise ValueError("Invalid auth message")
        token_len = payload[0]
        if len(payload) < 1 + token_len + 1 + 16:  # +16 for Instance UUID
            raise ValueError("Invalid auth message length")
        token = payload[1 : 1 + token_len].decode()
        reverse = bool(payload[1 + token_len])
        instance = uuid.UUID(bytes=payload[1 + token_len + 1 : 1 + token_len + 1 + 16])
        return AuthMessage(token=token, reverse=reverse, instance=instance)

    elif msg_type == BinaryType.AUTH_RESPONSE:
        if len(payload) < 1:
            raise ValueError("Invalid auth response message")
        success = bool(payload[0])
        error = None
        if not success and len(payload) > 1:
            error_len = payload[1]
            if len(payload) < 2 + error_len:
                raise ValueError("Invalid auth response error length")
            error = payload[2 : 2 + error_len].decode()
        return AuthResponseMessage(success=success, error=error)

    elif msg_type == BinaryType.CONNECT:
        if len(payload) < 17:  # Protocol(1) + ChannelID(16)
            raise ValueError("Invalid connect message")
        protocol = bytes_to_protocol(payload[0])
        channel_id = uuid.UUID(bytes=payload[1:17])
        address = None
        port = None
        if protocol == "tcp":
            payload = payload[17:]
            if len(payload) < 1:
                raise ValueError("Invalid tcp connect message")
            addr_len = payload[0]
            if len(payload) < 1 + addr_len + 2:
                raise ValueError("Invalid tcp connect message length")
            address = payload[1 : 1 + addr_len].decode()
            port = struct.unpack(">H", payload[1 + addr_len : 1 + addr_len + 2])[0]
        return ConnectMessage(
            protocol=protocol, channel_id=channel_id, address=address, port=port
        )

    elif msg_type == BinaryType.CONNECT_RESPONSE:
        if len(payload) < 17:  # Success(1) + ChannelID(16)
            raise ValueError("Invalid connect response message")
        success = bool(payload[0])
        channel_id = uuid.UUID(bytes=payload[1:17])
        error = None
        if not success:
            if len(payload) < 18:
                raise ValueError("Invalid connect response error length")
            error_len = payload[17]
            if len(payload) < 18 + error_len:
                raise ValueError("Invalid connect response message length")
            error = payload[18 : 18 + error_len].decode()
        return ConnectResponseMessage(
            success=success, channel_id=channel_id, error=error
        )

    elif msg_type == BinaryType.DATA:
        if (
            len(payload) < 22
        ):  # Protocol(1) + ChannelID(16) + Compression(1) + DataLen(4)
            raise ValueError("Invalid data message")
        protocol = bytes_to_protocol(payload[0])
        channel_id = uuid.UUID(bytes=payload[1:17])
        compression = payload[17]
        data_len = struct.unpack(">I", payload[18:22])[0]
        if len(payload) < 22 + data_len:
            raise ValueError("Invalid data message length")

        # Handle decompression
        raw_data = payload[22 : 22 + data_len]
        if compression == DATA_COMPRESSION_GZIP:
            with gzip.GzipFile(fileobj=io.BytesIO(raw_data), mode="rb") as gz:
                decompressed_data = gz.read()
        else:
            decompressed_data = raw_data

        msg = DataMessage(
            protocol=protocol,
            channel_id=channel_id,
            compression=compression,
            data=decompressed_data,
        )

        if protocol == "udp":
            payload = payload[22 + data_len :]
            if len(payload) < 1:
                raise ValueError("Invalid udp data message")
            addr_len = payload[0]
            if len(payload) < 1 + addr_len + 2 + 1:
                raise ValueError("Invalid udp data message length")
            msg.address = payload[1 : 1 + addr_len].decode()
            msg.port = struct.unpack(">H", payload[1 + addr_len : 1 + addr_len + 2])[0]
            payload = payload[1 + addr_len + 2 :]
            target_addr_len = payload[0]
            if len(payload) < 1 + target_addr_len + 2:
                raise ValueError("Invalid udp data message target address")
            msg.target_addr = payload[1 : 1 + target_addr_len].decode()
            msg.target_port = struct.unpack(
                ">H", payload[1 + target_addr_len : 1 + target_addr_len + 2]
            )[0]
        return msg

    elif msg_type == BinaryType.DISCONNECT:
        if len(payload) < 16:  # ChannelID(16)
            raise ValueError("Invalid disconnect message")
        channel_id = uuid.UUID(bytes=payload[:16])
        return DisconnectMessage(channel_id=channel_id)

    elif msg_type == BinaryType.CONNECTOR:
        if len(payload) < 16:  # ChannelID(16)
            raise ValueError("Invalid connector message")
        channel_id = uuid.UUID(bytes=payload[:16])
        payload = payload[16:]
        if len(payload) < 1:
            raise ValueError("Invalid connector message length")
        token_len = payload[0]
        if len(payload) < 1 + token_len + 1:  # +1 for operation
            raise ValueError("Invalid connector message length")
        token = payload[1 : 1 + token_len].decode()
        operation = bytes_to_operation(payload[1 + token_len])
        if not operation:
            raise ValueError("Invalid operation type")
        return ConnectorMessage(
            channel_id=channel_id, connector_token=token, operation=operation
        )

    elif msg_type == BinaryType.CONNECTOR_RESPONSE:
        if len(payload) < 17:  # ChannelID(16) + Success(1)
            raise ValueError("Invalid connector response message")
        channel_id = uuid.UUID(bytes=payload[:16])
        success = bool(payload[16])
        error = None
        connector_token = None
        if not success:
            if len(payload) < 18:
                raise ValueError("Invalid connector response error length")
            error_len = payload[17]
            if len(payload) < 18 + error_len:
                raise ValueError("Invalid connector response message length")
            error = payload[18 : 18 + error_len].decode()
        elif len(payload) > 17:
            token_len = payload[17]
            if len(payload) < 18 + token_len:
                raise ValueError("Invalid connector response token length")
            connector_token = payload[18 : 18 + token_len].decode()
        return ConnectorResponseMessage(
            success=success,
            channel_id=channel_id,
            error=error,
            connector_token=connector_token,
        )

    else:
        raise ValueError(f"Unknown binary message type: {msg_type}")
