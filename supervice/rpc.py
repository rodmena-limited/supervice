import asyncio
import json
import os
import struct
from typing import Any
from supervice.logger import get_logger
HEADER_SIZE = 4  # 4 bytes for message length (uint32, big-endian)
MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB max message size
VALID_COMMANDS = frozenset(
    {
        "status",
        "start",
        "stop",
        "restart",
        "startgroup",
        "stopgroup",
        "reload",
    }
)

class RPCServer:
    def __init__(self, socket_path: str, supervisor: Any):
        self.socket_path = socket_path
        self.supervisor = supervisor
        self.logger = get_logger()
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        # Security: Set umask to create socket with restrictive permissions atomically
        # This prevents the race condition where socket is world-readable briefly
        old_umask = os.umask(0o177)  # Results in mode 0o600
        try:
            self.server = await asyncio.start_unix_server(self.handle_client, self.socket_path)
        finally:
            os.umask(old_umask)

        self.logger.info("RPC Server listening on %s", self.socket_path)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)

    async def _read_message(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read a length-prefixed message from the stream."""
        # Read the 4-byte header
        header = await reader.readexactly(HEADER_SIZE)
        if not header:
            return None

        msg_length = struct.unpack(">I", header)[0]

        if msg_length > MAX_MESSAGE_SIZE:
            msg = "Message too large: %d bytes (max %d)" % (msg_length, MAX_MESSAGE_SIZE)
            raise ValueError(msg)

        if msg_length == 0:
            return b""

        # Read the message body
        return await reader.readexactly(msg_length)

    async def _write_message(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        """Write a length-prefixed message to the stream."""
        header = struct.pack(">I", len(data))
        writer.write(header + data)
        await writer.drain()

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await self._read_message(reader)
            if data is None:
                return

            # Parse JSON with proper error handling
            try:
                request = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as e:
                self.logger.warning("Invalid JSON in RPC request: %s", e)
                response = {
                    "status": "error",
                    "code": "INVALID_JSON",
                    "message": "Invalid JSON: %s" % e,
                }
                await self._write_message(writer, json.dumps(response).encode("utf-8"))
                return

            # Validate request structure
            if not isinstance(request, dict):
                response = {
                    "status": "error",
                    "code": "INVALID_REQUEST",
                    "message": "Request must be a JSON object",
                }
                await self._write_message(writer, json.dumps(response).encode("utf-8"))
                return

            # Validate command
            command = request.get("command")
            if command not in VALID_COMMANDS:
                self.logger.warning("Unknown RPC command: %s", command)
                response = {
                    "status": "error",
                    "code": "UNKNOWN_COMMAND",
                    "message": "Unknown command: %s" % command,
                }
                await self._write_message(writer, json.dumps(response).encode("utf-8"))
                return

            response = await self.process_request(request)
            await self._write_message(writer, json.dumps(response).encode("utf-8"))

        except asyncio.IncompleteReadError:
            # Client disconnected mid-message
            self.logger.debug("Client disconnected during read")
        except Exception as e:
            self.logger.error("RPC Error: %s", e)
            try:
                error_response = {"status": "error", "code": "INTERNAL_ERROR", "message": str(e)}
                await self._write_message(writer, json.dumps(error_response).encode("utf-8"))
            except Exception:
                pass  # Client may have disconnected
        finally:
            writer.close()
            await writer.wait_closed()
