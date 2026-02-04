import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any
from supervice.logger import get_logger
MAX_EVENT_QUEUE_SIZE = 1000
EventHandler = Callable[[Event], Awaitable[None]]

class EventType(Enum):
    PROCESS_STATE_STARTING = auto()
    PROCESS_STATE_RUNNING = auto()
    PROCESS_STATE_BACKOFF = auto()
    PROCESS_STATE_STOPPING = auto()
    PROCESS_STATE_EXITED = auto()
    PROCESS_STATE_STOPPED = auto()
    PROCESS_STATE_FATAL = auto()
    PROCESS_STATE_UNKNOWN = auto()
    PROCESS_STATE_UNHEALTHY = auto()
    HEALTHCHECK_PASSED = auto()
    HEALTHCHECK_FAILED = auto()

@dataclass
class Event:
    type: EventType
    payload: dict[str, Any]
