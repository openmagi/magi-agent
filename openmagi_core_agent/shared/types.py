from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class WarningLevel(str, Enum):
    NORMAL = "normal"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class TokenBudgetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    context_window: int
    total_tokens: int
    utilization: float
    warning_level: WarningLevel
    message_count: int
