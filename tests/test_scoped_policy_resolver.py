"""Tests for the mode scoped_policy_ids resolver (PR-D1, inert)."""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.modes import AgentMode, set_active_mode, upsert_mode
from magi_agent.customize.scoped_policy import (
    ScopedPolicyOverlay,
    active_scoped_policy_ids,
    resolve_scoped_policy_overlay,
)
from magi_agent.runtime.per_turn_agent_mode_context import (
    reset_per_turn_agent_mode,
    set_per_turn_agent_mode,
)

_KNOWN_REF = "evidence:test-run"  # a known WHAT-menu ref (see is_known_ref)


def _det_rule(rule_id: str, *, ref: str = _KNOWN_REF, enabled: bool = True) -> dict:
    return {
        "id": rule_id,
        "enabled": enabled,
        "what": {"kind": "deterministic_ref", "payload": {"ref": ref}},
        "firesAt": "pre_final",
        "action": "block",
    }


def _tool_rule(rule_id: str) -> dict:
    return {
        "id": rule_id,
        "enabled": True,
        "what": {"kind": "tool_perm", "payload": {"match": {"domain": "x"}}},
        "firesAt": "before_tool_use",
        "action": "block",
    }


def _llm_rule(rule_id: str) -> dict:
    return {
        "id": rule_id,
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": "cited"}},
        "firesAt": "pre_final",
        "action": "block",
    }


def _resolve(ids, *, rules=(), checks=()):
    return resolve_scoped_policy_overlay(
        ids, custom_rules=list(rules), dashboard_check_ids=set(checks)
    )


# --- pure resolver -----------------------------------------------------------


def test_empty_ids_empty_overlay():
    overlay = _resolve([])
    assert overlay.is_empty and overlay.dropped == ()


def test_custom_rule_deterministic_ref_to_prefinal():
    overlay = _resolve(["custom_rule:cr1"], rules=[_det_rule("cr1")])
    assert overlay.prefinal_validator_refs == (_KNOWN_REF,)
    assert not overlay.tool_perm_rule_ids and not overlay.dropped


def test_custom_rule_deterministic_ref_unknown_ref_dropped():
    overlay = _resolve(["custom_rule:cr1"], rules=[_det_rule("cr1", ref="evidence:nope")])
    assert overlay.prefinal_validator_refs == ()
    assert overlay.dropped == (("custom_rule:cr1", "unknown_ref"),)


def test_custom_rule_tool_perm_to_tool_bucket():
    overlay = _resolve(["custom_rule:tp1"], rules=[_tool_rule("tp1")])
    assert overlay.tool_perm_rule_ids == ("tp1",)


def test_custom_rule_unsupported_kind_dropped():
    overlay = _resolve(["custom_rule:l1"], rules=[_llm_rule("l1")])
    assert overlay.dropped == (("custom_rule:l1", "unsupported_kind:llm_criterion"),)


def test_custom_rule_unknown_id_dropped():
    overlay = _resolve(["custom_rule:ghost"], rules=[_det_rule("cr1")])
    assert overlay.dropped == (("custom_rule:ghost", "unknown"),)


def test_force_include_ignores_enabled_flag():
    # A globally-disabled rule still resolves — scoping force-activates it.
    overlay = _resolve(["custom_rule:cr1"], rules=[_det_rule("cr1", enabled=False)])
    assert overlay.prefinal_validator_refs == (_KNOWN_REF,)


def test_dashboard_check_known_and_unknown():
    overlay = _resolve(
        ["dashboard_check:ok", "dashboard_check:missing"], checks=["ok"]
    )
    assert overlay.dashboard_check_ids == ("ok",)
    assert overlay.dropped == (("dashboard_check:missing", "unknown"),)


def test_seam_spec_and_verifier_deferred():
    overlay = _resolve(["seam_spec:s1", "verifier:sourceOpened@1"])
    assert overlay.is_empty
    assert overlay.dropped == (
        ("seam_spec:s1", "deferred"),
        ("verifier:sourceOpened@1", "deferred"),
    )


def test_unknown_namespace_and_malformed():
    overlay = _resolve(["foo:bar", "nocolon", "custom_rule:", ":x"])
    reasons = dict(overlay.dropped)
    assert reasons["foo:bar"] == "unsupported_namespace"
    assert reasons["nocolon"] == "malformed"
    assert reasons["custom_rule:"] == "malformed"
    assert reasons[":x"] == "malformed"


def test_dedupe_across_and_within_buckets():
    overlay = _resolve(
        ["custom_rule:cr1", "custom_rule:cr1", "custom_rule:cr2"],
        rules=[_det_rule("cr1"), _det_rule("cr2")],
    )
    # cr1 + cr2 both map to the same _KNOWN_REF → deduped to one entry.
    assert overlay.prefinal_validator_refs == (_KNOWN_REF,)


def test_is_empty_property():
    assert ScopedPolicyOverlay().is_empty
    assert not ScopedPolicyOverlay(tool_perm_rule_ids=("x",)).is_empty


