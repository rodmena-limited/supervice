"""Parent-death-signal machinery (Linux prctl / FreeBSD procctl).

Everything here runs either in the parent at import/startup time or inside the
forked child before exec, where dlopen/imports are unsafe if any other thread
holds allocator or loader locks — so libc is loaded once at import time and
the preexec hook touches nothing but that pre-loaded handle.
"""

import ctypes
import os
import shlex
import shutil
import signal
import stat
import sys

PR_SET_PDEATHSIG = 1  # Linux prctl(2)
PR_GET_PDEATHSIG = 2  # Linux prctl(2): read the current setting back
PROC_PDEATHSIG_CTL = 11  # FreeBSD sys/procctl.h
PROC_PDEATHSIG_STATUS = 12  # FreeBSD sys/procctl.h: read it back
P_PID = 0  # FreeBSD procctl(2): apply to the calling process

# Exit codes used by the startup probe's throwaway child to report what it
# observed. Anything else is a signal death or an unexpected failure, and is
# treated as "not functional" rather than assumed working.
_PROBE_SET_FAILED = 254
_PROBE_READBACK_FAILED = 253
_PROBE_RAISED = 252

_LIBC: ctypes.CDLL | None = None
if sys.platform == "linux":
    try:
        _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        _LIBC = None
elif sys.platform.startswith("freebsd"):
    try:
        _LIBC = ctypes.CDLL("libc.so.7", use_errno=True)
        # id_t is 32-bit on FreeBSD; the value passed is always 0 (self), so
        # explicit argtypes keep the call honest on every supported arch.
        _LIBC.procctl.argtypes = (
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        )
    except (AttributeError, OSError):
        _LIBC = None


def pdeathsig_supported() -> bool:
    """Whether the platform *has* a parent-death-signal mechanism at all.

    This only reports that libc loaded and a code path exists. It says nothing
    about whether the syscall works here — use `pdeathsig_functional()` for
    that. Kept separate because the two questions have different answers on a
    restricted host (a jail, a seccomp sandbox), and conflating them is what
    let a broken mechanism report itself as supported.
    """
    return _LIBC is not None


def _pdeathsig_set(sig: int) -> int:
    """Install a parent-death signal on the calling process. 0 on success.

    ctypes does NOT raise when a syscall returns -1, so the return value is the
    only failure signal there is. Callers must check it.
    """
    if _LIBC is None:
        return -1
    if sys.platform == "linux":
        return int(_LIBC.prctl(PR_SET_PDEATHSIG, int(sig)))
    if sys.platform.startswith("freebsd"):
        val = ctypes.c_int(int(sig))
        return int(_LIBC.procctl(P_PID, 0, PROC_PDEATHSIG_CTL, ctypes.byref(val)))
    return -1


def _pdeathsig_get() -> tuple[int, int]:
    """Read the calling process's parent-death signal. Returns (rc, value)."""
    if _LIBC is None:
        return -1, 0
    out = ctypes.c_int(0)
    if sys.platform == "linux":
        rc = int(_LIBC.prctl(PR_GET_PDEATHSIG, ctypes.byref(out)))
    elif sys.platform.startswith("freebsd"):
        rc = int(_LIBC.procctl(P_PID, 0, PROC_PDEATHSIG_STATUS, ctypes.byref(out)))
    else:
        return -1, 0
    return rc, out.value


_pdeathsig_functional_cache: bool | None = None


def pdeathsig_functional() -> bool:
    """Verify the parent-death-signal mechanism actually works on this host.

    Forks a throwaway child, performs the REAL set operation there, reads it
    back, and reports via exit status — which is safe from a child, unlike
    logging.

    Why the real set rather than a cheap read of the current value: the
    likeliest place for this to be unavailable is a restricted environment such
    as a FreeBSD jail, which is exactly the shape that permits the read and
    denies the write. A probe that is weakest precisely where it is most needed
    is not worth running, so this performs the same syscall, in the same
    post-fork context, as the real spawn path.

    Runs at most once per process; the result is cached. Nothing is added to
    the per-spawn hot path.
    """
    global _pdeathsig_functional_cache
    if _pdeathsig_functional_cache is not None:
        return _pdeathsig_functional_cache

    if _LIBC is None:
        _pdeathsig_functional_cache = False
        return False

    want = int(signal.SIGKILL)
    try:
        pid = os.fork()
    except OSError:
        # Cannot fork: report unverified rather than assume it works.
        _pdeathsig_functional_cache = False
        return False

    if pid == 0:  # child
        code = _PROBE_RAISED
        try:
            if _pdeathsig_set(want) != 0:
                code = _PROBE_SET_FAILED
            else:
                rc, value = _pdeathsig_get()
                code = _PROBE_READBACK_FAILED if rc != 0 else (value & 0xFF)
        except BaseException:  # noqa: BLE001 - must never escape into the child
            code = _PROBE_RAISED
        os._exit(code)

    try:
        _, status = os.waitpid(pid, 0)
    except OSError:
        _pdeathsig_functional_cache = False
        return False

    observed = os.waitstatus_to_exitcode(status)
    _pdeathsig_functional_cache = observed == (want & 0xFF)
    return _pdeathsig_functional_cache


def setuid_binary(command: str) -> str | None:
    """Resolved path of `command` if it is a setuid/setgid image, else None.

    The kernel clears the parent-death signal when exec'ing such a binary
    (measured on FreeBSD 15.1; specified in procctl(2) and prctl(2)), so a
    program whose command is one silently loses pdeathsig at exec even though
    the syscall succeeded. That is per-command, so no startup probe can catch it.

    Note this does NOT apply to the `user` option, which switches uid via
    setuid(2) in the preexec hook rather than exec'ing a setuid image.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    path = shutil.which(argv[0])
    if path is None:
        return None
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    return path if mode & (stat.S_ISUID | stat.S_ISGID) else None


def _pdeathsig_preexec() -> None:
    """Post-fork/pre-exec hook: SIGKILL the child when the parent dies.

    Only ever touches the pre-loaded libc handle — no imports, no dlopen, no
    allocation-heavy work is safe here.
    """
    if _LIBC is None:
        return
    try:
        # The return value is deliberately discarded HERE and checked at
        # startup instead, by pdeathsig_functional(). There is nothing safe to
        # do with a failure in this context: logging is unsafe post-fork, and
        # raising does not degrade pdeathsig, it fails the spawn outright
        # (CPython reports preexec_fn exceptions back over the error pipe), so
        # the supervisor would start nothing at all, for every program, on
        # every restart. Degrade orphan-prevention rather than lose the service.
        _pdeathsig_set(int(signal.SIGKILL))
    except Exception:
        pass
