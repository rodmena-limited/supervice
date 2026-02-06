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

    def test_invalid_signal_raises(self) -> None:
        """Test that invalid signal names raise ConfigValidationError."""
        with self.assertRaises(ConfigValidationError) as ctx:
            _validate_signal("INVALID", "testprog")
        self.assertIn("testprog", str(ctx.exception))
        self.assertIn("INVALID", str(ctx.exception))

    def test_signal_with_sig_prefix(self) -> None:
        """Test that signals with SIG prefix are handled."""
        # SIGTERM should work (strips SIG prefix)
        _validate_signal("SIGTERM", "test")

class TestDirectoryValidation(unittest.TestCase):
    """Tests for directory validation."""

    def test_existing_directory_passes(self) -> None:
        """Test that existing directories pass validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Should not raise
            _validate_directory(tmpdir, "test")
