"""Regression tests for the 2026-08-30 audit round (issuedb #15).

Each class pins one finding that was confirmed live; the executable
reproductions live in audit/evaluations/ and these are the fast in-suite
guards. SPECS/14-audit-round-2-fixes.md carries the spec.
"""

import asyncio
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from supervice.core import Supervisor
from supervice.events import EventBus, EventType
from supervice.health import ScriptHealthChecker
from supervice.models import HealthCheckConfig, HealthCheckType, ProgramConfig
from supervice.process import BACKOFF, RUNNING, STARTING, Process, _ChildLogWriter
from supervice.rpc import RPCServer


def _write(path: str, body: str) -> None:
    with open(path, "w") as f:
        f.write(body)


def _procfs_sees_us() -> bool:
    """Trust /proc only after it reproduces THIS process's own cmdline.

    FreeBSD ships a bare /proc mountpoint with nothing mounted: isdir is
    True, listdir is empty, and a counter that trusts it returns [] forever —
    turning the absence assertions below into false passes. Mirrors
    audit/evaluations/probelib.py (not importable here: the sdist ships
    tests/ but not audit/). Found live on FreeBSD 15.1.
    """
    try:
        with open("/proc/%d/cmdline" % os.getpid(), "rb") as f:
            return b"\x00" in f.read()
    except OSError:
        return False


def _pids_with_argv(argv: list[str]) -> list[int]:
    if _procfs_sees_us():
        want = "\x00".join(argv) + "\x00"
        found = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % entry, "rb") as f:
                    if f.read().decode("utf-8", "replace") == want:
                        found.append(int(entry))
            except OSError:
                continue
        return found
    # FreeBSD/macOS: repeated -o and -A, never comma-joined specs or -e
    # (see tests/test_portability.py for why).
    want_cmd = " ".join(argv)
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1] == want_cmd and parts[0].isdigit():
            found.append(int(parts[0]))
    return found


