from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.workflows.compiler import CompiledWorkflowContract

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class WorkflowCostEstimate(BaseModel):
    model_config = _MODEL_CONFIG

    workflow_id: str = Field(alias="workflowId")
    estimated_child_agents: int = Field(alias="estimatedChildAgents", ge=0)
    estimated_total_tokens: int = Field(alias="estimatedTotalTokens", ge=0)
    estimated_credits_microcents: int = Field(alias="estimatedCreditsMicrocents", ge=0)
    basis: Mapping[str, object]

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        raise TypeError("model_construct is disabled for WorkflowCostEstimate")


def estimate_workflow_cost(
    contract: CompiledWorkflowContract,
    *,
    per_child_token_estimate: int,
    model_microcents_per_1k: int,
) -> WorkflowCostEstimate:
    children = len(contract.selected_recipes)
    total_tokens = children * max(0, per_child_token_estimate)
    credits = (total_tokens // 1000) * max(0, model_microcents_per_1k)
    return WorkflowCostEstimate(
        workflowId=contract.workflow_id,
        estimatedChildAgents=children,
        estimatedTotalTokens=total_tokens,
        estimatedCreditsMicrocents=credits,
        basis={
            "perChildTokenEstimate": per_child_token_estimate,
            "modelMicrocentsPer1k": model_microcents_per_1k,
        },
    )
