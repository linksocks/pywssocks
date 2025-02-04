from typing import List
import logging
import pytest

from .utils import *

test_logger = logging.getLogger(__name__)

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
        asyncio.run(asyncio.wait_for(server.serve(), 5))


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
        asyncio.run(asyncio.wait_for(client.connect(), 5))
    except asyncio.TimeoutError:
        pass
    
@pytest.mark.asyncio
async def test_forward_lib(caplog, website):
    import asyncio
    from pywssocks import WSSocksServer, WSSocksClient

    caplog.set_level(logging.DEBUG)

    ws_port = get_free_port()
    socks_port = get_free_port()
    server = WSSocksServer(ws_host="0.0.0.0", ws_port=ws_port)
    server_task = await server.wait_ready(timeout=6)
    server.add_forward_token("<token>")
    client = WSSocksClient(
        token="<token>",
        ws_url=f"ws://localhost:{ws_port}",
        socks_host="127.0.0.1",
        socks_port=socks_port,
    )
    client_task = await client.wait_ready(timeout=6)
    await async_assert_web_connection(website, socks_port)


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
        asyncio.run(asyncio.wait_for(server.serve(), 5))


def test_reverse_client():
    import asyncio
    from pywssocks import WSSocksClient

    client = WSSocksClient(
        token="<token>",
        ws_url="ws://localhost:8765",
        reverse=True,
    )
    try:
        asyncio.run(asyncio.wait_for(client.connect(), 5))
    except asyncio.TimeoutError:
        pass

@pytest.mark.asyncio
async def test_reverse_lib(caplog, website):
    import asyncio
    from pywssocks import WSSocksServer, WSSocksClient
    
    caplog.set_level(logging.DEBUG)

    ws_port = get_free_port()
    socks_port = get_free_port()
    server = WSSocksServer(ws_host="0.0.0.0", ws_port=ws_port)
    server_task = await server.wait_ready(timeout=6)
    server.add_reverse_token("<token>", socks_port)
    client = WSSocksClient(
        token="<token>",
        ws_url=f"ws://localhost:{ws_port}",
        reverse=True,
    )
    client_task = await client.wait_ready(timeout=6)
    await async_assert_web_connection(website, socks_port)

@pytest.mark.asyncio
async def test_forward_remove_token(caplog, website):
    import asyncio
    from pywssocks import WSSocksServer, WSSocksClient

    # Output log when fail
    caplog.set_level(logging.DEBUG)

    # Define server and client
    ws_port = get_free_port()
    socks_port = get_free_port()
    server = WSSocksServer(
        ws_host="0.0.0.0", ws_port=ws_port
    )
    server_task = await server.wait_ready(timeout=6)
    
    client = WSSocksClient(
        token=f"<token>",
        ws_url=f"ws://localhost:{ws_port}",
        socks_port=socks_port,
    )
    
    # Add token
    token = server.add_forward_token(f"<token>")
    
    # Start client
    client_task = await client.wait_ready(timeout=6)
    
    # Test connection
    await async_assert_web_connection(website, socks_port)

    # Remove token
    server.remove_token("<token>")
    
    # Test connection
    with pytest.raises(RuntimeError):
        await async_assert_web_connection(website, socks_port)

@pytest.mark.asyncio
async def test_reverse_remove_token(caplog, website):
    import asyncio
    from pywssocks import WSSocksServer, WSSocksClient

    # Output log when fail
    caplog.set_level(logging.DEBUG)

    # Define server and client 1-3
    ws_port = get_free_port()
    server = WSSocksServer(
        ws_host="0.0.0.0", ws_port=ws_port, socks_port_pool=[get_free_port() for _ in range(2)]
    )
    clients: List[WSSocksClient] = []
    for i in range(3):
        logger = logging.getLogger(f"websockets.client.{i}")
        client = WSSocksClient(
            token=f"<token{i}>",
            ws_url=f"ws://localhost:{ws_port}",
            reverse=True,
            logger=logger,
        )
        clients.append(client)

    # 2 ports available, token2 socks can not be allocated
    ports = {}
    for i in range(3):
        token, port = server.add_reverse_token(f"<token{i}>")
        ports[i] = port
        if not port:
            assert i == 2

    # Remove token0
    server.remove_token("<token0>")

    # Now token2 can be allocated
    token, port = server.add_reverse_token("<token2>")
    assert port is not None

    # Start server and client 1 & 2
    server_task = await server.wait_ready(timeout=6)
    client_tasks = {}
    client_tasks[1] = await clients[1].wait_ready(timeout=6)
    client_tasks[2] = await clients[2].wait_ready(timeout=6)

    # Test token 1 & 2
    await async_assert_web_connection(website, ports[1])
    await async_assert_web_connection(website, ports[2])

    # Remove token2 when the server is running
    server.remove_token("<token2>")
    
    # Test token 2
    with pytest.raises(RuntimeError):
        await async_assert_web_connection(website, ports[2])

    # Add token0 when the server is running
    token, port = server.add_reverse_token("<token0>")
    assert port is not None

    # Start client 0
    client_tasks[0] = await clients[0].wait_ready(timeout=6)

    # Test token 0
    await async_assert_web_connection(website, ports[0])
    
    # Wait last client 2 to exit
    last_task: asyncio.Task = client_tasks[2]
    await asyncio.wait_for(last_task, 5)
    
    test_logger.info("Client 2 exited")
    
    # Remove token0 when the server is running
    server.remove_token("<token0>")
    
    test_logger.info("<token0> Removed")
    
    # Add token2 again (will reuse the port)
    token, port = server.add_reverse_token("<token2>")
    assert port is not None
    
    test_logger.info("<token2> Added")
    
    # Start client 2 again
    client_tasks[2] = await clients[2].wait_ready(timeout=6)
    
    test_logger.info("Client 2 Added")
    
    # Test token 2
    await async_assert_web_connection(website, ports[2])
