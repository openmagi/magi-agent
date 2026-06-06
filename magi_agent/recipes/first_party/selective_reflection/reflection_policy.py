"""Reflection gate policy — decide whether to reflect, skip, or bound reflection.

``ReflectionPolicy`` is a frozen dataclass (immutable after construction) so a
stale reference can never be mutated mid-invocation.  All band names are
domain-agnostic strings; the policy knows nothing about GAIA.

Default bands (matching OAgents findings):
    - ``low`` / ``medium``  → full reflection (up to ``max_depth`` passes)
    - ``high``              → bounded reflection (1 pass only)
    - ``very_high``         → skip entirely (suppress reflection)

The feature is default-OFF: ``enabled=False`` causes ``decide()`` to always
return ``"skip"`` regardless of band.  Set ``MAGI_REFLECTION_ENABLED=true``
(or ``1``/``yes``/``on``) in the environment and build a policy with
``enabled=True`` to activate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ReflectionDecision = Literal["skip", "reflect", "bounded_reflect"]


@dataclass(frozen=True)
class ReflectionPolicy:
    """Gating policy for the selective reflection step.

    All attribute access after construction is guaranteed read-only (frozen
    dataclass).  Build once and reuse across invocations.

    Attributes
    ----------
    enabled:
        Master switch.  ``False`` (default) → ``decide()`` always returns
        ``"skip"``.  Maps to ``MAGI_REFLECTION_ENABLED``.
    max_depth:
        Maximum number of critique passes for ``low``/``medium`` complexity.
        Default ``1`` (one pass).
    high_complexity_max_depth:
        Maximum passes when in ``bounded_reflect`` mode (``"high"`` band).
        Always ``1``; exposed as a named field for clarity, not for overriding.
    suppress_bands:
        Tuple of band names for which reflection is fully suppressed.
        Default ``("very_high",)``.
    bounded_bands:
        Tuple of band names for which reflection runs in bounded mode.
        Default ``("high",)``.
    fail_open:
        When ``True`` (default) a reflection error is non-blocking; the commit
        proceeds with the draft as-is.  Set ``False`` to make errors fatal.
    """

    enabled: bool = False
    max_depth: int = 1
    high_complexity_max_depth: int = 1
    suppress_bands: tuple[str, ...] = field(default=("very_high",))
    bounded_bands: tuple[str, ...] = field(default=("high",))
    fail_open: bool = True

    def decide(self, band: str) -> ReflectionDecision:
        """Return the gate decision for *band*.

        Parameters
        ----------
        band:
            A ``ComplexityBand`` string (``"low"``, ``"medium"``, ``"high"``,
            or ``"very_high"``).  Unknown values fall through to ``"reflect"``
            (safe default: attempt reflection on unknown complexity).

        Returns
        -------
        ``"skip"``            — do not run reflection
        ``"reflect"``         — run full reflection (up to ``max_depth``)
        ``"bounded_reflect"`` — run one-pass bounded reflection
        """
        if not self.enabled:
            return "skip"
        if band in self.suppress_bands:
            return "skip"
        if band in self.bounded_bands:
            return "bounded_reflect"
        return "reflect"

    def effective_max_depth(self, decision: ReflectionDecision) -> int:
        """Return the effective max-depth for *decision*.

        ``"skip"`` → 0 (not called in practice), ``"bounded_reflect"`` → 1,
        ``"reflect"`` → ``self.max_depth``.
        """
        if decision == "skip":
            return 0
        if decision == "bounded_reflect":
            return self.high_complexity_max_depth
        return self.max_depth


__all__ = [
    "ReflectionDecision",
    "ReflectionPolicy",
]
