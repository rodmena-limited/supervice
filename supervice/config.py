import configparser
import os

from supervice.models import (
    HealthCheckConfig,
    HealthCheckType,
    ProgramConfig,
    SupervisorConfig,
)

# Valid signal names (without SIG prefix)
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


class ConfigValidationError(ValueError):
    """Raised when config validation fails."""

    pass


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


def _validate_directory(directory: str, program_name: str) -> None:
    """Validate that a directory exists and is accessible."""
    if not os.path.exists(directory):
        raise ConfigValidationError(
            "Program '%s': directory '%s' does not exist" % (program_name, directory)
        )
    if not os.path.isdir(directory):
        raise ConfigValidationError(
            "Program '%s': '%s' is not a directory" % (program_name, directory)
        )
    if not os.access(directory, os.X_OK):
        raise ConfigValidationError(
            "Program '%s': directory '%s' is not accessible" % (program_name, directory)
        )


def _validate_logfile_path(logfile: str, program_name: str) -> None:
    """Validate that the parent directory of a logfile exists and is writable."""
    parent_dir = os.path.dirname(logfile) or "."
    if not os.path.exists(parent_dir):
        raise ConfigValidationError(
            "Program '%s': log directory '%s' does not exist" % (program_name, parent_dir)
        )
    if not os.access(parent_dir, os.W_OK):
        raise ConfigValidationError(
            "Program '%s': log directory '%s' is not writable" % (program_name, parent_dir)
        )


def _validate_positive_int(value: int, field_name: str, program_name: str) -> None:
    """Validate that a value is a positive integer."""
    if value < 0:
        raise ConfigValidationError(
            "Program '%s': %s must be non-negative, got %d" % (program_name, field_name, value)
        )


def _validate_healthcheck(hc: HealthCheckConfig, program_name: str) -> None:
    """Validate health check configuration."""
    _validate_positive_int(hc.interval, "healthcheck_interval", program_name)
    _validate_positive_int(hc.timeout, "healthcheck_timeout", program_name)
    _validate_positive_int(hc.retries, "healthcheck_retries", program_name)
    _validate_positive_int(hc.start_period, "healthcheck_start_period", program_name)

    if hc.interval == 0:
        raise ConfigValidationError(
            "Program '%s': healthcheck_interval must be at least 1" % program_name
        )

    if hc.type == HealthCheckType.TCP:
        if hc.port is None:
            raise ConfigValidationError(
                "Program '%s': healthcheck_port is required for TCP health checks" % program_name
            )
        if hc.port < 1 or hc.port > 65535:
            raise ConfigValidationError(
                "Program '%s': healthcheck_port must be between 1 and 65535" % program_name
            )
    elif hc.type == HealthCheckType.SCRIPT:
        if not hc.command:
            raise ConfigValidationError(
                "Program '%s': healthcheck_command required for script checks" % program_name
            )


def _validate_program(prog: ProgramConfig) -> None:
    """Validate a program configuration."""
    # Validate numeric bounds
    _validate_positive_int(prog.numprocs, "numprocs", prog.name)
    _validate_positive_int(prog.startsecs, "startsecs", prog.name)
    _validate_positive_int(prog.startretries, "startretries", prog.name)
    _validate_positive_int(prog.stopwaitsecs, "stopwaitsecs", prog.name)

    if prog.numprocs == 0:
        raise ConfigValidationError("Program '%s': numprocs must be at least 1" % prog.name)

    # Validate signal
    _validate_signal(prog.stopsignal, prog.name)

    # Validate user if specified
    if prog.user:
        _validate_user(prog.user, prog.name)

    # Validate directory if specified
    if prog.directory:
        _validate_directory(prog.directory, prog.name)

    # Validate log file paths if specified
    if prog.stdout_logfile:
        _validate_logfile_path(prog.stdout_logfile, prog.name)
    if prog.stderr_logfile:
        _validate_logfile_path(prog.stderr_logfile, prog.name)

    # Validate health check if configured
    if prog.healthcheck.type != HealthCheckType.NONE:
        _validate_healthcheck(prog.healthcheck, prog.name)


