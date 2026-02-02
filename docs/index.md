# Supervice

**A modern, lightweight, and fully async process supervisor for Unix-like systems.**

Supervice manages long-running processes with automatic restart, health checking,
process grouping, hot config reload, and a Unix socket RPC interface — all built
on Python's `asyncio` with zero external dependencies.

## Key Features

- **Async-first** — Built entirely on `asyncio` for efficient I/O multiplexing
- **Zero dependencies** — Pure Python stdlib only; nothing to install beyond Python 3.10+
- **Process groups** — Organize processes and control them as a unit
- **Health checks** — TCP and script-based health monitoring with auto-restart
- **Hot reload** — Add/remove programs without restarting the daemon
- **Daemon mode** — Double-fork daemonization with PID file locking
- **Graceful shutdown** — Orderly stop of all child process trees
- **Log rotation** — Built-in rotating file handler with configurable limits
- **Type-safe** — Fully type-hinted, passes `mypy --strict`

## Getting Started

```bash
pip install supervice
```

```ini
# supervisord.conf
[supervice]
logfile = supervice.log
pidfile = supervice.pid

[program:myapp]
command = python3 -u app.py
autostart = true
autorestart = true
```

```bash
supervice -c supervisord.conf -n    # foreground
supervicectl status                  # check status
```

```{toctree}
:maxdepth: 2
:caption: User Guide

installation
quickstart
configuration
cli
healthchecks
```

```{toctree}
:maxdepth: 2
:caption: Reference

architecture
api
```

```{toctree}
:maxdepth: 1
:caption: Project

contributing
changelog
```