# --- active-mode accessor ----------------------------------------------------


@pytest.fixture
def customize_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))


def _mode(mode_id: str, scoped: list[str]) -> AgentMode:
    return AgentMode.model_validate(
        {"id": mode_id, "displayName": mode_id.title(), "scopedPolicyIds": scoped}
    )


def test_active_scoped_ids_no_mode(customize_env: None) -> None:
    assert active_scoped_policy_ids() == ()


def test_active_scoped_ids_from_active_mode(customize_env: None) -> None:
    upsert_mode(_mode("research", ["custom_rule:cite", "dashboard_check:x"]))
    set_active_mode("research")
    assert active_scoped_policy_ids() == ("custom_rule:cite", "dashboard_check:x")


def test_active_scoped_ids_per_turn_override_wins(customize_env: None) -> None:
    upsert_mode(_mode("plain", []))
    upsert_mode(_mode("research", ["custom_rule:cite"]))
    set_active_mode("plain")
    token = set_per_turn_agent_mode("research")
    try:
        assert active_scoped_policy_ids() == ("custom_rule:cite",)
    finally:
        reset_per_turn_agent_mode(token)


def test_active_scoped_ids_unknown_mode_empty(customize_env: None) -> None:
    upsert_mode(_mode("research", ["custom_rule:cite"]))
    set_active_mode("research")
    token = set_per_turn_agent_mode("nonexistent")
    try:
        assert active_scoped_policy_ids() == ()
    finally:
        reset_per_turn_agent_mode(token)


# --- defensive / edge coverage (review follow-ups) ---------------------------


def test_malformed_rule_dicts_drop_safely():
    # Every malformed shape drops (never raises) with nothing reaching prefinal.
    # Missing/non-dict `what` has no kind → unsupported_kind:None; a
    # deterministic_ref with a missing/non-str ref → unknown_ref.
    rules = [
        {"id": "no_what"},  # missing what
        {"id": "bad_what", "what": "not-a-dict"},  # non-dict what
        {"id": "no_ref", "what": {"kind": "deterministic_ref", "payload": {}}},
        {"id": "bad_ref", "what": {"kind": "deterministic_ref", "payload": {"ref": 123}}},
    ]
    overlay = _resolve([f"custom_rule:{r['id']}" for r in rules], rules=rules)
    assert overlay.prefinal_validator_refs == ()
    reasons = {sid: reason for sid, reason in overlay.dropped}
    assert reasons["custom_rule:no_what"] == "unsupported_kind:None"
    assert reasons["custom_rule:bad_what"] == "unsupported_kind:None"
    assert reasons["custom_rule:no_ref"] == "unknown_ref"
    assert reasons["custom_rule:bad_ref"] == "unknown_ref"


def test_custom_rule_id_containing_colon_resolves():
    # _POLICY_RE permits ':' in an id; partition keeps the second colon in local.
    rule = _det_rule("ns:cr1")
    overlay = _resolve(["custom_rule:ns:cr1"], rules=[rule])
    assert overlay.prefinal_validator_refs == (_KNOWN_REF,)


def test_non_string_entry_recorded_malformed():
    overlay = _resolve([123, None], rules=[])  # type: ignore[list-item]
    reasons = {sid: reason for sid, reason in overlay.dropped}
    assert reasons.get("123") == "malformed" and reasons.get("None") == "malformed"


def test_duplicate_rule_ids_keep_first():
    rules = [_det_rule("cr1", ref=_KNOWN_REF), _tool_rule("cr1")]  # dup id
    overlay = _resolve(["custom_rule:cr1"], rules=rules)
    # first-wins: the deterministic_ref rule, not the tool_perm one.
    assert overlay.prefinal_validator_refs == (_KNOWN_REF,)
    assert overlay.tool_perm_rule_ids == ()


def test_dashboard_and_tool_perm_dedupe_within_bucket():
    overlay = _resolve(
        ["dashboard_check:a", "dashboard_check:a", "custom_rule:tp", "custom_rule:tp"],
        rules=[_tool_rule("tp")],
        checks=["a"],
    )
    assert overlay.dashboard_check_ids == ("a",)
    assert overlay.tool_perm_rule_ids == ("tp",)


# --- PR-D2 pre-final consumption bridge (scoped_prefinal_validator_refs) ------

from magi_agent.customize.scoped_policy import scoped_prefinal_validator_refs  # noqa: E402
from magi_agent.customize.store import set_custom_rule  # noqa: E402


def _enable_customize_flags(mp: pytest.MonkeyPatch) -> None:
    mp.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    mp.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")


