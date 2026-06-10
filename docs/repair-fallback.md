# Repair and Fallback

How the runtime handles missing evidence: retry, downgrade, approve, fallback, abstain, or block.

When evidence is missing or validation fails, the enforcement decision flow determines the next action. Repair is currently implicit in the engine pre-final gate and commit boundary block plans, not a separate framework.

## Evidence enforcement repair flow

When an evidence contract verdict fails (state is block_ready), the next action is decided on the live output path by the engine pre-final gate (cli/engine.py) via the verifier bus (harness/verifier_bus.py).

- If verdict.ok is True: the gate passes the turn. No repair needed.
- If verdict is block_ready for a coding-domain turn: the gate blocks the final answer (Terminal.error, error="pre_final_evidence_gate_blocked"); with MAGI_CODING_REPAIR_LOOP_ENABLED it drives a repair loop that gathers more evidence and retries. This is live and on by default for the coding domain.
- For research-domain turns, the final-projection gate records the same verdict for diagnostics only: a block_ready verdict becomes "block_ready_local_fake" (its final_answer_blocking_enabled flag is Literal[False]) and does not block output.
- If verdict is not block_ready (state is missing or failed): missing evidence is logged to the audit ledger but does not block.

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
