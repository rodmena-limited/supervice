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

    def _handle_sighup(self) -> None:
        self.logger.info("Received SIGHUP, ignoring (use 'reload' command instead)")

    async def reload_config(self) -> dict[str, list[str]]:
        self.logger.info("Reloading config from %s", self._config_path)
        new_config = parse_config(self._config_path)

        old_names = set(self.processes.keys())
        new_names: set[str] = set()
        new_programs: list[ProgramConfig] = []

        for prog in new_config.programs:
            if prog.numprocs > 1:
                for i in range(prog.numprocs):
                    new_names.add("%s:%02d" % (prog.name, i))
            else:
                new_names.add(prog.name)
            new_programs.append(prog)

        added = new_names - old_names
        removed = old_names - new_names
        changed = [n for n in (old_names & new_names) if self._program_changed(n, new_config)]

        for name in removed:
            proc = self.processes[name]
            await proc.stop_process()
            await proc.stop()
            del self.processes[name]
            for group_procs in self.groups.values():
                if name in group_procs:
                    group_procs.remove(name)

        if added:
            self._create_processes(new_programs)
            for name in added:
                if name in self.processes:
                    await self.processes[name].start()

        self.config = new_config

        for name in changed:
            self.logger.warning("Program '%s' config changed; restart manually to apply", name)

        result: dict[str, list[str]] = {
            "added": sorted(added),
            "removed": sorted(removed),
            "changed": sorted(changed),
        }
        self.logger.info("Reload complete: %s", result)
        return result

    def _program_changed(self, name: str, new_config: SupervisorConfig) -> bool:
        old_proc = self.processes.get(name)
        if not old_proc:
            return False
        for prog in new_config.programs:
            if prog.numprocs > 1:
                for i in range(prog.numprocs):
                    if "%s:%02d" % (prog.name, i) == name:
                        new_conf = replace(prog, name=name)
                        return old_proc.config != new_conf
            elif prog.name == name:
                return old_proc.config != prog
        return False

    def _acquire_pidfile_lock(self) -> None:
        fd = os.open(self.config.pidfile, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as e:
            os.close(fd)
            msg = "Another supervice instance is already running (pidfile: %s)"
            self.logger.critical(msg, self.config.pidfile)
            raise RuntimeError(msg % self.config.pidfile) from e
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode())
        self._pidfile_fd = fd

    def _release_pidfile_lock(self) -> None:
        if self._pidfile_fd is not None:
            try:
                fcntl.flock(self._pidfile_fd, fcntl.LOCK_UN)
                os.close(self._pidfile_fd)
            except OSError:
                pass
            self._pidfile_fd = None
            if self.config.pidfile and os.path.exists(self.config.pidfile):
                try:
                    os.remove(self.config.pidfile)
                except OSError:
                    pass
