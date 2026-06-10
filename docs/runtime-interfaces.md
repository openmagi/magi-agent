# Runtime Interfaces

Python interfaces exposed by the Magi Agent runtime for extension.

The runtime exposes typed interfaces for turn control, evidence, boundaries, hooks, tools, recipes, and policy. This page catalogs every public interface with its implementation status and actual module path.

## Turn and runtime interfaces (Python)

The runtime core defines types for turn input, session management, and hook dispatch. These are the entry points for the governed turn loop.

- TurnInput (cli/contracts.py): prompt, session_id, turn_id, initial_messages, harness_state, image_blocks. The input model for each agent turn.
- HookBusRunResult (hooks/bus.py): final_action (continue/block/pending_control_request), observation (effective_hooks, skipped_by_scope, failed_open, failed_closed, blocked_by), permission_boundary. The result of running hooks at a lifecycle point.
- EvidenceLedgerEntry (evidence/ledger.py): kind, sequence, evidence_ref, session_id, turn_id, run_on, agent_role, spawn_depth, source_kind, producer_surface, payload (sanitized), metadata, traffic_attached/execution_attached/route_attached (all False). The primary ledger record.
- ToolRegistration (tools/registry.py): manifest (ToolManifest), enabled, handler. The registry entry for a tool.
- RecipeSnapshot (recipes/compiler.py): snapshot_id (sha256 of pack_ids[:16]), resolved_profile, selected_pack_ids, composition_policy_metadata, attachment_flags (all false). The compiled recipe output.

## Evidence interfaces (Python)

The evidence system defines the core data types for recording, contracting, and evaluating evidence. All types are Pydantic BaseModel subclasses with frozen=True and strict validation.

- EvidenceRecord (evidence/types.py): type, status, observed_at, source (EvidenceSource), fields, preview, metadata. Implemented and active.
- EvidenceSource (evidence/types.py): kind (8 values: tool_trace, adk_event, transcript, artifact, execution_contract, verifier, custom_extractor, external_ack), tool_name, tool_call_id, event_id, transcript_entry_id, artifact_id, contract_id, verifier_name, extractor_id, acknowledgement_id, channel, metadata. Implemented and active.
- EvidenceContract (evidence/types.py): id, description, triggers, when, requirements, on_missing (audit|block_final_answer), retry_message, scope. Implemented and active.
- EvidenceContractVerdict (evidence/types.py): contract_id, ok, state (audit|pass|missing|failed|block_ready), enforcement, missing_requirements, matched_evidence, failures, retry_message, requirement_coverage. Implemented and active.
- EvidenceRequirement (evidence/types.py): type, after, command_pattern, exit_code, fields (Mapping of name to EvidenceFieldMatcher). Implemented and active.
- EvidenceFieldMatcher: equals, one_of, matches, exists. At least one matcher required. Implemented and active.
- EvidenceContractFailure: code, contract_id, requirement_type, message, metadata. Implemented and active.
- EvidenceContractScopeMetadata: agent_roles, run_on, spawn_depth, enforcement, audit_before_block, opt_out_allowed, hard_safety. Implemented and active.

## Tool evidence interface (Python)

The tool evidence boundary produces ToolEvidenceRecord instances that capture every tool interaction with sanitized summaries and content hashes.

- ToolEvidenceRecord: kind, tool_call_id, tool_id, tool_name, observed_at, terminal, executed, status, arg_summary, result_summary, args_hash, result_hash, error_code, error_message, duration_ms. Implemented and active.
- ToolEvidenceBoundary: record_pair() method produces a (tool_call, tool_result) tuple. Implemented and active.

## Boundary interfaces (Python)

Each boundary module exposes Intent, Receipt/Decision, and AuthorityFlags types. All are implemented with default-off behavior.

- MemoryMutationIntent / MemoryMutationReceipt: memory write boundary. Implemented, default-off.
- ChildTaskRequest / ChildRunnerResult: child runner boundary. Implemented, default-off.
- ArtifactRecord / ArtifactChannelDeliveryRequest / ArtifactChannelDeliveryDecision: artifact delivery boundary. Implemented, default-off.
- CommitIntent / CommitBoundaryPlan: commit boundary. Implemented, descriptive only.
- EvidenceEnforcementRequest / EvidenceEnforcementDecision: enforcement boundary. Implemented, default-off.

## Hook manifest interface (Python)

HookManifest defines how hooks are registered and configured. It is a Pydantic model with 18 fields controlling hook behavior.

- name: unique hook identifier.
- point: HookPoint enum value (15 lifecycle points).
- description: human-readable purpose.
- source: ToolSource indicating where the hook code lives.
- priority: execution order (default 100, lower runs first).
- blocking: whether the hook can block the operation (default True).
- fail_open: whether hook failure allows the operation to continue (default False).
- timeout_ms: maximum execution time in milliseconds (default 5000).
- enabled: whether the hook is active (default True).
- security_critical: marks hooks that enforce security invariants (default False).
- if_condition: optional condition expression for conditional execution.
- scope: HookScope for scoping to specific contexts.
- opt_out: whether the hook can be opted out of (default True).
- execution_type: how the hook is executed (inline, subprocess, or http).
- command: shell command for subprocess-type hooks.
- url: endpoint URL for http-type hooks.
- http_headers: additional HTTP headers for http-type hooks.
- http_method: HTTP method for http-type hooks (default POST).

## Runtime policy and harness interfaces

Runtime policy and harness state are represented in Python models across runtime, harness, evidence, recipes, and hooks modules. They capture the policy configuration for a run including approval, verification, delivery, retry, response mode, citations, and harness rules.

- Policy snapshot: approval, verification, delivery, retry, response mode, citations, and harness directives for the current turn.
- Runtime status: executable directives, user directives, harness directives, advisory directives, and warnings.
- Harness rules: id, source text, enabled flag, trigger, condition, action, enforcement, timeout, and priority.

## Conceptual interfaces (not implemented)

The following interface names appear in design documents or are referenced conceptually but do not have implemented types in the codebase. They are listed here for completeness and to prevent confusion.

- ToolHostRequest / ToolHostReceipt: conceptual types for a dedicated ToolHost boundary. Tool execution currently goes through the hook system (beforeToolUse/afterToolUse) and produces ToolEvidenceRecord, not a separate ToolHostReceipt.
- RepairDecision (as a cross-boundary orchestrator): the existing RepairDecision in harness/repair_policy.py handles single-plan repair steps, but there is no cross-boundary repair orchestration type.
- ProjectionResult: no projection write result type exists. The earlier default-off projection write boundary module (and its ProjectionWriteBoundaryResult type) was removed from the codebase.
- ValidationResult: validation is expressed through EvidenceContractVerdict and EvidenceEnforcementDecision, not a separate ValidationResult type.
