#!/usr/bin/env python3
"""CLAIM: a second supervice instance that fails startup (pidfile held by a
live sibling) leaves the sibling's reconciliation state file untouched.

If it wipes the file, the live sibling's crash-recovery memory is destroyed:
a later SIGKILL of the sibling then orphans children that the next start can
no longer find. The wipe is silent — an empty state file looks exactly like a
clean shutdown.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probelib import (  # noqa: E402
    PYTHON,
    REPO,
    Daemon,
    info,
    mkdtemp,
    preflight,
    probe_error,
    probe_fail,
    probe_pass,
    read_state,
    wait_until,
)

NAME = "failed_start_must_not_clobber_state"

CONFIG = """
[supervice]
pidfile=%(dir)s/p.pid
socket=%(dir)s/p.sock
state_file=%(dir)s/p.state.json
loglevel=INFO

[program:keeper]
command=sleep 300
autostart=true
autorestart=true
startsecs=0
"""


def main() -> None:
    preflight()
    tmpdir = mkdtemp()
    daemon = Daemon(tmpdir, CONFIG % {"dir": tmpdir})
    try:
        daemon.start()
        if not daemon.wait_state("keeper", ("RUNNING",), timeout=15):
            probe_error(NAME, "keeper never reached RUNNING under instance A")

        # Known-positive: the state file records the child, and our reader sees it.
        records = wait_until(
            lambda: (lambda r: r if r else None)(
                read_state(daemon.state_file) if os.path.exists(daemon.state_file) else []
            ),
            timeout=10,
        )
        if not records:
            probe_error(NAME, "state file never recorded the RUNNING child")
        before = json.dumps(records, sort_keys=True)
        info("state file before: %d record(s), pid %s" % (len(records), records[0]["pid"]))

        # The experiment: a second instance on the same config must fail AND
        # must not touch the state file.
        second = subprocess.run(
            [PYTHON, "-m", "supervice.main", "-c", daemon.config_path, "-n"],
            cwd=tmpdir,
            env={**os.environ, "PYTHONPATH": REPO},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if second.returncode == 0:
            probe_error(NAME, "second instance unexpectedly started (duplicate daemon!)")
        info("second instance refused as expected (rc=%d)" % second.returncode)

        after_records = read_state(daemon.state_file)
        after = json.dumps(after_records, sort_keys=True)
        if after != before:
            probe_fail(
                NAME,
                "failed second start rewrote the state file: before=%s after=%s"
                % (before, after),
            )
        probe_pass(NAME, "state file unchanged across the failed start (%s)" % before)
    finally:
        daemon.cleanup()


if __name__ == "__main__":
    main()
