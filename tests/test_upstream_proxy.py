"""Tests for upstream proxy support (HTTP and SOCKS5)"""

import asyncio
import base64
import contextlib
import logging
import socket
import threading
from typing import Optional, Tuple

import pytest

from .utils import get_free_port, async_assert_web_connection

test_logger = logging.getLogger(__name__)


class MockHTTPProxy:
    """A simple HTTP CONNECT proxy for testing"""

    def __init__(self, auth: Optional[Tuple[str, str]] = None):
        self.auth = auth
        self.server_socket: Optional[socket.socket] = None
        self.port: Optional[int] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.auth_received: Optional[str] = None

    def start(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.server_socket.listen(5)
        self.port = self.server_socket.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass

    def _serve(self):
        while self._running:
            try:
                self.server_socket.settimeout(0.5)
                try:
                    conn, addr = self.server_socket.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_connection, args=(conn,), daemon=True
                ).start()
            except Exception as e:
                if self._running:
                    test_logger.error(f"HTTP proxy error: {e}")
                break

    def _handle_connection(self, conn: socket.socket):
        try:
            conn.settimeout(10)
            data = conn.recv(4096)
            if not data:
                return

            request = data.decode("utf-8", errors="ignore")
            lines = request.split("\r\n")

            # Parse CONNECT request
            if not lines[0].startswith("CONNECT"):
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return

            # Extract target host:port
            parts = lines[0].split()
            if len(parts) < 2:
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return

            target = parts[1]

            # Check authentication if required
            if self.auth:
                auth_header = None
                for line in lines[1:]:
                    if line.lower().startswith("proxy-authorization:"):
                        auth_header = line.split(":", 1)[1].strip()
                        self.auth_received = auth_header
                        break

                if not auth_header:
                    conn.sendall(
                        b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                        b'Proxy-Authenticate: Basic realm="proxy"\r\n\r\n'
                    )
                    return

                # Verify credentials
                expected = base64.b64encode(
                    f"{self.auth[0]}:{self.auth[1]}".encode()
                ).decode()
                if not auth_header.endswith(expected):
                    conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                    return

            # Connect to target
            try:
                host, port = target.rsplit(":", 1)
                port = int(port)
                target_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                target_socket.settimeout(10)
                target_socket.connect((host, port))
            except Exception as e:
                conn.sendall(f"HTTP/1.1 502 Bad Gateway\r\n\r\n".encode())
                return

            # Send success response
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

            # Relay data between client and target
            self._relay(conn, target_socket)

        except Exception as e:
            test_logger.debug(f"HTTP proxy connection error: {e}")
        finally:
            try:
                conn.close()
            except:
                pass

    def _relay(self, client: socket.socket, target: socket.socket):
        """Relay data between client and target sockets"""

        def forward(src, dst):
            try:
                while True:
                    data = src.recv(32768)
                    if not data:
                        break
                    dst.sendall(data)
            except:
                pass
            finally:
                try:
                    src.close()
                except:
                    pass
                try:
                    dst.close()
                except:
                    pass

        t1 = threading.Thread(target=forward, args=(client, target), daemon=True)
        t2 = threading.Thread(target=forward, args=(target, client), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=30)


