#!/usr/bin/env python3
"""End-to-end orphan harness: does a child survive an abrupt supervisor kill?

Runs as a standalone script on any supported platform and prints raw results,
or is imported by tests/test_orphan_e2e.py for the CI assertions.

    python3 tests/orphan_harness.py            # all arms
    python3 tests/orphan_harness.py --arm B    # one arm

Why this exists (SPECS/13): every pdeathsig test in this repo was a mock
assertion against a fake libc, or a check that a warning string appeared. None
of them killed a supervisor and asked whether a child died, so the guarantee
had never been exercised on any platform.

READ THIS BEFORE CHANGING AN ARM
--------------------------------
Arm C is not a bug report, it is a permanent guard. process.py routes child
stdout through a pipe ONLY when a logfile is configured; killing the supervisor
closes the read end, so the next write kills a chatty child by SIGPIPE. A chatty
child therefore dies whether or not pdeathsig works, on every platform. That is
why arms A and B use a silent child, and why arm C is kept next to them: it is
the false positive, recorded, so nobody reintroduces it as a "better" test.

Arm B is the control. Without it, arm A proves nothing — a child that died could
have been killed by supervice's own teardown rather than by the kernel. Arm B
uses the identical child and the identical kill with pdeathsig off; if it dies
too, arm A's result is meaningless and the harness says so.

PROVING THE CONTROL CAN FAIL
----------------------------
A control that has never been seen to fail is decoration. `--sabotage-control`
breaks it on purpose and the harness must then report INCONCLUSIVE.

The recipe is PER-PLATFORM and the obvious one is wrong half the time. Forcing
pdeathsig=true in the control only has teeth where pdeathsig is implemented; on
macOS it is a no-op, the control survives, and the sabotage proves nothing —
validating the control only on the platforms where it is least needed. There,
make the control chatty instead and let SIGPIPE kill it. See
`sabotage_control()`, whose Darwin branch came from macbook-admin-bd8e86 after
my recipe silently failed to bite on their machine.

WHEN SENDING THIS TO ANOTHER PLATFORM RIG
-----------------------------------------
State what a WRONG result looks like, in advance, alongside the expected one.
A runner who has been told the failure modes cannot quietly file a surprising
result as normal — they must match the prediction or explain the gap. That
protocol, not any assertion in this file, is what got the no-op sabotage above
diagnosed instead of shrugged at. It originated with
bikeroom-freebsd-operato-dd8bca and is the cheapest practice here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supervice.client import Controller  # noqa: E402

# The child must produce NO output in arms A and B. See module docstring.
SILENT_CHILD = "import time,sys\nwhile True: time.sleep(3600)\n"
CHATTY_CHILD = "import time,sys\nwhile True:\n    print('tick', flush=True)\n    time.sleep(0.05)\n"

STARTUP_TIMEOUT = 15.0
REAP_GRACE = 3.0


class ArmResult:
    def __init__(self, arm: str, description: str) -> None:
        self.arm = arm
        self.description = description
        self.supervisor_pid: int | None = None
        self.child_pid: int | None = None
        self.child_survived: bool | None = None
        self.error: str | None = None

    @property
    def outcome(self) -> str:
        if self.error:
            return "ERROR"
        return "SURVIVED" if self.child_survived else "DIED"


def _pid_alive(pid: int) -> bool:
    """True if pid exists. Signal 0 checks existence without delivering."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but is not ours — still alive for our purposes.
        return True
    return True


def _pid_matches_marker(pid: int, marker: str) -> bool:
    """Guard the harness against its own PID-reuse hazard.

    A reaped pid can be recycled onto an unrelated process between the kill and
    the check. `_pid_alive` alone would then report SURVIVED for a stranger. So
    confirm the pid still carries our unique marker in its argv.
    """
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return marker in out.stdout


