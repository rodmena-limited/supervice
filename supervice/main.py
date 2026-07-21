import argparse
import asyncio
import os
import sys

from supervice.core import Supervisor
from supervice.logger import setup_logger

DEFAULT_LOGFILE = "supervice.log"


def _daemonize() -> None:
    pid = os.fork()
    if pid > 0:
        os._exit(0)

    os.setsid()

    pid = os.fork()
    if pid > 0:
        os._exit(0)

    sys.stdin.close()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)


def main() -> None:
    parser = argparse.ArgumentParser(description="Supervice: A modern process supervisor")
    parser.add_argument(
        "-c",
        "--configuration",
        help="Configuration file path",
        default="supervisord.conf",
    )
    parser.add_argument(
        "-n",
        "--nodaemon",
        action="store_true",
        help="Run in the foreground (default: daemonize)",
    )
    parser.add_argument("-l", "--logfile", help="Log file path")
    parser.add_argument("-e", "--loglevel", help="Log level", default=None)

    args = parser.parse_args()

    # Bootstrap logger so any load_config errors are visible on stderr/stdout.
    setup_logger(level=args.loglevel or "INFO", logfile=args.logfile)

    supervisor = Supervisor()

    try:
        supervisor.load_config(args.configuration)
    except Exception as e:
        sys.stderr.write("Failed to load config '%s': %s\n" % (args.configuration, e))
        sys.exit(1)

    # Configure the logger exactly once, now that config is loaded. CLI flags
    # take precedence over config values. When daemonizing we always need a
    # logfile (stdout is closed), so fall back to a default.
    loglevel = args.loglevel or supervisor.config.loglevel
    logfile = args.logfile or supervisor.config.logfile
    if not args.nodaemon and not logfile:
        logfile = DEFAULT_LOGFILE
        sys.stderr.write("Warning: no logfile configured, using '%s'\n" % logfile)
    setup_logger(
        level=loglevel,
        logfile=logfile,
        maxbytes=supervisor.config.log_maxbytes,
        backups=supervisor.config.log_backups,
    )

    if not args.nodaemon:
        _daemonize()

    try:
        asyncio.run(supervisor.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        sys.stderr.write("Supervice crashed: %s\n" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
