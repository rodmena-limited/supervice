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
    pass
