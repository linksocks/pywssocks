import logging
import socket
from typing import Optional, Tuple
import random

logger = logging.getLogger()


def get_free_port(ipv6=False, min_port=10000, max_port=65535):
    """Get a free port for either IPv4 or IPv6 within specified range"""
    addr_family = socket.AF_INET6 if ipv6 else socket.AF_INET

    for _ in range(1000):
        port = random.randint(min_port, max_port)
        with socket.socket(addr_family, socket.SOCK_STREAM) as s:
            try:
                s.bind(("" if not ipv6 else "::", port))
                s.listen(1)
                return port
            except OSError:
                continue
    raise OSError("No free ports available in the specified range")


def assert_web_connection(
    website,
    socks_port: Optional[int] = None,
    socks_auth: Optional[Tuple[str, str]] = None,
    timeout: float = 6.0,
):
    """Helper function to test connection to the local http server with or without proxy"""
    import requests

    msg = f"Requesting web connection test for {website}"
    if socks_port:
        msg += f" using SOCKS5 at 127.0.0.1:{socks_port}"
    if socks_auth:
        msg += " with auth"
    msg += "."
    logger.info(msg)

    session = requests.Session()
    session.trust_env = False
    if socks_port:
        proxy_url = f"socks5h://127.0.0.1:{socks_port}"
        if socks_auth:
            proxy_url = (
                f"socks5h://{socks_auth[0]}:{socks_auth[1]}@127.0.0.1:{socks_port}"
            )
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
    else:
        proxies = None
    try:
        response = session.get(
            website,
            proxies=proxies,
            timeout=timeout,
        )
    except Exception as e:
        raise RuntimeError(
            f"Web connection test FAILED: {e.__class__.__name__}: {e}"
        ) from None
    assert response.status_code == 204


async def async_assert_web_connection(
    website,
    socks_port: Optional[int] = None,
    socks_auth: Optional[Tuple[str, str]] = None,
    timeout: float = 6.0,
):
    """Helper function to test async connection to the local http server with or without proxy"""
    import httpx
    import asyncio

    msg = f"Requesting web connection test for {website}"
    if socks_port:
        msg += f" using SOCKS5 at 127.0.0.1:{socks_port}"
    if socks_auth:
        msg += " with auth"
    msg += "."
    logger.info(msg)

    if socks_port:
        proxy_url = f"socks5://127.0.0.1:{socks_port}"
        if socks_auth:
            proxy_url = (
                f"socks5://{socks_auth[0]}:{socks_auth[1]}@127.0.0.1:{socks_port}"
            )
    else:
        proxy_url = None

    async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout) as client:
        try:
            response = await asyncio.wait_for(client.get(website), timeout=timeout)
            assert response.status_code == 204
            return response
        except asyncio.TimeoutError:
            raise RuntimeError(
                "Web connection test FAILED: Operation timed out"
            ) from None
        except Exception as e:
            raise RuntimeError(
                f"Web connection test FAILED: {e.__class__.__name__}: {e}"
            ) from None


def assert_udp_connection(udp_server, socks_port=None, socks_auth=None):
    """Helper function to connect to the local udp echo server with or without proxy"""

    import socks

    host, port = udp_server.split(":")
    port = int(port)

    if socks_port:
        sock = socks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
        if socks_auth:
            sock.set_proxy(
                socks.SOCKS5,
                "127.0.0.1",
                socks_port,
                username=socks_auth[0],
                password=socks_auth[1],
            )
        else:
            sock.set_proxy(socks.SOCKS5, "127.0.0.1", socks_port)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        test_data = b"Hello UDP"
        success_count = 0
        total_attempts = 10
        sock.settimeout(1)

        for i in range(total_attempts):
            try:
                sock.sendto(test_data, (host, port))
                data, _ = sock.recvfrom(1024)
                if data == test_data:
                    success_count += 1
            except socket.timeout:
                continue

        if success_count < total_attempts / 2:
            raise AssertionError(
                f"UDP connection test failed: only {success_count}/{total_attempts} "
                f"packets were successfully echoed"
            )
    finally:
        sock.close()