def test_scoped_prefinal_flags_off_is_empty(
    customize_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # These customize flags are profile-aware default-ON; an explicit "0" wins.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    set_custom_rule(_det_rule("cr1", enabled=False))
    upsert_mode(_mode("research", ["custom_rule:cr1"]))
    set_active_mode("research")
    assert scoped_prefinal_validator_refs() == ()


def test_scoped_prefinal_force_includes_disabled_rule_ref(
    customize_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_customize_flags(monkeypatch)
    # Globally DISABLED deterministic_ref rule — scoping force-activates it.
    set_custom_rule(_det_rule("cr1", enabled=False))
    upsert_mode(_mode("research", ["custom_rule:cr1"]))
    set_active_mode("research")
    assert scoped_prefinal_validator_refs() == (_KNOWN_REF,)


def test_scoped_prefinal_no_active_mode_is_empty(
    customize_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_customize_flags(monkeypatch)
    set_custom_rule(_det_rule("cr1"))
    assert scoped_prefinal_validator_refs() == ()


def test_scoped_prefinal_only_custom_rules_flag_off_is_empty(
    customize_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    set_custom_rule(_det_rule("cr1", enabled=False))
    upsert_mode(_mode("research", ["custom_rule:cr1"]))
    set_active_mode("research")
    assert scoped_prefinal_validator_refs() == ()


# --- PR-D3 tool-time consumption (tool_perm force-include) --------------------

from magi_agent.customize.tool_perm import matched_decision  # noqa: E402
from magi_agent.customize.verification_policy import (  # noqa: E402
    CustomizeVerificationPolicy,
)


def _tool_perm_rule(rule_id: str, *, tool: str, enabled: bool, scope: str = "always") -> dict:
    return {
        "id": rule_id,
        "scope": scope,
        "enabled": enabled,
        "what": {"kind": "tool_perm", "payload": {"match": {"tool": tool}, "decision": "deny"}},
        "firesAt": "before_tool_use",
        "action": "block",
    }


def test_enabled_tool_perm_rules_force_include_disabled():
    policy = CustomizeVerificationPolicy.from_overrides(
        {"verification": {"custom_rules": [_tool_perm_rule("tp1", tool="Danger", enabled=False)]}}
    )
    assert policy.enabled_tool_perm_rules() == []  # disabled → absent
    forced = policy.enabled_tool_perm_rules(force_include_ids={"tp1"})
    assert [r["id"] for r in forced] == ["tp1"]  # force-included


def test_enabled_tool_perm_rules_force_include_still_scope_filtered():
    policy = CustomizeVerificationPolicy.from_overrides(
        {
            "verification": {
                "custom_rules": [
                    _tool_perm_rule("tp1", tool="Danger", enabled=False, scope="coding")
                ]
            }
        }
    )
    # forced but scope=coding → filtered out on a research turn
    assert policy.enabled_tool_perm_rules(current_scope="research", force_include_ids={"tp1"}) == []
    assert [
        r["id"]
        for r in policy.enabled_tool_perm_rules(current_scope="coding", force_include_ids={"tp1"})
    ] == ["tp1"]


def test_matched_decision_force_includes_scoped_disabled_rule(
    customize_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    set_custom_rule(_tool_perm_rule("tp1", tool="Danger", enabled=False))  # globally off
    # No mode → disabled rule stays inert.
    assert matched_decision(tool_name="Danger", arguments={}) is None
    # Mode scoping the rule → force-active → deny.
    upsert_mode(_mode("locked", ["custom_rule:tp1"]))
    set_active_mode("locked")
    assert matched_decision(tool_name="Danger", arguments={}) == ("deny", "tp1")


def test_matched_decision_scoped_flag_off_is_inert(
    customize_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")  # explicit off
    set_custom_rule(_tool_perm_rule("tp1", tool="Danger", enabled=False))
    upsert_mode(_mode("locked", ["custom_rule:tp1"]))
    set_active_mode("locked")
    assert matched_decision(tool_name="Danger", arguments={}) is None


def test_forced_rule_never_shadows_enabled_rule():
    # An enabled deny rule ordered AFTER a force-included ask rule (in authoring
    # order) must still win: force-include is strictly additive, never a
    # downgrade. enabled candidates are ordered before forced-only ones.
    ask_rule = {
        "id": "forced_ask",
        "scope": "always",
        "enabled": False,
        "what": {"kind": "tool_perm", "payload": {"match": {"tool": "T"}, "decision": "ask"}},
        "firesAt": "before_tool_use",
        "action": "block",
    }
    deny_rule = _tool_perm_rule("enabled_deny", tool="T", enabled=True)
    policy = CustomizeVerificationPolicy.from_overrides(
        {"verification": {"custom_rules": [ask_rule, deny_rule]}}  # ask authored first
    )
    ordered = policy.enabled_tool_perm_rules(force_include_ids={"forced_ask"})
    # enabled deny comes first despite being authored second → first-match = deny.
    assert [r["id"] for r in ordered] == ["enabled_deny", "forced_ask"]


def test_enabled_tool_perm_rules_byte_identical_without_forced():
    # Empty force_include_ids → identical to the historic enabled-only list.
    rules = [
        _tool_perm_rule("a", tool="A", enabled=True),
        _tool_perm_rule("b", tool="B", enabled=False),
    ]
    policy = CustomizeVerificationPolicy.from_overrides({"verification": {"custom_rules": rules}})
    assert [r["id"] for r in policy.enabled_tool_perm_rules()] == ["a"]
