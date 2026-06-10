# Projection

How runtime state is projected into model-visible context and user-visible output.

Projection controls what the model sees as context and what users receive as output. Durable writes to output channels (transcript, SSE, control events) are performed directly by the surfaces that own them; receipts that reach the model first pass through the sanitizing projections described below.

## Removed: projection write boundary

Earlier revisions shipped an experimental `runtime/projection_write_boundary.py` that modeled output writes as gated intents (always `allowed=False`, with no storage backend attached). It was removed together with the unwired runner-session stack because no production path consumed it. Output writes now happen at the points that own them: durable session logs in `cli/session_log.py`, SSE frames in the transport stream routes, and control events inside the engine.

## Relationship to the commit boundary

The commit boundary (commit_boundary.py) plans projection intents as CommitIntent records with targets matching the historical projection write targets (transcript, sse, control, hook). Each CommitIntent is descriptive only and is not executed.

The commit boundary also plans hook intents (beforeCommit, afterCommit, afterTurnEnd, onTaskCheckpoint, onAbort) that observe the turn lifecycle but do not write to output channels directly.

## Memory projection via write boundary

Memory writes follow a separate projection path through the memory write boundary (write_boundary.py). Memory mutation receipts include a public_projection() method that produces a sanitized view safe for model-visible context. The projection strips production execution claims, forces authority flags to False, and sanitizes receipt IDs, provider IDs, turn IDs, and error codes.

Similarly, ChildRunnerResult, ArtifactChannelDeliveryDecision, and EvidenceEnforcementDecision all provide public_projection() methods that strip private metadata before the result is returned to the model.
