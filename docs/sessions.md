# Sessions

How Magi Agent sessions manage state, context, and continuity across turns.

Sessions track turn history, context windows, compaction, and durable state across interactions.

## Session lifecycle

A session is a conversation with the agent. It remembers what you discussed, what files were changed, and what evidence was collected across turns. Sessions persist across compaction — when the context window fills up, older turns are summarized but key evidence and decisions are preserved.

Implementation: session identity is managed by the runtime session identity module, session continuity across turns by the session continuity module, and the ADK bridge session service handles session state persistence (read-only or disabled by default). Each turn enters through the engine driver (MagiEngineDriver in cli/engine.py), which enforces single-flight session concurrency via ActiveTurnRegistry and classifies turn errors.

## Context window and turn management

Each turn within a session adds to the context window. The runtime tracks the accumulated token count and triggers compaction when the context approaches the model's limit. Turn state includes the model's text output, tool calls with their ToolEvidenceRecord entries, and any evidence contract verdicts evaluated during the turn.

The beforeLLMCall hook point fires before each model invocation, allowing hooks to inject context or block the call. The afterLLMCall hook fires after the model responds, before tool execution begins.

## Compaction and context continuity

When the context window grows too large, the runtime compacts older turns into a summary. The beforeCompaction and afterCompaction hook points (HookPoint.BEFORE_COMPACTION and HookPoint.AFTER_COMPACTION) fire around this operation, allowing hooks to preserve critical context or record what was compacted.

Compaction preserves the evidence ledger separately from the conversation transcript. Evidence records accumulated across turns survive compaction because they are stored in the runtime control plane, not in the model-visible context. This means evidence contracts can reference evidence from earlier turns even after compaction removes the original tool call details from the transcript.

## Session state and recovery

Session state is persisted to the workspace PVC so that sessions can survive pod restarts. The ADK bridge session service handles serialization and deserialization of session state including the transcript, evidence ledger, and active harness configuration.

Session management is implemented and active. A dedicated public session API for external consumers to create, query, or manipulate sessions is planned but not yet exposed as an extension point. Currently, sessions are managed internally by the runtime and accessed through the ADK bridge.
