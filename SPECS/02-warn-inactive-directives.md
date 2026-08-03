# Warn when pdeathsig requested but unsupported — EARS spec

Ticket: `issuedb` #2 (in-progress)
Source: `PORTABILITY-FREEBSD.md` §1b

## Requirements

- If a program requests pdeathsig on a platform where it cannot be honoured,
  then the system shall log a warning at config load naming the directive and
  the platform.
