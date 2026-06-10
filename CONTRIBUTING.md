# Contributing to Magi

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
uv sync --extra dev --extra cli
uv run --extra cli magi --help
```

## Running Tests

```bash
uv run --extra dev --extra cli pytest -q
```

Run lint and (advisory) type checks the same way CI does:

```bash
uv run --extra dev ruff check .
uv run --extra dev mypy magi_agent   # advisory: CI does not block on this yet
```

## Code Style

- Prefer typed Python APIs with clear return types at public boundaries.
- Keep runtime changes scoped to the canonical `magi_agent` package.
- Follow the existing module structure before adding new abstractions.
- Keep public docs focused on user-facing behavior and stable contracts.

## Pull Requests

1. Fork the repo and create a feature branch
2. Write tests for new functionality
3. Ensure the relevant `uv run --extra dev ...` checks pass
4. Submit a PR with a clear description

## Reporting Issues

Use [GitHub Issues](https://github.com/openmagi/magi-agent/issues). Include:
- Steps to reproduce
- Expected vs actual behavior
- Magi Agent version, Python version, and OS
