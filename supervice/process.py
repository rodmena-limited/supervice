import asyncio
import ctypes
import os
import pwd
import shlex
import shutil
import signal
import sys
import time
from asyncio import subprocess

from supervice.events import Event, EventBus, EventType
from supervice.health import HealthChecker, create_health_checker
from supervice.logger import get_logger
from supervice.models import HealthCheckType, ProgramConfig

# Process States
STOPPED = "STOPPED"
STARTING = "STARTING"
RUNNING = "RUNNING"
BACKOFF = "BACKOFF"
STOPPING = "STOPPING"
EXITED = "EXITED"
FATAL = "FATAL"
UNHEALTHY = "UNHEALTHY"  # Process is running but health checks failing

# Upper bound on the delay between restart attempts.
MAX_BACKOFF_DELAY = 30

# Loaded once in the parent at import time. The preexec hook below runs in the
# forked child before exec, where dlopen/imports are unsafe if any other thread
# holds allocator or loader locks — so nothing may be loaded there.
PR_SET_PDEATHSIG = 1
_LIBC: ctypes.CDLL | None = None
if sys.platform == "linux":
    try:
        _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        _LIBC = None

_STATE_EVENTS = {
    STARTING: EventType.PROCESS_STATE_STARTING,
    RUNNING: EventType.PROCESS_STATE_RUNNING,
    BACKOFF: EventType.PROCESS_STATE_BACKOFF,
    STOPPING: EventType.PROCESS_STATE_STOPPING,
    EXITED: EventType.PROCESS_STATE_EXITED,
    STOPPED: EventType.PROCESS_STATE_STOPPED,
    FATAL: EventType.PROCESS_STATE_FATAL,
    UNHEALTHY: EventType.PROCESS_STATE_UNHEALTHY,
}


class ProcessStartError(Exception):
    """An explicit start request ended in FATAL."""


def _pdeathsig_preexec() -> None:
    """Post-fork/pre-exec hook: SIGKILL the child when the parent dies.

    Only ever touches the pre-loaded libc handle — no imports, no dlopen, no
    allocation-heavy work is safe here.
    """
    if _LIBC is not None:
        try:
            _LIBC.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL))
        except Exception:
            pass


