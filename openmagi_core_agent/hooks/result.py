from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


HookAction = Literal["continue", "replace", "block", "skip", "permission_decision"]
PermissionDecision = Literal["approve", "deny", "ask"]


class HookResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: HookAction = "continue"
    decision: PermissionDecision | None = None
    reason: str | None = None
    value: object | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_permission_decision_action(self) -> HookResult:
        if self.decision is not None and self.action != "permission_decision":
            raise ValueError("permission decisions require action='permission_decision'")
        if self.action == "permission_decision" and self.decision is None:
            raise ValueError("permission_decision requires a decision")
        return self
