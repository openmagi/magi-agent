from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.harness.parallel_execution import (
    ATTACHMENT_FLAGS,
    FailureAggregationMetadata,
    ParallelBatchMetadata,
    ParallelProgressSummaryMetadata,
    ParallelExecutionScope,
    ParallelToolPolicyInput,
    SpeculativeCodingEligibilityMetadata,
    SpeculativePlanningEligibilityMetadata,
    SpeculativeReasoningExperimentMetadata,
    ToolFailureMetadata,
    ToolLimitMetadata,
    ToolTimeoutBudgetMetadata,
    build_parallel_tool_policy_decision,
)


def _scope(**updates: object) -> ParallelExecutionScope:
    data = {"runOn": "main", "agentRole": "general", "spawnDepth": 0}
    data.update(updates)
    return ParallelExecutionScope.model_validate(data)


def _policy_input(**updates: object) -> ParallelToolPolicyInput:
    data = {
        "toolName": "inspect_sources",
        "toolClass": "read_only",
        "sideEffectClass": "read_only",
        "manifestParallelSafetyProof": True,
        "scope": _scope(),
        "toolClassLimit": {"toolClass": "read_only", "maxConcurrent": 4},
        "turnLimit": {"toolClass": "turn", "maxConcurrent": 8},
    }
    data.update(updates)
    return ParallelToolPolicyInput.model_validate(data)


def test_independent_read_only_tools_are_parallel_eligible_when_manifest_proves_safety() -> None:
    decision = build_parallel_tool_policy_decision(_policy_input())

    assert decision.parallel_eligible is True
    assert decision.serialization_required is False
    assert decision.default_enabled is False
    assert decision.metadata_only is True
    assert decision.scope.run_on == "main"
    assert decision.scope.agent_role == "general"
    assert decision.scope.spawn_depth == 0


def test_pure_computation_tools_are_parallel_eligible_metadata() -> None:
    decision = build_parallel_tool_policy_decision(
        _policy_input(
            toolName="calculate_totals",
            toolClass="pure_compute",
            sideEffectClass="none",
            manifestParallelSafetyProof=True,
            toolClassLimit={"toolClass": "pure_compute", "maxConcurrent": 6},
        )
    )

    assert decision.parallel_eligible is True
    assert decision.serialization_required is False
    assert decision.hard_safety_blocked is False


@pytest.mark.parametrize(
    ("tool_class", "side_effect_class"),
    (
        ("stateful", "local_process"),
        ("mutating", "local_workspace"),
        ("external_side_effect", "external"),
    ),
)
def test_stateful_mutating_and_external_side_effect_tools_are_serialized_by_default(
    tool_class: str,
    side_effect_class: str,
) -> None:
    decision = build_parallel_tool_policy_decision(
        _policy_input(
            toolClass=tool_class,
            sideEffectClass=side_effect_class,
            manifestParallelSafetyProof=False,
            toolClassLimit={"toolClass": tool_class, "maxConcurrent": 1},
        )
    )

    assert decision.parallel_eligible is False
    assert decision.serialization_required is True
    assert decision.hard_safety_blocked is True
    assert decision.hard_safety_bypassable is False


def test_manifest_proof_is_required_for_stateful_mutating_external_parallel_metadata_and_coding_stays_blocked() -> None:
    with pytest.raises(ValidationError, match="manifest proof"):
        _policy_input(
            toolClass="mutating",
            sideEffectClass="local_workspace",
            manifestParallelSafetyProof=False,
            requestedParallelEligible=True,
            toolClassLimit={"toolClass": "mutating", "maxConcurrent": 2},
        )

    proven = build_parallel_tool_policy_decision(
        _policy_input(
            toolClass="mutating",
            sideEffectClass="local_workspace",
            manifestParallelSafetyProof=True,
            requestedParallelEligible=True,
            workspaceAdoptionAvailable=False,
            toolClassLimit={"toolClass": "mutating", "maxConcurrent": 2},
        )
    )
    assert proven.parallel_eligible is False
    assert proven.serialization_required is True
    assert proven.hard_safety_blocked is True

    coding = SpeculativeCodingEligibilityMetadata(
        workspaceIsolationRepresented=True,
        workspaceAdoptionRepresented=False,
    )
    assert coding.eligible is False
    assert coding.blocked_reason == "workspace_adoption_not_represented"


@pytest.mark.parametrize("field", ("toolClassLimit", "turnLimit"))
def test_concurrency_limits_validate_positive_bounded_values(field: str) -> None:
    valid = ToolLimitMetadata(toolClass="read_only", maxConcurrent=8)
    assert valid.max_concurrent == 8

    with pytest.raises(ValidationError):
        _policy_input(**{field: {"toolClass": "read_only", "maxConcurrent": 0}})
    with pytest.raises(ValidationError):
        _policy_input(**{field: {"toolClass": "read_only", "maxConcurrent": 65}})


