#!/usr/bin/env python3
"""CLAIM: when a script health check times out, the whole check subtree is
killed — no grandchild of the `sh -c` survives.

The check command backgrounds a distinctively-named sleep and waits on it, so
the shell cannot exec-optimise it away. Each timed-out check that kills only
the shell leaks one such sleep, reparented to init, forever.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probelib import (  # noqa: E402
    Daemon,
    info,
    kill_pid_hard,
    mkdtemp,
    pids_with_argv,
    preflight,
    probe_error,
    probe_fail,
    probe_pass,
    wait_until,
)

NAME = "health_script_timeout_leaves_no_children"
MARKER = ["sleep", "437.13"]

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
healthcheck_command=sleep 437.13 & wait
healthcheck_interval=1
healthcheck_timeout=1
healthcheck_retries=3
healthcheck_start_period=0
"""


def main() -> None:
    preflight()
    leftover = pids_with_argv(MARKER)
    if leftover:
        probe_error(NAME, "marker processes already present before the probe: %s" % leftover)

    tmpdir = mkdtemp()
    daemon = Daemon(tmpdir, CONFIG % {"dir": tmpdir})
    seen: set[int] = set()
    try:
        daemon.start()
        if not daemon.wait_state("svc", ("RUNNING", "UNHEALTHY"), timeout=15):
            probe_error(NAME, "svc never reached RUNNING")

        # Known-positive: during check windows the marker grandchild exists,
        # so the counter demonstrably CAN see what it counts.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            seen.update(pids_with_argv(MARKER))
            time.sleep(0.1)
        if not seen:
            probe_error(NAME, "marker grandchild never observed alive — check never ran?")
        info("%d distinct marker grandchildren observed during checks: %s"
             % (len(seen), sorted(seen)))
    finally:
        daemon.cleanup()

    # After the daemon is fully gone, every timed-out check's subtree must be
    # gone too. Give reparenting/reaping a moment to settle.
    time.sleep(2)
    leaked = wait_until(lambda: None if pids_with_argv(MARKER) else True, timeout=3)
    survivors = pids_with_argv(MARKER)
    for pid in survivors:
        kill_pid_hard(pid)
    if not leaked:
        probe_fail(
            NAME,
            "%d health-check grandchild(ren) leaked past daemon shutdown: %s "
            "(killed by the probe)" % (len(survivors), survivors),
        )
    probe_pass(NAME, "every timed-out check's subtree was killed; nothing leaked")


if __name__ == "__main__":
    main()
