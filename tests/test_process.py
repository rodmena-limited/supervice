import asyncio
import os
import tempfile
import unittest
from supervice.events import EventBus
from supervice.models import ProgramConfig
from supervice.process import (
    BACKOFF,
    EXITED,
    FATAL,
    RUNNING,
    STOPPED,
    Process,
)

class TestProcessLifecycle(unittest.TestCase):
    pass
