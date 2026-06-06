# Environment Variable Reference

Complete reference for environment variables that control Magi Agent runtime behavior, grouped by category.

Every environment variable recognized by the Magi Agent runtime: identity, server, build, memory, ToolHost, tool concurrency, output mode, authority flags, and gate canary flags.

## Required Variables

These variables must be set for the runtime to start. They populate the required fields of RuntimeConfig.

- BOT_ID — Bot identifier. Maps to RuntimeConfig.bot_id.
- USER_ID — Owner user identifier. Maps to RuntimeConfig.user_id.
- GATEWAY_TOKEN — API gateway bearer token. Maps to RuntimeConfig.gateway_token.
- CORE_AGENT_API_PROXY_URL — URL of the API proxy service. Maps to RuntimeConfig.api_proxy_url.
- CORE_AGENT_CHAT_PROXY_URL — URL of the chat proxy service. Maps to RuntimeConfig.chat_proxy_url.
- CORE_AGENT_REDIS_URL — URL of the Redis instance. Maps to RuntimeConfig.redis_url.
- CORE_AGENT_MODEL — LLM model identifier (e.g. claude-sonnet-4-20250514). Maps to RuntimeConfig.model.

## Server Variables

Server configuration for the Magi Agent HTTP process.

- CORE_AGENT_PORT (default 8080) — HTTP port the agent server listens on.

## Build Variables

Build provenance metadata. These populate BuildInfo and are typically set by CI/CD pipelines.

- CORE_AGENT_VERSION — Semantic version string. Falls back to "0.1.0-adk-scaffold".
- CORE_AGENT_BUILD_SHA — Git commit SHA. Fallback chain: CORE_AGENT_BUILD_SHA -> git rev-parse HEAD -> None.
- IMAGE_REPO — Container image repository (for public examples, ghcr.io/openmagi/magi-agent-runtime).
- IMAGE_TAG — Container image tag (e.g. 0.19.70).
- IMAGE_DIGEST — Container image digest (sha256:...).

## Memory Variables

Control the Python memory adapter subsystem. These populate PythonMemoryAdapterConfig.

- CORE_AGENT_PYTHON_MEMORY_ADAPTER (default "off") — Adapter provider ref. Values: "off", "hipocampus_qmd_readonly".
- MEMORY_ADAPTER_MODE (default "disabled") — Operating mode. Values: "disabled", "readonly_fixture", "readonly_local".
- MEMORY_WORKSPACE_ROOT — Workspace root path for local memory adapters.

## ToolHost Variables

Control the Python ToolHost attachment subsystem. These populate PythonToolHostAttachmentConfig.

- CORE_AGENT_PYTHON_ADK_TOOLHOST_ATTACH (default "0") — Whether to attach the ToolHost. Set to "1" to enable.
- TOOLHOST_MODE (default "disabled") — ToolHost operating mode. Values: "disabled", "shadow_readonly".

## Tool Concurrency Variables

Control parallel tool execution. When enabled, the runtime can execute multiple tool calls concurrently within a single turn.

- MAGI_TOOL_CONCURRENCY_ENABLED (default "0") — Set to "1" to enable concurrent tool execution.
- MAGI_MAX_TOOL_CONCURRENCY (default 8) — Maximum number of concurrent tool executions per turn.

## Output Mode Variables

Control what the Python runtime is allowed to output.

- CORE_AGENT_PYTHON_OUTPUT_MODE (default "off") — Output mode. Values: "diagnostic_only" (internal diagnostics only), "health_only" (health endpoint only), "off" (no output), "user_visible_canary" (canary user-visible output).

## Authority Flag Variables

These variables correspond to PythonRuntimeAuthorityConfig fields. All must be set to "false" (or omitted, as the default is False). The Literal[False] type annotation means the runtime structurally rejects any attempt to set them to true.

- TRANSCRIPT_WRITE — Must be false. Controls transcript write authority.
- SSE_WRITE — Must be false. Controls SSE write authority.
- CHANNEL_DELIVERY — Must be false. Controls channel delivery authority.
- DB_WRITE — Must be false. Controls database write authority.
- WORKSPACE_MUTATION — Must be false. Controls workspace mutation authority.
- CHILD_EXECUTION — Must be false. Controls child agent execution authority.
- MISSION_RUNTIME — Must be false. Controls mission runtime authority.
- EVIDENCE_BLOCK_MODE — Must be false. Controls evidence blocking mode.

## Gate 5B Canary and Gate 3A Replay Variables

Gate readiness configurations use environment variables to control per-gate kill switches, environment allowlists, and canary selection. Each gate (2 through 8) has its own readiness config with structurally-false authority flags.

- Gate 5B canary flags: GATE5_KILL_SWITCH_ENABLED (default true), GATE5_ENVIRONMENT, GATE5_ENVIRONMENT_ALLOWLIST, GATE5_MAX_SHADOW_CHECKS (default 0).
- Gate 3A replay flags: GATE3_KILL_SWITCH_ENABLED (default true), GATE3_ENVIRONMENT, GATE3_ENVIRONMENT_ALLOWLIST, GATE3_MAX_REPLAY_BUNDLES (default 0).
- All gate readiness configs share the pattern: enabled, kill_switch_enabled, selected_bot_digest, selected_owner_user_id_digest, environment, environment_allowlist, plus gate-specific limits.

- [Default-off gates](/docs/default-off-gates)
- [Config reference](/docs/config-reference)