def test_stable_batch_ordering_and_batch_ids_are_deterministic() -> None:
    first = ParallelBatchMetadata.build(
        turn_id="turn-1",
        tool_names=("read_b", "read_a", "calc"),
        batch_ordinal=2,
    )
    second = ParallelBatchMetadata.build(
        turn_id="turn-1",
        tool_names=("read_b", "read_a", "calc"),
        batch_ordinal=2,
    )

    assert first.batch_id == second.batch_id
    assert first.ordered_tool_names == ("read_b", "read_a", "calc")
    assert [item.input_order for item in first.items] == [0, 1, 2]
    assert first.stable_ordering is True


def test_failure_aggregation_preserves_input_order_and_redacts_secret_summaries() -> None:
    aggregation = FailureAggregationMetadata.from_failures(
        batch_id="batch-1",
        failures=(
            ToolFailureMetadata(toolName="read_b", inputOrder=1, publicSummary="Authorization: Bearer abc"),
            ToolFailureMetadata(toolName="read_a", inputOrder=0, publicSummary="OPENAI_API_KEY=sk-proj-secret"),
        ),
    )

    assert [failure.tool_name for failure in aggregation.failures] == ["read_a", "read_b"]
    assert "sk-proj-secret" not in aggregation.public_summary
    assert "Bearer abc" not in aggregation.public_summary
    assert "[REDACTED]" in aggregation.public_summary


def test_public_failure_metadata_redacts_common_secret_shapes() -> None:
    failure = ToolFailureMetadata(
        toolName="fetch_private_context",
        inputOrder=0,
        publicSummary=(
            "Authorization: Basic dXNlcjpwYXNz "
            "provider sk-proj-live-secret and sk-live-secret "
            "github_token=ghp_1234567890abcdef "
            "service_role=super secret role value "
            "service_role_key=service role key value "
            "private_key=multi word private key value "
            "-----BEGIN PRIVATE KEY----- abc def -----END PRIVATE KEY-----"
        ),
    )

    assert "dXNlcjpwYXNz" not in failure.public_summary
    assert "sk-proj-live-secret" not in failure.public_summary
    assert "sk-live-secret" not in failure.public_summary
    assert "ghp_1234567890abcdef" not in failure.public_summary
    assert "super secret role value" not in failure.public_summary
    assert "service role key value" not in failure.public_summary
    assert "multi word private key value" not in failure.public_summary
    assert "BEGIN PRIVATE KEY" not in failure.public_summary
    assert "[REDACTED]" in failure.public_summary


def test_progress_summary_is_metadata_only_stably_ordered_and_redacted() -> None:
    batch = ParallelBatchMetadata.build(
        turn_id="turn-progress",
        tool_names=("read_sources", "summarize_secret"),
        batch_ordinal=1,
    )
    timeout = ToolTimeoutBudgetMetadata(
        scope="batch",
        batchId=batch.batch_id,
        timeoutMs=30000,
    )

    progress = ParallelProgressSummaryMetadata.from_batch(
        batch,
        queued=0,
        running=1,
        completed=1,
        failed=0,
        publicSummary="read_sources done; Authorization: Basic dXNlcjpwYXNz; sk-proj-secret",
        timeoutBudgets=(timeout,),
    )

    assert progress.batch_id == batch.batch_id
    assert progress.completed == 1
    assert progress.total == 2
    assert progress.ordered_tool_names == ("read_sources", "summarize_secret")
    assert progress.item_refs == ("0:read_sources", "1:summarize_secret")
    assert progress.metadata_only is True
    assert progress.stable_ordering is True
    assert progress.execution_attached is False
    assert progress.scheduler_attached is False
    assert progress.tool_execution_attached is False
    assert progress.timeout_budgets == (timeout,)
    assert "dXNlcjpwYXNz" not in progress.public_summary
    assert "sk-proj-secret" not in progress.public_summary
    assert "[REDACTED]" in progress.public_summary


def test_timeout_budget_metadata_validates_positive_bounded_detached_values() -> None:
    per_class = ToolTimeoutBudgetMetadata(
        scope="tool_class",
        toolClass="read_only",
        timeoutMs=5000,
    )
    assert per_class.metadata_only is True
    assert per_class.execution_attached is False
    assert per_class.scheduler_attached is False

    with pytest.raises(ValidationError):
        ToolTimeoutBudgetMetadata(scope="tool_class", toolClass="read_only", timeoutMs=0)
    with pytest.raises(ValidationError):
        ToolTimeoutBudgetMetadata(scope="tool_class", toolClass="read_only", timeoutMs=600001)
    with pytest.raises(ValidationError, match="batchId"):
        ToolTimeoutBudgetMetadata(scope="batch", timeoutMs=1000)
    # C-4 PR-G2 (raise-to-coerce): a forged ``Literal[False]`` flag on
    # ``model_copy(update=...)`` is now coerced to False instead of raising.
    coerced = per_class.model_copy(update={"executionAttached": True})
    assert coerced.execution_attached is False


