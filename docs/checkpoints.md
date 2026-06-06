# Checkpoints

Durable checkpoints for resuming, inspecting, and auditing governed agent runs.

Checkpoints capture runtime state so work can be resumed, repaired, or audited after interruption.

## The evidence ledger as checkpoint mechanism

Every significant action the agent takes is recorded in an evidence ledger — a running log of tool calls, source inspections, test results, and verification steps. This ledger acts as a checkpoint: you can review what the agent did, why it did it, and whether each step was supported by evidence.

Implementation: the evidence ledger (evidence/ledger.py) appends an EvidenceLedgerEntry for every tool call, boundary decision, and contract evaluation. Each entry carries a sequence number, evidence_ref, session_id, turn_id, run_on (main/child), agent_role, spawn_depth, source_kind, and producer_surface. The ledger is append-only and survives compaction. Payloads are automatically sanitized (Bearer tokens and API keys are redacted).

## Evidence accumulation across turns

EvidenceRecord instances accumulate across turns within a session. Each record carries a type (one of the BUILTIN_EVIDENCE_TYPES like GitDiff, TestRun, CodeDiagnostics, CommitCheckpoint, FileDeliver, or a custom: prefixed type), a status (ok, failed, or unknown), an observed_at timestamp, and a source describing where the evidence came from.

Evidence contracts reference this accumulated evidence at evaluation time. A contract's requirements can match evidence from any turn in the session, not just the current turn. See [Evidence](/docs/evidence) for the full EvidenceRecord structure.

## Evidence contract verdicts as checkpoint gates

Evidence contracts (evidence/contracts.py) act as checkpoint gates. The EvidenceContractEngine evaluates a contract against the accumulated evidence and produces an EvidenceContractVerdict with a state of pass, missing, failed, audit, or block_ready. When a contract's on_missing field is set to block_final_answer, the verdict can block the agent from producing a final response until the required evidence is present.

The CommitCheckpoint builtin evidence type is specifically designed as a checkpoint marker. It records that a commit boundary was reached, providing a durable checkpoint in the ledger that downstream contracts can reference.

## The onTaskCheckpoint hook point

The HookPoint.ON_TASK_CHECKPOINT (onTaskCheckpoint) hook fires when the runtime reaches a task checkpoint. Hooks registered at this point can record audit events, persist state, or trigger external notifications. This is distinct from the commit boundary hooks (beforeCommit/afterCommit) which fire around the output commit operation.

The checkpoint hook receives the current evidence ledger state, allowing hooks to inspect what evidence has been collected and what contract verdicts are pending.

## Checkpoint limitations

The evidence ledger is implemented and active as the checkpoint mechanism. Explicit rollback-to-checkpoint (rewinding runtime state to a previous checkpoint and replaying from there) is planned but not yet implemented. Currently, checkpoints are write-forward: the ledger records what happened, and contracts gate what can happen next, but there is no mechanism to undo or replay from a prior checkpoint.
