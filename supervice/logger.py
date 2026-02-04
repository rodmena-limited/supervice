import logging
import sys
from logging.handlers import RotatingFileHandler

# Default log rotation settings
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50MB
DEFAULT_BACKUP_COUNT = 10


def setup_logger(
    level: str = "INFO",
    logfile: str | None = None,
    maxbytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """
    Configures and returns the root logger with optional log rotation.

    Args:
        level: Logging level (DEBUG, INFO, WARN, ERROR, CRITICAL).
        logfile: Path to log file. If None, logs to stdout.
        maxbytes: Maximum size of log file before rotation (default 50MB).
        backups: Number of backup files to keep (default 10).

    Returns:
        Configured logger instance.
    """
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError("Invalid log level: %s" % level)

    logger = logging.getLogger("supervice")
    logger.setLevel(numeric_level)

    # clear existing handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    if logfile:
        # Use RotatingFileHandler for log rotation
        file_handler: logging.Handler
        if maxbytes > 0:
            file_handler = RotatingFileHandler(logfile, maxBytes=maxbytes, backupCount=backups)
        else:
            # No rotation if maxbytes is 0
            file_handler = logging.FileHandler(logfile)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    else:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def get_logger() -> logging.Logger:
    """Returns the global logger instance."""
    return logging.getLogger("supervice")
