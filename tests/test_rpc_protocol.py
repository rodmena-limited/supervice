import asyncio
import json
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock
from supervice.rpc import HEADER_SIZE, MAX_MESSAGE_SIZE, RPCServer

class TestLengthPrefixedProtocol(unittest.TestCase):
    """Tests for length-prefixed message protocol."""
