# Startup reconciliation of orphaned children — EARS spec

**Ticket:** issuedb #12 · **Priority:** high · **Tag:** feature

## Requirements

- The supervisor shall record the program name, pid, process group id, and
  process start time of each child it spawns, to a state file written
  atomically.
- When the supervisor starts, for each record whose pid is alive **and** whose
  process start time matches the record, the supervisor shall treat that
  process as an orphan of a previous supervisor instance.
- Where a program is configured with `pdeathsig = true`, the supervisor shall
  terminate its identified orphans by process group before spawning
  replacements.
- Where a program is configured with `pdeathsig = false`, the supervisor shall
  **not** terminate its identified orphans.
- If a recorded pid is alive but its start time does not match the record, then
  the supervisor shall not signal that process.
- If the supervisor cannot read a process start time for a recorded pid, then
  the supervisor shall not signal that process and shall log a warning.
- If the state file is missing, unreadable or corrupt, then the supervisor
  shall log a warning and start normally without signalling anything.
- The supervisor shall not adopt orphans into its process table.

## Why this is not a macOS-only item

pdeathsig and reconciliation cover **disjoint** sets, not the same problem at
different strengths:

| | covers | when |
|---|---|---|
| `pdeathsig` | generation one only | at `kill -9` speed |
| reconciliation | the whole process tree | at next startup |

Neither subsumes the other. The kernel clears pdeathsig across fork on both
Linux and FreeBSD (measured — see [#9](09-docs-pdeathsig-reach.md)), so **a
Linux host with pdeathsig working perfectly still has an uncovered generation
two.**

The pgid anchor is already trusted by this codebase: children are spawned with
`start_new_session=True` (`process.py:467`) so each is a session leader from
birth, and teardown already uses `os.getpgid`/`os.killpg`
(`process.py:721-722`).

## Measured harm

macbook-admin-bd8e86, Darwin arm64, supervice 0.3.0: orphans accumulate across
supervisor restarts and the restart does not notice — 4 orphaned children before
restart, 5 after, because the supervisor spawned a duplicate. The flock pidfile
does not help: the lock dies with the process that held it. For a port-binding
service that is a hard failure; for a queue consumer it is a silent
duplicate-worker bug.

## Design decisions already made

**No adopt.** An orphan is PPID 1: we cannot `waitpid()` it, so no exit status
and no restart-on-exit; its stdout fds died with the old supervisor, so no logs.
Adoption yields a process reported RUNNING that the state machine cannot
manage — a status display that lies, which is worse than the orphan. Policy
surface is `reconcile = kill | warn | off`, default `kill`.

**Scoping to `pdeathsig = true` is a regression guard, not an optimisation.**
`pdeathsig = false` is a documented deliberate opt-out
(`audit-2026-07-23.md` M4) for operators who decided that surviving a supervisor
crash matters more than never orphaning. For those programs the orphan is the
*intended* outcome, and a blanket reconciler would silently invert a documented
guarantee on Linux and FreeBSD with no test failing.

## Blocking test — the refusal direction

Deliberately recycle a pid onto a decoy process and assert reconciliation
**leaves it alone**. A reconciler exercised only on "does it kill the orphans"
is the same shape as a cap tested only for blocking; its false-positive mode is
SIGKILLing a live process that was never ours. This requirement was derived
independently by macbook-admin-bd8e86 and auth-service-b080da.

## Supersedes

The launcher workaround at `README.md:316` uses `pkill -u myuser -f 'myapp'` —
pattern-matched on a command string, with exactly the PID-safety hole this spec
forbids. Update that section when this lands.

## Start-time source (resolved)

| Platform | Source | Resolution |
|---|---|---|
| Linux | `/proc/<pid>/stat` field 22 | clock ticks since boot |
| Darwin | `sysctl(CTL_KERN, KERN_PROC, KERN_PROC_PID)` → `kinfo_proc.kp_proc.p_starttime` | microseconds |
| FreeBSD | `kinfo_proc` via sysctl (`ki_start`) | microseconds |

**`ps -o lstart=` is unusable.** Measured on Darwin 27.0.0 arm64 over 12
children spawned back to back: 1 distinct `tv_sec` out of 12, but 12 distinct
`(sec, usec)` out of 12. Second resolution has *no* discriminating power on a
spawn burst.

Two further properties of the Darwin field, both measured:

- A reaped pid makes the sysctl return size 0 — a clean "gone" signal with no
  zombie ambiguity and no errno interpretation.
- pid 1 (launchd) reports `p_starttime = (10, 401885)` — 1970-01-01 + 10s —
  because launchd starts before the clock is set. The field is raw wall-clock at
  spawn, **not** monotonic-since-boot and **not** sanitised. Comparing a
  kernel-recorded value against the same kernel record later is safe; **do not
  derive an age or elapsed time from it.**

## PID reuse is not theoretical

Measured on Darwin 27.0.0 arm64 (macbook-admin-bd8e86), `fork`/`_exit` — the
cheapest possible spawn, establishing the adversarial floor:

```
kern.maxproc       = 12000
kern.maxprocperuid = 8000
sustained rate     = 140 spawns/sec
41,962 spawns in 300s -> pid space WRAPPED (95799 -> ... -> 39694)
```

The pid space wraps in under five minutes of sustained spawning; a full cycle
back to a specific pid is ~10–15 minutes at that rate. A parallel build, a test
suite, or a CI runner does this incidentally. So a supervisor restarted more
than ~10 minutes after a crash on a busy Mac can have a bare pid check match a
completely unrelated live process.

This is why the refusal test is blocking and why the reconciler fails closed
rather than falling back to a pid-only match.
