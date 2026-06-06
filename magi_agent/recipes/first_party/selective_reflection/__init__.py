"""Selective (complexity-gated) reflection — general first-party capability.

Default-OFF via ``MAGI_REFLECTION_ENABLED`` (see
``magi_agent.config.env.parse_selective_reflection_env``).  Zero code runs in
the hot path when the flag is false.
"""

from __future__ import annotations

from magi_agent.recipes.first_party.selective_reflection.complexity_signal import (
    ComplexityBand,
    ComplexitySignal,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_policy import (
    ReflectionDecision,
    ReflectionPolicy,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_step import (
    CritiqueVerdict,
    ReflectionResult,
    run_reflection_step,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_hook import (
    REFLECTION_HOOK_ID,
    build_reflection_hook_contribution,
)

__all__ = [
    "ComplexityBand",
    "ComplexitySignal",
    "CritiqueVerdict",
    "REFLECTION_HOOK_ID",
    "ReflectionDecision",
    "ReflectionPolicy",
    "ReflectionResult",
    "build_reflection_hook_contribution",
    "run_reflection_step",
]
