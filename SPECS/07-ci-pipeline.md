# GitHub Actions CI pipeline — EARS spec

Ticket: `issuedb` #7 (in-progress)
Source: user request 2026-08-03 (no CI existed)

## Requirements

- On every push to main and every pull request, the CI pipeline shall run
  ruff, mypy, and the full pytest suite on Python 3.10, 3.11, 3.12, and 3.13
  and pass.
