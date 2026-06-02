from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

# Re-export shared types so existing imports from context.types still work.
from openmagi_core_agent.shared.types import TokenBudgetSnapshot, WarningLevel

__all__ = [
    "ContextManagementConfig",
    "TokenBudgetSnapshot",
    "TrackedMessage",
    "WarningLevel",
]


class ContextManagementConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    moderate_threshold: float = 0.60
    high_threshold: float = 0.75
    critical_threshold: float = 0.90
    proactive_recovery_enabled: bool = False

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "ContextManagementConfig":
        if not (0.0 <= self.moderate_threshold <= self.high_threshold <= self.critical_threshold <= 1.0):
            raise ValueError("thresholds must satisfy 0 ≤ moderate ≤ high ≤ critical ≤ 1.0")
        return self


class TrackedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    tokens: int
    tool_use_id: str | None = None
    role: str = ""
    kind: str = ""  # "user_message", "tool_result", etc.
