import pytest
from pywssocks.message import (
    AuthMessage,
    AuthResponseMessage,
    ConnectMessage,
    ConnectResponseMessage,
    DataMessage,
    DisconnectMessage,
    ConnectorMessage,
    ConnectorResponseMessage,
    LogMessage,
    PartnersMessage,
    pack_message,
    parse_message,
    BinaryType,
)
from uuid import uuid4


def test_auth_message_serialization():
    """Test AuthMessage serialization and deserialization"""
    msg = AuthMessage(token="test_token", reverse=True, instance=uuid4())
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, AuthMessage)
    assert unpacked.token == "test_token"
    assert unpacked.reverse is True
    assert unpacked.instance == msg.instance


def test_auth_message_forward():
    """Test AuthMessage for forward proxy"""
    msg = AuthMessage(token="test_token", reverse=False, instance=uuid4())
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, AuthMessage)
    assert unpacked.reverse is False


def test_auth_response_message_success():
    """Test AuthResponseMessage with success"""
    msg = AuthResponseMessage(success=True, error=None)
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, AuthResponseMessage)
    assert unpacked.success is True
    assert unpacked.error is None


def test_auth_response_message_failure():
    """Test AuthResponseMessage with failure"""
    msg = AuthResponseMessage(success=False, error="Authentication failed")
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, AuthResponseMessage)
    assert unpacked.success is False
    assert unpacked.error == "Authentication failed"


def test_connect_message_tcp():
    """Test ConnectMessage for TCP"""
    channel_id = uuid4()
    msg = ConnectMessage(
        protocol="tcp", channel_id=channel_id, address="example.com", port=443
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectMessage)
    assert unpacked.protocol == "tcp"
    assert unpacked.channel_id == channel_id
    assert unpacked.address == "example.com"
    assert unpacked.port == 443


def test_connect_message_udp():
    """Test ConnectMessage for UDP"""
    channel_id = uuid4()
    msg = ConnectMessage(protocol="udp", channel_id=channel_id, address=None, port=None)
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectMessage)
    assert unpacked.protocol == "udp"
    assert unpacked.channel_id == channel_id
    assert unpacked.address is None
    assert unpacked.port is None


def test_connect_response_message_success():
    """Test ConnectResponseMessage with success"""
    channel_id = uuid4()
    msg = ConnectResponseMessage(success=True, channel_id=channel_id, error=None)
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectResponseMessage)
    assert unpacked.success is True
    assert unpacked.channel_id == channel_id
    assert unpacked.error is None


def test_connect_response_message_failure():
    """Test ConnectResponseMessage with failure"""
    channel_id = uuid4()
    msg = ConnectResponseMessage(
        success=False, channel_id=channel_id, error="Connection refused"
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectResponseMessage)
    assert unpacked.success is False
    assert unpacked.error == "Connection refused"


def test_data_message_tcp_uncompressed():
    """Test DataMessage for TCP without compression"""
    channel_id = uuid4()
    test_data = b"Hello, World!"
    msg = DataMessage(
        protocol="tcp",
        channel_id=channel_id,
        data=test_data,
        compression=0,
        address=None,
        port=None,
        target_addr=None,
        target_port=None,
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, DataMessage)
    assert unpacked.protocol == "tcp"
    assert unpacked.channel_id == channel_id
    assert unpacked.data == test_data
    assert unpacked.compression == 0


def test_data_message_tcp_compressed():
    """Test DataMessage for TCP with compression"""
    channel_id = uuid4()
    test_data = b"Hello, World!" * 100
    msg = DataMessage(
        protocol="tcp",
        channel_id=channel_id,
        data=test_data,
        compression=1,
        address=None,
        port=None,
        target_addr=None,
        target_port=None,
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, DataMessage)
    assert unpacked.protocol == "tcp"
    assert unpacked.data == test_data
    assert unpacked.compression == 1


def test_data_message_udp():
    """Test DataMessage for UDP"""
    channel_id = uuid4()
    test_data = b"UDP packet"
    msg = DataMessage(
        protocol="udp",
        channel_id=channel_id,
        data=test_data,
        compression=0,
        address="192.168.1.1",
        port=5000,
        target_addr="10.0.0.1",
        target_port=6000,
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, DataMessage)
    assert unpacked.protocol == "udp"
    assert unpacked.data == test_data
    assert unpacked.address == "192.168.1.1"
    assert unpacked.port == 5000
    assert unpacked.target_addr == "10.0.0.1"
    assert unpacked.target_port == 6000


