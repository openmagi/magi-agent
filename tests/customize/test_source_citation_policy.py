"""Wave 4b Piece C: the first-party source_citation policy.

The citation feature is expressed as one read-only first-party policy in the
floor evidence pack, composed of four member rules, mode-scopable via
``policy:source_citation``. No em-dashes anywhere in this file per the citation
feature style rule.
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
    "source_citation.capture",
    "source_citation.render",
    "source_citation.gate",
    "source_citation.claim_coverage",
)


def test_builtin_is_registered() -> None:
    ids = {policy.policy_id for policy in BUILTIN_POLICIES}
    assert "source_citation" in ids


def test_policy_appears_in_surface(tmp_path) -> None:
    path = tmp_path / "customize.json"
    policies = {policy.policy_id: policy for policy in list_policies(path)}
    assert "source_citation" in policies
    policy = policies["source_citation"]
    assert policy.origin == "builtin"
    assert policy.rule_ids == _MEMBERS


def test_policy_carries_source_inspection_binding(tmp_path) -> None:
    policy = get_policy("source_citation", tmp_path / "customize.json")
    assert policy is not None
    assert policy.binding is not None
    assert policy.binding.producer_rule_id == "source_citation.capture"
    assert policy.binding.evidence_type == "SourceInspection"


def test_builtin_is_read_only() -> None:
    policy = get_policy("source_citation")
    assert policy is not None
    with pytest.raises(ValueError):
        upsert_policy(policy)


def test_policy_is_mode_scopable(tmp_path) -> None:
    # A mode can reference policy:source_citation; the resolver fans it out to
    # its member rules rather than dropping it as unknown.
    policies_map = scoped_policies_ruleids(tmp_path / "customize.json")
    assert policies_map["source_citation"] == _MEMBERS
    overlay = resolve_scoped_policy_overlay(
        ["policy:source_citation"],
        custom_rules=(),
        dashboard_check_ids=(),
        policies=policies_map,
    )
    # The ref resolved (fanned out); it is NOT recorded as an unknown policy.
    assert not any(
        ref == "policy:source_citation" and reason == "unknown"
        for ref, reason in overlay.dropped
    )


def test_floor_builtin_id_cannot_be_shadowed(tmp_path) -> None:
    # source_citation is a FLOOR builtin (its gate can BLOCK): U1's honesty fix
    # makes upsert_policy reject a user policy that reuses a floor id, so a floor
    # can never be silently shadowed by a display clone.
    path = tmp_path / "customize.json"
    with pytest.raises(ValueError, match="floor"):
        upsert_policy(
            Policy.model_validate(
                {
                    "id": "source_citation",
                    "displayName": "My Citation Clone",
                    "ruleIds": ["source_citation.capture"],
                }
            ),
            path,
        )
    # The builtin remains the source_citation card (no user shadow was stored).
    policy = get_policy("source_citation", path)
    assert policy is not None
    assert policy.origin == "builtin"


def test_non_floor_builtin_id_can_be_shadowed(tmp_path) -> None:
    # A NON-floor builtin (verify_before_replying is a nudge, user-disableable)
    # is a strong default rather than a floor, so a stored user policy that
    # reuses its id still wins (the original shadow guarantee, preserved for the
    # ids where it is legitimate).
    path = tmp_path / "customize.json"
    upsert_policy(
        Policy.model_validate(
            {
                "id": "verify_before_replying",
                "displayName": "My Verify Clone",
                "ruleIds": ["verify_before_replying.claim_citation"],
            }
        ),
        path,
    )
    policy = get_policy("verify_before_replying", path)
    assert policy is not None
    # The stored clone shadows the builtin (user clone wins).
    assert policy.origin == "user"
    assert policy.display_name == "My Verify Clone"
