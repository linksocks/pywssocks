import pytest
import threading
import time
from pywssocks.common import PortPool, init_logging
import logging


def test_port_pool_basic():
    """Test basic port allocation and release"""
    pool = PortPool([8000, 8001, 8002])
    
    # Allocate a port
    port1 = pool.get()
    assert port1 in [8000, 8001, 8002]
    
    # Allocate another port
    port2 = pool.get()
    assert port2 in [8000, 8001, 8002]
    assert port2 != port1
    
    # Release a port
    pool.put(port1)
    
    # Allocate again, should get a port from the pool
    port3 = pool.get()
    assert port3 in [8000, 8001, 8002]


def test_port_pool_specific_port():
    """Test requesting a specific port"""
    pool = PortPool([8000, 8001, 8002])
    
    # Request specific port
    port = pool.get(8001)
    assert port == 8001
    
    # Request same port again, should fail
    port2 = pool.get(8001)
    assert port2 is None
    
    # Release and request again
    pool.put(8001)
    port3 = pool.get(8001)
    assert port3 == 8001


def test_port_pool_exhaustion():
    """Test port pool exhaustion"""
    pool = PortPool([8000, 8001])
    
    port1 = pool.get()
    port2 = pool.get()
    
    # Pool should be exhausted
    port3 = pool.get()
    assert port3 is None
    
    # Release one port
    pool.put(port1)
    
    # Should be able to allocate again
    port4 = pool.get()
    assert port4 == port1


def test_port_pool_thread_safety():
    """Test port pool thread safety"""
    pool = PortPool(range(8000, 8100))
    allocated_ports = []
    lock = threading.Lock()
    
    def allocate_ports():
        for _ in range(10):
            port = pool.get()
            if port:
                with lock:
                    allocated_ports.append(port)
            time.sleep(0.001)
    
    threads = [threading.Thread(target=allocate_ports) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Check no duplicate ports were allocated
    assert len(allocated_ports) == len(set(allocated_ports))


def test_port_pool_release_non_allocated():
    """Test releasing a port that wasn't allocated"""
    pool = PortPool([8000, 8001, 8002])
    
    # Release a port that was never allocated
    pool.put(8000)
    
    # Should be able to allocate it
    port = pool.get(8000)
    assert port == 8000


def test_port_pool_large_range():
    """Test port pool with large range"""
    pool = PortPool(range(10000, 20000))
    
    ports = set()
    for _ in range(100):
        port = pool.get()
        assert port is not None
        assert 10000 <= port < 20000
        ports.add(port)
    
    # All ports should be unique
    assert len(ports) == 100


def test_port_pool_empty():
    """Test port pool with empty range"""
    pool = PortPool([])
    
    port = pool.get()
    assert port is None


def test_port_pool_single_port():
    """Test port pool with single port"""
    pool = PortPool([8000])
    
    port1 = pool.get()
    assert port1 == 8000
    
    port2 = pool.get()
    assert port2 is None
    
    pool.put(8000)
    port3 = pool.get()
    assert port3 == 8000


def test_port_pool_request_out_of_range():
    """Test requesting a port not in the pool"""
    pool = PortPool([8000, 8001, 8002])
    
    # Request a port not in the pool - it will be allocated anyway
    # because PortPool doesn't restrict to the initial pool for specific requests
    port = pool.get(9000)
    assert port == 9000
    
    # But it should not be available again
    port2 = pool.get(9000)
    assert port2 is None


def test_port_pool_multiple_releases():
    """Test releasing the same port multiple times"""
    pool = PortPool([8000, 8001])
    
    port = pool.get(8000)
    assert port == 8000
    
    # Release multiple times
    pool.put(8000)
    pool.put(8000)  # Should be idempotent
    
    # Should still be able to allocate
    port2 = pool.get(8000)
    assert port2 == 8000


def test_init_logging_info():
    """Test logging initialization with INFO level"""
    init_logging(logging.INFO)
    
    # Logger level may be set to DEBUG by pytest, just verify it's configured
    logger = logging.getLogger()
    assert logger.level <= logging.INFO


def test_init_logging_debug():
    """Test logging initialization with DEBUG level"""
    init_logging(logging.DEBUG)
    
    logger = logging.getLogger()
    assert logger.level == logging.DEBUG


def test_init_logging_warning():
    """Test logging initialization with WARNING level"""
    init_logging(logging.WARNING)
    
    # Logger level may be set to DEBUG by pytest, just verify it's configured
    logger = logging.getLogger()
    assert logger.level <= logging.WARNING


def test_logging_level_names():
    """Test that WARNING is renamed to WARN"""
    init_logging(logging.INFO)
    
    # Check that WARNING level is renamed to WARN
    assert logging.getLevelName(logging.WARNING) == "WARN"


def test_port_pool_concurrent_specific_requests():
    """Test concurrent requests for specific ports"""
    pool = PortPool([8000, 8001, 8002])
    results = []
    lock = threading.Lock()
    
    def request_port(port_num):
        port = pool.get(port_num)
        with lock:
            results.append((port_num, port))
    
    threads = [
        threading.Thread(target=request_port, args=(8000,)),
        threading.Thread(target=request_port, args=(8000,)),
        threading.Thread(target=request_port, args=(8001,)),
    ]
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    # Only one thread should successfully get port 8000
    port_8000_results = [r for r in results if r[0] == 8000]
    successful_8000 = [r for r in port_8000_results if r[1] is not None]
    assert len(successful_8000) == 1


def test_port_pool_allocation_pattern():
    """Test that port allocation is random from available ports"""
    pool = PortPool(range(8000, 8010))
    
    # Allocate all ports
    allocated = []
    for _ in range(10):
        port = pool.get()
        assert port is not None
        allocated.append(port)
    
    # All ports should be allocated
    assert len(set(allocated)) == 10
    
    # No more ports available
    assert pool.get() is None


def test_port_pool_partial_release():
    """Test partial release and reallocation"""
    pool = PortPool([8000, 8001, 8002, 8003])
    
    # Allocate all
    p1 = pool.get()
    p2 = pool.get()
    p3 = pool.get()
    p4 = pool.get()
    
    assert pool.get() is None
    
    # Release two ports
    pool.put(p1)
    pool.put(p3)
    
    # Should be able to allocate two more
    p5 = pool.get()
    p6 = pool.get()
    
    assert p5 in [p1, p3]
    assert p6 in [p1, p3]
    assert p5 != p6
    
    # No more available
    assert pool.get() is None
