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
                await asyncio.wait_for(self._task, timeout=self.config.stopwaitsecs + 2)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    async def start_process(self) -> None:
        """Request to start the managed process (RPC)."""
        async with self._state_lock:
            if self.state == RUNNING:
                return
            self.should_run = True
            self.backoff = 0  # Reset backoff on manual start

        # Wait for state transition using event-based waiting (not polling)
        deadline = asyncio.get_event_loop().time() + 5.0  # 5 second timeout
        while asyncio.get_event_loop().time() < deadline:
            if self.state == RUNNING:
                return
            if self.state == FATAL:
                raise Exception("Spawn failed")
            try:
                # Wait for state change event with remaining timeout
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                await asyncio.wait_for(self._state_changed.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break

    async def stop_process(self) -> None:
        """Request to stop the managed process (RPC)."""
        async with self._state_lock:
            self.should_run = False
        await self.kill()

    async def supervise(self) -> None:
        """Main supervision loop."""
        while not self.stop_event.is_set():
            if self.should_run:
                if self.state in (STOPPED, EXITED, FATAL, BACKOFF):
                    if self.state == BACKOFF:
                        delay = self.config.startsecs + self.backoff
                        self.logger.info("Backoff %s: waiting %ds", self.config.name, delay)
                        try:
                            await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
                            continue
                        except asyncio.TimeoutError:
                            pass

                    if self.should_run and not self.stop_event.is_set():
                        await self.spawn()

                        if self.state == EXITED:
                            if self.config.autorestart:
                                await self._change_state(BACKOFF)
                                self.backoff += 1
                                if self.backoff > self.config.startretries:
                                    await self._change_state(FATAL)
                                    self.should_run = False
                            else:
                                self.should_run = False
                        elif self.state == FATAL:
                            self.should_run = False

            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass

        if self.process and self.process.returncode is None:
            await self.kill()

    async def spawn(self) -> None:
        await self._change_state(STARTING)
        self.logger.info("Spawning %s", self.config.name)

        stdout_dest: int | IO[Any] = asyncio.subprocess.DEVNULL
        stderr_dest: int | IO[Any] = asyncio.subprocess.DEVNULL
        stdout_file: IO[Any] | None = None
        stderr_file: IO[Any] | None = None

        try:
            # Simple file logging
            if self.config.stdout_logfile:
                stdout_file = open(self.config.stdout_logfile, "a")
                stdout_dest = stdout_file

            if self.config.stderr_logfile:
                stderr_file = open(self.config.stderr_logfile, "a")
                stderr_dest = stderr_file

            # Parse command
            args = shlex.split(self.config.command)
            program = args[0]
            program_args = args[1:]

            if not os.path.isabs(program):
                import shutil

                executable = shutil.which(program)
                if not executable:
                    raise FileNotFoundError("Command not found: %s" % program)
            else:
                executable = program

            # Store user for preexec closure (avoid late binding issues)
            target_user = self.config.user

            def preexec() -> None:
                # User switching - must happen first before any other setup
                if target_user:
                    import pwd

                    try:
                        pw_record = pwd.getpwnam(target_user)
                        # Set supplementary groups first (requires root)
                        try:
                            os.initgroups(target_user, pw_record.pw_gid)
                        except PermissionError:
                            # Not running as root, just set primary group
                            pass
                        os.setgid(pw_record.pw_gid)
                        os.setuid(pw_record.pw_uid)
                    except KeyError:
                        # User not found - write to stderr before exit
                        sys.stderr.write("supervice: user '%s' not found\n" % target_user)
                        sys.stderr.flush()
                        os._exit(EXIT_CODE_USER_SWITCH_FAILED)
                    except PermissionError:
                        msg = "supervice: permission denied switching to user '%s'\n"
                        sys.stderr.write(msg % target_user)
                        sys.stderr.flush()
                        os._exit(EXIT_CODE_USER_SWITCH_FAILED)
                    except OSError as e:
                        msg = "supervice: failed to switch to user '%s': %s\n"
                        sys.stderr.write(msg % (target_user, e))
                        sys.stderr.flush()
                        os._exit(EXIT_CODE_USER_SWITCH_FAILED)

                # Linux: auto-kill child when parent dies
                if sys.platform == "linux":
                    try:
                        pr_set_pdeathsig = 1
                        libc = ctypes.CDLL("libc.so.6", use_errno=True)
                        libc.prctl(pr_set_pdeathsig, signal.SIGKILL)
                    except (OSError, AttributeError):
                        pass

            self.process = await asyncio.create_subprocess_exec(
                executable,
                *program_args,
                stdout=stdout_dest,
                stderr=stderr_dest,
                env={**os.environ, **self.config.environment},
                cwd=self.config.directory,
                preexec_fn=preexec,
                start_new_session=True,
            )
            await self._change_state(RUNNING)
            self.logger.info("Started %s (pid %d)", self.config.name, self.process.pid)

            import time as _time

            self._spawn_time = asyncio.get_event_loop().time()
            self.started_at = _time.time()

            await self._start_health_checks()

            await self.wait()

        except Exception as e:
            self.logger.error("Failed to spawn %s: %s", self.config.name, e)
            await self._change_state(FATAL)
        finally:
            # Always close file handles to prevent leaks
            if stdout_file is not None:
                stdout_file.close()
            if stderr_file is not None:
                stderr_file.close()

    async def wait(self) -> None:
        if not self.process:
            return

        return_code = await self.process.wait()

        # Check for preexec failure codes - these indicate setup errors, not normal exits
        if return_code == EXIT_CODE_USER_SWITCH_FAILED:
            self.logger.error(
                "%s failed: could not switch to user '%s' (exit code %d)",
                self.config.name,
                self.config.user,
                return_code,
            )
            await self._change_state(FATAL)
            return
        elif return_code == EXIT_CODE_PREEXEC_FAILED:
            self.logger.error(
                "%s failed: preexec setup error (exit code %d)", self.config.name, return_code
            )
            await self._change_state(FATAL)
            return

        self.logger.info("%s exited with code %d", self.config.name, return_code)

        runtime = asyncio.get_event_loop().time() - self._spawn_time
        if runtime >= self.config.startsecs:
            self.backoff = 0

        await self._change_state(EXITED)

    async def _run_health_checks(self) -> None:
        """Run health checks periodically while process is running."""
        if not self._health_checker:
            return

        hc_config = self.config.healthcheck

        # Wait for start period before beginning health checks
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
                result = await self._health_checker.check()

                if result.healthy:
                    if self._health_failures > 0:
                        self.logger.info(
                            "%s: health check passed after %d failures",
                            self.config.name,
                            self._health_failures,
                        )
                    self._health_failures = 0
                    self.is_healthy = True

                    # Transition back to RUNNING if we were UNHEALTHY
                    if self.state == UNHEALTHY:
                        await self._change_state(RUNNING)

                    # Emit health check passed event
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

                    # Emit health check failed event
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

                        # Auto-restart on health failure if autorestart is enabled
                        if self.config.autorestart:
                            self.logger.info(
                                "%s: restarting due to health check failures", self.config.name
                            )
                            await self.kill()
                            self._health_failures = 0
                            return  # Exit health check loop, process will be restarted

            except asyncio.CancelledError:
                return
            except Exception as e:
                self.logger.error("%s: health check error: %s", self.config.name, e)

            # Wait for next check interval
            await asyncio.sleep(hc_config.interval)

    async def _start_health_checks(self) -> None:
        """Start the health check task if health checks are configured."""
        if self._health_checker and self.config.healthcheck.type != HealthCheckType.NONE:
            self._health_failures = 0
            self.is_healthy = None
            self._health_task = asyncio.create_task(self._run_health_checks())
