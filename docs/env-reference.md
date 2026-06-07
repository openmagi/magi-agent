# Environment Variable Reference

Status: ✅ Active — the local `magi` CLI and dashboard run with a single
provider key or `~/.magi/config.toml`.

This page lists the environment variables a local user or self-hosted operator
needs. Platform-specific deployment variables are intentionally outside this
public local reference.

## Local CLI — required: one provider key

The local `magi` CLI needs exactly ONE of the following to talk to a real model.
Set a provider API key in your environment, or point the CLI at a
`~/.magi/config.toml`. With none configured, `magi` still launches but uses a
model-free stub runner.

Provider keys (the CLI auto-detects the first one present, in this order):

- `ANTHROPIC_API_KEY` — selects the `anthropic` provider. Default model `claude-sonnet-4-6`.
- `OPENAI_API_KEY` — selects the `openai` provider. Default model `gpt-5.5`.
- `GEMINI_API_KEY` — selects the `gemini` provider. Default model `gemini-3.5-flash`.
- `GOOGLE_API_KEY` — alias accepted for the `gemini` provider (used when `GEMINI_API_KEY` is unset).
- `FIREWORKS_API_KEY` — selects the `fireworks` provider. Default model `accounts/fireworks/models/kimi-k2-instruct`.

> Default model ids drift as providers retire names; override with `MAGI_MODEL`
> or `[model].model`. The authoritative defaults live in `magi_agent/cli/providers.py`.

Provider / model selection:

- `MAGI_PROVIDER` — force a specific provider (`anthropic`, `openai`, `gemini`, `fireworks`) instead of auto-detecting.
- `MAGI_MODEL` — override the model id for the selected provider.

Config file alternative (instead of, or in addition to, env keys):

- `MAGI_CONFIG` — path to the TOML config file. Defaults to `~/.magi/config.toml`.
  The file may set `[model].provider`, `[model].model`, `[model].api_key`, and
  per-provider keys under `[providers.<name>].api_key`.

Useful local toggles:

- `MAGI_CLI_ENABLED` (default on) — set to `0`/`false`/`no`/`off` to disable the CLI (it then exits with code 2).
- `MAGI_FIRST_PARTY_TOOLS_ENABLED` (default on) — set to `0`/`false`/`no`/`off` to disable Magi's first-party local tools once a real model runner is configured.
- `MAGI_TOOL_CONCURRENCY_ENABLED` (default `0`) — set to `1` to allow concurrent tool execution within a turn.
- `MAGI_MAX_TOOL_CONCURRENCY` (default `8`) — maximum concurrent tool executions per turn.
- `MAGI_RUNTIME_PROFILE` — selects a runtime profile (`magi_agent/config/env.py`).
- `MAGI_MEMORY_WRITE_ENABLED` (default off) — gates the `MemoryWrite` tool; memory is read-only unless enabled.
- `MAGI_EDIT_FUZZY_MATCH_ENABLED` — enables fuzzy matching for the edit tool.
- `MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT` — enables edit-match evidence enforcement.

## Local server

- `CORE_AGENT_PORT` (default `8080`) — HTTP port used by `magi-agent serve`.

## Build metadata

These are optional and usually set by release or container builds.

- `CORE_AGENT_VERSION` — Semantic version string.
- `CORE_AGENT_BUILD_SHA` — Git commit SHA.
- `IMAGE_REPO` — Container image repository.
- `IMAGE_TAG` — Container image tag.
- `IMAGE_DIGEST` — Container image digest.

## Local memory and ToolHost options

- `MEMORY_WORKSPACE_ROOT` — Workspace root path for local memory adapters.
- `MAGI_FIRST_PARTY_TOOLS_ENABLED` — Disable first-party tools when set to
  `0`/`false`/`no`/`off`.

## Authority and rollout flags

The authority flags below correspond to PythonRuntimeAuthorityConfig fields and
are read by packaged server deployments only (the local CLI does not read them).
All must be `false` or omitted. The `Literal[False]` type annotation means the
runtime structurally rejects attempts to set them to true, and the env parser
raises if any is set truthy. The env var names carry the `CORE_AGENT_PYTHON_`
prefix (`magi_agent/config/env.py`):

- `CORE_AGENT_PYTHON_TRANSCRIPT_WRITE` — Must be false. Transcript write authority.
- `CORE_AGENT_PYTHON_SSE_WRITE` — Must be false. SSE write authority.
- `CORE_AGENT_PYTHON_CHANNEL_DELIVERY` — Must be false. Channel delivery authority.
- `CORE_AGENT_PYTHON_DB_WRITE` — Must be false. Database write authority.
- `CORE_AGENT_PYTHON_WORKSPACE_MUTATION` — Must be false. Workspace mutation authority.
- `CORE_AGENT_PYTHON_CHILD_EXECUTION` — Must be false. Child agent execution authority.
- `CORE_AGENT_PYTHON_MISSION_RUNTIME` — Must be false. Mission runtime authority.
- `CORE_AGENT_PYTHON_EVIDENCE_BLOCK_MODE` — Must be false. Evidence blocking mode.

- [Security](/docs/security)
- [Config reference](/docs/config-reference)
