#!/usr/bin/env python3
"""CLAIM: a spawned child is recorded in the reconciliation state file while
still inside its startsecs window, so a supervisor SIGKILLed during that
window can reconcile the child at the next start.

pdeathsig=false models the platforms (macOS) and configs where reconciliation
is the ONLY orphan cover. reconcile=kill makes the next start's obligation
unambiguous: if the record exists, the orphan dies; if the recording hole
exists, a duplicate runs alongside it.
"""
import os
import signal
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
    proc_alive,
    read_state,
    wait_until,
)

NAME = "startsecs_window_child_is_recorded"

CONFIG = """
[supervice]
pidfile=%(dir)s/p.pid
socket=%(dir)s/p.sock
state_file=%(dir)s/p.state.json
loglevel=INFO

[program:slowstart]
command=sleep 300
autostart=true
autorestart=true
startsecs=8
pdeathsig=false
reconcile=kill
"""


def main() -> None:
    preflight()
    tmpdir = mkdtemp()
    config = CONFIG % {"dir": tmpdir}
    daemon_a = Daemon(tmpdir, config)
    orphan_pid = 0
    try:
        daemon_a.start()
        status = daemon_a.wait_state("slowstart", ("STARTING",), timeout=10)
        if not status or not status.get("pid"):
            probe_error(NAME, "child never observed in STARTING with a pid")
        orphan_pid = status["pid"]
        if not proc_alive(orphan_pid):
            probe_error(NAME, "child pid %d not alive" % orphan_pid)
        info("child %d alive, state STARTING (inside the 8s startsecs window)" % orphan_pid)

        # The claim under test: the record exists during the window.
        records = wait_until(
            lambda: read_state(daemon_a.state_file)
            if os.path.exists(daemon_a.state_file)
            else None,
            timeout=3,
        )
        recorded = [r for r in (records or []) if r["pid"] == orphan_pid]
        if not recorded:
            probe_fail(
                NAME,
                "child %d in startsecs window has NO state-file record "
                "(state file: %s) — a SIGKILL now orphans it invisibly"
                % (orphan_pid, records),
            )
        info("record present for pid %d during the window" % orphan_pid)

        # Prove the consequence end to end: SIGKILL the supervisor mid-window,
        # confirm the child survives (pdeathsig=false), and confirm the next
        # start reconciles it away.
        assert daemon_a.proc is not None
        daemon_a.proc.send_signal(signal.SIGKILL)
        daemon_a.proc.wait(timeout=10)
        if not wait_until(lambda: proc_alive(orphan_pid), timeout=2):
            probe_error(NAME, "child died with the supervisor despite pdeathsig=false")
        info("supervisor SIGKILLed; child %d survived as an orphan" % orphan_pid)

        # Instance B shares A's pidfile/socket/state paths, so it reconciles
        # A's leftovers. Daemon() rewrites the identical config in place.
        daemon_b = Daemon(tmpdir, config)
        try:
            daemon_b.start()
            gone = wait_until(lambda: not proc_alive(orphan_pid), timeout=10)
            if not gone:
                probe_fail(
                    NAME,
                    "next start left orphan %d running (reconcile=kill was "
                    "recorded but not honoured)" % orphan_pid,
                )
            probe_pass(
                NAME,
                "child recorded during startsecs; after SIGKILL the next start "
                "reconciled orphan %d away" % orphan_pid,
            )
        finally:
            daemon_b.cleanup()
    finally:
        if orphan_pid and proc_alive(orphan_pid):
            os.kill(orphan_pid, signal.SIGKILL)
        daemon_a.cleanup()


if __name__ == "__main__":
    main()
