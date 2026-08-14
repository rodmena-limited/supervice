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

Supervice uses `prctl(PR_SET_PDEATHSIG)` to ensure a child process is killed
when the supervisor dies. This requires `libc.so.6` to be available (standard on
all Linux distributions).

See [`pdeathsig` and what it does *not* cover](configuration.md#pdeathsig-scope)
before relying on it: the guarantee is one generation deep.

### FreeBSD

Supported. The same guarantee is provided via
`procctl(PROC_PDEATHSIG_CTL)`, using `libc.so.7`. Verified on
FreeBSD 15.1-RELEASE/amd64.

Two packaging notes that catch people out:

- FreeBSD ports install **versioned** Python binaries. There is usually no
  `python3` on `PATH` — use `python3.12` (or whichever version you installed).
- supervice is commonly installed into a per-service virtualenv, e.g.
  `/opt/myservice/venv/bin/supervice`, rather than onto `PATH`. `command -v
  supervice` returning nothing does **not** mean it is missing.

### macOS

Supported for supervision. macOS has **no kernel equivalent of pdeathsig** — no
`prctl` and no `procctl` — so `pdeathsig = true` is accepted but inactive, and
supervice logs a warning at config load:

```
WARNING Program 'worker': pdeathsig requested but unsupported on darwin;
        children will survive an abrupt supervisor kill
```

If the supervisor is killed abruptly (`SIGKILL`, OOM, panic), its children
survive as orphans. A graceful `SIGTERM` shutdown stops children normally on
every platform.

> **Do not test this with a chatty child.** If a program has a
> `stdout_logfile`, supervice pipes its output; killing the supervisor closes
> the read end and the next write kills the child by `SIGPIPE`. The child is
> reaped **by accident**, not by pdeathsig, so the obvious test passes on macOS
> while the guarantee is absent. Test with a silent child. `tests/orphan_harness.py`
> does this and keeps the false-positive case alongside it.

### Windows

Not supported. Supervice relies on POSIX signals, Unix domain sockets,
`fork()`, and `setsid()` which are not available on Windows.
