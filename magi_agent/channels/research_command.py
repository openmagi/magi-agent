from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.recipes.workflow_recipe import (
    DeepResearchWorkflowBundle,
    build_deep_research_workflow,
)
from magi_agent.workflows.cost_estimate import (
    WorkflowCostEstimate,
    estimate_workflow_cost,
)

__all__ = ["ResearchCommandResult", "prepare_research_command"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class ResearchCommandResult(BaseModel):
    model_config = _MODEL_CONFIG

    query: str
    cost_estimate: WorkflowCostEstimate = Field(alias="costEstimate")
    compiled_bundle: DeepResearchWorkflowBundle = Field(alias="compiledBundle")
    confirm_prompt: str = Field(alias="confirmPrompt")
    awaiting_confirmation: bool = Field(default=True, alias="awaitingConfirmation")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        raise TypeError("model_construct is disabled for ResearchCommandResult")


def prepare_research_command(
    *,
    query: str,
    per_child_token_estimate: int,
    model_microcents_per_1k: int,
) -> ResearchCommandResult:
    """Prepare a /research run: build the deep-research bundle, estimate cost,
    and produce a user-facing confirm prompt. Does NOT execute — execution
    happens only after the user confirms (routed via the PR2 seam)."""
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("research query must not be empty")
    bundle = build_deep_research_workflow(peer_attestations=[], min_peer_support=2)
    estimate = estimate_workflow_cost(
        bundle.contract,
        per_child_token_estimate=per_child_token_estimate,
        model_microcents_per_1k=model_microcents_per_1k,
    )
    credits = estimate.estimated_credits_microcents
    prompt = (
        f"이 조사는 에이전트 {estimate.estimated_child_agents}개를 돌리고 "
        f"약 {credits} microcents(credits)가 듭니다. 진행할까요? (예/아니오)"
    )
    return ResearchCommandResult(
        query=cleaned,
        costEstimate=estimate,
        compiledBundle=bundle,
        confirmPrompt=prompt,
        awaitingConfirmation=True,
    )
