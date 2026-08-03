import io
import logging
import os
import signal
import sys
import tempfile
import unittest
import unittest.mock as mock
from contextlib import redirect_stdout

import supervice.process as proc
from supervice import __version__
from supervice.config import ConfigValidationError, parse_config
from supervice.core import Supervisor
from supervice.process import (
    P_PID,
    PR_SET_PDEATHSIG,
    PROC_PDEATHSIG_CTL,
    _pdeathsig_preexec,
)


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def capture_logs(level: int = logging.WARNING) -> _LogCapture:
    """Attach a capturing handler to the supervice logger; remove it on exit."""
    logger = logging.getLogger("supervice")
    handler = _LogCapture()
    handler.setLevel(level)
    logger.addHandler(handler)
    return handler


def release_logs(handler: _LogCapture) -> None:
    logging.getLogger("supervice").removeHandler(handler)


def write_config(body: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".conf")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    return path


def write_env_file(lines: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines))
    return path


class TestPdeathsigDispatch(unittest.TestCase):
    """§1a: the preexec hook must dispatch per platform and never no-op silently."""

    def test_linux_uses_prctl(self) -> None:
        libc = mock.MagicMock()
        with (
            mock.patch.object(proc, "_LIBC", libc),
            mock.patch.object(proc.sys, "platform", "linux"),
        ):
            _pdeathsig_preexec()
        libc.prctl.assert_called_once_with(PR_SET_PDEATHSIG, int(signal.SIGKILL))

    def test_freebsd_uses_procctl(self) -> None:
        libc = mock.MagicMock()
        with (
            mock.patch.object(proc, "_LIBC", libc),
            mock.patch.object(proc.sys, "platform", "freebsd15.1"),
        ):
            _pdeathsig_preexec()
        libc.procctl.assert_called_once()
        args, _ = libc.procctl.call_args
        self.assertEqual(args[:3], (P_PID, 0, PROC_PDEATHSIG_CTL))
        self.assertIn("cparam 'P'", repr(args[3]))

    def test_no_libc_is_a_noop(self) -> None:
        with mock.patch.object(proc, "_LIBC", None):
            _pdeathsig_preexec()  # must not raise

    def test_supported_flag(self) -> None:
        with mock.patch.object(proc, "_LIBC", mock.MagicMock()):
            self.assertTrue(proc.pdeathsig_supported())
        with mock.patch.object(proc, "_LIBC", None):
            self.assertFalse(proc.pdeathsig_supported())


class TestInactiveDirectiveWarning(unittest.TestCase):
    """§1b: pdeathsig requested on an unsupported platform must warn at load."""

    CONFIG = """
[supervice]
pidfile=

[program:api]
command = sleep 60
pdeathsig = true
"""

    def test_warns_when_pdeathsig_unsupported(self) -> None:
        path = write_config(self.CONFIG)
        try:
            sup = Supervisor()
            handler = capture_logs()
            try:
                with mock.patch.object(proc, "_LIBC", None):
                    sup.load_config(path)
            finally:
                release_logs(handler)
            joined = "\n".join(r.getMessage() for r in handler.records)
            self.assertIn("pdeathsig", joined)
            self.assertIn("unsupported", joined)
        finally:
            os.unlink(path)

    def test_no_warning_when_pdeathsig_disabled(self) -> None:
        path = write_config(self.CONFIG.replace("pdeathsig = true", "pdeathsig = false"))
        try:
            sup = Supervisor()
            handler = capture_logs()
            try:
                with mock.patch.object(proc, "_LIBC", None):
                    sup.load_config(path)
            finally:
                release_logs(handler)
            self.assertFalse(any("pdeathsig" in r.getMessage() for r in handler.records))
        finally:
            os.unlink(path)

    def test_no_warning_when_supported(self) -> None:
        path = write_config(self.CONFIG)
        try:
            sup = Supervisor()
            handler = capture_logs()
            try:
                with mock.patch.object(proc, "_LIBC", mock.MagicMock()):
                    sup.load_config(path)
            finally:
                release_logs(handler)
            self.assertFalse(any("pdeathsig" in r.getMessage() for r in handler.records))
        finally:
            os.unlink(path)


