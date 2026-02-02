# Changelog

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
