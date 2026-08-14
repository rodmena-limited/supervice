# Warn when pdeathsig is requested for a setuid/setgid command — EARS spec

**Ticket:** issuedb #11 · **Priority:** medium · **Tag:** feature

## Requirements

- Where a program requests `pdeathsig` and its resolved command is a
  set-user-ID or set-group-ID binary, the supervice config loader shall log a
  warning that the parent-death signal is cleared at exec for that program.
- If a program's resolved command is not a set-user-ID or set-group-ID binary,
  then the config loader shall not emit that warning.
- If a program's command cannot be resolved to a path, then the config loader
  shall not emit that warning.
- The supervice documentation shall record that the `user` option is
  unaffected, because it switches uid via `setuid(2)` in preexec rather than
  exec'ing a setuid image.

## Rationale

Per the FreeBSD 15.1 `procctl(2)` man page and `PORTABILITY-FREEBSD.md:57`,
pdeathsig is cleared when exec'ing a set-user-ID or set-group-ID binary. Such a
program sets pdeathsig in preexec, silently loses it at exec, and orphans on
supervisor crash — while the config says `pdeathsig = true`, the syscall
succeeded, and the startup probe from [#10](10-pdeathsig-functional-probe.md)
passes. This is per-command, not per-host, so no startup probe can catch it.

Detection validated both directions on Linux: stat the resolved `argv[0]`;
`sudo`/`passwd`/`su` flagged SETUID/SETGID, `python3`/`/bin/sleep` not flagged.

## Kernel behaviour — MEASURED, no longer spec-only

Confirmed on FreeBSD 15.1 by bikeroom-freebsd-operato-dd8bca. Built on `/home`
(verified suid-capable by reading `mount`), helper setuid to `nobody` (65534,
holds no privileges — no setuid-root binary existed at any point), run as
`ledger` (1005) so there is a real credential transition:

| Arm | | Result |
|---|---|---|
| 1 | CONTROL, ordinary `execve` | `ruid=1005 euid=1005` **pdeathsig=9** |
| 2 | TEST, setuid `execve` 1005→65534 | `ruid=1005 euid=65534` **pdeathsig=0 — CLEARED** |
| 3 | NEGATIVE CONTROL, same helper from nosuid `/var/tmp` | setup failed, euid unchanged → **NO VERDICT** |

Arm 1 proves the helper *can* report 9 — without it, the 0 in arm 2 means
nothing. Arm 3 is the trap below, fired deliberately.

Preserved as `/var/tmp/freebsd-pdeathsig-harness/05-setuid-clearing.sh` on that
host: refuses to run on a nosuid filesystem, refuses to print a verdict without a
credential transition, and deletes every binary it builds.

## Trap for whoever re-runs that test

`/tmp` is mounted `nosuid` by default on FreeBSD and on many hardened Linux
systems. A setuid helper built in `/tmp` has its setuid bit **ignored** at exec,
no credential change occurs, pdeathsig is therefore **not** cleared, and the
test reports "survives exec of a setuid binary" — confidently contradicting the
man page, with a green run and no error anywhere. The test would be measuring
the mount options of its own scratch directory. Worse: because the result
contradicts the specification, the runner is likely to believe the measurement
over the spec.

**Blocking preconditions, not advisory notes:**

1. Build the helper on a filesystem verified suid-capable by reading `mount`
   output, never assumed.
2. The helper shall assert its own euid **changed**, and refuse to print any
   pdeathsig verdict if it did not — fail closed, no warn-and-continue.
3. Run as an unprivileged user exec'ing a helper owned by a **different**
   unprivileged user. Root exec'ing setuid has no credential transition, so a
   root-run test could report "survives" for an unrelated reason. No
   setuid-root binary need exist at any point.

*(Trap identified by auth-service-b080da on FreeBSD 15.1: `zroot/tmp on /tmp
(zfs, local, noatime, nosuid, nfsv4acls)`.)*
