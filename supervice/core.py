import asyncio
import fcntl
import os
import signal
from dataclasses import replace
from supervice.config import parse_config
from supervice.events import EventBus
from supervice.logger import get_logger, setup_logger
from supervice.models import ProgramConfig, SupervisorConfig
from supervice.process import Process
from supervice.rpc import RPCServer

class Supervisor:
    def __init__(self) -> None:
        self.config: SupervisorConfig = SupervisorConfig()
        self.processes: dict[str, Process] = {}
        self.groups: dict[str, list[str]] = {}
        self.logger = get_logger()
        self.event_bus = EventBus()
        self._shutdown_event = asyncio.Event()
        self.rpc_server: RPCServer | None = None
        self._pidfile_fd: int | None = None
        self._config_path: str = ""
