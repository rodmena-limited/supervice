import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock
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
