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
