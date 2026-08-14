# End-to-end orphan test + Darwin fd-leak coverage — EARS spec

**Ticket:** issuedb #13 · **Priority:** high · **Tag:** test

## Requirements

- The supervice test suite shall include an end-to-end test that SIGKILLs a
  running supervisor and asserts whether its child survived.
- The end-to-end orphan test shall use a child that produces **no output** on
  stdout or stderr.
- The end-to-end orphan test shall include a control arm with `pdeathsig`
  disabled, in which the child is asserted to **survive**.
- Where `/proc/self/fd` is unavailable and `/dev/fd` is available, the TCP
  health-check fd-leak test shall perform its assertion using `/dev/fd`.
- If neither `/proc/self/fd` nor `/dev/fd` is available, then that test shall
  skip.
- Before the fd-leak test is counted as coverage on a platform, it shall be
  demonstrated to **fail** on that platform against a deliberately leaked
  descriptor.

## Defect 1 — the core guarantee has never been tested

There is no end-to-end orphan test in this repo. Every pdeathsig test in
`tests/test_portability.py` is either a mock assertion against a fake libc
(lines 62–91) or a check that a warning string appears at config load (95–146).
Nothing kills a supervisor and asks whether a child died. The guarantee has been
verified by hand on FreeBSD by auth-service-b080da and falsified-by-accident on
Darwin by macbook-admin-bd8e86; CI knows about neither.

## Defect 2 — the silent-child requirement is not a style preference

`process.py:431-453` sets stdout/stderr to `subprocess.DEVNULL` by default and
to `subprocess.PIPE` only when a logfile is configured. When the supervisor
dies, the read end of that pipe closes and the next write kills a chatty child
by SIGPIPE. So a chatty-child-plus-logfile orphan test **passes whether or not
pdeathsig works**.

Measured on Darwin, three children, identical config, same run:

| child | outcome |
|---|---|
| writes stdout, `stdout_logfile` set | reaped — *looks like it works* |
| writes stdout, no logfile | orphaned, PPID 1 |
| silent | orphaned, PPID 1 |

This is **not** Darwin-specific. It applies on every platform whenever pdeathsig
is absent, disabled, or has failed. An orphan test built on a chatty child is
green forever regardless of what is implemented.

*Cross-check:* the FreeBSD verification by auth-service-b080da used a copy of
`/bin/sleep` — silent — so that result is **not** confounded. Had it used a
chatty child with a logfile, it would have been a false confirmation of the
FreeBSD branch.

## Defect 3 — fd-leak test silently skipped on Darwin

`tests/test_health.py:108` skips on macOS (`"no /proc/self/fd on this
platform"`), so the fd-leak check never runs there and fd leaks in health checks
are untested on that platform.

**Condition on fixing it:** do not merely unskip. Today the test is *honestly*
skipped — it says "not tested here". Unskipping it without demonstrating it can
fail converts an honest skip into a vacuous pass, which is strictly worse. Prove
it goes **red** against ~30 deliberately leaked fds on Darwin before counting it
as coverage. The existing slack of 3 may be wrong there, since `/dev/fd` listing
behaviour differs from Linux; only the red run will tell.
