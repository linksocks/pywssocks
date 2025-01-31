import subprocess
import time
import pytest
import socket
import contextlib
import threading

import requests

SERVER_START_MSG = "WebSocket server started"
CLIENT_START_MSG = "Authentication successful"


@pytest.fixture
def unused_ports():
    """Returns a tuple of two unused ports for testing"""

    def get_free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    port1 = get_free_port()
    port2 = get_free_port()
    return port1, port2


@contextlib.contextmanager
def forward_proxy(unused_ports, socks_auth=None):
    """Create forward proxy server and client processes with optional SOCKS auth"""
    try:
        ws_port, socks_port = unused_ports
        server_process = subprocess.Popen(
            ["pywssocks", "server", "-t", "test_token", "-P", str(ws_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)

        client_cmd = [
            "pywssocks",
            "client",
            "-t",
            "test_token",
            "-u",
            f"ws://localhost:{ws_port}",
            "-p",
            str(socks_port),
        ]
        if socks_auth:
            client_cmd.extend(["-n", socks_auth[0], "-w", socks_auth[1]])

        client_process = subprocess.Popen(
            client_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        assert wait_for_output(server_process, SERVER_START_MSG, 10)
        assert wait_for_output(client_process, CLIENT_START_MSG, 10)
        yield server_process, client_process, ws_port, socks_port
    finally:
        server_process.terminate()
        client_process.terminate()
        server_process.wait()
        client_process.wait()


@contextlib.contextmanager
def reverse_proxy(unused_ports, socks_auth=None):
    """Create reverse proxy server and client processes with optional SOCKS auth"""
    try:
        ws_port, socks_port = unused_ports
        server_cmd = [
            "pywssocks",
            "server",
            "-t",
            "test_token",
            "-P",
            str(ws_port),
            "-p",
            str(socks_port),
            "-r",
        ]
        if socks_auth:
            server_cmd.extend(["-n", socks_auth[0], "-w", socks_auth[1]])

        server_process = subprocess.Popen(
            server_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        client_process = subprocess.Popen(
            [
                "pywssocks",
                "client",
                "-t",
                "test_token",
                "-u",
                f"ws://localhost:{ws_port}",
                "-r",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        assert wait_for_output(server_process, SERVER_START_MSG, 10)
        assert wait_for_output(client_process, CLIENT_START_MSG, 10)
        yield server_process, client_process, ws_port, socks_port
    finally:
        server_process.terminate()
        client_process.terminate()
        server_process.wait()
        client_process.wait()


def wait_for_output(process, text, timeout=5):
    """Helper function to wait for specific output in process stderr"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        line = process.stderr.readline().decode()
        print(line)
        if text.lower() in line.lower():
            return True
        if process.poll() is not None:
            raise RuntimeError(
                f"process terminated unexpectedly while waiting for '{text}'"
            )
    return False


def assert_proxy_connection(socks_port, socks_auth=None):
    proxy_url = f"socks5h://127.0.0.1:{socks_port}"
    if socks_auth:
        proxy_url = f"socks5h://{socks_auth[0]}:{socks_auth[1]}@127.0.0.1:{socks_port}"
    proxies = {
        "http": proxy_url,
        "https": proxy_url,
    }
    try:
        response = requests.get(
            "http://www.gstatic.com/generate_204", proxies=proxies, timeout=5
        )
        assert response.status_code == 204
    except requests.ConnectionError:
        raise RuntimeError(f"fail to connect to proxy") from None


def test_forward(unused_ports):
    with forward_proxy(unused_ports) as (_, _, _, socks_port):
        assert_proxy_connection(socks_port)


def test_forward_reconnect(unused_ports):
    with forward_proxy(unused_ports) as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_proxy_connection(socks_port)

        # Kill server and check for retry message
        server_process.terminate()
        server_process.wait()
        assert wait_for_output(client_process, "retrying", timeout=5)

        # Restart server
        server_process = subprocess.Popen(
            ["pywssocks", "server", "-t", "test_token", "-P", str(ws_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert wait_for_output(server_process, SERVER_START_MSG)

        # Wait for reconnection
        assert wait_for_output(client_process, CLIENT_START_MSG, timeout=10)

        # Test connection
        assert_proxy_connection(socks_port)


def test_reverse(unused_ports):
    with reverse_proxy(unused_ports) as (_, _, _, socks_port):
        assert_proxy_connection(socks_port)


def test_reverse_reconnect(unused_ports):
    with reverse_proxy(unused_ports) as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_proxy_connection(socks_port)

        # Kill client and check for closed message
        client_process.terminate()
        client_process.wait()
        assert wait_for_output(server_process, "closed", timeout=5)

        # Restart client
        client_process = subprocess.Popen(
            [
                "pywssocks",
                "client",
                "-t",
                "test_token",
                "-u",
                f"ws://localhost:{ws_port}",
                "-r",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert wait_for_output(client_process, CLIENT_START_MSG)

        # Test connection
        assert_proxy_connection(socks_port)


def test_forward_with_auth(unused_ports):
    socks_auth = ("test_user", "test_pass")
    with forward_proxy(unused_ports, socks_auth=socks_auth) as (_, _, _, socks_port):
        assert_proxy_connection(socks_port, socks_auth=socks_auth)


def test_reverse_with_auth(unused_ports):
    socks_auth = ("test_user", "test_pass")
    with reverse_proxy(unused_ports, socks_auth=socks_auth) as (_, _, _, socks_port):
        assert_proxy_connection(socks_port, socks_auth=socks_auth)


def test_reverse_load_balancing(unused_ports):
    with reverse_proxy(unused_ports) as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_proxy_connection(socks_port)

        # Start multiple client
        client_processes = [
            subprocess.Popen(
                [
                    "pywssocks",
                    "client",
                    "-t",
                    "test_token",
                    "-u",
                    f"ws://localhost:{ws_port}",
                    "-r",
                    "-d",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(3)
        ]

        for c in client_processes:
            assert wait_for_output(c, CLIENT_START_MSG, 5)

        for _ in range(len(client_processes) + 1):
            assert_proxy_connection(socks_port)

        count = 0
        for c in client_processes:
            if wait_for_output(c, "Attempting TCP connection", 1):
                count += 1
        assert count == len(client_processes)


def test_reverse_wait_reconnect(unused_ports):
    with reverse_proxy(unused_ports) as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_proxy_connection(socks_port)

        # Kill client and check for closed message
        client_process.terminate()
        client_process.wait()
        assert wait_for_output(server_process, "closed", timeout=5)

        # Start a background thread to make proxy request
        proxy_success = threading.Event()

        def make_request():
            try:
                assert_proxy_connection(socks_port)
                proxy_success.set()
            except:
                pass

        request_thread = threading.Thread(target=make_request)
        request_thread.start()

        # Wait a moment to ensure request has started but is waiting
        time.sleep(1)
        assert not proxy_success.is_set(), "Request should be waiting"

        # Start new client
        new_client_process = subprocess.Popen(
            [
                "pywssocks",
                "client",
                "-t",
                "test_token",
                "-u",
                f"ws://localhost:{ws_port}",
                "-r",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            # Wait for new client to connect successfully
            assert wait_for_output(new_client_process, CLIENT_START_MSG)

            # Wait for proxy request to complete
            assert proxy_success.wait(
                timeout=5
            ), "Request did not complete after client reconnection"
        finally:
            new_client_process.terminate()
            new_client_process.wait()
            request_thread.join(timeout=1)
