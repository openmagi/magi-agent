"""beforeCommit HookContribution for the selective reflection gate.

``build_reflection_hook_contribution()`` returns a ``HookContribution``
admitted into the registry at ``beforeCommit`` stage with priority 25, or
``None`` when the policy is disabled.

Priority placement
------------------
The existing research proof verifier runs at the ``beforeCommit`` stage.
By convention in this codebase's hook registry the proof verifier is placed at
priority 30 (the ``_STAGE_ORDER`` constant in ``hook_composition.py`` assigns
the stage order, and within the same stage hooks are ordered by priority).

Priority 25 places the selective reflection hook *before* the proof verifier
(lower numeric priority = earlier execution), so a corrected draft can pass
the proof verifier in the same commit attempt rather than requiring an extra
round-trip.

Blocking policy
---------------
The hook is ``blocking=False`` / ``failureMode="fail_open"``: a reflection
error (timeout, model failure, parse failure) never blocks the commit.  The
agent's original draft is committed as-is.  This matches the ``fail_open``
principle in ``ReflectionPolicy``.

Security
--------
The hook is ``securityCritical=False`` and ``sideEffectful=False``.
Reflection is a best-effort quality pass, not a security gate.  Opt-out is
therefore permitted by the composition layer (security-critical hooks cannot
be disabled via ``disabled_hook_ids``).
"""

from __future__ import annotations

from magi_agent.recipes.first_party.selective_reflection.reflection_policy import (
    ReflectionPolicy,
)
from magi_agent.recipes.hook_composition import HookContribution


REFLECTION_HOOK_ID = "magi.selective-reflection"
_REFLECTION_RECIPE_REF = "magi.first-party.selective-reflection"
_REFLECTION_STAGE = "beforeCommit"
_REFLECTION_PRIORITY = 25


def build_reflection_hook_contribution(
    *,
    policy: ReflectionPolicy,
    recipe_ref: str = _REFLECTION_RECIPE_REF,
) -> HookContribution | None:
    """Return a registry-admitted ``HookContribution`` or ``None``.

    Returns ``None`` when the policy is disabled so the caller can skip
    registration entirely — zero code runs in the hot path when the feature is
    off.

    Parameters
    ----------
    policy:
        The active ``ReflectionPolicy``.  When ``policy.enabled`` is ``False``
        this function returns ``None``.
    recipe_ref:
        The recipe ref to embed in the contribution.  Overridable for testing;
        production callers use the default.
    """
    if not policy.enabled:
        return None

    payload: dict[str, object] = {
        "recipeRef": recipe_ref,
        "hookId": REFLECTION_HOOK_ID,
        "stage": _REFLECTION_STAGE,
        "priority": _REFLECTION_PRIORITY,
        "scope": ("all",),
        "idempotencyKey": None,
        "blocking": False,
        "failureMode": "fail_open",
        "sideEffectful": False,
        "securityCritical": False,
        "privateConfig": {},
    }
    payload["contributionDigest"] = HookContribution.compute_contribution_digest(payload)
    return HookContribution._from_registry_contribution(payload)


__all__ = [
    "REFLECTION_HOOK_ID",
    "build_reflection_hook_contribution",
]
