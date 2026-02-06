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

    def test_kill_process_group(self) -> None:
        """Test that kill terminates child processes too."""

        async def run() -> None:
            self.event_bus.start()
            # Script that spawns a child process
            cmd = "sh -c 'sleep 60 & sleep 60'"
            config = ProgramConfig(name="test", command=cmd)
            process = Process(config, self.event_bus)

            spawn_task = asyncio.create_task(process.spawn())
            await asyncio.sleep(0.3)  # Let processes start

            self.assertEqual(process.state, RUNNING)
            pid = process.process.pid

            await process.kill()

            self.assertEqual(process.state, STOPPED)

            # Verify process group is gone - attempting to get pgid should fail
            await asyncio.sleep(0.1)  # Small delay for cleanup
            with self.assertRaises(ProcessLookupError):
                os.getpgid(pid)

            try:
                await asyncio.wait_for(spawn_task, timeout=1)
            except asyncio.TimeoutError:
                spawn_task.cancel()

            await self.event_bus.stop()

        asyncio.run(run())

    def test_restart_on_failure(self) -> None:
        """Test that process restarts after failure with autorestart=True."""

        async def run() -> None:
            self.event_bus.start()
            config = ProgramConfig(
                name="test",
                command="sh -c 'exit 1'",
                autorestart=True,
                startretries=3,
                startsecs=0,
            )
            process = Process(config, self.event_bus)
            process.should_run = True

            spawn_count = 0
            original_spawn = process.spawn

            async def counting_spawn() -> None:
                nonlocal spawn_count
                spawn_count += 1
                await original_spawn()

            process.spawn = counting_spawn

            # Run supervision loop briefly - enough for a few retries
            task = asyncio.create_task(process.supervise())
            await asyncio.sleep(2.5)
            process.stop_event.set()

            try:
                await asyncio.wait_for(task, timeout=2)
            except asyncio.TimeoutError:
                task.cancel()

            # Verify multiple spawn attempts occurred (autorestart working)
            self.assertGreaterEqual(spawn_count, 2)
            # Process should be in BACKOFF or EXITED state (retry loop)
            self.assertIn(process.state, (BACKOFF, EXITED, FATAL))

            await self.event_bus.stop()

        asyncio.run(run())
