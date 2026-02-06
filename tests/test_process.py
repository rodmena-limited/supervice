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

    def test_spawn_failure_command_not_found(self) -> None:
        """Test spawn with invalid command transitions to FATAL."""

        async def run() -> None:
            self.event_bus.start()
            config = ProgramConfig(name="test", command="nonexistent_cmd_xyz_12345")
            process = Process(config, self.event_bus)

            await process.spawn()

            self.assertEqual(process.state, FATAL)

            await self.event_bus.stop()

        asyncio.run(run())

    def test_kill_process(self) -> None:
        """Test killing a running process transitions to STOPPED."""

        async def run() -> None:
            self.event_bus.start()
            config = ProgramConfig(name="test", command="sleep 60")
            process = Process(config, self.event_bus)

            # Start spawn in background
            spawn_task = asyncio.create_task(process.spawn())
            await asyncio.sleep(0.3)  # Let it start

            self.assertEqual(process.state, RUNNING)

            await process.kill()

            self.assertEqual(process.state, STOPPED)

            # Clean up spawn task
            try:
                await asyncio.wait_for(spawn_task, timeout=1)
            except asyncio.TimeoutError:
                spawn_task.cancel()

            await self.event_bus.stop()

        asyncio.run(run())