class TestEnvFile(unittest.TestCase):
    """§2: env_file parsing, precedence, and hard-error semantics."""

    def _config(self, env_file: str, environment: str = "") -> str:
        return (
            "[supervice]\npidfile=\n\n"
            "[program:api]\ncommand = sleep 60\nenv_file = %s\n"
            "environment = %s\n" % (env_file, environment)
        )

    def test_basic_parse_and_comments(self) -> None:
        env_path = write_env_file(
            ["# a comment", "", "SECRET=topsecret", 'QUOTED="hello world"', "EMPTY="]
        )
        try:
            cfg_path = write_config(self._config(env_path))
            try:
                cfg = parse_config(cfg_path)
                prog = cfg.programs[0]
                self.assertEqual(prog.environment["SECRET"], "topsecret")
                self.assertEqual(prog.environment["QUOTED"], "hello world")
                self.assertEqual(prog.environment["EMPTY"], "")
                self.assertEqual(prog.env_file, [env_path])
            finally:
                os.unlink(cfg_path)
        finally:
            os.unlink(env_path)

    def test_later_file_wins_over_earlier(self) -> None:
        first = write_env_file(["SHARED=first", "ONLY1=one"])
        second = write_env_file(["SHARED=second", "ONLY2=two"])
        try:
            cfg_path = write_config(self._config("%s, %s" % (first, second)))
            try:
                prog = parse_config(cfg_path).programs[0]
                self.assertEqual(prog.environment["SHARED"], "second")
                self.assertEqual(prog.environment["ONLY1"], "one")
                self.assertEqual(prog.environment["ONLY2"], "two")
            finally:
                os.unlink(cfg_path)
        finally:
            os.unlink(first)
            os.unlink(second)

    def test_environment_overrides_env_file(self) -> None:
        env_path = write_env_file(["SECRET=from_file"])
        try:
            cfg_path = write_config(self._config(env_path, "SECRET=from_inline"))
            try:
                prog = parse_config(cfg_path).programs[0]
                self.assertEqual(prog.environment["SECRET"], "from_inline")
            finally:
                os.unlink(cfg_path)
        finally:
            os.unlink(env_path)

    def test_missing_file_is_hard_error(self) -> None:
        cfg_path = write_config(self._config("/nonexistent/nope.env"))
        try:
            with self.assertRaises(ConfigValidationError) as cm:
                parse_config(cfg_path)
            self.assertIn("does not exist", str(cm.exception))
        finally:
            os.unlink(cfg_path)

    def test_malformed_line_is_hard_error(self) -> None:
        env_path = write_env_file(["SECRET=ok", "this line has no equals"])
        try:
            cfg_path = write_config(self._config(env_path))
            try:
                with self.assertRaises(ConfigValidationError) as cm:
                    parse_config(cfg_path)
                self.assertIn("malformed env line", str(cm.exception))
            finally:
                os.unlink(cfg_path)
        finally:
            os.unlink(env_path)

    def test_env_file_reaches_child_process(self) -> None:
        env_path = write_env_file(["MY_SECRET=from_file"])
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_file = os.path.join(tmpdir, "out.txt")
                cfg_path = write_config(
                    "[supervice]\npidfile=\n\n"
                    "[program:api]\ncommand = sh -c 'echo $MY_SECRET > %s'\n"
                    "env_file = %s\n" % (out_file, env_path)
                )
                try:
                    cfg = parse_config(cfg_path)
                    prog = cfg.programs[0]
                    self.assertEqual(prog.environment["MY_SECRET"], "from_file")
                finally:
                    os.unlink(cfg_path)
        finally:
            os.unlink(env_path)


class TestPidfileNone(unittest.TestCase):
    """§4: pidfile=none disables the pidfile; parent dirs are validated at load."""

    def test_pidfile_none_means_no_pidfile(self) -> None:
        path = write_config("[supervice]\npidfile = none\n")
        try:
            cfg = parse_config(path)
            self.assertEqual(cfg.pidfile, "")
        finally:
            os.unlink(path)

    def test_pidfile_empty_means_no_pidfile(self) -> None:
        path = write_config("[supervice]\npidfile =\n")
        try:
            cfg = parse_config(path)
            self.assertEqual(cfg.pidfile, "")
        finally:
            os.unlink(path)

    def test_pidfile_parent_missing_fails_at_load(self) -> None:
        path = write_config("[supervice]\npidfile = /nonexistent_dir_xyz/supervice.pid\n")
        try:
            with self.assertRaises(ConfigValidationError) as cm:
                parse_config(path)
            self.assertIn("pidfile directory", str(cm.exception))
        finally:
            os.unlink(path)

    def test_socket_parent_missing_fails_at_load(self) -> None:
        path = write_config("[supervice]\npidfile =\nsocket = /nonexistent_dir_xyz/s.sock\n")
        try:
            with self.assertRaises(ConfigValidationError) as cm:
                parse_config(path)
            self.assertIn("socket directory", str(cm.exception))
        finally:
            os.unlink(path)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory write permission checks")
    def test_pidfile_parent_not_writable_fails_at_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ro_dir = os.path.join(tmpdir, "ro")
            os.mkdir(ro_dir)
            os.chmod(ro_dir, 0o555)
            path = write_config("[supervice]\npidfile = %s/supervice.pid\n" % ro_dir)
            try:
                with self.assertRaises(ConfigValidationError) as cm:
                    parse_config(path)
                self.assertIn("not writable", str(cm.exception))
            finally:
                os.unlink(path)
                os.chmod(ro_dir, 0o755)


class TestVersionFlag(unittest.TestCase):
    """§5: supervice --version and supervicectl --version print the version."""

    def test_supervice_version(self) -> None:
        from supervice.main import main

        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["supervice", "--version"]
        try:
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    main()
        finally:
            sys.argv = old_argv
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, buf.getvalue())

    def test_supervicectl_version(self) -> None:
        from supervice.client import main

        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["supervicectl", "--version"]
        try:
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    main()
        finally:
            sys.argv = old_argv
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
