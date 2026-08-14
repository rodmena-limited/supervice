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

## Open question

Start-time granularity on Darwin: `ps -o lstart=` is second-resolution, thin as
a PID-reuse guard where the pid space is small and recycles fast. Determine
whether `kinfo_proc` via `sysctl(KERN_PROC_PID)` exposes microsecond start time.
Linux uses `/proc/<pid>/stat` field 22.
