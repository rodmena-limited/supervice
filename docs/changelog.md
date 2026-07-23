# Changelog

## 0.2.0

Hardening release driven by a full critical-systems audit
(`audit-2026-07-23.md`). Every confirmed defect ships with a regression test
(`tests/test_audit_regressions.py`); the suite, `mypy --strict`, and `ruff`
are clean, and the fixes were verified end-to-end (daemon + CLI battery, race
stress runs).

### Fixed

- **Config values containing `%` no longer crash the parser.** Interpolation is
  disabled, so the documented `%(process_num)s` log templating actually works
  and commands like `date +%s` are configurable.
- **A stop that lands mid-spawn now stops the child.** `spawn()` re-checks for
  a stop request after the fork; `stop`/`stopgroup` wait for the state to
  settle and report the state actually reached. Previously the child could be
  left running forever after an acknowledged stop.
- **`reload` applies changed program configs.** Edited settings take effect on
  the next restart, as the log message always claimed. Reloading an unchanged
  file no longer misreports `numprocs > 1` programs as changed on every reload.
- **Starting a FATAL process works and reports the truth.** Previously it
  always returned an INTERNAL_ERROR while starting the process anyway.
- **`start` replies are truthful.** "Started X" is only returned when the
  process actually reached RUNNING; otherwise an error names the state reached.
- **Health-check restarts are paced and bounded.** They now go through the
  backoff machinery and escalate to FATAL after `startretries` consecutive
  health restarts (a passing check resets the counter). Previously a
  persistently failing check caused an unbounded kill/respawn storm.
- **Transient spawn errors are retried.** fd exhaustion, a briefly missing log
  directory, or a binary mid-deploy now retry under backoff/`startretries`
  instead of instantly marking the process permanently FATAL. Permanent errors
  (unknown user, unparseable command, permission denied) still fail fast.
- **Manual stops settle cleanly.** No more transient/terminal BACKOFF states or
  polluted retry counters after an operator stop; an unkillable (D-state)
  process stays STOPPING instead of being reported STOPPED (preventing
  duplicate instances).
- **Startup binds the RPC socket before spawning children**, so a conflicting
  instance is detected before any duplicate process is forked, and startup
  failures run the full shutdown path.
- **Supervision loops have an exception safety net** — an unexpected internal
  error marks the process FATAL (visible, alertable) instead of silently
  freezing its supervision.

### Changed

- **`RUNNING` is now honest:** a process stays `STARTING` until it survives
  `startsecs` (supervisord semantics). Exits inside the window count against
  `startretries`; after reaching RUNNING, restarts are paced at 1s.
- **Backoff delay** is now the retry count in seconds (1s, 2s, …, capped 30s),
  no longer coupled to `startsecs`.
- **Default RPC socket moved out of `/tmp`** (squatting/impersonation risk) to
  `$XDG_RUNTIME_DIR/supervice.sock`, `/run/supervice.sock` for root, else
  `~/.supervice.sock`. The daemon refuses to replace a socket it cannot prove
  stale and warns when the socket directory is world-writable.
- **Default daemon `logfile` is now empty:** foreground mode logs to stdout
  (container-friendly); daemon mode falls back to `supervice.log`.
- **User switching no longer uses `preexec_fn`.** Users are resolved in the
  parent and switched via `subprocess`'s native `user`/`group`/`extra_groups`
  (thread-safe). The pdeathsig hook no longer dlopens libc post-fork.
- **Script health checks run as the program's `user`**, never as the
  (possibly root) daemon.

### Added

- **Child log rotation:** stdout/stderr are captured via pipes and rotated at
  `stdout_logfile_maxbytes`/`stderr_logfile_maxbytes` with
  `*_logfile_backups` kept.
- **`%(process_num)s` expansion in `command` and `environment`** (in addition
  to logfiles), so `numprocs` instances can bind distinct ports.
