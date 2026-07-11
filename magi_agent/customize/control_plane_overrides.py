"""User-facing toggles for in-context control-plane *behaviors*.

The verification customize surface (``preset_overrides``, ``custom_rules``,
``seam_specs``) only governs the **after-tool / pre-final gate** layer. A second,
orthogonal layer of runtime behavior, the control-plane *loop controls* that
inject visible content into the live model loop (the periodic "Facts Survey"
replan, the goal nudge, the tool-synthesis reflection nudge, empty-response
recovery), had **no** user-facing switch: each is gated purely on a
``MAGI_*_ENABLED`` env flag, and the ``lab`` / dogfood runtime profiles seed
those flags ON (``runtime.local_defaults``). So a user who turned every
Customize toggle OFF still saw the Facts Survey, because the dashboard never
reached this layer.

This module closes that gap. It defines the curated catalog of *toggleable*
control-plane behaviors and an apply step that projects the persisted
``customize.json`` ``control_plane`` section onto the environment **as an
explicit overwrite** (not ``setdefault``). Wired at startup right after
``apply_*_runtime_defaults(os.environ)``, an explicit user toggle therefore
wins over the profile seed AND over a prior shell export: the toggle is the
top authority, which is the whole point of a user-facing control.

Tri-state, like ``preset_overrides``: a behavior id absent from the section
leaves its env flag untouched (so OFF/empty is byte-identical to before this
module existed). Only an explicit ``True`` / ``False`` projects.

Security: this catalog is deliberately limited to *behavior* injections. Hard
infrastructural / safety controls (compaction, evidence ledger, egress gate,
GA governance, kernel recipe/role packs) are intentionally NOT exposed here --
mirroring ``customize/catalog.py``'s refusal to map security-critical packs --
so a user cannot walk back a safety obligation through this seam.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from magi_agent.config._truthy import is_true

__all__ = [
    "ControlPlaneBehavior",
    "CONTROL_PLANE_BEHAVIORS",
    "control_plane_behavior_catalog",
    "apply_control_plane_overrides_to_env",
]


@dataclass(frozen=True)
class ControlPlaneBehavior:
    """A user-toggleable in-context control-plane behavior."""

    id: str
    env_var: str
    label: str
    description: str
    #: Additional env flags pinned to the SAME value as ``env_var`` when this
    #: behavior is toggled. Used to disable an entire re-invocation family with
    #: one toggle: the "goal-loop" toggle also pins the legacy goal-nudge so
    #: turning it OFF cannot leave an ambient re-invocation path live (F1-B).
    also_env_vars: tuple[str, ...] = ()


# Curated, conservative catalog. Each entry maps a stable UI id to the single
# ``MAGI_*_ENABLED`` flag that gates the corresponding loop control. Every flag
# here is a clean strict-truthy gate whose ON path the ``lab`` profile already
# exercises (flag alone is sufficient -- no companion params required), so a
# plain "1"/"0" overwrite toggles the behavior cleanly in both directions.
CONTROL_PLANE_BEHAVIORS: tuple[ControlPlaneBehavior, ...] = (
    ControlPlaneBehavior(
        id="facts-replan",
        env_var="MAGI_FACTS_REPLAN_ENABLED",
        label="Periodic facts survey (replan)",
        description=(
            "Injects a recurring in-context 'Facts Survey' (given / learned / "
            "still-to-look-up / derive) plus a refreshed plan every few working "
            "steps. Helps long multi-step turns stay grounded; turn it off if "
            "you find the recurring survey block noisy."
        ),
    ),
    ControlPlaneBehavior(
        id="goal-loop",
        env_var="MAGI_GOAL_LOOP_ENABLED",
        label="Goal nudge",
        description=(
            "Periodically re-injects the active goal so a long turn does not "
            "drift away from what you asked for."
        ),
        # F1-B: the same toggle also pins the LEGACY goal-nudge, so turning
        # "Goal nudge" OFF disables every ambient re-invocation family. Without
        # this, disabling the goal loop revived the legacy nudge (which re-
        # invoked the model and duplicated the answer).
        also_env_vars=("MAGI_GOAL_NUDGE_ENABLED",),
    ),
    ControlPlaneBehavior(
        id="tool-synthesis-nudge",
        env_var="MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED",
        label="Tool-result synthesis nudge",
        description=(
            "After a burst of tool calls, nudges the model to synthesize the "
            "results into an answer instead of calling more tools."
        ),
    ),
    ControlPlaneBehavior(
        id="empty-response-recovery",
        env_var="MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED",
        label="Empty-response recovery",
        description=(
            "If a turn ends with tool calls but no text, re-invokes the model "
            "once asking it to produce its final answer. Turn off to let the "
            "frontend show a fallback banner instead."
        ),
    ),
)

_BY_ID: dict[str, ControlPlaneBehavior] = {b.id: b for b in CONTROL_PLANE_BEHAVIORS}


def control_plane_behavior_catalog(
    env: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Serializable catalog for the dashboard Customize surface.

    ``enabled`` is the behavior's *current effective* state -- the truthiness of
    its env flag as the profile seed (plus any prior toggle projection) left it.
    The UI shows this when no explicit override is recorded yet, so the toggle
    reflects reality instead of guessing.
    """

    source = env if env is not None else os.environ
    return [
        {
            "id": b.id,
            "env_var": b.env_var,
            "label": b.label,
            "description": b.description,
            "enabled": is_true(source.get(b.env_var)),
        }
        for b in CONTROL_PLANE_BEHAVIORS
    ]


def _coerce_section(overrides: Mapping[str, object] | None) -> Mapping[str, object]:
    """Extract the ``control_plane`` mapping, fail-soft to empty."""

    if not isinstance(overrides, Mapping):
        return {}
    section = overrides.get("control_plane")
    if not isinstance(section, Mapping):
        return {}
    return section


def apply_control_plane_overrides_to_env(
    env: MutableMapping[str, str],
    overrides: Mapping[str, object] | None,
) -> None:
    """Project ``overrides['control_plane']`` onto ``env`` as an overwrite.

    For every catalog behavior whose id maps to an explicit ``bool`` in the
    section, set its env flag to ``"1"`` / ``"0"`` (overwrite, so the user
    toggle beats the lab/dogfood seed). Absent ids and non-bool values are
    ignored -- the env flag is left exactly as the profile defaults set it.
    Never raises: a malformed overrides document degrades to a no-op.
    """

    try:
        section = _coerce_section(overrides)
        if not section:
            return
        for behavior_id, value in section.items():
            if not isinstance(value, bool):
                # Tri-state: only explicit booleans project. Strings / null /
                # nested junk are ignored rather than guessed at.
                continue
            behavior = _BY_ID.get(behavior_id)
            if behavior is None:
                # Unknown id (stale UI, hand-edited file). Never touch a flag we
                # do not own -- in particular never a safety flag not in the
                # curated catalog.
                continue
            projected = "1" if value else "0"
            env[behavior.env_var] = projected
            # F1-B: pin any coupled flags to the SAME value (disable a whole
            # re-invocation family with one toggle).
            for coupled in behavior.also_env_vars:
                env[coupled] = projected
    except Exception:  # noqa: BLE001 - fail-soft; a bad file must not break startup
        return
