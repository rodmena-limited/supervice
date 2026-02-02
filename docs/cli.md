# CLI Reference

Supervice provides two command-line tools: `supervice` (the daemon) and
`supervicectl` (the control client).

## `supervice` — Daemon

The main daemon process that manages child processes.

### Usage

```bash
supervice [-h] [-c CONFIG] [-n] [-l LOGFILE] [-e LOGLEVEL]
```

### Options

| Option | Description |
|--------|-------------|
| `-c`, `--configuration` | Path to configuration file (default: `supervisord.conf`) |
| `-n`, `--nodaemon` | Run in foreground instead of daemonizing |
| `-l`, `--logfile` | Override log file path from config |
| `-e`, `--loglevel` | Override log level (default: `INFO`) |
| `-h`, `--help` | Show help message |

### Foreground Mode

```bash
supervice -c supervisord.conf -n
```

Runs in the foreground with logs to stdout (unless `-l` is specified). Useful
for development and debugging. Press `Ctrl+C` to stop.

### Daemon Mode

```bash
supervice -c supervisord.conf
```

Performs a double-fork daemonization:

1. First `fork()` — parent exits
2. `setsid()` — creates new session
3. Second `fork()` — prevents controlling terminal acquisition
4. Redirects stdin/stdout/stderr to `/dev/null`

Logs are written to the configured log file. If no log file is configured,
defaults to `supervice.log` in the current directory.

### Signal Handling

| Signal | Behavior |
|--------|----------|
| `SIGTERM` | Graceful shutdown — stops all processes, then exits |
| `SIGINT` | Same as SIGTERM (Ctrl+C in foreground mode) |
| `SIGHUP` | Logged and ignored (use `supervicectl reload` instead) |

## `supervicectl` — Control Client

Command-line client that communicates with the running daemon over a Unix socket.

### Usage

```bash
supervicectl [-h] [-s SOCKET] {status,start,stop,restart,startgroup,stopgroup,reload}
```

### Global Options

| Option | Description |
|--------|-------------|
| `-s`, `--socket` | Unix socket path (default: `/tmp/supervice.sock`) |
| `-h`, `--help` | Show help message |

### Commands

#### `status`

Show the status of all managed processes.

```bash
supervicectl status
```

Output columns:

| Column | Description |
|--------|-------------|
| `NAME` | Process name (e.g., `worker:00`) |
| `STATE` | Current state (`RUNNING`, `STOPPED`, `FATAL`, etc.) |
| `PID` | OS process ID (or `-` if not running) |
| `UPTIME` | Time since process started (e.g., `1:23:45`) |
| `HEALTH` | Health check status: `OK`, `FAIL`, or `-` (only shown if health checks configured) |

Example output:

```
NAME                 STATE      PID        UPTIME       HEALTH
--------------------------------------------------------------
webapp               RUNNING    12345      1:23:45      OK
worker:00            RUNNING    12346      1:23:44      -
worker:01            STOPPED    -          -            -
worker:02            FATAL      -          -            -
```

**Exit code:** 0 on success, 1 if daemon is not running or error occurs.

#### `start`

Start a stopped process.

```bash
supervicectl start <name>
```

Waits up to 5 seconds for the process to reach `RUNNING` state.

**Exit code:** 0 on success, 1 if process not found or start failed.

#### `stop`

Stop a running process.

```bash
supervicectl stop <name>
```

Sends the configured stop signal (default: `SIGTERM`) and waits for exit.

**Exit code:** 0 on success, 1 if process not found.

#### `restart`

Restart a process (stop + start).

```bash
supervicectl restart <name>
supervicectl restart <name> --force
```

| Option | Description |
|--------|-------------|
| `--force` | Use SIGKILL instead of graceful stop signal |

**Exit code:** 0 on success, 1 if process not found.

#### `startgroup`

Start all processes in a group.

```bash
supervicectl startgroup <group>
```

Starts all processes in the named group concurrently.

**Exit code:** 0 on success, 1 if group not found.

#### `stopgroup`

Stop all processes in a group.

```bash
supervicectl stopgroup <group>
```

Stops all processes in the named group concurrently.

**Exit code:** 0 on success, 1 if group not found.

#### `reload`

Reload the configuration file and apply changes.

```bash
supervicectl reload
```

Reload behavior:

- **Added programs** — Started automatically
- **Removed programs** — Stopped and removed
- **Changed programs** — Reported, but require manual restart to apply

Example output:

```
Added: newworker
Removed: oldworker
Changed (restart to apply): webapp
```

**Exit code:** 0 on success, 1 if daemon is not running or reload failed.

### Custom Socket Path

If the daemon uses a non-default socket path, specify it with `-s`:

```bash
supervicectl -s /var/run/supervice.sock status
supervicectl -s /var/run/supervice.sock stop webapp
```

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Command succeeded |
| `1` | Error (process not found, daemon not running, command failed) |

## RPC Protocol

The client communicates with the daemon using a length-prefixed JSON protocol
over a Unix domain socket.

### Wire Format

```
[4 bytes: message length (uint32, big-endian)][JSON payload]
```

Maximum message size: 1 MB.

### Request Format

```json
{
    "command": "start",
    "name": "webapp"
}
```

### Response Format

```json
{
    "status": "ok",
    "message": "Started webapp"
}
```

Error responses include a `code` field:

```json
{
    "status": "error",
    "code": "UNKNOWN_COMMAND",
    "message": "Unknown command: foo"
}
```

Error codes: `INVALID_JSON`, `INVALID_REQUEST`, `UNKNOWN_COMMAND`, `INTERNAL_ERROR`.
