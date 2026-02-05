import asyncio
import ctypes
import os
import shlex
import signal
import sys
from asyncio import subprocess
from typing import IO, Any
from supervice.events import Event, EventBus, EventType
from supervice.health import HealthChecker, create_health_checker
from supervice.logger import get_logger
from supervice.models import HealthCheckType, ProgramConfig
STOPPED = "STOPPED"
STARTING = "STARTING"
RUNNING = "RUNNING"
