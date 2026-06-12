# Sessions

How Magi Agent sessions manage state, context, and continuity across turns.

Sessions track turn history, context windows, compaction, and durable state across interactions.

## Session lifecycle

A session is a conversation with the agent. It remembers what you discussed, what files were changed, and what evidence was collected across turns. Sessions persist across compaction — when the context window fills up, older turns are dropped from the model-visible transcript (tail-keep truncation) while key evidence and decisions are preserved separately in the control-plane ledger.

Implementation: session identity is managed by the runtime session identity module, session continuity across turns by the session continuity module, and the ADK bridge session service handles session state persistence (read-only or disabled by default). Each turn enters through the engine driver (MagiEngineDriver in cli/engine.py), which enforces single-flight session concurrency via ActiveTurnRegistry and classifies turn errors.

## Context window and turn management

Each turn within a session adds to the context window. The runtime tracks the accumulated token count, and once a turn crosses the configured token threshold the live compaction plugin trims older turns before the next model call. Turn state includes the model's text output, tool calls with their ToolEvidenceRecord entries, and any evidence contract verdicts evaluated during the turn.

The beforeLLMCall hook point fires before each model invocation, allowing hooks to inject context or block the call. The afterLLMCall hook fires after the model responds, before tool execution begins.

## Compaction and context continuity

There are two compaction stacks in the runtime, and they behave differently:

- **Tail-keep truncation (live).** The compaction that actually runs is a
  tail-keep truncation plugin (`magi_agent/adk_bridge/context_compaction.py`)
  driven by the `ContextLifecycleBoundary` decision engine. When a context
  crosses the token threshold, it trims the model-visible transcript down to the
  most recent tail of events before the next model call — it does **not**
  summarize the dropped turns. This plugin is gated by
  `MAGI_CONTEXT_COMPACTION_ENABLED` (with `MAGI_COMPACTION_TOKEN_THRESHOLD` and
  `MAGI_COMPACTION_TAIL_EVENTS`) and is **default-ON in the local-full and
  hosted-full profiles**. Set `MAGI_CONTEXT_COMPACTION_ENABLED=0` to disable it.
- **LLM-summary compaction (deferred / optional).** A separate, full LLM-based
  summary engine (`magi_agent/context/auto_compact.py`,
  `AutoCompactionEngine`) can summarize older turns instead of dropping them.
  This path is **not wired into the live turn loop** today; it is an optional
  path that is not invoked by default.

The beforeCompaction and afterCompaction hook points (HookPoint.BEFORE_COMPACTION and HookPoint.AFTER_COMPACTION) fire around the compaction operation, allowing hooks to preserve critical context or record what was compacted.

Compaction preserves the evidence ledger separately from the conversation transcript. Evidence records accumulated across turns survive compaction because they are stored in the runtime control plane, not in the model-visible context. This means evidence contracts can reference evidence from earlier turns even after compaction trims the original tool call details out of the transcript.

## Session state and recovery

Session state is persisted to the workspace so that sessions can survive restarts. The ADK bridge session service handles serialization and deserialization of session state including the transcript, evidence ledger, and active harness configuration. (Durable cross-restart persistence backends — such as a Kubernetes PVC — are a hosted-deployment concern, not part of the OSS CLI/serve runtime.)

Session management is implemented and active. A dedicated public session API for external consumers to create, query, or manipulate sessions is planned but not yet exposed as an extension point. Currently, sessions are managed internally by the runtime and accessed through the ADK bridge.
