# Evidence Types Reference

Complete reference for all 15 built-in evidence types, their producers, typical fields, and the EvidenceFieldMatcher for requirement matching.

Every built-in evidence type with its producer, typical fields, and recording context, plus the EvidenceFieldMatcher operators for evidence requirement matching.

## Built-in Evidence Types

The BUILTIN_EVIDENCE_TYPES tuple defines the 15 canonical evidence type names. Evidence records with a type not in this list (and not prefixed with "custom:") are rejected by validate_evidence_type_name().

All 15 types share the single EvidenceRecord schema. There are no per-type dataclasses — field validation for contract requirements is performed dynamically through EvidenceFieldMatcher patterns.

- GitDiff — Produced by git operations. Typical fields: files_changed, insertions, deletions. Recorded after git diff/commit tool calls.
- TestRun — Produced by test execution. Typical fields: passed, failed, errors, command. Recorded after test runner tool calls.
- CodeDiagnostics — Produced by linting or type checking. Typical fields: errors, warnings, tool_name. Recorded after lint/typecheck tool calls.
- CommitCheckpoint — Produced at commit boundaries. Records the commit state at a beforeCommit or afterCommit hook point.
- FileDeliver — Produced by file delivery operations. Records file path, size, and delivery status.
- ArtifactVerify — Produced by artifact verification. Records artifact identity and verification result.
- DeterministicEvidenceVerifier — Produced by deterministic verifiers. Records the verifier name and pass/fail status.
- WebSearch — Produced by web search tools. Records query, result count, and source URLs.
- KnowledgeSearch — Produced by knowledge base search tools. Records query, collection, and match count.
- SourceInspection — Produced by document or source inspection. Records source identity and content digest.
- PlanVerifier — Produced by plan verification. Records plan steps and completion status.
- Calculation — Produced by calculation tools. Records expression, result, and precision metadata.
- DateRange — Produced by date/time operations. Records start, end, and timezone metadata.
- Clock — Produced by time-sensitive operations. Records observed timestamp and clock source.
- TelegramDeliveryAck — Produced by Telegram delivery confirmation. Records message_id and chat_id.

## EvidenceRecord Schema

EvidenceRecord is the canonical evidence entry stored in the evidence ledger. Each record has a validated type, status, timestamp, source provenance, arbitrary fields, and optional metadata.

- type (str) — Must be a valid built-in type name or custom:PascalCaseName.
- status (EvidenceStatus) — One of "ok", "failed", "unknown".
- observed_at (int | float, alias observedAt) — Timestamp when the evidence was observed. Must be finite.
- source (EvidenceSource) — Provenance: kind (tool_trace, adk_event, transcript, artifact, execution_contract, verifier, custom_extractor, external_ack), plus optional tool_name, tool_call_id, event_id, transcript_entry_id, artifact_id, contract_id, verifier_name, extractor_id, acknowledgement_id, channel.
- fields (Mapping[str, object]) — Arbitrary evidence fields. Frozen to MappingProxyType.
- preview (str | None) — Optional human-readable preview. Must be non-empty when provided.
- metadata (Mapping[str, object]) — Optional metadata. Frozen to MappingProxyType.

## EvidenceFieldMatcher

EvidenceFieldMatcher is used in EvidenceRequirement.fields to declare what values satisfy a requirement. At least one matcher must be set.

- equals (object | None) — Exact value match. Values are frozen recursively.
- one_of (tuple[object, ...] | None, alias oneOf) — Value must be one of the listed values. Must be non-empty when provided.
- matches (str | None) — Regex pattern match. Must be a valid, restricted-safe regex (no wildcards, no grouping, no backreferences, max 300 chars).
- exists (bool | None) — Whether the field must exist (true) or must not exist (false).

- [Evidence concepts](/docs/evidence)
- [Evidence contracts](/docs/evidence-contracts)

## Custom Evidence Types

Custom evidence types use the custom:PascalCaseName naming convention (e.g. custom:DeploymentVerification). The name must be at most 80 characters and match ^custom:[A-Z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$. Custom types are validated by the same EvidenceFieldMatcher rules as built-in types.
