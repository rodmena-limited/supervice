import argparse
import asyncio
import json
import struct
import sys
from typing import Any
HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1024 * 1024

def _format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)

class Controller:
    def __init__(self, socket_path: str = "/tmp/supervice.sock"):
        self.socket_path = socket_path

    async def _read_message(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read a length-prefixed message from the stream."""
        header = await reader.readexactly(HEADER_SIZE)
        if not header:
            return None

        msg_length = struct.unpack(">I", header)[0]

        if msg_length > MAX_MESSAGE_SIZE:
            raise ValueError("Message too large: %d bytes" % msg_length)

        if msg_length == 0:
            return b""

        return await reader.readexactly(msg_length)

    async def _write_message(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        """Write a length-prefixed message to the stream."""
        header = struct.pack(">I", len(data))
        writer.write(header + data)
        await writer.drain()
