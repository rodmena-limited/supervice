# Supervice

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy--strict-blue.svg)](https://mypy-lang.org/)
[![Documentation](https://readthedocs.org/projects/supervice/badge/?version=latest)](https://supervice.readthedocs.io)

A modern, lightweight, and fully async process supervisor for Unix-like systems.
**Zero dependencies. Pure Python. Production-ready.**

Supervice manages long-running processes with automatic restart, health checking,
process grouping, hot config reload, and a Unix socket RPC interface — all built
on Python's `asyncio` with no external packages.

---

## Features

- **Async-first** — Built entirely on `asyncio` for efficient I/O multiplexing
- **Zero dependencies** — Pure Python stdlib; nothing to install beyond Python 3.10+
- **Process groups** — Organize related processes and control them as a unit
- **Health checks** — TCP connectivity and script-based health monitoring with auto-restart
- **Hot reload** — Add/remove programs without restarting the daemon (`supervicectl reload`)
- **Daemon mode** — Proper double-fork daemonization with PID file locking
- **Graceful shutdown** — SIGTERM/SIGINT triggers orderly stop of all child processes
- **Process group kill** — Stops entire process trees, not just the main PID
- **Log rotation** — Built-in `RotatingFileHandler` with configurable size and backup count
- **Uptime tracking** — Per-process wall-clock uptime displayed in status output
- **Retry with backoff** — Configurable start retries with automatic FATAL state on exhaustion
- **User switching** — Run processes as a specific user (requires root)
- **Type-safe** — Fully type-hinted, passes `mypy --strict`

## Installation

**Requirements:** Python 3.10+ on a Unix-like OS (Linux, macOS).

```bash
# From source
git clone https://github.com/yourusername/supervice.git
cd supervice
pip install .

# Development install (includes docs dependencies)
pip install -e ".[docs]"
```

## Quick Start

### 1. Create a configuration file

```ini
# supervisord.conf
[supervice]
loglevel = INFO
logfile = supervice.log
pidfile = supervice.pid

[program:webapp]
command = python3 -u app.py
autostart = true
autorestart = true
stdout_logfile = webapp.log
stderr_logfile = webapp_err.log

[program:worker]
command = python3 -u worker.py
numprocs = 4
autostart = true
autorestart = true
stdout_logfile = worker_%(process_num)s.log
stderr_logfile = worker_err_%(process_num)s.log
```

### 2. Start the daemon

```bash
# Foreground (development)
supervice -c supervisord.conf -n

# Background (production — default)
supervice -c supervisord.conf
```

### 3. Control processes

```bash
# Check status
supervicectl status

# Output:
# NAME                 STATE      PID        UPTIME
# --------------------------------------------------------
# webapp               RUNNING    12345      1:23:45
# worker:00            RUNNING    12346      1:23:44
# worker:01            RUNNING    12347      1:23:44
# worker:02            RUNNING    12348      1:23:44
# worker:03            RUNNING    12349      1:23:44

# Start / stop / restart individual processes
supervicectl stop worker:00
supervicectl start worker:00
supervicectl restart worker:00
supervicectl restart worker:00 --force   # SIGKILL instead of graceful

# Group operations
supervicectl stopgroup workers
supervicectl startgroup workers

# Hot reload (add/remove programs without restart)
supervicectl reload

# Use a custom socket path
supervicectl -s /var/run/supervice.sock status
```

## Configuration Reference

### `[supervice]` — Global settings

| Option | Default | Description |
|--------|---------|-------------|
| `logfile` | *(stdout)* | Daemon log file; empty logs to stdout in foreground, `supervice.log` when daemonized |
| `loglevel` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `pidfile` | `supervice.pid` | Path to the PID/lock file; set to `none` (or empty) to disable |
| `socket` | *(runtime dir)* | RPC socket; defaults to `$XDG_RUNTIME_DIR/supervice.sock` (root: `/run/supervice.sock`, else `~/.supervice.sock`) |
| `shutdown_timeout` | `30` | Seconds to wait for graceful shutdown |
| `log_maxbytes` | `52428800` | Max log file size before rotation (bytes, 0 = no rotation) |
| `log_backups` | `10` | Number of rotated log backup files to keep |

### `[program:NAME]` — Process definitions

| Option | Default | Description |
|--------|---------|-------------|
| `command` | *(required)* | Command to execute (supports shell-style quoting) |
| `numprocs` | `1` | Number of instances to run (creates `NAME:00`, `NAME:01`, ...) |
| `autostart` | `true` | Start automatically when daemon starts |
| `autorestart` | `true` | Restart automatically when process exits |
| `startsecs` | `1` | Seconds a process must run to be considered successfully started |
| `startretries` | `3` | Max consecutive start attempts before entering FATAL state |
| `stopsignal` | `TERM` | Signal to send when stopping (`TERM`, `INT`, `QUIT`, `KILL`, etc.) |
| `stopwaitsecs` | `10` | Seconds to wait after stop signal before sending SIGKILL |
| `stdout_logfile` | *(none)* | File for stdout (rotated by the daemon; supports `%(process_num)s`) |
| `stderr_logfile` | *(none)* | File for stderr (rotated by the daemon; supports `%(process_num)s`) |
| `stdout_logfile_maxbytes` / `stderr_logfile_maxbytes` | `50MB` | Child log rotation threshold (0 disables) |
| `stdout_logfile_backups` / `stderr_logfile_backups` | `10` | Rotated child log backups to keep |
| `pdeathsig` | `true` | Linux/FreeBSD: SIGKILL the **direct child** if the supervisor dies. One generation only — grandchildren are never covered; see [docs](docs/configuration.md#pdeathsig-scope) |
| `reconcile` | `auto` | Orphans of a crashed supervisor found at startup: `auto`, `kill`, `warn`, `off`. Matches on identity, not pid — see [docs](docs/configuration.md#reconcile) |
| `environment` | *(none)* | Environment variables: `KEY=VAL,KEY2="val with,comma"` |
| `env_file` | *(none)* | Comma-separated `KEY=VALUE` secrets files (`#` comments, quotes stripped); read as the supervisor before the privilege drop. Later files win; `environment` overrides `env_file` |
| `directory` | *(none)* | Working directory for the process |
| `user` | *(none)* | Run as this user (requires root privileges) |

### `[group:NAME]` — Process groups

| Option | Default | Description |
|--------|---------|-------------|
| `programs` | *(required)* | Comma-separated list of program names |

### Health check options (per program)

| Option | Default | Description |
|--------|---------|-------------|
| `healthcheck_type` | `none` | Health check type: `none`, `tcp`, `script` |
| `healthcheck_interval` | `30` | Seconds between health checks |
| `healthcheck_timeout` | `10` | Seconds to wait for health check response |
| `healthcheck_retries` | `3` | Consecutive failures before marking unhealthy |
| `healthcheck_start_period` | `10` | Seconds to wait before starting health checks |
| `healthcheck_port` | *(none)* | TCP port to check (required for `tcp` type) |
| `healthcheck_host` | `127.0.0.1` | TCP host to check |
| `healthcheck_command` | *(none)* | Script to run (required for `script` type) |

**Example with health checks:**

```ini
[program:api]
command = python3 -u api_server.py
autostart = true
autorestart = true
healthcheck_type = tcp
healthcheck_port = 8080
healthcheck_interval = 15
healthcheck_retries = 3
healthcheck_start_period = 5
```

## Process States

```
STOPPED ──┐
EXITED  ──┼──> STARTING ──> RUNNING ──> STOPPING ──> STOPPED
FATAL   ──┤                    │                        │
BACKOFF ──┘                    │                     EXITED
                               ▼
                           UNHEALTHY (health check failures)
                               │
                               ▼
                         auto-restart (if autorestart=true)
```

| State | Description |
|-------|-------------|
| `STOPPED` | Process is not running (initial or manually stopped) |
| `STARTING` | Process has been spawned, waiting for confirmation |
| `RUNNING` | Process is running and healthy |
| `BACKOFF` | Process exited too quickly, waiting before retry |
| `STOPPING` | Stop signal sent, waiting for process to exit |
| `EXITED` | Process has exited (normally or abnormally) |
| `FATAL` | Process failed to start after exhausting retries |
| `UNHEALTHY` | Process is running but health checks are failing |

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   supervice                      │
│                                                  │
│  ┌──────────┐    ┌───────────┐    ┌──────────┐  │
│  │  Config   │───▶│ Supervisor │───▶│ Process  │  │
│  │  Parser   │    │   (core)   │    │ Manager  │  │
│  └──────────┘    └─────┬─────┘    └────┬─────┘  │
│                        │               │         │
│                   ┌────▼────┐    ┌─────▼─────┐  │
│                   │   RPC    │    │  EventBus  │  │
│                   │  Server  │    │  (pub/sub) │  │
│                   └────┬────┘    └───────────┘  │
│                        │                         │
└────────────────────────┼─────────────────────────┘
                         │ Unix Socket
                    ┌────▼────┐
                    │supervice│
                    │  ctl    │
                    └─────────┘
```

## Platform Support

| Platform | Status |
|----------|--------|
| Linux | **First-class** — full feature set, including `pdeathsig` via `prctl(2)` (direct child only) |
| FreeBSD | **Supported** (15.x, 13.x) — all features, including `pdeathsig` via `procctl(2)` (direct child only); see the FreeBSD notes below |
| macOS | Supported for supervision, **without `pdeathsig`** — no kernel equivalent exists; supervice logs a warning if you request it. Children survive an abrupt supervisor kill |

Field notes from the first production FreeBSD deployment live in
[`PORTABILITY-FREEBSD.md`](PORTABILITY-FREEBSD.md).

### FreeBSD: rc.d integration

Run the supervisor itself as root from `rc.d` with **no** `${name}_user` and
**no** `daemon -u` — FreeBSD's `rc.subr` wraps the whole command in `su -m`
when `${name}_user` is set, so combining it with `daemon -u <user>` runs
`setuid` twice and fails with `EPERM` (and `daemon -f` swallows the error).
Let supervice drop privileges per program with its `user =` directive
instead.

A worked `/usr/local/etc/rc.d/supervice` unit (adapted from production):

```sh
#!/bin/sh
# PROVIDE: supervice
# REQUIRE: LOGIN
# KEYWORD: shutdown

. /etc/rc.subr

name="supervice"
rcvar="supervice_enable"

load_rc_config $name

command="/usr/local/bin/supervice"
command_args="-c /usr/local/etc/supervice.ini"

run_rc_command "$1"
```

```ini
; /usr/local/etc/supervice.ini
[supervice]
logfile = /var/log/supervice/supervice.log
; daemon(8) is NOT used with -p here; let supervice own its pidfile.
; If an outer supervisor already owns it, use: pidfile = none
pidfile  = /var/run/supervice/supervice.pid
socket   = /var/run/supervice/supervice.sock

[program:api]
command = /usr/local/bin/myapp
directory = /usr/local/myapp
user = myapp
startsecs = 3
startretries = 3
stopsignal = TERM
stopwaitsecs = 10
healthcheck_type = tcp
healthcheck_port = 8080
```

Notes for FreeBSD operators:

- If something else (e.g. `daemon(8) -p`) already writes the pidfile, set
  `pidfile = none` — `daemon(8)` writes it as root before dropping privileges,
  so a second writer fails with `EPERM`/`EACCES`.
- If you run supervice under `daemon(8)`, start it with **`-r`** (restart on
  death) and **`-P`** (pidfile holds daemon's *own* pid, not the child's):
  pointing the pidfile at the child means `service stop` kills supervice and
  daemon immediately restarts it.
- The pidfile and socket parent directories must exist and be writable before
  `supervice` starts; config load now fails with a clear message otherwise.
- Export `HOME` if your program reads client certificates from
  `$HOME/.postgresql` or similar (asyncpg does; a wrapper script can set it).

### macOS

macOS has no kernel pdeathsig equivalent. `pdeathsig = true` is accepted but
inactive, and supervice logs a warning at config load. If the supervisor is
killed abruptly its children survive as orphans, and a later restart will spawn
duplicates alongside them.

If orphan reaping is essential today, a launcher can reap before exec'ing the
supervisor:

```sh
pkill -u myuser -f 'myapp' || true
exec supervice -c /etc/supervice.ini -n
```

> **Know what this costs before you use it.** `pkill -f` matches on a command
> string, not on identity. It will kill *any* process whose command line
> matches — including one that merely looks similar, and including a process
> that happens to be a different program entirely. There is no check that the
> process is one supervice started. Scope the pattern as tightly as you can, and
> prefer a dedicated user account so `-u` does real work.
>
> Note also the `exec` on the last line: without it the supervisor would be a
> child of this script, adding a generation. The same rule applies to your own
> program wrappers — see
> [`pdeathsig` scope](docs/configuration.md#pdeathsig-scope).

A supervisor-side replacement that matches on `{pid, pgid, start-time}` rather
than on a command string is tracked as issue #12; until it lands, the snippet
above is the available option and the caveat is real.

**Testing orphan behaviour on macOS:** use a **silent** child. A program with a
`stdout_logfile` is reaped by `SIGPIPE` when the supervisor dies — by accident,
not by pdeathsig — so the obvious test passes while the guarantee is absent.
`python3 tests/orphan_harness.py` does this correctly and keeps the
false-positive case beside it.

## Documentation

Full documentation is available at [supervice.readthedocs.io](https://supervice.readthedocs.io).

## Development

```bash
# Run tests
python3 -m pytest tests/ -v

# Type checking (strict mode)
mypy --strict supervice/

# Linting
ruff check supervice/

# Formatting
ruff format supervice/

# Build documentation locally
pip install -e ".[docs]"
cd docs && make html
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.