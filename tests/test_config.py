import os
import tempfile
import unittest
from supervice.config import _parse_bool, _parse_env, parse_config
from supervice.models import ProgramConfig, SupervisorConfig

class TestModels(unittest.TestCase):

    def test_program_config_defaults(self):
        pc = ProgramConfig(name="test", command="echo")
        self.assertEqual(pc.name, "test")
        self.assertEqual(pc.command, "echo")
        self.assertEqual(pc.numprocs, 1)
        self.assertTrue(pc.autostart)
        self.assertIsNone(pc.group)

    def test_supervisor_config_defaults(self):
        sc = SupervisorConfig()
        self.assertEqual(sc.logfile, "supervice.log")
        self.assertEqual(sc.loglevel, "INFO")
        self.assertEqual(sc.programs, [])

class TestConfigParsing(unittest.TestCase):

    def test_parse_bool(self):
        self.assertTrue(_parse_bool("true"))
        self.assertTrue(_parse_bool("True"))
        self.assertTrue(_parse_bool("1"))
        self.assertTrue(_parse_bool("yes"))
        self.assertTrue(_parse_bool("on"))
        self.assertFalse(_parse_bool("false"))
        self.assertFalse(_parse_bool("0"))
        self.assertFalse(_parse_bool("no"))
        self.assertFalse(_parse_bool("off"))
        self.assertFalse(_parse_bool("random"))

    def test_parse_env(self):
        env_str = 'KEY1=val1,KEY2="val2", KEY3 = val3 '
        env = _parse_env(env_str)
        self.assertEqual(env["KEY1"], "val1")
        self.assertEqual(env["KEY2"], "val2")
        self.assertEqual(env["KEY3"], "val3")

        self.assertEqual(_parse_env(""), {})

    def test_parse_config_file(self):
        config_content = """
    [supervice]
    loglevel=DEBUG
    logfile=test.log

    [program:prog1]
    command=sleep 1
    numprocs=2
    environment=FOO=bar

    [group:g1]
    programs=prog1
    """
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(config_content)
            fname = f.name

        try:
            config = parse_config(fname)

            self.assertEqual(config.loglevel, "DEBUG")
            self.assertEqual(config.logfile, "test.log")
            self.assertEqual(len(config.programs), 1)

            prog = config.programs[0]
            self.assertEqual(prog.name, "prog1")
            self.assertEqual(prog.command, "sleep 1")
            self.assertEqual(prog.numprocs, 2)
            self.assertEqual(prog.environment["FOO"], "bar")
            self.assertEqual(prog.group, "g1")

        finally:
            os.remove(fname)

    def test_parse_config_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            parse_config("nonexistent_file.conf")
