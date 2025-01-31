import socket
import pytest

def get_free_port(ipv6=False):
    """Get a free port for either IPv4 or IPv6"""
    
    addr_family = socket.AF_INET6 if ipv6 else socket.AF_INET
    with socket.socket(addr_family, socket.SOCK_STREAM) as s:
        s.bind(("" if not ipv6 else "::", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def test_import():
    from pywssocks import WSSocksClient, WSSocksServer, PortPool


def test_forward_server():
    import asyncio
    from pywssocks import WSSocksServer

    ws_port = get_free_port()

    server = WSSocksServer(
        ws_host="0.0.0.0",
        ws_port=ws_port,
    )
    token = server.add_forward_token()
    print(f"Token: {token}")
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(server.start(), 5))


def test_forward_client():
    import asyncio
    from pywssocks import WSSocksClient

    socks_port = get_free_port()

    client = WSSocksClient(
        token="<token>",
        ws_url="ws://localhost:8765",
        socks_host="127.0.0.1",
        socks_port=socks_port,
    )
    try:
        asyncio.run(asyncio.wait_for(client.start(), 5))
    except asyncio.TimeoutError:
        pass


def test_reverse_server():
    import asyncio
    from pywssocks import WSSocksServer

    ws_port = get_free_port()

    server = WSSocksServer(
        ws_host="0.0.0.0",
        ws_port=ws_port,
        socks_host="127.0.0.1",
        socks_port_pool=range(1024, 10240),
    )
    token, port = server.add_reverse_token()
    print(f"Token: {token}\nPort: {port}")
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(asyncio.wait_for(server.start(), 5))


def test_reverse_client():
    import asyncio
    from pywssocks import WSSocksClient

    client = WSSocksClient(
        token="<token>",
        ws_url="ws://localhost:8765",
        reverse=True,
    )
    try:
        asyncio.run(asyncio.wait_for(client.start(), 5))
    except asyncio.TimeoutError:
        pass
