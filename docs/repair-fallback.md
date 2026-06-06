# Repair and Fallback

How the runtime handles missing evidence: retry, downgrade, approve, fallback, abstain, or block.

When evidence is missing or validation fails, the enforcement decision flow determines the next action. Repair is currently implicit in the enforcement boundary and commit boundary block plans, not a separate framework.

## Evidence enforcement repair flow

When the evidence enforcement boundary evaluates a contract and the verdict fails (state is block_ready), the enforcement decision depends on the request flags. This is the current repair mechanism, implemented in EvidenceEnforcementBoundary.evaluate().

- If verdict.ok is True: status=pass, action=pass. No repair needed.
- If verdict is block_ready and repair_allowed is True: status=repair_required, action=repair. The caller should gather more evidence and retry.
- If verdict is block_ready and escalation_allowed is True: status=escalate_required, action=escalate. The caller should escalate to a higher authority (e.g. human review).
- If verdict is block_ready and neither flag is set: status=block_ready_local_fake, action=block_intent. The intent to block is recorded but not enforced (because evidence_block_enabled is Literal[False]).
- If verdict is not block_ready (state is missing or failed): status=audit_missing, action=audit. Missing evidence is logged but does not block.

## Commit boundary block plans

The commit boundary produces block plans when verifiers reject a turn. Block plans include a retryable flag and reason codes that guide the retry mechanism. This is the second repair path, implemented in commit_boundary.py.

- build_before_commit_block_plan() produces a blocked CommitBoundaryPlan when a hook or verifier blocks the turn.
- retryable is determined by is_before_commit_block_retryable(): sealed file violations, memory mutation tool requirements, claim citation requirements, and hook throws/timeouts are non-retryable.
- reason_code is extracted from the block reason using the [RULE:CODE] or [RETRY:CODE] pattern.
- required_action maps specific codes to user-facing instructions (e.g. GOAL_PROGRESS_EXECUTE_NEXT maps to call the required tool before answering).
- The commit boundary also supports retryMessage on EvidenceContract, which is passed through the verdict to the enforcement decision.

## Repair policy module

The harness includes a repair policy module (harness/repair_policy.py) that defines RepairPlan and RepairDecision types. A RepairPlan specifies a sequence of RepairAction values and a maximum attempt count. The next_repair_action() function selects the action for a given attempt index.

- RepairAction: removeUnsupportedClaims, searchMoreSources, rerunCalculation, askUserForPolicy, abstain, block.
- RepairPlan: plan_id, max_attempts (0-5), actions tuple.
- RepairDecision: action, attempt_index, reason_codes, plan_id.
- When attempt_index exceeds max_attempts or the actions list, the action falls back to block with reason_codes (repair_attempt_limit_exceeded).

## Harness policy enforcement

Harness policies define per-rule enforcement as either audit or block-on-fail.
When enforcement is audit, the violation is recorded without stopping the turn.
When enforcement is block-on-fail, a failing rule produces a commit-boundary
block plan with a retryable flag.

- Harness enforcement: audit for log-only checks, or block-on-fail for turn-blocking checks.
- Harness action types: require a specific tool, require tool input to match a pattern, run a verifier, block with a reason, or activate a builtin preset.
- Builtin preset examples: fact grounding, answer quality, self-claim checks, response-language checks, and deterministic evidence.

## Current behavior vs future repair framework

The current repair mechanism is implicit in the enforcement decision flow and commit boundary block plans. There is no dedicated RepairDecision type that orchestrates a multi-step repair loop across boundaries. The RepairPlan/RepairDecision types in harness/repair_policy.py provide the building blocks, but integration with the enforcement boundary to automatically drive repair sequences is not yet implemented.

A future explicit repair framework would unify the enforcement boundary repair_required action, commit boundary retryable block plans, and harness repair policy into a single orchestrated repair loop with configurable strategies per evidence contract.
