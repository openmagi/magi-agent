"""Phase 2 — ``custom_rules.scope`` enforcement filter.

Adds an optional ``current_scope`` argument to the turn-time accessors:
``enabled_tool_perm_rules`` + ``enabled_llm_criterion_rules``. When supplied,
each accessor returns ONLY rules whose ``scope`` matches the current turn (per
``preset_scope_matches``: ``always`` is universal; multi-scope match-any).

Backwards-compat: omitting ``current_scope`` (or passing ``None``) preserves
the historic scope-blind behavior so existing call sites that have not yet
been threaded keep working.

``enabled_deterministic_refs`` is intentionally NOT touched in this PR — it is
called from assembly build time (no per-turn scope available) and a safe fix
needs the recipe-pack ref-ownership work (Phase 4).
"""
from __future__ import annotations

from typing import Any

from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def _rule_tool_perm(*, scope: str, id_: str) -> dict[str, Any]:
    return {
        "id": id_,
        "scope": scope,
        "enabled": True,
        "what": {
            "kind": "tool_perm",
            "payload": {"match": {"tool": "web_fetch"}, "decision": "deny"},
        },
        "firesAt": "before_tool_use",
        "action": "block",
    }


def _rule_llm(*, scope: str, id_: str, fires_at: str = "pre_final") -> dict[str, Any]:
    return {
        "id": id_,
        "scope": scope,
        "enabled": True,
        "what": {
            "kind": "llm_criterion",
            "payload": {"criterion": "be honest"},
        },
        "firesAt": fires_at,
        "action": "block",
    }


def _policy(*rules: dict[str, Any]) -> CustomizeVerificationPolicy:
    return CustomizeVerificationPolicy(custom_rules=tuple(rules))


# ---------------------------------------------------------------------------
# enabled_tool_perm_rules
# ---------------------------------------------------------------------------


def test_tool_perm_omitting_scope_returns_all_enabled_rules() -> None:
    """Backwards-compat: no ``current_scope`` ⇒ scope-blind, every enabled rule
    returned (existing behavior for legacy call sites)."""
    policy = _policy(
        _rule_tool_perm(scope="coding", id_="a"),
        _rule_tool_perm(scope="research", id_="b"),
        _rule_tool_perm(scope="always", id_="c"),
    )
    out = policy.enabled_tool_perm_rules()
    ids = {r["id"] for r in out}
    assert ids == {"a", "b", "c"}


def test_tool_perm_current_scope_filters_to_matching_rules() -> None:
    policy = _policy(
        _rule_tool_perm(scope="coding", id_="a"),
        _rule_tool_perm(scope="research", id_="b"),
        _rule_tool_perm(scope="always", id_="c"),
    )
    out = policy.enabled_tool_perm_rules(current_scope="research")
    ids = {r["id"] for r in out}
    # research-scope rule + always-scope rule (universal) both match.
    assert ids == {"b", "c"}


def test_tool_perm_current_scope_coding_only_returns_coding_plus_always() -> None:
    policy = _policy(
        _rule_tool_perm(scope="coding", id_="a"),
        _rule_tool_perm(scope="research", id_="b"),
        _rule_tool_perm(scope="always", id_="c"),
    )
    out = policy.enabled_tool_perm_rules(current_scope="coding")
    assert {r["id"] for r in out} == {"a", "c"}


def test_tool_perm_missing_rule_scope_treated_as_always() -> None:
    """A persisted rule without a ``scope`` (legacy / corrupt) does not vanish
    on a scope-aware call — it falls back to the universal ``always`` so old
    rules keep firing until the user edits them."""
    rule = _rule_tool_perm(scope="coding", id_="legacy")
    rule.pop("scope")
    policy = _policy(rule)
    out = policy.enabled_tool_perm_rules(current_scope="research")
    assert [r["id"] for r in out] == ["legacy"]


def test_tool_perm_unknown_scope_value_treated_as_always() -> None:
    """Defensive: a rule with a non-vocabulary scope (e.g. UI hand-edit) does
    not disappear; treat as ``always`` to avoid silent omission."""
    policy = _policy(_rule_tool_perm(scope="zzz-unknown", id_="x"))
    out = policy.enabled_tool_perm_rules(current_scope="coding")
    assert [r["id"] for r in out] == ["x"]


# ---------------------------------------------------------------------------
# enabled_llm_criterion_rules
# ---------------------------------------------------------------------------


def test_llm_criterion_omitting_scope_returns_all_enabled_rules_for_fires_at() -> None:
    policy = _policy(
        _rule_llm(scope="coding", id_="a"),
        _rule_llm(scope="research", id_="b"),
        _rule_llm(scope="always", id_="c"),
    )
    out = policy.enabled_llm_criterion_rules(fires_at="pre_final")
    assert {r["id"] for r in out} == {"a", "b", "c"}


def test_llm_criterion_current_scope_filters_to_matching_rules() -> None:
    policy = _policy(
        _rule_llm(scope="coding", id_="a"),
        _rule_llm(scope="research", id_="b"),
        _rule_llm(scope="always", id_="c"),
    )
    out = policy.enabled_llm_criterion_rules(
        fires_at="pre_final", current_scope="research"
    )
    assert {r["id"] for r in out} == {"b", "c"}


def test_llm_criterion_after_tool_use_independent_of_pre_final() -> None:
    """``current_scope`` filter is composed with the existing ``fires_at`` filter,
    not a replacement — a coding after_tool_use rule and a research pre_final
    rule do not collide."""
    policy = _policy(
        _rule_llm(scope="coding", id_="a", fires_at="after_tool_use"),
        _rule_llm(scope="coding", id_="b", fires_at="pre_final"),
    )
    after_out = policy.enabled_llm_criterion_rules(
        fires_at="after_tool_use", current_scope="coding"
    )
    pre_out = policy.enabled_llm_criterion_rules(
        fires_at="pre_final", current_scope="coding"
    )
    assert [r["id"] for r in after_out] == ["a"]
    assert [r["id"] for r in pre_out] == ["b"]


def test_llm_criterion_missing_rule_scope_treated_as_always() -> None:
    rule = _rule_llm(scope="coding", id_="legacy")
    rule.pop("scope")
    policy = _policy(rule)
    out = policy.enabled_llm_criterion_rules(
        fires_at="pre_final", current_scope="research"
    )
    assert [r["id"] for r in out] == ["legacy"]
