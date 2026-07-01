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