async def _wait_for(predicate, timeout: float, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(interval)
    return None


class TestFailedStartupDoesNotClobberState(unittest.TestCase):
    """A start refused by the pidfile lock must not touch the state file:
    the file belongs to the live sibling holding the lock."""

    def test_state_file_untouched_when_pidfile_held(self) -> None:
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, "p.state.json")
        sentinel = {
            "version": 1,
            "children": [
                {
                    "name": "keeper",
                    "pid": 12345,
                    "pgid": 12345,
                    "token": "starttime:1 pgid:12345",
                    "pdeathsig": True,
                    "reconcile": "auto",
                }
            ],
        }
        _write(state, json.dumps(sentinel))
        conf = os.path.join(tmp, "c.ini")
        _write(
            conf,
            "[supervice]\n"
            "pidfile=%(d)s/p.pid\n"
            "socket=%(d)s/p.sock\n"
            "state_file=%(d)s/p.state.json\n"
            "[program:keeper]\n"
            "command=sleep 60\n"
            "autostart=true\n" % {"d": tmp},
        )

        # Play the live sibling: hold the pidfile flock ourselves.
        holder = os.open(os.path.join(tmp, "p.pid"), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

            supervisor = Supervisor()
            supervisor.load_config(conf)
            with self.assertRaises(RuntimeError):
                asyncio.run(supervisor.run())
        finally:
            os.close(holder)

        with open(state) as f:
            self.assertEqual(json.load(f), sentinel)
        shutil.rmtree(tmp, ignore_errors=True)


class TestSpawnedChildAnnouncedDuringStartsecs(unittest.TestCase):
    """PROCESS_SPAWNED must fire, with the pid, while the child is still
    inside its startsecs window — that event is what puts the child in the
    reconciliation snapshot before RUNNING is ever reached."""

    def test_spawn_event_carries_pid_while_starting(self) -> None:
        async def scenario() -> None:
            bus = EventBus()
            bus.start()
            seen: list[tuple[int, str]] = []

            async def recorder(event) -> None:
                seen.append((event.payload["pid"], process.state))

            bus.subscribe(EventType.PROCESS_SPAWNED, recorder)
            config = ProgramConfig(
                name="slow", command="sleep 30", startsecs=10, autostart=True
            )
            process = Process(config, bus)
            await process.start()
            try:
                got = await _wait_for(lambda: seen, timeout=5)
                self.assertTrue(got, "PROCESS_SPAWNED never published")
                pid, state_at_delivery = seen[0]
                assert process.process is not None
                self.assertEqual(pid, process.process.pid)
                self.assertEqual(state_at_delivery, STARTING)
            finally:
                await process.stop()
                await bus.stop()

        asyncio.run(scenario())


class TestStartDuringBackoffIsPrompt(unittest.TestCase):
    """An explicit start while the supervise loop sleeps out a backoff delay
    must wake the loop and spawn now, not after the residual delay."""

    def test_start_process_interrupts_backoff_wait(self) -> None:
        tmp = tempfile.mkdtemp()
        prog = os.path.join(tmp, "prog.sh")
        _write(prog, "exit 1\n")

        async def scenario() -> None:
            config = ProgramConfig(
                name="flappy",
                command="/bin/sh %s" % prog,
                startsecs=1,
                startretries=10,
                autorestart=True,
                autostart=True,
            )
            process = Process(config, EventBus())
            await process.start()
            try:
                in_long_backoff = await _wait_for(
                    lambda: process.state == BACKOFF and process.backoff >= 3,
                    timeout=15,
                )
                self.assertTrue(in_long_backoff, "never reached a >=3s backoff wait")

                _write(prog, "exec sleep 30\n")
                t0 = time.monotonic()
                final_state = await process.start_process()
                elapsed = time.monotonic() - t0
                self.assertEqual(final_state, RUNNING)
                self.assertLess(
                    elapsed,
                    2.5,
                    "start served the residual backoff delay (%.1fs)" % elapsed,
                )
            finally:
                await process.stop()

        asyncio.run(scenario())
        shutil.rmtree(tmp, ignore_errors=True)


class TestHealthScriptTimeoutKillsSubtree(unittest.TestCase):
    """A timed-out or cancelled script check must take its whole process
    group with it — `sh -c` grandchildren included."""

    def _checker(self, command: str) -> ScriptHealthChecker:
        return ScriptHealthChecker(
            HealthCheckConfig(
                type=HealthCheckType.SCRIPT, timeout=1, command=command
            )
        )

    def test_timeout_leaves_no_grandchild(self) -> None:
        marker = ["sleep", "87.65"]

        async def scenario() -> None:
            # Presence first: an absence assertion from a counter that has
            # never seen the grandchild alive proves nothing (a blind counter
            # made this a false pass on FreeBSD's stub /proc).
            task = asyncio.create_task(self._checker("sleep 87.65 & wait").check())
            appeared = await _wait_for(lambda: _pids_with_argv(marker), timeout=3)
            self.assertTrue(appeared, "check subtree never observed — counter blind?")
            result = await task
            self.assertFalse(result.healthy)
            self.assertIn("timed out", result.message)
            gone = await _wait_for(
                lambda: not _pids_with_argv(marker) or None, timeout=2
            )
            self.assertTrue(gone, "grandchild leaked: %s" % _pids_with_argv(marker))

        asyncio.run(scenario())

    def test_cancellation_leaves_no_grandchild(self) -> None:
        marker = ["sleep", "87.66"]

        async def scenario() -> None:
            task = asyncio.create_task(self._checker("sleep 87.66 & wait").check())
            appeared = await _wait_for(lambda: _pids_with_argv(marker), timeout=3)
            self.assertTrue(appeared, "check subtree never observed")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            gone = await _wait_for(
                lambda: not _pids_with_argv(marker) or None, timeout=2
            )
            self.assertTrue(gone, "cancelled check leaked: %s" % _pids_with_argv(marker))

        asyncio.run(scenario())


class TestPumpKeepsDrainingOnWriterFailure(unittest.TestCase):
    """A broken log sink must not stop the pump: an unread pipe fills and
    blocks the child in write(2)."""

    def test_pump_drains_to_eof_after_write_failure(self) -> None:
        tmp = tempfile.mkdtemp()
        logdir = os.path.join(tmp, "logs")
        os.makedirs(logdir)

        async def scenario() -> None:
            writer = _ChildLogWriter(os.path.join(logdir, "out.log"), maxbytes=64, backups=1)
            shutil.rmtree(logdir)  # every rotation from now on fails

            process = Process(ProgramConfig(name="t", command="true"), EventBus())
            reader = asyncio.StreamReader()
            reader.feed_data(b"x" * 200)  # exceeds maxbytes -> rotate -> raises
            reader.feed_data(b"y" * 200)  # must still be consumed
            reader.feed_eof()
            await asyncio.wait_for(process._pump_stream(reader, writer), timeout=5)
            self.assertTrue(reader.at_eof())

        asyncio.run(scenario())
        shutil.rmtree(tmp, ignore_errors=True)


class TestProbeSocketGarbageIsUnknown(unittest.TestCase):
    """An occupant replying protocol-violating garbage is 'unknown', never an
    unhandled ValueError that crashes startup."""

    def test_oversized_length_prefix_classified_unknown(self) -> None:
        tmp = tempfile.mkdtemp()
        sock = os.path.join(tmp, "g.sock")

        async def scenario() -> None:
            async def occupant(reader, writer):
                await reader.read(4096)
                writer.write(b"\xff\xff\xff\xffgarbage")
                await writer.drain()
                writer.close()

            server = await asyncio.start_unix_server(occupant, sock)
            try:
                rpc = RPCServer(sock, supervisor=MagicMock())
                self.assertEqual(await rpc._probe_socket(), "unknown")
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(scenario())
        shutil.rmtree(tmp, ignore_errors=True)


class TestStatusOmitsStaleHealth(unittest.TestCase):
    """A dead process must not wear the previous run's health verdict."""

    def test_healthy_flag_gated_on_liveness(self) -> None:
        async def scenario() -> None:
            dead = Process(ProgramConfig(name="dead", command="true"), EventBus())
            dead.is_healthy = True  # left over from a previous run
            dead.process = None

            alive = Process(ProgramConfig(name="alive", command="true"), EventBus())
            alive.is_healthy = True
            alive.process = MagicMock(returncode=None, pid=4242)

            supervisor = MagicMock()
            supervisor.processes = {"dead": dead, "alive": alive}
            rpc = RPCServer("/nonexistent.sock", supervisor)
            response = await rpc.process_request({"command": "status"})
            by_name = {p["name"]: p for p in response["processes"]}
            self.assertNotIn("healthy", by_name["dead"])
            self.assertEqual(by_name["alive"]["healthy"], True)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
