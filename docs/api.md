# API Reference

This section documents the public Python API for embedding Supervice or
extending its functionality.

## supervice.models

Data models used throughout the project.

### `HealthCheckType`

```python
class HealthCheckType(Enum):
    NONE = "none"
    TCP = "tcp"
    SCRIPT = "script"
```

### `HealthCheckConfig`

```python
@dataclass
class HealthCheckConfig:
    type: HealthCheckType = HealthCheckType.NONE
    interval: int = 30
    timeout: int = 10
    retries: int = 3
    start_period: int = 10
    port: int | None = None
    host: str = "127.0.0.1"
    command: str | None = None
```

### `ProgramConfig`

```python
@dataclass
class ProgramConfig:
    name: str
    command: str
    numprocs: int = 1
    autostart: bool = True
    autorestart: bool = True
    startsecs: int = 1
    startretries: int = 3
    stopsignal: str = "TERM"
    stopwaitsecs: int = 10
    stdout_logfile: str | None = None
    stderr_logfile: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    directory: str | None = None
    user: str | None = None
    group: str | None = None
    healthcheck: HealthCheckConfig = field(default_factory=HealthCheckConfig)
```

### `SupervisorConfig`

```python
@dataclass
class SupervisorConfig:
    logfile: str = "supervice.log"
    pidfile: str = "supervice.pid"
    loglevel: str = "INFO"
    socket_path: str = "/tmp/supervice.sock"
    shutdown_timeout: int = 30
    log_maxbytes: int = 52428800  # 50MB
    log_backups: int = 10
    programs: list[ProgramConfig] = field(default_factory=list)
```

## supervice.config

Configuration parsing and validation.

### `parse_config(path: str) -> SupervisorConfig`

Parse an INI configuration file and return a validated `SupervisorConfig`.

**Parameters:**
- `path` — Path to the configuration file

**Raises:**
- `FileNotFoundError` — Config file does not exist
- `ConfigValidationError` — Validation error in config

**Example:**

```python
from supervice.config import parse_config

config = parse_config("supervisord.conf")
for prog in config.programs:
    print(f"{prog.name}: {prog.command}")
```

### `ConfigValidationError`

```python
class ConfigValidationError(ValueError):
    pass
```

Raised when configuration validation fails. The error message describes the
specific validation failure.

## supervice.core

### `Supervisor`

The central coordinator that manages processes, RPC, and lifecycle.

```python
class Supervisor:
    config: SupervisorConfig
    processes: dict[str, Process]
    groups: dict[str, list[str]]
```

#### `load_config(path: str) -> None`

Load configuration from an INI file, set up logging, create Process instances.

#### `async run() -> None`

Start the supervisor. Installs signal handlers, starts processes, starts RPC
server, and waits for shutdown signal.

#### `async shutdown() -> None`

Gracefully shut down all processes, stop RPC server, release PID file lock.

#### `async reload_config() -> dict[str, list[str]]`

Reload configuration from disk. Returns a dict with keys `added`, `removed`,
and `changed`, each containing a sorted list of process names.

**Example:**

```python
import asyncio
from supervice.core import Supervisor

supervisor = Supervisor()
supervisor.load_config("supervisord.conf")
asyncio.run(supervisor.run())
```

## supervice.process

### Process States

```python
STOPPED = "STOPPED"
STARTING = "STARTING"
RUNNING = "RUNNING"
BACKOFF = "BACKOFF"
STOPPING = "STOPPING"
EXITED = "EXITED"
FATAL = "FATAL"
UNHEALTHY = "UNHEALTHY"
```

### `Process`

Manages a single OS process lifecycle.

```python
class Process:
    config: ProgramConfig
    state: str
    process: asyncio.subprocess.Process | None
    should_run: bool
    started_at: float | None
    is_healthy: bool | None
```

#### `async start() -> None`

Start the supervision task (internal lifecycle management).

#### `async stop() -> None`

Stop the supervision task and kill the process.

#### `async start_process() -> None`

