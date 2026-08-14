import asyncio
import fcntl
import os
import signal
import sys
from dataclasses import replace

from supervice.config import parse_config
from supervice.events import Event, EventBus, EventType
from supervice.logger import get_logger
from supervice.models import HealthCheckType, ProgramConfig, SupervisorConfig
from supervice.process import (
    Process,
    pdeathsig_functional,
    pdeathsig_supported,
    setuid_binary,
)
from supervice.reconcile import (
    ChildRecord,
    StateStore,
    decide,
    kill_group,
    process_start_token,
)
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
        self._reload_lock = asyncio.Lock()
        self.state_store: StateStore | None = None

    def load_config(self, path: str) -> None:
        self.logger.info("Loading config from %s", path)
        self._config_path = path
        try:
            self.config = parse_config(path)
            # Initialize RPC server with configured socket path
            self.rpc_server = RPCServer(self.config.socket_path, self)
            self.state_store = StateStore(
                self.config.state_file or StateStore.default_path(self.config.pidfile)
            )
        except Exception as e:
            self.logger.critical("Failed to load config: %s", e)
            raise

        self._create_processes(self.config.programs)
        self._rebuild_groups(self.config.programs)

    @staticmethod
    def _expand(value: str, process_num: int) -> str:
        return value.replace("%(process_num)s", "%02d" % process_num)

    @classmethod
    def _expand_opt(cls, value: str | None, process_num: int) -> str | None:
        if value is None:
            return None
        return cls._expand(value, process_num)

    @staticmethod
    def _instance_names(prog_config: ProgramConfig) -> list[str]:
        """Return the process instance name(s) a program config expands to."""
        if prog_config.numprocs > 1:
            return ["%s:%02d" % (prog_config.name, i) for i in range(prog_config.numprocs)]
        return [prog_config.name]

    @classmethod
    def _instance_config(cls, prog: ProgramConfig, process_num: int) -> ProgramConfig:
        """Build the effective per-instance config for one numprocs slot.

        %(process_num)s is expanded in command, environment values, and both
        logfile paths. This is the single source of truth for instance configs:
        creation and change-detection must both use it, or reloads misreport.
        """
        if prog.numprocs > 1:
            name = "%s:%02d" % (prog.name, process_num)
        else:
            name = prog.name
        return replace(
            prog,
            name=name,
            command=cls._expand(prog.command, process_num),
            environment={k: cls._expand(v, process_num) for k, v in prog.environment.items()},
            stdout_logfile=cls._expand_opt(prog.stdout_logfile, process_num),
            stderr_logfile=cls._expand_opt(prog.stderr_logfile, process_num),
        )

    def _create_processes(
        self,
        programs: list[ProgramConfig],
    ) -> None:
        self._warn_about_pdeathsig(programs)
        for prog_config in programs:
            if prog_config.numprocs > 1:
                for field_name in ("stdout_logfile", "stderr_logfile"):
                    logpath = getattr(prog_config, field_name)
                    if logpath and "%(process_num)s" not in logpath:
                        self.logger.warning(
                            "Program '%s': %s=%s has no %%(process_num)s but numprocs=%d; "
                            "all instances will write to the same file with interleaved output",
                            prog_config.name,
                            field_name,
                            logpath,
                            prog_config.numprocs,
                        )
                if prog_config.healthcheck.type == HealthCheckType.TCP:
                    self.logger.warning(
                        "Program '%s': numprocs=%d with a TCP health check: all instances "
                        "will probe the same port %s; use %%(process_num)s in the command "
                        "to give each instance its own port",
                        prog_config.name,
                        prog_config.numprocs,
                        prog_config.healthcheck.port,
                    )
            for i in range(prog_config.numprocs):
                p_conf = self._instance_config(prog_config, i)
                if p_conf.name not in self.processes:
                    self.processes[p_conf.name] = Process(p_conf, self.event_bus)

    def _warn_about_pdeathsig(self, programs: list[ProgramConfig]) -> None:
        """Report pdeathsig problems: host-wide ones once, per-program ones per program.

        `pdeathsig` defaults to true, so on a platform without it every program
        in a stock config would otherwise emit an identical line about a
        condition that is host-wide and that no per-program change can fix. A
        twenty-program config produced twenty warnings nobody could act on,
        which trains people to skim past exactly the warning that matters.
        (Reported by macbook-admin-bd8e86 from a Darwin run.)

        The setuid case stays per-program because it genuinely is per-program:
        it depends on that program's command, and the host is otherwise fine.
        """
        wanted = [p for p in programs if p.pdeathsig]
        if not wanted:
            return

        names = ", ".join(p.name for p in wanted[:5])
        if len(wanted) > 5:
            names += ", ... (%d total)" % len(wanted)

        if not pdeathsig_supported():
            self.logger.warning(
                "pdeathsig is unsupported on %s; children will survive an abrupt "
                "supervisor kill. Requested by: %s",
                sys.platform,
                names,
            )
            return

        if not pdeathsig_functional():
            # Supported in principle but the syscall does not work here -- a
            # jail or sandbox. Previously silent: the config said pdeathsig=true
            # and nothing anywhere said otherwise.
            self.logger.warning(
                "pdeathsig is supported on %s but NOT functional here (the syscall "
                "failed); children will survive an abrupt supervisor kill. "
                "Requested by: %s",
                sys.platform,
                names,
            )
            return

        for prog_config in wanted:
            suid = setuid_binary(prog_config.command)
            if suid is not None:
                # The kernel clears the parent-death signal when exec'ing a
                # setuid/setgid image, so this one program loses it even though
                # the host-wide probe passed.
                self.logger.warning(
                    "Program '%s': command '%s' is a setuid/setgid binary; the "
                    "kernel clears pdeathsig at exec, so this program's children "
                    "will survive an abrupt supervisor kill despite pdeathsig=true",
                    prog_config.name,
                    suid,
                )

    def _rebuild_groups(self, programs: list[ProgramConfig]) -> None:
        """Reconcile self.groups with the given config from scratch.

        Groups are derived entirely from the program configs (their ``group``
        field and ``numprocs``), so adding, removing, renaming a group, or
        moving a program between groups is all reflected correctly.
        """
        groups: dict[str, list[str]] = {}
        for prog_config in programs:
            group_name = prog_config.group if prog_config.group else prog_config.name
            members = groups.setdefault(group_name, [])
            for instance_name in self._instance_names(prog_config):
                if instance_name not in members:
                    members.append(instance_name)
        self.groups = groups

    async def run(self) -> None:
        self.logger.info("Supervisor starting")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_signal, sig)
        loop.add_signal_handler(signal.SIGHUP, self._handle_sighup)

        try:
            if self.config.pidfile:
                self._acquire_pidfile_lock()

            self.event_bus.start()

            # Bind the RPC socket BEFORE spawning anything: if another instance
            # is already alive this raises and no duplicate children are forked.
            if self.rpc_server:
                await self.rpc_server.start()

            # Only now: the pidfile lock and the RPC bind above have both
            # succeeded, so no other supervisor is alive for this config and
            # anything still recorded belongs to a dead instance. Running this
            # earlier could kill a live sibling's children.
            self._reconcile_orphans()

            # Record on state changes rather than once after startup:
            # Process.start() only creates the supervise task and returns
            # before any child has been spawned, so a one-shot snapshot here
            # would persist an empty list and disarm the next reconciliation
            # entirely -- silently, since an empty state file looks exactly
            # like a clean shutdown.
            for event_type in (
                EventType.PROCESS_STATE_RUNNING,
                EventType.PROCESS_STATE_EXITED,
                EventType.PROCESS_STATE_STOPPED,
                EventType.PROCESS_STATE_FATAL,
            ):
                self.event_bus.subscribe(event_type, self._on_child_state_change)

            start_tasks = []
            for process in self.processes.values():
                start_tasks.append(process.start())
            if start_tasks:
                await asyncio.gather(*start_tasks)

            await self._shutdown_event.wait()
        finally:
            # Runs on both the normal path and startup failures, so partially
            # started state (event bus, RPC socket, pidfile) is always undone.
            try:
                await self.shutdown()
            except Exception:
                self.logger.critical("Error during shutdown", exc_info=True)

    def _reconcile_orphans(self) -> None:
        """Deal with children a previous supervisor instance left running.

        Never raises: a failure here must not stop the supervisor starting.
        Every uncertain case declines to signal -- see reconcile.decide().
        """
        if self.state_store is None:
            return
        try:
            records = self.state_store.load()
        except Exception:
            self.logger.warning("Could not read reconciliation state; skipping", exc_info=True)
            return

        killed = 0
        for record in records:
            try:
                verdict = decide(record)
            except Exception:
                self.logger.warning(
                    "Program '%s': reconciliation check failed for pid %d; leaving it alone",
                    record.name,
                    record.pid,
                    exc_info=True,
                )
                continue

            if verdict.action == "kill":
                if kill_group(record.pgid, record.pid):
                    killed += 1
                    self.logger.warning(
                        "Program '%s': killed orphaned process group %d (pid %d) - %s",
                        record.name,
                        record.pgid,
                        record.pid,
                        verdict.reason,
                    )
            elif verdict.action == "warn":
                self.logger.warning(
                    "Program '%s': pid %d is still running - %s",
                    record.name,
                    record.pid,
                    verdict.reason,
                )
            elif "NOT ours" in verdict.reason:
                # Deliberately loud: this is the guard doing its job, and a
                # silent refusal looks identical to never having checked.
                self.logger.info(
                    "Program '%s': %s - not touching it", record.name, verdict.reason
                )

        if killed:
            self.logger.warning(
                "Reconciled %d orphaned process group(s) from a previous supervisor", killed
            )

    async def _on_child_state_change(self, event: Event) -> None:
        """Re-snapshot whenever a child starts or stops."""
        self._record_children()

    def _record_children(self) -> None:
        """Snapshot live children so the next start can find them. Never raises."""
        if self.state_store is None:
            return
        records = []
        for process in self.processes.values():
            child = process.process
            if child is None or child.returncode is not None:
                continue
            try:
                pgid = os.getpgid(child.pid)
            except OSError:
                pgid = 0
            token = process_start_token(child.pid)
            if token is None:
                # No identity means the next start could not safely act on this
                # record, so recording it would only invite a pid-only match.
                continue
            records.append(
                ChildRecord(
                    name=process.config.name,
                    pid=child.pid,
                    pgid=pgid,
                    token=token,
                    pdeathsig=process.config.pdeathsig,
                    reconcile=process.config.reconcile,
                )
            )
        try:
            self.state_store.save(records)
        except Exception:
            self.logger.warning("Could not write reconciliation state", exc_info=True)

    def _handle_signal(self, sig: int) -> None:
        self.logger.info("Received signal %d", sig)
        self._shutdown_event.set()

    def _handle_sighup(self) -> None:
        self.logger.info("Received SIGHUP, ignoring (use 'reload' command instead)")

    async def reload_config(self) -> dict[str, list[str]]:
        async with self._reload_lock:
            return await self._reload_config_locked()

    async def _reload_config_locked(self) -> dict[str, list[str]]:
        self.logger.info("Reloading config from %s", self._config_path)
        new_config = parse_config(self._config_path)

        old_names = set(self.processes.keys())
        new_names: set[str] = set()
        new_programs: list[ProgramConfig] = []

        for prog in new_config.programs:
            for name in self._instance_names(prog):
                new_names.add(name)
            new_programs.append(prog)

        added = new_names - old_names
        removed = old_names - new_names
        changed = [n for n in (old_names & new_names) if self._program_changed(n, new_config)]

        for name in removed:
            proc = self.processes[name]
            await proc.stop_process()
            await proc.stop()
            del self.processes[name]

        if added:
            self._create_processes(new_programs)
            for name in added:
                if name in self.processes:
                    await self.processes[name].start()

        # Apply changed configs to the existing Process objects so the next
        # (manual or automatic) restart actually uses the new settings.
        for prog in new_config.programs:
            for i in range(prog.numprocs):
                inst = self._instance_config(prog, i)
                if inst.name in changed and inst.name in self.processes:
                    self.processes[inst.name].update_config(inst)

        self.config = new_config

        # Reconcile group membership with the new config. This handles added,
        # removed, and renamed groups as well as programs that moved between
        # groups — none of which the incremental add/remove logic covers.
        self._rebuild_groups(new_config.programs)

        for name in changed:
            self.logger.info(
                "Program '%s': config updated; takes effect on next restart", name
            )

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
            for i in range(prog.numprocs):
                inst = self._instance_config(prog, i)
                if inst.name == name:
                    return old_proc.config != inst
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
            # Only remove the pidfile if it still contains our PID, so we never
            # delete a file another instance created (e.g. if the path changed
            # or was recreated by a second daemon).
            if self.config.pidfile and os.path.exists(self.config.pidfile):
                try:
                    with open(self.config.pidfile) as f:
                        contents = f.read().strip()
                    if contents == str(os.getpid()):
                        os.remove(self.config.pidfile)
                except OSError:
                    pass

    async def shutdown(self) -> None:
        self.logger.info("Shutting down...")

        if self.rpc_server:
            await self.rpc_server.stop()
        await self.event_bus.stop()

        stop_tasks = []
        for process in self.processes.values():
            stop_tasks.append(process.stop())

        if stop_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*stop_tasks, return_exceptions=True),
                    timeout=self.config.shutdown_timeout,
                )
            except asyncio.TimeoutError:
                self.logger.warning(
                    "Shutdown timed out after %ds, some processes may not have stopped cleanly",
                    self.config.shutdown_timeout,
                )

        # Re-snapshot rather than clear. A clean shutdown usually leaves
        # nothing, but the shutdown timeout above can expire with children
        # still alive -- and those are exactly the orphans the next start needs
        # to know about. Clearing unconditionally would discard the record of
        # the one shutdown that did not go to plan.
        self._record_children()

        # Release the pidfile lock *last* — only after all children have been
        # stopped (or the shutdown timeout fired). Releasing earlier would let a
        # new instance grab the pidfile/socket while our children are still
        # alive, orphaning them.
        self._release_pidfile_lock()

        self.logger.info("Shutdown complete")
