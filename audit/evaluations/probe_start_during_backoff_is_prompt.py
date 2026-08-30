#!/usr/bin/env python3
"""CLAIM: an explicit `supervicectl start` issued while the program sits in
BACKOFF begins the spawn promptly (< 3 s), instead of silently waiting out
the residual backoff delay (up to 30 s) and answering with a wrong state.

Sequence: a crash-looping program climbs the backoff ladder; the moment a
backoff wait of >= 4 s begins, the program on disk is repaired and `start` is
issued. The daemon knows the operator asked — the only question is when it
acts.
"""
import os
import re
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

NAME = "start_during_backoff_is_prompt"

CONFIG = """
[supervice]
pidfile=%(dir)s/p.pid
socket=%(dir)s/p.sock
state_file=%(dir)s/p.state.json
loglevel=INFO

[program:flappy]
command=/bin/sh %(dir)s/prog.sh
autostart=true
autorestart=true
startsecs=1
startretries=12
"""

BACKOFF_RE = re.compile(r"Backoff flappy: waiting (\d+)s")


def main() -> None:
    preflight()
    tmpdir = mkdtemp()
    prog = os.path.join(tmpdir, "prog.sh")
    with open(prog, "w") as f:
        f.write("exit 1\n")
    daemon = Daemon(tmpdir, CONFIG % {"dir": tmpdir})
    try:
        daemon.start()

        # Known-positive: the backoff ladder is climbing and our log parser
        # sees it (>=2 distinct delays proves both).
        def backoff_delays() -> list[int]:
            return [int(m.group(1)) for m in BACKOFF_RE.finditer(daemon.log_text())]

        if not wait_until(lambda: len(set(backoff_delays())) >= 2, timeout=20):
            probe_error(NAME, "backoff ladder never observed climbing: %s" % backoff_delays())

        # Wait for the START of a backoff wait of >= 4s: the moment its log
        # line appears, ~4s of residual delay lie ahead.
        seen = len(backoff_delays())

        def long_wait_begun():
            delays = backoff_delays()
            if len(delays) > seen - 1 and delays and delays[-1] >= 4:
                return delays[-1]
            return None

        residual = wait_until(long_wait_begun, timeout=45, interval=0.05)
        if not residual:
            probe_error(NAME, "never entered a backoff wait >= 4s: %s" % backoff_delays())
        info("in a %ds backoff wait; repairing the program and issuing start" % residual)

        with open(prog, "w") as f:
            f.write("exec sleep 300\n")

        t0 = time.monotonic()
        result = daemon.ctl("start", "flappy", timeout=30)
        running = daemon.wait_state("flappy", ("RUNNING",), timeout=residual + 5)
        elapsed = time.monotonic() - t0

        if not running:
            probe_fail(NAME, "flappy never reached RUNNING after explicit start")
        if elapsed >= 3.0 or result.returncode != 0:
            probe_fail(
                NAME,
                "explicit start took %.1fs to reach RUNNING (ctl rc=%d, said %r) — "
                "the residual backoff delay was served to the operator"
                % (elapsed, result.returncode, result.stdout.strip()),
            )
        probe_pass(
            NAME,
            "explicit start during a %ds backoff reached RUNNING in %.1fs (ctl: %r)"
            % (residual, elapsed, result.stdout.strip()),
        )
    finally:
        daemon.cleanup()


if __name__ == "__main__":
    main()