class _ChildLogWriter:
    """Size-rotated log sink for one child output stream.

    Writes are ordinary buffered file I/O performed on the event loop — the
    same tradeoff the daemon's own RotatingFileHandler makes.
    """

    def __init__(self, path: str, maxbytes: int, backups: int) -> None:
        self.path = path
        self.maxbytes = maxbytes
        self.backups = backups
        self._file = open(path, "ab")
        try:
            self._size = os.fstat(self._file.fileno()).st_size
        except OSError:
            self._size = 0

    def write(self, chunk: bytes) -> None:
        if self.maxbytes <= 0:
            self._file.write(chunk)
            self._file.flush()
            self._size += len(chunk)
            return
        # Split oversized chunks so the live file never exceeds maxbytes.
        while chunk:
            room = self.maxbytes - self._size
            if room <= 0:
                self._rotate()
                room = self.maxbytes
            part = chunk[:room]
            self._file.write(part)
            self._file.flush()
            self._size += len(part)
            chunk = chunk[len(part):]

    def _rotate(self) -> None:
        self._file.close()
        if self.backups > 0:
            for i in range(self.backups - 1, 0, -1):
                src = "%s.%d" % (self.path, i)
                if os.path.exists(src):
                    os.replace(src, "%s.%d" % (self.path, i + 1))
            os.replace(self.path, "%s.1" % self.path)
        else:
            try:
                os.remove(self.path)
            except OSError:
                pass
        self._file = open(self.path, "ab")
        self._size = 0

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass


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
        # Serializes individual state transitions; transition *sequences* are
        # kept coherent by the ownership rules documented on each method.
        self._state_lock = asyncio.Lock()
        # Set on every transition; waiters clear before re-checking state so no
        # edge is lost. Never cleared by the setter.
        self._state_changed = asyncio.Event()
        # Health check state
        self._health_checker: HealthChecker | None = create_health_checker(
            config.healthcheck, user=config.user
        )
        self._health_task: asyncio.Task[None] | None = None
        self._health_failures = 0
        # Consecutive health-triggered restarts; reset by a passing check or an
        # explicit start request. Bounds health-restart churn (FATAL when it
        # exceeds startretries).
        self._health_restarts = 0
        self.is_healthy: bool | None = None  # None = not checked yet
        self.started_at: float | None = None
        # True once the current/last run survived the startsecs gate. Runs that
        # never reached RUNNING count against startretries; runs that did are
        # restarted indefinitely (with 1s pacing) under autorestart.
        self._reached_running = False
        self._log_tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------------ state

    def _set_state_locked(self, new_state: str) -> None:
        """Perform a transition. Caller must hold _state_lock."""
        old_state = self.state
        self.state = new_state
        self._state_changed.set()
        if new_state == old_state:
            return  # idempotent transition; wake waiters but publish nothing
        event_type = _STATE_EVENTS.get(new_state)
        if event_type:
            payload = {
                "processname": self.config.name,
                "groupname": self.config.group or self.config.name,
                "from_state": old_state,
                "pid": self.process.pid if self.process else None,
            }
            self.event_bus.publish(Event(type=event_type, payload=payload))

    async def _change_state(self, new_state: str) -> None:
        async with self._state_lock:
            self._set_state_locked(new_state)

    def update_config(self, new_config: ProgramConfig) -> None:
        """Swap in a new configuration; takes full effect at the next spawn.

        stopsignal/stopwaitsecs apply immediately; a running health-check task
        keeps its captured settings until the process is restarted.
        """
        self.config = new_config
        self._health_checker = create_health_checker(
            new_config.healthcheck, user=new_config.user
        )

    # ------------------------------------------------- system lifecycle (core)

    async def start(self) -> None:
        """Start the supervision task (system lifecycle)."""
        if self._task and not self._task.done():
            return
        self.stop_event.clear()
        self._task = asyncio.create_task(self.supervise())

    async def stop(self) -> None:
        """Stop the supervision task (system lifecycle)."""
        self.should_run = False
        self.stop_event.set()
        await self.kill()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=self.config.stopwaitsecs + 5)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    # ----------------------------------------------------------- RPC commands

    async def start_process(self) -> str:
        """Request a start (RPC). Returns the state that was reached.

        Raises ProcessStartError only if *this* start attempt ends FATAL — a
        FATAL left over from an earlier run is cleared, not reported as a new
        failure.
        """
        async with self._state_lock:
            if self.state == RUNNING:
                return RUNNING
            self.should_run = True
            self.backoff = 0
            self._health_restarts = 0
            # Clear terminal/waiting states left over from a previous run so
            # they are not mistaken for this request's outcome, and so an
            # explicit start skips any pending backoff delay.
            if self.state in (FATAL, EXITED, BACKOFF):
                self._set_state_locked(STOPPED)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(5.0, self.config.startsecs + 5.0)
        while True:
            self._state_changed.clear()
            if self.state == RUNNING:
                return RUNNING
            if self.state == FATAL:
                raise ProcessStartError(
                    "%s failed to start (state: FATAL)" % self.config.name
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                return self.state
            try:
                await asyncio.wait_for(self._state_changed.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return self.state

    async def stop_process(self) -> str:
        """Request a stop (RPC). Returns the settled state.

        STOPPED/EXITED mean the process is down. STOPPING means it could not be
        killed (unkillable, e.g. uninterruptible I/O) — callers must not treat
        that as success.
        """
        async with self._state_lock:
            self.should_run = False
        await self.kill()

        # A process waiting in BACKOFF has no child to kill; the stop simply
        # cancels the pending retry, which must be reflected as STOPPED.
        async with self._state_lock:
            if not self.should_run and self.state == BACKOFF:
                self._set_state_locked(STOPPED)

        # If a spawn was in flight when the stop landed, kill() had nothing to
        # signal yet; spawn's own stop-recheck will kill the child and settle
        # the state. Wait for that instead of reporting success early.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.stopwaitsecs + 7.0
        while True:
            self._state_changed.clear()
            if self.state in (STOPPED, EXITED, FATAL):
                return self.state
            remaining = deadline - loop.time()
            if remaining <= 0:
                return self.state
            try:
                await asyncio.wait_for(self._state_changed.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return self.state

    # ------------------------------------------------------------ supervision

    async def supervise(self) -> None:
        """Main supervision loop. Sole owner of respawn/backoff decisions."""
        while not self.stop_event.is_set():
            try:
                # Normalize: a cancelled retry (stop while in BACKOFF) settles
                # to STOPPED no matter which path cancelled it.
                if not self.should_run and self.state == BACKOFF:
                    await self._change_state(STOPPED)

                if self.should_run and self.state in (STOPPED, EXITED, FATAL, BACKOFF):
                    if self.state == BACKOFF:
                        delay = min(max(self.backoff, 1), MAX_BACKOFF_DELAY)
                        self.logger.info("Backoff %s: waiting %ds", self.config.name, delay)
                        try:
                            await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                            continue
                        except asyncio.TimeoutError:
                            pass

                    if self.should_run and not self.stop_event.is_set():
                        await self.spawn()

                        if self.state == EXITED:
                            if self.should_run and self.config.autorestart:
                                if self._reached_running:
                                    # A real run ended: restart indefinitely,
                                    # paced at 1s so a short-lived program
                                    # cannot become a tight respawn loop.
                                    self.backoff = 1
                                    await self._change_state(BACKOFF)
                                else:
                                    self.backoff += 1
                                    if self.backoff > self.config.startretries:
                                        self.logger.error(
                                            "%s: giving up after %d failed start attempts",
                                            self.config.name,
                                            self.backoff,
                                        )
                                        await self._change_state(FATAL)
                                        self.should_run = False
                                    else:
                                        await self._change_state(BACKOFF)
                            else:
                                self.should_run = False
                        elif self.state == FATAL:
                            self.should_run = False
            except asyncio.CancelledError:
                raise
            except Exception:
                # Safety net: supervision must never die silently. Mark the
                # process FATAL (visible + alertable) instead of freezing it.
                self.logger.critical(
                    "Supervision error for %s; marking FATAL",
                    self.config.name,
                    exc_info=True,
                )
                self.should_run = False
                try:
                    await self._change_state(FATAL)
                except Exception:
                    pass

            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass

        if self.process and self.process.returncode is None:
            await self.kill()

    async def spawn(self) -> None:
        await self._change_state(STARTING)
        self.logger.info("Spawning %s", self.config.name)
        self._reached_running = False

        stdout_writer: _ChildLogWriter | None = None
        stderr_writer: _ChildLogWriter | None = None
        self._log_tasks = []

        try:
            args = shlex.split(self.config.command)
            if not args:
                raise ValueError("empty command")
            program = args[0]
            if os.path.isabs(program):
                executable = program
            else:
                executable = shutil.which(program) or ""
                if not executable:
                    raise FileNotFoundError("Command not found: %s" % program)

            # Resolve the target user in the parent and let subprocess perform
            # setuid/setgid/setgroups in its C child path. Doing this via a
            # Python preexec_fn is unsafe in a threaded parent (import/allocator
            # deadlocks after fork).
            popen_user: dict[str, object] = {}
            if self.config.user:
                try:
                    pw = pwd.getpwnam(self.config.user)
                except KeyError:
                    self.logger.error(
                        "%s failed: user '%s' not found", self.config.name, self.config.user
                    )
                    await self._change_state(FATAL)
                    return
                popen_user["user"] = pw.pw_uid
                popen_user["group"] = pw.pw_gid
                if os.geteuid() == 0:
                    popen_user["extra_groups"] = os.getgrouplist(
                        self.config.user, pw.pw_gid
                    )

            stdout_dest: int = subprocess.DEVNULL
            stderr_dest: int = subprocess.DEVNULL
            if self.config.stdout_logfile:
                stdout_writer = _ChildLogWriter(
                    self.config.stdout_logfile,
                    self.config.stdout_logfile_maxbytes,
                    self.config.stdout_logfile_backups,
                )
                stdout_dest = subprocess.PIPE
            if self.config.stderr_logfile:
                stderr_writer = _ChildLogWriter(
                    self.config.stderr_logfile,
                    self.config.stderr_logfile_maxbytes,
                    self.config.stderr_logfile_backups,
                )
                stderr_dest = subprocess.PIPE

            preexec = None
            if self.config.pdeathsig and _LIBC is not None:
                preexec = _pdeathsig_preexec

            self.process = await asyncio.create_subprocess_exec(
                executable,
                *args[1:],
                stdout=stdout_dest,
                stderr=stderr_dest,
                env={**os.environ, **self.config.environment},
                cwd=self.config.directory,
                preexec_fn=preexec,
                start_new_session=True,
                **popen_user,  # type: ignore[arg-type]
            )

            if stdout_writer is not None and self.process.stdout is not None:
                self._log_tasks.append(
                    asyncio.create_task(self._pump_stream(self.process.stdout, stdout_writer))
                )
                stdout_writer = None  # the pump owns closing it now
            if stderr_writer is not None and self.process.stderr is not None:
                self._log_tasks.append(
                    asyncio.create_task(self._pump_stream(self.process.stderr, stderr_writer))
                )
                stderr_writer = None

            # A stop request may have landed between the supervise loop's check
            # and the fork; honour it now instead of leaving the child running.
            if not self.should_run or self.stop_event.is_set():
                self.logger.info(
                    "%s: stop requested during spawn; killing child", self.config.name
                )
                await self.kill()
                return

            # startsecs gate: only report RUNNING once the process survived the
            # configured startup window. An exit inside the window is a failed
            # start attempt and counts against startretries.
            if self.config.startsecs > 0:
                exited_early = True
                try:
                    await asyncio.wait_for(
                        self.process.wait(), timeout=self.config.startsecs
                    )
                except asyncio.TimeoutError:
                    exited_early = False
                if exited_early:
                    self.logger.warning(
                        "%s exited before startsecs (%ds): start attempt failed",
                        self.config.name,
                        self.config.startsecs,
                    )
                    await self.wait()
                    return

            self.backoff = 0
            self._reached_running = True
            await self._change_state(RUNNING)
            self.logger.info("Started %s (pid %d)", self.config.name, self.process.pid)
            self.started_at = time.time()

            await self._start_health_checks()
            await self.wait()

        except Exception as e:
            # ValueError: unparseable command/arguments; PermissionError: user
            # switch or exec permission denied. Both are permanent — anything
            # else (EMFILE/EAGAIN, log dir briefly missing, binary mid-deploy)
            # is retried under the normal backoff/startretries policy.
            if isinstance(e, (ValueError, PermissionError)):
                self.logger.error("%s failed permanently: %s", self.config.name, e)
                await self._change_state(FATAL)
            else:
                self.logger.error(
                    "Failed to spawn %s (will retry): %s", self.config.name, e
                )
                await self._change_state(EXITED)
        finally:
            for writer in (stdout_writer, stderr_writer):
                if writer is not None:
                    writer.close()
            if self._log_tasks:
                # Drain child output. EOF arrives promptly once the child (and
                # any pipe-holding descendants) are gone; if a detached
                # descendant keeps the pipe open, leave the pump running — it
                # closes the file itself at EOF.
                await asyncio.wait(self._log_tasks, timeout=2)

    async def _pump_stream(
        self, stream: asyncio.StreamReader, writer: _ChildLogWriter
    ) -> None:
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error("%s: log writer error: %s", self.config.name, e)
        finally:
            writer.close()

    async def wait(self) -> None:
        """Wait for the child to exit and record the outcome.

        If a stop is in progress (STOPPING/STOPPED), the exit is expected and
        this only completes the STOPPING -> STOPPED sequence; otherwise the
        exit is recorded as EXITED for the supervise loop to act on.
        """
        if not self.process:
            return

        return_code = await self.process.wait()
        self.logger.info("%s exited with code %d", self.config.name, return_code)

        async with self._state_lock:
            if self.state in (STOPPING, STOPPED):
                self._set_state_locked(STOPPED)
                return
            self._set_state_locked(EXITED)

    # ---------------------------------------------------------- health checks

    async def _run_health_checks(self) -> None:
        """Run health checks periodically while the process is running."""
        checker = self._health_checker
        if not checker:
            return

        hc_config = self.config.healthcheck

        if hc_config.start_period > 0:
            self.logger.debug(
                "%s: waiting %ds before starting health checks",
                self.config.name,
                hc_config.start_period,
            )
            await asyncio.sleep(hc_config.start_period)

        running_states = (RUNNING, UNHEALTHY)
        while self.state in running_states and self.process and self.process.returncode is None:
            try:
                result = await checker.check()

                if result.healthy:
                    if self._health_failures > 0:
                        self.logger.info(
                            "%s: health check passed after %d failures",
                            self.config.name,
                            self._health_failures,
                        )
                    self._health_failures = 0
                    self._health_restarts = 0
                    self.is_healthy = True

                    if self.state == UNHEALTHY:
                        await self._change_state(RUNNING)

                    self.event_bus.publish(
                        Event(
                            type=EventType.HEALTHCHECK_PASSED,
                            payload={
                                "processname": self.config.name,
                                "message": result.message,
                                "pid": self.process.pid if self.process else None,
                            },
                        )
                    )
                else:
                    self._health_failures += 1
                    self.logger.warning(
                        "%s: health check failed (%d/%d): %s",
                        self.config.name,
                        self._health_failures,
                        hc_config.retries,
                        result.message,
                    )

                    self.event_bus.publish(
                        Event(
                            type=EventType.HEALTHCHECK_FAILED,
                            payload={
                                "processname": self.config.name,
                                "message": result.message,
                                "failures": self._health_failures,
                                "pid": self.process.pid if self.process else None,
                            },
                        )
                    )

                    if self._health_failures >= hc_config.retries:
                        self.is_healthy = False
                        self.logger.error(
                            "%s: health check failed %d times, marking as unhealthy",
                            self.config.name,
                            self._health_failures,
                        )

                        if self.state == RUNNING:
                            await self._change_state(UNHEALTHY)

                        if self.config.autorestart:
                            self._health_restarts += 1
                            self._health_failures = 0
                            if self._health_restarts > self.config.startretries:
                                # Bounded: persistent unhealthiness must not
                                # become an endless kill/respawn cycle.
                                self.logger.error(
                                    "%s: still unhealthy after %d health-triggered "
                                    "restarts; giving up (FATAL)",
                                    self.config.name,
                                    self._health_restarts - 1,
                                )
                                async with self._state_lock:
                                    self.should_run = False
                                await self.kill()
                                await self._change_state(FATAL)
                            else:
                                self.logger.info(
                                    "%s: restarting due to health check failures "
                                    "(restart %d/%d)",
                                    self.config.name,
                                    self._health_restarts,
                                    self.config.startretries,
                                )
                                await self.kill()
                                # Route the respawn through BACKOFF so repeated
                                # health restarts are paced, not immediate.
                                self.backoff = self._health_restarts
                                await self._change_state(BACKOFF)
                            return

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error("%s: health check error: %s", self.config.name, e)

            await asyncio.sleep(hc_config.interval)

    async def _start_health_checks(self) -> None:
        if self._health_checker and self.config.healthcheck.type != HealthCheckType.NONE:
            self._health_failures = 0
            self.is_healthy = None
            self._health_task = asyncio.create_task(self._run_health_checks())

    async def _stop_health_checks(self) -> None:
        task = self._health_task
        self._health_task = None
        # Never cancel-and-await the *current* task: kill() is legitimately
        # called from inside the health task itself (health-triggered restart).
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ---------------------------------------------------------------- killing

    def _signal_group(self, sig: int) -> None:
        if not self.process:
            return
        try:
            pgid = os.getpgid(self.process.pid)
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            pass

    async def force_kill(self) -> None:
        """Immediately SIGKILL the process group without graceful shutdown."""
        await self._stop_health_checks()
        self.started_at = None
        async with self._state_lock:
            self.should_run = False

        if not self.process or self.process.returncode is not None:
            return

        await self._change_state(STOPPING)
        self._signal_group(signal.SIGKILL)
        try:
            await asyncio.wait_for(self.process.wait(), timeout=2)
        except asyncio.TimeoutError:
            self.logger.critical(
                "%s: process group did not die after SIGKILL; leaving state STOPPING",
                self.config.name,
            )
            return
        await self._change_state(STOPPED)

    async def kill(self) -> None:
        await self._stop_health_checks()
        self.started_at = None

        if not self.process or self.process.returncode is not None:
            return

        await self._change_state(STOPPING)
        self.logger.info("Stopping %s", self.config.name)

        sig = getattr(signal, "SIG%s" % self.config.stopsignal, signal.SIGTERM)
        self._signal_group(sig)

        try:
            await asyncio.wait_for(self.process.wait(), timeout=self.config.stopwaitsecs)
        except asyncio.TimeoutError:
            self.logger.warning("%s did not stop, killing process group", self.config.name)
            self._signal_group(signal.SIGKILL)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except asyncio.TimeoutError:
                # Unkillable (e.g. uninterruptible D-state). Claiming STOPPED
                # here would let a duplicate instance be started; stay STOPPING
                # until the exit is actually observed (wait() finishes the
                # sequence whenever the process finally dies).
                self.logger.critical(
                    "%s: process group did not die after SIGKILL; leaving state STOPPING",
                    self.config.name,
                )
                return

        await self._change_state(STOPPED)
