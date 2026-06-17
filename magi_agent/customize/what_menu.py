"""WHAT-menu for deterministic custom rules.

A custom ``deterministic_ref`` rule may only require a ref that has a LIVE
producer on the turn it fires. The menu offers exactly that producer-backed set,
in two tiers:

* **base** — refs ``evidence/local_tool_collector._inferred_refs`` emits
  unconditionally on every applicable turn (always producible).
* **config-gated** — refs an engine satisfier in ``cli/engine.py`` folds into the
  pre-final gate ONLY when that satisfier is active (its producer flag resolves
  ON, or the matching Customize preset is enabled). Surfacing such a ref while
  its producer is inert would let a custom rule require a ref nothing emits →
  unconditional block ("fake toggle"). So each config-gated entry is listed only
  when its producer is currently active; ``known_refs``/``is_known_ref`` reflect
  the same live state, and the assembly-layer compile path
  (``cli/real_runner._apply_customize_verification``) drops a persisted rule whose
  producer has since gone inert.

This is an EXPLICIT descriptor mapping (spec §12 / roadmap §6.5.2), not a raw
set-intersection of ``BUILTIN_EVIDENCE_TYPES`` and ``_inferred_refs``. Keep the
base refs in sync with ``_inferred_refs`` and the config-gated entries in sync
with their ``cli/engine.py`` satisfier gate; ``test_customize_what_menu`` guards
both invariants.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Each entry: the public ref + a human label, its source evidence type, the
# enforcement tier, the (fixed) fire-at point, and the actions a det rule may use.
_BASE_MENU: tuple[dict[str, Any], ...] = (
    {
        "ref": "verifier:dev-coding:test-evidence",
        "label": "Tests pass after a code change",
        "evidenceType": "TestRun",
        "tier": "deterministic",
        "firesAt": "pre_final",
        "allowedActions": ("block", "retry", "audit"),
    },
    {
        "ref": "evidence:test-run",
        "label": "Tests were actually run",
        "evidenceType": "TestRun",
        "tier": "deterministic",
        "firesAt": "pre_final",
        "allowedActions": ("block", "retry", "audit"),
    },
    {
        "ref": "evidence:git-diff",
        "label": "A code change was recorded (git diff)",
        "evidenceType": "GitDiff",
        "tier": "deterministic",
        "firesAt": "pre_final",
        "allowedActions": ("block", "retry", "audit"),
    },
)

# Config-gated entries: (descriptor, producer flag, Customize preset id). The
# (flag, preset) pair MUST mirror the activeness gate of the matching engine
# satisfier in ``cli/engine.py`` so the menu never advertises an inert producer.
_CONFIG_GATED_MENU: tuple[tuple[dict[str, Any], str, str], ...] = (
    (
        {
            "ref": "fact_grounding",
            "label": "Factual values are grounded in opened sources",
            "evidenceType": "FactGrounding",
            "tier": "deterministic",
            "firesAt": "pre_final",
            "allowedActions": ("block", "retry", "audit"),
        },
        "MAGI_FACT_GROUNDING_VERIFICATION_ENABLED",
        "fact-grounding",
    ),
    (
        {
            "ref": "verifier:research-source-evidence",
            "label": "At least one source was actually inspected",
            "evidenceType": "SourceLedger",
            "tier": "deterministic",
            "firesAt": "pre_final",
            "allowedActions": ("block", "retry", "audit"),
        },
        "MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED",
        "source-authority",
    ),
    (
        {
            "ref": "evidence:artifact-delivery-ref",
            "label": "Promised artifacts were actually delivered",
            "evidenceType": "ArtifactDelivery",
            "tier": "deterministic",
            "firesAt": "pre_final",
            "allowedActions": ("block", "retry", "audit"),
        },
        "MAGI_GA_DELIVERABLE_GATE_ENABLED",
        "artifact-delivery",
    ),
)


def _flag_on(name: str, env: Mapping[str, str] | None) -> bool:
    """True if a registered flag resolves ON, dispatching by its kind.

    Tracks a ``_b``→``_pb`` (strict-OFF → profile-aware default-ON) registration
    change automatically, so the menu predicate stays aligned with the satisfier
    gate across H0-2.
    """
    from magi_agent.config.flags import flag_bool, flag_profile_bool, get_flag

    try:
        spec = get_flag(name)
    except Exception:
        return False
    try:
        if spec.kind == "profile_bool":
            return flag_profile_bool(name, env=env)
        if spec.kind == "bool":
            return flag_bool(name, env=env)
    except Exception:
        return False
    return False


def _producer_active(flag: str, preset_id: str, env: Mapping[str, str] | None) -> bool:
    if _flag_on(flag, env):
        return True
    try:
        from magi_agent.customize.runtime_gate import preset_enabled

        return preset_enabled(preset_id, default=False)
    except Exception:
        return False


def _active_entries(env: Mapping[str, str] | None) -> tuple[dict[str, Any], ...]:
    extra = tuple(
        descriptor
        for descriptor, flag, preset_id in _CONFIG_GATED_MENU
        if _producer_active(flag, preset_id, env)
    )
    return (*_BASE_MENU, *extra)


def what_menu(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Return the WHAT-menu descriptors (lists, JSON-serializable).

    Config-gated entries appear only while their producer is currently active.
    """
    return [
        {**e, "allowedActions": list(e["allowedActions"])} for e in _active_entries(env)
    ]


def known_refs(env: Mapping[str, str] | None = None) -> frozenset[str]:
    return frozenset(e["ref"] for e in _active_entries(env))


def is_known_ref(ref: str, env: Mapping[str, str] | None = None) -> bool:
    return ref in known_refs(env)


def allowed_actions_for(ref: str, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    for e in _active_entries(env):
        if e["ref"] == ref:
            return tuple(e["allowedActions"])
    return ()
