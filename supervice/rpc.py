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
