"""General-purpose task-complexity signal for the selective reflection gate.

The ``ComplexitySignal`` captures runtime observables (tool-call count,
sub-goal count) and derives a ``ComplexityBand`` classification.  The gate
that uses this signal is domain-agnostic: GAIA Level labels are **never**
consulted.  GAIA Level 3 tasks empirically produce ``high``/``very_high``
band values because they require 6+ tool calls — that mapping emerges from
observed task behaviour, not from hardcoding.

Band thresholds are configurable via environment variables so they can be
tuned per deployment without a code change:
    MAGI_REFLECTION_LOW_MAX      (default 2)
    MAGI_REFLECTION_MEDIUM_MAX   (default 5)
    MAGI_REFLECTION_HIGH_MAX     (default 10)

Any ``estimated_step_count`` above ``MAGI_REFLECTION_HIGH_MAX`` maps to
``very_high``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


ComplexityBand = Literal["low", "medium", "high", "very_high"]

# ---------------------------------------------------------------------------
# Configurable thresholds — env-overridable, parsed once at import time.
# ---------------------------------------------------------------------------
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _int_from_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value < 0:
            return default
        return value
    except ValueError:
        return default


_LOW_MAX: int = _int_from_env("MAGI_REFLECTION_LOW_MAX", 2)
_MEDIUM_MAX: int = _int_from_env("MAGI_REFLECTION_MEDIUM_MAX", 5)
_HIGH_MAX: int = _int_from_env("MAGI_REFLECTION_HIGH_MAX", 10)


@dataclass(frozen=True)
class ComplexitySignal:
    """General-purpose task-complexity estimate from runtime observables.

    Parameters
    ----------
    tool_call_count:
        Number of distinct tool invocations accumulated in the current turn's
        context.  Available on every ADK turn.
    sub_goal_count:
        Number of decomposed sub-goals in the planning phase.  Provided by a
        ledger contract when active; defaults to ``0`` when no ledger is used.
    estimated_step_count:
        ``max(tool_call_count, sub_goal_count)`` — the primary gate input.
        Using the maximum is the *conservative* direction: a task with few tool
        calls but many planned sub-goals is treated as complex.
    band:
        Derived ``ComplexityBand`` classification.  The gate policy consumes
        this field; callers should not inspect ``estimated_step_count`` directly.
    """

    tool_call_count: int
    sub_goal_count: int
    estimated_step_count: int
    band: ComplexityBand

    @classmethod
    def from_runtime(
        cls,
        *,
        tool_call_count: int,
        sub_goal_count: int = 0,
    ) -> "ComplexitySignal":
        """Construct a signal from raw runtime counters.

        This is the **only** constructor callers should use.  Do not pass
        ``band`` or ``estimated_step_count`` directly — they are derived.

        Parameters
        ----------
        tool_call_count:
            Number of tool invocations in the current turn.  Must be >= 0.
        sub_goal_count:
            Number of ledger sub-goals (0 when no ledger is active).
        """
        if tool_call_count < 0:
            raise ValueError("tool_call_count must be >= 0")
        if sub_goal_count < 0:
            raise ValueError("sub_goal_count must be >= 0")
        estimated = max(tool_call_count, sub_goal_count)
        band: ComplexityBand
        if estimated <= _LOW_MAX:
            band = "low"
        elif estimated <= _MEDIUM_MAX:
            band = "medium"
        elif estimated <= _HIGH_MAX:
            band = "high"
        else:
            band = "very_high"
        return cls(
            tool_call_count=tool_call_count,
            sub_goal_count=sub_goal_count,
            estimated_step_count=estimated,
            band=band,
        )


__all__ = [
    "ComplexityBand",
    "ComplexitySignal",
]
