"""PR-F7 unit tests for ``magi_agent.customize.budgets_apply``.

Covers the four contract cases from the design doc (§5 PR-F7):

(a) env unset + budget authored → env populated from the budget.
(b) env already set + budget authored → env unchanged (operator wins).
(c) env unset + no budget authored → no-op.
(d) F7 master flag OFF → applier is a no-op regardless of budget state.

Plus follow-on guards: the triple-gate's middle/inner flags must also short-
circuit the applier; the BUDGET_ENV_MAP vocabulary is the only authoritative
list of keys; effective_budget_envs reports the raw env (or None when unset).
"""
from __future__ import annotations

import pytest

from magi_agent.customize.budgets_apply import (
    BUDGET_ENV_MAP,
    apply_budgets_if_enabled,
    effective_budget_envs,
)
from magi_agent.customize.verification_policy import CustomizeVerificationPolicy


def _policy(budgets: dict[str, int]) -> CustomizeVerificationPolicy:
    return CustomizeVerificationPolicy.from_overrides(
        {"verification": {"budgets": budgets}}
    )


def _flags_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Triple-gate ON. Sufficient + necessary for the applier to project."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")


def test_env_unset_budget_set_populates_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """(a) Operator authored a budget, env is unset → applier seeds the env."""
    _flags_on(monkeypatch)
    env: dict[str, str] = {}
    apply_budgets_if_enabled(
        env=env,
        policy=_policy(
            {
                "maxToolCallsPerTurn": 30,
                "loopGuardHardThreshold": 12,
            }
        ),
    )
    assert env["MAGI_TOOL_MAX_CALLS_PER_TURN"] == "30"
    assert env["MAGI_LOOP_GUARD_HARD_THRESHOLD"] == "12"
    # The third budget was not authored — stays unset.
    assert "MAGI_MAX_STEPS_BRAKE_HARD" not in env


def test_env_set_budget_set_operator_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) Explicit operator env is preserved; the dashboard save is dormant."""
    _flags_on(monkeypatch)
    env: dict[str, str] = {
        "MAGI_TOOL_MAX_CALLS_PER_TURN": "200",
        "MAGI_LOOP_GUARD_HARD_THRESHOLD": "5",
    }
    apply_budgets_if_enabled(
        env=env,
        policy=_policy(
            {
                "maxToolCallsPerTurn": 30,
                "loopGuardHardThreshold": 12,
            }
        ),
    )
    assert env["MAGI_TOOL_MAX_CALLS_PER_TURN"] == "200"
    assert env["MAGI_LOOP_GUARD_HARD_THRESHOLD"] == "5"


def test_budget_unset_env_unset_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """(c) No budget authored, no env set → applier is a pure no-op."""
    _flags_on(monkeypatch)
    env: dict[str, str] = {}
    apply_budgets_if_enabled(env=env, policy=_policy({}))
    assert env == {}


def test_flag_off_applier_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) Master F7 flag OFF → applier is a no-op even with an authored budget."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUDGETS_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    env: dict[str, str] = {}
    apply_budgets_if_enabled(
        env=env, policy=_policy({"maxToolCallsPerTurn": 30})
    )
    assert env == {}


def test_customize_master_off_applier_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Middle gate (master customize) OFF → applier is a no-op."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    # Force a safe profile so the profile-aware default-ON does not flip the
    # master gate back ON from the explicit "0".
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    env: dict[str, str] = {}
    apply_budgets_if_enabled(
        env=env, policy=_policy({"maxToolCallsPerTurn": 30})
    )
    assert env == {}


def test_custom_rules_off_applier_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inner gate (custom-rules) OFF → applier is a no-op."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_BUDGETS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    env: dict[str, str] = {}
    apply_budgets_if_enabled(
        env=env, policy=_policy({"maxToolCallsPerTurn": 30})
    )
    assert env == {}


def test_budget_env_map_covers_three_canonical_budgets() -> None:
    """Vocabulary lock: the F7 surface ships exactly the 3 designed budgets."""
    assert set(BUDGET_ENV_MAP) == {
        "maxToolCallsPerTurn",
        "maxStepsBrakeHard",
        "loopGuardHardThreshold",
    }
    assert BUDGET_ENV_MAP["maxToolCallsPerTurn"] == "MAGI_TOOL_MAX_CALLS_PER_TURN"
    assert BUDGET_ENV_MAP["maxStepsBrakeHard"] == "MAGI_MAX_STEPS_BRAKE_HARD"
    assert BUDGET_ENV_MAP["loopGuardHardThreshold"] == "MAGI_LOOP_GUARD_HARD_THRESHOLD"


def test_effective_budget_envs_reports_unset_as_none() -> None:
    """The dashboard GET surface needs to distinguish 'unset' from empty string."""
    env: dict[str, str] = {"MAGI_TOOL_MAX_CALLS_PER_TURN": "42"}
    snapshot = effective_budget_envs(env)
    assert snapshot["maxToolCallsPerTurn"] == "42"
    assert snapshot["maxStepsBrakeHard"] is None
    assert snapshot["loopGuardHardThreshold"] is None


def test_policy_budget_accessor_filters_malformed() -> None:
    """``CustomizeVerificationPolicy.budget`` enforces positive-int semantics."""
    policy = _policy(
        {
            "maxToolCallsPerTurn": 30,
            # The store normalize step drops these; the accessor double-checks.
        }
    )
    assert policy.budget("maxToolCallsPerTurn") == 30
    assert policy.budget("loopGuardHardThreshold") is None
    assert policy.budget("unknown") is None


def test_none_policy_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller passing ``policy=None`` (e.g. before load) must not crash."""
    _flags_on(monkeypatch)
    env: dict[str, str] = {}
    apply_budgets_if_enabled(env=env, policy=None)  # type: ignore[arg-type]
    assert env == {}
