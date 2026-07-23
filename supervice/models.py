import os
from dataclasses import dataclass, field
from enum import Enum

# Defaults for child (per-program) log rotation, mirroring supervisord's names.
DEFAULT_CHILD_LOG_MAXBYTES = 50 * 1024 * 1024  # 50MB
DEFAULT_CHILD_LOG_BACKUPS = 10


def default_socket_path() -> str:
    """Return a per-user runtime path for the RPC socket.

    /tmp is deliberately avoided: it is world-writable, so any local user could
    pre-create the path to block startup or impersonate the daemon. The daemon
    and supervicectl both call this, so they agree without configuration.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and os.path.isdir(xdg):
        return os.path.join(xdg, "supervice.sock")
    if os.geteuid() == 0 and os.path.isdir("/run"):
        return "/run/supervice.sock"
    return os.path.join(os.path.expanduser("~"), ".supervice.sock")


class HealthCheckType(Enum):
    NONE = "none"
    TCP = "tcp"
    SCRIPT = "script"


@dataclass
class HealthCheckConfig:
    """Configuration for process health checks."""

    type: HealthCheckType = HealthCheckType.NONE
    interval: int = 30  # seconds between health checks
    timeout: int = 10  # seconds to wait for health check response
    retries: int = 3  # consecutive failures before marking unhealthy
    start_period: int = 10  # seconds to wait before starting health checks
    # TCP health check options
    port: int | None = None
    host: str = "127.0.0.1"
    # Script health check options
    command: str | None = None


@dataclass
class ProgramConfig:
    name: str
    command: str
    numprocs: int = 1
    autostart: bool = True
    autorestart: bool = True
    startsecs: int = 1
    startretries: int = 3
    stopsignal: str = "TERM"
    stopwaitsecs: int = 10
    stdout_logfile: str | None = None
    stderr_logfile: str | None = None
    stdout_logfile_maxbytes: int = DEFAULT_CHILD_LOG_MAXBYTES
    stdout_logfile_backups: int = DEFAULT_CHILD_LOG_BACKUPS
    stderr_logfile_maxbytes: int = DEFAULT_CHILD_LOG_MAXBYTES
    stderr_logfile_backups: int = DEFAULT_CHILD_LOG_BACKUPS
    environment: dict[str, str] = field(default_factory=dict)
    directory: str | None = None
    user: str | None = None
    group: str | None = None
    # Linux: SIGKILL the child if the supervisor dies (no orphans, at the cost
    # of coupling child lifetime to the supervisor's). Set false to let
    # children survive a supervisor crash.
    pdeathsig: bool = True
    healthcheck: HealthCheckConfig = field(default_factory=HealthCheckConfig)


@dataclass
class SupervisorConfig:
    # Empty logfile means: log to stdout in foreground mode; daemon mode falls
    # back to supervice.log (with a warning) since stdout is closed.
    logfile: str = ""
    pidfile: str = "supervice.pid"
    loglevel: str = "INFO"
    socket_path: str = field(default_factory=default_socket_path)
    shutdown_timeout: int = 30  # seconds to wait for graceful shutdown
    log_maxbytes: int = 50 * 1024 * 1024  # 50MB default
    log_backups: int = 10  # number of backup log files
    programs: list[ProgramConfig] = field(default_factory=list)
