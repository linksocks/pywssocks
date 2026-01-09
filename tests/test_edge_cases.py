import asyncio
import pytest
import logging
from .utils import *


def test_forward_invalid_token(caplog, website):
    """Test forward proxy with invalid token"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        
        # Create server with one token
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_forward_token("valid_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            # Create client with different token
            client = WSSocksClient(
                token="invalid_token",
                ws_url=f"ws://localhost:{ws_port}",
                socks_host="127.0.0.1",
                socks_port=socks_port,
                reconnect=False,
                logger=logging.getLogger("websockets.client.test")
            )
            
            # Client should fail to authenticate
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(client.wait_ready(timeout=2), timeout=3)
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_invalid_token(caplog, website):
    """Test reverse proxy with invalid token"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        
        # Create server with one token
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            socks_host="127.0.0.1",
            socks_port_pool=[socks_port],
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_reverse_token("valid_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            # Create client with different token
            client = WSSocksClient(
                token="invalid_token",
                ws_url=f"ws://localhost:{ws_port}",
                reverse=True,
                reconnect=False,
                logger=logging.getLogger("websockets.client.test")
            )
            
            # Client should fail to authenticate
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(client.wait_ready(timeout=2), timeout=3)
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_connection_refused(caplog):
    """Test forward proxy connection to refused target"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        refused_port = get_free_port()  # Port with no server
        
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_forward_token("test_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            client = WSSocksClient(
                token="test_token",
                ws_url=f"ws://localhost:{ws_port}",
                socks_host="127.0.0.1",
                socks_port=socks_port,
                logger=logging.getLogger("websockets.client.test")
            )
            client_task = await client.wait_ready(timeout=5)
            
            try:
                # Try to connect to refused port
                with pytest.raises(RuntimeError):
                    await async_assert_web_connection(
                        f"http://127.0.0.1:{refused_port}",
                        socks_port,
                        timeout=3
                    )
            finally:
                client_task.cancel()
                try:
                    await asyncio.wait_for(client_task, 2)
                except asyncio.CancelledError:
                    pass
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_connection_refused(caplog):
    """Test reverse proxy connection to refused target"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        refused_port = get_free_port()  # Port with no server
        
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            socks_host="127.0.0.1",
            socks_port_pool=[socks_port],
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_reverse_token("test_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            client = WSSocksClient(
                token="test_token",
                ws_url=f"ws://localhost:{ws_port}",
                reverse=True,
                logger=logging.getLogger("websockets.client.test")
            )
            client_task = await client.wait_ready(timeout=5)
            
            try:
                # Try to connect to refused port
                with pytest.raises(RuntimeError):
                    await async_assert_web_connection(
                        f"http://127.0.0.1:{refused_port}",
                        socks_port,
                        timeout=3
                    )
            finally:
                client_task.cancel()
                try:
                    await asyncio.wait_for(client_task, 2)
                except asyncio.CancelledError:
                    pass
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_auth_wrong_credentials(caplog, website):
    """Test forward proxy with wrong SOCKS authentication"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_forward_token("test_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            client = WSSocksClient(
                token="test_token",
                ws_url=f"ws://localhost:{ws_port}",
                socks_host="127.0.0.1",
                socks_port=socks_port,
                socks_username="user",
                socks_password="pass",
                logger=logging.getLogger("websockets.client.test")
            )
            client_task = await client.wait_ready(timeout=5)
            
            try:
                # Try with wrong credentials
                with pytest.raises(RuntimeError):
                    await async_assert_web_connection(
                        website,
                        socks_port,
                        socks_auth=("wrong_user", "wrong_pass"),
                        timeout=3
                    )
            finally:
                client_task.cancel()
                try:
                    await asyncio.wait_for(client_task, 2)
                except asyncio.CancelledError:
                    pass
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_auth_wrong_credentials(caplog, website):
    """Test reverse proxy with wrong SOCKS authentication"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            socks_host="127.0.0.1",
            socks_port_pool=[socks_port],
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_reverse_token("test_token", username="user", password="pass")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            client = WSSocksClient(
                token="test_token",
                ws_url=f"ws://localhost:{ws_port}",
                reverse=True,
                logger=logging.getLogger("websockets.client.test")
            )
            client_task = await client.wait_ready(timeout=5)
            
            try:
                # Try with wrong credentials
                with pytest.raises(RuntimeError):
                    await async_assert_web_connection(
                        website,
                        socks_port,
                        socks_auth=("wrong_user", "wrong_pass"),
                        timeout=3
                    )
            finally:
                client_task.cancel()
                try:
                    await asyncio.wait_for(client_task, 2)
                except asyncio.CancelledError:
                    pass
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_no_reconnect(caplog, website):
    """Test forward proxy with reconnect disabled"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_forward_token("test_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            client = WSSocksClient(
                token="test_token",
                ws_url=f"ws://localhost:{ws_port}",
                socks_host="127.0.0.1",
                socks_port=socks_port,
                reconnect=False,
                logger=logging.getLogger("websockets.client.test")
            )
            client_task = await client.wait_ready(timeout=5)
            
            # Test connection works
            await async_assert_web_connection(website, socks_port)
            
            # Stop server
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
            
            # Wait for client to detect disconnection
            await asyncio.wait_for(client.disconnected.wait(), 5)
            
            # Client should not reconnect
            await asyncio.sleep(2)
            assert not client.connected.is_set()
            
            client_task.cancel()
            try:
                await asyncio.wait_for(client_task, 2)
            except asyncio.CancelledError:
                pass
        finally:
            pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_no_reconnect(caplog, website):
    """Test reverse proxy with reconnect disabled"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            socks_host="127.0.0.1",
            socks_port_pool=[socks_port],
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_reverse_token("test_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            client = WSSocksClient(
                token="test_token",
                ws_url=f"ws://localhost:{ws_port}",
                reverse=True,
                reconnect=False,
                logger=logging.getLogger("websockets.client.test")
            )
            client_task = await client.wait_ready(timeout=5)
            
            # Test connection works
            await async_assert_web_connection(website, socks_port)
            
            # Stop client
            client_task.cancel()
            try:
                await asyncio.wait_for(client_task, 2)
            except asyncio.CancelledError:
                pass
            
            # Wait for server to detect disconnection
            await asyncio.sleep(2)
            
            # No client should be connected
            assert len(server._clients) == 0
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_socks_wait_server_false(caplog, website):
    """Test forward proxy with socks_wait_server=False"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        
        # Create client first with socks_wait_server=False
        client = WSSocksClient(
            token="test_token",
            ws_url=f"ws://localhost:{ws_port}",
            socks_host="127.0.0.1",
            socks_port=socks_port,
            socks_wait_server=False,
            reconnect=False,
            logger=logging.getLogger("websockets.client.test")
        )
        
        # SOCKS server should start immediately
        await asyncio.sleep(1)
        
        # Now start the WebSocket server
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_forward_token("test_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            client_task = await client.wait_ready(timeout=5)
            
            try:
                # Test connection works
                await async_assert_web_connection(website, socks_port)
            finally:
                client_task.cancel()
                try:
                    await asyncio.wait_for(client_task, 2)
                except asyncio.CancelledError:
                    pass
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_connector_remove(caplog, website):
    """Test removing connector token"""
    async def _main():
        from pywssocks import WSSocksServer, WSSocksClient
        
        ws_port = get_free_port()
        socks_port = get_free_port()
        connector_port = get_free_port()
        
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            socks_host="127.0.0.1",
            socks_port_pool=[socks_port],
            logger=logging.getLogger("websockets.server.test")
        )
        server.add_reverse_token("test_token")
        server.add_connector_token("connector_token", "test_token")
        server_task = await server.wait_ready(timeout=5)
        
        try:
            # Start reverse client
            reverse_client = WSSocksClient(
                token="test_token",
                ws_url=f"ws://localhost:{ws_port}",
                reverse=True,
                logger=logging.getLogger("websockets.client.reverse")
            )
            reverse_task = await reverse_client.wait_ready(timeout=5)
            
            try:
                # Start connector client
                connector_client = WSSocksClient(
                    token="connector_token",
                    ws_url=f"ws://localhost:{ws_port}",
                    socks_host="127.0.0.1",
                    socks_port=connector_port,
                    logger=logging.getLogger("websockets.client.connector")
                )
                connector_task = await connector_client.wait_ready(timeout=5)
                
                try:
                    # Test connection works
                    await async_assert_web_connection(website, connector_port)
                    
                    # Remove connector token
                    server.remove_connector_token("connector_token")
                    
                    # Wait a bit
                    await asyncio.sleep(1)
                    
                    # Connection should fail now
                    with pytest.raises(RuntimeError):
                        await async_assert_web_connection(website, connector_port, timeout=3)
                finally:
                    connector_task.cancel()
                    try:
                        await asyncio.wait_for(connector_task, 2)
                    except asyncio.CancelledError:
                        pass
            finally:
                reverse_task.cancel()
                try:
                    await asyncio.wait_for(reverse_task, 2)
                except asyncio.CancelledError:
                    pass
        finally:
            server_task.cancel()
            try:
                await asyncio.wait_for(server_task, 2)
            except asyncio.CancelledError:
                pass
    
    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_port_pool_class():
    """Test PortPool class directly"""
    from pywssocks import PortPool
    
    pool = PortPool([8000, 8001, 8002])
    
    # Test basic allocation
    port1 = pool.get()
    assert port1 in [8000, 8001, 8002]
    
    # Test specific port request
    port2 = pool.get(8001)
    assert port2 == 8001 or port2 is None
    
    # Test release
    pool.put(port1)
    
    # Test reallocation
    port3 = pool.get()
    assert port3 is not None
