from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
import pytest

from .utils import *

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