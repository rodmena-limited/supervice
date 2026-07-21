# Changelog

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