Request the process to start (used by RPC). Waits up to 5 seconds for the
process to reach `RUNNING` state.

#### `async stop_process() -> None`

Request the process to stop (used by RPC). Sends the configured stop signal.

#### `async force_kill() -> None`

Immediately SIGKILL the process without graceful shutdown.

#### `async kill() -> None`

Send stop signal to the entire process group, escalate to SIGKILL after
`stopwaitsecs` timeout.

## supervice.client

### `Controller`

Client for communicating with the Supervice daemon over Unix socket.

```python
class Controller:
    def __init__(self, socket_path: str = "/tmp/supervice.sock"): ...
```

#### `async send_command(command: str, **kwargs) -> dict`

Send a raw RPC command and return the response.

#### `async status() -> bool`

Print process status table. Returns `True` on success.

#### `async start_process(name: str) -> bool`

Start a named process. Returns `True` on success.

#### `async stop_process(name: str) -> bool`

Stop a named process. Returns `True` on success.

#### `async restart_process(name: str, force: bool = False) -> bool`

Restart a named process. With `force=True`, uses SIGKILL. Returns `True` on success.

#### `async start_group(name: str) -> bool`

Start all processes in a group. Returns `True` on success.

#### `async stop_group(name: str) -> bool`

Stop all processes in a group. Returns `True` on success.

#### `async reload() -> bool`

Reload daemon configuration. Returns `True` on success.

**Example:**

```python
import asyncio
from supervice.client import Controller

async def check():
    ctl = Controller("/tmp/supervice.sock")
    await ctl.status()
    await ctl.restart_process("webapp")

asyncio.run(check())
```

## supervice.events

### `EventType`

```python
class EventType(Enum):
    PROCESS_STATE_STARTING = auto()
    PROCESS_STATE_RUNNING = auto()
    PROCESS_STATE_BACKOFF = auto()
    PROCESS_STATE_STOPPING = auto()
    PROCESS_STATE_EXITED = auto()
    PROCESS_STATE_STOPPED = auto()
    PROCESS_STATE_FATAL = auto()
    PROCESS_STATE_UNKNOWN = auto()
    PROCESS_STATE_UNHEALTHY = auto()
    HEALTHCHECK_PASSED = auto()
    HEALTHCHECK_FAILED = auto()
```

### `Event`

```python
@dataclass
class Event:
    type: EventType
    payload: dict[str, Any]
```

### `EventBus`

Async publish/subscribe event system.

```python
class EventBus:
    def __init__(self, maxsize: int = 1000): ...
```

#### `start() -> None`

Start the event processing task.

#### `async stop() -> None`

Stop the event processing task.

#### `subscribe(event_type: EventType, handler: EventHandler) -> None`

Register a handler for an event type. Handler signature:
`async def handler(event: Event) -> None`

#### `publish(event: Event) -> None`

Publish an event to the queue. Non-blocking. If the queue is full, the oldest
event is dropped.

**Example:**

```python
from supervice.events import EventBus, EventType

bus = EventBus()

async def on_running(event):
    print(f"{event.payload['processname']} started")

bus.subscribe(EventType.PROCESS_STATE_RUNNING, on_running)
bus.start()
```

## supervice.health

### `HealthCheckResult`

```python
class HealthCheckResult:
    healthy: bool
    message: str
```

### `HealthChecker` (ABC)

```python
class HealthChecker(ABC):
    async def check(self) -> HealthCheckResult: ...
```

### `TCPHealthChecker`

Checks TCP connectivity to a host:port.

### `ScriptHealthChecker`

Runs a command and checks exit code (0 = healthy).

### `create_health_checker(config: HealthCheckConfig) -> HealthChecker | None`

Factory function. Returns `None` if health check type is `NONE`.

## supervice.logger

### `setup_logger(level, logfile, maxbytes, backups) -> logging.Logger`

Configure the root `supervice` logger with optional file rotation.

### `get_logger() -> logging.Logger`

Return the global `supervice` logger instance.