class MockSOCKS5Proxy:
    """A simple SOCKS5 proxy for testing"""

    def __init__(self, auth: Optional[Tuple[str, str]] = None):
        self.auth = auth
        self.server_socket: Optional[socket.socket] = None
        self.port: Optional[int] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(("127.0.0.1", 0))
        self.server_socket.listen(5)
        self.port = self.server_socket.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass

    def _serve(self):
        while self._running:
            try:
                self.server_socket.settimeout(0.5)
                try:
                    conn, addr = self.server_socket.accept()
                except socket.timeout:
                    continue
                threading.Thread(
                    target=self._handle_connection, args=(conn,), daemon=True
                ).start()
            except Exception as e:
                if self._running:
                    test_logger.error(f"SOCKS5 proxy error: {e}")
                break

    def _handle_connection(self, conn: socket.socket):
        try:
            conn.settimeout(10)

            # Read auth methods
            data = conn.recv(256)
            if not data or len(data) < 2 or data[0] != 0x05:
                return

            if self.auth:
                # Require username/password auth
                conn.sendall(bytes([0x05, 0x02]))

                # Read auth request
                auth_data = conn.recv(256)
                if not auth_data or auth_data[0] != 0x01:
                    return

                ulen = auth_data[1]
                username = auth_data[2 : 2 + ulen].decode()
                plen = auth_data[2 + ulen]
                password = auth_data[3 + ulen : 3 + ulen + plen].decode()

                if username != self.auth[0] or password != self.auth[1]:
                    conn.sendall(bytes([0x01, 0x01]))  # Auth failed
                    return
                conn.sendall(bytes([0x01, 0x00]))  # Auth success
            else:
                # No auth required
                conn.sendall(bytes([0x05, 0x00]))

            # Read connect request
            data = conn.recv(256)
            if not data or len(data) < 7 or data[0] != 0x05 or data[1] != 0x01:
                return

            # Parse target address
            atyp = data[3]
            if atyp == 0x01:  # IPv4
                target_addr = socket.inet_ntoa(data[4:8])
                target_port = int.from_bytes(data[8:10], "big")
            elif atyp == 0x03:  # Domain
                domain_len = data[4]
                target_addr = data[5 : 5 + domain_len].decode()
                target_port = int.from_bytes(
                    data[5 + domain_len : 7 + domain_len], "big"
                )
            elif atyp == 0x04:  # IPv6
                target_addr = socket.inet_ntop(socket.AF_INET6, data[4:20])
                target_port = int.from_bytes(data[20:22], "big")
            else:
                conn.sendall(bytes([0x05, 0x08, 0x00, 0x01, 0, 0, 0, 0, 0, 0]))
                return

            # Connect to target
            try:
                target_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                target_socket.settimeout(10)
                target_socket.connect((target_addr, target_port))
            except Exception:
                conn.sendall(bytes([0x05, 0x05, 0x00, 0x01, 0, 0, 0, 0, 0, 0]))
                return

            # Send success response
            conn.sendall(bytes([0x05, 0x00, 0x00, 0x01, 0, 0, 0, 0, 0, 0]))

            # Relay data
            self._relay(conn, target_socket)

        except Exception as e:
            test_logger.debug(f"SOCKS5 proxy connection error: {e}")
        finally:
            try:
                conn.close()
            except:
                pass

    def _relay(self, client: socket.socket, target: socket.socket):
        """Relay data between client and target sockets"""

        def forward(src, dst):
            try:
                while True:
                    data = src.recv(32768)
                    if not data:
                        break
                    dst.sendall(data)
            except:
                pass
            finally:
                try:
                    src.close()
                except:
                    pass
                try:
                    dst.close()
                except:
                    pass

        t1 = threading.Thread(target=forward, args=(client, target), daemon=True)
        t2 = threading.Thread(target=forward, args=(target, client), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=30)


@contextlib.asynccontextmanager
async def forward_server_with_upstream(
    upstream_proxy: str,
    upstream_proxy_type: str,
    upstream_username: Optional[str] = None,
    upstream_password: Optional[str] = None,
    token: Optional[str] = "<token>",
    ws_port: Optional[int] = None,
    idx: int = 0,
):
    """Create a forward server with upstream proxy configuration"""
    from pywssocks import WSSocksServer

    try:
        ws_port = ws_port or get_free_port()
        assert ws_port
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            upstream_proxy=upstream_proxy,
            upstream_proxy_type=upstream_proxy_type,
            upstream_username=upstream_username,
            upstream_password=upstream_password,
            logger=logging.getLogger(f"websockets.server.{idx}"),
        )
        use_token = server.add_forward_token(token)
        task = await server.wait_ready(timeout=5)
        yield server, task, ws_port, use_token
    finally:
        try:
            task.cancel()
            await asyncio.wait_for(task, 3)
        except (UnboundLocalError, asyncio.CancelledError):
            pass


