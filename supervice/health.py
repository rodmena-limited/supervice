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
