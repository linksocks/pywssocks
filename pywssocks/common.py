from typing import Iterable, Optional
import logging
import threading


class PortPool:
    """A thread-safe port pool for managing available network ports.

    This class provides functionality to allocate and release ports from a predefined pool,
    ensuring thread-safe operations for concurrent access.
    """

    def __init__(self, pool: Iterable[int]) -> None:
        """
        Args:
            pool: An iterable of integer port numbers that can be allocated.
        """
        self._port_pool = set(pool)
        self._used_ports: set[int] = set()
        self._used_ports_lock: threading.Lock = threading.Lock()

    def get(self, port: Optional[int] = None) -> Optional[int]:
        """Get an available port from the pool.

        Args:
            port: Optional specific port number to request. If None, any available port in the pool will be returned.

        Returns:
            port: The allocated port number, or None if no port is available.
        """
        with self._used_ports_lock:
            if port is not None:
                if port not in self._used_ports:
                    self._used_ports.add(port)
                    return port
                return None

            # If no specific port requested, allocate from range
            available_ports = self._port_pool - self._used_ports
            if available_ports:
                port = next(iter(available_ports))
                self._used_ports.add(port)
                return port

            return None

    def put(self, port: int) -> None:
        """Return a port back to the pool.

        Args:
            port: The port number to release back to the pool.
        """
        with self._used_ports_lock:
            if port in self._used_ports:
                self._used_ports.remove(port)


def init_logging(level: int = logging.INFO):
    """Initialize logging with custom format: [mm-dd hh:mm] INFO : msg"""

    logging._levelToName[logging.WARNING] = "WARN"

    logging.basicConfig(
        format="[%(asctime)s] %(levelname)-5s: %(message)s",
        datefmt="%m-%d %H:%M",
        level=level,
    )

    if level >= logging.INFO:
        logging.getLogger("websockets.server").setLevel(logging.WARNING)
