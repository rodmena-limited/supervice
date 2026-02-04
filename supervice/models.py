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
