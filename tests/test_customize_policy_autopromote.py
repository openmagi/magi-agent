"""PR-1 (policies-first surface unification): no more orphan rules.

Covers the auto-promote-on-save helper (U1), the idempotent read-time
migration for unreferenced rules (U2), and the policy-level enabled cascade
(U4) at the module level. The transport-route wiring is exercised in
``test_customize_policy_routes.py`` / ``test_customize_routes.py``.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.customize.policies import (
    ensure_policies_for_unreferenced_rules,
    get_policy,
    list_policies,
    promote_rule_to_policy,
    set_policy_enabled,
)
from magi_agent.customize.store import (
    load_overrides,
    save_overrides,
    set_custom_rule,
    set_seam_spec,
)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "customize.json"


def _rule(rule_id: str, *, group_id: str | None = None, enabled: bool = True) -> dict:
    rule: dict = {
        "id": rule_id,
        "scope": "always",
        "enabled": enabled,
        "what": {"kind": "deterministic_ref", "payload": {"ref": "evidence:test"}},
        "firesAt": "pre_final",
        "action": "block",
    }
    if group_id is not None:
        rule["groupId"] = group_id
    return rule


# --- U1: promote_rule_to_policy -------------------------------------------


def test_promote_creates_one_rule_policy(tmp_path: Path) -> None:
    p = _path(tmp_path)
    set_custom_rule(_rule("cr_solo"), path=p)
    pid = promote_rule_to_policy(_rule("cr_solo"), path=p)
    assert pid is not None
    pol = get_policy(pid, p)
    assert pol is not None
    assert pol.rule_ids == ("cr_solo",)
    assert pol.origin == "user"
    # displayName falls back to the rule id (custom rules carry no name field).
    assert pol.display_name == "cr_solo"


def test_promote_uses_supplied_display_name_and_intent(tmp_path: Path) -> None:
    p = _path(tmp_path)
    set_custom_rule(_rule("cr_named"), path=p)
    pid = promote_rule_to_policy(
        _rule("cr_named"),
        path=p,
        display_name="No secrets in output",
        intent="block any tool output that leaks an API key",
    )
    pol = get_policy(pid, p)
    assert pol is not None
    assert pol.display_name == "No secrets in output"
    assert pol.intent == "block any tool output that leaks an API key"


def test_promote_is_noop_when_rule_already_referenced(tmp_path: Path) -> None:
    p = _path(tmp_path)
    set_custom_rule(_rule("cr_bound"), path=p)
    # First promote creates the policy.
    first = promote_rule_to_policy(_rule("cr_bound"), path=p)
    assert first is not None
    before = {pol.policy_id for pol in list_policies(p)}
    # A second promote (e.g. an UPDATE to the same rule) must not create a
    # second policy for a rule already referenced by a Policy.
    second = promote_rule_to_policy(_rule("cr_bound"), path=p)
    assert second is None
    assert {pol.policy_id for pol in list_policies(p)} == before


def test_promote_collision_suffixes_the_id(tmp_path: Path) -> None:
    p = _path(tmp_path)
    # A user policy already owns the slug the rule id would derive to.
    from magi_agent.customize.policies import Policy, upsert_policy

    upsert_policy(
        Policy.model_validate(
            {"id": "cr-name", "displayName": "Pre-existing", "ruleIds": ["unrelated"]}
        ),
        p,
    )
    set_custom_rule(_rule("cr.name"), path=p)  # slugifies to "cr-name"
    pid = promote_rule_to_policy(_rule("cr.name"), path=p)
    assert pid is not None
    assert pid != "cr-name"  # collision-suffixed
    pol = get_policy(pid, p)
    assert pol is not None and pol.rule_ids == ("cr.name",)


# --- U2: ensure_policies_for_unreferenced_rules ---------------------------


def test_ensure_synthesizes_for_unreferenced_custom_rules(tmp_path: Path) -> None:
    p = _path(tmp_path)
    set_custom_rule(_rule("cr_x"), path=p)
    set_custom_rule(_rule("cr_y"), path=p)
    created = ensure_policies_for_unreferenced_rules(p)
    assert created == 2
    referenced = set()
    for pol in list_policies(p):
        referenced.update(pol.rule_ids)
    assert {"cr_x", "cr_y"} <= referenced


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    p = _path(tmp_path)
    set_custom_rule(_rule("cr_x"), path=p)
    assert ensure_policies_for_unreferenced_rules(p) == 1
    # Running twice creates nothing new.
    assert ensure_policies_for_unreferenced_rules(p) == 0


def test_ensure_skips_rules_already_in_a_policy(tmp_path: Path) -> None:
    p = _path(tmp_path)
    from magi_agent.customize.policies import Policy, upsert_policy

    set_custom_rule(_rule("cr_a"), path=p)
    set_custom_rule(_rule("cr_b"), path=p)
    # cr_a is a member of a multi-rule policy; only cr_b should be synthesized.
    upsert_policy(
        Policy.model_validate(
            {"id": "multi", "displayName": "Multi", "ruleIds": ["cr_a", "cr_other"]}
        ),
        p,
    )
    created = ensure_policies_for_unreferenced_rules(p)
    assert created == 1
    # cr_a stays inside "multi"; no top-level single policy was minted for it.
    singles = [pol for pol in list_policies(p) if pol.rule_ids == ("cr_a",)]
    assert singles == []


def test_ensure_respects_group_migration_first(tmp_path: Path) -> None:
    p = _path(tmp_path)
    # Two grouped rules must land in ONE multi-rule policy, not two singles.
    save_overrides(
        {
            "verification": {
                "custom_rules": [
                    _rule("cr_1", group_id="credible source"),
                    _rule("cr_2", group_id="credible source"),
                ]
            }
        },
        p,
    )
    created = ensure_policies_for_unreferenced_rules(p)
    # Exactly one policy for the group; no leftover singles.
    grouped = [
        pol for pol in list_policies(p) if set(pol.rule_ids) == {"cr_1", "cr_2"}
    ]
    assert len(grouped) == 1
    singles = [
        pol
        for pol in list_policies(p)
        if pol.origin == "user" and len(pol.rule_ids) == 1
    ]
    assert singles == []
    assert created == 1


def test_ensure_covers_seam_specs(tmp_path: Path) -> None:
    p = _path(tmp_path)
    set_seam_spec({"id": "seam_1", "spec_version": "1", "actions": []}, path=p)
    created = ensure_policies_for_unreferenced_rules(p)
    assert created == 1
    referenced = set()
    for pol in list_policies(p):
        referenced.update(pol.rule_ids)
    assert "seam_1" in referenced


# --- U4: set_policy_enabled cascade ---------------------------------------


def test_set_policy_enabled_cascades_to_member_rules(tmp_path: Path) -> None:
    p = _path(tmp_path)
    set_custom_rule(_rule("cr_a", enabled=True), path=p)
    set_custom_rule(_rule("cr_b", enabled=True), path=p)
    from magi_agent.customize.policies import Policy, upsert_policy

    upsert_policy(
        Policy.model_validate(
            {"id": "grp", "displayName": "Group", "ruleIds": ["cr_a", "cr_b"]}
        ),
        p,
    )
    set_policy_enabled("grp", False, path=p)
    rules = load_overrides(p)["verification"]["custom_rules"]
    by_id = {r["id"]: r for r in rules}
    assert by_id["cr_a"]["enabled"] is False
    assert by_id["cr_b"]["enabled"] is False
    # Re-enable flips them back.
    set_policy_enabled("grp", True, path=p)
    rules = load_overrides(p)["verification"]["custom_rules"]
    by_id = {r["id"]: r for r in rules}
    assert by_id["cr_a"]["enabled"] is True
    assert by_id["cr_b"]["enabled"] is True


def test_set_policy_enabled_unknown_policy_raises(tmp_path: Path) -> None:
    p = _path(tmp_path)
    import pytest

    with pytest.raises(KeyError):
        set_policy_enabled("nope", False, path=p)


def test_set_policy_enabled_builtin_rejected(tmp_path: Path) -> None:
    p = _path(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        set_policy_enabled("source_citation", False, path=p)
