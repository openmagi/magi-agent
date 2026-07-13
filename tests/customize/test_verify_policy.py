"""PR-V6: the first-party verify_before_replying policy.

The verify-before-replying feature is expressed as one read-only first-party
policy in the floor evidence pack, composed of six member rules, mode-scopable
via ``policy:verify_before_replying``. The policy carries no PolicyBinding
(design Section 11 note 3: verify findings must never satisfy an evidence gate).
No em-dashes anywhere in this file per the citation feature style rule.
"""
from __future__ import annotations

import pytest

from magi_agent.customize.policies import (
    BUILTIN_POLICIES,
    Policy,
    get_policy,
    list_policies,
    upsert_policy,
)
from magi_agent.customize.scoped_policy import (
    resolve_scoped_policy_overlay,
    scoped_policies_ruleids,
)

_MEMBERS = (
    "verify_before_replying.claim_citation",
    "verify_before_replying.evidence_consistency",
    "verify_before_replying.activity_grounding",
    "verify_before_replying.execution_claims",
    "verify_before_replying.sycophancy_heuristics",
    "verify_before_replying.skeptic_review",
)


def test_builtin_policy_present_and_shaped(tmp_path) -> None:
    """list_policies() contains verify_before_replying with the member rule ids,
    origin == 'builtin', and binding is None (design Section 11 note 3: verify
    findings must never satisfy an evidence gate). Membership is asserted
    dynamically (>= 6 members, new execution_claims rule present) rather than a
    frozen equality so adding future member rules does not require editing this
    assertion."""
    path = tmp_path / "customize.json"
    policies = {p.policy_id: p for p in list_policies(path)}
    assert "verify_before_replying" in policies
    policy = policies["verify_before_replying"]
    assert policy.origin == "builtin"
    assert set(_MEMBERS).issubset(set(policy.rule_ids))
    assert "verify_before_replying.execution_claims" in policy.rule_ids
    assert len(policy.rule_ids) >= 6
    assert policy.binding is None


def test_builtin_policy_is_read_only(tmp_path) -> None:
    """upsert_policy rejects the builtin origin; a user clone under the same id
    shadows the builtin per the list_policies contract (:242-260)."""
    policy = get_policy("verify_before_replying")
    assert policy is not None
    # Upserting a builtin-origin policy raises ValueError.
    with pytest.raises(ValueError):
        upsert_policy(policy)
    # A user clone (origin defaults to 'user') shadows the builtin.
    path = tmp_path / "customize.json"
    upsert_policy(
        Policy.model_validate(
            {
                "id": "verify_before_replying",
                "displayName": "My Verify Clone",
                "ruleIds": [_MEMBERS[0]],
            }
        ),
        path,
    )
    cloned = get_policy("verify_before_replying", path)
    assert cloned is not None
    # The stored clone shadows the builtin (user clone wins).
    assert cloned.origin == "user"
    assert cloned.display_name == "My Verify Clone"


def test_policy_ref_expands_for_mode_scoping(tmp_path) -> None:
    """policy:verify_before_replying expands to the member refs through the
    scoped_policy resolver exactly as policy:source_citation does."""
    policies_map = scoped_policies_ruleids(tmp_path / "customize.json")
    assert policies_map["verify_before_replying"] == _MEMBERS
    overlay = resolve_scoped_policy_overlay(
        ["policy:verify_before_replying"],
        custom_rules=(),
        dashboard_check_ids=(),
        policies=policies_map,
    )
    # The ref resolved (fanned out); it is NOT recorded as an unknown policy.
    assert not any(
        ref == "policy:verify_before_replying" and reason == "unknown"
        for ref, reason in overlay.dropped
    )
