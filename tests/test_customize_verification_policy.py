from __future__ import annotations

from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def test_policy_from_overrides_lists_enabled_with_modes() -> None:
    overrides = {
        "verification": {
            "harness_presets": ["answer-quality", "coding-verification"],
            "recipes": ["research"],
            "hooks": {"beforeCommit": True, "afterToolCall": False},
            "modes": {"answer-quality": "hybrid"},
            "custom_rules": [],
        }
    }
    policy = CustomizeVerificationPolicy.from_overrides(overrides)
    assert policy.is_enabled("answer-quality")
    assert policy.is_enabled("coding-verification")
    assert not policy.is_enabled("fact-grounding")
    assert policy.mode("answer-quality") == "hybrid"
    assert policy.mode("coding-verification") == "deterministic"  # default
    assert policy.enabled_recipes == frozenset({"research"})
    assert policy.enabled_hooks == frozenset({"beforeCommit"})  # only truthy


def test_policy_from_empty_overrides() -> None:
    policy = CustomizeVerificationPolicy.from_overrides({})
    assert policy.enabled_presets == frozenset()
    assert policy.mode("anything") == "deterministic"
    assert policy.user_rules == ""


def test_policy_ignores_malformed_entries() -> None:
    policy = CustomizeVerificationPolicy.from_overrides(
        {"verification": {"harness_presets": ["ok", 7, None], "modes": {"ok": 3}}}
    )
    assert policy.is_enabled("ok")
    # non-string preset ids dropped; non-string mode ignored → default
    assert policy.mode("ok") == "deterministic"


def test_policy_reads_user_rules() -> None:
    policy = CustomizeVerificationPolicy.from_overrides({"user_rules": "Always cite."})
    assert policy.user_rules == "Always cite."


def test_resolve_enabled_tri_state() -> None:
    policy = CustomizeVerificationPolicy.from_overrides(
        {"verification": {"preset_overrides": {"coding-verification": False}}}
    )
    # explicit False overrides a default-on gate (opt-out)
    assert policy.resolve_enabled("coding-verification", default=True) is False
    assert policy.explicit_preset("coding-verification") is False
    # unset → falls back to default
    assert policy.resolve_enabled("answer-quality", default=True) is True
    assert policy.resolve_enabled("answer-quality", default=False) is False
    assert policy.explicit_preset("answer-quality") is None