def test_disconnect_message():
    """Test DisconnectMessage"""
    channel_id = uuid4()
    msg = DisconnectMessage(channel_id=channel_id, error=None)
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, DisconnectMessage)
    assert unpacked.channel_id == channel_id
    assert unpacked.error is None


def test_disconnect_message_with_error():
    """Test DisconnectMessage with error"""
    channel_id = uuid4()
    msg = DisconnectMessage(channel_id=channel_id, error="Connection lost")
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, DisconnectMessage)
    assert unpacked.error == "Connection lost"


def test_connector_message_add():
    """Test ConnectorMessage for adding connector"""
    channel_id = uuid4()
    msg = ConnectorMessage(
        channel_id=channel_id, connector_token="connector_token", operation="add"
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectorMessage)
    assert unpacked.operation == "add"
    assert unpacked.connector_token == "connector_token"
    assert unpacked.channel_id == channel_id


def test_connector_message_remove():
    """Test ConnectorMessage for removing connector"""
    channel_id = uuid4()
    msg = ConnectorMessage(
        channel_id=channel_id, connector_token="connector_token", operation="remove"
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectorMessage)
    assert unpacked.operation == "remove"
    assert unpacked.channel_id == channel_id


def test_connector_response_message_success():
    """Test ConnectorResponseMessage with success"""
    channel_id = uuid4()
    msg = ConnectorResponseMessage(
        success=True,
        channel_id=channel_id,
        error=None,
        connector_token="connector_token",
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectorResponseMessage)
    assert unpacked.success is True
    assert unpacked.error is None
    assert unpacked.channel_id == channel_id


def test_connector_response_message_failure():
    """Test ConnectorResponseMessage with failure"""
    channel_id = uuid4()
    msg = ConnectorResponseMessage(
        success=False, channel_id=channel_id, error="Token already exists"
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, ConnectorResponseMessage)
    assert unpacked.success is False
    assert unpacked.error == "Token already exists"
    assert unpacked.channel_id == channel_id


def test_log_message():
    """Test LogMessage"""
    msg = LogMessage(level="INFO", msg="Test log message")
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, LogMessage)
    assert unpacked.level == "INFO"
    assert unpacked.msg == "Test log message"


def test_partners_message():
    """Test PartnersMessage"""
    msg = PartnersMessage(count=5)
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, PartnersMessage)
    assert unpacked.count == 5


def test_invalid_message_type():
    """Test parsing invalid message type"""
    invalid_data = b"\x01\xff"  # Invalid message type
    with pytest.raises(ValueError):
        parse_message(invalid_data)


def test_empty_message():
    """Test parsing empty message"""
    with pytest.raises((ValueError, IndexError)):
        parse_message(b"")


def test_corrupted_message():
    """Test parsing corrupted message"""
    corrupted_data = b"\x01\x01\xff\xff\xff"
    with pytest.raises((ValueError, Exception)):
        parse_message(corrupted_data)


def test_data_message_empty_data():
    """Test DataMessage with empty data"""
    channel_id = uuid4()
    msg = DataMessage(
        protocol="tcp",
        channel_id=channel_id,
        data=b"",
        compression=0,
        address=None,
        port=None,
        target_addr=None,
        target_port=None,
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, DataMessage)
    assert unpacked.data == b""


def test_data_message_large_data():
    """Test DataMessage with large data"""
    channel_id = uuid4()
    large_data = b"X" * 65536  # 64KB
    msg = DataMessage(
        protocol="tcp",
        channel_id=channel_id,
        data=large_data,
        compression=1,
        address=None,
        port=None,
        target_addr=None,
        target_port=None,
    )
    packed = pack_message(msg)
    unpacked = parse_message(packed)

    assert isinstance(unpacked, DataMessage)
    assert unpacked.data == large_data


def test_message_type_enum():
    """Test BinaryType enum values"""
    assert BinaryType.AUTH == 1
    assert BinaryType.AUTH_RESPONSE == 2
    assert BinaryType.CONNECT == 3
    assert BinaryType.DATA == 4
    assert BinaryType.CONNECT_RESPONSE == 5
    assert BinaryType.DISCONNECT == 6
    assert BinaryType.CONNECTOR == 7
    assert BinaryType.CONNECTOR_RESPONSE == 8
    assert BinaryType.LOG == 9
    assert BinaryType.PARTNERS == 10
