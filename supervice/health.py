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
