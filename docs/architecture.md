# Architecture

This document describes the internal architecture of Supervice for contributors
and anyone interested in how it works under the hood.

## Module Overview

```
supervice/
├── main.py        Entry point, argument parsing, daemonization
├── core.py        Supervisor orchestrator — central coordinator
├── process.py     Process lifecycle with async state machine
├── config.py      INI config parser with validation
├── models.py      Data models (dataclasses)
├── rpc.py         Unix socket RPC server (JSON over length-prefixed protocol)
├── client.py      CLI client (supervicectl) and Controller class
├── events.py      EventBus — async pub/sub for state changes
├── health.py      Health check implementations (TCP, script)
└── logger.py      Logging setup with rotation
```

## Data Flow

```
                    Config File (INI)
                         │
                         ▼
                  ┌──────────────┐
                  │ parse_config │  config.py
                  └──────┬───────┘
                         │ SupervisorConfig
                         ▼
                  ┌──────────────┐
                  │  Supervisor  │  core.py
                  │ load_config  │
                  └──────┬───────┘
                         │ Creates Process instances
                         ▼
              ┌──────────────────────┐
              │     async run()      │
              │                      │
              │  ┌────────────────┐  │
              │  │   Process(s)   │  │  process.py
              │  │   supervise()  │  │
              │  └────────┬───────┘  │
              │           │          │
              │  ┌────────▼───────┐  │
              │  │   RPCServer    │  │  rpc.py
              │  │   (Unix sock)  │  │
              │  └────────┬───────┘  │
              │           │          │
              │  ┌────────▼───────┐  │
              │  │   EventBus     │  │  events.py
              │  │   (pub/sub)    │  │
              │  └────────────────┘  │
              └──────────────────────┘
                         ▲
                         │ JSON/Unix Socket
                         │
              ┌──────────────────────┐
              │     Controller       │  client.py
              │   (supervicectl)     │
              └──────────────────────┘
```

## Process State Machine

Each `Process` instance manages a single OS process through a state machine:

```
                          ┌──────────┐
                          │ STOPPED  │ ◄── initial state
                          └────┬─────┘
                               │ should_run = true
                               ▼
                          ┌──────────┐
                     ┌───▶│ STARTING │
                     │    └────┬─────┘
                     │         │ spawn succeeds
                     │         ▼
                     │    ┌──────────┐         ┌───────────┐
                     │    │ RUNNING  │────────▶│ UNHEALTHY │
                     │    └────┬─────┘  health │           │
                     │         │        fails  └─────┬─────┘
                     │         │                     │ auto-restart
                     │         ▼                     │ (kill + restart)
                     │    ┌──────────┐               │
                     │    │ STOPPING │ ◄─────────────┘
                     │    └────┬─────┘
                     │         │ process exits
                     │         ▼
                     │    ┌──────────┐
                     │    │  EXITED  │
                     │    └────┬─────┘
                     │         │ autorestart = true
                     │         ▼                        ┌───────┐
                     │    ┌──────────┐    retries > max  │ FATAL │
                     └────│ BACKOFF  │─────────────────▶│       │
                          └──────────┘                  └───────┘
```

### State Transitions

| From | To | Trigger |
|------|----|---------|
| `STOPPED` | `STARTING` | `should_run` set to true |
| `STARTING` | `RUNNING` | Process spawned successfully |
| `STARTING` | `FATAL` | Spawn failed (command not found, permission error) |
| `RUNNING` | `STOPPING` | Stop requested or health check restart |
| `RUNNING` | `UNHEALTHY` | Health check failures exceed threshold |
| `UNHEALTHY` | `RUNNING` | Health check passes again |
| `UNHEALTHY` | `STOPPING` | Auto-restart triggered |
| `STOPPING` | `STOPPED` | Process exited after stop signal |
| `STOPPING` | `EXITED` | Process exited |
| `EXITED` | `BACKOFF` | `autorestart` is true |
| `BACKOFF` | `STARTING` | Backoff delay elapsed |
| `BACKOFF` | `FATAL` | Retry count exceeds `startretries` |

### Concurrency Safety

State transitions are protected by an `asyncio.Lock` (`_state_lock`) to prevent
race conditions between the supervision loop, RPC commands, and health check
tasks.

## Supervisor (core.py)

The `Supervisor` class is the central coordinator:

1. **Loads configuration** — Parses INI, creates `Process` instances
2. **Starts supervision** — Launches async tasks for each process
3. **Signal handling** — SIGINT/SIGTERM trigger shutdown, SIGHUP is ignored
4. **PID file locking** — Prevents multiple instances via `fcntl.flock()`
5. **Manages RPC server** — Delegates commands to individual processes
6. **Hot reload** — Adds/removes processes based on config changes

