from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RepairAction = Literal[
    "removeUnsupportedClaims",
    "searchMoreSources",
    "rerunCalculation",
    "askUserForPolicy",
    "abstain",
    "block",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class RepairPlan(BaseModel):
    model_config = _MODEL_CONFIG

    plan_id: str = Field(alias="planId")
    max_attempts: int = Field(alias="maxAttempts", ge=0, le=5)
    actions: tuple[RepairAction, ...] = ()

    @model_validator(mode="after")
    def _validate_actions_for_attempts(self) -> RepairPlan:
        if self.max_attempts > 0 and not self.actions:
            raise ValueError("actions must be non-empty when maxAttempts > 0")
        return self


class RepairDecision(BaseModel):
    model_config = _MODEL_CONFIG

    action: RepairAction
    attempt_index: int = Field(alias="attemptIndex", ge=0)
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    plan_id: str = Field(alias="planId")


def next_repair_action(plan: RepairPlan, *, attempt_index: int) -> RepairDecision:
    if attempt_index < 0:
        raise ValueError("attempt_index must be non-negative")
    if attempt_index >= plan.max_attempts or attempt_index >= len(plan.actions):
        return RepairDecision(
            action="block",
            attemptIndex=attempt_index,
            reasonCodes=("repair_attempt_limit_exceeded",),
            planId=plan.plan_id,
        )
    return RepairDecision(
        action=plan.actions[attempt_index],
        attemptIndex=attempt_index,
        reasonCodes=("repair_action_selected",),
        planId=plan.plan_id,
    )


__all__ = [
    "RepairAction",
    "RepairDecision",
    "RepairPlan",
    "next_repair_action",
]
