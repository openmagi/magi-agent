# Boundaries

Runtime boundaries that govern when model proposals become state, output, or side effects.

Boundaries are control points that prevent the agent from taking action without verification. Seven boundary modules follow the Intent-to-Receipt pattern where every side effect is validated before execution.

## The Intent-to-Receipt pattern

Every boundary module follows the same structure: a typed Intent describes what the model or runtime wants to do, the boundary evaluates the intent against its configuration and authority flags, and a typed Receipt or Decision records what actually happened. This separates description from execution.

All boundary modules include an AuthorityFlags model (e.g. MemoryWriteAuthorityFlags, ChildRunnerAuthorityFlags) where every field is typed as Literal[False] and defaults to False. The model_construct and model_copy methods are overridden to always return all-False instances. This makes it structurally impossible for code inside the boundary to grant itself production authority.

- Intent types describe the proposed action with all relevant metadata.
- Receipt/Decision types record the outcome: status, reason codes, diagnostic metadata, and authority flags.
- Authority flags are always Literal[False] with enforced serialization to False.
- Boundaries default to disabled. Enabling requires explicit configuration, and even enabled boundaries only run local fake evaluation unless production authority is externally attached.

## Tool evidence boundary

The tool evidence boundary (evidence/tool_boundary.py) records a ToolEvidenceRecord for every tool interaction. It does not gate tool execution; it creates the evidence trail that other boundaries and contracts consume. Status: implemented and active.

- ToolEvidenceKind: tool_call, tool_result, tool_error, tool_timeout.
- ToolEvidenceStatus: ok, error, denied, not_found, not_exposed.
- Fields include tool_call_id, tool_id, tool_name, observed_at, terminal, executed, arg_summary, result_summary, args_hash, result_hash, error_code, error_message, duration_ms.
- Argument and result summaries are sanitized: secrets, private paths, patch bodies, and raw output are redacted. Hashes use sha256 over JSON-serialized content.
- PolicyFailureReason (denied, not_found, not_exposed, missing_handler) maps to ToolEvidenceStatus and error codes for denied tool calls.
- ToolEvidenceBoundary.record_pair() produces a (tool_call, tool_result) tuple for a single tool invocation.

### ToolEvidenceRecord fields

```
class ToolEvidenceRecord(BaseModel):
    kind: ToolEvidenceKind
    tool_call_id: str
    tool_id: str
    tool_name: str
    observed_at: int | float
    terminal: bool
    executed: bool
    status: ToolEvidenceStatus
    arg_summary: Mapping[str, object]
    result_summary: Mapping[str, object]
    args_hash: str | None
    result_hash: str | None
    error_code: str | None
    error_message: str | None
    duration_ms: int | None
```

## Evidence enforcement boundary

The evidence enforcement boundary (evidence/enforcement_boundary.py) evaluates an EvidenceContract against collected evidence records and produces an EvidenceEnforcementDecision. It determines whether evidence is sufficient to proceed, requires repair, or should block. Status: implemented, evaluation is default-off (config.enabled=False).

- EvidenceEnforcementStatus: disabled, evaluation_intent, pass, audit_missing, repair_required, escalate_required, block_ready_local_fake.
- EvidenceEnforcementAction: audit, pass, repair, escalate, block_intent.
- EvidenceEnforcementDomain: research, coding, completion, general.
- EvidenceEnforcementConfig gates evaluation: enabled, local_fake_evaluation_enabled, evidence_block_enabled (Literal[False]), final_answer_blocking_enabled (Literal[False]).
- EvidenceEnforcementAuthorityFlags: all fields Literal[False] including evidence_block_enabled, final_answer_blocked, live_tool_dispatched, shell_git_or_test_executed, production_writes_enabled, route_attached.
- When the verdict fails with block_ready state: if repair_allowed, status is repair_required; if escalation_allowed, status is escalate_required; otherwise status is block_ready_local_fake with action block_intent.

## Memory write boundary

The memory write boundary (memory/write_boundary.py) governs all memory mutations. A MemoryMutationIntent describes the proposed operation and produces a MemoryMutationReceipt. All memory writes are disabled by default. Status: implemented, default-off.

- MemoryMutationOperation: remember, write, redact, delete, compact, decay, export.
- MemoryMutationStatus: blocked, approval_required, unsupported, success.
- MemoryMutationIntent includes provider_id, turn_id, operation, target_sha256, target_text, path_refs, content, matched_count, target_still_present, failure_kind, child_memory_isolated.
- MemoryMutationReceipt includes receipt_id, status, executed, memory_write_allowed, production_write_enabled, provider_call_attempted, filesystem_mutation_attempted, production_receipt, local_test_only, target (MemoryMutationTarget), authority_flags.
- MemoryWriteAuthorityFlags: all Literal[False] including memory_write_allowed, memory_redact_allowed, memory_delete_allowed, provider_call_allowed, filesystem_write_allowed, database_write_allowed, network_call_allowed, production_write_enabled.
- Local test-only receipts use HMAC signature verification (local_test_receipt_marker + local_test_receipt_signature) to distinguish test receipts from production claims.

