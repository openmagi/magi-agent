from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class WorkflowRouteDecision(BaseModel):
    model_config = _MODEL_CONFIG

    routed: bool = False
    reason: str = "default_not_routed"
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        raise TypeError("model_construct is disabled for WorkflowRouteDecision")


def decide_workflow_route(
    *,
    eligible: bool,
    confirmed: bool,
    enabled: bool,
) -> WorkflowRouteDecision:
    """Route to a workflow ONLY when eligible AND confirmed AND enabled.
    Default = not routed. Authority flags stay False — modeling only, no live
    executor attachment in this PR."""
    if eligible and confirmed and enabled:
        return WorkflowRouteDecision(routed=True, reason="eligible_confirmed_enabled")
    return WorkflowRouteDecision(routed=False, reason="precondition_unmet")
