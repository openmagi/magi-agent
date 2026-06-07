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

- `ANTHROPIC_API_KEY` — selects the `anthropic` provider. Default model `claude-sonnet-4-5`.
- `OPENAI_API_KEY` — selects the `openai` provider. Default model `gpt-4o`.
- `GEMINI_API_KEY` — selects the `gemini` provider. Default model `gemini-2.0-flash`.
- `GOOGLE_API_KEY` — alias accepted for the `gemini` provider (used when `GEMINI_API_KEY` is unset).
- `FIREWORKS_API_KEY` — selects the `fireworks` provider. Default model `accounts/fireworks/models/llama-v3p1-70b-instruct`.

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

The authority flags below correspond to PythonRuntimeAuthorityConfig fields. All
must be `false` or omitted. The `Literal[False]` type annotation means the runtime
structurally rejects attempts to set them to true.

- `TRANSCRIPT_WRITE` — Must be false. Controls transcript write authority.
- `SSE_WRITE` — Must be false. Controls SSE write authority.
- `CHANNEL_DELIVERY` — Must be false. Controls channel delivery authority.
- `DB_WRITE` — Must be false. Controls database write authority.
- `WORKSPACE_MUTATION` — Must be false. Controls workspace mutation authority.
- `CHILD_EXECUTION` — Must be false. Controls child agent execution authority.
- `MISSION_RUNTIME` — Must be false. Controls mission runtime authority.
- `EVIDENCE_BLOCK_MODE` — Must be false. Controls evidence blocking mode.

- [Default-off gates](/docs/default-off-gates)
- [Config reference](/docs/config-reference)
