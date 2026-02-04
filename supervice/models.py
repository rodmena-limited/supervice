from dataclasses import dataclass, field
from enum import Enum

class HealthCheckType(Enum):
    NONE = 'none'
    TCP = 'tcp'
    SCRIPT = 'script'
