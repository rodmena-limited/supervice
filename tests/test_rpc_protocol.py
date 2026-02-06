import asyncio
import json
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock
from supervice.rpc import HEADER_SIZE, MAX_MESSAGE_SIZE, RPCServer

class TestLengthPrefixedProtocol(unittest.TestCase):
    """Tests for length-prefixed message protocol."""

    def test_read_message_success(self) -> None:
        """Test reading a valid length-prefixed message."""

        async def run() -> None:
            message = b'{"command": "status"}'
            header = struct.pack(">I", len(message))

            reader = MagicMock()
            reader.readexactly = AsyncMock(side_effect=[header, message])

            server = RPCServer("sock", MagicMock())
            result = await server._read_message(reader)

            self.assertEqual(result, message)

        asyncio.run(run())

    def test_read_message_too_large(self) -> None:
        """Test that oversized messages are rejected."""

        async def run() -> None:
            # Create a header claiming message is larger than MAX_MESSAGE_SIZE
            header = struct.pack(">I", MAX_MESSAGE_SIZE + 1)

            reader = MagicMock()
            reader.readexactly = AsyncMock(return_value=header)

            server = RPCServer("sock", MagicMock())
            with self.assertRaises(ValueError) as ctx:
                await server._read_message(reader)

            self.assertIn("too large", str(ctx.exception).lower())

        asyncio.run(run())

    def test_write_message_format(self) -> None:
        """Test that messages are written with correct header."""

        async def run() -> None:
            writer = MagicMock()
            writer.drain = AsyncMock()

            server = RPCServer("sock", MagicMock())
            message = b'{"status": "ok"}'
            await server._write_message(writer, message)

            # Check that write was called with header + message
            call_args = writer.write.call_args[0][0]
            expected_header = struct.pack(">I", len(message))
            self.assertEqual(call_args[:HEADER_SIZE], expected_header)
            self.assertEqual(call_args[HEADER_SIZE:], message)

        asyncio.run(run())

class TestRPCValidation(unittest.TestCase):
    """Tests for RPC request validation."""

    def test_unknown_command_rejected(self) -> None:
        """Test that unknown commands are rejected with proper error."""

        async def run() -> None:
            supervisor = MagicMock()
            supervisor.processes = {}

            server = RPCServer("sock", supervisor)
            result = await server.process_request({"command": "invalid_xyz"})

            self.assertEqual(result["status"], "error")

        asyncio.run(run())
