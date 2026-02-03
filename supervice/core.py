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

    def load_config(self, path: str) -> None:
        self.logger.info("Loading config from %s", path)
        self._config_path = path
        try:
            self.config = parse_config(path)
            # Re-setup logger with config values
            setup_logger(
                level=self.config.loglevel,
                logfile=self.config.logfile,
                maxbytes=self.config.log_maxbytes,
                backups=self.config.log_backups,
            )
            # Initialize RPC server with configured socket path
            self.rpc_server = RPCServer(self.config.socket_path, self)
        except Exception as e:
            self.logger.critical("Failed to load config: %s", e)
            raise

        self._create_processes(self.config.programs)

    def _expand_logfile(path: str | None, process_num: int) -> str | None:
        if path is None:
            return None
        return path.replace("%(process_num)s", "%02d" % process_num)

    def _create_processes(
        self,
        programs: list[ProgramConfig],
    ) -> None:
        for prog_config in programs:
            group_name = prog_config.group if prog_config.group else prog_config.name
            if group_name not in self.groups:
                self.groups[group_name] = []

            if prog_config.numprocs > 1:
                for i in range(prog_config.numprocs):
                    instance_name = "%s:%02d" % (prog_config.name, i)
                    p_conf = replace(
                        prog_config,
                        name=instance_name,
                        stdout_logfile=self._expand_logfile(prog_config.stdout_logfile, i),
                        stderr_logfile=self._expand_logfile(prog_config.stderr_logfile, i),
                    )
                    if instance_name not in self.processes:
                        self.processes[instance_name] = Process(p_conf, self.event_bus)
                        self.groups[group_name].append(instance_name)
            else:
                if prog_config.name not in self.processes:
                    self.processes[prog_config.name] = Process(prog_config, self.event_bus)
                    self.groups[group_name].append(prog_config.name)

    async def run(self) -> None:
        self.logger.info("Supervisor starting")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_signal, sig)
        loop.add_signal_handler(signal.SIGHUP, self._handle_sighup)

        if self.config.pidfile:
            self._acquire_pidfile_lock()

        self.event_bus.start()
        start_tasks = []
        for process in self.processes.values():
            start_tasks.append(process.start())

        if start_tasks:
            await asyncio.gather(*start_tasks)

        if self.rpc_server:
            await self.rpc_server.start()

        await self._shutdown_event.wait()

        await self.shutdown()

    def _handle_signal(self, sig: int) -> None:
        self.logger.info("Received signal %d", sig)
        self._shutdown_event.set()
