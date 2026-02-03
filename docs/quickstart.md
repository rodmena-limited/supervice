# Quick Start

This guide walks you through setting up Supervice to manage your first processes.

## 1. Create a Configuration File

Create a file named `supervisord.conf`:

```ini
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
```

## 2. Start the Daemon

**Foreground mode** (recommended for development):

```bash
supervice -c supervisord.conf -n
```

**Daemon mode** (production):

```bash
supervice -c supervisord.conf
```

In daemon mode, Supervice performs a double-fork, detaches from the terminal,
and writes logs to the configured log file.

## 3. Check Process Status

```bash
supervicectl status
```

Output:

```
NAME                 STATE      PID        UPTIME
----------------------------------------------------
webapp               RUNNING    12345      0:05
```

## 4. Control Processes

```bash
supervicectl stop webapp        # stop a process
supervicectl start webapp       # start a process
supervicectl restart webapp     # graceful restart (SIGTERM + start)
supervicectl restart webapp --force  # force restart (SIGKILL + start)
```

## 5. Multiple Instances

Run multiple copies of the same program:

```ini
[program:worker]
command = python3 -u worker.py
numprocs = 4
autostart = true
autorestart = true
stdout_logfile = worker_%(process_num)s.log
```

This creates `worker:00`, `worker:01`, `worker:02`, `worker:03`. Control them
individually or as a group:

```bash
supervicectl stop worker:02     # stop one instance
supervicectl status             # see all instances
```

## 6. Process Groups

Group related programs for batch operations:

```ini
[program:web]
command = python3 -u web.py

[program:api]
command = python3 -u api.py

[group:frontend]
programs = web,api
```

```bash
supervicectl stopgroup frontend     # stops both web and api
supervicectl startgroup frontend    # starts both
```

## 7. Hot Reload

Add or remove programs without restarting the daemon:

```bash
# Edit supervisord.conf (add/remove [program:...] sections)
supervicectl reload
```

Output:

```
Added: newworker
Removed: oldworker
Changed (restart to apply): webapp
```

## 8. Shut Down

Send `SIGTERM` or `SIGINT` to the daemon process, or press `Ctrl+C` if running
in foreground mode. All managed processes will be gracefully stopped.

## Next Steps

- {doc}`configuration` — Full configuration reference
- {doc}`healthchecks` — Set up TCP and script-based health monitoring
- {doc}`cli` — Complete CLI reference
