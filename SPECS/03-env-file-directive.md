# env_file directive — EARS spec

Ticket: `issuedb` #3 (in-progress)
Source: `PORTABILITY-FREEBSD.md` §2

## Requirements

- Where a program config sets `env_file`, the system shall load `KEY=VALUE`
  variables from the file(s), skipping blank lines and `#` comments.
- When multiple `env_file` paths are given, the system shall apply later files
  over earlier ones.
- When both `env_file` and `environment` are set, the system shall prefer
  `environment` values.
- If an `env_file` path does not exist or is not readable, then config loading
  shall fail with a clear error.
- The system shall read `env_file` as the supervisor before any privilege drop.

## Design decisions

- File paths are comma-separated, e.g. `env_file = /a.env, /b.env`.
- `%(process_num)s` is expanded in env_file values like `environment` values
  (values are merged into `environment` at parse time).
- A non-comment line without `=` is a config error naming file and line.
