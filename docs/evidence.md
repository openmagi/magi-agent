# Evidence

Evidence ledger entries, source receipts, claim graphs, and evidence contracts that back runtime decisions.

Evidence is the runtime record that links model claims to inspected sources, tool receipts, and approval records. The evidence system has two key types: EvidenceLedgerEntry (the append-only ledger record with sequence numbers and secret redaction) and EvidenceRecord (the contract-matchable evidence unit). Both are implemented and active.

## EvidenceLedgerEntry

EvidenceLedgerEntry (evidence/ledger.py) is the primary type in the append-only evidence ledger. Each entry carries a sequence number, evidence_ref, session_id, turn_id, and metadata about where and how the evidence was produced. The ledger automatically redacts Bearer tokens and API keys from payloads.

- kind: one of evidence_record, verifier_verdict, transcript_ref, artifact_ref, control_ref, source_summary.
- sequence: monotonically increasing integer for ledger ordering.
- evidence_ref: reference to the underlying evidence data.
- session_id, turn_id: context identifiers linking the entry to a specific turn.
- run_on: 'main' or 'child' indicating which agent produced this evidence.
- agent_role: 'general', 'coding', or 'research'.
- spawn_depth: nesting level (0 for main agent, >0 for child agents).
- source_kind, producer_surface: provenance metadata.
- payload: sanitized data (Bearer tokens and API keys automatically redacted).
- traffic_attached, execution_attached, route_attached: all typed as False.

## EvidenceRecord

An EvidenceRecord (evidence/types.py) captures a single piece of evidence observed during a run. It is the contract-matchable unit of the evidence system. Records are created by tool boundaries, verifiers, and custom extractors, then matched against EvidenceContract requirements to produce verdicts.

- type: a string identifying the evidence kind. Must match BUILTIN_EVIDENCE_TYPES or use custom:PascalCaseName format.
- status: EvidenceStatus with values ok, failed, unknown.
- observed_at: timestamp (int or float) when the evidence was observed.
- source: EvidenceSource describing where the evidence came from (8 source kinds: tool_trace, adk_event, transcript, artifact, execution_contract, verifier, custom_extractor, external_ack).
- fields: Mapping[str, object] of typed data specific to the evidence type.
- preview: optional human-readable summary.
- metadata: Mapping[str, object] for additional context.

### EvidenceSource fields

```
class EvidenceSource(BaseModel):
    kind: EvidenceSourceKind  # tool_trace | adk_event | transcript |
         # artifact | execution_contract | verifier | custom_extractor |
         # external_ack
    tool_name: str | None
    tool_call_id: str | None
    event_id: str | None
    transcript_entry_id: str | None
    artifact_id: str | None
    contract_id: str | None
    verifier_name: str | None
    extractor_id: str | None
    acknowledgement_id: str | None
    channel: str | None
    metadata: Mapping[str, object]
```

## Builtin evidence types

The evidence system defines 18 builtin evidence types. Each type name must exactly match one of these strings when used without the custom: prefix. Custom types use the custom:PascalCaseName format.

- GitDiff: evidence from git diff output after code changes.
- TestRun: evidence from test execution, supports commandPattern and exitCode matching.
- CodeDiagnostics: evidence from linter or type checker output.
- CommitCheckpoint: evidence that a git commit was made.
- FileDeliver: evidence that a file was delivered to a channel.
- ArtifactVerify: evidence that an artifact was verified.
- DeterministicEvidenceVerifier: evidence from a deterministic (non-LLM) verifier.
- WebSearch: evidence from web search results.
- KnowledgeSearch: evidence from knowledge base search.
- SourceInspection: evidence from inspecting a source file or document.
- PlanVerifier: evidence from plan verification.
- Calculation: evidence from a calculation or computation.
- DateRange: evidence establishing a date or time range.
- Clock: evidence of the current time.
- TelegramDeliveryAck: evidence that a Telegram message was delivered.
- PromptTransform: evidence that a prompt was transformed or rewritten.
- EditMatch: evidence that an edit matched the expected target before being applied.
- DocumentCoverage: evidence that a generated document covered the required sections.

## EvidenceContract and verdict

An EvidenceContract declares what evidence must be present and what to do when it is missing. The contract engine evaluates contracts against collected evidence records and produces an EvidenceContractVerdict.

- EvidenceContract fields: id, description, triggers (afterToolUse | beforeCommit), when (optional condition mapping), requirements (tuple of EvidenceRequirement), on_missing (audit | block_final_answer), retry_message, scope (EvidenceContractScopeMetadata).
- EvidenceRequirement fields: type, after (last_code_mutation | contract_start), command_pattern, exit_code, fields (Mapping of field name to EvidenceFieldMatcher).
- EvidenceFieldMatcher supports equals, one_of, matches (regex), and exists matchers.
- EvidenceContractVerdict: contract_id, ok (bool), state (audit | pass | missing | failed | block_ready), enforcement, missing_requirements, matched_evidence, failures, retry_message, requirement_coverage.
- EvidenceContractFailure codes: EVIDENCE_CONTRACT_MISSING, EVIDENCE_CONTRACT_STALE, EVIDENCE_CONTRACT_FIELD_MISMATCH, EVIDENCE_CONTRACT_COMMAND_MISMATCH, EVIDENCE_CONTRACT_INVALID_CONFIG.

## Evidence flow

Evidence flows through the system in a pipeline: tool call produces a ToolEvidenceRecord, which is promoted to an EvidenceRecord with source kind tool_trace. Collected EvidenceRecords are matched against EvidenceContract requirements. The contract engine produces an EvidenceContractVerdict.

Live enforcement of that verdict happens in the engine pre-final gate. For coding-domain turns the engine runs the pre-final verifier bus before the final answer; when an evidence contract with on_missing="block_final_answer" is unsatisfied, the gate blocks the turn (Terminal.error, error="pre_final_evidence_gate_blocked") and, with MAGI_CODING_REPAIR_LOOP_ENABLED, drives a repair loop. This coding pre-final gate is the only path that actually blocks output today, and it is on by default for the coding domain.

The research final-projection gate is an audit/diagnostic projection without live blocking authority: its final_answer_blocking_enabled flag is Literal[False], so a verdict in state "block_ready" is recorded as a "block_ready_local_fake" intent rather than blocking the final answer. It does not sit on the live output path. Enforcement converges on the single engine pre-final gate; there is no separate standalone enforcement-boundary module.

The flow is: tool call -> ToolEvidenceRecord -> EvidenceRecord -> contract matching -> EvidenceContractVerdict -> engine pre-final gate (coding domain: live block/repair; research projection: audit-only).

- Step 1: Tool boundary creates ToolEvidenceRecord with kind, status, hashes, and sanitized summaries.
- Step 2: Record is wrapped as EvidenceRecord with type matching the evidence category (e.g. TestRun, GitDiff).
- Step 3: Contract engine iterates requirements, matches records by type, checks after-boundary freshness, validates field matchers.
- Step 4: Verdict summarizes pass/fail with matched evidence and missing requirements.
- Step 5: The engine pre-final gate maps the verdict to an action. For coding-domain turns this is live: a "block_ready" verdict blocks the final answer or triggers repair. For research-domain turns the final-projection gate records the same verdict for diagnostics only (block_ready is recorded as "block_ready_local_fake" and does not block output).
