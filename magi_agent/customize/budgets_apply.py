"""F7 — Customize budgets applier.

Reads the operator-authored ``verification.budgets`` dict off a resolved
:class:`magi_agent.customize.verification_policy.CustomizeVerificationPolicy`
and projects each entry onto its live ``MAGI_*`` env variable via
``MutableMapping.setdefault`` so an explicit operator env value always wins
(shell export, k8s env, dogfood profile, lab seed). The applier seeds the
budget ONLY when the env variable is unset.

Triple-gated:

1. ``MAGI_CUSTOMIZE_BUDGETS_ENABLED`` (strict bool, default-OFF) — the master
   F7 switch. OFF ⇒ applier is a no-op so a fresh install / hosted serve is
   byte-identical.
2. ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` (profile-aware default-ON) — the
   master Customize switch. OFF ⇒ no policy is loaded, applier is a no-op.
3. ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` (profile-aware default-ON) — the
   per-policy switch. OFF ⇒ no rules/budgets are projected.

The applier is pure: it mutates the given env mapping and never performs I/O.
The caller (``run_governed_turn`` at turn entry) is responsible for timing so
downstream readers (``CoreToolhostHandlerSet.from_env``, ``parse_loop_guard_env``)
see the budget before they bind.

See ``docs/plans/2026-06-23-customize-depth-enrichment-design.md`` §5 PR-F7.
The proper :class:`budget_constraint` primitive (scope/turn-type aware budgets)
is deferred to the next series and will subsume this surface.
"""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.customize.verification_policy import (
        CustomizeVerificationPolicy,
    )


# Frozen vocabulary: customize budget name -> MAGI_* env variable name.
# Order is insertion-stable so the F7 dashboard, the persisted JSON, and the
# applier all reference the same canonical list.
BUDGET_ENV_MAP: dict[str, str] = {
    "maxToolCallsPerTurn": "MAGI_TOOL_MAX_CALLS_PER_TURN",
    "maxStepsBrakeHard": "MAGI_MAX_STEPS_BRAKE_HARD",
    "loopGuardHardThreshold": "MAGI_LOOP_GUARD_HARD_THRESHOLD",
}


def _budgets_master_enabled() -> bool:
    """Triple-gate read: master + customize + custom-rules all ON?

    Defensive: any flag-read failure (test isolation, broken registry) returns
    ``False`` so the applier degrades to a no-op rather than mis-projecting a
    budget on top of a turn that the operator never opted into.
    """
    try:
        from magi_agent.config.flags import (  # noqa: PLC0415
            flag_bool,
            flag_profile_bool,
        )

        if not flag_bool("MAGI_CUSTOMIZE_BUDGETS_ENABLED"):
            return False
        if not flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED"):
            return False
        if not flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED"):
            return False
        return True
    except Exception:
        return False


def apply_budgets_if_enabled(
    *,
    env: MutableMapping[str, str],
    policy: "CustomizeVerificationPolicy",
) -> None:
    """Project operator-authored Customize budgets onto live MAGI_* env (F7).

    Per-budget contract:

    * ``env`` already has the env name set → leave it (operator wins).
    * ``env`` unset AND ``policy.budget(field)`` returns a positive int →
      set ``env[name] = str(value)``.
    * ``policy.budget(field)`` returns ``None`` (no override authored) → no-op.

    No-ops entirely when the triple-gate is not satisfied (master F7 flag,
    customize-verification, custom-rules). Never raises.

    Parameters
    ----------
    env:
        Mutable env mapping to project onto (typically ``os.environ``).
    policy:
        Resolved :class:`CustomizeVerificationPolicy` (already loaded by
        :func:`apply_verification_overrides`).
    """
    if policy is None:
        return
    if not _budgets_master_enabled():
        return
    try:
        for budget_name, env_name in BUDGET_ENV_MAP.items():
            value = policy.budget(budget_name)
            if value is None:
                continue
            # setdefault: an explicit operator value (k8s env / shell export /
            # eval-profile seed / lab seed) ALWAYS wins. The applier only
            # seeds the budget when the operator has not pinned the env.
            env.setdefault(env_name, str(value))
    except Exception:
        # Fail-open: a malformed budget map must never break the turn.
        return


def effective_budget_envs(env: MutableMapping[str, str]) -> dict[str, str | None]:
    """Snapshot the current env values for each F7 budget env (read-only).

    Used by the GET /v1/app/customize/budgets endpoint so the dashboard can
    surface the resolved env value next to the persisted Customize budget
    (and explain "operator env pins this, your dashboard save is dormant").
    Missing entries return ``None`` so the UI can distinguish "unset" from
    an empty string.
    """
    return {
        budget_name: env.get(env_name)
        for budget_name, env_name in BUDGET_ENV_MAP.items()
    }
