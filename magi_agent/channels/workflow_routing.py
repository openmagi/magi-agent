from __future__ import annotations

from typing import Literal

from pydantic import Field

from magi_agent.ops.authority import FalseOnlyAuthorityModel

__all__ = ["WorkflowRouteDecision", "decide_workflow_route"]


class WorkflowRouteDecision(FalseOnlyAuthorityModel):
    routed: bool = False
    reason: str = "default_not_routed"
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")


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
