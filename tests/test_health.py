import asyncio
import socket
import unittest
from supervice.health import (
    HealthCheckResult,
    ScriptHealthChecker,
    TCPHealthChecker,
    create_health_checker,
)
from supervice.models import HealthCheckConfig, HealthCheckType

class TestHealthCheckResult(unittest.TestCase):
    """Tests for HealthCheckResult class."""

    def test_healthy_result(self) -> None:
        result = HealthCheckResult(True, "All good")
        self.assertTrue(result.healthy)
        self.assertEqual(result.message, "All good")
