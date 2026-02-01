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
| `logfile` | `supervice.log` | Path to the daemon log file |
| `loglevel` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `pidfile` | `supervice.pid` | Path to the PID/lock file |
| `socket` | `/tmp/supervice.sock` | Unix socket path for RPC |
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
| `stdout_logfile` | *(none)* | File for stdout (supports `%(process_num)s` substitution) |
| `stderr_logfile` | *(none)* | File for stderr (supports `%(process_num)s` substitution) |
| `environment` | *(none)* | Environment variables: `KEY=VAL,KEY2="val with,comma"` |
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