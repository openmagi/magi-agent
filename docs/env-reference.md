# Environment Variable Reference

Status: ✅ Active (local CLI) / 🚧 Default-off (hosted authority/gate flags) — the
local `magi` CLI runs with a single provider key; hosted runtime variables are
read only by the managed deployment.

Environment variables grouped by where they apply: the **Local CLI** (everything
a local user needs) and the **Hosted / managed runtime** (read by the managed
deployment only — a local CLI never needs them).

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

That is all a local user needs. Everything below is for the hosted deployment.

## Hosted / managed runtime only (NOT needed for local CLI)

> The local `magi` CLI does **not** read or require any variable in this section.
> These populate `RuntimeConfig` and related configs in the hosted/managed
> deployment. Setting them locally has no effect on the local CLI.

### Hosted identity and services

- `BOT_ID` — Bot identifier. Maps to RuntimeConfig.bot_id.
- `USER_ID` — Owner user identifier. Maps to RuntimeConfig.user_id.
- `GATEWAY_TOKEN` — API gateway bearer token. Maps to RuntimeConfig.gateway_token.
- `CORE_AGENT_API_PROXY_URL` — URL of the API proxy service. Maps to RuntimeConfig.api_proxy_url.
- `CORE_AGENT_CHAT_PROXY_URL` — URL of the chat proxy service. Maps to RuntimeConfig.chat_proxy_url.
- `CORE_AGENT_REDIS_URL` — URL of the Redis instance. Maps to RuntimeConfig.redis_url.
- `CORE_AGENT_MODEL` — LLM model identifier for the hosted runtime. Maps to RuntimeConfig.model.

### Hosted server

- `CORE_AGENT_PORT` (default 8080) — HTTP port the hosted agent server listens on.

### Hosted build provenance

Build metadata. These populate BuildInfo and are typically set by CI/CD pipelines.

- `CORE_AGENT_VERSION` — Semantic version string. Falls back to "0.1.0-adk-scaffold".
- `CORE_AGENT_BUILD_SHA` — Git commit SHA. Fallback chain: CORE_AGENT_BUILD_SHA -> git rev-parse HEAD -> None.
- `IMAGE_REPO` — Container image repository (for public examples, ghcr.io/openmagi/magi-agent-runtime).
- `IMAGE_TAG` — Container image tag (e.g. 0.19.70).
- `IMAGE_DIGEST` — Container image digest (sha256:...).

### Hosted memory adapter

Control the Python memory adapter subsystem. These populate PythonMemoryAdapterConfig.

- `CORE_AGENT_PYTHON_MEMORY_ADAPTER` (default "off") — Adapter provider ref. Values: "off", "hipocampus_qmd_readonly".
- `MEMORY_ADAPTER_MODE` (default "disabled") — Operating mode. Values: "disabled", "readonly_fixture", "readonly_local".
- `MEMORY_WORKSPACE_ROOT` — Workspace root path for local memory adapters.

### Hosted ToolHost attachment

Control the Python ToolHost attachment subsystem. These populate PythonToolHostAttachmentConfig.

- `CORE_AGENT_PYTHON_ADK_TOOLHOST_ATTACH` (default "0") — Whether to attach the ToolHost. Set to "1" to enable.
- `TOOLHOST_MODE` (default "disabled") — ToolHost operating mode. Values: "disabled", "shadow_readonly".

### Hosted output mode

Control what the hosted Python runtime is allowed to output.

- `CORE_AGENT_PYTHON_OUTPUT_MODE` (default "off") — Output mode. Values: "diagnostic_only" (internal diagnostics only), "health_only" (health endpoint only), "off" (no output), "user_visible_canary" (canary user-visible output).

### Hosted authority flags

These correspond to PythonRuntimeAuthorityConfig fields. All must be "false" (or omitted, as the default is False). The Literal[False] type annotation means the runtime structurally rejects any attempt to set them to true.

- `TRANSCRIPT_WRITE` — Must be false. Controls transcript write authority.
- `SSE_WRITE` — Must be false. Controls SSE write authority.
- `CHANNEL_DELIVERY` — Must be false. Controls channel delivery authority.
- `DB_WRITE` — Must be false. Controls database write authority.
- `WORKSPACE_MUTATION` — Must be false. Controls workspace mutation authority.
- `CHILD_EXECUTION` — Must be false. Controls child agent execution authority.
- `MISSION_RUNTIME` — Must be false. Controls mission runtime authority.
- `EVIDENCE_BLOCK_MODE` — Must be false. Controls evidence blocking mode.

### Hosted gate readiness (Gate 5B canary / Gate 3A replay)

Gate readiness configurations use environment variables to control per-gate kill switches, environment allowlists, and canary selection. Each gate (2 through 8) has its own readiness config with structurally-false authority flags.

- Gate 5B canary flags: GATE5_KILL_SWITCH_ENABLED (default true), GATE5_ENVIRONMENT, GATE5_ENVIRONMENT_ALLOWLIST, GATE5_MAX_SHADOW_CHECKS (default 0).
- Gate 3A replay flags: GATE3_KILL_SWITCH_ENABLED (default true), GATE3_ENVIRONMENT, GATE3_ENVIRONMENT_ALLOWLIST, GATE3_MAX_REPLAY_BUNDLES (default 0).
- All gate readiness configs share the pattern: enabled, kill_switch_enabled, selected_bot_digest, selected_owner_user_id_digest, environment, environment_allowlist, plus gate-specific limits.

- [Default-off gates](/docs/default-off-gates)
- [Config reference](/docs/config-reference)
