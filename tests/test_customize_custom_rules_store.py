from __future__ import annotations

from pathlib import Path

from magi_agent.customize.store import delete_custom_rule, load_overrides, set_custom_rule
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def _rule(rid: str, ref: str = "evidence:test-run", enabled: bool = True) -> dict:
    return {
        "id": rid,
        "scope": "coding",
        "enabled": enabled,
        "what": {"kind": "deterministic_ref", "payload": {"ref": ref}},
        "firesAt": "pre_final",
        "action": "block",
    }


def test_set_custom_rule_appends_and_roundtrips(tmp_path: Path):
    p = tmp_path / "customize.json"
    set_custom_rule(_rule("cr_1"), path=p)
    out = load_overrides(p)
    rules = out["verification"]["custom_rules"]
    assert len(rules) == 1 and rules[0]["id"] == "cr_1"


def test_set_custom_rule_upserts_by_id(tmp_path: Path):
    p = tmp_path / "customize.json"
    set_custom_rule(_rule("cr_1", ref="evidence:test-run"), path=p)
    set_custom_rule(_rule("cr_1", ref="evidence:git-diff"), path=p)
    rules = load_overrides(p)["verification"]["custom_rules"]
    assert len(rules) == 1
    assert rules[0]["what"]["payload"]["ref"] == "evidence:git-diff"


def test_delete_custom_rule(tmp_path: Path):
    p = tmp_path / "customize.json"
    set_custom_rule(_rule("cr_1"), path=p)
    set_custom_rule(_rule("cr_2"), path=p)
    delete_custom_rule("cr_1", path=p)
    ids = [r["id"] for r in load_overrides(p)["verification"]["custom_rules"]]
    assert ids == ["cr_2"]


def test_policy_enabled_deterministic_refs():
    overrides = {
        "verification": {
            "custom_rules": [
                _rule("cr_1", ref="evidence:test-run", enabled=True),
                _rule("cr_2", ref="evidence:git-diff", enabled=False),  # disabled → skip
                {"id": "cr_3", "enabled": True, "what": {"kind": "tool_perm", "payload": {}}},  # not det → skip
            ]
        }
    }
    policy = CustomizeVerificationPolicy.from_overrides(overrides)
    assert policy.enabled_deterministic_refs() == ["evidence:test-run"]


def test_policy_enabled_llm_criterion_rules_by_firesat():
    overrides = {
        "verification": {
            "custom_rules": [
                {"id": "a", "enabled": True, "firesAt": "pre_final",
                 "what": {"kind": "llm_criterion", "payload": {"criterion": "x"}}},
                {"id": "b", "enabled": True, "firesAt": "after_tool_use",
                 "what": {"kind": "llm_criterion", "payload": {"criterion": "y"}}},
                {"id": "c", "enabled": False, "firesAt": "pre_final",
                 "what": {"kind": "llm_criterion", "payload": {"criterion": "z"}}},
            ]
        }
    }
    policy = CustomizeVerificationPolicy.from_overrides(overrides)
    pre = policy.enabled_llm_criterion_rules(fires_at="pre_final")
    assert [r["id"] for r in pre] == ["a"]
    after = policy.enabled_llm_criterion_rules(fires_at="after_tool_use")
    assert [r["id"] for r in after] == ["b"]
