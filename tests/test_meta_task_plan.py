from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.meta_orchestration.task_plan import (
    MetaChildContextBudget,
    MetaChildTaskSpec,
    MetaTaskPlan,
)


def _child(**updates: object) -> MetaChildTaskSpec:
    payload: dict[str, object] = {
        "taskId": "task:analysis-1",
        "roleRef": "role:opaque-analysis",
        "scopeRef": "scope:bounded-parent-summary",
        "allowedToolRefs": ("tool:readonly-search",),
        "contextBudget": {
            "maxInputTokens": 4000,
            "maxOutputTokens": 1200,
            "reservedEvidenceTokens": 400,
        },
        "completionContractRef": "contract:evidence-envelope-summary",
        "deliveryMode": "return",
    }
    payload.update(updates)
    return MetaChildTaskSpec.model_validate(payload)


def _plan(**updates: object) -> MetaTaskPlan:
    payload: dict[str, object] = {
        "planId": "plan:parent-meta-1",
        "parentExecutionId": "parent:exec-1",
        "objectiveDigest": "sha256:" + "a" * 64,
        "objectivePreview": "Compare public release notes and return a sourced summary.",
        "acceptanceCriteriaRefs": ("criteria:sourced-summary",),
        "childTaskSpecs": (_child(),),
        "verifierChainRefs": ("verifier:evidence-required",),
        "maxRetryBudget": 1,
    }
    payload.update(updates)
    return MetaTaskPlan.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("objectivePreview", "raw prompt: include /Users/kevin/.ssh/id_rsa"),
        ("objectivePreview", "Authorization: Bearer unsafe-token-123456"),
        ("acceptanceCriteriaRefs", ("criteria:safe", "criteria:/workspace/private")),
        ("verifierChainRefs", ("verifier:hidden-reasoning",)),
    ),
)
def test_meta_task_plan_rejects_raw_private_or_auth_values(
    field_name: str,
    bad_value: object,
) -> None:
    with pytest.raises(ValidationError):
        _plan(**{field_name: bad_value})


def test_meta_child_task_requires_evidence_envelope() -> None:
    child = _child()

    assert child.requires_evidence_envelope is True
    with pytest.raises(ValidationError):
        _child(requiresEvidenceEnvelope=False)


def test_meta_task_plan_rejects_duplicate_child_task_ids() -> None:
    with pytest.raises(ValidationError):
        _plan(
            childTaskSpecs=(
                _child(taskId="task:duplicate"),
                _child(taskId="task:duplicate", roleRef="role:opaque-review"),
            )
        )


def test_meta_task_plan_has_no_tool_execution_authority() -> None:
    plan = _plan()

    assert plan.default_off is True
    assert set(plan.authority_flags.model_dump(by_alias=True).values()) == {False}
    with pytest.raises(ValidationError):
        MetaTaskPlan.model_validate(
            {
                **plan.model_dump(by_alias=True),
                "authorityFlags": {"toolExecutionAllowed": True},
            }
        )
    with pytest.raises(ValidationError):
        plan.model_copy(update={"authorityFlags": {"toolExecutionAllowed": True}})


def test_model_generated_free_text_plan_is_not_executable_without_validation() -> None:
    generated_plan = """
    1. Run a child agent with search.
    2. Execute the verifier.
    3. Return the final answer.
    """

    with pytest.raises(ValidationError):
        MetaTaskPlan.model_validate(generated_plan)


def test_child_context_budget_is_structured_metadata_only() -> None:
    budget = MetaChildContextBudget.model_validate(
        {
            "maxInputTokens": 1000,
            "maxOutputTokens": 500,
            "reservedEvidenceTokens": 100,
        }
    )

    assert budget.max_input_tokens == 1000
    assert budget.max_output_tokens == 500
