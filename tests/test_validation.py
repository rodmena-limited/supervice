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

    def test_nonexistent_directory_raises(self) -> None:
        """Test that nonexistent directories raise ConfigValidationError."""
        with self.assertRaises(ConfigValidationError) as ctx:
            _validate_directory("/nonexistent/path/xyz12345", "testprog")
        self.assertIn("does not exist", str(ctx.exception))

    def test_file_instead_of_directory_raises(self) -> None:
        """Test that files (not directories) raise ConfigValidationError."""
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaises(ConfigValidationError) as ctx:
                _validate_directory(f.name, "testprog")
            self.assertIn("not a directory", str(ctx.exception))

class TestNumericValidation(unittest.TestCase):
    """Tests for numeric bounds validation."""

    def test_positive_int_passes(self) -> None:
        """Test that positive integers pass validation."""
        _validate_positive_int(0, "field", "test")
        _validate_positive_int(1, "field", "test")
        _validate_positive_int(100, "field", "test")
