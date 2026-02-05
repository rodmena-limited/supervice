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
