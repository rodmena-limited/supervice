#!/usr/bin/env python3
"""CLAIM: once a process is stopped, `status` no longer reports the health
flag of the previous run. A STOPPED process showing HEALTH OK invites an
operator to trust a service that is not there at all.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probelib import (  # noqa: E402
    Daemon,
    info,
    mkdtemp,
    preflight,
    probe_error,
    probe_fail,
    probe_pass,
    wait_until,
)

NAME = "status_health_not_stale_after_stop"

CONFIG = """
[supervice]
pidfile=%(dir)s/p.pid
socket=%(dir)s/p.sock
state_file=%(dir)s/p.state.json
loglevel=INFO

[program:svc]
command=sleep 300
autostart=true
autorestart=false
startsecs=0
healthcheck_type=script
healthcheck_command=true
healthcheck_interval=1
healthcheck_timeout=5
healthcheck_retries=1
healthcheck_start_period=0
"""


def main() -> None:
    preflight()
    tmpdir = mkdtemp()
    daemon = Daemon(tmpdir, CONFIG % {"dir": tmpdir})
    try:
        daemon.start()
        if not daemon.wait_state("svc", ("RUNNING",), timeout=15):
            probe_error(NAME, "svc never reached RUNNING")

        # Known-positive: the health flag is reported True while running.
        def healthy_reported():
            proc = daemon.status_of("svc")
            return proc if proc and proc.get("healthy") is True else None

        if not wait_until(healthy_reported, timeout=15):
            probe_error(NAME, "health flag never reported True while running")
        info("health OK reported while running (flag proven visible)")

        result = daemon.ctl("stop", "svc")
        if result.returncode != 0:
            probe_error(NAME, "stop failed: %r" % (result.stdout + result.stderr))

        proc = daemon.status_of("svc")
        if proc is None:
            probe_error(NAME, "svc vanished from status after stop")
        if "healthy" in proc:
            probe_fail(
                NAME,
                "stopped process still carries the previous run's health flag: %r" % proc,
            )
        probe_pass(NAME, "stopped process reports no health flag: %r" % proc)
    finally:
        daemon.cleanup()


if __name__ == "__main__":
    main()
