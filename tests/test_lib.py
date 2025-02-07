import asyncio
import contextlib
from typing import Iterable, List
import logging
import pytest

from .utils import *

test_logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def forward_server(
    token: Optional[str] = "<token>",
    ws_port: Optional[int] = None,
    idx: int = 0,
    token_kw={},
    **kw,
):
    from pywssocks import WSSocksServer

    try:
        ws_port = ws_port or get_free_port()
        assert ws_port
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            logger=logging.getLogger(f"websockets.server.{idx}"),
            **kw,
        )
        use_token = server.add_forward_token(token, **token_kw)
        task = await server.wait_ready(timeout=5)
        yield server, task, ws_port, use_token
    finally:
        try:
            task.cancel()
            await asyncio.wait_for(task, 3)
        except (UnboundLocalError, asyncio.CancelledError):
            pass


@contextlib.asynccontextmanager
async def forward_client(ws_port: int, token: str, idx: int = 0, **kw):
    from pywssocks import WSSocksClient

    try:
        socks_port = get_free_port()
        client = WSSocksClient(
            token=token,
            ws_url=f"ws://localhost:{ws_port}",
            socks_host="127.0.0.1",
            socks_port=socks_port,
            reconnect_interval=1,
            logger=logging.getLogger(f"websockets.client.{idx}"),
            **kw,
        )
        task = await client.wait_ready(timeout=5)
        yield client, task, socks_port
    finally:
        try:
            task.cancel()
            await asyncio.wait_for(task, 3)
        except (UnboundLocalError, asyncio.CancelledError):
            pass


@contextlib.asynccontextmanager
async def forward_proxy(token: Optional[str] = "<token>", server_kw={}, client_kw={}):
    async with forward_server(token=token, **server_kw) as (
        server,
        server_task,
        ws_port,
        token,
    ):
        async with forward_client(ws_port, token, **client_kw) as (
            client,
            client_task,
            socks_port,
        ):
            yield server, client, socks_port


@contextlib.asynccontextmanager
async def reverse_server(
    token: Optional[str] = "<token>",
    pool: Iterable[int] = range(1024, 10240),
    ws_port: Optional[int] = None,
    idx: int = 0,
    token_kw={},
    **kw,
):
    from pywssocks import WSSocksServer

    try:
        ws_port = ws_port or get_free_port()
        assert ws_port
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            socks_host="127.0.0.1",
            socks_port_pool=pool,
            logger=logging.getLogger(f"websockets.server.{idx}"),
            **kw,
        )
        use_token, port = server.add_reverse_token(token=token, **token_kw)
        assert port, f"can not allocate any port in {pool}"
        assert use_token
        task = await server.wait_ready(timeout=5)
        yield server, task, ws_port, use_token, port
    finally:
        try:
            task.cancel()
            await asyncio.wait_for(task, 3)
        except (UnboundLocalError, asyncio.CancelledError):
            pass


@contextlib.asynccontextmanager
async def reverse_client(ws_port: int, token: str, idx: int = 0, **kw):
    from pywssocks import WSSocksClient

    try:
        client = WSSocksClient(
            token=token,
            ws_url=f"ws://localhost:{ws_port}",
            reverse=True,
            reconnect_interval=1,
            logger=logging.getLogger(f"websockets.client.{idx}"),
            **kw,
        )
        task = await client.wait_ready(timeout=5)
        yield client, task
    finally:
        try:
            task.cancel()
            await asyncio.wait_for(task, 3)
        except (UnboundLocalError, asyncio.CancelledError):
            pass


@contextlib.asynccontextmanager
async def reverse_proxy(
    token: Optional[str] = "<token>",
    pool: Iterable[int] = range(1024, 10240),
    server_kw={},
    client_kw={},
):
    async with reverse_server(token=token, pool=pool, **server_kw) as (
        server,
        server_task,
        ws_port,
        token,
        socks_port,
    ):
        async with reverse_client(ws_port, token, **client_kw) as (client, client_task):
            yield server, client, socks_port


def test_import():
    from pywssocks import WSSocksClient, WSSocksServer, PortPool


def test_website(website):
    assert_web_connection(website)


def test_website_async_tester(website):
    async def _main():
        await async_assert_web_connection(website)

    return asyncio.run(asyncio.wait_for(_main(), 30))


