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
