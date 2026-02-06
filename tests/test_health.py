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

    def test_unhealthy_result(self) -> None:
        result = HealthCheckResult(False, "Connection refused")
        self.assertFalse(result.healthy)
        self.assertEqual(result.message, "Connection refused")

class TestTCPHealthChecker(unittest.TestCase):
    """Tests for TCP health checks."""

    def test_tcp_check_connection_refused(self) -> None:
        """Test TCP check fails when connection is refused."""

        async def run() -> None:
            config = HealthCheckConfig(
                type=HealthCheckType.TCP,
                port=59999,  # Unlikely to be in use
                host="127.0.0.1",
                timeout=1,
            )
            checker = TCPHealthChecker(config)
            result = await checker.check()

            self.assertFalse(result.healthy)
            self.assertIn("refused", result.message.lower())

        asyncio.run(run())

    def test_tcp_check_success(self) -> None:
        """Test TCP check succeeds when port is listening."""

        async def run() -> None:
            # Start a simple TCP server
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(("127.0.0.1", 0))
            server_sock.listen(1)
            port = server_sock.getsockname()[1]

            try:
                config = HealthCheckConfig(
                    type=HealthCheckType.TCP, port=port, host="127.0.0.1", timeout=5
                )
                checker = TCPHealthChecker(config)
                result = await checker.check()

                self.assertTrue(result.healthy)
                self.assertIn("succeeded", result.message.lower())
            finally:
                server_sock.close()

        asyncio.run(run())

    def test_tcp_check_timeout(self) -> None:
        """Test TCP check times out correctly."""

        async def run() -> None:
            # Use a non-routable IP that will cause timeout
            config = HealthCheckConfig(
                type=HealthCheckType.TCP,
                port=80,
                host="10.255.255.1",  # Non-routable IP
                timeout=1,
            )
            checker = TCPHealthChecker(config)
            result = await checker.check()

            self.assertFalse(result.healthy)
            # Message could be timeout or connection error depending on platform

        asyncio.run(run())

    def test_tcp_check_no_port_configured(self) -> None:
        """Test TCP check fails gracefully when no port is configured."""

        async def run() -> None:
            config = HealthCheckConfig(type=HealthCheckType.TCP, port=None, host="127.0.0.1")
            checker = TCPHealthChecker(config)
            result = await checker.check()

            self.assertFalse(result.healthy)
            self.assertIn("No port configured", result.message)

        asyncio.run(run())
