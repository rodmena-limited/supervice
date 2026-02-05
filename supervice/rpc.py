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
