# Configuration Reference

Supervice uses standard INI format configuration files parsed by Python's
`configparser` module.

## File Format

```ini
[supervice]
key = value

[program:name]
key = value

[group:name]
programs = prog1,prog2
```

## `[supervice]` — Global Settings

Global daemon configuration.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `logfile` | string | `supervice.log` | Path to the daemon log file |
| `loglevel` | string | `INFO` | Logging level |
| `pidfile` | string | `supervice.pid` | Path to PID/lock file |
| `socket` | string | `/tmp/supervice.sock` | Unix socket path for RPC communication |
| `shutdown_timeout` | int | `30` | Seconds to wait for graceful shutdown of all processes |
| `log_maxbytes` | int | `52428800` | Max log file size in bytes before rotation (0 disables rotation) |
| `log_backups` | int | `10` | Number of rotated backup log files to keep |

### Log Levels

Valid values for `loglevel`:

- `DEBUG` — Detailed diagnostic information
- `INFO` — General operational messages (default)
- `WARNING` / `WARN` — Warning conditions
- `ERROR` — Error conditions
- `CRITICAL` — Critical failures

### PID File Locking

The `pidfile` serves dual purposes:

1. Records the daemon's PID for external tools
2. Uses `fcntl.flock()` to prevent multiple instances from running simultaneously

If another Supervice instance is already running with the same pidfile, the new
instance will exit with an error:

```
Another supervice instance is already running (pidfile: supervice.pid)
```

### Example

```ini
[supervice]
logfile = /var/log/supervice/supervice.log
loglevel = INFO
pidfile = /var/run/supervice.pid
socket = /var/run/supervice.sock
shutdown_timeout = 60
log_maxbytes = 104857600
log_backups = 5
```

## `[program:NAME]` — Process Definitions

Each `[program:NAME]` section defines a managed process. The `NAME` becomes the
process identifier used in CLI commands and status output.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `command` | string | *(required)* | Command to execute |
| `numprocs` | int | `1` | Number of instances to run |
| `autostart` | bool | `true` | Start when daemon starts |
| `autorestart` | bool | `true` | Restart when process exits |
| `startsecs` | int | `1` | Seconds a process must run to be considered started |
| `startretries` | int | `3` | Max start attempts before FATAL |
| `stopsignal` | string | `TERM` | Signal to send when stopping |
| `stopwaitsecs` | int | `10` | Seconds to wait before SIGKILL |
| `stdout_logfile` | string | *(none)* | File for stdout output |
| `stderr_logfile` | string | *(none)* | File for stderr output |
| `environment` | string | *(none)* | Environment variables |
| `directory` | string | *(none)* | Working directory |
| `user` | string | *(none)* | Run as this user |

### `command`

The command to execute. Supports shell-style quoting via `shlex.split()`:

```ini
command = python3 -u app.py
command = /usr/bin/node server.js --port 3000
command = bash -c "echo hello && sleep 10"
```

The command is resolved using `shutil.which()` if not an absolute path.

### `numprocs`

When set to a value greater than 1, Supervice creates multiple instances named
`NAME:00`, `NAME:01`, etc.:

```ini
[program:worker]
command = python3 worker.py
numprocs = 4
```

Creates: `worker:00`, `worker:01`, `worker:02`, `worker:03`.

### `startsecs` and `startretries`

These options work together to determine startup behavior:

- If a process exits before `startsecs` seconds have elapsed, it counts as a
  failed start attempt
- After `startretries` consecutive failed starts, the process enters `FATAL`
  state and stops trying
- If a process runs longer than `startsecs`, the retry counter resets to zero

```ini
startsecs = 5        # must run at least 5 seconds
startretries = 3     # try up to 3 times before giving up
```

### `stopsignal`

The signal sent to stop the process. Common values:

| Signal | Use Case |
|--------|----------|
| `TERM` | Default. Graceful shutdown |
| `INT` | Interrupt (like Ctrl+C) |
| `QUIT` | Quit with core dump |
| `KILL` | Immediate kill (cannot be caught) |
| `HUP` | Reload configuration |
| `USR1` / `USR2` | Application-specific |

