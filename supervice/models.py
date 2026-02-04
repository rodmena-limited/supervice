from dataclasses import dataclass, field
from enum import Enum

class HealthCheckType(Enum):
    NONE = 'none'
    TCP = 'tcp'
    SCRIPT = 'script'

@dataclass
class HealthCheckConfig:
    """Configuration for process health checks."""
    type: HealthCheckType = HealthCheckType.NONE
    interval: int = 30
    timeout: int = 10
    retries: int = 3
    start_period: int = 10
    port: int | None = None
    host: str = '127.0.0.1'
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
    stopsignal: str = 'TERM'
    stopwaitsecs: int = 10
    stdout_logfile: str | None = None
    stderr_logfile: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    directory: str | None = None
    user: str | None = None
    group: str | None = None
    healthcheck: HealthCheckConfig = field(default_factory=HealthCheckConfig)
