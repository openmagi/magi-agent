# Projection

How runtime state is projected into model-visible context and user-visible output.

Projection controls what the model sees as context and what users receive as output. The projection write boundary governs all durable writes to output channels, and is disabled by default.

## Projection write targets

The projection system defines four write targets that represent the channels through which runtime state can become visible output. All writes to these targets go through the projection write boundary, which is disabled by default.

- transcript: durable conversation transcript entries (assistant text, tool results, turn markers).
- sse: server-sent event stream to the client (turn_end, runtime_trace, legacy_finish).
- control_event: internal runtime control events (stop_reason, structured_output).
- control_request: internal runtime control requests (reject_pending_asks).

## ProjectionWriteIntent and result

A ProjectionWriteIntent describes a proposed write to an output channel. The boundary evaluates it and returns a ProjectionWriteBoundaryResult. Currently, all projection writes return allowed=False because no storage backend or receipt policy is attached.

- ProjectionWriteIntent fields: target (ProjectionWriteTarget), operation (string describing the write), session_key, idempotency_key, payload.
- ProjectionWriteBoundaryResult fields: allowed (Literal[False]), target, operation, durable_write_attempted (Literal[False]), production_receipt_produced (Literal[False]), authority_flags, denial, receipt.
- ProjectionWriteDenial: reason_code is always projection_writes_disabled.
- ProjectionWriteReceipt: defined but never populated (receipt is always None) because no storage backend is attached.

## Projection authority flags

ProjectionWriteAuthorityFlags follows the same default-off pattern as all boundary modules. All 10 flags are typed as Literal[False] and enforced to False through model validators and serializers.

- transcript_write_allowed, sse_write_allowed, control_event_write_allowed, control_request_write_allowed: per-target write gates.
- durable_write_allowed: whether writes can be persisted.
- production_receipt_allowed: whether production receipts can be issued.
- storage_backend_attached: whether a storage backend is connected.
- filesystem_write_allowed, database_write_allowed, transport_write_allowed: infrastructure-level write gates.

## Relationship to the commit boundary

The commit boundary (commit_boundary.py) plans projection intents as CommitIntent records with targets matching the projection write targets (transcript, sse, control, hook). Each CommitIntent is descriptive only and is not executed. The projection write boundary would govern the actual execution of these intents if enabled.

The commit boundary also plans hook intents (beforeCommit, afterCommit, afterTurnEnd, onTaskCheckpoint, onAbort) that observe the turn lifecycle but do not write to output channels directly.

## Memory projection via write boundary

Memory writes follow a separate projection path through the memory write boundary (write_boundary.py). Memory mutation receipts include a public_projection() method that produces a sanitized view safe for model-visible context. The projection strips production execution claims, forces authority flags to False, and sanitizes receipt IDs, provider IDs, turn IDs, and error codes.

Similarly, ChildRunnerResult, ArtifactChannelDeliveryDecision, and EvidenceEnforcementDecision all provide public_projection() methods that strip private metadata before the result is returned to the model.
