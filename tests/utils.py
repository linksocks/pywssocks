import logging
import socket
from typing import Optional, Tuple


def get_free_port(ipv6=False):
    """Get a free port for either IPv4 or IPv6"""

    addr_family = socket.AF_INET6 if ipv6 else socket.AF_INET
    with socket.socket(addr_family, socket.SOCK_STREAM) as s:
        s.bind(("" if not ipv6 else "::", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def assert_web_connection(
    website,
    socks_port: Optional[int] = None,
    socks_auth: Optional[Tuple[str, str]] = None,
):
    """Helper function to test connection to the local http server with or without proxy"""
    import requests

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
            timeout=6,
        )
    except Exception as e:
        raise RuntimeError(f"Web connection test FAILED: {e}") from None
    assert response.status_code == 204


async def async_assert_web_connection(
    website,
    socks_port: Optional[int] = None,
    socks_auth: Optional[Tuple[str, str]] = None,
):
    """Helper function to test async connection to the local http server with or without proxy"""
    import httpx

    if socks_port:
        proxy_url = f"socks5://127.0.0.1:{socks_port}"
        if socks_auth:
            proxy_url = (
                f"socks5://{socks_auth[0]}:{socks_auth[1]}@127.0.0.1:{socks_port}"
            )
    else:
        proxy_url = None

    async with httpx.AsyncClient(proxy=proxy_url, timeout=5.0) as client:
        try:
            response = await client.get(website)
            assert response.status_code == 204
        except Exception as e:
            raise RuntimeError(f"Web connection test FAILED: {e}") from None


def assert_udp_connection(udp_server, socks_port=None, socks_auth=None):
    """Helper function to connect to the local udp echo server with or without proxy"""
    host, port = udp_server.split(":")
    port = int(port)

    import socks

    sock = socks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
    if socks_port:
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

    try:
        test_data = b"Hello UDP"
        success_count = 0
        total_attempts = 10
        sock.settimeout(1)  # 设置较短的超时时间用于单次接收

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
