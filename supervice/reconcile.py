"""Startup reconciliation: find children a previous supervisor left behind.

WHY THIS EXISTS
---------------
`pdeathsig` asks the kernel to kill a child when its parent dies. It covers
exactly one generation, and only on Linux and FreeBSD. So on macOS, and for the
grandchildren of any forking program on every platform, an abruptly-killed
supervisor leaves processes running.

The orphan itself is survivable. The damage is what the *next* start does: with
no memory of what it spawned, the supervisor sees nothing running and starts a
second copy alongside the first. Measured on Darwin, supervice 0.3.0: four
orphaned children before a restart, five after. For a port-binding service that
is a hard failure; for a queue consumer it is a silent duplicate-worker bug.

This module gives the supervisor that memory.

IDENTITY, NOT PID
-----------------
"Record the pid and kill it if alive at startup" is how a supervisor eventually
SIGKILLs a stranger. Pids are recycled -- the pid space wraps in under five
minutes of sustained forking on an ordinary machine -- and reconciliation runs
at startup, which may be minutes or a week after the crash.

So a record is an *identity*: name, pid, pgid, and an opaque start token that
changes when the pid is reused. If the token does not match, the process is not
ours and we do not touch it.

FAIL CLOSED
-----------
Every failure path here declines to signal. Unreadable token, unreadable state
file, unparseable record, missing platform support: all of them mean "do not
kill", never "assume it is ours". The cost of a false negative is an orphan that
survives one more restart. The cost of a false positive is killing somebody
else's process.
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass

STATE_VERSION = 1

# Darwin sysctl(3) selectors for reading a process's start time. Verified on
# Darwin 27.0.0 arm64: sizeof(kinfo_proc)=648, p_starttime at offset 0.
CTL_KERN = 1
KERN_PROC = 14
KERN_PROC_PID = 1

# Program-level policy for what to do with an identified orphan.
RECONCILE_AUTO = "auto"  # kill if the program wanted pdeathsig, else warn
RECONCILE_KILL = "kill"
RECONCILE_WARN = "warn"
RECONCILE_OFF = "off"
RECONCILE_CHOICES = (RECONCILE_AUTO, RECONCILE_KILL, RECONCILE_WARN, RECONCILE_OFF)


@dataclass(frozen=True)
class ChildRecord:
    """One spawned child, as it was at spawn time."""

    name: str
    pid: int
    pgid: int
    token: str
    pdeathsig: bool
    reconcile: str


def _linux_token(pid: int) -> str | None:
    """Start time from /proc/<pid>/stat field 22, plus pgid and command line.

    Field 22 is ticks since boot and is monotonic -- it is never rewritten, and
    it keeps climbing for the life of the machine. That is what makes it a valid
    recycling guard: a pid reused minutes later necessarily carries a much
    larger value than the one recorded, so the tokens cannot match.

    Note the resolution is a clock tick (10ms at 100Hz), so two processes
    started in the SAME tick share a start time. That is not a weakness here:
    processes alive simultaneously cannot share a pid, and by the time a pid is
    recycled the counter has moved on by many thousands of ticks. pgid is folded
    in as cheap defence in depth rather than because the start time needs help.

    The command line is deliberately NOT part of the token, though it looks like
    free extra identity. Two reasons, both measured:
      - it is empty for a window after fork and before exec completes (198 of
        300 spawns on this machine), so a token taken at spawn could never match
        again;
      - long-running servers rewrite their own argv (setproctitle, gunicorn),
        so it changes under a healthy process.
    Either way the mismatch fails closed, which is safe but silently disables
    reconciliation -- for pre-forking servers most of all, which are exactly the
    programs this feature exists to protect.

    The comm field can contain both spaces and parentheses, so the split must be
    taken after the LAST ')' rather than by naive whitespace splitting.
    """
    try:
        with open("/proc/%d/stat" % pid) as f:
            raw = f.read()
    except OSError:
        return None
    try:
        fields = raw[raw.rindex(")") + 2 :].split()
        return "starttime:%s pgid:%s" % (fields[19], fields[2])
    except (ValueError, IndexError):
        return None


def _darwin_token(pid: int) -> str | None:
    """Microsecond start time from sysctl(KERN_PROC_PID) on macOS.

    `ps -o lstart=` is second-resolution, and the ps token carries no pid -- it
    is lstart + pgid. For a supervice child pgid == pid (each is a session
    leader), so a recycled pid held by another session leader that started in
    the SAME second produces a byte-identical token, and reconciliation would
    kill a process it does not own. Narrow, but a wrong-kill rather than a
    missed kill, which is the direction the whole guard exists to prevent.

    Microseconds close it. The struct offset is not guessed: sizeof(kinfo_proc)
    = 648 and p_starttime at offset 0 (extern_proc is the first member, and
    p_starttime its first field) were verified on the target by
    macbook-admin-bd8e86 on Darwin 27.0.0 arm64 -- 25/25 distinct tokens for
    concurrent processes, 12/12 for back-to-back spawns where ps gives 1/12.

    Returns None on any failure so the caller falls back to the ps token: this
    can only improve resolution, never lose the behaviour we already have.

    Deliberately NOT applied to FreeBSD, whose kinfo_proc layout differs and
    has not been verified on a real host. Guessing there would be exactly the
    unvalidated assumption this module exists to avoid, and it costs nothing:
    FreeBSD has working procctl pdeathsig already.
    """
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL("libc.dylib", use_errno=True)
    except OSError:
        return None

    mib = (ctypes.c_int * 4)(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid)
    buf = ctypes.create_string_buffer(4096)
    size = ctypes.c_size_t(len(buf))
    try:
        rc = libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0)
    except Exception:  # noqa: BLE001 - never let a ctypes problem escape
        return None
    # size 0 means the pid is gone; anything shorter than a timeval is unusable.
    if rc != 0 or size.value < 12:
        return None
    try:
        # struct timeval on Darwin: int64 tv_sec, int32 tv_usec.
        sec, usec = struct.unpack_from("<qi", buf, 0)
    except struct.error:
        return None
    if sec <= 0:
        return None
    return "darwin:%d.%06d" % (sec, usec)


def _ps_token(pid: int) -> str | None:
    """Fallback for platforms without /proc: ps start time + pgid.

    `lstart` is second-resolution, which is coarse -- a burst of spawns shares
    one value -- but the threat is a pid reused MINUTES later, and minutes are
    resolvable in seconds. Simultaneous processes sharing a start second cannot
    share a pid, so the coarseness costs nothing here.

    The command column is excluded for the same reasons as on Linux: it is
    unstable after fork and rewritable by the process itself.

    Uses REPEATED -o flags rather than a comma-joined spec. `-o lstart=,pgid=`
    is a GNU/Darwin extension; FreeBSD parses it as a SINGLE column named
    "lstart" with the header string ",pgid=", so the pgid is never requested and
    the token came out as the literal `ps:,pgid= Fri Aug 14 18:01:16 2026` --
    identical for every process on the host that started in that second. That is
    a wrong-kill door far wider than the one it was meant to leave. Repeated -o
    is POSIX and works on all three platforms. (Found on FreeBSD 15.1 by
    bikeroom-freebsd-operato-dd8bca.)

    The output is now VALIDATED rather than trusted: the last field must be a
    numeric pgid. A token that cannot be parsed returns None, so reconciliation
    declines to act. The previous code accepted whatever ps printed and turned a
    malformed command into a colliding identity -- a garbage token is worse than
    no token, because no token fails closed and a garbage one collides.

    Microsecond start times are available via sysctl(KERN_PROC_PID) on FreeBSD
    and would be a better anchor; Darwin already uses that path. FreeBSD's
    kinfo_proc layout has not been verified on a real host, so it is not
    guessed here -- see the resolution note in `token_is_subsecond()`.
    """
    # The pgid comes from a syscall, NOT from ps. Parsing it out of ps output is
    # ambiguous in a way that bites: `lstart` ends in a four-digit year, so
    # "take the last numeric field as the pgid" happily reads 2026 as a process
    # group. That is precisely how the FreeBSD defect stayed invisible -- the
    # token looked well-formed and carried a year where the discriminator should
    # have been. os.getpgid has no such failure mode.
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return None
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    lstart = " ".join(out.stdout.split())
    # Reject anything carrying a format fragment: if a platform ever echoes the
    # spec back instead of expanding it, that must fail closed rather than
    # become part of an identity.
    if not lstart or "=" in lstart:
        return None
    return "ps:%s pgid:%d" % (lstart, pgid)


def token_is_subsecond() -> bool:
    """Whether this platform's start token can distinguish same-second starts.

    This is the property the recycling guard actually depends on. When a pid is
    reused, the new holder has the SAME pid, and for a supervice child the same
    pgid too (children are session leaders, so pgid == pid). Every component of
    the token is therefore identical except the start time -- so if the start
    time is only accurate to the second, two processes holding that pid a second
    apart are indistinguishable and reconciliation could kill the wrong one.

        Linux    /proc stat field 22, clock ticks  -> sub-second
        Darwin   sysctl kinfo_proc, microseconds   -> sub-second
        other    ps lstart, whole seconds          -> NOT sub-second

    So on a platform falling back to ps there is a real same-second collision
    window. It is narrow -- the pid must be recycled within the same second the
    original started, which needs the pid space to wrap in under a second -- but
    it is a wrong-kill rather than a missed kill, and it is stated rather than
    left implicit.
    """
    return sys.platform == "linux" or sys.platform == "darwin"


def process_start_token(pid: int) -> str | None:
    """An opaque identity token for a live pid, or None if it cannot be read.

    None means "cannot establish identity" and callers MUST treat it as a
    refusal to act, never as "not ours" or "already gone" -- both of those are
    also None, and conflating them is how a guard turns into a no-op.
    """
    if pid <= 0:
        return None
    # Best resolution first; each falls back rather than failing, so a
    # platform-specific reader can only improve on the portable floor.
    token = _linux_token(pid)
    if token is not None:
        return token
    token = _darwin_token(pid)
    if token is not None:
        return token
    return _ps_token(pid)


class StateStore:
    """Per-supervisor record of spawned children, as atomically-replaced JSON.

    Deliberately NOT a single shared path such as ~/.supervice.state: supervice
    supports several daemons running concurrently with different configs, and a
    shared store would have one supervisor read another's records at startup and
    kill its children.

    JSON with tmp+rename rather than SQLite: one writer, a handful of rows,
    written on spawn/exit and read once at startup. rename(2) is atomic on
    POSIX, so a crash mid-write leaves the previous good file rather than a torn
    one, and there are no lock or -wal files to wedge. An operator can also read
    or delete it by hand mid-incident, which matters more than transactions for
    data this small.
    """

    def __init__(self, path: str) -> None:
        self.path = path

    @staticmethod
    def default_path(pidfile: str) -> str:
        """Sit beside the pidfile, which is already per-supervisor."""
        base = os.path.abspath(pidfile or "supervice.pid")
        directory = os.path.dirname(base) or "."
        stem = os.path.basename(base)
        for suffix in (".pid", ".pidfile"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return os.path.join(directory, ".%s.state.json" % (stem or "supervice"))

    def load(self) -> list[ChildRecord]:
        """Read records. Any problem yields an empty list, never a partial one."""
        try:
            with open(self.path) as f:
                blob = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(blob, dict) or blob.get("version") != STATE_VERSION:
            return []
        records = []
        for item in blob.get("children", []):
            try:
                records.append(
                    ChildRecord(
                        name=str(item["name"]),
                        pid=int(item["pid"]),
                        pgid=int(item["pgid"]),
                        token=str(item["token"]),
                        pdeathsig=bool(item["pdeathsig"]),
                        reconcile=str(item.get("reconcile", RECONCILE_AUTO)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                # Skip the unparseable record rather than dropping the file: a
                # malformed entry must not disarm reconciliation for the rest.
                continue
        return records

    def save(self, records: list[ChildRecord]) -> None:
        """Atomically replace the state file. Best effort; never raises."""
        blob = {"version": STATE_VERSION, "children": [asdict(r) for r in records]}
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".supervice-state-")
            with os.fdopen(fd, "w") as f:
                json.dump(blob, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
            tmp_path = None
        except OSError:
            pass
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def clear(self) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass


@dataclass(frozen=True)
class Verdict:
    """What reconciliation decided about one record, and why."""

    record: ChildRecord
    action: str  # "kill" | "warn" | "skip"
    reason: str


def decide(record: ChildRecord) -> Verdict:
    """Decide what to do about one recorded child. Pure; signals nothing."""
    if record.reconcile == RECONCILE_OFF:
        return Verdict(record, "skip", "reconcile=off for this program")

    token = process_start_token(record.pid)
    if token is None:
        # Covers "already gone" and "cannot read identity" alike. Both must
        # decline: we cannot tell them apart, and only one of them is safe.
        return Verdict(record, "skip", "pid %d is gone or its identity cannot be read" % record.pid)

    if token != record.token:
        return Verdict(
            record,
            "skip",
            "pid %d is alive but is NOT ours (start token differs; pid was recycled)" % record.pid,
        )

    if record.reconcile == RECONCILE_WARN:
        return Verdict(record, "warn", "reconcile=warn for this program")
    if record.reconcile == RECONCILE_KILL:
        return Verdict(record, "kill", "reconcile=kill for this program")

    # RECONCILE_AUTO. Honour what the program asked for on the previous run.
    if record.pdeathsig:
        return Verdict(record, "kill", "orphan of a previous supervisor (pdeathsig was requested)")
    # pdeathsig=false is a deliberate opt-out: surviving a supervisor crash is
    # the INTENDED outcome, so killing here would silently invert a documented
    # guarantee. But the duplicate-spawn that follows is not intended, and
    # saying nothing is how it stays invisible.
    return Verdict(
        record,
        "warn",
        "orphan of a previous supervisor; pdeathsig=false so it is left running "
        "-- starting this program will create duplicates",
    )


def kill_group(pgid: int, pid: int) -> bool:
    """SIGKILL the orphan's process group, falling back to the pid.

    The group is the point: pdeathsig cannot reach grandchildren on any
    platform, and children are spawned with start_new_session=True, so the
    group is exactly the subtree this supervisor created.
    """
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return True
        except (OSError, ProcessLookupError):
            pass
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except OSError:
        return False