async def async_assert_udp_connection(udp_server, socks_port=None, socks_auth=None):
    """Helper function to async connect to the local udp echo server with or without proxy"""
    host, port = udp_server.split(":")
    port = int(port)

    import asyncio
    import struct

    loop = asyncio.get_event_loop()
    if socks_port:
        # Create TCP socket for SOCKS5 negotiation
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.setblocking(False)

        try:
            await loop.sock_connect(tcp_sock, ("127.0.0.1", socks_port))

            # SOCKS5 handshake
            if socks_auth:
                # Handshake with authentication methods
                await loop.sock_sendall(tcp_sock, b"\x05\x02\x00\x02")
            else:
                # Handshake without authentication
                await loop.sock_sendall(tcp_sock, b"\x05\x01\x00")

            auth_resp = await loop.sock_recv(tcp_sock, 2)
            if auth_resp[0] != 0x05:
                raise RuntimeError("SOCKS5 protocol error")

            if auth_resp[1] == 0x02 and socks_auth:
                # Send username/password authentication
                username, password = socks_auth
                auth_msg = struct.pack(
                    "!B%dsB%ds" % (len(username), len(password)),
                    1,
                    username.encode(),
                    len(password),
                    password.encode(),
                )
                await loop.sock_sendall(tcp_sock, auth_msg)
                auth_resp = await loop.sock_recv(tcp_sock, 2)
                if auth_resp[1] != 0:
                    raise RuntimeError("SOCKS5 authentication failed")

            # UDP associate request
            udp_req = struct.pack("!BBBBIH", 0x05, 0x03, 0x00, 0x01, 0, 0)
            await loop.sock_sendall(tcp_sock, udp_req)

            resp = await loop.sock_recv(tcp_sock, 10)
            if resp[1] != 0:
                raise RuntimeError(f"SOCKS5 UDP associate failed with code {resp[1]}")

            # Parse the UDP relay address and port from response
            udp_relay_port = struct.unpack("!H", resp[8:10])[0]

            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
        except Exception as e:
            tcp_sock.close()
            raise RuntimeError(f"Failed to setup SOCKS5 UDP associate: {e}") from None
    else:
        tcp_sock = None
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)

    try:
        test_data = b"Hello UDP"
        success_count = 0
        total_attempts = 10

        for i in range(total_attempts):
            try:
                if socks_port:
                    # Build SOCKS5 UDP request header for IPv4
                    udp_header = struct.pack(
                        "!BBBB4sH",
                        0,  # RSV
                        0,  # FRAG
                        0,  # Reserved
                        0x01,  # ATYP (IPv4)
                        socket.inet_aton(host),  # IPv4 address
                        port,  # Port
                    )

                    await asyncio.wait_for(
                        loop.sock_sendto(
                            sock, udp_header + test_data, ("127.0.0.1", udp_relay_port)
                        ),
                        timeout=0.5,
                    )
                    data, _ = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, 1024), timeout=0.5
                    )
                    # Remove SOCKS5 UDP response header
                    data = data[10:]  # Skip UDP header
                else:
                    await asyncio.wait_for(
                        loop.sock_sendto(sock, test_data, (host, port)), timeout=0.5
                    )
                    data, _ = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, 1024), timeout=0.5
                    )

                if data == test_data:
                    success_count += 1
            except (asyncio.TimeoutError, BlockingIOError):
                continue

        if success_count < total_attempts / 2:
            raise AssertionError(
                f"UDP connection test failed: only {success_count}/{total_attempts} "
                f"packets were successfully echoed"
            )
    finally:
        sock.close()
        if tcp_sock:
            tcp_sock.close()


def has_ipv6_support():
    """Check if the system supports IPv6"""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
            return True
    except (socket.error, OSError):
        return False


def show_logs_on_failure(func):
    def wrapper(*args, **kwargs):
        caplog = kwargs.get("caplog", None)
        if caplog:
            caplog.set_level(logging.DEBUG)
        try:
            return func(*args, **kwargs)
        except Exception:
            if caplog:
                print("\nTest logs:")
                print(caplog.text)
            raise

    return wrapper