- **`pdeathsig` program option** to opt out of kill-children-on-supervisor-death.
- **`supervicectl --timeout`** (default 30s) so a wedged daemon cannot hang the
  CLI; group commands report per-process failures.
- **Load-time validation:** unparseable `command` values and `[group:*]`
  members that reference unknown programs are rejected at config load.

## 0.1.1

Bug-fix release addressing issues found in a full source audit. All fixes ship
with regression tests; `mypy --strict` and `ruff` remain clean.

### Fixed

- **Exit codes 126/127 no longer force `FATAL`.** A supervised program that
  legitimately exits with code 126 or 127 was permanently marked `FATAL` and
  never restarted, because those codes were overloaded as preexec-failure
  sentinels. Preexec (user-switch) failures are now detected out-of-band via a
  dedicated `CLOEXEC` status pipe, so the program's real exit code is always
  honoured.
- **`reload` now reconciles process groups.** Adding, removing, or renaming a
  `[group:*]` section — or moving a program between groups — is now reflected in
  the live group table, so `startgroup`/`stopgroup` work after a reload.
  Previously only program add/remove was handled and group changes were ignored.
- **`start` no longer races the supervision loop.** The state-change signal used
  a `set()`/`clear()` anti-pattern that could make `supervicectl start` block up
  to 5 seconds under load; it now uses a race-free clear-then-check-then-wait.
- **Refuse to hijack a live RPC socket.** Startup previously unlinked any
  existing socket unconditionally; it now probes for a live instance first and
  refuses to start if one responds (relevant when the pidfile lock is disabled).
- **PID file is only removed if it holds our PID**, so a foreign pidfile is never
  deleted.
- **PID file lock is released last during shutdown**, after all children stop, so
  a restarting instance can't orphan the old daemon's children.
- **TCP health check no longer leaks a socket fd** when the check is cancelled.
- **Config logger setup consolidated** into the entry point (no longer performed
  inside `load_config`), fixing fragile fd/handler ordering.
- **Unreadable config files raise** instead of being silently ignored.
- **Empty RPC requests** return a clear `EMPTY_REQUEST` error instead of a
  confusing "Invalid JSON".
- Warn when `numprocs > 1` is combined with a non-templated log file (output
  would silently interleave).

### Security

- Documented that `healthcheck_command` runs through a shell and executes
  arbitrary code as the daemon user; treat the config file as trusted input.

## 0.1.0 (Unreleased)

Initial release.

### Features

- Async process supervision built on Python `asyncio`
- Zero external dependencies — pure Python stdlib
- INI-based configuration compatible with Supervisor conventions
- Process lifecycle management with state machine (STOPPED, STARTING, RUNNING, BACKOFF, STOPPING, EXITED, FATAL, UNHEALTHY)
- Multiple process instances via `numprocs`
- Process groups with batch start/stop operations
- TCP and script-based health checks with configurable intervals, timeouts, and retries
- Auto-restart on process exit and health check failure
- Configurable start retries with FATAL state on exhaustion
- Hot configuration reload (add/remove programs without restart)
- Unix socket RPC with length-prefixed JSON protocol
- CLI control tool (`supervicectl`) with status, start, stop, restart, reload commands
- Graceful and forced restart (`--force` flag for SIGKILL)
- Process group kill (kills entire process tree, not just main PID)
- Double-fork daemonization with PID file locking (`fcntl.flock`)
- Log rotation via `RotatingFileHandler`
- Per-process uptime tracking and display
- User switching with `setuid`/`setgid` (requires root)
- Linux orphan prevention via `prctl(PR_SET_PDEATHSIG)`
- Log file path substitution with `%(process_num)s`
- Quote-aware environment variable parsing
- SIGHUP handling (logged and ignored)
- Bounded event queue with backpressure (prevents memory exhaustion)
- Restrictive Unix socket permissions (`0o600`)
- Full `mypy --strict` compliance
- Comprehensive test suite (63 tests)
