# Contributing

## Development Setup

```bash
git clone https://github.com/yourusername/supervice.git
cd supervice
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[docs]"
```

## Running Tests

```bash
python3 -m pytest tests/ -v
```

All 63 tests should pass. Tests use `unittest` with `asyncio` support.

## Code Quality

All code must pass these checks before merging:

```bash
mypy --strict supervice/
ruff check supervice/
ruff format --check supervice/
```

### Type Checking

Supervice uses `mypy --strict`. Rules:

- All functions must have type annotations
- No `Any` types unless absolutely necessary
- No `type: ignore` or `@ts-expect-error` suppressions
- Union types use `X | Y` syntax (Python 3.10+)

### Linting

Ruff is configured with these rule sets:

| Rule Set | Description |
|----------|-------------|
| `E` | pycodestyle errors |
| `F` | pyflakes |
| `W` | pycodestyle warnings |
| `I` | isort (import sorting) |
| `N` | pep8-naming |
| `UP` | pyupgrade |
| `B` | flake8-bugbear |

Line length: 100 characters.

## Project Structure

```
supervice/
├── supervice/          # Source code
│   ├── __init__.py
│   ├── main.py         # Daemon entry point
│   ├── core.py         # Supervisor orchestrator
│   ├── process.py      # Process lifecycle
│   ├── config.py       # Config parser
│   ├── models.py       # Data models
│   ├── events.py       # EventBus
│   ├── rpc.py          # RPC server
│   ├── client.py       # CLI client
│   ├── health.py       # Health checks
│   └── logger.py       # Logging
├── tests/              # Test suite
├── docs/               # Sphinx documentation
├── pyproject.toml      # Project config
├── README.md
└── LICENSE
```

## Making Changes

### Adding a Configuration Option

1. Add the field to the appropriate dataclass in `models.py`
2. Parse it in `config.py` (add validation if needed)
3. Use it in `process.py` or `core.py`
4. Add tests
5. Update documentation

### Adding an RPC Command

1. Add the command name to `VALID_COMMANDS` in `rpc.py`
2. Add a handler in `RPCServer.process_request()`
3. Add a CLI subcommand in `client.py`
4. Add a method to the `Controller` class
5. Add tests
6. Update documentation

### Adding a Process State

1. Define the state constant in `process.py`
2. Add the corresponding `EventType` in `events.py`
3. Add the state mapping in `Process._change_state()`
4. Update state transition logic in `Process.supervise()`
5. Add tests
6. Update documentation

## Constraints

- **Zero dependencies** — Do not add external packages. Use stdlib only.
- **Python 3.10+** — Use modern syntax (`match`, `X | Y` unions, etc.)
- **Unix-only** — No Windows compatibility needed
- **Async-first** — All I/O must use `asyncio`

## Building Documentation

```bash
cd docs
make html
```

Output is in `docs/_build/html/`. Open `index.html` in a browser.

## Code Style Tips

- Prefer `%` string formatting over f-strings (existing codebase convention for log messages)
- Use `shlex.split()` for command parsing
- Use `asyncio.wait_for()` with timeouts, never bare `await` on external I/O
- Process groups: always `os.killpg()` instead of `process.kill()`
