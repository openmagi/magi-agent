from __future__ import annotations

from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def _make_policy(custom_rules: list) -> CustomizeVerificationPolicy:
    overrides = {"verification": {"custom_rules": custom_rules}}
    return CustomizeVerificationPolicy.from_overrides(overrides)


def test_enabled_shacl_rule_returns_rule_id_and_shape_ttl() -> None:
    """One enabled shacl_constraint rule → list contains its ruleId + shapeTtl."""
    policy = _make_policy(
        [
            {
                "id": "rule-001",
                "enabled": True,
                "what": {
                    "kind": "shacl_constraint",
                    "payload": {
                        "ruleId": "my-shape",
                        "shapeTtl": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
                    },
                },
            }
        ]
    )
    result = policy.enabled_shacl_rules()
    assert result == [{"ruleId": "my-shape", "shapeTtl": "@prefix sh: <http://www.w3.org/ns/shacl#> ."}]


def test_disabled_shacl_rule_excluded() -> None:
    """A disabled shacl rule → excluded."""
    policy = _make_policy(
        [
            {
                "id": "rule-002",
                "enabled": False,
                "what": {
                    "kind": "shacl_constraint",
                    "payload": {
                        "ruleId": "my-shape",
                        "shapeTtl": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
                    },
                },
            }
        ]
    )
    result = policy.enabled_shacl_rules()
    assert result == []


def test_deterministic_ref_rule_excluded() -> None:
    """A deterministic_ref rule → excluded (only shacl_constraint)."""
    policy = _make_policy(
        [
            {
                "id": "rule-003",
                "enabled": True,
                "what": {
                    "kind": "deterministic_ref",
                    "payload": {"ref": "some-ref"},
                },
            }
        ]
    )
    result = policy.enabled_shacl_rules()
    assert result == []


def test_missing_payload_rule_id_falls_back_to_rule_id() -> None:
    """Rule with no payload.ruleId → uses rule['id'] as ruleId."""
    policy = _make_policy(
        [
            {
                "id": "rule-004",
                "enabled": True,
                "what": {
                    "kind": "shacl_constraint",
                    "payload": {
                        "shapeTtl": "@prefix sh: <http://www.w3.org/ns/shacl#> .",
                    },
                },
            }
        ]
    )
    result = policy.enabled_shacl_rules()
    assert result == [{"ruleId": "rule-004", "shapeTtl": "@prefix sh: <http://www.w3.org/ns/shacl#> ."}]


def test_empty_and_malformed_custom_rules_returns_empty_no_exception() -> None:
    """Empty / malformed custom_rules → returns [] (no exception)."""
    # Empty list
    policy_empty = _make_policy([])
    assert policy_empty.enabled_shacl_rules() == []

    # Malformed: missing 'what' field
    policy_no_what = _make_policy([{"id": "rule-bad", "enabled": True}])
    assert policy_no_what.enabled_shacl_rules() == []

    # Malformed: payload is not a dict
    policy_bad_payload = _make_policy(
        [
            {
                "id": "rule-bad2",
                "enabled": True,
                "what": {"kind": "shacl_constraint", "payload": "not-a-dict"},
            }
        ]
    )
    assert policy_bad_payload.enabled_shacl_rules() == []

    # Malformed: missing shapeTtl in payload
    policy_no_ttl = _make_policy(
        [
            {
                "id": "rule-bad3",
                "enabled": True,
                "what": {"kind": "shacl_constraint", "payload": {"ruleId": "x"}},
            }
        ]
    )
    assert policy_no_ttl.enabled_shacl_rules() == []
