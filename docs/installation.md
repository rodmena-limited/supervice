# Installation

## Requirements

- **Python 3.10+** (uses modern type hints, `match` statements, and asyncio features)
- **Unix-like OS** — Linux or macOS (uses POSIX signals, Unix sockets, `fork()`)
- **No external dependencies** — Supervice is pure Python stdlib

## Install from Source

```bash
git clone https://github.com/yourusername/supervice.git
cd supervice
pip install .
```

This installs two command-line tools:

- `supervice` — The daemon process
- `supervicectl` — The control client

## Development Install

For development with documentation dependencies:

```bash
pip install -e ".[docs]"
```

## Verify Installation

```bash
supervice --help
supervicectl --help
```

Expected output:

```
usage: supervice [-h] [-c CONFIGURATION] [-n] [-l LOGFILE] [-e LOGLEVEL]

Supervice: A modern process supervisor

options:
  -h, --help            show this help message and exit
  -c CONFIGURATION, --configuration CONFIGURATION
                        Configuration file path
  -n, --nodaemon        Run in the foreground (default: daemonize)
  -l LOGFILE, --logfile LOGFILE
                        Log file path
  -e LOGLEVEL, --loglevel LOGLEVEL
                        Log level
```

## System Requirements

### Linux

Supervice uses `prctl(PR_SET_PDEATHSIG)` on Linux to ensure child processes
are killed when the parent dies. This requires `libc.so.6` to be available
(standard on all Linux distributions).

### macOS

Fully supported. The `PR_SET_PDEATHSIG` feature is Linux-only and is
gracefully skipped on macOS.

### Windows

Not supported. Supervice relies on POSIX signals, Unix domain sockets,
`fork()`, and `setsid()` which are not available on Windows.
