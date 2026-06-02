# Handoff: Magi CLI Track 18 for the ADK migration owner

Date: 2026-05-31

Status: Track 18 has landed on `origin/main` as an additive Magi CLI surface for
the Python ADK runtime.

## TL;DR

Magi now has a `magi` CLI under
`magi-agent/magi_agent/cli/`.

It supports both:

- headless NDJSON/text modes
- a Textual/Rich terminal UI

Both modes drain the same engine generator:
`MagiEngineDriver.run_turn_stream(...)` in `cli/engine.py`.

Track 18 itself added the CLI as a separate package and did not require server
entrypoint changes. The CLI is still tightly coupled to the Python ADK runtime
contracts, so ADK migration work must keep these surfaces stable or update the
CLI projection and permission wiring at the same time.

## What landed

- Engine boundary: `cli/engine.py`
  `MagiEngineDriver.run_turn_stream(...)`.
- Headless output: `cli/headless.py` supports text, JSON, and stream JSON.
- Permissions: `cli/permissions.py` rules engine, wired through an ADK
  `before_tool_callback`.
- TUI: `cli/tui/**`, `cli/render/**`, and `cli/keybindings/**`.
- Session log: `cli/session_log.py` JSONL DAG and resume scaffolding.
- Entrypoint: `magi` console script via `pyproject.toml`.
- Optional CLI dependencies: `.[cli]` installs Textual, Rich, RapidFuzz, and
  Typer.
- Tests: `magi_agent/cli/tests` collects 347 tests on `origin/main`.

## ADK-coupled surfaces to watch

The CLI is a third consumer of the same Python runtime used by HTTP/SSE and
canary paths. During an ADK version bump, check these exact surfaces:

| Surface | Current location | CLI risk |
| --- | --- | --- |
| `OpenMagiRunnerAdapter.run_turn()` | `adk_bridge/runner_adapter.py:289` | ADK event shape changes can break CLI event projection. |
| `RunnerSessionBoundary.run_turn()` | `runtime/runner_session_boundary.py:410` | Main CLI turn path. |
| `_collect_runner_events()` | `runtime/runner_session_boundary.py:641` | Runner event collection semantics. |
| `OpenMagiEventBridge.project_adk_event()` | `adk_bridge/event_adapter.py:386` | Canonical ADK-to-runtime event projection. |
| `before_tool_callback` permission gate | `cli/wiring.py` | Security-sensitive callback signature/return contract. |
| `WorkspaceSessionService` | `adk_bridge/session_service.py:38` | CLI session create/get behavior. |
| `SessionContinuityBoundary.import_committed_transcript()` | `runtime/session_continuity.py:255` | CLI resume/rehydration boundary. |
| `ControlRequestStore` | `runtime/control.py:222` | Permission lifecycle and control requests. |
| `_sanitize_agent_event()` | `transport/sse.py:506` | Headless public output redaction. |

## Track 11 streaming constraint

The CLI can stream runtime events to the terminal, but this is still bounded by
the same ADK limitation from Track 11: ADK `base_llm_flow` currently blocks until
the full response before yielding. So the CLI's live output is coarse turn or
segment streaming, not true per-token model streaming.

If a later ADK version or flow patch yields incremental deltas, the CLI should
benefit through the existing `run_turn_stream(...)` generator. That is the
biggest ADK-version lever for CLI UX.

## Packaging and deploy coupling

Track 18 has not been activated through production deployment as a user-facing
runtime path. It should ride the same pending `core-agent-python` image train
only after packaging is explicit.

Current packaging facts:

- `pyproject.toml` always declares the `magi` console script.
- CLI runtime dependencies are in the optional `cli` extra.
- The current Dockerfile installs `.` rather than `.[cli]`.

That means the script can exist in an image while normal CLI/TUI commands still
miss their optional dependencies. The ADK migration owner should decide one of:

- build the core Python image with `pip install .[cli]`;
- ship CLI as a separate package/artifact;
- keep CLI source-only until local packaging is ready.

Also confirm whether `MAGI_CLI_ENABLED` default-on is acceptable for the chosen
image/package. The CLI is additive, but dependency and support boundaries should
be explicit before promoting it in install docs.

## Migration verification checklist

Run from `magi-agent` after any ADK bump or runtime
event/callback change:

```bash
uv run --extra dev --extra cli pytest magi_agent/cli/tests -q
uv run --extra cli magi --version
uv run --extra cli magi -h
```

For image packaging changes, also smoke the built image with the selected
install mode and confirm whether `magi` can start beyond fast-path help/version
commands.

## Not done / v1.1 backlog

- `--continue` engine rehydration is still partial.
- Usage/cost accounting is not complete.
- Per-token assistant consolidation and `parent_tool_use_id` threading depend on
  the Track 11/ADK streaming constraint.
- Slash-command expansion remains future work.
- Durable transcript persistence and user keybinding loading remain future work.
- Vim mode and keybinding hot reload remain future work.

## Pointers

- Design: `docs/plans/2026-05-30-magi-cli-design.md`
- Claude Code CLI teardown: `docs/architecture/claude-code-cli/00-overview.md`
- Execution prompts: `docs/plans/prompts/magi-track-18-*.md`
- Landed code: `magi-agent/magi_agent/cli/`
- Roadmap context:
  `docs/plans/2026-05-27-magi-python-adk-improvement-plans.md`
