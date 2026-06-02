# Magi Agent

OpenMagi Python ADK runtime and CLI for personal AI agents.

This repository now tracks the Python ADK implementation used by OpenMagi's hosted runtime. It includes:

- `openmagi-core-agent` / `magi-agent` HTTP runtime entrypoints
- `magi` CLI for local/headless/TUI workflows
- first-party harness and recipe-pack contracts for research, coding, general automation, memory, scheduler, channel delivery, browser automation, document/spreadsheet automation, and evidence gates
- selected full-toolhost runtime boundaries for Clock, Calculation, FileRead, Glob, Grep, FileWrite, FileEdit, PatchApply, and Bash

## Install for local development

```bash
uv sync --extra dev --extra cli
uv run --extra cli magi --version
uv run --extra dev pytest -q
```

## Run the runtime

```bash
uv run openmagi-core-agent
# or
uv run magi-agent
```

The production hosted service still controls live authority with explicit environment gates. External integrations, broad production DB writes, billing mutations, channel delivery, browser automation, and scheduler authority must remain default-off unless explicitly configured and verified by the deployment operator.

## CLI

```bash
uv run --extra cli magi --help
uv run --extra cli magi --output text "Summarize this repository"
```

## License

Apache-2.0.

## More docs

- CLI handoff: `docs/notes/2026-05-31-magi-cli-track18-handoff-for-adk-migration.md`
- CLI design: `docs/plans/2026-05-30-magi-cli-design.md`
- Python ADK architecture: `docs/architecture/magi-agent-python-adk-architecture.md`
