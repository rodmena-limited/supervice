import argparse
import asyncio
import json
import struct
import sys
from typing import Any
HEADER_SIZE = 4
MAX_MESSAGE_SIZE = 1024 * 1024