### Shutdown Sequence

1. Receive SIGINT/SIGTERM
2. Set shutdown event
3. Release PID file lock
4. Stop RPC server
5. Stop EventBus
6. Stop all processes (with `shutdown_timeout`)
7. Exit

## RPC Server (rpc.py)

The RPC server listens on a Unix domain socket with restrictive permissions
(`0o600`).

### Protocol

Length-prefixed JSON over Unix socket:

```
┌─────────────┬──────────────────┐
│ 4-byte len  │   JSON payload   │
│ (uint32 BE) │                  │
└─────────────┴──────────────────┘
```

### Commands

| Command | Parameters | Description |
|---------|-----------|-------------|
| `status` | *(none)* | List all processes with state, PID, uptime |
| `start` | `name` | Start a process |
| `stop` | `name` | Stop a process |
| `restart` | `name`, `force` (optional) | Restart a process |
| `startgroup` | `name` | Start all processes in group |
| `stopgroup` | `name` | Stop all processes in group |
| `reload` | *(none)* | Reload configuration |

### Security

- Socket created with `umask(0o177)` for atomic restrictive permissions
- Unknown commands are rejected with `UNKNOWN_COMMAND` error
- Invalid JSON is rejected with `INVALID_JSON` error
- Maximum message size: 1 MB

## EventBus (events.py)

Async publish/subscribe system for process state changes.

### Design

- Bounded `asyncio.Queue` (default 1000 events) prevents memory exhaustion
- When queue is full, oldest events are dropped with a warning
- Subscribers receive events asynchronously via `await handler(event)`
- Event processing errors are logged but don't crash the bus

### Event Types

| Event | Payload |
|-------|---------|
| `PROCESS_STATE_STARTING` | processname, groupname, from_state, pid |
| `PROCESS_STATE_RUNNING` | processname, groupname, from_state, pid |
| `PROCESS_STATE_BACKOFF` | processname, groupname, from_state, pid |
| `PROCESS_STATE_STOPPING` | processname, groupname, from_state, pid |
| `PROCESS_STATE_EXITED` | processname, groupname, from_state, pid |
| `PROCESS_STATE_STOPPED` | processname, groupname, from_state, pid |
| `PROCESS_STATE_FATAL` | processname, groupname, from_state, pid |
| `PROCESS_STATE_UNHEALTHY` | processname, groupname, from_state, pid |
| `HEALTHCHECK_PASSED` | processname, message, pid |
| `HEALTHCHECK_FAILED` | processname, message, failures, pid |

## Health Checks (health.py)

Health checks run as separate `asyncio.Task` instances alongside each process.

### Architecture

```
Process.supervise()
     │
     ├── spawn() ──▶ _start_health_checks() ──▶ asyncio.Task(_run_health_checks)
     │                                                │
     │                                                ├── sleep(start_period)
     │                                                ├── loop:
     │                                                │   ├── checker.check()
     │                                                │   ├── handle result
     │                                                │   └── sleep(interval)
     │                                                │
     └── kill() ───▶ _stop_health_checks() ──────────▶ task.cancel()
```

### Factory Pattern

`create_health_checker()` returns the appropriate checker based on config:

- `HealthCheckType.TCP` → `TCPHealthChecker`
- `HealthCheckType.SCRIPT` → `ScriptHealthChecker`
- `HealthCheckType.NONE` → `None`

## Daemonization (main.py)

The `_daemonize()` function implements standard Unix double-fork:

1. **First fork** — Parent exits, child continues
2. **`setsid()`** — Creates new session, detaches from terminal
3. **Second fork** — Prevents reacquisition of controlling terminal
4. **Redirect stdio** — stdin/stdout/stderr → `/dev/null`

## Child Process Management

### Process Groups

Each child process is started with `start_new_session=True`, creating a new
process group. This ensures that `os.killpg()` kills the entire process tree
(the main process and all its children), not just the top-level process.

### Orphan Prevention (Linux)

On Linux, `prctl(PR_SET_PDEATHSIG, SIGKILL)` is set in the `preexec_fn` to
ensure child processes are killed if the parent dies unexpectedly.

### User Switching

When `user` is configured, the `preexec_fn` callback:

1. Calls `os.initgroups()` to set supplementary groups
2. Calls `os.setgid()` to set the group ID
3. Calls `os.setuid()` to set the user ID

Failures exit with code 126 (`EXIT_CODE_USER_SWITCH_FAILED`), which the parent
process interprets as a `FATAL` state.
