#!/usr/bin/env python3
"""CLAIM: a pre-existing socket whose occupant replies protocol-violating
garbage (e.g. a length prefix over the 1 MB cap) is classified 'unknown' and
startup refuses to replace it with the documented message — it does not crash
with an unhandled ValueError, and it does not remove the socket.
"""
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probelib import (  # noqa: E402
    PYTHON,
    REPO,
    info,
    mkdtemp,
    preflight,
    probe_error,
    probe_fail,
    probe_pass,
    wait_until,
)

NAME = "foreign_socket_reply_is_refused_cleanly"

FOREIGN = textwrap.dedent(
    """\
    import socket, sys
    path = sys.argv[1]
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(4)
    sys.stdout.write("ready\\n")
    sys.stdout.flush()
    while True:
        conn, _ = server.accept()
        try:
            conn.recv(4096)
            conn.sendall(b"\\xff\\xff\\xff\\xffgarbage")
        finally:
            conn.close()
    """
)

CONFIG = """
[supervice]
pidfile=%(dir)s/p.pid
socket=%(dir)s/p.sock
state_file=%(dir)s/p.state.json

[program:svc]
command=sleep 300
autostart=true
startsecs=0
"""


def main() -> None:
    preflight()
    tmpdir = mkdtemp()
    sock = os.path.join(tmpdir, "p.sock")
    foreign_path = os.path.join(tmpdir, "foreign.py")
    with open(foreign_path, "w") as f:
        f.write(FOREIGN)
    with open(os.path.join(tmpdir, "supervice.ini"), "w") as f:
        f.write(CONFIG % {"dir": tmpdir})

    foreign = subprocess.Popen(
        [sys.executable, foreign_path, sock],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert foreign.stdout is not None
        if not wait_until(lambda: os.path.exists(sock), timeout=10):
            probe_error(NAME, "foreign server never bound the socket")
        info("foreign occupant bound %s and replies garbage" % sock)

        result = subprocess.run(
            [PYTHON, "-m", "supervice.main", "-c", os.path.join(tmpdir, "supervice.ini"), "-n"],
            cwd=tmpdir,
            env={**os.environ, "PYTHONPATH": REPO},
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            probe_fail(NAME, "daemon STARTED over an occupied socket it could not verify")
        if "could not be verified as stale" not in output:
            probe_fail(
                NAME,
                "refusal did not use the 'unknown socket' path; daemon said: %r"
                % output.strip()[-300:],
            )
        if not os.path.exists(sock):
            probe_fail(NAME, "the foreign occupant's socket file was removed")
        probe_pass(
            NAME,
            "garbage reply classified unknown; startup refused with the documented "
            "message and left the socket alone (rc=%d)" % result.returncode,
        )
    finally:
        foreign.kill()
        foreign.wait(timeout=10)


if __name__ == "__main__":
    main()
