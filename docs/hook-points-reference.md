# Hook Points Reference

Complete reference for all 15 HookPoint enum values, their camelCase keys, firing conditions, payloads, and blocking behavior.

Every HookPoint enum value with its key, when it fires, blocking behavior, and the ADK callback mapping table.

## HookPoint Enum Values

HookPoint is a Python str Enum with 15 members. Each member has a camelCase value used as the wire key. Hooks registered at a given point fire when the runtime reaches that lifecycle moment.

- BEFORE_TURN_START ("beforeTurnStart") — Fires before a new turn begins. Use for context injection, task contract setup.
- AFTER_TURN_END ("afterTurnEnd") — Fires after a turn completes. Use for task board completion, artifact delivery.
- BEFORE_LLM_CALL ("beforeLLMCall") — Fires before each LLM API call. Use for context injection, coding context, pre-refusal.
- AFTER_LLM_CALL ("afterLLMCall") — Fires after each LLM API call. Use for answer quality, fact grounding, claim citation, output delivery, response language.
- BEFORE_TOOL_USE ("beforeToolUse") — Fires before tool execution. Use for dangerous pattern detection, path safety, permission checks, resource existence.
- AFTER_TOOL_USE ("afterToolUse") — Fires after tool execution. Use for evidence recording, source authority, deterministic evidence.
- BEFORE_COMMIT ("beforeCommit") — Fires before a commit boundary. Use for coding verification, secret exposure, path escape, sealed files.
- AFTER_COMMIT ("afterCommit") — Fires after a commit boundary. Use for coding child review, sealed file verification.
- ON_ABORT ("onAbort") — Fires when a run is aborted. Non-blocking by convention.
- ON_ERROR ("onError") — Fires on runtime errors. Maps from both on_model_error_callback and on_tool_error_callback in ADK.
- ON_TASK_CHECKPOINT ("onTaskCheckpoint") — Fires at task checkpoints. Use for task contract, goal progress, task board completion.
- BEFORE_COMPACTION ("beforeCompaction") — Fires before context compaction. Use for memory continuity ledger.
- AFTER_COMPACTION ("afterCompaction") — Fires after context compaction. Use for memory continuity ledger.
- ON_RULE_VIOLATION ("onRuleViolation") — Fires when a harness rule is violated. Non-blocking by convention.
- ON_ARTIFACT_CREATED ("onArtifactCreated") — Fires when an artifact is created. Use for artifact delivery verification.

## ADK Callback Mapping

The ADK bridge maps Google ADK callback names to HookPoint enum members. This mapping is defined in callback_adapter.py and used by the ADK runner to route lifecycle events to the hook bus.

- before_agent_callback -> BEFORE_TURN_START (beforeTurnStart)
- after_agent_callback -> AFTER_TURN_END (afterTurnEnd)
- before_model_callback -> BEFORE_LLM_CALL (beforeLLMCall)
- after_model_callback -> AFTER_LLM_CALL (afterLLMCall)
- on_model_error_callback -> ON_ERROR (onError)
- before_tool_callback -> BEFORE_TOOL_USE (beforeToolUse)
- after_tool_callback -> AFTER_TOOL_USE (afterToolUse)
- on_tool_error_callback -> ON_ERROR (onError)

## HookManifest Field Reference

HookManifest declares a single hook registration. It specifies which point to fire at, priority, blocking behavior, timeout, scope filtering, and security criticality.

- name (str) — Unique hook name.
- point (HookPoint) — Which lifecycle point this hook fires at.
- description (str) — Human-readable description of the hook.
- source (ToolSource) — Where the hook implementation comes from (builtin, plugin, config).
- priority (int, default 100) — Execution priority. Lower values run first.
- blocking (bool, default True) — Whether this hook can block the lifecycle event.
- fail_open (bool, alias failOpen, default False) — Whether the hook fails open (continues on error) or fails closed (blocks on error).
- timeout_ms (int, alias timeoutMs, default 5000) — Hook execution timeout in milliseconds.
- enabled (bool, default True) — Whether the hook is active.
- security_critical (bool, alias securityCritical, default False) — Security-critical hooks bypass scope filtering.
- if_condition (str | None, alias if, default None) — Optional condition expression for conditional hook execution.
- scope (HookScope) — Scope filter (agent role, run type, spawn depth).
- opt_out (bool, alias optOut, default True) — Whether the hook can be opted out of.
- execution_type (ExecutionType, default handler) — Determines how the hook is invoked: handler (in-process), command (shell), or http (external endpoint).
- command (str | None, default None) — Shell command for command-type hooks.
- url (str | None, default None) — Endpoint URL for http-type hooks.
- http_headers (dict[str, str] | None, default None) — Custom headers for http-type hooks.
- http_method (HttpMethod, default POST) — HTTP method for http-type hooks: GET, POST, PUT, PATCH, or DELETE.

- [Hook points concepts](/docs/hook-points)
- [Harness schema](/docs/harness-schema)
