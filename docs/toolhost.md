# ToolHost

Tool catalog, ToolManifest schema, ToolRegistry, and governed tool dispatch.

The tool system is built on ToolManifest (tools/manifest.py) with comprehensive metadata (permission, budget, dangerous, parallel_safety, side_effect_class), ToolRegistry (tools/registry.py) for registration, dispatcher.py for execution, and the ADK tool adapter for model-facing attachment. Local first-party tools run through this governed path once a provider key is configured. Tool policy is enforced via permission mode, hooks, evidence receipts, and harness rules.

## Tool catalog and ToolManifest

The tool catalog (tools/catalog.py) defines core tools such as FileRead, FileWrite, FileEdit, PatchApply, Glob, Grep, Bash (dangerous, requires approval), TestRun (dangerous, timeout 300s), GitDiff, TodoWrite, AskUserQuestion, EnterPlanMode, and ExitPlanMode. Each tool is declared via a ToolManifest (tools/manifest.py) with comprehensive metadata.

ToolRegistry (tools/registry.py) manages ToolRegistration records with register/enable/disable/list_available operations. The dispatcher (tools/dispatcher.py) performs permission and budget checking, then either executes the registered handler or returns a governed denial. Tool policy enforcement happens through the hook system: beforeToolUse fires before each call and can deny it, afterToolUse fires after execution and records evidence.

The full ToolManifest has 30 fields. Key fields shown above; see tools/manifest.py for the complete schema including output_schema, cost_class (free | metered | premium), latency_class (inline | background), adk_tool_type, preconditions, postconditions, transient_failure_classes, capability_tags, and the nested Budget model (max_calls_per_turn, max_parallel, output_chars, transcript_chars).

- ToolManifest fields: name, description, kind (core/native/custom/external/skill-compat), permission (read/write/execute/net/meta), input_schema, timeout_ms.
- Budget fields: max_calls_per_turn, max_parallel, output_chars, transcript_chars.
- Safety fields: dangerous (bool), mutates_workspace (bool), parallel_safety (unsafe/readonly/concurrency_safe).
- Mode fields: available_in_modes (plan/act), emits_evidence_types, side_effect_class (none/local_process/local_workspace/external/local_and_external).
- Metadata: tags, should_defer.
- Evidence promotion: ToolEvidenceRecord data feeds into EvidenceRecord with source kind tool_trace.

## Tool denial via policy

Tools can be denied at the beforeToolUse hook point based on policy. The tool evidence boundary records the denial with a specific PolicyFailureReason and corresponding ToolEvidenceStatus.

- denied: tool is known but the current policy does not allow it.
- not_found: tool name does not match any registered tool.
- not_exposed: tool exists but is not exposed to the current agent or context.
- missing_handler: tool is registered but has no execution handler (mapped to not_found status).

### Denied tool evidence

```
# build_denied_tool_error_evidence() produces:
{
  "kind": "tool_error",
  "toolCallId": "call_abc123",
  "toolName": "DangerousTool",
  "terminal": true,
  "executed": false,
  "status": "denied",
  "errorCode": "tool_denied",
  "errorMessage": "[redacted]"
}
```

## HarnessRule actions for tools

Evidence contracts and harness policies define typed actions that the runtime evaluates at beforeCommit or afterToolUse trigger points. Several action types directly govern tool behavior.

- require_tool: the turn must include a call to the named tool before committing. Fields: type, toolName.
- require_tool_input_match: a specific tool must be called with input matching a pattern. Fields: type, toolName, inputPath, pattern.
- llm_verifier: an LLM evaluates the output against a prompt. Fields: type, prompt.
- block: unconditionally block the turn with a reason. Fields: type, reason.
- builtin_preset: activate a named builtin preset. Fields: type, preset (BuiltinPresetId), config (optional BuiltinPresetConfig with enabled and mode).

## Builtin presets

The runtime includes 5 builtin preset identifiers that can be activated via HarnessRule actions. Each preset configures specific hook points and verifier gates.

- fact-grounding: activates at afterLLMCall with grounding-required verifier gate. Ensures claims are backed by inspected sources.
- answer-quality: activates at afterLLMCall with answer-quality verifier gate. Checks overall response quality.
- self-claim: activates at afterLLMCall with self-claim verifier gate. Detects when the model makes claims about its own capabilities.
- response-language: activates at afterLLMCall with response-language-policy config gate. Enforces response language policy.
- deterministic-evidence: activates at afterToolUse and afterLLMCall with deterministic-evidence verifier gate. Requires deterministic (non-LLM) evidence for claims.

## ToolHostRequest and ToolHostReceipt (conceptual)

The conceptual ToolHostRequest and ToolHostReceipt types do not exist in the codebase. Tool execution uses the ADK tool execution path with hook-based policy enforcement (beforeToolUse / afterToolUse) and ToolEvidenceRecord as the output. A dedicated ToolHost abstraction that wraps tool execution with its own request/receipt types is a potential future design but is not implemented.

Where real tool execution lives: the concrete `*ToolHost` classes each carry their own request/receipt shape that converges on ToolEvidenceRecord rather than a unified Request/Receipt type. For the actual contract, see:

- [ToolHost API](/docs/toolhost-api) — concrete dispatch, policy enforcement, and the ToolEvidenceRecord receipt equivalent (HookContext acts as the request context).
- [Evidence](/docs/evidence) — how ToolEvidenceRecord is produced and promoted into the evidence pipeline.
- [Hook points](/docs/hook-points) — the beforeToolUse / afterToolUse points where tool policy is enforced.
