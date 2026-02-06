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

    def test_negative_int_raises(self) -> None:
        """Test that negative integers raise ConfigValidationError."""
        with self.assertRaises(ConfigValidationError) as ctx:
            _validate_positive_int(-1, "numprocs", "testprog")
        self.assertIn("must be non-negative", str(ctx.exception))

class TestConfigValidation(unittest.TestCase):
    """Integration tests for config validation."""

    def test_invalid_signal_in_config(self) -> None:
        """Test that invalid stopsignal in config raises error."""
        config_content = """
    [program:test]
    command=echo hello
    stopsignal=INVALID
    """
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write(config_content)
            fname = f.name

        try:
            with self.assertRaises(ConfigValidationError) as ctx:
                parse_config(fname)
            self.assertIn("stopsignal", str(ctx.exception).lower())
        finally:
            os.remove(fname)

    def test_invalid_loglevel_raises(self) -> None:
        """Test that invalid loglevel raises ConfigValidationError."""
        config_content = """
    [supervice]
    loglevel=INVALID
    """
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write(config_content)
            fname = f.name

        try:
            with self.assertRaises(ConfigValidationError) as ctx:
                parse_config(fname)
            self.assertIn("loglevel", str(ctx.exception).lower())
        finally:
            os.remove(fname)

    def test_zero_numprocs_raises(self) -> None:
        """Test that numprocs=0 raises ConfigValidationError."""
        config_content = """
    [program:test]
    command=echo hello
    numprocs=0
    """
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write(config_content)
            fname = f.name

        try:
            with self.assertRaises(ConfigValidationError) as ctx:
                parse_config(fname)
            self.assertIn("numprocs", str(ctx.exception))
        finally:
            os.remove(fname)

    def test_valid_config_with_health_checks(self) -> None:
        """Test that valid config with health checks parses correctly."""
        config_content = """
    [supervice]
    loglevel=INFO
    socket=/tmp/test.sock
    shutdown_timeout=30

    [program:webserver]
    command=python -m http.server 8080
    healthcheck_type=tcp
    healthcheck_port=8080
    healthcheck_interval=10
    healthcheck_timeout=5
    healthcheck_retries=3
    """
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".conf") as f:
            f.write(config_content)
            fname = f.name

        try:
            config = parse_config(fname)
            self.assertEqual(config.socket_path, "/tmp/test.sock")
            self.assertEqual(config.shutdown_timeout, 30)
            self.assertEqual(len(config.programs), 1)

            prog = config.programs[0]
            self.assertEqual(prog.healthcheck.port, 8080)
            self.assertEqual(prog.healthcheck.interval, 10)
        finally:
            os.remove(fname)
