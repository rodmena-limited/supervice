import configparser
import os
from supervice.models import (
    HealthCheckConfig,
    HealthCheckType,
    ProgramConfig,
    SupervisorConfig,
)
VALID_SIGNALS = frozenset(
    {
        "HUP",
        "INT",
        "QUIT",
        "ILL",
        "TRAP",
        "ABRT",
        "BUS",
        "FPE",
        "KILL",
        "USR1",
        "SEGV",
        "USR2",
        "PIPE",
        "ALRM",
        "TERM",
        "STKFLT",
        "CHLD",
        "CONT",
        "STOP",
        "TSTP",
        "TTIN",
        "TTOU",
        "URG",
        "XCPU",
        "XFSZ",
        "VTALRM",
        "PROF",
        "WINCH",
        "IO",
        "PWR",
        "SYS",
    }
)

def _parse_bool(value: str) -> bool:
    return value.lower() in ("true", "1", "yes", "on")

def _parse_env(value: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not value:
        return env

    i = 0
    n = len(value)
    while i < n:
        while i < n and value[i] in (" ", "\t"):
            i += 1
        if i >= n:
            break

        eq_pos = value.find("=", i)
        if eq_pos < 0:
            break
        key = value[i:eq_pos].strip()
        i = eq_pos + 1

        while i < n and value[i] in (" ", "\t"):
            i += 1
        if i >= n:
            env[key] = ""
            break

        if value[i] in ('"', "'"):
            quote = value[i]
            i += 1
            val_start = i
            while i < n and value[i] != quote:
                i += 1
            env[key] = value[val_start:i]
            if i < n:
                i += 1
            while i < n and value[i] in (",", " ", "\t"):
                i += 1
        else:
            val_start = i
            while i < n and value[i] != ",":
                i += 1
            env[key] = value[val_start:i].strip()
            if i < n:
                i += 1

    return env

def _validate_signal(sig_name: str, program_name: str) -> None:
    """Validate that a signal name is valid."""
    sig_upper = sig_name.upper()
    if sig_upper not in VALID_SIGNALS:
        # Also check with SIG prefix stripped
        if sig_upper.startswith("SIG"):
            sig_upper = sig_upper[3:]
        if sig_upper not in VALID_SIGNALS:
            raise ConfigValidationError(
                "Program '%s': invalid stopsignal '%s'. Valid signals: %s"
                % (program_name, sig_name, ", ".join(sorted(VALID_SIGNALS)))
            )

def _validate_user(username: str, program_name: str) -> None:
    """Validate that a user exists on the system."""
    import pwd

    try:
        pwd.getpwnam(username)
    except KeyError as e:
        raise ConfigValidationError(
            "Program '%s': user '%s' does not exist" % (program_name, username)
        ) from e

class ConfigValidationError(ValueError):
    """Raised when config validation fails."""
