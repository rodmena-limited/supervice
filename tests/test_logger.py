import logging
import os
import tempfile
import unittest

from supervice.logger import get_logger, setup_logger


class TestLogger(unittest.TestCase):
    def test_get_logger(self):
        logger = get_logger()
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, "supervice")

    def test_setup_logger_stdout(self):
        logger = setup_logger(level="DEBUG")
        self.assertEqual(logger.level, logging.DEBUG)
        self.assertTrue(any(isinstance(h, logging.StreamHandler) for h in logger.handlers))

    def test_setup_logger_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            fname = f.name

        try:
            logger = setup_logger(level="WARN", logfile=fname)
            self.assertEqual(logger.level, logging.WARN)
            self.assertTrue(any(isinstance(h, logging.FileHandler) for h in logger.handlers))

            logger.warn("Test Message")

            # Verify write
            with open(fname) as f:
                content = f.read()
                self.assertIn("Test Message", content)
        finally:
            # close handlers to allow delete
            for h in logger.handlers:
                h.close()
            os.remove(fname)

    def test_invalid_level(self):
        with self.assertRaises(ValueError):
            setup_logger(level="INVALID")
