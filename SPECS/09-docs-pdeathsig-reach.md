# Docs: pdeathsig platform support and one-generation reach — EARS spec

**Ticket:** issuedb #9 · **Priority:** high · **Tag:** docs

## Requirements

- The supervice documentation shall state that `pdeathsig` is implemented on
  Linux via `prctl(PR_SET_PDEATHSIG)` and on FreeBSD via
  `procctl(PROC_PDEATHSIG_CTL)`.
- If any document states a platform limitation for `pdeathsig`, then that
  statement shall be consistent across `README.md`, `docs/installation.md`,
  `docs/api.md` and `docs/configuration.md`.
- The supervice documentation shall state that `pdeathsig` protects only the
  direct child process and does not protect grandchildren.
- The supervice documentation shall distinguish structural exposure
  (pre-forking servers, worker pools, master/worker splits), which has no
  configuration fix, from wrapper-script exposure, which is corrected by `exec`.
- Where a program's `command` is a wrapper script, the supervice documentation
  shall state that the wrapper must `exec` the real program for `pdeathsig` to
  apply to it.
- The supervice installation documentation shall state that FreeBSD ports
  install versioned Python binaries (`python3.12`, not `python3`) and that
  supervice is commonly installed in a per-service virtualenv rather than on
  `PATH`.

## Defects

| Location | Claim | Status |
|---|---|---|
| `docs/installation.md:65` | "The `PR_SET_PDEATHSIG` feature is Linux-only" | **wrong** since 0.3.0 |
| `docs/api.md:58` | `# Linux: SIGKILL children if the supervisor dies` | **wrong** |
| `README.md:240` | FreeBSD supported via `procctl(2)` | correct |
| `docs/configuration.md:95` | "Linux/FreeBSD" | correct |

The one-generation limit appears only in `audit-2026-07-23.md:45`, framed as a
stop/kill concern, and is absent from all user-facing documentation.

## Evidence

- FreeBSD 15.1 `procctl(2)`: *"The value is cleared for child processes and when
  executing set-user-ID or set-group-ID binaries."*
- Measured on FreeBSD 15.1 (auth-service-b080da): supervisor → child(pdeathsig)
  → grandchild; `kill -9` supervisor ⇒ child **died**, grandchild **survived**.
  Control arm without pdeathsig ⇒ both survived.
- Measured on Linux/amd64: wrapper doing `exec realprog` ⇒ `pdeathsig=9`
  (protected); wrapper doing `realprog` ⇒ `pdeathsig=0` (unprotected).

## Severity rationale

Understating a feature costs a reader an afternoon; overstating its reach costs
an operator their workers.
