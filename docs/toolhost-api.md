# ToolHost API Reference

Reference for ToolManifest full schema (tools/manifest.py), ToolRegistry (tools/registry.py), tool execution flow, ToolEvidenceRecord, and dispatch status (currently BLOCKED).

ToolManifest full schema with permission, budget, dangerous, mutates_workspace, parallel_safety (unsafe/readonly/concurrency_safe), side_effect_class, emits_evidence_types, and tags. ToolRegistry manages ToolRegistration records. Dispatcher is BLOCKED (toolDispatchAllowed=False).

## Tool Execution Flow

The ToolHost governs every tool call through a hook-bounded execution pipeline. This is the current implementation flow, not a planned API.

- 1. Model proposes a tool call with tool_name and arguments.
- 2. beforeToolUse hook point fires. Blocking hooks (dangerous-patterns, path-escape, arity-permission) can deny the call.
- 3. If not denied, the tool executes against the workspace or external service.
- 4. afterToolUse hook point fires. Evidence-recording hooks (source-authority, deterministic-evidence, resource-existence) observe the result.
- 5. A ToolEvidenceRecord is created and added to the evidence ledger.
- 6. The tool result is returned to the model for the next proposal.

## ToolEvidenceRecord

ToolEvidenceRecord captures the full trace of a single tool execution. It is a frozen Pydantic model created by the tool boundary layer after each tool call.

- kind (ToolEvidenceKind) — One of "tool_call", "tool_result", "tool_error", "tool_timeout".
- tool_call_id (str, alias toolCallId) — Unique identifier for this tool call.
- tool_id (str, alias toolId) — Tool identifier (may differ from tool_name for aliased tools).
- tool_name (str, alias toolName) — Tool name as proposed by the model.
- observed_at (int | float, alias observedAt) — Timestamp of the observation.
- terminal (bool) — Whether this is the final record for this tool call.
- executed (bool) — Whether the tool was actually executed (false for denied calls).
- status (ToolEvidenceStatus) — One of "ok", "error", "denied", "not_found", "not_exposed".
- arg_summary (Mapping[str, object], alias argSummary) — Sanitized summary of tool arguments. Private paths and secrets are redacted.
- result_summary (Mapping[str, object], alias resultSummary) — Sanitized summary of tool results.
- args_hash (str | None, alias argsHash) — SHA-256 hash of the raw arguments.
- result_hash (str | None, alias resultHash) — SHA-256 hash of the raw result.
- error_code (str | None, alias errorCode) — Error code if the tool failed.
- error_message (str | None, alias errorMessage) — Error message if the tool failed. Sanitized to remove secrets.
- duration_ms (int | None, alias durationMs) — Execution duration in milliseconds.

## Tool Denial Statuses

When a tool call is denied by policy, the ToolEvidenceRecord records the denial reason as a PolicyFailureReason. The tool is not executed, and executed is set to false.

- denied — Tool call was denied by a blocking hook (e.g. dangerous-patterns, arity-permission). Error code: tool_denied.
- not_found — Tool was not found in the tool registry. Error code: tool_not_found.
- not_exposed — Tool exists but is not exposed to the current agent context. Error code: tool_not_exposed.
- missing_handler — Tool is registered but has no handler implementation. Error code: tool_missing_handler. Status maps to not_found.

## Policy Enforcement

ToolHost policy enforcement is driven by BuiltinHarnessPreset verifier gates and HookManifest registrations at the beforeToolUse and afterToolUse hook points. The enforcement boundary evaluates evidence contracts and produces EvidenceEnforcementDecision outcomes.

The ToolHostRequest and ToolHostReceipt types referenced in the architecture docs are not yet implemented as formal Pydantic models. The current implementation uses ToolEvidenceRecord as the receipt equivalent and HookContext as the request context.

- [Harness schema](/docs/harness-schema)
- [Evidence contracts](/docs/evidence-contracts)
- [Hook points reference](/docs/hook-points-reference)
