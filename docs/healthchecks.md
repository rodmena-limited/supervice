# Health Checks

Supervice supports health checking for managed processes. When a process fails
its health checks, it can be automatically restarted.

## Overview

Two health check types are available:

- **TCP** — Verifies that a TCP port is accepting connections
- **Script** — Runs a command and checks its exit code

Health checks run periodically while a process is in `RUNNING` state. If a
check fails `healthcheck_retries` consecutive times, the process is marked
`UNHEALTHY` and optionally restarted.

## TCP Health Checks

TCP health checks verify that a process is listening on a specific port.

```ini
[program:api]
command = python3 api_server.py
autostart = true
autorestart = true
healthcheck_type = tcp
healthcheck_port = 8080
healthcheck_host = 127.0.0.1
healthcheck_interval = 15
healthcheck_timeout = 5
healthcheck_retries = 3
healthcheck_start_period = 10
```

### How It Works

1. Supervice creates a non-blocking TCP socket
2. Attempts to connect to `healthcheck_host:healthcheck_port`
3. If the connection succeeds within `healthcheck_timeout`, the check passes
4. Connection refused, timeout, or other errors count as failures

### When to Use

- Web servers, API servers, database proxies
- Any process that listens on a TCP port
- When you want to verify the process is actually serving, not just running

## Script Health Checks

Script health checks run a custom command and interpret exit code 0 as healthy.

```ini
[program:worker]
command = python3 worker.py
autostart = true
autorestart = true
healthcheck_type = script
healthcheck_command = python3 check_worker.py
healthcheck_interval = 30
healthcheck_timeout = 10
healthcheck_retries = 3
healthcheck_start_period = 15
```

### How It Works

1. Supervice runs the `healthcheck_command` as a subprocess
2. Waits up to `healthcheck_timeout` seconds for it to complete
3. Exit code 0 = healthy, any other code = unhealthy
4. If the script times out, it is killed and counts as a failure

### When to Use

- Checking application-specific health (queue depth, memory usage, etc.)
- Verifying external dependencies (database connectivity, API availability)
- Custom health logic that can't be expressed as a TCP check

### Example Health Check Scripts

**Check an HTTP endpoint:**

```bash
#!/bin/bash
curl -sf http://localhost:8080/health > /dev/null
```

**Check a file exists (heartbeat):**

```bash
#!/bin/bash
find /tmp/worker.heartbeat -mmin -1 | grep -q .
```

**Check process memory usage:**

```python
#!/usr/bin/env python3
import psutil, sys
proc = psutil.Process()
if proc.memory_info().rss > 500 * 1024 * 1024:  # 500MB
    sys.exit(1)
```

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `healthcheck_type` | `none` | `none`, `tcp`, or `script` |
| `healthcheck_interval` | `30` | Seconds between checks |
| `healthcheck_timeout` | `10` | Seconds to wait for check to complete |
| `healthcheck_retries` | `3` | Consecutive failures before marking unhealthy |
| `healthcheck_start_period` | `10` | Grace period before first check (seconds) |
| `healthcheck_port` | *(none)* | TCP port to check (required for `tcp`) |
| `healthcheck_host` | `127.0.0.1` | TCP host to connect to |
| `healthcheck_command` | *(none)* | Command to run (required for `script`) |

## Health Check Lifecycle

```
Process starts
      │
      ▼
Wait healthcheck_start_period seconds
      │
      ▼
┌─────────────────────┐
│   Run health check   │◄──────────────────┐
└──────────┬──────────┘                    │
           │                               │
     ┌─────▼─────┐                         │
     │  Passed?   │── YES ──▶ Reset failure │
     └─────┬─────┘           counter       │
           │ NO                             │
           ▼                               │
   Increment failure                       │
     counter                               │
           │                               │
     ┌─────▼──────────┐                    │
     │ >= retries?     │── NO ─────────────┘
     └─────┬──────────┘     (wait interval)
           │ YES
           ▼
   Mark UNHEALTHY
           │
     ┌─────▼──────────┐
     │ autorestart?    │── NO ──▶ Stay UNHEALTHY
     └─────┬──────────┘
           │ YES
           ▼
   Kill + Restart process
```

## Events

Health checks emit events through the EventBus:

| Event | Trigger |
|-------|---------|
| `HEALTHCHECK_PASSED` | A health check succeeds |
| `HEALTHCHECK_FAILED` | A health check fails |
| `PROCESS_STATE_UNHEALTHY` | Process transitions to UNHEALTHY |

## Status Display

When health checks are configured, the `status` command shows a `HEALTH` column:

```bash
supervicectl status
```

```
NAME                 STATE      PID        UPTIME       HEALTH
--------------------------------------------------------------
api                  RUNNING    12345      1:23:45      OK
worker               RUNNING    12346      1:23:44      FAIL
other                RUNNING    12347      0:05         -
```

| Value | Meaning |
|-------|---------|
| `OK` | Last health check passed |
| `FAIL` | Health check threshold exceeded |
| `-` | No health checks configured or not yet checked |

## Validation

Health check configuration is validated at config parse time:

- `healthcheck_interval` must be at least 1
- `healthcheck_port` is required for `tcp` type (must be 1-65535)
- `healthcheck_command` is required for `script` type
- Numeric values must be non-negative
