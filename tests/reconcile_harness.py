#!/usr/bin/env python3
"""End-to-end reconciliation harness: crash the supervisor, restart, count children.

    python3 tests/reconcile_harness.py

Reproduces the duplicate-spawn bug macbook-admin-bd8e86 measured on Darwin
(4 orphans before a restart, 5 after) and shows whether reconciliation fixes it.

WHY THIS RUNS ON LINUX TOO
--------------------------
The bug is a macOS symptom, but it is not a macOS bug — it appears wherever
pdeathsig does not reach, which includes every grandchild on every platform.
Setting `pdeathsig = false` reproduces the exact macOS condition on Linux and
FreeBSD: the child is orphaned by an abrupt supervisor kill. That makes the fix
testable in CI on all three platforms rather than only on the one machine that
exhibits it naturally.

ARMS
----
  R1  reconcile=off    orphan survives the restart AND a duplicate is spawned
                       -> this is the BUG, reproduced. Must show 2 live children.
  R2  reconcile=kill   orphan is killed at startup, one child afterwards
                       -> this is the FIX. Must show 1.
  R3  reconcile=auto   with pdeathsig=false: orphan deliberately left alive, but
       + pdeathsig off the supervisor must WARN about the duplicate rather than
                       silently create it. Must show 2 AND a warning.

R1 is not a failing test — it is the control. Without it, R2 showing "1 child"
could equally mean the harness never managed to orphan anything.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STARTUP_TIMEOUT = 15.0
SILENT_CHILD = "import time\nwhile True: time.sleep(3600)\n"


def _live_pids(marker: str) -> list[int]:
    """Every live process carrying our unique marker, via ps.

    Counted rather than inferred: the whole question is how many children exist,
    so asking the OS is the only answer worth having.
    """
    try:
        # -A, not -e: on FreeBSD `-e` means "show the environment", NOT "all
        # processes", so `ps -eo pid=,command=` returned 18 getty lines and the
        # harness could not see its own child. Repeated -o rather than a
        # comma-joined spec for the same reason -- FreeBSD reads everything
        # after the first comma as the FIRST column's header, swallowing
        # `command=` entirely. Both forms below are POSIX and work on Linux,
        # FreeBSD and macOS. (Found by bikeroom-freebsd-operato-dd8bca.)
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or marker not in fields[1]:
            continue
        if fields[1].lstrip().startswith("ps "):
            continue
        try:
            pids.append(int(fields[0]))
        except ValueError:
            continue
    return pids


def detection_works() -> str | None:
    """Prove _live_pids can see a process we KNOW exists. Returns an error or None.

    Without this the harness reports "first supervisor never started a child"
    when the truth is that its own `ps` invocation is blind -- which is exactly
    what happened on FreeBSD, and which blames the product for a defect in the
    measuring instrument. A detector that has never been shown finding anything
    cannot be trusted to report an absence.
    """
    marker = "detectioncheck_%s" % uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="supervice-detect-") as tmp:
        script = os.path.join(tmp, "%s.py" % marker)
        with open(script, "w") as f:
            f.write(SILENT_CHILD)
        sentinel = subprocess.Popen(
            [sys.executable, "-u", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 10.0
            while time.time() < deadline:
                found = _live_pids(marker)
                if sentinel.pid in found:
                    return None
                time.sleep(0.2)
            return (
                "process detection is blind on this platform: spawned pid %d "
                "carrying marker %s and `ps -A -o pid= -o command=` did not "
                "report it. Every arm below would misreport as 'no child'."
                % (sentinel.pid, marker)
            )
        finally:
            sentinel.kill()
            sentinel.wait(timeout=10)


def _write_config(tmp: str, marker: str, *, pdeathsig: bool, reconcile: str) -> str:
    child = os.path.join(tmp, "%s.py" % marker)
    with open(child, "w") as f:
        f.write(SILENT_CHILD)
    cfg = os.path.join(tmp, "supervice.ini")
    with open(cfg, "w") as f:
        f.write(
            "\n".join(
                [
                    "[supervice]",
                    "loglevel=INFO",
                    "logfile=%s" % os.path.join(tmp, "d.log"),
                    "pidfile=%s" % os.path.join(tmp, "d.pid"),
                    "socket=%s" % os.path.join(tmp, "s.sock"),
                    "",
                    "[program:victim]",
                    "command=%s -u %s" % (sys.executable, child),
                    "autostart=true",
                    "autorestart=false",
                    "pdeathsig=%s" % ("true" if pdeathsig else "false"),
                    "reconcile=%s" % reconcile,
                ]
            )
            + "\n"
        )
    return cfg


def _boot(cfg: str, sock: str, marker: str) -> subprocess.Popen[bytes] | None:
    """Start a supervisor and wait until IT is up, not until some child exists.

    Waiting on a child count is wrong on the restart: the orphan from the
    previous instance already satisfies it, so the wait returns instantly and
    the caller measures before the new supervisor has reconciled or spawned
    anything. That made a WORKING reconciliation look broken -- the failure
    direction that gets a correct feature "fixed" until it breaks.

    Wait for the RPC socket, which only appears once this supervisor has bound
    it, then for the child count to stop changing.
    """
    sup = subprocess.Popen(
        [sys.executable, "-m", "supervice.main", "-c", cfg, "-n"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if sup.poll() is not None:
            return None
        if os.path.exists(sock):
            break
        time.sleep(0.2)
    else:
        return sup

    # Settle: reconciliation and the first spawn both have to land.
    last = None
    stable_since = time.time()
    while time.time() < deadline:
        now = _live_pids(marker)
        if now != last:
            last, stable_since = now, time.time()
        elif time.time() - stable_since >= 1.5 and now:
            break
        time.sleep(0.2)
    return sup


def run_arm(arm: str, *, pdeathsig: bool, reconcile: str, description: str) -> dict[str, object]:
    marker = "reconcileprobe_%s" % uuid.uuid4().hex[:12]
    result: dict[str, object] = {"arm": arm, "description": description, "error": None}

    with tempfile.TemporaryDirectory(prefix="supervice-reconcile-") as tmp:
        cfg = _write_config(tmp, marker, pdeathsig=pdeathsig, reconcile=reconcile)
        sock = os.path.join(tmp, "s.sock")
        try:
            sup = _boot(cfg, sock, marker)
            if sup is None or not _live_pids(marker):
                result["error"] = "first supervisor never started a child"
                return result
            result["before_crash"] = len(_live_pids(marker))

            os.kill(sup.pid, signal.SIGKILL)
            sup.wait(timeout=10)
            time.sleep(2.0)

            orphans = _live_pids(marker)
            result["orphaned"] = len(orphans)
            if not orphans:
                # pdeathsig reaped it, so there is nothing for reconciliation to
                # find and this arm cannot say anything. Report rather than
                # quietly producing a "1 child" result that looks like success.
                result["error"] = (
                    "no orphan survived the crash, so reconciliation was never exercised"
                )
                return result

            # The dead supervisor's socket file is still on disk (SIGKILL ran no
            # cleanup). Remove it so _boot waits for the NEW supervisor to bind
            # rather than matching the corpse's leftovers.
            if os.path.exists(sock):
                os.unlink(sock)
            sup2 = _boot(cfg, sock, marker)
            time.sleep(1.0)
            result["after_restart"] = len(_live_pids(marker))

            log = os.path.join(tmp, "d.log")
            text = open(log).read() if os.path.exists(log) else ""
            result["warned"] = "still running" in text or "killed orphaned" in text

            if sup2 and sup2.poll() is None:
                sup2.kill()
                sup2.wait(timeout=10)
        except Exception as e:  # noqa: BLE001 - harness reports, never masks
            result["error"] = "%s: %s" % (type(e).__name__, e)
        finally:
            for pid in _live_pids(marker):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    return result


ARMS = [
    ("R1", dict(pdeathsig=False, reconcile="off", description="BUG reproduced: expect 2")),
    ("R2", dict(pdeathsig=False, reconcile="kill", description="FIX: expect 1")),
    ("R3", dict(pdeathsig=False, reconcile="auto", description="opt-out: expect 2 + warning")),
]


def main() -> int:
    import platform

    print("supervice reconciliation harness")
    print("platform : %s %s (%s)" % (platform.system(), platform.release(), platform.machine()))
    print("python   : %s" % platform.python_version())

    # Validate the instrument before trusting it to report absence.
    blind = detection_works()
    if blind is not None:
        print("")
        print("ABORTED: %s" % blind)
        print("This is a defect in the harness, NOT a result about supervice.")
        return 2
    print("detection: OK (verified against a known-live sentinel process)")
    print("")

    results = []
    for arm, spec in ARMS:
        r = run_arm(arm, **spec)  # type: ignore[arg-type]
        results.append(r)
        if r["error"]:
            print("ARM %s  ERROR: %s" % (arm, r["error"]))
            continue
        print(
            "ARM %s  before=%s orphaned=%s after_restart=%s warned=%s   %s"
            % (
                arm,
                r.get("before_crash"),
                r.get("orphaned"),
                r.get("after_restart"),
                r.get("warned"),
                r["description"],
            )
        )

    print("")
    by = {r["arm"]: r for r in results if not r["error"]}
    if "R1" not in by or "R2" not in by:
        print("INCONCLUSIVE: control or fix arm errored; no claim can be made.")
        return 1

    if by["R1"].get("after_restart") != 2:
        print(
            "INCONCLUSIVE: the CONTROL (reconcile=off) did not reproduce the duplicate.\n"
            "Expected 2 live children after restart, got %s. Without the bug\n"
            "reproduced, arm R2 showing 1 proves nothing." % by["R1"].get("after_restart")
        )
        return 1

    if by["R2"].get("after_restart") == 1:
        print(
            "RECONCILIATION WORKS: duplicate reproduced with it off (2),\n"
            "prevented with it on (1)."
        )
    else:
        print(
            "RECONCILIATION FAILED: with reconcile=kill the restart still left %s children."
            % by["R2"].get("after_restart")
        )
        return 1

    if "R3" in by:
        r3 = by["R3"]
        if r3.get("after_restart") == 2 and r3.get("warned"):
            print("pdeathsig=false opt-out honoured: orphan left alive, duplicate WARNED about.")
        else:
            print(
                "ARM R3 unexpected: after_restart=%s warned=%s (expected 2 and True)"
                % (r3.get("after_restart"), r3.get("warned"))
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
