"""#12: startup reconciliation.

The refusal cases come FIRST in this file, deliberately. Reconciliation sends
SIGKILL to a process group; its dangerous failure mode is not "misses an orphan"
but "kills a process that was never ours". A reconciler exercised only on
does-it-kill is the same shape as a rate cap tested only for blocking.

Note what is NOT tested here: the kernel's pid allocator. An earlier plan was to
force a real pid recycle and prove reconciliation left the stranger alone. That
takes 35-40 minutes of maximum-rate forking, is load-dependent, and can only
produce INCONCLUSIVE or a slow pass -- and it tests the kernel rather than us.
The behaviour under test is the *comparison*, so the mismatch is constructed
directly. (Redesign proposed by macbook-admin-bd8e86 after their 25-minute run
returned INCONCLUSIVE.)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock

from supervice.config import ConfigValidationError, parse_config
from supervice.reconcile import (
    RECONCILE_AUTO,
    RECONCILE_KILL,
    RECONCILE_OFF,
    RECONCILE_WARN,
    ChildRecord,
    StateStore,
    _ps_token,
    decide,
    process_start_token,
    token_is_subsecond,
)


def spawn_sleeper() -> subprocess.Popen[bytes]:
    """Spawn the way the daemon does.

    start_new_session=True matters: supervice always spawns children as session
    leaders (process.py), so pgid == pid. Spawning with a plain Popen instead
    leaves every child sharing the test runner's process group, which is a
    configuration the product never produces -- and on Darwin, where the token
    is lstart + pgid, it makes distinct children collide for a reason that
    cannot occur in production.
    """
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )


def record_for(proc: subprocess.Popen[bytes], **kw: object) -> ChildRecord:
    token = process_start_token(proc.pid)
    assert token is not None, "precondition: a live pid must have a readable token"
    defaults: dict[str, object] = dict(
        name="victim",
        pid=proc.pid,
        pgid=os.getpgid(proc.pid),
        token=token,
        pdeathsig=True,
        reconcile=RECONCILE_AUTO,
    )
    defaults.update(kw)
    return ChildRecord(**defaults)  # type: ignore[arg-type]


class TestRefusal(unittest.TestCase):
    """The direction that must never regress: declining to kill."""

    def test_refuses_when_the_pid_is_gone(self) -> None:
        proc = spawn_sleeper()
        record = record_for(proc)
        proc.kill()
        proc.wait()
        time.sleep(0.2)

        # Precondition: prove the token really has become unreadable, otherwise
        # this test would pass simply because decide() never looked.
        self.assertIsNone(process_start_token(record.pid))

        verdict = decide(record)
        self.assertEqual(verdict.action, "skip")
        self.assertIn("gone", verdict.reason)

    def test_refuses_when_the_pid_is_alive_but_is_a_stranger(self) -> None:
        """The pid-recycling guard, constructed rather than waited for.

        Models what a real recycle looks like: our child died, and LATER a
        different process came to hold that pid. The two are never alive at the
        same time, so the discriminator is the start time having advanced.

        Constructed directly rather than by forcing a real pid wrap, which takes
        35-40 minutes of maximum-rate forking, is load-dependent, and tests the
        kernel's allocator rather than our comparison.
        """
        ours = spawn_sleeper()
        recorded_token = process_start_token(ours.pid)
        self.assertIsNotNone(recorded_token)
        ours.kill()
        ours.wait()

        # A tick must elapse, or "later" is indistinguishable from "now" at the
        # resolution of the clock. Without this the test could pass or fail on
        # scheduling luck, which is worse than not having it.
        time.sleep(0.25)

        stranger = spawn_sleeper()
        try:
            stranger_token = process_start_token(stranger.pid)
            self.assertIsNotNone(stranger_token)
            # Precondition: the later process must be distinguishable from the
            # earlier one, or every assertion below passes for the wrong reason.
            self.assertNotEqual(recorded_token, stranger_token)

            impostor = ChildRecord(
                name="victim",
                pid=stranger.pid,
                pgid=os.getpgid(stranger.pid),
                token=str(recorded_token),
                pdeathsig=True,
                reconcile=RECONCILE_KILL,
            )
            verdict = decide(impostor)
            self.assertEqual(verdict.action, "skip")
            self.assertIn("NOT ours", verdict.reason)

            # And it is still alive: the refusal was real, not a description.
            self.assertIsNone(stranger.poll())
        finally:
            stranger.kill()
            stranger.wait()

    def test_a_matching_token_is_required_not_merely_a_live_pid(self) -> None:
        """Guard against the token check being bypassed entirely.

        If decide() ever stopped comparing tokens and only checked liveness,
        every other test here would still pass. This one would not.
        """
        proc = spawn_sleeper()
        try:
            record = record_for(proc, reconcile=RECONCILE_KILL)
            self.assertEqual(decide(record).action, "kill")

            tampered = ChildRecord(
                name=record.name,
                pid=record.pid,
                pgid=record.pgid,
                token=record.token + "-tampered",
                pdeathsig=record.pdeathsig,
                reconcile=record.reconcile,
            )
            self.assertEqual(decide(tampered).action, "skip")
        finally:
            proc.kill()
            proc.wait()

    def test_refuses_when_reconcile_is_off(self) -> None:
        proc = spawn_sleeper()
        try:
            verdict = decide(record_for(proc, reconcile=RECONCILE_OFF))
            self.assertEqual(verdict.action, "skip")
        finally:
            proc.kill()
            proc.wait()

    def test_pdeathsig_false_is_warned_not_killed(self) -> None:
        """pdeathsig=false is a documented opt-out: the orphan is INTENDED.

        Killing here would silently invert a guarantee an operator chose. But
        the duplicate-spawn that follows is not intended, so it must be loud.
        """
        proc = spawn_sleeper()
        try:
            verdict = decide(record_for(proc, pdeathsig=False, reconcile=RECONCILE_AUTO))
            self.assertEqual(verdict.action, "warn")
            self.assertIn("duplicates", verdict.reason)
        finally:
            proc.kill()
            proc.wait()


class TestKillDecision(unittest.TestCase):
    """The other direction: it must actually act when it should."""

    def test_kills_a_genuine_orphan_under_auto(self) -> None:
        proc = spawn_sleeper()
        try:
            verdict = decide(record_for(proc, pdeathsig=True, reconcile=RECONCILE_AUTO))
            self.assertEqual(verdict.action, "kill")
        finally:
            proc.kill()
            proc.wait()

    def test_explicit_kill_overrides_pdeathsig_false(self) -> None:
        proc = spawn_sleeper()
        try:
            verdict = decide(record_for(proc, pdeathsig=False, reconcile=RECONCILE_KILL))
            self.assertEqual(verdict.action, "kill")
        finally:
            proc.kill()
            proc.wait()

    def test_explicit_warn_overrides_pdeathsig_true(self) -> None:
        proc = spawn_sleeper()
        try:
            verdict = decide(record_for(proc, pdeathsig=True, reconcile=RECONCILE_WARN))
            self.assertEqual(verdict.action, "warn")
        finally:
            proc.kill()
            proc.wait()


class TestStartToken(unittest.TestCase):
    def test_live_token_is_stable_across_reads(self) -> None:
        proc = spawn_sleeper()
        try:
            first = process_start_token(proc.pid)
            time.sleep(0.25)
            self.assertEqual(first, process_start_token(proc.pid))
        finally:
            proc.kill()
            proc.wait()

    def test_a_later_process_has_a_different_token(self) -> None:
        """The property the recycling guard actually depends on.

        NOT "any two processes differ" -- two started in the same clock tick
        share a start time, and that is harmless because simultaneous processes
        cannot share a pid. What must hold is that a process starting LATER is
        distinguishable, since that is what a recycled pid always is.
        """
        first = spawn_sleeper()
        first_token = process_start_token(first.pid)
        first.kill()
        first.wait()
        time.sleep(0.25)
        second = spawn_sleeper()
        try:
            self.assertNotEqual(first_token, process_start_token(second.pid))
        finally:
            second.kill()
            second.wait()

    def test_start_time_is_monotonic_so_recycling_cannot_collide(self) -> None:
        """Pin the assumption the whole design rests on.

        If start times ever stopped increasing, a recycled pid could reproduce
        an old token and reconciliation would kill a stranger. That would be a
        silent, catastrophic regression, so it is asserted rather than assumed.
        """
        a = spawn_sleeper()
        token_a = process_start_token(a.pid) or ""
        a.kill()
        a.wait()
        time.sleep(0.25)
        b = spawn_sleeper()
        try:
            token_b = process_start_token(b.pid) or ""
            field = "starttime:" if token_a.startswith("starttime:") else None
            if field is None:
                self.skipTest("non-/proc platform; ordering asserted by the ps token instead")
            val_a = int(token_a.split()[0].split(":")[1])
            val_b = int(token_b.split()[0].split(":")[1])
            self.assertGreater(val_b, val_a)
        finally:
            b.kill()
            b.wait()

    def test_token_is_stable_from_the_instant_of_spawn(self) -> None:
        """The token read immediately after fork must equal the one read later.

        Regression guard. The token briefly included the command line, which is
        empty between fork and exec (198 of 300 spawns on the development
        machine) and is rewritten by long-running servers that use setproctitle.
        Either made the recorded token stop matching a perfectly healthy child.
        It failed closed, so nothing was killed wrongly -- reconciliation just
        silently stopped working, for pre-forking servers most of all, which are
        exactly what it exists to protect.
        """
        mismatches = 0
        for _ in range(40):
            proc = spawn_sleeper()
            try:
                at_spawn = process_start_token(proc.pid)
                time.sleep(0.05)
                later = process_start_token(proc.pid)
                if at_spawn is None or at_spawn != later:
                    mismatches += 1
            finally:
                proc.kill()
                proc.wait()
        self.assertEqual(mismatches, 0, "token changed between spawn and later read")

    def test_ps_token_contains_a_real_pgid_not_a_format_fragment(self) -> None:
        """Guard the portable fallback against silently losing a component.

        `-o lstart=,pgid=` is a GNU/Darwin extension. FreeBSD parses it as ONE
        column, so the token became the literal 'ps:,pgid= <date>' -- identical
        for every process on the host that started in that second. It looked
        like a working token and was a wrong-kill door.

        Asserted on every platform, not just FreeBSD: the point is that the
        fallback's shape is checked wherever it can be checked.
        """
        proc = spawn_sleeper()
        try:
            token = _ps_token(proc.pid)
            self.assertIsNotNone(token)
            assert token is not None
            self.assertNotIn(",pgid=", token)
            self.assertIn("pgid:%d" % os.getpgid(proc.pid), token)
        finally:
            proc.kill()
            proc.wait()

    def test_format_fragment_in_ps_output_yields_no_token(self) -> None:
        """A token that cannot be trusted must fail closed, not be emitted.

        No token declines to act; a garbage token collides. On FreeBSD `ps`
        echoed the literal ",pgid=" instead of expanding it, and the old code
        folded that straight into an identity.
        """
        for bogus in ("", "   ", ",pgid=", ",pgid=\nFri Aug 14 18:01:16 2026"):
            with self.subTest(output=repr(bogus)):
                completed = subprocess.CompletedProcess([], 0, bogus, "")
                with mock.patch("subprocess.run", return_value=completed):
                    self.assertIsNone(_ps_token(os.getpid()))

    def test_pgid_is_never_parsed_out_of_ps_output(self) -> None:
        """The year in `lstart` must never be mistaken for a process group.

        `lstart` ends in a four-digit year, so any rule of the form "the last
        numeric field is the pgid" reads 2026 as a process group and produces a
        well-formed token with a year where the discriminator belongs. The pgid
        comes from os.getpgid instead, so this cannot happen.
        """
        completed = subprocess.CompletedProcess([], 0, "Fri Aug 14 18:01:16 2026", "")
        with mock.patch("subprocess.run", return_value=completed):
            token = _ps_token(os.getpid())
        self.assertIsNotNone(token)
        assert token is not None
        self.assertIn("pgid:%d" % os.getpgid(os.getpid()), token)
        self.assertNotIn("pgid:2026", token)

    def test_subsecond_claim_matches_the_actual_token(self) -> None:
        """token_is_subsecond() must describe the token this platform emits.

        If it ever claimed sub-second while the ps fallback were in use, the
        startup warning about the weaker guard would be suppressed on exactly
        the platform that needs it.
        """
        proc = spawn_sleeper()
        try:
            token = process_start_token(proc.pid) or ""
            if token_is_subsecond():
                self.assertFalse(
                    token.startswith("ps:"),
                    "claims sub-second resolution but is using the ps fallback",
                )
            else:
                self.assertTrue(token.startswith("ps:"))
        finally:
            proc.kill()
            proc.wait()

    def test_nonexistent_and_invalid_pids_yield_none(self) -> None:
        for pid in (0, -1, 999999):
            with self.subTest(pid=pid):
                self.assertIsNone(process_start_token(pid))


class TestStateStore(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="supervice-state-test-")
        self.path = os.path.join(self.dir, "state.json")

    def test_roundtrip(self) -> None:
        store = StateStore(self.path)
        records = [
            ChildRecord("a", 1, 2, "tok-a", True, RECONCILE_AUTO),
            ChildRecord("b", 3, 4, "tok-b", False, RECONCILE_WARN),
        ]
        store.save(records)
        self.assertEqual(store.load(), records)

    def test_missing_file_is_empty_not_an_error(self) -> None:
        self.assertEqual(StateStore(os.path.join(self.dir, "nope.json")).load(), [])

    def test_corrupt_file_is_empty_not_an_error(self) -> None:
        with open(self.path, "w") as f:
            f.write("{ this is not json")
        self.assertEqual(StateStore(self.path).load(), [])

    def test_wrong_version_is_ignored(self) -> None:
        with open(self.path, "w") as f:
            json.dump({"version": 999, "children": [{"name": "x"}]}, f)
        self.assertEqual(StateStore(self.path).load(), [])

    def test_one_bad_record_does_not_disarm_the_others(self) -> None:
        """A malformed entry must not silently drop reconciliation entirely."""
        with open(self.path, "w") as f:
            json.dump(
                {
                    "version": 1,
                    "children": [
                        {"name": "broken"},  # missing everything else
                        {
                            "name": "good",
                            "pid": 7,
                            "pgid": 7,
                            "token": "t",
                            "pdeathsig": True,
                            "reconcile": "auto",
                        },
                    ],
                },
                f,
            )
        loaded = StateStore(self.path).load()
        self.assertEqual([r.name for r in loaded], ["good"])

    def test_save_is_atomic_leaving_no_temp_files(self) -> None:
        store = StateStore(self.path)
        store.save([ChildRecord("a", 1, 2, "t", True, RECONCILE_AUTO)])
        leftovers = [n for n in os.listdir(self.dir) if n.startswith(".supervice-state-")]
        self.assertEqual(leftovers, [])

    def test_default_path_is_per_supervisor_not_global(self) -> None:
        """Two configs must never share a store, or one kills the other's children."""
        a = StateStore.default_path("/var/run/alpha.pid")
        b = StateStore.default_path("/var/run/beta.pid")
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith(".alpha.state.json"))
        self.assertTrue(os.path.isabs(a))


class TestReconcileConfigParsing(unittest.TestCase):
    CONFIG = """
[supervice]
pidfile=

[program:api]
command = sleep 60
reconcile = %s
"""

    def _parse(self, value: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".conf")
        with os.fdopen(fd, "w") as f:
            f.write(self.CONFIG % value)
        try:
            return parse_config(path).programs[0].reconcile
        finally:
            os.unlink(path)

    def test_valid_values_accepted(self) -> None:
        for value in ("auto", "kill", "warn", "off", "KILL", " warn "):
            with self.subTest(value=value):
                self.assertEqual(self._parse(value), value.strip().lower())

    def test_invalid_value_rejected_at_load(self) -> None:
        """Fail at config load, not during a post-crash startup nobody watches."""
        with self.assertRaises(ConfigValidationError) as ctx:
            self._parse("destroy-everything")
        self.assertIn("reconcile", str(ctx.exception))

    def test_default_is_auto(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".conf")
        with os.fdopen(fd, "w") as f:
            f.write("[supervice]\npidfile=\n\n[program:api]\ncommand = sleep 60\n")
        try:
            self.assertEqual(parse_config(path).programs[0].reconcile, RECONCILE_AUTO)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
