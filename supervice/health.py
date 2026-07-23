"""Health check implementations for process monitoring."""

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

    def __init__(self, config: HealthCheckConfig, user: str | None = None):
        self.config = config
        self.user = user
        self.logger = get_logger()

    @abstractmethod
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

        loop = asyncio.get_event_loop()
        try:
            # The context manager guarantees the socket is closed on every exit
            # path — including a bare Exception or CancelledError propagating out
            # (cancellation is a BaseException and would otherwise leak the fd).
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setblocking(False)
                await asyncio.wait_for(loop.sock_connect(sock, (host, port)), timeout=timeout)
            return HealthCheckResult(True, "TCP connection to %s:%d succeeded" % (host, port))
        except asyncio.TimeoutError:
            return HealthCheckResult(
                False, "TCP connection to %s:%d timed out after %ds" % (host, port, timeout)
            )
        except ConnectionRefusedError:
            return HealthCheckResult(False, "TCP connection to %s:%d refused" % (host, port))
        except OSError as e:
            return HealthCheckResult(False, "TCP connection to %s:%d failed: %s" % (host, port, e))
        except Exception as e:
            return HealthCheckResult(False, "TCP health check error: %s" % e)


class ScriptHealthChecker(HealthChecker):
    """Health checker that runs a script and checks exit code."""

    async def check(self) -> HealthCheckResult:
        if not self.config.command:
            return HealthCheckResult(False, "No command configured for script health check")

        timeout = self.config.timeout

        try:
            # Run the check as the program's user, never as the (possibly root)
            # daemon: a check script writable by the service user must not be a
            # privilege-escalation path. If the switch is not permitted the
            # check fails visibly rather than silently running privileged.
            kwargs: dict[str, str] = {}
            if self.user:
                kwargs["user"] = self.user
            proc = await asyncio.create_subprocess_shell(
                self.config.command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,  # type: ignore[arg-type]
            )

            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

                if proc.returncode == 0:
                    return HealthCheckResult(True, "Health check script exited with code 0")
                else:
                    stderr_msg = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
                    return_code = proc.returncode if proc.returncode is not None else -1
                    return HealthCheckResult(
                        False,
                        "Health check script exited with code %d%s"
                        % (return_code, ": " + stderr_msg if stderr_msg else ""),
                    )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
                return HealthCheckResult(
                    False, "Health check script timed out after %ds" % timeout
                )
        except Exception as e:
            return HealthCheckResult(False, "Health check script error: %s" % e)


def create_health_checker(
    config: HealthCheckConfig, user: str | None = None
) -> HealthChecker | None:
    """Factory function to create the appropriate health checker."""
    if config.type == HealthCheckType.TCP:
        return TCPHealthChecker(config, user)
    elif config.type == HealthCheckType.SCRIPT:
        return ScriptHealthChecker(config, user)
    return None
