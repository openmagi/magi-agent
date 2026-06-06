# Config Reference

Complete reference for RuntimeConfig (config/env.py), PythonMemoryAdapterConfig, PythonToolHostAttachmentConfig, PythonRuntimeAuthorityConfig, and all Literal[False] safety invariants.

Every RuntimeConfig sub-model, its fields, types, defaults, and Literal[False] safety invariants. RuntimeConfig is constructed from env vars by parse_runtime_env() in main.py and carries: bot_id, user_id, gateway_token, service URLs, model, runtime='core-agent', runtime_engine='adk-python', build info, memory config, toolhost config, and authority config (all authority flags False).

> The Literal[False] authority flags below scope the **enforcement/governance layer and external delivery** (when a boundary may block/gate behavior, write to channels, or attach to production) — they do **not** mean the runtime is inert. With a provider key, the local `magi` CLI runs a real model + first-party tools today; see [What works today](/docs/what-works-today).

## RuntimeConfig (top-level)

RuntimeConfig is the frozen Pydantic model that carries the full runtime configuration for a Magi Agent process. It is constructed once at startup from environment variables and never mutated.

- bot_id (str, alias botId) — Bot identifier, required.
- user_id (str, alias userId) — Owner user identifier, required.
- gateway_token (str, alias gatewayToken) — API gateway bearer token, required.
- api_proxy_url (AnyUrl, alias apiProxyUrl) — URL of the API proxy service, required.
- chat_proxy_url (AnyUrl, alias chatProxyUrl) — URL of the chat proxy service, required.
- redis_url (AnyUrl, alias redisUrl) — URL of the Redis instance, required.
- model (str) — LLM model identifier (e.g. claude-sonnet-4-20250514), required.
- runtime (Literal["core-agent"], default "core-agent") — Runtime engine identifier.
- runtime_engine (Literal["adk-python"], alias runtimeEngine, default "adk-python") — Engine variant.
- build (BuildInfo) — Version, build SHA, image repo/tag/digest metadata.
- memory (PythonMemoryAdapterConfig) — Memory adapter configuration.
- toolhost (PythonToolHostAttachmentConfig) — ToolHost attachment configuration.
- security_posture (PythonSecurityPostureConfig, alias securityPosture) — Security posture flags.
- context_continuity (PythonContextContinuityConfig, alias contextContinuity) — Context continuity canary state.
- gate2_readiness through gate8_readiness — Per-gate readiness configs with Literal[False] authority flags.
- authority (PythonRuntimeAuthorityConfig) — Top-level runtime authority flags.

- [Environment variables](/docs/env-reference)
- [Boundaries overview](/docs/boundaries)

## BuildInfo

BuildInfo carries version and image provenance metadata. All fields except version are optional and populated from environment variables at startup.

- version (str, default "0.1.0-adk-scaffold") — Semantic version string.
- build_sha (str | None, alias buildSha) — Git commit SHA of the build.
- image_repo (str | None, alias imageRepo) — Container image repository.
- image_tag (str | None, alias imageTag) — Container image tag.
- image_digest (str | None, alias imageDigest) — Container image digest (sha256:...).

## PythonMemoryAdapterConfig

Controls the Python memory adapter subsystem. The adapter field selects a provider (default "off"). Three fields are structurally locked to Literal[False] to prevent memory projection or live provider calls.

- enabled (bool, default False) — Whether the memory adapter is active.
- mode (Literal["disabled", "readonly_fixture", "readonly_local"], default "disabled") — Adapter operating mode.
- adapter (str, default "off") — Provider adapter ref. Valid: "off" or a safe alphanumeric ref like "hipocampus_qmd_readonly".
- workspace_root (str | None, alias workspaceRoot) — Workspace root path for local adapters.
- prompt_projection_enabled (Literal[False], alias promptProjectionEnabled) — Structurally false. Cannot enable prompt projection.
- live_provider_calls_enabled (Literal[False], alias liveProviderCallsEnabled) — Structurally false. Cannot enable live provider calls.
- adk_memory_service_attachment_enabled (Literal[False], alias adkMemoryServiceAttachmentEnabled) — Structurally false. Cannot attach ADK memory service.

- [Memory concepts](/docs/memory)

## PythonToolHostAttachmentConfig

Controls whether the Python ToolHost subsystem is attached. Two fields are structurally locked to Literal[False] to prevent production tool mutation.

- enabled (bool, default False) — Whether the ToolHost is active.
- mode (Literal["disabled", "shadow_readonly"], default "disabled") — ToolHost operating mode.
- production_attachment_enabled (Literal[False], alias productionAttachmentEnabled) — Structurally false. Cannot attach to production.
- live_tool_mutation_enabled (Literal[False], alias liveToolMutationEnabled) — Structurally false. Cannot enable live tool mutation.

- [ToolHost API](/docs/toolhost-api)

## PythonRuntimeAuthorityConfig

Top-level runtime authority flags. Eight fields are structurally locked to Literal[False] using the _FalseOnlyModel pattern, meaning the runtime cannot escalate to write authority regardless of configuration input. Two additional fields (user_visible_output_allowed, canary_routing_allowed) are boolean but force-reset to False on model_construct and model_copy. These flags govern the hosted enforcement/governance boundary — whether that layer is attached to live production writes and routing — not whether the local CLI can run a model or first-party tools (it can; see [What works today](/docs/what-works-today)).

- user_visible_output_allowed (bool, alias userVisibleOutputAllowed, default False) — Whether user-visible output is allowed. Force-reset on construct/copy.
- canary_routing_allowed (bool, alias canaryRoutingAllowed, default False) — Whether canary routing is allowed. Force-reset on construct/copy.
- transcript_write_allowed (Literal[False], alias transcriptWriteAllowed) — Structurally false.
- sse_write_allowed (Literal[False], alias sseWriteAllowed) — Structurally false.
- channel_write_allowed (Literal[False], alias channelWriteAllowed) — Structurally false.
- db_write_allowed (Literal[False], alias dbWriteAllowed) — Structurally false.
- workspace_mutation_allowed (Literal[False], alias workspaceMutationAllowed) — Structurally false.
- child_execution_allowed (Literal[False], alias childExecutionAllowed) — Structurally false.
- mission_runtime_allowed (Literal[False], alias missionRuntimeAllowed) — Structurally false.
- evidence_block_mode_allowed (Literal[False], alias evidenceBlockModeAllowed) — Structurally false.

- [Default-off gates](/docs/default-off-gates)

## EvidenceEnforcementConfig

Controls the evidence enforcement boundary. Two fields are structurally locked to Literal[False] to prevent live evidence blocking.

- enabled (bool, default False) — Whether evidence enforcement is active.
- local_fake_evaluation_enabled (bool, alias localFakeEvaluationEnabled, default False) — Whether local fake evaluation is enabled for testing.
- evidence_block_enabled (Literal[False], alias evidenceBlockEnabled) — Structurally false. Cannot enable evidence blocking.
- final_answer_blocking_enabled (Literal[False], alias finalAnswerBlockingEnabled) — Structurally false. Cannot enable final answer blocking.
- route_attached (Literal[False], alias routeAttached) — Structurally false. Cannot attach routing.

- [Evidence concepts](/docs/evidence)
- [Evidence contracts](/docs/evidence-contracts)
