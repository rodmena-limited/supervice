# Docs: platform matrix, FreeBSD rc.d example, spawn INFO line — EARS spec

Ticket: `issuedb` #6 (in-progress)
Source: `PORTABILITY-FREEBSD.md` §3 and §6

## Requirements

- While spawning a program, the system shall log an INFO line containing the
  resolved command, working directory, and uid.
- The README shall state the supported platform matrix (Linux first-class;
  FreeBSD and macOS caveats).
- The README shall include a worked FreeBSD rc.d example that avoids the
  `daemon(8)` / `${name}_user` double-setuid trap.
