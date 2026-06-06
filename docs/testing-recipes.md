# Testing Recipes

Test recipes and harnesses with deterministic replay, contract tests, and shadow runs.

Verify runtime behavior with contract tests, deterministic replay, and shadow/canary evidence before enabling live authority.

## Testing evidence contracts with fixture evidence

Evidence contracts are deterministic: given the same contract definition and the same evidence records, the EvidenceContractEngine always produces the same verdict. This makes contracts directly testable with fixture data. Create EvidenceRecord instances with known field values, pass them to evaluate_evidence_contract() along with the contract, and assert the resulting EvidenceContractVerdict.

The contract engine (evidence/contracts.py) validates requirements against records using field matchers (equals, one_of, matches, exists). Each requirement specifies a type (matching a BUILTIN_EVIDENCE_TYPES value or custom: prefix) and optional field constraints. The engine produces a verdict with state pass, missing, failed, audit, or block_ready.

## Using local_fake_evaluation_enabled for boundary testing

The EvidenceRolloutMetadata model (evidence/rollout.py) includes a mode field that can be set to audit or block_final_answer. When testing boundary behavior locally, the evidence enforcement boundary can be configured with local_fake_evaluation_enabled to simulate enforcement decisions without connecting to a live authority provider.

This lets developers test the full boundary decision path: intent creation, boundary evaluation, and receipt production. The boundary will produce real EvidenceEnforcementDecision instances but will not attach live traffic or execution authority (both are typed as Literal[False]).

## Creating test EvidenceRecord instances

EvidenceRecord is a frozen Pydantic model. To create test instances, construct them with the required fields: type (a BUILTIN_EVIDENCE_TYPES value like TestRun, GitDiff, or CodeDiagnostics, or a custom: prefixed string), status (ok, failed, or unknown), observed_at (numeric Unix epoch timestamp), and source (an EvidenceSource with kind and relevant identifiers).

Test evidence should use realistic field values that match what the runtime would produce. The evidence/builtin.py module defines ProducerSurface mappings that show which evidence types are produced by which runtime surfaces. Use these as a reference for constructing realistic test fixtures.

## Verifying contract verdicts with known inputs

After calling evaluate_evidence_contract(contract, records), assert the verdict fields: ok (boolean), state (pass/missing/failed/audit/block_ready), matched_evidence (records that satisfied requirements), missing_requirements (requirements with no matching evidence), and failures (list of EvidenceContractFailure with code, contract_id, and message).

The failure codes are EVIDENCE_CONTRACT_MISSING (no evidence of required type), EVIDENCE_CONTRACT_STALE (evidence exists but is too old), EVIDENCE_CONTRACT_FIELD_MISMATCH (evidence exists but fields do not match), EVIDENCE_CONTRACT_COMMAND_MISMATCH (command pattern does not match), and EVIDENCE_CONTRACT_INVALID_CONFIG (contract definition is malformed).

## Running tests with pytest and shadow gates

Run pytest from the `openmagi/magi-agent` checkout. The test suite includes runtime, recipe, fixture, CLI, dashboard, and transport coverage. Tests use standard pytest fixtures and assertions without external service dependencies.

The shadow/ directory (52 files) provides staged diagnostic testing infrastructure: gate1 (basic healthcheck, simple assistant text), gate2 (fixture infrastructure), gate3a (recorded replay with input bundles and output reports), gate3b (real-time simulation), gate4 (dry-run shadows + comparison reports), gate5a (memory-free canary testing), gate5b (user-visible routing canary with mocked runner). A packaged third-party test harness for external recipe authors is planned but not yet available.
