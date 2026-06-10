"""Hosted-deployment control-stage overlay.

Mirrors :mod:`magi_agent.runtime.local_defaults` but for *hosted* bots
(real ``bot_id``/``user_id``/``gateway_token``). Hosted pods today run with all
ControlPlane/observability/introspection controls OFF because
:func:`apply_local_full_runtime_defaults` is gated on the local-dev identity.

This module adds an explicit, operator-driven overlay keyed on a single
``MAGI_CONTROL_STAGE`` variable so the verified-locally controls become
*flippable* on hosted without changing any code-level default. The overlay only
applies when the deployment is explicitly marked hosted
(``MAGI_DEPLOYMENT=hosted``) — never via reverse-detection — and uses
``setdefault`` so explicit operator env always wins.

Scope by PR:

* PR1 (C8): ``observability`` — DB on the hosted PVC subPath.
* PR2 (C3, this change): the six ControlPlane controls. ``resilience`` wires
  edit-retry, loop-guard, error-recovery and max-steps-brake; ``full`` adds
  context-compaction and shadow self-review; ``hardgate`` promotes self-review
  from shadow to live. control_plane.py already reads each flag from env, so
  no builder change is needed — the overlay just ``setdefault``s the canonical
  flag values per stage.

The tau-bench live result for the C3 controls was *null* (resilience controls
do not move pass^k accuracy metrics — doc 14 §0/§3-2). This overlay is about
*enable-ability* on hosted, not a performance claim; stage promotion is decided
by operational/resilience metrics, not accuracy benches.

The default stage is ``off``, which is byte-identical to today's hosted
runtime. Stages are additive: ``full`` includes everything in ``resilience``
and ``hardgate`` everything in ``full``. Per doc 14, C9 introspection /
memory-write and C11 coding-repair / doc-coverage are explicitly NOT wired
here — sibling PRs own those.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

CONTROL_STAGE_ENV = "MAGI_CONTROL_STAGE"
HOSTED_DEPLOYMENT_ENV = "MAGI_DEPLOYMENT"

DEFAULT_CONTROL_STAGE = "off"
CONTROL_STAGES = ("off", "resilience", "full", "hardgate")

# Hosted PVC subPath for the observability SQLite DB. The hosted rootfs is
# read-only; the workspace PVC is the only writable mount, so the observability
# events DB must live there (not the cwd ``.openmagi``).
HOSTED_OBS_HOME = "/workspace/.openmagi"

# --- Per-stage overlay fragments (composed additively below) ---

# C8 observability (PR1): only at ``full`` and above. DB lands on the PVC.
_OBSERVABILITY_OVERLAY: Mapping[str, str] = {
    "MAGI_OBSERVABILITY_ENABLED": "1",
    "MAGI_OBS_HOME": HOSTED_OBS_HOME,
}

# C3 resilience controls (PR2): edit-retry, loop-guard, error-recovery,
# max-steps-brake. control_plane.py registers each control only when its flag is
# true. These are accuracy-neutral (tau-bench null) but operationally safe.
_C3_RESILIENCE_OVERLAY: Mapping[str, str] = {
    "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
    "MAGI_LOOP_GUARD_ENABLED": "1",
    "MAGI_ERROR_RECOVERY_ENABLED": "1",
    "MAGI_MAX_STEPS_BRAKE_ENABLED": "1",
}

# C3 ``full`` additions (PR2): context-compaction + self-review. self-review is
# shadow-first on hosted — enabled, but ``MAGI_SELF_REVIEW_SHADOW`` stays "1" so
# it only observes (no live candidate generation) until ``hardgate``.
_C3_FULL_OVERLAY: Mapping[str, str] = {
    "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
    "MAGI_SELF_REVIEW_ENABLED": "1",
    "MAGI_SELF_REVIEW_SHADOW": "1",
}

# C3 ``hardgate`` promotion (PR2): flip self-review from shadow to live.
_C3_HARDGATE_OVERLAY: Mapping[str, str] = {
    "MAGI_SELF_REVIEW_SHADOW": "0",
}


def _compose(*fragments: Mapping[str, str]) -> Mapping[str, str]:
    """Merge overlay fragments left-to-right (later fragments win)."""

    merged: dict[str, str] = {}
    for fragment in fragments:
        merged.update(fragment)
    return merged


# Stage -> env overlay. Stages are additive: each higher stage layers its own
# fragment on top of the lower stage's overlay. Stage ``off`` is empty (no-op),
# keeping the hosted runtime byte-identical to today.
_RESILIENCE_OVERLAY = _compose(_C3_RESILIENCE_OVERLAY)
_FULL_OVERLAY = _compose(
    _RESILIENCE_OVERLAY, _OBSERVABILITY_OVERLAY, _C3_FULL_OVERLAY
)
_HARDGATE_OVERLAY = _compose(_FULL_OVERLAY, _C3_HARDGATE_OVERLAY)

HOSTED_CONTROL_STAGE_DEFAULTS: Mapping[str, Mapping[str, str]] = {
    "off": {},
    "resilience": _RESILIENCE_OVERLAY,
    "full": _FULL_OVERLAY,
    "hardgate": _HARDGATE_OVERLAY,
}


def is_hosted_deployment(environ: Mapping[str, str]) -> bool:
    """True only when the operator explicitly marks the deployment hosted.

    We require an explicit ``MAGI_DEPLOYMENT=hosted`` marker rather than
    reverse-detecting from the absence of the local-dev identity, which is
    fragile (see doc 14 open-decision #2).
    """

    raw = environ.get(HOSTED_DEPLOYMENT_ENV)
    return raw is not None and raw.strip().lower() == "hosted"


def resolve_control_stage(environ: Mapping[str, str]) -> str:
    """Resolve the requested control stage, failing safe to ``off``.

    Unknown / empty values fall back to ``off`` so a typo never silently flips
    a more aggressive stage.
    """

    raw = (environ.get(CONTROL_STAGE_ENV) or "").strip().lower()
    if raw in CONTROL_STAGES:
        return raw
    return DEFAULT_CONTROL_STAGE


def apply_hosted_runtime_defaults(environ: MutableMapping[str, str]) -> None:
    """Overlay the hosted control-stage defaults onto ``environ`` in place.

    No-op unless the deployment is explicitly hosted. ``setdefault`` semantics:
    explicit operator env always wins. Stage ``off`` (the default) sets nothing,
    keeping hosted byte-identical to today.
    """

    if not is_hosted_deployment(environ):
        return
    stage = resolve_control_stage(environ)
    for key, value in HOSTED_CONTROL_STAGE_DEFAULTS.get(stage, {}).items():
        environ.setdefault(key, value)
