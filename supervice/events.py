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

class EventBus:
    def __init__(self, maxsize: int = MAX_EVENT_QUEUE_SIZE) -> None:
        self.subscribers: dict[EventType, list[EventHandler]] = {}
        self.logger = get_logger()
        # Bounded queue to prevent memory exhaustion
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None
        self._dropped_events = 0

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._process_events())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
