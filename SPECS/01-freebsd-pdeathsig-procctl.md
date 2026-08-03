# FreeBSD pdeathsig via procctl — EARS spec

Ticket: `issuedb` #1 (in-progress)
Source: `PORTABILITY-FREEBSD.md` §1a

## Requirements

- Where supervice runs on FreeBSD, when a program has pdeathsig enabled, the
  system shall configure `PROC_PDEATHSIG_CTL` on the child so it receives
  SIGKILL when the supervisor dies.
- While pdeathsig is unsupported on a platform, the system shall not apply it.

## Notes

- `procctl(2)` with `P_PID` (0), id 0, `PROC_PDEATHSIG_CTL` (11), pointer to a
  `c_int` holding SIGKILL.
- The setting survives `execve` except setuid/setgid binaries.
- Preloaded at import time in the parent; the preexec hook only touches the
  preloaded libc handle (no dlopen/imports in the forked child).