@pytest.mark.skipif(
    not has_ipv6_support(), reason="IPv6 is not supported on this system"
)
def test_website_ipv6(website_v6):
    assert_web_connection(website_v6)


def test_udp_server(udp_server):
    assert_udp_connection(udp_server)


def test_udp_server_async_tester(udp_server):
    async def _main():
        await async_assert_udp_connection(udp_server)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_basic(caplog, website):
    async def _main():
        async with forward_proxy() as (server, client, socks_port):
            await async_assert_web_connection(website, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_basic(caplog, website):
    async def _main():
        async with reverse_proxy() as (server, client, socks_port):
            await async_assert_web_connection(website, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_remove_token(caplog, website):
    async def _main():
        async with forward_proxy() as (server, client, socks_port):
            # Test initial connection works
            await async_assert_web_connection(website, socks_port)

            # Remove token and verify connection fails
            server.remove_token("<token>")
            with pytest.raises(RuntimeError):
                await async_assert_web_connection(website, socks_port)

            # Add token again and verify connection
            server.add_forward_token("<token>")
            await async_assert_web_connection(website, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_remove_token(caplog, website):
    async def _main():
        async with reverse_proxy(pool=[get_free_port() for _ in range(2)]) as (
            server,
            client,
            socks_port,
        ):
            # 2 ports available, token 2 can not be allocated
            token1, port1 = server.add_reverse_token(f"<token1>")
            assert port1 is not None
            token2, port2 = server.add_reverse_token(f"<token2>")
            assert port2 is None

            # Start client 1 and test
            async with reverse_client(server._ws_port, f"<token1>", idx=1) as (
                client1,
                client1_task,
            ):
                await async_assert_web_connection(website, port1)

                # Remove client 1
                server.remove_token("<token1>")

                # Wait client 1 to exit
                await asyncio.wait_for(client1_task, 3)

                # Now token2 can be allocated
                token2, port2 = server.add_reverse_token("<token2>")
                assert port2 == port1

                with pytest.raises(RuntimeError):
                    await async_assert_web_connection(website, port1)

                # Start client 2 and test
                async with reverse_client(server._ws_port, f"<token2>", idx=2) as (
                    client2,
                    client2_task,
                ):
                    await async_assert_web_connection(website, port2)

    return asyncio.run(asyncio.wait_for(_main(), 30))


@pytest.mark.skipif(
    not has_ipv6_support(), reason="IPv6 is not supported on this system"
)
def test_forward_ipv6(caplog, website_v6):
    async def _main():
        async with forward_proxy() as (server, client, socks_port):
            await async_assert_web_connection(website_v6, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


@pytest.mark.skipif(
    not has_ipv6_support(), reason="IPv6 is not supported on this system"
)
def test_reverse_ipv6(caplog, website_v6):
    async def _main():
        async with reverse_proxy() as (server, client, socks_port):
            await async_assert_web_connection(website_v6, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_reconnect(caplog, website):
    async def _main():
        async with forward_server(idx=1) as (server, server_task, ws_port, token):
            async with forward_client(ws_port, token) as (
                client,
                client_task,
                socks_port,
            ):
                # Test initial connection
                await async_assert_web_connection(website, socks_port)

                # Stop the server
                server_task.cancel()
                try:
                    server_task.cancel()
                    await asyncio.wait_for(server_task, 3)
                except asyncio.CancelledError:
                    pass

                # Wait client detect disconnection
                await asyncio.wait_for(client.disconnected.wait(), 3)

                # Test reconnection
                async with forward_server(ws_port=ws_port, idx=2) as (_, _, _, _):
                    await asyncio.wait_for(client.connected.wait(), 6)
                    await async_assert_web_connection(website, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_reconnect(caplog, website):
    async def _main():
        async with reverse_server() as (
            server,
            server_task,
            ws_port,
            token,
            socks_port,
        ):
            async with reverse_client(ws_port, token, idx=1) as (client, client_task):
                # Test initial connection
                await async_assert_web_connection(website, socks_port)

                # Stop the client
                client_task.cancel()
                try:
                    client_task.cancel()
                    await asyncio.wait_for(client_task, 3)
                except asyncio.CancelledError:
                    pass

                # Wait for the server to detect disconnection
                for i in range(50):
                    if len(server._clients) == 0:
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise asyncio.TimeoutError()

                # Test reconnection
                async with reverse_client(ws_port, token, idx=2) as (_, _):
                    await async_assert_web_connection(website, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_auth(caplog, website):
    async def _main():
        kw = dict(
            socks_username="test_user",
            socks_password="test_pass",
        )
        async with forward_proxy(client_kw=kw) as (server, client, socks_port):
            await async_assert_web_connection(
                website, socks_port, socks_auth=("test_user", "test_pass")
            )

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_auth(caplog, website):
    async def _main():
        kw = {
            "token_kw": dict(
                username="test_user",
                password="test_pass",
            )
        }
        async with reverse_proxy(server_kw=kw) as (server, client, socks_port):
            await async_assert_web_connection(
                website, socks_port, socks_auth=("test_user", "test_pass")
            )

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_load_balancing(caplog, website):
    async def _main():
        client_count = 3
        async with reverse_server() as (
            server,
            server_task,
            ws_port,
            token,
            socks_port,
        ):
            async with contextlib.AsyncExitStack() as stack:
                # Create multiple clients
                clients = []
                for idx in range(client_count):
                    client, task = await stack.enter_async_context(
                        reverse_client(ws_port, token, idx=idx + 1)
                    )
                    clients.append((client, task))

                # Test connections are distributed
                for _ in range(client_count):
                    await async_assert_web_connection(website, socks_port)

                # Check if all clients were used
                used_clients = set()
                record: logging.LogRecord
                for record in caplog.records:
                    if "Attempting TCP connection to" in record.message:
                        if record.name.startswith("websockets.client."):
                            try:
                                client_idx = int(record.name.split(".")[-1])
                                used_clients.add(client_idx)
                            except:
                                continue

                assert (
                    len(used_clients) == client_count
                ), f"Only {len(used_clients)} of {client_count} clients were used"

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_wait_reconnect(website):
    async def _main():
        async with forward_server() as (server, server_task, ws_port, token):
            async with forward_client(ws_port, token, idx=1) as (
                client,
                client_task,
                socks_port,
            ):
                await async_assert_web_connection(website, socks_port)

                # Stop the server
                try:
                    server_task.cancel()
                    await asyncio.wait_for(server_task, 3)
                except asyncio.CancelledError:
                    pass

                # Start request
                request_task = asyncio.create_task(
                    async_assert_web_connection(website, socks_port, timeout=6)
                )
                await asyncio.sleep(0.3)
                assert not request_task.done()

                # Wait for the client to detect disconnection
                await asyncio.wait_for(client.disconnected.wait(), 3)

                # Test reconnection
                async with forward_server(ws_port=ws_port, idx=2) as (_, _, _, _):
                    await asyncio.wait_for(client.connected.wait(), 6)
                    await async_assert_web_connection(website, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_wait_reconnect(website):
    async def _main():
        async with reverse_server() as (
            server,
            server_task,
            ws_port,
            token,
            socks_port,
        ):
            async with reverse_client(ws_port, token, idx=1) as (client, client_task):
                await async_assert_web_connection(website, socks_port)

                # Stop the client
                try:
                    client_task.cancel()
                    await asyncio.wait_for(client_task, 3)
                except asyncio.CancelledError:
                    pass

                # Wait for the server to detect disconnection
                for i in range(50):
                    if len(server._clients) == 0:
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise asyncio.TimeoutError()

                # Start request
                request_task = asyncio.create_task(
                    async_assert_web_connection(website, socks_port, timeout=6)
                )
                await asyncio.sleep(0.3)
                assert not request_task.done()

                # Start another client
                async with reverse_client(ws_port, token, idx=2) as (
                    client,
                    client_task,
                ):
                    await request_task

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_udp(caplog, udp_server):
    async def _main():
        async with forward_proxy() as (server, client, socks_port):
            await async_assert_udp_connection(udp_server, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_udp(caplog, udp_server):
    async def _main():
        async with reverse_proxy() as (server, client, socks_port):
            await async_assert_udp_connection(udp_server, socks_port)

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_http_access(caplog):
    async def _main():
        import httpx

        async with forward_server() as (server, server_task, ws_port, use_token):
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://127.0.0.1:{ws_port}")
                text = response.text
                assert "is running" in text
                assert response.status_code == 200

    return asyncio.run(asyncio.wait_for(_main(), 30))
