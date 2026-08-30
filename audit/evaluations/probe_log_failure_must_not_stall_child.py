#!/usr/bin/env python3
"""CLAIM: if the child-log sink breaks (log directory removed, rotation
fails), supervice keeps draining the child's stdout pipe, so the child never
blocks on a full pipe.

The child writes 8 KB bursts to stdout and heartbeats to a file after every
burst. If the supervisor stops reading, the 64 KB pipe fills within ~1 s and
the child freezes mid-write — the heartbeat stops. A logging problem must
degrade logging, never the service.
"""
import os
import shutil
import sys
import time

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

NAME = "log_failure_must_not_stall_child"

CHILD = """\
import os, sys, time
hb = sys.argv[1]
burst = b"x" * 8192 + b"\\n"
while True:
    os.write(1, burst)
    with open(hb, "a") as f:
        f.write("%.3f\\n" % time.time())
    time.sleep(0.05)
"""

CONFIG = """
[supervice]
pidfile=%(dir)s/p.pid
socket=%(dir)s/p.sock
state_file=%(dir)s/p.state.json
loglevel=INFO

[program:chatty]
command=%(python)s %(dir)s/child.py %(dir)s/heartbeat
autostart=true
autorestart=false
startsecs=0
stdout_logfile=%(dir)s/logs/out.log
stdout_logfile_maxbytes=65536
stdout_logfile_backups=1
"""


def heartbeat_count(path: str) -> int:
    try:
        with open(path) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def main() -> None:
    preflight()
    tmpdir = mkdtemp()
    logs = os.path.join(tmpdir, "logs")
    os.makedirs(logs)
    with open(os.path.join(tmpdir, "child.py"), "w") as f:
        f.write(CHILD)
    hb = os.path.join(tmpdir, "heartbeat")
    daemon = Daemon(
        tmpdir, CONFIG % {"dir": tmpdir, "python": sys.executable}
    )
    try:
        daemon.start()
        if not daemon.wait_state("chatty", ("RUNNING",), timeout=15):
            probe_error(NAME, "chatty never reached RUNNING")

        # Known-positive 1: the heartbeat advances while logging works.
        base = wait_until(lambda: heartbeat_count(hb) or None, timeout=10)
        if not base:
            probe_error(NAME, "heartbeat never started")
        # Known-positive 2: rotation is genuinely exercised before the break.
        if not wait_until(lambda: os.path.exists(os.path.join(logs, "out.log.1")), timeout=15):
            probe_error(NAME, "rotation never happened; the broken path would not be reached")
        info("heartbeat live, rotation observed; removing the log directory")

        shutil.rmtree(logs)

        # Let the failure land (next rotation attempt), then measure whether
        # the child is still making progress.
        time.sleep(3)
        count_a = heartbeat_count(hb)
        time.sleep(3)
        count_b = heartbeat_count(hb)
        gained = count_b - count_a
        status = daemon.status_of("chatty")
        if gained < 10:
            probe_fail(
                NAME,
                "child stalled after log dir removal: %d heartbeats in 3s "
                "(pipe full, child blocked in write; state=%s)"
                % (gained, status and status["state"]),
            )
        probe_pass(
            NAME,
            "child kept running after log sink broke: %d heartbeats in 3s (state=%s)"
            % (gained, status and status["state"]),
        )
    finally:
        daemon.cleanup()


if __name__ == "__main__":
    main()
