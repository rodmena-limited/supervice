# 14 — Audit round 2: state-file clobber, startsecs recording hole, backoff-start latency, health-script leak, pump stall, probe crash, stale health flag

Ticket: issuedb #15. Date: 2026-08-30.

## EARS spec

- If a supervice instance fails startup because another instance holds the
  pidfile lock or the RPC socket, then it shall not modify the reconciliation
  state file (byte-identical before/after the failed start).
- When a child process has been spawned, the supervisor shall record it in the
  reconciliation state file within 1 s — before the `startsecs` gate elapses —
  so a supervisor SIGKILLed during the startsecs window can reconcile that
  child at the next start.
- When an explicit start request arrives while a process is waiting in
  BACKOFF, the supervisor shall begin the spawn within 1 s, not after the
  residual backoff delay (which can be up to 30 s).
- If a script health check times out, then the supervisor shall SIGKILL the
  check's entire process group, leaving 0 descendants of the check alive
  within 2 s.
- If writing child output to the configured log file fails, then the
  supervisor shall keep draining the child's output pipe, so the child never
  blocks on a full pipe (≥ 2 MB of child writes proceed after the log
  directory is removed).
- If the RPC socket probe receives a protocol-violating reply (e.g. a length
  prefix over 1 MB), then startup shall classify the socket as `unknown` and
  refuse to replace it, with the refusal message — not crash with an
  unhandled ValueError.
- When a process is not alive, the status report shall omit the health flag
  left over from its previous run.
- Every reproduction is persisted under `audit/evaluations/` with a
  `run_all.sh` runner; the full test suite, `mypy --strict`, and `ruff` stay
  green.

## Technical problems

1. Single-writer invariant on crash-recovery state: only the instance that
   owns the pidfile lock and the RPC socket may write the state file.
2. Recording liveness must span the whole child lifetime, not begin at the
   RUNNING gate.
3. Supervise-loop waits must wake on control requests, not only on system
   stop.
4. Subprocess subtree termination for health scripts (grandchildren of
   `sh -c`).
5. Pipe-backpressure decoupling: a log-sink failure must not stop pipe
   draining.
6. Protocol robustness at the socket-probe trust boundary.

## Solution domains

- POSIX process groups (`start_new_session` + `killpg`) — already this
  codebase's concept for child kill; extended to health scripts.
- `asyncio.Event` wakeups — existing pattern (`stop_event`, `_state_changed`).
- The existing event bus — new `PROCESS_SPAWNED` event.
- The pidfile flock as the ownership token — state writes gated on it.

## Alternatives (chosen vs rejected)

- **State clobber**: `_startup_committed` flag set after pidfile + socket are
  both acquired [CHOSEN] vs flocking the state file itself [REJECTED: a second
  lock file when the pidfile flock already is the ownership token] vs never
  snapshotting in shutdown [REJECTED: the shutdown snapshot is load-bearing
  for the timeout-expired shutdown that leaves children alive].
- **startsecs hole**: publish `PROCESS_SPAWNED` after
  `create_subprocess_exec`, supervisor subscribes [CHOSEN] vs subscribing to
  STARTING [REJECTED: fires before the child exists, no pid yet] vs a direct
  Supervisor callback into Process [REJECTED: couples Process to Supervisor;
  the bus exists].
- **Backoff wake**: dedicated wake event set by `start_process` and by
  `stop()` [CHOSEN] vs a shorter poll [REJECTED: still up to poll-interval
  latency, more idle churn] vs cancelling the supervise task [REJECTED:
  cancellation-as-control-flow, against the existing design].
- **Health-script leak**: `start_new_session=True` + `killpg` on timeout
  [CHOSEN, mirrors main child handling] vs `kill()` only [status quo: leaks
  grandchildren] vs a `timeout(1)` shell wrapper [REJECTED: external binary
  dependency].
- **Pump**: on write failure log once, keep draining, discard [CHOSEN] vs
  stopping the child [REJECTED: a log problem must not become an outage] vs
  unbounded write retry [REJECTED: blocks the loop].
- **Stale health flag**: omit `healthy` for a dead process at the reporting
  layer, like `uptime` [CHOSEN] vs resetting `is_healthy` in `kill()`
  [REJECTED: the last-known value is still useful internally].
