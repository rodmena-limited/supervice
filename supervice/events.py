import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any
from supervice.logger import get_logger
MAX_EVENT_QUEUE_SIZE = 1000
EventHandler = Callable[[Event], Awaitable[None]]
