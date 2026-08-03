# supervice on FreeBSD — field notes, portability gaps, and feature requests

Written 2026-08-03 by the TokenGate agent after migrating TokenGate from a
Linux/Docker deployment to a **native FreeBSD 15.1** host (`rodmena-vm-2`),
where supervice supervises the API and the worker under an `rc.d` unit.

**Headline: supervice worked.** It started both programs, honoured `directory`,
ran them as an unprivileged user, kept them up, restarted cleanly, and its TCP
health checks behaved. Nothing below is a complaint about the core design —
these are the sharp edges a second platform exposes, ordered by how much time
each one cost.

Version tested: `supervice 0.2.1` (PyPI), CPython 3.12.13, FreeBSD 15.1-RELEASE.

---

## 1. `pdeathsig` silently does nothing off Linux — and FreeBSD *can* do it

`process.py` loads libc only on Linux:

```python
PR_SET_PDEATHSIG = 1
_LIBC: ctypes.CDLL | None = None
if sys.platform == "linux":
    try:
        _LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        _LIBC = None
```

and `_pdeathsig_preexec()` is a no-op when `_LIBC is None`.

So a config carrying `pdeathsig = true` — as TokenGate's shipped `supervice.ini`
does — is accepted, reported as configured, and **provides no protection at
all** on FreeBSD. If the supervisor is SIGKILLed, children are reparented to
init and keep running: exactly the orphan scenario the flag exists to prevent.
For a service that holds a database pool and flushes a billing outbox, orphaned
children are worse than a crash.

Two asks:

**(a) Implement it.** FreeBSD has had the equivalent since 11.2:

```c
int procctl(idtype_t idtype, id_t id, int cmd, void *data);
/* cmd = PROC_PDEATHSIG_CTL (11), data = pointer to int signal number */
```

From ctypes that is roughly:

```python
elif sys.platform.startswith("freebsd"):
    _LIBC = ctypes.CDLL("libc.so.7", use_errno=True)
    # procctl(P_PID=0, 0 (self), PROC_PDEATHSIG_CTL=11, &sig)
```

Note `PROC_PDEATHSIG_CTL` applies to *the calling process* and is cleared on
exec of a setuid binary; for the ordinary case it survives `execve`, which is
what the pre-exec hook needs. macOS has no equivalent — there the honest
behaviour is (b).

**(b) Never fail silently.** Where the platform cannot honour `pdeathsig`, log
a warning once at config load: `pdeathsig requested but unsupported on
<platform>; children will survive an abrupt supervisor kill`. A feature that
quietly evaporates on a platform is worse than one that is absent, because the
operator believes they have it. This is the single change I would prioritise.

---

## 2. No `env_file` directive

`[program:*]` supports `environment = A=1,B=2`, which is fine for two values
and unusable for real secrets: it puts credentials in a world-readable config
file, and there is no way to point at a `0600` file owned by the service user.

We worked around it by setting `directory` to the app root and letting
pydantic-settings read `.env` from the working directory — which only works
because our framework happens to do that. A plain program has no such escape.

Request:

```ini
[program:api]
env_file = /opt/tokengate/etc/tokengate.env   ; KEY=VALUE lines, # comments
environment = LOG_LEVEL=info                   ; still wins over env_file
```

Semantics worth pinning down: later file wins over earlier, explicit
`environment` overrides `env_file`, missing file is a hard error at start (not
a silent empty env), and the file is read **as the supervisor** before the
privilege drop so a `0600 root:root` secret file can be delivered to an
unprivileged child.

## 3. A `${name}_user` / `daemon(8)` collision is easy to walk into

Not a supervice bug, but it will bite every FreeBSD user, so it belongs in your
docs. FreeBSD's `rc.subr` interprets `${name}_user` by wrapping the whole
command in `su -m`. If the rc script *also* passes `daemon -u <user>`, the
`setuid` runs when the process is already unprivileged and fails with `EPERM` —
and `daemon -f` sends that error to `/dev/null`, so `service foo start` prints
`Starting foo.` and nothing runs. That cost me three debugging rounds.

Since supervice already supports `user =` per program, the cleanest FreeBSD
integration is: run the supervisor itself as root from rc.d with **no**
`${name}_user` and **no** `daemon -u`, and let supervice drop privileges per
program. A worked `rc.d` example in the docs would save people this entirely —
happy to contribute the one we now run in production.

## 4. Two supervisors, one pidfile

`daemon(8) -p <file>` writes its pidfile **as root, before dropping
privileges**. If `supervice.ini`'s `pidfile` points at the same path, supervice
(now unprivileged) cannot write it and dies with:

```
Supervice crashed: [Errno 13] Permission denied: '/var/run/tokengate/supervice.pid'
```

The message is good — it names the file — but two improvements would help:

- Allow `pidfile =` (empty) or `pidfile = none` to mean *don't write one*,
  which is the correct configuration when something else already supervises
  the supervisor. An empty value currently is not obviously supported.
- Create the pidfile/socket parent directory if missing, or fail at config load
  with "directory does not exist / not writable by <user>" rather than at
  spawn time.

## 5. `--version` is missing

```
$ supervice --version
supervice: error: unrecognized arguments: --version
```

Trivial, but it is the first thing anyone types when reporting a bug from a
host they do not own, and the first thing a deploy script records.

## 6. Smaller observations

- **Startup log is quiet by default.** At default loglevel the only line before
  the RPC socket appears is `Loading config from …`. When a program fails its
  `startsecs` window it is not obvious from the log alone what was tried. A
  single INFO line per program at spawn — resolved command, cwd, uid — would
  make first-run diagnosis much faster on a new platform.
- **`healthcheck_type = tcp` worked unmodified** on FreeBSD, including on a
  worker whose only listener is a health port. No action needed; recording it
  because it is the part I expected to be fragile and was not.
- **Config keys accepted but unimplemented should warn.** Same class as
  `pdeathsig`: any key parsed and then ignored on a platform should announce
  itself. A generic "these directives are inactive on this platform: …" line at
  startup would cover future cases automatically.
- **Docs/packaging**: worth stating the supported platform matrix explicitly in
  the README. We were happy to be the FreeBSD guinea pig, but "supervice targets
  Linux; FreeBSD works with these caveats" would have set expectations right.

## What we run in production now

```ini
[supervice]
logfile = /var/log/tokengate/supervice.log
pidfile = /var/run/tokengate/supervice.pid
socket  = /var/run/tokengate/supervice.sock

[program:api]
command = /opt/tokengate/venv/bin/python -m uvicorn tokengate_service.main:app --host 127.0.0.1 --port 8080
directory = /opt/tokengate/app
startsecs = 3
startretries = 3
stopsignal = TERM
stopwaitsecs = 10
healthcheck_type = tcp
healthcheck_port = 8080
; pdeathsig deliberately omitted: no-op on FreeBSD (see §1)

[program:worker]
command = /opt/tokengate/venv/bin/tokengate-worker
directory = /opt/tokengate/app
startsecs = 3
startretries = 3
stopsignal = TERM
stopwaitsecs = 15
healthcheck_type = tcp
healthcheck_port = 8091
```

Driven by `/usr/local/etc/rc.d/tokengate` through `daemon(8)`, with `HOME`
exported by a wrapper script (unrelated to supervice: asyncpg probes
`$HOME/.postgresql` for client certs and fails with `EACCES` when `HOME` is
`/root`).

Priority order if you only take three: **§1(b) warn on inactive directives**,
**§1(a) real FreeBSD pdeathsig**, **§2 env_file**.
