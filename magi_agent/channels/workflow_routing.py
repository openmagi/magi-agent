from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["WorkflowRouteDecision", "decide_workflow_route"]

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
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        raise TypeError("model_construct is disabled for WorkflowRouteDecision")

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(str(key), str(key)): value for key, value in update.items()})
        data["route_attached"] = False
        data["execution_attached"] = False
        _ = deep
        return type(self).model_validate(data)


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
