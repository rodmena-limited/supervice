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
