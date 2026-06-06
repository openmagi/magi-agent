# Runtime Events Reference

Reference for runtime event kinds, their payloads, and how events relate to SSE streaming, boundary decisions, and evidence recording.

Every runtime event kind (status, token, tool, control, artifact, error), how events carry turn_id, and how they connect to SSE streaming, boundary checks, and the evidence ledger.

## Event Kinds

The runtime emits structured events throughout a governed run. Each event has a kind, a payload, and a turn_id linking it to the current turn.

- status — Runtime status changes: run started, turn started, turn completed, run completed, run aborted.
- token — Token-level streaming events from the LLM. Carries delta text and token metadata.
- tool — Tool lifecycle events: tool_call (model proposes), tool_result (execution complete), tool_error, tool_timeout.
- control — Control plane events: boundary check results, hook execution results, policy decisions, compaction events.
- artifact — Artifact creation and delivery events: file created, artifact published, delivery acknowledged.
- error — Error events: model errors, tool errors, hook errors, boundary violations.

## Events and SSE Streaming

Runtime events are the source of truth for SSE streaming to clients. The transport layer projects events into SSE frames. Token events become streaming text deltas. Tool events become tool-use status updates. Status events become run lifecycle notifications. Control events are typically not projected to the client SSE stream but are recorded in the audit ledger.

The event projection layer filters which events reach the client based on output mode (diagnostic_only, health_only, off, user_visible_canary) and authority flags. Events that would leak private runtime state (hook decisions, evidence verdicts, boundary checks) are projected as safe summaries or omitted entirely.

## Events and Boundary Decisions

Boundary checks produce control events that record the decision (allow, deny, audit) and the policy that triggered it. These events feed into the evidence ledger as execution_contract source evidence. When a boundary check denies a tool call, both a control event (with the denial reason) and a tool event (with status denied) are emitted.

Hook execution results are also emitted as control events, recording the hook name, point, execution time, and whether the hook blocked or allowed the lifecycle transition.

## Events and Evidence Recording

Evidence records are created from runtime events by the evidence extraction layer. Tool events produce ToolEvidenceRecord entries. Verifier hook results produce EvidenceRecord entries with the verifier source kind. The evidence ledger is append-only within a run and is consulted by evidence contracts at their trigger points (afterToolUse, beforeCommit) to determine whether requirements are satisfied.

- [Evidence types reference](/docs/evidence-types-reference)
- [ToolHost API](/docs/toolhost-api)
- [Boundaries overview](/docs/boundaries)
