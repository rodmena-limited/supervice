# Verify pdeathsig is functional at startup — EARS spec

**Ticket:** issuedb #10 · **Priority:** high · **Tag:** feature

## Requirements

- When the supervice daemon starts and at least one program requests
  `pdeathsig`, the supervisor shall verify the parent-death-signal mechanism is
  functional by performing a real set operation in a forked child and reading
  the value back.
- If the verification child reports that the set operation failed, then the
  supervisor shall log a warning naming the platform and stating that children
  will survive an abrupt supervisor kill.
- While verification succeeds, the supervisor shall emit no `pdeathsig` warning.
- The supervisor shall not modify its own parent-death signal while verifying.
- The supervisor shall perform verification once per daemon start and shall not
  add any syscall to the per-process spawn path.
- If the verification itself cannot be performed, then the supervisor shall
  treat the mechanism as unverified and warn rather than claim support.

## Defect

`pdeathsig_supported()` (`process.py:57`) is `return _LIBC is not None` — it
tests only that libc *loaded*, not that the syscall works. The return values of
`prctl` (`process.py:86`) and `procctl` (`process.py:89`) are discarded and
checked nowhere. ctypes does not raise on a `-1` return, so a failing syscall is
silent: config says `pdeathsig = true`, no warning is emitted, and children
orphan on supervisor crash.

## Design note — why fork-and-really-SET, not a STATUS read

A read-only probe (`PR_GET_PDEATHSIG` / `PROC_PDEATHSIG_STATUS`) was proposed
first and rejected. The likeliest real-world failure is a FreeBSD jail, which is
exactly the shape that permits the read and restricts the write. A probe that is
weakest precisely where it is most needed is not worth its complexity.
Performing the real set in a forked child exercises the same syscall in the same
post-fork context as the real spawn path, and reports via exit status — which is
safe from a child, unlike logging.

## Do not "fix" the bare except

The `except Exception: pass` in `_pdeathsig_preexec` (`process.py:90`) is
deliberate and load-bearing. An exception raised in `preexec_fn` does not
degrade pdeathsig — it fails the spawn entirely (verified:
`SubprocessError: Exception occurred in preexec_fn`), so the supervisor would
start nothing, for every program, at every restart. It is **not** the silent
path; the discarded return value is.

## Blocking tests (both directions)

- known-positive: probe reports functional on a working host.
- known-negative: with the set operation forced to fail, the probe reports
  failure.

Prototyped on Linux/amd64: positive ⇒ child reports `pdeathsig=9`; negative ⇒
child reports `254`; parent `pdeathsig` unchanged at `0`. FreeBSD 15.1 data
confirming `PROC_PDEATHSIG_STATUS` is non-mutating and reflects real state
(`0 → 9` after a genuine set) supplied by auth-service-b080da.