def parse_config(path: str) -> SupervisorConfig:
    if not os.path.exists(path):
        raise FileNotFoundError("Config file not found: %s" % path)

    parser = configparser.ConfigParser()
    parser.read(path)

    sup_config = SupervisorConfig()

    if parser.has_section("supervice"):
        sect = parser["supervice"]
        sup_config.logfile = sect.get("logfile", sup_config.logfile)
        sup_config.pidfile = sect.get("pidfile", sup_config.pidfile)
        sup_config.loglevel = sect.get("loglevel", sup_config.loglevel)
        sup_config.socket_path = sect.get("socket", sup_config.socket_path)
        sup_config.shutdown_timeout = sect.getint("shutdown_timeout", sup_config.shutdown_timeout)
        sup_config.log_maxbytes = sect.getint("log_maxbytes", sup_config.log_maxbytes)
        sup_config.log_backups = sect.getint("log_backups", sup_config.log_backups)

        # Validate loglevel
        valid_levels = {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}
        if sup_config.loglevel.upper() not in valid_levels:
            raise ConfigValidationError(
                "Invalid loglevel '%s'. Valid levels: %s"
                % (sup_config.loglevel, ", ".join(sorted(valid_levels)))
            )

        # Validate numeric bounds
        if sup_config.shutdown_timeout <= 0:
            raise ConfigValidationError("shutdown_timeout must be positive")
        if sup_config.log_maxbytes < 0:
            raise ConfigValidationError("log_maxbytes must be non-negative")
        if sup_config.log_backups < 0:
            raise ConfigValidationError("log_backups must be non-negative")

    for section in parser.sections():
        if section.startswith("program:"):
            name = section.split(":", 1)[1]
            sect = parser[section]

            # Parse health check configuration
            hc_type_str = sect.get("healthcheck_type", "none").lower()
            hc_type = HealthCheckType.NONE
            if hc_type_str == "tcp":
                hc_type = HealthCheckType.TCP
            elif hc_type_str == "script":
                hc_type = HealthCheckType.SCRIPT

            healthcheck = HealthCheckConfig(
                type=hc_type,
                interval=sect.getint("healthcheck_interval", 30),
                timeout=sect.getint("healthcheck_timeout", 10),
                retries=sect.getint("healthcheck_retries", 3),
                start_period=sect.getint("healthcheck_start_period", 10),
                port=sect.getint("healthcheck_port") if sect.get("healthcheck_port") else None,
                host=sect.get("healthcheck_host", "127.0.0.1"),
                command=sect.get("healthcheck_command"),
            )

            prog = ProgramConfig(
                name=name,
                command=sect.get("command", ""),
                numprocs=sect.getint("numprocs", 1),
                autostart=_parse_bool(sect.get("autostart", "true")),
                autorestart=_parse_bool(sect.get("autorestart", "true")),
                startsecs=sect.getint("startsecs", 1),
                startretries=sect.getint("startretries", 3),
                stopsignal=sect.get("stopsignal", "TERM"),
                stopwaitsecs=sect.getint("stopwaitsecs", 10),
                stdout_logfile=sect.get("stdout_logfile"),
                stderr_logfile=sect.get("stderr_logfile"),
                environment=_parse_env(sect.get("environment", "")),
                directory=sect.get("directory"),
                user=sect.get("user"),
                healthcheck=healthcheck,
            )

            if not prog.command:
                raise ConfigValidationError("Program '%s': missing command" % name)

            # Validate the program configuration
            _validate_program(prog)

            sup_config.programs.append(prog)

    # Process groups
    # Groups are defined as [group:foo]
    # programs=bar,baz
    for section in parser.sections():
        if section.startswith("group:"):
            group_name = section.split(":", 1)[1]
            sect = parser[section]
            programs_str = sect.get("programs", "")
            if not programs_str:
                continue

            program_names = [p.strip() for p in programs_str.split(",")]

            # Verify and update programs
            for prog in sup_config.programs:
                if prog.name in program_names:
                    prog.group = group_name

    return sup_config
