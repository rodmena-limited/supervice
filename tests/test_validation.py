import os
import tempfile
import unittest
from supervice.config import (
    ConfigValidationError,
    _validate_directory,
    _validate_positive_int,
    _validate_signal,
    parse_config,
)

class TestSignalValidation(unittest.TestCase):
    """Tests for signal name validation."""

    def test_valid_signals(self) -> None:
        """Test that valid signal names pass validation."""
        valid_signals = ["TERM", "KILL", "HUP", "INT", "QUIT", "USR1", "USR2"]
        for sig in valid_signals:
            # Should not raise
            _validate_signal(sig, "test")
