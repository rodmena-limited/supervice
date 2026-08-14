# Changelog

## 0.4.0

Correctness release for orphan handling, driven by cross-platform verification
on Linux, FreeBSD 15.1 and macOS. The theme is that several guarantees were
being *reported* rather than *delivered*, and none of the tests could have
noticed.

### Added

- **`reconcile` program option** (`auto` | `kill` | `warn` | `off`, default
  `auto`). At startup, before spawning anything, supervice checks the children
  it recorded last run and acts on any still alive — killing by process group,
  so it reaches the whole subtree.

  This fixes duplicate workers after a supervisor crash: with no memory of what
  it spawned, a restarting supervisor saw nothing running and started a second
  copy alongside the first. Measured on macOS: four orphans became five across
  a restart.

  It is **not** a macOS-only fix. The kernel clears `pdeathsig` across `fork()`
  on every platform, so the grandchildren of any forking program — a
  pre-forking web server, a worker pool — were never covered on Linux or
  FreeBSD either. `pdeathsig` and `reconcile` cover disjoint sets: one acts at
  `kill -9` speed on the direct child, the other covers the whole tree at the
  next start.

  Records are an *identity* (`name`, `pid`, `pgid`, start token), never a bare
  pid: the pid space wraps in minutes and reconciliation may run a week after
  the crash. Every uncertain case declines to signal. `pdeathsig = false` is
  honoured as the deliberate opt-out it is — the orphan is left alive, and the
  duplicate it implies is warned about rather than silently created.

- **`state_file` supervisor option** for the reconciliation record. Defaults
  beside the pidfile, deliberately per-supervisor: concurrent daemons with
  different configs must not share one, or one would kill another's children.

### Fixed

- **`pdeathsig` was only ever checked for *existence*, not function.**
  `pdeathsig_supported()` tested that libc loaded; the `prctl`/`procctl` return
  values were discarded, and ctypes does not raise on `-1`. On a host where the
  syscall fails — a jail, a sandbox — the config said `pdeathsig = true`, no
  warning was emitted, and children orphaned anyway. A startup probe now forks
  a throwaway child, performs the real call, reads it back, and warns if it did
  not take.

- **Warning when a program's command is a setuid/setgid binary.** The kernel
  clears the parent-death signal at exec for such images (measured on FreeBSD
  15.1), so that program loses `pdeathsig` even where the host is fine. This is
  per-command, so no host-wide probe can catch it. Does not affect the `user`
  option, which uses `setuid(2)` before exec.

- **Host-wide `pdeathsig` warnings are emitted once, not per program.** With
  the default of `true`, a twenty-program config on macOS produced twenty
  identical lines about a condition no per-program change could fix.

### Changed

- **Documentation corrected on two counts.** `installation.md` and `api.md`
  still described `pdeathsig` as Linux-only, three releases after FreeBSD
  support shipped. More seriously, the **one-generation limit** was recorded
  only in an internal audit file: the guarantee covers the direct child and
  never grandchildren. Now documented as two distinct situations — structural
  (a forking server; no configuration fixes it) and accidental (a wrapper
  script missing `exec`; one word fixes it).

- The macOS `pkill -f` launcher snippet previously recommended in the README is
  superseded by `reconcile`, and now carries the caveat that it matched on a
  command string rather than identity.

### Tests

- **The orphan guarantee had never been tested end to end.** Every `pdeathsig`
  test was a mock assertion against a fake libc or a check that a warning
  string appeared; nothing killed a supervisor and asked whether a child died.
  Two harnesses now do, with controls proven capable of failing on each
  platform by a platform-appropriate sabotage.

- Orphan tests must use a **silent** child: with a `stdout_logfile`, killing the
  supervisor closes the pipe and `SIGPIPE` reaps a chatty child by accident, so
  such a test passes whether or not `pdeathsig` works. The false-positive case
  is kept beside the real one so it is not reintroduced.

- The health-check fd-leak test now runs on macOS and the BSDs via `/dev/fd`
  instead of skipping everywhere but Linux.

- A scanner rejects `ps` invocations using comma-joined `-o` specs or `-e` for
  "all processes" — both are GNU/Darwin forms that silently misbehave on
  FreeBSD, and both had cost a real guard.

Verified on Linux 6.14/x86_64, FreeBSD 15.1-RELEASE-p2/amd64 (privileged and
unprivileged) and macOS 27.0/arm64: 162 collected, 0 failed on all three.

## 0.3.0

Portability release driven by `PORTABILITY-FREEBSD.md` — field notes from
migrating TokenGate to a native FreeBSD 15.1 host. Adds first-class FreeBSD
support, closes the silent-feature-evaporation gap, and ships a real CI
pipeline.

### Added

- **`pdeathsig` now works on FreeBSD** via `procctl(PROC_PDEATHSIG_CTL)` — the
  supervisor's death now SIGKILLs children there too, matching Linux
  (`prctl(PR_SET_PDEATHSIG)`).
- **Warning for inactive directives.** If a program requests `pdeathsig` on a
  platform that cannot honour it (e.g. macOS), supervice logs a warning at
  config load naming the directive and platform instead of failing silently.
- **`env_file` directive** (`[program:*]`). Points at `KEY=VALUE` secrets files
  (`#` comments and blank lines skipped, quotes stripped). Multiple
  comma-separated files are supported with later files winning; explicit
  `environment` values override `env_file`. Files are read as the supervisor
  before the privilege drop, so a `0600 root:root` secrets file can be
  delivered to an unprivileged child. A missing/unreadable file is a hard
  config-load error.
- **`pidfile = none`** (or empty) disables the pidfile lock — the correct
  setting when something else (e.g. FreeBSD `daemon(8)`) already supervises
  the supervisor.
- **Config-load validation of the pidfile and socket parent directories** —
  a missing/non-writable directory now fails at load with a clear message
  instead of crashing after daemonize.
- **`--version`** on both `supervice` and `supervicectl` (exit 0).
- **Per-spawn INFO line** with the resolved command, working directory, and
  uid, so first-run diagnosis on a new platform does not require guessing.
- **GitHub Actions CI** — ruff, mypy `--strict`, and the full pytest suite on
  Python 3.10–3.13 for every push and pull request.

### Documentation

- README platform matrix (Linux first-class; FreeBSD supported; macOS without
  `pdeathsig`) and a worked FreeBSD `rc.d` example that avoids the
  `daemon(8)` / `${name}_user` double-setuid trap.
- `PORTABILITY-FREEBSD.md` added to the repository as the source field notes.

## 0.2.1

Certification release. Adds the full critical-systems audit trail and the
v0.2.0 certification (`audit-2026-07-23.md`) to the repository, and removes an
unreachable code path in the client. No functional changes since 0.2.0.

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
