# Build a Harness

Step-by-step guide to creating evidence contracts and attaching them to the harness engine.

Walk through defining an EvidenceContract with triggers, requirements, and on_missing behavior, then attaching it to HarnessEngine via EvidenceContractScope for role-scoped enforcement.

## Define an EvidenceContract

An EvidenceContract declares what evidence must be present at specific lifecycle points. It has an id, triggers (when to check), requirements (what evidence to look for), and on_missing (what to do when evidence is absent).

Triggers are lifecycle hook points: 'afterToolUse' checks after each tool execution, 'beforeCommit' checks before committing results. Requirements specify evidence types (from the 17 builtin types or custom:PascalCaseName), optional field matchers, and after constraints.

Pydantic models use populate_by_name=True, so both snake_case field names and camelCase aliases work. Examples here use snake_case for consistency with Python conventions.

This is a minimal single-contract example. Production harnesses like the research domain (source_ledger.py: 855 lines, citation_audit.py: 285 lines, claim_graph.py: 786 lines) or coding domain (coding_verification.py: 355 lines) compose many contracts with scope-aware enforcement, repair policies, and multi-layer evidence tracking.

### EvidenceContract definition (Python, implemented and active)

```
from magi_agent.evidence.types import (
    EvidenceContract, EvidenceRequirement, EvidenceFieldMatcher,
)

contract = EvidenceContract(
    id='myorg.test-before-commit',
    description='Require TestRun evidence before committing code changes',
    triggers=('beforeCommit',),
    requirements=(
        EvidenceRequirement(
            type='TestRun',
            after='last_code_mutation',
            fields={
                'passed': EvidenceFieldMatcher(equals=True),
            },
        ),
    ),
    on_missing='audit',  # or 'block_final_answer'
    retry_message='Run tests before committing.',
)
```

## Scope with EvidenceContractScope

To attach a contract to the harness engine, wrap it in an EvidenceContractScope. This declares which agent roles, run contexts, and spawn depths the contract applies to. The scope also sets enforcement level and opt-out policy.

### EvidenceContractScope (Python, implemented and active)

```
from magi_agent.harness.evidence_scope import (
    EvidenceContractScope, SpawnDepthRange,
)

scope = EvidenceContractScope(
    contract_id='myorg.test-before-commit',
    agent_roles=('coding',),
    run_on=('main', 'child'),
    spawn_depth=SpawnDepthRange(min_depth=0, max_depth=2),
    enforcement='audit',  # 'off' | 'audit' | 'block_final_answer'
    audit_before_block=True,
    opt_out_allowed=True,
    hard_safety=False,
)
```

## Attach to HarnessEngine

Pass evidence contract scopes and hook manifests to HarnessEngine. When resolve() is called with a HarnessResolutionRequest, the engine filters contracts by scope, resolves opt-outs, and returns scoped hooks with a ResolvedHarnessPresetState containing evidence contract snapshots.

### HarnessEngine usage (Python, implemented and active)

```
from magi_agent.harness.engine import (
    HarnessEngine, HarnessResolutionRequest,
)

engine = HarnessEngine(
    hooks=(my_hook_manifest,),
    evidence_contracts=(scope,),
    evidence_rollout_mode='audit',  # default enforcement mode
)

request = HarnessResolutionRequest(
    agent_role='coding',
    spawn_depth=0,
    run_on='main',
)
selected_hooks, state = engine.resolve(request)
# state.evidence_contracts contains resolved snapshots
# state.effective_evidence_contracts lists active contract IDs
```

## Evidence requirements and field matchers

EvidenceRequirement.fields maps field names to EvidenceFieldMatcher instances. Matchers support equals (exact value), one_of (value in set), matches (restricted safe regex), and exists (field presence). At least one matcher must be declared per field.

The after field constrains timing: 'last_code_mutation' requires evidence collected after the most recent file change, 'contract_start' requires evidence from the current contract evaluation. command_pattern and exit_code apply to tool execution evidence.

- EvidenceFieldMatcher.equals: exact value match (frozen JSON-like values)
- EvidenceFieldMatcher.one_of: value must be in the provided tuple
- EvidenceFieldMatcher.matches: restricted regex (no lookaheads, no unbounded wildcards, max 300 chars)
- EvidenceFieldMatcher.exists: boolean, checks field presence
- Regex restrictions: no grouping constructs, no brace quantifiers, no nested quantified groups

## Testing with fixture evidence

Test evidence contract evaluation by constructing EvidenceRecord fixtures and checking that the contract verdict is correct. Use the real EvidenceContractVerdict type which reports ok, state, enforcement, missing_requirements, matched_evidence, and failures.

### Fixture evidence test (Python)

```
from magi_agent.evidence.types import (
    EvidenceRecord, EvidenceSource,
)

record = EvidenceRecord(
    type='TestRun',
    status='ok',
    observed_at=1716840000,
    source=EvidenceSource(
        kind='tool_trace',
        tool_name='Bash',
    ),
    fields={'passed': True, 'test_count': 42},
)
# Use record in contract evaluation tests
```
