import asyncio
import os
import tempfile
import unittest
from supervice.events import EventBus
from supervice.models import ProgramConfig
from supervice.process import (
    BACKOFF,
    EXITED,
    FATAL,
    RUNNING,
    STOPPED,
    Process,
)

class TestProcessLifecycle(unittest.TestCase):

    def setUp(self) -> None:
        self.event_bus = EventBus()

    def test_spawn_success(self) -> None:
        """Test successful process spawn and exit."""

        async def run() -> None:
            self.event_bus.start()
            config = ProgramConfig(name="test", command="echo hello")
            process = Process(config, self.event_bus)

            await process.spawn()

            self.assertEqual(process.state, EXITED)
            self.assertIsNotNone(process.process)
            self.assertEqual(process.process.returncode, 0)

            await self.event_bus.stop()

        asyncio.run(run())