## Projection write boundary

The projection write boundary (runtime/projection_write_boundary.py) governs writes to output channels: transcript, SSE, control events, and control requests. All projection writes are disabled by default and produce a ProjectionWriteBoundaryResult with allowed=False. Status: implemented, default-off.

- ProjectionWriteTarget: transcript, sse, control_event, control_request.
- ProjectionWriteIntent includes target, operation, session_key, idempotency_key, payload.
- ProjectionWriteBoundaryResult: allowed is Literal[False], includes denial (ProjectionWriteDenial with reason_code projection_writes_disabled) and authority_flags.
- ProjectionWriteAuthorityFlags: all Literal[False] including transcript_write_allowed, sse_write_allowed, control_event_write_allowed, control_request_write_allowed, durable_write_allowed, production_receipt_allowed, storage_backend_attached, filesystem_write_allowed, database_write_allowed, transport_write_allowed.
- evaluate_projection_write_intent() always returns allowed=False with a denial explaining that storage backend and receipt policy are not attached.

## Child runner boundary

The child runner boundary (runtime/child_runner_boundary.py) governs spawning child agent tasks. A ChildTaskRequest describes the task and produces a ChildRunnerResult. Production child execution is disabled by default. Status: implemented, default-off.

- ChildRole: coding, research, reviewer, implementer, debugging, general.
- ChildDeliveryMode: return, background.
- ChildRunnerStatus: disabled, blocked, ok, error.
- ChildTaskRequest includes parent_execution_id, turn_id, task_id, objective, role, delivery, budget_tokens, budget_ms, metadata.
- ChildRunnerResult includes status, task_id, prompt_ref, envelope (ChildRunnerEnvelopeRef with child_ref, evidence_refs, artifact_refs, audit_event_refs), error_code, error_message, diagnostic_metadata, authority_flags.
- ChildRunnerAuthorityFlags: all Literal[False] including child_runner_attached, real_child_runner_executed, raw_transcript_injected, raw_tool_logs_injected, hidden_reasoning_injected, parent_context_raw_injection, workspace_mutated, memory_provider_called, route_attached, production_authority.
- ChildRunnerConfig.production_child_execution_enabled and production_writes_enabled are typed as Literal[False].

## Artifact delivery boundary

The artifact delivery boundary (artifacts/delivery_boundary.py) governs artifact creation and channel delivery. An ArtifactChannelDeliveryRequest describes the operation and produces an ArtifactChannelDeliveryDecision. Production delivery is disabled by default. Status: implemented, default-off.

- ArtifactKind: document, spreadsheet, file, rendered_preview, delivery_receipt, child_handoff.
- ArtifactOperation: artifact.create, artifact.read, artifact.list, artifact.update, artifact.delete, artifact.import_child, file.deliver, file.send.
- ArtifactBoundaryStatus: disabled, artifact_intent, artifact_recorded_local_fake, delivery_intent, delivery_recorded_local_fake, channel_absent, unsupported_channel, blocked.
- ArtifactRecord includes artifact_id, kind, title, filename, mime_type, content_digest, artifact_ref, source_refs, provenance_refs.
- ArtifactChannelAuthorityFlags: all Literal[False] including adk_artifact_service_attached, artifact_written, channel_delivery_performed, production_storage_written, production_channel_write, route_attached.
- ArtifactChannelDeliveryConfig.production_storage_writes_enabled and production_channel_delivery_enabled are typed as Literal[False].

## Commit boundary

The commit boundary (runtime/commit_boundary.py) plans the sequence of intents that would execute when a turn commits, blocks, or aborts. CommitIntent and CommitBoundaryPlan are descriptive only: executed=False, enabled=False, defaultOff=True are enforced at construction time. Status: implemented, descriptive only (execution disabled by default).

- CommitPlanStatus: committed, blocked, aborted.
- IntentTarget: transcript, sse, control, hook, local_runtime.
- CommitIntent includes target, operation, payload, plus enforced fields executed (Literal[False]), enabled (Literal[False]), defaultOff (Literal[True]).
- CommitBoundaryPlan includes status, intents tuple, finalText, reason, retryable, retryKind, stopReason, reasonCode, requiredAction, plus the same enforced disabled fields.
- build_commit_plan() creates a committed plan with intents for beforeCommit hook, assistant_text transcript, turn_committed transcript, stop_reason control, turn_end SSE, legacy_finish SSE, afterCommit hook, afterTurnEnd hook, and onTaskCheckpoint hook.
- build_before_commit_block_plan() creates a blocked plan when a verifier rejects the turn, with retryable flag and reason codes extracted from the block reason.
- build_abort_plan() creates an aborted plan with reject_pending_asks, turn_aborted, and onAbort intents.
