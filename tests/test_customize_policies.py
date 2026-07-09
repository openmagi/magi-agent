"""Policy entity: typed model + customize.json CRUD + groupId migration.

Phase 1a of the policy-abstraction design (clawy
docs/plans/2026-07-03-policy-abstraction-and-organic-multi-rule-authoring-design.md).
Storage-layer only; no runtime consumption yet.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.customize.policies import (
    Policy,
    PolicyBinding,
    PolicyReview,
    delete_policy,
    get_policy,
    implicit_policy_for_rule,
    list_policies,
    migrate_groups_to_policies,
    upsert_policy,
)
from magi_agent.customize.store import load_overrides, save_overrides


def _path(tmp_path: Path) -> Path:
    return tmp_path / "customize.json"


def _policy(policy_id: str = "verify-source", **kw) -> Policy:
    return Policy.model_validate(
        {
            "id": policy_id,
            "displayName": kw.get("displayName", "Verify source before high-risk tool"),
            "intent": kw.get("intent", "require a credible source before a high-risk tool"),
            "ruleIds": kw.get("ruleIds", ["cr_producer", "cr_gate"]),
        }
    )


# --- empty / roundtrip / list / delete ---


def test_empty_store_has_only_builtin_policies(tmp_path: Path) -> None:
    p = _path(tmp_path)
    # An empty store surfaces exactly the first-party builtins, no user
    # policies (list_policies always includes the read-only builtins so the
    # Rules surface tells the truth about runtime-native policies).
    policies = list_policies(p)
    assert [pol.policy_id for pol in policies] == [
        "injection_guard",
        "source_citation",
        "system_safety",
        "verify_before_replying",
    ]
    assert all(pol.origin == "builtin" for pol in policies)
    assert get_policy("verify-source", p) is None


def test_upsert_get_roundtrip(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_policy(_policy(), p)
    got = get_policy("verify-source", p)
    assert got is not None
    assert got.policy_id == "verify-source"
    assert got.rule_ids == ("cr_producer", "cr_gate")
    assert got.intent.startswith("require a credible source")


def test_list_sorted_and_skips_malformed(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_policy(_policy("zeta"), p)
    upsert_policy(_policy("alpha"), p)
    # Hand-inject a malformed entry + a key/id mismatch.
    overrides = load_overrides(p)
    overrides["policies"]["broken"] = {"id": "broken"}  # missing displayName
    overrides["policies"]["mismatch"] = {"id": "other", "displayName": "X"}
    save_overrides(overrides, p)
    ids = [pol.policy_id for pol in list_policies(p)]
    # sorted, always-present builtins included, malformed + mismatch skipped
    assert ids == [
        "alpha",
        "injection_guard",
        "source_citation",
        "system_safety",
        "verify_before_replying",
        "zeta",
    ]


def test_delete(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_policy(_policy(), p)
    delete_policy("verify-source", p)
    assert get_policy("verify-source", p) is None
    delete_policy("verify-source", p)  # idempotent no-op


def test_upsert_updates_in_place(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_policy(_policy(ruleIds=["cr_a"]), p)
    upsert_policy(_policy(ruleIds=["cr_a", "cr_b"]), p)
    got = get_policy("verify-source", p)
    assert got is not None and got.rule_ids == ("cr_a", "cr_b")
    # In-place: one user policy (no duplicate) plus the always-present builtins.
    assert [pol.policy_id for pol in list_policies(p)] == [
        "injection_guard",
        "source_citation",
        "system_safety",
        "verify-source",
        "verify_before_replying",
    ]


# --- validation ---


def test_invalid_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate({"id": "Bad Id", "displayName": "x"})


def test_empty_display_name_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate({"id": "p", "displayName": "   "})


def test_rule_ids_deduped() -> None:
    pol = Policy.model_validate(
        {"id": "p", "displayName": "P", "ruleIds": ["cr_a", "cr_a", "cr_b"]}
    )
    assert pol.rule_ids == ("cr_a", "cr_b")


def test_rule_id_with_colon_rejected() -> None:
    # Member ids are bare custom-rule ids; the ``custom_rule:`` prefix is added
    # only when a mode references them, never stored here.
    with pytest.raises(ValidationError):
        Policy.model_validate({"id": "p", "displayName": "P", "ruleIds": ["custom_rule:cr_a"]})


def test_too_many_rule_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate(
            {"id": "p", "displayName": "P", "ruleIds": [f"cr_{i}" for i in range(65)]}
        )


def test_binding_roundtrip() -> None:
    pol = Policy.model_validate(
        {
            "id": "p",
            "displayName": "P",
            "ruleIds": ["cr_prod", "cr_gate"],
            "binding": {
                "producerRuleId": "cr_prod",
                "gateRuleId": "cr_gate",
                "evidenceType": "custom:SourceCredibility",
            },
        }
    )
    assert isinstance(pol.binding, PolicyBinding)
    assert pol.binding.evidence_type == "custom:SourceCredibility"


def test_review_verdict_validated() -> None:
    with pytest.raises(ValidationError):
        PolicyReview.model_validate({"verdict": "not-a-verdict"})
    assert PolicyReview.model_validate({"verdict": "unreviewed"}).verdict == "unreviewed"


def test_builtin_origin_cannot_be_upserted(tmp_path: Path) -> None:
    p = _path(tmp_path)
    builtin = Policy.model_validate(
        {"id": "b", "displayName": "B", "origin": "builtin"}
    )
    with pytest.raises(ValueError):
        upsert_policy(builtin, p)


def test_to_payload_omits_none_optionals() -> None:
    pol = _policy()
    payload = pol.to_payload()
    assert "binding" not in payload  # exclude_none
    assert "review" not in payload
    assert payload["id"] == "verify-source"


# --- store back-compat ---


def test_old_store_without_policies_key_normalizes(tmp_path: Path) -> None:
    p = _path(tmp_path)
    # A pre-policies customize.json: no top-level "policies".
    p.write_text(
        json.dumps({"verification": {"custom_rules": []}, "agent_modes": {}}),
        encoding="utf-8",
    )
    overrides = load_overrides(p)
    assert overrides["policies"] == {}  # default filled in, not dropped
    # No stored user policies; the surface still shows the first-party builtins.
    assert [pol.policy_id for pol in list_policies(p)] == [
        "injection_guard",
        "source_citation",
        "system_safety",
        "verify_before_replying",
    ]


def test_normalizer_drops_non_dict_policy_entries(tmp_path: Path) -> None:
    p = _path(tmp_path)
    p.write_text(
        json.dumps({"policies": {"ok": {"id": "ok", "displayName": "OK"}, "bad": 123}}),
        encoding="utf-8",
    )
    overrides = load_overrides(p)
    assert "ok" in overrides["policies"]
    assert "bad" not in overrides["policies"]


def test_saving_policies_preserves_other_keys(tmp_path: Path) -> None:
    p = _path(tmp_path)
    upsert_policy(_policy(), p)
    overrides = load_overrides(p)
    # Sibling collections still present + defaulted.
    assert "custom_rules" in overrides["verification"]
    assert "agent_modes" in overrides
    assert overrides["policies"]["verify-source"]["displayName"]


# --- implicit 1-rule policy (read-time) ---


def test_implicit_policy_for_rule() -> None:
    pol = implicit_policy_for_rule({"id": "cr_solo", "what": {}})
    assert pol is not None
    assert pol.rule_ids == ("cr_solo",)
    assert pol.display_name == "cr_solo"


def test_implicit_policy_none_for_bad_rule() -> None:
    assert implicit_policy_for_rule({"what": {}}) is None
    assert implicit_policy_for_rule({"id": "has:colon"}) is None


# --- groupId -> Policy migration ---


def _rule(rule_id: str, group_id: str | None = None) -> dict:
    rule: dict = {
        "id": rule_id,
        "scope": "always",
        "enabled": True,
        "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test"}},
        "firesAt": "pre_final",
        "action": "block",
    }
    if group_id is not None:
        rule["groupId"] = group_id
    return rule


def test_migrate_groups_creates_one_policy_per_group(tmp_path: Path) -> None:
    p = _path(tmp_path)
    save_overrides(
        {
            "verification": {
                "custom_rules": [
                    _rule("cr_1", "credible source"),
                    _rule("cr_2", "credible source"),
                    _rule("cr_3", "other group"),
                    _rule("cr_ungrouped"),  # no groupId -> not migrated
                ]
            }
        },
        p,
    )
    created = migrate_groups_to_policies(p)
    assert created == 2
    policies = list_policies(p)
    by_rules = {tuple(sorted(pol.rule_ids)): pol for pol in policies}
    assert ("cr_1", "cr_2") in by_rules
    assert ("cr_3",) in by_rules
    # Migrated policies are placeholder-intent + unreviewed.
    migrated = by_rules[("cr_1", "cr_2")]
    assert migrated.intent == ""
    assert migrated.review is not None and migrated.review.verdict == "unreviewed"
    # The ungrouped rule was not turned into a persisted policy.
    assert all("cr_ungrouped" not in pol.rule_ids for pol in policies)


def test_migrate_groups_is_idempotent(tmp_path: Path) -> None:
    p = _path(tmp_path)
    save_overrides(
        {"verification": {"custom_rules": [_rule("cr_1", "grp"), _rule("cr_2", "grp")]}},
        p,
    )
    # 'grp' is a single group -> exactly one policy; re-running creates nothing.
    # The surface = the one migrated policy + the always-present builtins (4 now).
    assert migrate_groups_to_policies(p) == 1
    assert len(list_policies(p)) == 5
    assert migrate_groups_to_policies(p) == 0
    assert len(list_policies(p)) == 5
