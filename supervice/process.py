import asyncio
import ctypes
import os
import shlex
import signal
import sys
from asyncio import subprocess
from typing import IO, Any
from supervice.events import Event, EventBus, EventType
from supervice.health import HealthChecker, create_health_checker
from supervice.logger import get_logger
from supervice.models import HealthCheckType, ProgramConfig
STOPPED = "STOPPED"
STARTING = "STARTING"
RUNNING = "RUNNING"
BACKOFF = "BACKOFF"
STOPPING = "STOPPING"
EXITED = "EXITED"
FATAL = "FATAL"
UNHEALTHY = "UNHEALTHY"  # Process is running but health checks failing
EXIT_CODE_USER_SWITCH_FAILED = 126  # User switching failed
EXIT_CODE_PREEXEC_FAILED = 127  # Other preexec failure

class Process:
    def __init__(self, config: ProgramConfig, event_bus: EventBus):
        self.config = config
        self.state = STOPPED
        self.process: subprocess.Process | None = None
        self.backoff = 0
        self.stop_event = asyncio.Event()
        self.logger = get_logger()
        self._task: asyncio.Task[None] | None = None
        self.event_bus = event_bus
        self.should_run = config.autostart
        # Lock for state transitions to prevent race conditions
        self._state_lock = asyncio.Lock()
        # Event to signal state changes (for start_process waiting)
        self._state_changed = asyncio.Event()
        # Health check state
        self._health_checker: HealthChecker | None = create_health_checker(config.healthcheck)
        self._health_task: asyncio.Task[None] | None = None
        self._health_failures = 0
        self.is_healthy: bool | None = None  # None = not checked yet, True/False = check result
        self._spawn_time: float = 0.0
        self.started_at: float | None = None

    async def _change_state(self, new_state: str) -> None:
        async with self._state_lock:
            old_state = self.state
            self.state = new_state

            # Signal that state has changed (for waiters in start_process)
            self._state_changed.set()
            self._state_changed.clear()

            # Map state string to EventType
            event_type = None
            if new_state == STARTING:
                event_type = EventType.PROCESS_STATE_STARTING
            elif new_state == RUNNING:
                event_type = EventType.PROCESS_STATE_RUNNING
            elif new_state == BACKOFF:
                event_type = EventType.PROCESS_STATE_BACKOFF
            elif new_state == STOPPING:
                event_type = EventType.PROCESS_STATE_STOPPING
            elif new_state == EXITED:
                event_type = EventType.PROCESS_STATE_EXITED
            elif new_state == STOPPED:
                event_type = EventType.PROCESS_STATE_STOPPED
            elif new_state == FATAL:
                event_type = EventType.PROCESS_STATE_FATAL
            elif new_state == UNHEALTHY:
                event_type = EventType.PROCESS_STATE_UNHEALTHY

            if event_type:
                payload = {
                    "processname": self.config.name,
                    "groupname": self.config.group or self.config.name,
                    "from_state": old_state,
                    "pid": self.process.pid if self.process else None,
                }
                self.event_bus.publish(Event(type=event_type, payload=payload))