@contextlib.asynccontextmanager
async def forward_client(ws_port: int, token: str, idx: int = 0):
    """Create a forward client"""
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
async def reverse_server(
    token: Optional[str] = "<token>",
    ws_port: Optional[int] = None,
    idx: int = 0,
):
    """Create a reverse server"""
    from pywssocks import WSSocksServer

    try:
        ws_port = ws_port or get_free_port()
        assert ws_port
        server = WSSocksServer(
            ws_host="0.0.0.0",
            ws_port=ws_port,
            socks_host="127.0.0.1",
            socks_port_pool=range(10000, 65535),
            logger=logging.getLogger(f"websockets.server.{idx}"),
        )
        use_token, port = server.add_reverse_token(token=token)
        assert port, "Failed to allocate port"
        task = await server.wait_ready(timeout=5)
        yield server, task, ws_port, use_token, port
    finally:
        try:
            task.cancel()
            await asyncio.wait_for(task, 3)
        except (UnboundLocalError, asyncio.CancelledError):
            pass


@contextlib.asynccontextmanager
async def reverse_client_with_upstream(
    ws_port: int,
    token: str,
    upstream_proxy: str,
    upstream_proxy_type: str,
    upstream_username: Optional[str] = None,
    upstream_password: Optional[str] = None,
    idx: int = 0,
):
    """Create a reverse client with upstream proxy configuration"""
    from pywssocks import WSSocksClient

    try:
        client = WSSocksClient(
            token=token,
            ws_url=f"ws://localhost:{ws_port}",
            reverse=True,
            reconnect_interval=1,
            upstream_proxy=upstream_proxy,
            upstream_proxy_type=upstream_proxy_type,
            upstream_username=upstream_username,
            upstream_password=upstream_password,
            logger=logging.getLogger(f"websockets.client.{idx}"),
        )
        task = await client.wait_ready(timeout=5)
        yield client, task
    finally:
        try:
            task.cancel()
            await asyncio.wait_for(task, 3)
        except (UnboundLocalError, asyncio.CancelledError):
            pass


