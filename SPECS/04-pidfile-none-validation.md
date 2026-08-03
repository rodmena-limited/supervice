# pidfile=none and parent-dir validation — EARS spec

Ticket: `issuedb` #4 (in-progress)
Source: `PORTABILITY-FREEBSD.md` §4

## Requirements

- When `pidfile` is set to `none` or empty, the system shall not create a
  pidfile lock.
- If the pidfile parent directory does not exist or is not writable, then
  config loading shall fail with a clear error.
- If the socket parent directory does not exist, then config loading shall
  fail with a clear error.
