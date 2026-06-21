"""Default-OFF configuration for the deep web research orchestrator.

All invariants are frozen; ``enabled=False`` is the default so no existing
agent or harness changes behaviour without an explicit opt-in.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

DEEP_RESEARCH_ENABLED_ENV = "MAGI_DEEP_WEB_RESEARCH_ENABLED"
DEEP_RESEARCH_MAX_QUERIES_ENV = "MAGI_DEEP_WEB_RESEARCH_MAX_QUERIES"
DEEP_RESEARCH_MAX_ITERATIONS_ENV = "MAGI_DEEP_WEB_RESEARCH_MAX_ITERATIONS"
DEEP_RESEARCH_CROSS_VERIFY_ENV = "MAGI_DEEP_WEB_RESEARCH_CROSS_VERIFY"

def _is_true(value: object) -> bool:
    # I-2 PR A: delegates to the canonical truthy leaf so the truthy set
    # lives in exactly one place (was a local ``_TRUE_VALUES`` frozenset).
    from magi_agent.config._truthy import is_true as _canonical_is_true  # noqa: PLC0415

    return _canonical_is_true(str(value or ""))


def _int_env(key: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.environ.get(key, "").strip()
    if raw.isdigit():
        clamped = max(lo, min(hi, int(raw)))
        return clamped
    return default


class DeepResearchConfig(BaseModel):
    """Configuration for deep web research.  Default-OFF.

    The orchestrator never starts unless ``enabled=True`` is passed
    explicitly (or ``MAGI_DEEP_WEB_RESEARCH_ENABLED=1`` is set in env).
    """

    model_config = _MODEL_CONFIG

    enabled: bool = False
    max_queries: int = Field(default=3, ge=1, le=8)
    max_fetch_per_query: int = Field(default=3, ge=1, le=6)
    max_iterations: int = Field(default=2, ge=1, le=4)
    min_sources_for_cross_verify: int = Field(default=2, ge=2, le=5)
    fetch_timeout_s: float = Field(default=30.0, ge=5.0)
    cross_verify_required: bool = True
    navigate_sections: bool = True


def deep_research_config_from_env() -> DeepResearchConfig:
    """Build a ``DeepResearchConfig`` from environment variables.

    All env-var overrides are clamped to valid ranges; malformed values
    fall back to the defaults defined on ``DeepResearchConfig``.
    """
    enabled = _is_true(os.environ.get(DEEP_RESEARCH_ENABLED_ENV, ""))
    max_queries = _int_env(DEEP_RESEARCH_MAX_QUERIES_ENV, 3, lo=1, hi=8)
    max_iterations = _int_env(DEEP_RESEARCH_MAX_ITERATIONS_ENV, 2, lo=1, hi=4)
    cross_verify_raw = os.environ.get(DEEP_RESEARCH_CROSS_VERIFY_ENV, "1")
    cross_verify_required = _is_true(cross_verify_raw) if cross_verify_raw.strip() else True
    return DeepResearchConfig(
        enabled=enabled,
        max_queries=max_queries,
        max_iterations=max_iterations,
        cross_verify_required=cross_verify_required,
    )


__all__ = [
    "DEEP_RESEARCH_CROSS_VERIFY_ENV",
    "DEEP_RESEARCH_ENABLED_ENV",
    "DEEP_RESEARCH_MAX_ITERATIONS_ENV",
    "DEEP_RESEARCH_MAX_QUERIES_ENV",
    "DeepResearchConfig",
    "deep_research_config_from_env",
]
