import asyncio
import socket
from abc import ABC, abstractmethod
from supervice.logger import get_logger
from supervice.models import HealthCheckConfig, HealthCheckType

class HealthCheckResult:
    """Result of a health check execution."""
    def __init__(self, healthy: bool, message: str = ""):
        self.healthy = healthy
        self.message = message

    def __repr__(self) -> str:
        status = "healthy" if self.healthy else "unhealthy"
        return "HealthCheckResult(%s: %s)" % (status, self.message)

class HealthChecker(ABC):
    """Abstract base class for health checkers."""
    def __init__(self, config: HealthCheckConfig):
        self.config = config
        self.logger = get_logger()

    async def check(self) -> HealthCheckResult:
        """Execute the health check and return the result."""
        pass

class TCPHealthChecker(HealthChecker):
    """Health checker that verifies TCP connectivity to a port."""

    async def check(self) -> HealthCheckResult:
        if self.config.port is None:
            return HealthCheckResult(False, "No port configured for TCP health check")

        host = self.config.host
        port = self.config.port
        timeout = self.config.timeout

        try:
            # Use asyncio to create a non-blocking socket connection
            loop = asyncio.get_event_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)

            try:
                await asyncio.wait_for(loop.sock_connect(sock, (host, port)), timeout=timeout)
                sock.close()
                return HealthCheckResult(True, "TCP connection to %s:%d succeeded" % (host, port))
            except asyncio.TimeoutError:
                sock.close()
                return HealthCheckResult(
                    False, "TCP connection to %s:%d timed out after %ds" % (host, port, timeout)
                )
            except ConnectionRefusedError:
                sock.close()
                return HealthCheckResult(False, "TCP connection to %s:%d refused" % (host, port))
            except OSError as e:
                sock.close()
                return HealthCheckResult(
                    False, "TCP connection to %s:%d failed: %s" % (host, port, e)
                )
        except Exception as e:
            return HealthCheckResult(False, "TCP health check error: %s" % e)
