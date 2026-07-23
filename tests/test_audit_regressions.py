"""Regression tests for the 2026-07-23 critical-systems audit findings.

Each test class maps to a finding in audit-2026-07-23.md (C = critical,
H = high). These must stay green for the certification to hold.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from supervice.config import ConfigValidationError, parse_config
from supervice.core import Supervisor
from supervice.events import Event, EventBus, EventType
from supervice.models import ProgramConfig, default_socket_path
from supervice.process import (
    EXITED,
    FATAL,
    RUNNING,
    STARTING,
    STOPPED,
    Process,
    _ChildLogWriter,
)
from supervice.rpc import RPCServer


def _write(path: str, body: str) -> None:
    with open(path, "w") as f:
        f.write(body)


class TestC1ConfigInterpolation(unittest.TestCase):
    """C1: '%' in config values must be taken literally."""

    def test_documented_process_num_syntax_parses(self) -> None:
        tmp = tempfile.mkdtemp()
        conf = os.path.join(tmp, "c.ini")
        _write(
            conf,
            "[program:worker]\n"
            "command = /bin/sleep 5\n"
            "numprocs = 2\n"
            "autostart = false\n"
            "stdout_logfile = " + tmp + "/worker_%(process_num)s.log\n",
        )
        cfg = parse_config(conf)
        self.assertEqual(cfg.programs[0].stdout_logfile, tmp + "/worker_%(process_num)s.log")

    def test_bare_percent_in_command_parses(self) -> None:
        tmp = tempfile.mkdtemp()
        conf = os.path.join(tmp, "c.ini")
        _write(conf, "[program:stamper]\ncommand = /bin/date +%s\nautostart = false\n")
        cfg = parse_config(conf)
        self.assertEqual(cfg.programs[0].command, "/bin/date +%s")

    def test_process_num_expanded_in_command_env_and_logfiles(self) -> None:
        prog = ProgramConfig(
            name="w",
            command="server --port 90%(process_num)s",
            numprocs=2,
            environment={"IDX": "%(process_num)s"},
            stdout_logfile="/tmp/w-%(process_num)s.log",
        )
        inst = Supervisor._instance_config(prog, 1)
        self.assertEqual(inst.name, "w:01")
        self.assertEqual(inst.command, "server --port 9001")
        self.assertEqual(inst.environment["IDX"], "01")
        self.assertEqual(inst.stdout_logfile, "/tmp/w-01.log")


class TestC2StopDuringSpawn(unittest.TestCase):
    """C2: a stop landing mid-spawn must actually stop the child."""

    def test_stop_during_spawn_kills_child(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            config = ProgramConfig(
                name="t", command="/bin/sleep 30", autostart=True, autorestart=True,
                startsecs=0, stopwaitsecs=2,
            )
            p = Process(config, bus)
            stop_state: list[str] = []
            stop_done = asyncio.Event()

            async def on_starting(ev: Event) -> None:
                # Lands while spawn() is suspended inside create_subprocess_exec.
                stop_state.append(await p.stop_process())
                stop_done.set()

            bus.subscribe(EventType.PROCESS_STATE_STARTING, on_starting)
            await p.start()
            await asyncio.wait_for(stop_done.wait(), 10)
            await asyncio.sleep(0.3)

            child_alive = p.process is not None and p.process.returncode is None
            self.assertFalse(child_alive, "child must not survive an acknowledged stop")
            self.assertEqual(p.state, STOPPED)
            # And the stop RPC must have reported a settled down-state.
            self.assertIn(stop_state[0], (STOPPED, EXITED))

            await p.stop()
            await bus.stop()

        asyncio.run(run())


class TestC3ReloadAppliesConfig(unittest.TestCase):
    """C3/H5: reload must apply changed configs and not report phantom changes."""

    def _conf(self, tmp: str, cmd: str) -> str:
        return (
            "[supervice]\npidfile=%s/s.pid\nsocket=%s/s.sock\nlogfile=%s/s.log\n"
            "[program:w]\ncommand = %s\nautostart = false\n"
            "[program:m]\ncommand = /bin/sleep 111\nautostart = false\nnumprocs = 2\n"
            "stdout_logfile = %s/m-%s.log\n"
            % (tmp, tmp, tmp, cmd, tmp, "%(process_num)s")
        )

    def test_reload_applies_changed_command(self) -> None:
        async def run() -> None:
            tmp = tempfile.mkdtemp()
            conf = os.path.join(tmp, "s.ini")
            _write(conf, self._conf(tmp, "/bin/sleep 111"))
            sup = Supervisor()
            sup.load_config(conf)

            _write(conf, self._conf(tmp, "/bin/sleep 222"))
            result = await sup.reload_config()
            self.assertIn("w", result["changed"])
            # The live Process object must now hold the new command, so any
            # subsequent restart uses it.
            self.assertEqual(sup.processes["w"].config.command, "/bin/sleep 222")

        asyncio.run(run())

    def test_reload_of_unchanged_file_reports_nothing(self) -> None:
        async def run() -> None:
            tmp = tempfile.mkdtemp()
            conf = os.path.join(tmp, "s.ini")
            _write(conf, self._conf(tmp, "/bin/sleep 111"))
            sup = Supervisor()
            sup.load_config(conf)

            result = await sup.reload_config()
            self.assertEqual(result["changed"], [], "identical reload must report no changes")
            self.assertEqual(result["added"], [])
            self.assertEqual(result["removed"], [])
            # Instance logfile expansion applied to created processes.
            self.assertTrue(sup.processes["m:00"].config.stdout_logfile.endswith("m-00.log"))

        asyncio.run(run())


class TestC4StartAfterFatal(unittest.TestCase):
    """C4: starting a FATAL process must succeed and report the truth."""

    def test_start_on_fatal_recovers_without_error(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            config = ProgramConfig(
                name="t", command="/bin/false", autostart=True, autorestart=True,
                startsecs=1, startretries=0, stopwaitsecs=2,
            )
            p = Process(config, bus)
            await p.start()
            for _ in range(100):
                if p.state == FATAL:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(p.state, FATAL)

            # Operator fixed the underlying problem; start must not raise and
            # must actually reach RUNNING.
            p.config.command = "/bin/sleep 30"
            p.config.startsecs = 0
            final = await p.start_process()
            self.assertEqual(final, RUNNING)
            self.assertEqual(p.state, RUNNING)

            await p.stop()
            await bus.stop()

        asyncio.run(run())


class TestH1HealthRestartBounded(unittest.TestCase):
    """H1: health-triggered restarts must be paced and bounded, not a storm."""

    def test_persistent_unhealthy_escalates_to_fatal(self) -> None:
        async def run() -> None:
            from supervice.models import HealthCheckConfig, HealthCheckType

            bus = EventBus()
            bus.start()
            spawns = 0

            async def count(ev: Event) -> None:
                nonlocal spawns
                spawns += 1

            bus.subscribe(EventType.PROCESS_STATE_STARTING, count)
            hc = HealthCheckConfig(
                type=HealthCheckType.SCRIPT, command="exit 1",
                interval=1, timeout=2, retries=1, start_period=0,
            )
            config = ProgramConfig(
                name="t", command="/bin/sleep 60", autostart=True, autorestart=True,
                startsecs=0, startretries=1, stopwaitsecs=2, healthcheck=hc,
            )
            p = Process(config, bus)
            await p.start()

            for _ in range(160):
                if p.state == FATAL:
                    break
                await asyncio.sleep(0.1)

            self.assertEqual(p.state, FATAL, "must give up after bounded health restarts")
            self.assertFalse(p.should_run)
            # startretries=1 -> initial spawn + exactly one health restart.
            self.assertEqual(spawns, 2, "restarts must be bounded by startretries")

            await p.stop()
            await bus.stop()

        asyncio.run(run())


class TestH2SpawnErrorsRetried(unittest.TestCase):
    """H2: transient spawn errors retry with backoff before FATAL."""

    def test_spawn_error_retries_then_fatal(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            attempts = 0

            async def count(ev: Event) -> None:
                nonlocal attempts
                attempts += 1

            bus.subscribe(EventType.PROCESS_STATE_STARTING, count)
            config = ProgramConfig(
                name="t", command="/bin/sleep 5", autostart=True, autorestart=True,
                startsecs=0, startretries=2,
                stdout_logfile="/nonexistent_dir_supervice_h2/out.log",
            )
            p = Process(config, bus)
            await p.start()

            for _ in range(150):
                if p.state == FATAL:
                    break
                await asyncio.sleep(0.1)

            self.assertEqual(p.state, FATAL)
            self.assertEqual(attempts, 3, "startretries=2 -> exactly 3 attempts")

            await p.stop()
            await bus.stop()

        asyncio.run(run())


class TestH3TruthfulStart(unittest.TestCase):
    """H3: 'start' must never claim success for a process that is not RUNNING."""

    def test_start_reports_non_running_as_error(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            config = ProgramConfig(
                name="t", command="/bin/false", autostart=False, autorestart=True,
                startsecs=1, startretries=99, stopwaitsecs=2,
            )
            p = Process(config, bus)
            await p.start()
            await asyncio.sleep(0.1)

            supervisor = MagicMock()
            supervisor.processes = {"t": p}
            server = RPCServer("unused.sock", supervisor)
            result = await server._start_one("t")

            self.assertEqual(result["status"], "error")
            self.assertNotIn("Started", result.get("message", ""))

            await p.stop()
            await bus.stop()

        asyncio.run(run())

    def test_startsecs_gates_running_state(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            config = ProgramConfig(name="t", command="/bin/sleep 60", startsecs=2)
            p = Process(config, bus)
            spawn_task = asyncio.create_task(p.spawn())

            await asyncio.sleep(1.0)
            self.assertEqual(p.state, STARTING, "must not be RUNNING before startsecs")
            await asyncio.sleep(1.6)
            self.assertEqual(p.state, RUNNING, "must be RUNNING after surviving startsecs")

            await p.kill()
            try:
                await asyncio.wait_for(spawn_task, timeout=3)
            except asyncio.TimeoutError:
                spawn_task.cancel()
            await bus.stop()

        asyncio.run(run())

    def test_exit_within_startsecs_is_start_failure(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            attempts = 0

            async def count(ev: Event) -> None:
                nonlocal attempts
                attempts += 1

            bus.subscribe(EventType.PROCESS_STATE_STARTING, count)
            config = ProgramConfig(
                name="t", command="/bin/false", autostart=True, autorestart=True,
                startsecs=1, startretries=1,
            )
            p = Process(config, bus)
            await p.start()
            for _ in range(100):
                if p.state == FATAL:
                    break
                await asyncio.sleep(0.1)
            self.assertEqual(p.state, FATAL)
            self.assertEqual(attempts, 2, "startretries=1 -> two attempts, then FATAL")
            await p.stop()
            await bus.stop()

        asyncio.run(run())


class TestH4SocketDefault(unittest.TestCase):
    """H4: default socket path must avoid world-writable /tmp."""

    def test_prefers_xdg_runtime_dir(self) -> None:
        tmp = tempfile.mkdtemp()
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            self.assertEqual(default_socket_path(), os.path.join(tmp, "supervice.sock"))

    def test_falls_back_to_home_without_xdg(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
        with patch.dict(os.environ, env, clear=True):
            path = default_socket_path()
        if os.geteuid() == 0:
            self.assertEqual(path, "/run/supervice.sock")
        else:
            self.assertEqual(path, os.path.join(os.path.expanduser("~"), ".supervice.sock"))
        self.assertFalse(path.startswith("/tmp/"))


class TestChildLogRotation(unittest.TestCase):
    """M3: child logs must rotate at maxbytes instead of growing forever."""

    def test_writer_rotates(self) -> None:
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "x.log")
        writer = _ChildLogWriter(path, maxbytes=1000, backups=2)
        for _ in range(5):
            writer.write(b"a" * 600)
        writer.close()

        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.path.exists(path + ".1"))
        self.assertTrue(os.path.exists(path + ".2"))
        self.assertFalse(os.path.exists(path + ".3"), "backups must be capped")
        self.assertLessEqual(os.path.getsize(path), 1000)

    def test_process_output_lands_in_rotated_logs(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            tmp = tempfile.mkdtemp()
            path = os.path.join(tmp, "out.log")
            config = ProgramConfig(
                name="t",
                command="sh -c 'yes 0123456789012345678901234567890123456789 | head -c 5000'",
                startsecs=0,
                stdout_logfile=path,
                stdout_logfile_maxbytes=2000,
                stdout_logfile_backups=3,
            )
            p = Process(config, bus)
            await p.spawn()
            total = sum(
                os.path.getsize(f)
                for f in (path, path + ".1", path + ".2", path + ".3")
                if os.path.exists(f)
            )
            self.assertGreaterEqual(total, 5000)
            self.assertTrue(os.path.exists(path + ".1"), "rotation must have occurred")
            self.assertLessEqual(os.path.getsize(path), 2000)
            await bus.stop()

        asyncio.run(run())


class TestConfigValidationHardening(unittest.TestCase):
    """Load-time validation for errors that previously surfaced at spawn."""

    def test_group_with_unknown_program_rejected(self) -> None:
        tmp = tempfile.mkdtemp()
        conf = os.path.join(tmp, "c.ini")
        _write(
            conf,
            "[program:real]\ncommand = /bin/sleep 1\nautostart = false\n"
            "[group:g]\nprograms = real, typo_program\n",
        )
        with self.assertRaises(ConfigValidationError) as ctx:
            parse_config(conf)
        self.assertIn("typo_program", str(ctx.exception))

    def test_unparseable_command_rejected_at_load(self) -> None:
        tmp = tempfile.mkdtemp()
        conf = os.path.join(tmp, "c.ini")
        _write(conf, "[program:bad]\ncommand = sh -c 'unclosed\nautostart = false\n")
        with self.assertRaises(ConfigValidationError):
            parse_config(conf)


class TestStopSettles(unittest.TestCase):
    """stop_process must return only when the process has actually settled."""

    def test_stop_returns_settled_state(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            config = ProgramConfig(
                name="t", command="/bin/sleep 60", autostart=True,
                startsecs=0, stopwaitsecs=3,
            )
            p = Process(config, bus)
            await p.start()
            for _ in range(50):
                if p.state == RUNNING:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(p.state, RUNNING)

            final = await p.stop_process()
            self.assertEqual(final, STOPPED)
            self.assertEqual(p.state, STOPPED)
            self.assertTrue(p.process is None or p.process.returncode is not None)

            # No zombie respawn afterwards: state must remain STOPPED.
            await asyncio.sleep(0.5)
            self.assertEqual(p.state, STOPPED)

            await p.stop()
            await bus.stop()

        asyncio.run(run())


class TestManualStopStateTrail(unittest.TestCase):
    """M1: a manual stop must end (and stay) STOPPED, never drift to BACKOFF."""

    def test_no_backoff_after_manual_stop(self) -> None:
        async def run() -> None:
            bus = EventBus()
            bus.start()
            states: list[str] = []

            async def record(ev: Event) -> None:
                states.append(ev.type.name)

            bus.subscribe(EventType.PROCESS_STATE_BACKOFF, record)
            config = ProgramConfig(
                name="t", command="/bin/sleep 60", autostart=True, autorestart=True,
                startsecs=0, stopwaitsecs=3,
            )
            p = Process(config, bus)
            await p.start()
            for _ in range(50):
                if p.state == RUNNING:
                    break
                await asyncio.sleep(0.05)

            await p.stop_process()
            await asyncio.sleep(0.6)

            self.assertEqual(p.state, STOPPED)
            self.assertEqual(states, [], "manual stop must not emit BACKOFF")

            await p.stop()
            await bus.stop()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
