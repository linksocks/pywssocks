import os
import subprocess
import sys
import time
import pytest
import contextlib
import threading

from .utils import *

SERVER_START_MSG = "started on: ws://"
CLIENT_START_MSG = "Authentication successful"

os.environ["PYTHONUNBUFFERED"] = "1"


@contextlib.contextmanager
def forward_proxy(socks_auth=None):
    """Create forward proxy server and client processes with optional SOCKS auth"""

    try:
        ws_port = get_free_port()
        socks_port = get_free_port()
        server_process = subprocess.Popen(
            ["pywssocks", "server", "-t", "test_token", "-P", str(ws_port), "-d"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
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
            "-d",
        ]
        if socks_auth:
            client_cmd.extend(["-n", socks_auth[0], "-w", socks_auth[1]])

        client_process = subprocess.Popen(
            client_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        assert wait_for_output(server_process, SERVER_START_MSG, 10)
        assert wait_for_output(client_process, CLIENT_START_MSG, 10)

        yield server_process, client_process, ws_port, socks_port
    finally:
        server_process.terminate()
        client_process.terminate()

        server_output = server_process.stderr.read().decode()
        if hasattr(server_process, "stderr_hist"):
            server_output = "\n".join(server_process.stderr_hist) + "\n" + server_output

        client_output = client_process.stderr.read().decode()
        if hasattr(client_process, "stderr_hist"):
            client_output = "\n".join(client_process.stderr_hist) + "\n" + client_output

        print(f"Server Output:\n{server_output}", file=sys.stderr)
        print(f"Client Output:\n{client_output}", file=sys.stderr)

        server_process.wait()
        client_process.wait()


@contextlib.contextmanager
def reverse_proxy(socks_auth=None):
    """Create reverse proxy server and client processes with optional SOCKS auth"""
    try:
        ws_port = get_free_port()
        socks_port = get_free_port()
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
            "-d",
        ]
        if socks_auth:
            server_cmd.extend(["-n", socks_auth[0], "-w", socks_auth[1]])

        server_process = subprocess.Popen(
            server_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        time.sleep(1)

        client_process = subprocess.Popen(
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
            bufsize=0,
        )

        assert wait_for_output(server_process, SERVER_START_MSG, 10)
        assert wait_for_output(client_process, CLIENT_START_MSG, 10)

        yield server_process, client_process, ws_port, socks_port

    finally:
        server_process.terminate()
        client_process.terminate()

        server_output = server_process.stderr.read().decode()
        if hasattr(server_process, "stderr_hist"):
            server_output = "\n".join(server_process.stderr_hist) + "\n" + server_output

        client_output = client_process.stderr.read().decode()
        if hasattr(client_process, "stderr_hist"):
            client_output = "\n".join(client_process.stderr_hist) + "\n" + client_output

        print(f"Server Output:\n{server_output}", file=sys.stderr)
        print(f"Client Output:\n{client_output}", file=sys.stderr)

        server_process.wait()
        client_process.wait()


def wait_for_output(process, text, timeout=6):
    """Helper function to wait for specific output in process stderr"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        line = process.stderr.readline().decode()
        if not hasattr(process, "stderr_hist"):
            process.stderr_hist = [line.strip()]
        else:
            process.stderr_hist.append(line.strip())
        if text.lower() in line.lower():
            return True
        if process.poll() is not None:
            raise RuntimeError(
                f"process terminated unexpectedly while waiting for '{text}'"
            )
    return False


def test_website(website):
    assert_web_connection(website)


@pytest.mark.skipif(
    not has_ipv6_support(), reason="IPv6 is not supported on this system"
)
def test_website_ipv6(website_v6):
    assert_web_connection(website_v6)


def test_forward_cli(website):
    with forward_proxy() as (_, _, _, socks_port):
        assert_web_connection(website, socks_port)


def test_forward_reconnect(website):
    with forward_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_web_connection(website, socks_port)

        # Kill server and check for retry message
        server_process.terminate()
        server_process.wait()
        assert wait_for_output(client_process, "retrying", timeout=6)

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
        assert_web_connection(website, socks_port)


def test_reverse_cli(website):
    with reverse_proxy() as (_, _, _, socks_port):
        assert_web_connection(website, socks_port)


def test_reverse_reconnect(website):
    with reverse_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_web_connection(website, socks_port)

        # Kill client and check for closed message
        client_process.terminate()
        client_process.wait()
        assert wait_for_output(server_process, "closed", timeout=6)

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
        assert_web_connection(website, socks_port)


def test_forward_with_auth(website):
    socks_auth = ("test_user", "test_pass")
    with forward_proxy(socks_auth=socks_auth) as (_, _, _, socks_port):
        assert_web_connection(website, socks_port, socks_auth=socks_auth)


def test_reverse_with_auth(website):
    socks_auth = ("test_user", "test_pass")
    with reverse_proxy(socks_auth=socks_auth) as (_, _, _, socks_port):
        assert_web_connection(website, socks_port, socks_auth=socks_auth)


def test_reverse_load_balancing(website):
    with reverse_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_web_connection(website, socks_port)

        # Start multiple client
        client_processes = []
        for _ in range(3):
            client_processes.append(
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
            )

        try:
            for c in client_processes:
                assert wait_for_output(c, CLIENT_START_MSG, 5)

            for _ in range(len(client_processes) + 1):
                assert_web_connection(website, socks_port)

            count = 0
            for c in client_processes:
                if wait_for_output(c, "Attempting TCP connection", 1):
                    count += 1
            assert count == len(client_processes)
        finally:
            for i, c_process in enumerate(client_processes):
                c_process.terminate()

                c_output = c_process.stderr.read().decode()
                if hasattr(c_process, "stderr_hist"):
                    c_output = "\n".join(c_process.stderr_hist) + "\n" + c_output

                print(f"Client {i} Output:\n{c_output}", file=sys.stderr)

                c_process.wait()


def test_reverse_wait_reconnect(website):
    with reverse_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_web_connection(website, socks_port)

        # Kill client and check for closed message
        client_process.terminate()
        client_process.wait()
        assert wait_for_output(server_process, "closed", timeout=6)

        # Start a background thread to make proxy request
        proxy_success = threading.Event()

        def make_request():
            try:
                assert_web_connection(website, socks_port)
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
            bufsize=0,
        )

        try:
            # Wait for new client to connect successfully
            assert wait_for_output(new_client_process, CLIENT_START_MSG)

            # Wait for proxy request to complete
            assert proxy_success.wait(
                timeout=10
            ), "Request did not complete after client reconnection"
        finally:
            new_client_process.terminate()
            new_client_process.wait()
            request_thread.join(timeout=1)


@pytest.mark.skipif(
    not has_ipv6_support(), reason="IPv6 is not supported on this system"
)
def test_forward_ipv6(website_v6):
    with forward_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_web_connection(website_v6, socks_port)


@pytest.mark.skipif(
    not has_ipv6_support(), reason="IPv6 is not supported on this system"
)
def test_reverse_ipv6(website_v6):
    with reverse_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_web_connection(website_v6, socks_port)


def test_forward_udp(udp_server):
    with forward_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_udp_connection(udp_server, socks_port)


def test_reverse_udp(udp_server):
    with reverse_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_udp_connection(udp_server, socks_port)


def test_http_access():
    with forward_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        import requests

        session = requests.Session()
        session.trust_env = False
        response = session.get(
            f"http://127.0.0.1:{ws_port}",
            timeout=6,
        )
        assert "is running" in response.content.decode()
        assert response.status_code == 200