@pytest.mark.parametrize("side_effect_class", ("none", "read_only"))
def test_speculative_planning_requires_cheap_verifier_rejection_and_no_or_read_only_side_effects(
    side_effect_class: str,
) -> None:
    eligible = SpeculativePlanningEligibilityMetadata(
        verifierCanCheaplyRejectBadDrafts=True,
        sideEffectClass=side_effect_class,
        maxDrafts=3,
    )
    assert eligible.eligible is True
    assert eligible.default_enabled is False

    blocked = SpeculativePlanningEligibilityMetadata(
        verifierCanCheaplyRejectBadDrafts=False,
        sideEffectClass=side_effect_class,
        maxDrafts=3,
    )
    assert blocked.eligible is False

    with pytest.raises(ValidationError, match="side effects"):
        SpeculativePlanningEligibilityMetadata(
            verifierCanCheaplyRejectBadDrafts=True,
            sideEffectClass="external",
            maxDrafts=3,
        )


def test_speculative_reasoning_experiments_are_benchmark_only_and_default_off() -> None:
    experiment = SpeculativeReasoningExperimentMetadata(
        benchmarkId="bench-1",
        maxVariants=4,
        verifierCanRankOutcomes=True,
    )

    assert experiment.benchmark_only is True
    assert experiment.default_enabled is False
    assert experiment.runtime_rollout_attached is False
    assert experiment.eligible is True

    # C-4 PR-G2 (raise-to-coerce): a forged ``Literal[False]`` flag on
    # ``model_copy(update=...)`` is now coerced to False instead of raising.
    coerced = experiment.model_copy(update={"runtimeRolloutAttached": True})
    assert coerced.runtime_rollout_attached is False


def test_non_hard_parallel_read_opt_out_disables_parallel_without_bypassing_hard_safety() -> None:
    opted_out = build_parallel_tool_policy_decision(
        _policy_input(optOutNonHardParallel=True)
    )
    assert opted_out.parallel_eligible is False
    assert opted_out.non_hard_parallel_opted_out is True
    assert opted_out.serialization_required is True

    hard_blocked = build_parallel_tool_policy_decision(
        _policy_input(
            toolClass="external_side_effect",
            sideEffectClass="external",
            manifestParallelSafetyProof=False,
            optOutNonHardParallel=True,
            toolClassLimit={"toolClass": "external_side_effect", "maxConcurrent": 1},
        )
    )
    assert hard_blocked.hard_safety_blocked is True
    assert hard_blocked.hard_safety_bypassable is False


def test_all_attachment_flags_remain_false_including_model_copy_updates() -> None:
    """C-4 PR-G2 (raise-to-coerce): a forged ``Literal[False]`` attachment
    flag on ``model_copy(update=...)`` is now coerced to False uniformly
    rather than raising a ``ValidationError``. The end-result invariant is
    preserved: the value still reads False on the resulting decision.
    """
    decision = build_parallel_tool_policy_decision(_policy_input())

    for flag in ATTACHMENT_FLAGS:
        assert decision.model_dump(by_alias=True)[flag] is False
        coerced = decision.model_copy(update={flag: True})
        assert coerced.model_dump(by_alias=True)[flag] is False


def test_attachment_flags_remain_false_for_python_field_name_model_copy_updates() -> None:
    """C-4 PR-G2 (raise-to-coerce): same as the alias-form sibling above,
    but via the Python snake_case field name. The kernel's introspection
    coerces uniformly regardless of whether the caller used the alias or
    the field name.
    """
    decision = build_parallel_tool_policy_decision(_policy_input())

    for field_name in (
        "traffic_attached",
        "execution_attached",
        "runner_attached",
        "route_attached",
        "scheduler_attached",
        "tool_execution_attached",
        "child_execution_attached",
        "workspace_attached",
        "canary_attached",
    ):
        coerced = decision.model_copy(update={field_name: True})
        assert getattr(coerced, field_name) is False


def test_hard_tool_eligibility_requires_manifest_proof_and_workspace_adoption() -> None:
    eligible = build_parallel_tool_policy_decision(
        _policy_input(
            toolClass="mutating",
            sideEffectClass="local_workspace",
            manifestParallelSafetyProof=True,
            requestedParallelEligible=True,
            workspaceAdoptionAvailable=True,
            toolClassLimit={"toolClass": "mutating", "maxConcurrent": 2},
        )
    )
    assert eligible.parallel_eligible is True
    assert eligible.serialization_required is False
    assert eligible.hard_safety_blocked is False

    blocked_without_adoption = build_parallel_tool_policy_decision(
        _policy_input(
            toolClass="mutating",
            sideEffectClass="local_workspace",
            manifestParallelSafetyProof=True,
            requestedParallelEligible=True,
            workspaceAdoptionAvailable=False,
            toolClassLimit={"toolClass": "mutating", "maxConcurrent": 2},
        )
    )
    assert blocked_without_adoption.parallel_eligible is False
    assert blocked_without_adoption.hard_safety_blocked is True

    with pytest.raises(ValidationError, match="mutating side effects"):
        _policy_input(
            toolClass="read_only",
            sideEffectClass="local_workspace",
            manifestParallelSafetyProof=True,
            toolClassLimit={"toolClass": "read_only", "maxConcurrent": 2},
        )
