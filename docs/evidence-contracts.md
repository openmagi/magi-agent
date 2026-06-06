# Evidence Contracts

Define and test evidence contracts that declare required evidence for agent actions.

Evidence contracts declare what evidence must be present at specific lifecycle triggers and what action to take when evidence is missing. The contract engine evaluates contracts deterministically against collected evidence records.

## EvidenceContract schema

An EvidenceContract is a Pydantic model that declares the evidence requirements for a specific verification concern. The contract engine evaluates it against evidence records and produces a verdict.

- id must match the pattern [A-Za-z0-9][A-Za-z0-9._:-]*.
- triggers must be non-empty. afterToolUse evaluates after each tool call. beforeCommit evaluates before the turn commits.
- when is an optional condition mapping that can provide boundary timestamps (lastCodeMutation, contractStart).
- on_missing determines the verdict state when evidence is missing: audit logs a warning, block_final_answer produces a block_ready verdict.
- retry_message is passed through the verdict to guide the model on what evidence to gather.

### EvidenceContract fields

```
class EvidenceContract(BaseModel):
    id: str                    # unique contract identifier
    description: str | None    # human-readable purpose
    triggers: tuple[EvidenceTrigger, ...]  # afterToolUse | beforeCommit
    when: Mapping[str, object] | None  # condition for activation
    requirements: tuple[EvidenceRequirement, ...]  # what evidence is needed
    on_missing: EvidenceOnMissing  # audit | block_final_answer
    retry_message: str | None  # message for the model on retry
    scope: EvidenceContractScopeMetadata | None  # scoping rules
    traffic_attached: Literal[False]   # always False
    execution_attached: Literal[False]  # always False
```

## EvidenceRequirement and field matchers

Each requirement in a contract specifies an evidence type that must be present, with optional constraints on freshness, command patterns, exit codes, and field values.

- type must match a builtin evidence type or use custom:PascalCaseName format.
- after controls freshness: last_code_mutation requires evidence observed after the last code change, contract_start requires evidence observed after the contract activation timestamp.
- command_pattern and exit_code are only valid for TestRun type requirements. Using them on other types produces an EVIDENCE_CONTRACT_INVALID_CONFIG failure.
- Field matchers use restricted safe regex: no lookaheads, no grouping constructs, no brace quantifiers, no unbounded wildcards, max 300 characters.
- At least one matcher (equals, one_of, matches, or exists) must be specified per field entry.

### EvidenceRequirement fields

```
class EvidenceRequirement(BaseModel):
    type: str           # must match BUILTIN_EVIDENCE_TYPES or custom:*
    after: EvidenceAfter | None  # last_code_mutation | contract_start
    command_pattern: str | None  # regex for TestRun command field
    exit_code: int | None        # required exit code for TestRun
    fields: Mapping[str, EvidenceFieldMatcher]  # field value constraints

class EvidenceFieldMatcher(BaseModel):
    equals: object | None   # exact match (strict type checking)
    one_of: tuple | None    # value must be one of these
    matches: str | None     # regex pattern match
    exists: bool | None     # field must exist (True) or not (False)
```

## Trigger mechanics

Contract triggers determine when the contract is evaluated during the turn lifecycle.

- afterToolUse: the contract is evaluated after each tool call completes. This allows incremental evidence checking as the agent works. Useful for verifying that specific tools were called correctly.
- beforeCommit: the contract is evaluated before the turn is committed. This is the final check before output is projected. Useful for ensuring all required evidence has been gathered before the response is finalized.
- A contract can have both triggers, meaning it is evaluated at both points.

## on_missing enforcement

The on_missing field determines the severity of missing evidence.

- audit: missing evidence produces a verdict with state audit or missing. The turn is not blocked. Missing evidence is logged for review. This is the default for contracts that inform rather than enforce.
- block_final_answer: missing evidence produces a verdict with state block_ready. When consumed by the enforcement boundary, this triggers repair_required, escalate_required, or block_intent depending on the enforcement configuration. This is used for contracts that must be satisfied before output.

## Contract scoping

EvidenceContractScopeMetadata controls when a contract applies based on the agent context.

- agent_roles: tuple of EvidenceAgentRole (general, coding, research). Contract applies only when the agent role matches.
- run_on: tuple of EvidenceRunOn (main, child). Contract applies only when running as main agent or child agent.
- spawn_depth: EvidenceSpawnDepthRange with min_depth and max_depth. Controls nesting depth applicability.
- enforcement: off, audit, or block_final_answer. Scope-level enforcement override.
- audit_before_block: when True (default), audit logs are written before blocking. Required when enforcement is block_final_answer.
- opt_out_allowed: when True (default), the contract can be opted out of. When False, the contract is mandatory.
- hard_safety: when True, the contract enforces a safety invariant and cannot be opt-out allowed.

## Example evidence contract

A complete evidence contract that requires a passing test run after any code change before the turn can commit.

### Test-after-code-change contract

```
{
  "id": "test-after-code-change",
  "description": "Require passing tests after code modifications",
  "triggers": ["beforeCommit"],
  "when": { "lastCodeMutation": 1716840000 },
  "requirements": [
    {
      "type": "TestRun",
      "after": "last_code_mutation",
      "exitCode": 0,
      "fields": {
        "passed": { "equals": true }
      }
    }
  ],
  "onMissing": "block_final_answer",
  "retryMessage": "Run the test suite before completing.",
  "trafficAttached": false,
  "executionAttached": false
}
```
