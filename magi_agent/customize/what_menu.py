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

PR-F-UX5 — evidence vs verifier/condition split
-----------------------------------------------

The same descriptor set is also viewable as two disjoint UX buckets:

* **evidence_refs** — entries whose ref prefix is ``evidence:``. These are raw
  producer records the runtime captures from tools/skills/spawns. They are the
  *input* a deterministic rule operates against. Surfaced to the wizard's
  "Check evidence record present" picker AND used as the source for the
  field-constraint type picker (verifiers have no traversable fields).
* **judgment_refs** — entries whose ref prefix is ``verifier:`` OR is an
  unprefixed named-judgment ref (e.g. the bare ``fact_grounding`` token).
  These are verdict primitives — judgments produced by built-in verifier code
  that has already evaluated some evidence and emitted a pass/fail. Surfaced
  to the wizard's new "Check verifier / condition passed" picker, where they
  live alongside user-authored named conditions in the Conditions tab with
  origin badges.

Both buckets route to the SAME backend storage payload
(``kind: deterministic_ref``, ``payload: {ref}``) — the split is purely a UX
clarification (raw evidence vs verdict primitive), not a new wire shape.

Ref-name stability invariant
----------------------------

Storage keys ``custom_rules.what.payload.ref`` reference these refs verbatim
and ``preset_map.controls_refs`` keys onto them; recipe-emitted ref strings in
``recipes/compiler.py``, ``recipes/reliability_policy.py``,
``recipes/recipe_routing.py``, ``firstparty/packs/evidence_gitdiff/pack.toml``
and ``evidence/local_tool_collector`` emit them too. Therefore the 6 ref
strings (including the bare ``fact_grounding`` and the
``evidence:artifact-delivery-ref`` token, NOT renamed to a ``verifier:`` form)
must stay byte-identical when split.
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

    .. deprecated:: PR-F-UX5
        Prefer :func:`evidence_menu` and :func:`judgment_menu` for the
        ergonomically-split UX shape. This function is preserved as the
        union of both buckets for back-compat with existing call sites
        (``customRuleMenu`` catalog field, NL compiler, tests).
    """
    return [
        {**e, "allowedActions": list(e["allowedActions"])} for e in _active_entries(env)
    ]


def _is_evidence_ref(ref: str) -> bool:
    """Classify a menu entry as raw-evidence (vs verdict-primitive).

    A ref is an evidence record iff it carries the ``evidence:`` prefix; every
    other ref (``verifier:*`` or unprefixed named judgments such as
    ``fact_grounding``) is a verdict primitive surfaced by built-in verifier
    code. See module docstring for why the bare ``fact_grounding`` token and
    the ``evidence:artifact-delivery-ref`` token are NOT renamed despite their
    semantic-vs-prefix mismatch — the strings are byte-keyed across storage,
    preset_map, recipes, and the local tool collector.
    """
    return ref.startswith("evidence:")


def evidence_menu(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Return only the raw-evidence ref descriptors (``evidence:*`` prefix).

    These are the producer records a deterministic rule operates against — the
    inputs, not the verdicts. Source for the wizard's "Check evidence record
    present" picker and the (only) source for the field-constraint type picker
    (verifiers have no traversable fields).
    """
    return [
        {**e, "allowedActions": list(e["allowedActions"])}
        for e in _active_entries(env)
        if _is_evidence_ref(e["ref"])
    ]


def judgment_menu(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Return only the verdict-primitive ref descriptors.

    Entries are built-in verifier outputs: ``verifier:*`` refs and unprefixed
    named-judgment refs (such as the bare ``fact_grounding`` token). Source
    for the wizard's "Check verifier / condition passed" picker; the Conditions
    tab merges these with user-authored named conditions under an origin badge.
    """
    return [
        {**e, "allowedActions": list(e["allowedActions"])}
        for e in _active_entries(env)
        if not _is_evidence_ref(e["ref"])
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
