import asyncio
import os
import stat
import tempfile
import unittest
from supervice.events import EventBus
from supervice.models import ProgramConfig
from supervice.process import FATAL, Process
from supervice.rpc import RPCServer

class TestSocketPermissions(unittest.TestCase):
    """Tests for socket permission security."""
