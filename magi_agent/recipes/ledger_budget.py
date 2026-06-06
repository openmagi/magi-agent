"""Ledger budget policy — deterministic per-task budget contract.

Default-OFF.  All classes carry ``default_off: Literal[True] = True``.
No ADK runner, provider call, browser, or live execution is attached.
"""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)

# GAIA level-specific defaults (calibrated from observed median complexity).
_GAIA_LEVEL_DEFAULTS: dict[int, dict[str, int]] = {
    1: {
        "step_budget": 10,
        "token_budget": 200_000,
        "wall_budget_ms": 120_000,
        "stall_threshold": 3,
        "max_replan_count": 2,
        "per_step_token_budget": 20_000,
        "per_step_wall_ms": 30_000,
    },
    2: {
        "step_budget": 15,
        "token_budget": 300_000,
        "wall_budget_ms": 240_000,
        "stall_threshold": 3,
        "max_replan_count": 2,
        "per_step_token_budget": 20_000,
        "per_step_wall_ms": 45_000,
    },
    3: {
        "step_budget": 20,
        "token_budget": 400_000,
        "wall_budget_ms": 360_000,
        "stall_threshold": 3,
        "max_replan_count": 2,
        "per_step_token_budget": 20_000,
        "per_step_wall_ms": 60_000,
    },
}


class LedgerBudgetPolicy(BaseModel):
    """Principled per-task budget contract.

    Replaces the operator-level ``signal.alarm(300)`` SIGALRM hack with a
    deterministic, auditable budget enforced inside the orchestration loop.

    All limits are checked on step boundaries — no process-level signals are
    needed.  A :class:`StallVerdict` is produced when any limit is crossed;
    the orchestrator records it in the progress ledger and assembles a
    graceful partial answer.
    """

    model_config = _MODEL_CONFIG

    step_budget: int = Field(ge=1, le=1_000)
    """Maximum total orchestration steps before forced termination."""

    token_budget: int = Field(ge=1, le=10_000_000)
    """Maximum cumulative tokens across all steps."""

    wall_budget_ms: int = Field(ge=1_000, le=3_600_000)
    """Wall-clock budget for the entire task (milliseconds).

    Replaces the 300 s SIGALRM operator hack.  When exceeded the orchestrator
    receives a :class:`StallVerdict` and returns a graceful partial answer.
    """

    stall_threshold: int = Field(ge=1, le=50)
    """Consecutive "stalled" steps before stall-detection fires and triggers
    a re-plan (or forced termination when ``max_replan_count`` is exhausted).
    """

    max_replan_count: int = Field(ge=0, le=20)
    """Maximum number of re-plans allowed before the orchestrator terminates
    with the best available partial answer.
    """

    per_step_token_budget: int = Field(ge=100, le=500_000)
    """Per-individual-step token cap.  Steps that exceed this produce a
    ``budget_exceeded`` :class:`ProgressStepVerdict`.
    """

    per_step_wall_ms: int = Field(ge=1_000, le=600_000)
    """Per-individual-step wall-clock cap (milliseconds)."""

    default_off: Literal[True] = Field(default=True)
    """Authority flag — this capability is default-OFF.

    Must never be set to False.  Activation is controlled exclusively by the
    ``MAGI_LEDGER_ORCHESTRATOR_ENABLED`` environment variable.
    """

    @field_validator("step_budget")
    @classmethod
    def _step_budget_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("step_budget must be at least 1")
        return value

    @model_validator(mode="after")
    def _validate_per_step_within_total(self) -> LedgerBudgetPolicy:
        if self.per_step_token_budget > self.token_budget:
            raise ValueError(
                "per_step_token_budget must not exceed total token_budget"
            )
        if self.per_step_wall_ms > self.wall_budget_ms:
            raise ValueError(
                "per_step_wall_ms must not exceed total wall_budget_ms"
            )
        return self

    # ------------------------------------------------------------------
    # Digest
    # ------------------------------------------------------------------

    def policy_digest(self) -> str:
        """Deterministic sha256 of all budget fields.

        Any single-field mutation produces a different digest, making budget
        tampering detectable by downstream consumers.
        """
        payload = {
            "stepBudget": self.step_budget,
            "tokenBudget": self.token_budget,
            "wallBudgetMs": self.wall_budget_ms,
            "stallThreshold": self.stall_threshold,
            "maxReplanCount": self.max_replan_count,
            "perStepTokenBudget": self.per_step_token_budget,
            "perStepWallMs": self.per_step_wall_ms,
            "defaultOff": True,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{sha256(encoded).hexdigest()}"

    # ------------------------------------------------------------------
    # Public projection (safe for logging / prompt injection)
    # ------------------------------------------------------------------

    def public_projection(self) -> dict[str, object]:
        return {
            "stepBudget": self.step_budget,
            "tokenBudget": self.token_budget,
            "wallBudgetMs": self.wall_budget_ms,
            "stallThreshold": self.stall_threshold,
            "maxReplanCount": self.max_replan_count,
            "perStepTokenBudget": self.per_step_token_budget,
            "perStepWallMs": self.per_step_wall_ms,
            "defaultOff": True,
            "policyDigest": self.policy_digest(),
        }


def default_gaia_policy(level: int) -> LedgerBudgetPolicy:
    """Return the default :class:`LedgerBudgetPolicy` for a GAIA question level.

    Parameters
    ----------
    level:
        GAIA question difficulty level (1, 2, or 3).  Any value outside this
        range falls back to the L2 (medium) budget.
    """
    defaults = _GAIA_LEVEL_DEFAULTS.get(level, _GAIA_LEVEL_DEFAULTS[2])
    return LedgerBudgetPolicy(**defaults)


__all__ = [
    "LedgerBudgetPolicy",
    "default_gaia_policy",
]
