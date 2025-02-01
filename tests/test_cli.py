from http.server import HTTPServer, SimpleHTTPRequestHandler
import subprocess
import sys
import time
import pytest
import socket
import contextlib
import threading

import socks
import requests

SERVER_START_MSG = "WebSocket server started"
CLIENT_START_MSG = "Authentication successful"

def get_free_port(ipv6=False):
    """Get a free port for either IPv4 or IPv6"""
    
    addr_family = socket.AF_INET6 if ipv6 else socket.AF_INET
    with socket.socket(addr_family, socket.SOCK_STREAM) as s:
        s.bind(("" if not ipv6 else "::", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

@pytest.fixture(scope="session", name='udp_server')
def local_udp_echo_server():
    """Create a local udp echo server"""
    udp_port = get_free_port()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('localhost', udp_port))
    
    def echo_server():
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                sock.sendto(data, addr)
            except:
                break
                
    server_thread = threading.Thread(target=echo_server)
    server_thread.daemon = True
    server_thread.start()
    
    yield f"127.0.0.1:{udp_port}"
    
    sock.close()

@pytest.fixture(scope="session", name='website')
def local_http_server():
    """Create a local ipv4 http server"""
    
    http_port = get_free_port()
    
    class TestHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/generate_204":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404)
        
    httpd = HTTPServer(('localhost', http_port), TestHandler)
        
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    yield f"http://127.0.0.1:{http_port}/generate_204"
        
    httpd.shutdown()
    httpd.server_close()

@pytest.fixture(scope="session", name='website_v6')
def local_http_server_v6():
    """Create a local ipv6 http server"""
    
    http_port = get_free_port(ipv6=True)
    
    class HTTPServerV6(HTTPServer):
        address_family = socket.AF_INET6
    
    class TestHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/generate_204":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404)
        
    httpd = HTTPServerV6(('::1', http_port), TestHandler)
    
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    yield f"http://[::1]:{http_port}/generate_204"
        
    httpd.shutdown()
    httpd.server_close()

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
        )

        assert wait_for_output(server_process, SERVER_START_MSG, 10)
        assert wait_for_output(client_process, CLIENT_START_MSG, 10)
        time.sleep(1)
        yield server_process, client_process, ws_port, socks_port
    finally:
        server_process.terminate()
        client_process.terminate()
        
        server_output = server_process.stderr.read().decode()
        if hasattr(server_process, 'stderr_hist'):
            server_output = '\n'.join(server_process.stderr_hist) +'\n' + server_output
            
        client_output = client_process.stderr.read().decode()
        if hasattr(client_process, 'stderr_hist'):
            client_output = '\n'.join(client_process.stderr_hist) + '\n' + client_output
        
        print(f'Server Output:\n{server_output}', file=sys.stderr)
        print(f'Client Output:\n{client_output}', file=sys.stderr)
        
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
                "-d",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        assert wait_for_output(server_process, SERVER_START_MSG, 10)
        assert wait_for_output(client_process, CLIENT_START_MSG, 10)
        time.sleep(1)
        yield server_process, client_process, ws_port, socks_port
    finally:
        server_process.terminate()
        client_process.terminate()
        
        server_output = server_process.stderr.read().decode()
        if hasattr(server_process, 'stderr_hist'):
            server_output = '\n'.join(server_process.stderr_hist) +'\n' + server_output
            
        client_output = client_process.stderr.read().decode()
        if hasattr(client_process, 'stderr_hist'):
            client_output = '\n'.join(client_process.stderr_hist) + '\n' + client_output
        
        print(f'Server Output:\n{server_output}', file=sys.stderr)
        print(f'Client Output:\n{client_output}', file=sys.stderr)
        
        server_process.wait()
        client_process.wait()


def wait_for_output(process, text, timeout=5):
    """Helper function to wait for specific output in process stderr"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        line = process.stderr.readline().decode()
        if not hasattr(process, 'stderr_hist'):
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


def assert_web_connection(website, socks_port=None, socks_auth=None):
    """Helper function to test connection to the local http server with or without proxy"""
    session = requests.Session()
    session.trust_env = False
    if socks_port:
        proxy_url = f"socks5h://127.0.0.1:{socks_port}"
        if socks_auth:
            proxy_url = f"socks5h://{socks_auth[0]}:{socks_auth[1]}@127.0.0.1:{socks_port}"
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
    else:
        proxies = None
    response = session.get(
        website,
        proxies=proxies,
        timeout=5,
    )
    assert response.status_code == 204
    
def assert_udp_connection(udp_server, socks_port=None, socks_auth=None):
    """Helper function to connect to the local udp echo server with or without proxy"""
    host, port = udp_server.split(':')
    port = int(port)
    
    sock = socks.socksocket(socket.AF_INET, socket.SOCK_DGRAM)
    if socks_port:
        if socks_auth:
            sock.set_proxy(socks.SOCKS5, "127.0.0.1", socks_port, 
                          username=socks_auth[0], password=socks_auth[1])
        else:
            sock.set_proxy(socks.SOCKS5, "127.0.0.1", socks_port)
        
    try:
        test_data = b"Hello UDP"
        sock.sendto(test_data, (host, port))
        
        sock.settimeout(5)
        data, _ = sock.recvfrom(1024)
        
        assert data == test_data, f"Echo data mismatch: {data} != {test_data}"
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

def test_website(website):
    assert_web_connection(website)

@pytest.mark.skipif(not has_ipv6_support(), reason="IPv6 is not supported on this system")
def test_website_ipv6(website_v6):
    assert_web_connection(website_v6)

def test_forward(website):
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
        assert wait_for_output(client_process, "retrying", timeout=5)

        # Restart server
        server_process = subprocess.Popen(
            ["pywssocks", "server", "-t", "test_token", "-P", str(ws_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert wait_for_output(server_process, SERVER_START_MSG)
        time.sleep(1)

        # Wait for reconnection
        assert wait_for_output(client_process, CLIENT_START_MSG, timeout=10)
        time.sleep(1)

        # Test connection
        assert_web_connection(website, socks_port)


def test_reverse(website):
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
        time.sleep(1)

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
            
        time.sleep(1)

        for _ in range(len(client_processes) + 1):
            assert_web_connection(website, socks_port)

        count = 0
        for c in client_processes:
            if wait_for_output(c, "Attempting TCP connection", 1):
                count += 1
        assert count == len(client_processes)


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
        assert wait_for_output(server_process, "closed", timeout=5)

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
        )

        try:
            # Wait for new client to connect successfully
            assert wait_for_output(new_client_process, CLIENT_START_MSG)
            time.sleep(1)

            # Wait for proxy request to complete
            assert proxy_success.wait(
                timeout=10
            ), "Request did not complete after client reconnection"
        finally:
            new_client_process.terminate()
            new_client_process.wait()
            request_thread.join(timeout=1)

@pytest.mark.skipif(not has_ipv6_support(), reason="IPv6 is not supported on this system")
def test_forward_ipv6(website_v6):
    with forward_proxy() as (
        server_process,
        client_process,
        ws_port,
        socks_port,
    ):
        assert_web_connection(website_v6, socks_port)

@pytest.mark.skipif(not has_ipv6_support(), reason="IPv6 is not supported on this system")
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