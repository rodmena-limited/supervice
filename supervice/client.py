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
