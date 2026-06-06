# Coding Verification

Worked example of a coding recipe requiring GitDiff, TestRun, and CodeDiagnostics evidence.

A coding verification workflow uses evidence contracts to require GitDiff, TestRun, and CodeDiagnostics evidence before commits. These builtin evidence types and the coding harness pack are fully implemented.

## Coding scenario

User request: Fix the authentication bug in login.ts and make sure tests pass before committing.

The coding harness pack (corresponding to first-party recipe pack openmagi.dev-coding) provides tools (FileRead, FileEdit, PatchApply), hooks (coding-verification, completion-evidence), and a child agent review component. Evidence contracts require proof of file changes (GitDiff), test results (TestRun), and code quality checks (CodeDiagnostics) before the commit boundary.

## Builtin evidence types for coding

Three builtin evidence types are central to coding verification:

- GitDiff: records file changes with paths, hunks, and content digests. Produced after file mutations.
- TestRun: records test execution results with pass/fail status, test count, and command used. Requires after='last_code_mutation' to ensure tests ran after the latest edit.
- CodeDiagnostics: records linting, type-checking, or static analysis results. Fields can include diagnostic_count and severity.
- CommitCheckpoint: records a durable checkpoint before committing. Produced by the commit boundary.

## Evidence contracts for coding

The coding evidence contracts trigger at beforeCommit to ensure tests passed and code diagnostics are clean before any commit proceeds. The after='last_code_mutation' constraint ensures stale test results (from before the latest edit) do not satisfy the contract.

These contracts are excerpts from the coding domain implementation, which spans ~3,800 lines across git diff verification, test run validation, diagnostics checking, and planner command alignment. The full coding harness includes 10 evidence case categories and domain-specific attachment flags.

### Coding evidence contracts (Python, uses real types)

```
test_contract = EvidenceContract(
    id='coding.test-before-commit',
    description='Tests must pass after last code mutation',
    triggers=('beforeCommit',),
    requirements=(
        EvidenceRequirement(
            type='TestRun',
            after='last_code_mutation',
            fields={'passed': EvidenceFieldMatcher(equals=True)},
        ),
    ),
    on_missing='audit',
    retry_message='Run tests before committing.',
)

diff_contract = EvidenceContract(
    id='coding.diff-present',
    description='GitDiff evidence must exist before commit',
    triggers=('beforeCommit',),
    requirements=(
        EvidenceRequirement(type='GitDiff'),
    ),
    on_missing='audit',
)
```

## Scoping to coding role

The coding contracts scope to agent_role='coding' for both main and child runs. The default harness state includes the coding pack with effective_harness_packs containing 'coding' and 'hard-safety'. Child coding agents (spawn_depth > 0) get only the coding and hard-safety packs.

### Coding scope (Python)

```
coding_scope = EvidenceContractScope(
    contract_id='coding.test-before-commit',
    agent_roles=('coding',),
    run_on=('main', 'child'),
    enforcement='audit',
    opt_out_allowed=True,  # users can opt out of TDD requirement
    hard_safety=False,
)
```

## Evidence contract verdicts

When the beforeCommit trigger fires, the runtime evaluates each active evidence contract. An EvidenceContractVerdict is produced with ok (boolean), state ('audit', 'pass', 'missing', 'failed', 'block_ready'), enforcement ('audit' or 'block_final_answer'), matched_evidence, missing_requirements, and failures.

In audit mode (the default), missing evidence is logged but does not block the commit. In block_final_answer mode, the runtime prevents the final answer when evidence is missing. The audit_before_block=true default ensures contracts run in audit mode before being promoted to blocking.
