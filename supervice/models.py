from dataclasses import dataclass, field
from enum import Enum


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
    environment: dict[str, str] = field(default_factory=dict)
    directory: str | None = None
    user: str | None = None
    group: str | None = None
    healthcheck: HealthCheckConfig = field(default_factory=HealthCheckConfig)


@dataclass
class SupervisorConfig:
    logfile: str = "supervice.log"
    pidfile: str = "supervice.pid"
    loglevel: str = "INFO"
    socket_path: str = "/tmp/supervice.sock"
    shutdown_timeout: int = 30  # seconds to wait for graceful shutdown
    log_maxbytes: int = 50 * 1024 * 1024  # 50MB default
    log_backups: int = 10  # number of backup log files
    programs: list[ProgramConfig] = field(default_factory=list)
