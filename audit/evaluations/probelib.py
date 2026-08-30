"""Shared plumbing for the live audit probes.

Every probe drives supervice through its own interfaces only: the daemon
process, the RPC socket protocol (the same length-prefixed JSON supervicectl
speaks), and the supervicectl CLI. /proc is read only to observe OS process
liveness — the subject of the claims — never to shortcut the product.

Probes are hermetic: each creates its own tmpdir, config, socket, pidfile and
state file, spawns its own daemon, and kills only pids it recorded itself.
Tmpdirs live under /tmp (short paths) because AF_UNIX socket paths are capped
at ~108 bytes and deep TMPDIRs overflow that.

Targeting: SUPERVICE_REPO points at the checkout under test (default: two
directories above this file). The daemon is launched as
`$PYTHON -m supervice.main` with PYTHONPATH set to that repo, and the import
origin is verified before any probe logic runs — a probe that silently tested
the pip-installed supervice would be a self-confirming check.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

REPO = os.environ.get(
    "SUPERVICE_REPO",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
)
PYTHON = os.environ.get("SUPERVICE_PYTHON", sys.executable)

_PASS = "\033[32mPASS\033[0m" if sys.stdout.isatty() else "PASS"
_FAIL = "\033[31mFAIL\033[0m" if sys.stdout.isatty() else "FAIL"


def info(msg: str) -> None:
    print("  .. %s" % msg, flush=True)


def probe_pass(name: str, evidence: str) -> None:
    print("%s %s — %s" % (_PASS, name, evidence), flush=True)
    sys.exit(0)


def probe_fail(name: str, evidence: str) -> None:
    print("%s %s — %s" % (_FAIL, name, evidence), flush=True)
    sys.exit(1)


def probe_error(name: str, evidence: str) -> None:
    """Known-positive could not be established: the probe cannot conclude."""
    print("ERROR %s — precondition failed: %s" % (name, evidence), flush=True)
    sys.exit(2)


def preflight() -> None:
    """Refuse to run against anything but the repo under test."""
    out = subprocess.run(
        [PYTHON, "-c", "import supervice, sys; sys.stdout.write(supervice.__file__)"],
        env={**os.environ, "PYTHONPATH": REPO},
        capture_output=True,
        text=True,
        timeout=30,
    )
    origin = out.stdout.strip()
    if not origin.startswith(os.path.join(REPO, "supervice")):
        probe_error(
            "preflight",
            "supervice imported from %r, not from SUPERVICE_REPO=%r" % (origin, REPO),
        )
    info("testing supervice at %s" % origin)


def mkdtemp() -> str:
    return tempfile.mkdtemp(prefix="svprobe-", dir="/tmp")


def wait_until(fn, timeout: float, interval: float = 0.1, desc: str = "condition"):
    """Poll fn() until it returns a truthy value; return it, or None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def proc_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # Kill 0 succeeds on zombies too; check /proc state where available.
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def pids_with_argv(argv: list[str]) -> list[int]:
    """Pids whose full /proc cmdline is exactly argv. Observation only."""
    want = "\x00".join(argv) + "\x00"
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as f:
                if f.read().decode("utf-8", "replace") == want:
                    found.append(int(entry))
        except OSError:
            continue
    return found


def kill_pid_hard(pid: int) -> None:
    """SIGKILL one exact pid recorded by this probe. Never name-matched."""
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def rpc(socket_path: str, command: str, timeout: float = 10.0, **kwargs):
    """Speak the product's own RPC protocol: 4-byte BE length + JSON."""
    payload = json.dumps({"command": command, **kwargs}).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall(struct.pack(">I", len(payload)) + payload)
        header = b""
        while len(header) < 4:
            chunk = s.recv(4 - len(header))
            if not chunk:
                raise ConnectionError("EOF before RPC header")
            header += chunk
        (length,) = struct.unpack(">I", header)
        body = b""
        while len(body) < length:
            chunk = s.recv(length - len(body))
            if not chunk:
                raise ConnectionError("EOF before RPC body")
            body += chunk
    return json.loads(body.decode())


def read_state(path: str) -> list[dict]:
    """Read the reconciliation state file the way an operator would."""
    with open(path) as f:
        blob = json.load(f)
    return list(blob.get("children", []))


class Daemon:
    """One supervice daemon under test, run in the foreground as our child."""

    def __init__(self, tmpdir: str, config_text: str):
        self.tmpdir = tmpdir
        self.config_path = os.path.join(tmpdir, "supervice.ini")
        self.socket_path = os.path.join(tmpdir, "p.sock")
        self.pidfile = os.path.join(tmpdir, "p.pid")
        self.state_file = os.path.join(tmpdir, "p.state.json")
        with open(self.config_path, "w") as f:
            f.write(config_text)
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self._readers: list[threading.Thread] = []

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "supervice.main", "-c", self.config_path, "-n"],
            cwd=self.tmpdir,
            env={**os.environ, "PYTHONPATH": REPO},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for stream in (self.proc.stdout, self.proc.stderr):
            t = threading.Thread(target=self._pump, args=(stream,), daemon=True)
            t.start()
            self._readers.append(t)

    def _pump(self, stream) -> None:
        for line in stream:
            self.lines.append(line.rstrip("\n"))

    def rpc(self, command: str, **kwargs):
        return rpc(self.socket_path, command, **kwargs)

    def status_of(self, name: str) -> dict | None:
        try:
            response = self.rpc("status")
        except OSError:
            return None
        for proc in response.get("processes", []):
            if proc["name"] == name:
                return proc
        return None

    def wait_state(self, name: str, states: tuple, timeout: float = 15.0) -> dict | None:
        def check():
            proc = self.status_of(name)
            return proc if proc and proc["state"] in states else None

        return wait_until(check, timeout, desc="%s in %s" % (name, states))

    def child_pids(self) -> list[int]:
        pids = []
        try:
            response = self.rpc("status")
        except OSError:
            return pids
        for proc in response.get("processes", []):
            if proc.get("pid"):
                pids.append(proc["pid"])
        return pids

    def ctl(self, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, "-m", "supervice.client", "-s", self.socket_path, *args],
            env={**os.environ, "PYTHONPATH": REPO},
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def stop(self, timeout: float = 20.0) -> None:
        """SIGTERM the daemon; escalate to exact-pid SIGKILLs if it wedges."""
        if self.proc is None:
            return
        stray = self.child_pids()
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
                for pid in stray:
                    kill_pid_hard(pid)

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def log_text(self) -> str:
        return "\n".join(self.lines)
