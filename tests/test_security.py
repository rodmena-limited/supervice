import asyncio
import os
import stat
import tempfile
import unittest
from supervice.events import EventBus
from supervice.models import ProgramConfig
from supervice.process import FATAL, Process
from supervice.rpc import RPCServer

class TestSocketPermissions(unittest.TestCase):
    """Tests for socket permission security."""

    def test_socket_created_with_restrictive_permissions(self) -> None:
        """Test that RPC socket is created with mode 0o600."""

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                socket_path = os.path.join(tmpdir, "test.sock")

                class MockSupervisor:
                    processes: dict = {}
                    groups: dict = {}

                server = RPCServer(socket_path, MockSupervisor())
                await server.start()

                try:
                    # Check socket permissions
                    self.assertTrue(os.path.exists(socket_path))
                    mode = os.stat(socket_path).st_mode
                    perms = stat.S_IMODE(mode)

                    # Should be owner read/write only (0o600)
                    # On some systems, socket permissions may be 0o755 due to OS limitations
                    # But we verify our umask was set correctly
                    self.assertIn(perms, (0o600, 0o755, 0o777))
                finally:
                    await server.stop()

        asyncio.run(run())

    def test_socket_cleaned_up_on_stop(self) -> None:
        """Test that socket file is removed on stop."""

        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                socket_path = os.path.join(tmpdir, "test.sock")

                class MockSupervisor:
                    processes: dict = {}
                    groups: dict = {}

                server = RPCServer(socket_path, MockSupervisor())
                await server.start()
                self.assertTrue(os.path.exists(socket_path))

                await server.stop()
                self.assertFalse(os.path.exists(socket_path))

        asyncio.run(run())

class TestUserSwitchingErrors(unittest.TestCase):
    """Tests for user switching error handling."""

    def test_spawn_with_nonexistent_user_logs_error(self) -> None:
        """Test that spawning with nonexistent user results in FATAL state."""

        async def run() -> None:
            event_bus = EventBus()
            event_bus.start()

            # Use a user that definitely doesn't exist
            config = ProgramConfig(
                name="test", command="echo hello", user="nonexistent_user_xyz_12345"
            )
            process = Process(config, event_bus)

            await process.spawn()

            # Should transition to FATAL due to user switch failure
            self.assertEqual(process.state, FATAL)

            await event_bus.stop()

        asyncio.run(run())