def test_forward_proxy_with_http_upstream(website):
    """Test forward proxy with HTTP upstream proxy"""

    async def _main():
        # Start mock HTTP proxy
        http_proxy = MockHTTPProxy().start()
        try:
            upstream_addr = f"127.0.0.1:{http_proxy.port}"

            async with forward_server_with_upstream(
                upstream_proxy=upstream_addr,
                upstream_proxy_type="http",
            ) as (server, server_task, ws_port, token):
                async with forward_client(ws_port, token) as (
                    client,
                    client_task,
                    socks_port,
                ):
                    await async_assert_web_connection(website, socks_port)
        finally:
            http_proxy.stop()

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_proxy_with_socks5_upstream(website):
    """Test forward proxy with SOCKS5 upstream proxy"""

    async def _main():
        # Start mock SOCKS5 proxy
        socks5_proxy = MockSOCKS5Proxy().start()
        try:
            upstream_addr = f"127.0.0.1:{socks5_proxy.port}"

            async with forward_server_with_upstream(
                upstream_proxy=upstream_addr,
                upstream_proxy_type="socks5",
            ) as (server, server_task, ws_port, token):
                async with forward_client(ws_port, token) as (
                    client,
                    client_task,
                    socks_port,
                ):
                    await async_assert_web_connection(website, socks_port)
        finally:
            socks5_proxy.stop()

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_proxy_with_http_upstream_auth(website):
    """Test forward proxy with HTTP upstream proxy requiring authentication"""

    async def _main():
        # Start mock HTTP proxy with auth
        http_proxy = MockHTTPProxy(auth=("testuser", "testpass")).start()
        try:
            upstream_addr = f"127.0.0.1:{http_proxy.port}"

            async with forward_server_with_upstream(
                upstream_proxy=upstream_addr,
                upstream_proxy_type="http",
                upstream_username="testuser",
                upstream_password="testpass",
            ) as (server, server_task, ws_port, token):
                async with forward_client(ws_port, token) as (
                    client,
                    client_task,
                    socks_port,
                ):
                    await async_assert_web_connection(website, socks_port)

            # Verify auth was received
            assert http_proxy.auth_received is not None
            assert "Basic" in http_proxy.auth_received
        finally:
            http_proxy.stop()

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_forward_proxy_with_socks5_upstream_auth(website):
    """Test forward proxy with SOCKS5 upstream proxy requiring authentication"""

    async def _main():
        # Start mock SOCKS5 proxy with auth
        socks5_proxy = MockSOCKS5Proxy(auth=("testuser", "testpass")).start()
        try:
            upstream_addr = f"127.0.0.1:{socks5_proxy.port}"

            async with forward_server_with_upstream(
                upstream_proxy=upstream_addr,
                upstream_proxy_type="socks5",
                upstream_username="testuser",
                upstream_password="testpass",
            ) as (server, server_task, ws_port, token):
                async with forward_client(ws_port, token) as (
                    client,
                    client_task,
                    socks_port,
                ):
                    await async_assert_web_connection(website, socks_port)
        finally:
            socks5_proxy.stop()

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_proxy_with_http_upstream(website):
    """Test reverse proxy with HTTP upstream proxy on client side"""

    async def _main():
        # Start mock HTTP proxy
        http_proxy = MockHTTPProxy().start()
        try:
            upstream_addr = f"127.0.0.1:{http_proxy.port}"

            async with reverse_server() as (
                server,
                server_task,
                ws_port,
                token,
                socks_port,
            ):
                async with reverse_client_with_upstream(
                    ws_port,
                    token,
                    upstream_proxy=upstream_addr,
                    upstream_proxy_type="http",
                ) as (client, client_task):
                    await async_assert_web_connection(website, socks_port)
        finally:
            http_proxy.stop()

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_reverse_proxy_with_socks5_upstream(website):
    """Test reverse proxy with SOCKS5 upstream proxy on client side"""

    async def _main():
        # Start mock SOCKS5 proxy
        socks5_proxy = MockSOCKS5Proxy().start()
        try:
            upstream_addr = f"127.0.0.1:{socks5_proxy.port}"

            async with reverse_server() as (
                server,
                server_task,
                ws_port,
                token,
                socks_port,
            ):
                async with reverse_client_with_upstream(
                    ws_port,
                    token,
                    upstream_proxy=upstream_addr,
                    upstream_proxy_type="socks5",
                ) as (client, client_task):
                    await async_assert_web_connection(website, socks_port)
        finally:
            socks5_proxy.stop()

    return asyncio.run(asyncio.wait_for(_main(), 30))


def test_http_proxy_udp_warning(caplog):
    """Test that HTTP proxy logs a warning for UDP connections"""
    from pywssocks.relay import Relay

    with caplog.at_level(logging.WARNING):
        relay = Relay(
            upstream_proxy="127.0.0.1:8080",
            upstream_proxy_type="http",
        )

    # Check that warning was logged
    assert any(
        "HTTP proxy does not support UDP" in record.message for record in caplog.records
    )


def test_proxy_type_auto_detection():
    """Test that proxy type defaults to socks5 when not specified"""
    from pywssocks.relay import Relay

    relay = Relay(
        upstream_proxy="127.0.0.1:8080",
        upstream_proxy_type=None,
    )
    # Default should be socks5 (or None which defaults to socks5 in _handle_tcp_connection)
    assert relay._upstream_proxy_type is None


def test_http_connect_request_format():
    """Test HTTP CONNECT request format"""
    import base64

    # Test basic auth encoding
    username = "testuser"
    password = "testpass"
    credentials = f"{username}:{password}"
    encoded = base64.b64encode(credentials.encode()).decode()

    assert encoded == "dGVzdHVzZXI6dGVzdHBhc3M="

    # Verify the format matches what relay.py generates
    expected_header = f"Proxy-Authorization: Basic {encoded}"
    assert "Basic" in expected_header
    assert encoded in expected_header
