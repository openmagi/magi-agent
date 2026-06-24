"""PR-F-UX6 — custom_rules ``groupId`` round-trip + grouped accessor + delete.

Covers the storage layer for hybrid persistence: N rules sharing a
``groupId`` are written via ``set_custom_rules_group`` and surfaced through
``CustomizeVerificationPolicy.enabled_custom_rules_grouped`` as one logical
bucket keyed by groupId (with ungrouped rules under ``None``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.custom_rules import validate_custom_rule
from magi_agent.customize.store import (
    delete_custom_rule_group,
    load_overrides,
    set_custom_rule,
    set_custom_rules_group,
)
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def _det_rule(rid: str, ref: str = "evidence:test-run", enabled: bool = True) -> dict:
    return {
        "id": rid,
        "scope": "coding",
        "enabled": enabled,
        "what": {"kind": "deterministic_ref", "payload": {"ref": ref}},
        "firesAt": "pre_final",
        "action": "block",
    }


# ---------------------------------------------------------------------------
# validate_custom_rule — groupId optional, non-empty str when present
# ---------------------------------------------------------------------------


def test_validate_accepts_rule_without_group_id() -> None:
    rule = _det_rule("cr_1")
    assert validate_custom_rule(rule) == []


def test_validate_accepts_rule_with_string_group_id() -> None:
    rule = {**_det_rule("cr_1"), "groupId": "grp_abc"}
    assert validate_custom_rule(rule) == []


def test_validate_rejects_empty_group_id() -> None:
    rule = {**_det_rule("cr_1"), "groupId": ""}
    errors = validate_custom_rule(rule)
    assert any("groupId" in e for e in errors)


def test_validate_rejects_whitespace_only_group_id() -> None:
    rule = {**_det_rule("cr_1"), "groupId": "   "}
    errors = validate_custom_rule(rule)
    assert any("groupId" in e for e in errors)


def test_validate_rejects_non_string_group_id() -> None:
    rule = {**_det_rule("cr_1"), "groupId": 123}
    errors = validate_custom_rule(rule)
    assert any("groupId" in e for e in errors)


# ---------------------------------------------------------------------------
# set_custom_rules_group — N rules share the groupId on disk
# ---------------------------------------------------------------------------


def test_set_custom_rules_group_writes_all_with_shared_group_id(
    tmp_path: Path,
) -> None:
    p = tmp_path / "customize.json"
    rules = [_det_rule("cr_a"), _det_rule("cr_b", ref="evidence:git-diff")]
    set_custom_rules_group(rules, "grp_hybrid_1", path=p)

    stored = load_overrides(p)["verification"]["custom_rules"]
    assert len(stored) == 2
    assert all(r.get("groupId") == "grp_hybrid_1" for r in stored)


def test_set_custom_rules_group_overwrites_existing_group_id(
    tmp_path: Path,
) -> None:
    p = tmp_path / "customize.json"
    pre = {**_det_rule("cr_a"), "groupId": "OLD"}
    set_custom_rules_group([pre], "grp_new", path=p)
    stored = load_overrides(p)["verification"]["custom_rules"]
    assert stored[0]["groupId"] == "grp_new"


def test_set_custom_rules_group_upserts_by_id(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    set_custom_rule(_det_rule("cr_a", ref="evidence:test-run"), path=p)
    set_custom_rules_group(
        [_det_rule("cr_a", ref="evidence:git-diff")],
        "grp_1",
        path=p,
    )
    stored = load_overrides(p)["verification"]["custom_rules"]
    assert len(stored) == 1
    assert stored[0]["what"]["payload"]["ref"] == "evidence:git-diff"
    assert stored[0]["groupId"] == "grp_1"


def test_set_custom_rules_group_rejects_blank_group_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        set_custom_rules_group([_det_rule("cr_a")], "", path=tmp_path / "x.json")


def test_set_custom_rules_group_rejects_empty_rules(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        set_custom_rules_group([], "grp", path=tmp_path / "x.json")


# ---------------------------------------------------------------------------
# delete_custom_rule_group — removes all rules with that groupId
# ---------------------------------------------------------------------------


def test_delete_custom_rule_group_removes_all_matching(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    set_custom_rules_group(
        [_det_rule("cr_a"), _det_rule("cr_b", ref="evidence:git-diff")],
        "grp_to_delete",
        path=p,
    )
    set_custom_rule(_det_rule("cr_solo"), path=p)
    delete_custom_rule_group("grp_to_delete", path=p)

    ids = [r["id"] for r in load_overrides(p)["verification"]["custom_rules"]]
    assert ids == ["cr_solo"]


def test_delete_custom_rule_group_noop_on_missing_id(tmp_path: Path) -> None:
    p = tmp_path / "customize.json"
    set_custom_rule(_det_rule("cr_a"), path=p)
    delete_custom_rule_group("never-existed", path=p)
    ids = [r["id"] for r in load_overrides(p)["verification"]["custom_rules"]]
    assert ids == ["cr_a"]


# ---------------------------------------------------------------------------
# enabled_custom_rules_grouped accessor
# ---------------------------------------------------------------------------


def test_enabled_custom_rules_grouped_buckets_by_group_id() -> None:
    overrides = {
        "verification": {
            "custom_rules": [
                {**_det_rule("a"), "groupId": "grp1"},
                {**_det_rule("b", ref="evidence:git-diff"), "groupId": "grp1"},
                {**_det_rule("c", ref="evidence:test-run"), "groupId": "grp2"},
                _det_rule("d", ref="evidence:test-run"),  # ungrouped
                {**_det_rule("e", enabled=False), "groupId": "grp1"},
            ]
        }
    }
    policy = CustomizeVerificationPolicy.from_overrides(overrides)
    grouped = policy.enabled_custom_rules_grouped()

    # Only enabled rules are returned.
    assert sorted(grouped.keys(), key=lambda k: (k is None, k or "")) == [
        "grp1",
        "grp2",
        None,
    ]
    assert [r["id"] for r in grouped["grp1"]] == ["a", "b"]
    assert [r["id"] for r in grouped["grp2"]] == ["c"]
    assert [r["id"] for r in grouped[None]] == ["d"]


def test_enabled_custom_rules_grouped_treats_malformed_group_id_as_ungrouped() -> None:
    overrides = {
        "verification": {
            "custom_rules": [
                {**_det_rule("a"), "groupId": 123},  # non-str → ungrouped
                {**_det_rule("b"), "groupId": "  "},  # whitespace → ungrouped
                {**_det_rule("c"), "groupId": "real"},
            ]
        }
    }
    policy = CustomizeVerificationPolicy.from_overrides(overrides)
    grouped = policy.enabled_custom_rules_grouped()
    assert sorted(grouped.keys(), key=lambda k: (k is None, k or "")) == [
        "real",
        None,
    ]
    assert sorted(r["id"] for r in grouped[None]) == ["a", "b"]
