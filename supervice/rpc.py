import asyncio
import json
import os
import struct
from typing import Any
from supervice.logger import get_logger
HEADER_SIZE = 4  # 4 bytes for message length (uint32, big-endian)
MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB max message size