def _write_config(tmp: str, marker: str, pdeathsig: bool, chatty: bool) -> tuple[str, str]:
    child_src = os.path.join(tmp, "%s.py" % marker)
    with open(child_src, "w") as f:
        f.write(CHATTY_CHILD if chatty else SILENT_CHILD)

    sock = os.path.join(tmp, "s.sock")
    lines = [
        "[supervice]",
        "loglevel=INFO",
        "logfile=%s" % os.path.join(tmp, "d.log"),
        "pidfile=%s" % os.path.join(tmp, "d.pid"),
        "socket=%s" % sock,
        "",
        "[program:victim]",
        "command=%s -u %s" % (sys.executable, child_src),
        "autostart=true",
        "autorestart=false",
        "pdeathsig=%s" % ("true" if pdeathsig else "false"),
    ]
    if chatty:
        # Arm C only: this is what creates the SIGPIPE false positive.
        lines.append("stdout_logfile=%s" % os.path.join(tmp, "child.log"))

    cfg = os.path.join(tmp, "supervice.ini")
    with open(cfg, "w") as f:
        f.write("\n".join(lines) + "\n")
    return cfg, sock


async def _child_pid_via_rpc(sock: str) -> int | None:
    """Ask the daemon for the child pid through its own RPC interface.

    Deliberately not scraped from ps: the supervisor's own answer is what a
    user would get, and it is the thing under test.
    """
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if os.path.exists(sock):
            try:
                resp = await Controller(socket_path=sock).send_command("status")
                for p in resp.get("processes", []):
                    if p.get("state") == "RUNNING" and p.get("pid"):
                        return int(p["pid"])
            except (OSError, TimeoutError, json.JSONDecodeError):
                pass
        await asyncio.sleep(0.2)
    return None