The signal can be specified with or without the `SIG` prefix (`TERM` or `SIGTERM`).

### `stopwaitsecs`

After sending `stopsignal`, Supervice waits this many seconds for the process
to exit. If it hasn't exited by then, `SIGKILL` is sent to the entire process
group.

### Log File Substitution

The `stdout_logfile` and `stderr_logfile` options support `%(process_num)s`
substitution when `numprocs > 1`:

```ini
[program:worker]
command = python3 worker.py
numprocs = 3
stdout_logfile = logs/worker_%(process_num)s.log
stderr_logfile = logs/worker_%(process_num)s_err.log
```

This produces:
- `logs/worker_00.log`, `logs/worker_01.log`, `logs/worker_02.log`
- `logs/worker_00_err.log`, `logs/worker_01_err.log`, `logs/worker_02_err.log`

### `environment`

Set environment variables for the process. Format: `KEY=VALUE` pairs separated
by commas. Values containing commas must be quoted:

```ini
environment = APP_ENV=production,DB_HOST=localhost
environment = CONFIG="value,with,commas",DEBUG=false
environment = PATH="/usr/local/bin:/usr/bin"
```

Both single and double quotes are supported for values.

### `user`

Run the process as a specific system user. Requires the Supervice daemon to be
running as root:

```ini
user = www-data
```

Supervice switches the user ID, group ID, and supplementary groups before
executing the command. If user switching fails, the process enters `FATAL` state.

### `directory`

Set the working directory for the process:

```ini
directory = /opt/myapp
```

The directory must exist and be accessible. Validated at config parse time.

### Full Example

```ini
[program:api]
command = python3 -u api_server.py --port 8080
numprocs = 2
autostart = true
autorestart = true
startsecs = 5
startretries = 5
stopsignal = TERM
stopwaitsecs = 30
stdout_logfile = logs/api_%(process_num)s.log
stderr_logfile = logs/api_%(process_num)s_err.log
environment = APP_ENV=production,LOG_LEVEL=info
directory = /opt/api
user = apiuser
```

## `[group:NAME]` — Process Groups

Groups allow you to control multiple programs as a unit:

```ini
[program:web]
command = python3 web.py

[program:api]
command = python3 api.py

[program:worker]
command = python3 worker.py

[group:frontend]
programs = web,api

[group:backend]
programs = worker
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `programs` | string | *(required)* | Comma-separated list of program names |

Group operations:

```bash
supervicectl stopgroup frontend     # stops web and api
supervicectl startgroup frontend    # starts web and api
```

Programs can only belong to one group. If a program isn't in any explicit group,
it implicitly forms its own single-program group.

## Health Check Options

Health check options are specified within `[program:NAME]` sections. See
{doc}`healthchecks` for detailed documentation.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `healthcheck_type` | string | `none` | `none`, `tcp`, or `script` |
| `healthcheck_interval` | int | `30` | Seconds between checks |
| `healthcheck_timeout` | int | `10` | Seconds to wait for response |
| `healthcheck_retries` | int | `3` | Failures before unhealthy |
| `healthcheck_start_period` | int | `10` | Seconds before first check |
| `healthcheck_port` | int | *(none)* | TCP port (required for `tcp`) |
| `healthcheck_host` | string | `127.0.0.1` | TCP host |
| `healthcheck_command` | string | *(none)* | Script (required for `script`) |

## Config Validation

Supervice validates all configuration at parse time:

- **Missing command** — Programs must have a `command`
- **Invalid signal** — `stopsignal` must be a valid Unix signal name
- **Non-existent user** — `user` must exist on the system
- **Invalid directory** — `directory` must exist and be accessible
- **Log directory** — Parent directory of log files must exist and be writable
- **Numeric bounds** — `numprocs`, `startsecs`, etc. must be non-negative
- **Health check consistency** — TCP checks require `port`, script checks require `command`
- **Log level** — Must be a valid Python logging level

Invalid configuration causes the daemon to exit immediately with a descriptive error.
