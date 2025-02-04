from typing import Iterable, Optional
import logging
import threading


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


class PortPool:
    def __init__(self, pool: Iterable[int]) -> None:
        self._port_pool = set(pool)
        self._used_ports: set[int] = set()
        self._used_ports_lock: threading.Lock = threading.Lock()

    def get(self, port: Optional[int] = None):
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
        with self._used_ports_lock:
            if port in self._used_ports:
                self._used_ports.remove(port)