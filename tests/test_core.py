import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from supervice.core import Supervisor
from supervice.events import Event, EventBus, EventType
from supervice.rpc import RPCServer


class TestEventBus(unittest.TestCase):
    def test_pub_sub(self):
        async def run():
            bus = EventBus()
            bus.start()

            received_events = []

            async def handler(event):
                received_events.append(event)

            bus.subscribe(EventType.PROCESS_STATE_STARTING, handler)

            event = Event(EventType.PROCESS_STATE_STARTING, {"pid": 123})
            bus.publish(event)

            await asyncio.sleep(0.1)
            await bus.stop()

            self.assertEqual(len(received_events), 1)
            self.assertEqual(received_events[0].type, EventType.PROCESS_STATE_STARTING)

        asyncio.run(run())


class TestRPCServer(unittest.TestCase):
    def test_server_lifecycle(self):
        async def run():
            socket_path = "test_rpc.sock"
            if os.path.exists(socket_path):
                os.remove(socket_path)

            supervisor_mock = MagicMock()
            server = RPCServer(socket_path, supervisor_mock)

            await server.start()
            self.assertTrue(os.path.exists(socket_path))

            await server.stop()
            self.assertFalse(os.path.exists(socket_path))

        asyncio.run(run())

    def test_process_request_status(self):
        async def run():
            supervisor_mock = MagicMock()
            proc_mock = MagicMock()
            proc_mock.state = "RUNNING"
            proc_mock.process.pid = 999
            supervisor_mock.processes = {"p1": proc_mock}

            server = RPCServer("sock", supervisor_mock)

            request = {"command": "status"}
            response = await server.process_request(request)

            self.assertEqual(response["status"], "ok")

        asyncio.run(run())

    def test_process_request_commands(self):
        async def run():
            supervisor_mock = MagicMock()
            proc_mock = AsyncMock()
            # RPC replies are now truthful: they inspect the state reached.
            proc_mock.state = "STOPPED"
            proc_mock.start_process = AsyncMock(return_value="RUNNING")
            proc_mock.stop_process = AsyncMock(return_value="STOPPED")
            supervisor_mock.processes = {"p1": proc_mock}
            supervisor_mock.groups = {"g1": ["p1"]}

            server = RPCServer("sock", supervisor_mock)

            # Test Start
            res = await server.process_request({"command": "start", "name": "p1"})
            self.assertEqual(res["status"], "ok")
            proc_mock.start_process.assert_called()

            # Test Start Invalid
            res = await server.process_request({"command": "start", "name": "bad"})
            self.assertEqual(res["status"], "error")

            # Test Stop
            res = await server.process_request({"command": "stop", "name": "p1"})
            self.assertEqual(res["status"], "ok")
            proc_mock.stop_process.assert_called()

            # Test Stop Invalid
            res = await server.process_request({"command": "stop", "name": "bad"})
            self.assertEqual(res["status"], "error")

            # Test Group Start
            res = await server.process_request({"command": "startgroup", "name": "g1"})
            self.assertEqual(res["status"], "ok")
            self.assertGreaterEqual(proc_mock.start_process.call_count, 2)

            # Test Group Stop
            res = await server.process_request({"command": "stopgroup", "name": "g1"})
            self.assertEqual(res["status"], "ok")
            self.assertGreaterEqual(proc_mock.stop_process.call_count, 2)

            # Test Invalid Group
            res = await server.process_request({"command": "startgroup", "name": "bad"})
            self.assertEqual(res["status"], "error")

            # Unknown command
            res = await server.process_request({"command": "xyz"})
            self.assertEqual(res["status"], "error")

        asyncio.run(run())


def _write_config(body: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".conf")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    return path


class TestReloadGroupReconciliation(unittest.TestCase):
    """C2: reload_config must reconcile groups with the new config."""

    BASE = """
[supervice]
pidfile=
socket=/tmp/supervice_test_reload.sock

[program:worker]
command=sleep 60
autostart=false

[program:other]
command=sleep 60
autostart=false
"""

    def test_added_group_appears_after_reload(self) -> None:
        async def run() -> None:
            path = _write_config(self.BASE)
            sup = Supervisor()
            sup.load_config(path)
            self.assertNotIn("mygroup", sup.groups)

            with open(path, "w") as f:
                f.write(self.BASE + "\n[group:mygroup]\nprograms=worker,other\n")
            await sup.reload_config()

            self.assertIn("mygroup", sup.groups)
            self.assertEqual(sorted(sup.groups["mygroup"]), ["other", "worker"])
            os.remove(path)

        asyncio.run(run())

    def test_removed_group_gone_after_reload(self) -> None:
        async def run() -> None:
            with_group = self.BASE + "\n[group:mygroup]\nprograms=worker,other\n"
            path = _write_config(with_group)
            sup = Supervisor()
            sup.load_config(path)
            self.assertIn("mygroup", sup.groups)

            with open(path, "w") as f:
                f.write(self.BASE)
            await sup.reload_config()

            self.assertNotIn("mygroup", sup.groups)
            os.remove(path)

        asyncio.run(run())

    def test_program_moved_between_groups(self) -> None:
        async def run() -> None:
            g1 = self.BASE + "\n[group:g1]\nprograms=worker\n[group:g2]\nprograms=other\n"
            path = _write_config(g1)
            sup = Supervisor()
            sup.load_config(path)
            self.assertIn("worker", sup.groups["g1"])
            self.assertNotIn("worker", sup.groups.get("g2", []))

            # Move worker from g1 to g2.
            g2 = self.BASE + "\n[group:g1]\nprograms=\n[group:g2]\nprograms=worker,other\n"
            with open(path, "w") as f:
                f.write(g2)
            await sup.reload_config()

            self.assertNotIn("worker", sup.groups.get("g1", []))
            self.assertIn("worker", sup.groups["g2"])
            os.remove(path)

        asyncio.run(run())


class TestPidfileSafety(unittest.TestCase):
    """H3: pidfile must only be removed when it holds our own PID."""

    def test_foreign_pidfile_not_removed(self) -> None:
        d = tempfile.mkdtemp()
        pidpath = os.path.join(d, "x.pid")
        with open(pidpath, "w") as f:
            f.write("999999")  # some other process's PID

        sup = Supervisor()
        sup.config.pidfile = pidpath
        sup._pidfile_fd = os.open(pidpath, os.O_WRONLY)
        sup._release_pidfile_lock()

        self.assertTrue(os.path.exists(pidpath), "foreign pidfile must be preserved")
        os.remove(pidpath)

    def test_own_pidfile_removed(self) -> None:
        d = tempfile.mkdtemp()
        pidpath = os.path.join(d, "x.pid")
        with open(pidpath, "w") as f:
            f.write(str(os.getpid()))

        sup = Supervisor()
        sup.config.pidfile = pidpath
        sup._pidfile_fd = os.open(pidpath, os.O_WRONLY)
        sup._release_pidfile_lock()

        self.assertFalse(os.path.exists(pidpath), "our own pidfile must be removed")


if __name__ == "__main__":
    unittest.main()