def run_arm(arm: str, *, pdeathsig: bool, chatty: bool) -> ArmResult:
    result = ArmResult(arm, describe(arm, pdeathsig, chatty))
    marker = "orphanprobe_%s" % uuid.uuid4().hex[:12]

    with tempfile.TemporaryDirectory(prefix="supervice-orphan-") as tmp:
        cfg, sock = _write_config(tmp, marker, pdeathsig, chatty)

        sup = subprocess.Popen(
            [sys.executable, "-m", "supervice.main", "-c", cfg, "-n"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result.supervisor_pid = sup.pid

        try:
            child_pid = asyncio.run(_child_pid_via_rpc(sock))
            if child_pid is None:
                result.error = "child never reached RUNNING within %.0fs" % STARTUP_TIMEOUT
                return result
            result.child_pid = child_pid

            if not _pid_matches_marker(child_pid, marker):
                result.error = "pid %d does not carry marker %s" % (child_pid, marker)
                return result

            # SIGKILL, not SIGTERM: the supervisor must run NO cleanup path, so
            # that a dead child is attributable to the kernel rather than to
            # supervice tidying up on the way out.
            os.kill(sup.pid, signal.SIGKILL)
            sup.wait(timeout=10)

            time.sleep(REAP_GRACE)

            alive = _pid_alive(child_pid) and _pid_matches_marker(child_pid, marker)
            result.child_survived = alive
        except Exception as e:  # noqa: BLE001 - harness reports, never masks
            result.error = "%s: %s" % (type(e).__name__, e)
        finally:
            if sup.poll() is None:
                sup.kill()
                sup.wait(timeout=10)
            if result.child_pid and _pid_alive(result.child_pid):
                if _pid_matches_marker(result.child_pid, marker):
                    try:
                        os.kill(result.child_pid, signal.SIGKILL)
                    except OSError:
                        pass

    return result


ROLES = {
    "A": "the real claim",
    "B": "CONTROL, must survive",
    "C": "SIGPIPE false positive",
}

ARMS: dict[str, dict[str, bool]] = {
    "A": dict(pdeathsig=True, chatty=False),
    "B": dict(pdeathsig=False, chatty=False),
    "C": dict(pdeathsig=True, chatty=True),
}


def describe(arm: str, pdeathsig: bool, chatty: bool) -> str:
    """Build the arm label from the flags ACTUALLY used for the run.

    Deliberately derived, never stored. A hardcoded label decoupled from the
    flags beside it misdescribes exactly the runs where the label matters most
    — someone running a sabotaged variant to investigate a strange result — and
    a pasted log then sends the next reader hunting a phantom. Reported by
    macbook-admin-bd8e86 after two sabotage runs printed "SILENT" for a chatty
    control.
    """
    return "pdeathsig=%-5s %s child%s - %s" % (
        "true" if pdeathsig else "false",
        "CHATTY" if chatty else "SILENT",
        " + logfile" if chatty else "",
        ROLES[arm],
    )


def sabotage_control(arms: dict[str, dict[str, bool]]) -> str:
    """Break the control on purpose, by a mechanism that works on THIS platform.

    The control exists to prove that a dead child in arm A is attributable to
    pdeathsig. That claim is only worth anything if the control has been shown
    capable of dying — so this mode exists to demonstrate it, and the harness
    must then report INCONCLUSIVE.

    The recipe is platform-specific and the obvious one is wrong half the time.
    Forcing pdeathsig=true in the control has teeth only where pdeathsig is
    implemented; on macOS it is a no-op, the control survives, and the sabotage
    silently proves nothing. So on a platform with no parent-death signal, make
    the control chatty instead and let SIGPIPE do it.

    Carried here as a recipe rather than left as folklore, because the wrong
    recipe reads exactly like the right one.
    """
    if sys.platform == "linux" or sys.platform.startswith("freebsd"):
        arms["B"]["pdeathsig"] = True
        return "forced pdeathsig=true in the control (kernel parent-death signal)"
    arms["B"]["chatty"] = True
    return "made the control chatty with a logfile (SIGPIPE; no pdeathsig on this platform)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=sorted(ARMS), help="run a single arm")
    ap.add_argument(
        "--sabotage-control",
        action="store_true",
        help="break the control on purpose; the harness MUST then report INCONCLUSIVE",
    )
    args = ap.parse_args()

    selected = [args.arm] if args.arm else sorted(ARMS)

    arms = {k: dict(v) for k, v in ARMS.items()}
    sabotage = sabotage_control(arms) if args.sabotage_control else None

    print("supervice orphan harness")
    print("platform : %s %s (%s)" % (platform.system(), platform.release(), platform.machine()))
    print("python   : %s" % platform.python_version())
    if sabotage:
        print("SABOTAGE : %s" % sabotage)
        print("           expecting INCONCLUSIVE; any verdict means the control is inert")
    print("")

    results = []
    conclusive = False
    for arm in selected:
        spec = arms[arm]
        r = run_arm(arm, **spec)  # type: ignore[arg-type]
        results.append(r)
        print(
            "ARM %s  supervisor=%-7s child=%-7s  child=%-8s  %s"
            % (
                arm,
                r.supervisor_pid or "-",
                r.child_pid or "-",
                r.outcome,
                r.error or describe(arm, spec["pdeathsig"], spec["chatty"]),
            )
        )

    print("")
    by_arm = {r.arm: r for r in results}

    if "A" in by_arm and "B" in by_arm:
        a, b = by_arm["A"], by_arm["B"]
        if a.error or b.error:
            print("INCONCLUSIVE: an arm errored; no claim can be made.")
        elif not b.child_survived:
            # Describe the control as it was actually configured. Under
            # --sabotage-control it is not "pdeathsig OFF", and a verdict line
            # that misstates the setup is the same defect as a mislabelled arm.
            print(
                "INCONCLUSIVE: the CONTROL child died (%s), so arm A's result is not\n"
                "attributable to pdeathsig. Something else is killing children -\n"
                "investigate before trusting any orphan result here."
                % describe("B", arms["B"]["pdeathsig"], arms["B"]["chatty"])
            )
        elif not a.child_survived:
            conclusive = True
            print("pdeathsig is FUNCTIONAL here: child died with it on, survived with it off.")
        else:
            conclusive = True
            print(
                "pdeathsig is NOT functional here: the child survived an abrupt\n"
                "supervisor kill. Expected on macOS; a defect on Linux and FreeBSD."
            )

    if "C" in by_arm and not by_arm["C"].error:
        c = by_arm["C"]
        if not conclusive:
            # Do not narrate past the point where we stopped being able to
            # claim. Arm C only means something relative to the A/B verdict,
            # and there isn't one.
            print("\nARM C: not interpretable while the control is untrusted.")
        elif not c.child_survived and by_arm["A"].child_survived:
            print(
                "\nARM C died while ARM A survived. That is the SIGPIPE false positive\n"
                "isolated on one machine: a chatty child with a logfile is reaped by\n"
                "accident, so an orphan test built on one is green regardless of\n"
                "whether pdeathsig works."
            )
        elif not c.child_survived:
            print(
                "\nARM C died, as did ARM A. Consistent with working pdeathsig; this run\n"
                "does not separate it from the SIGPIPE path."
            )
        else:
            print("\nARM C survived - the SIGPIPE path did not fire; note the platform.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
