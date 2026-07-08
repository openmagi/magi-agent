"""U2 RED — flags and env readers for the deep-solve pipeline.

Covers:
  MAGI_DEEP_SOLVE_ENABLED          (_pb, profile default-ON)
  MAGI_DEEP_SOLVE_KILL_SWITCH      (raw allowlist, wins over enabled)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------


def test_deep_solve_flag_is_registered() -> None:
    """MAGI_DEEP_SOLVE_ENABLED must appear in the FLAGS registry."""
    from magi_agent.config.flags import FLAGS_BY_NAME

    assert "MAGI_DEEP_SOLVE_ENABLED" in FLAGS_BY_NAME


def test_deep_solve_flag_is_profile_bool() -> None:
    """MAGI_DEEP_SOLVE_ENABLED must be a profile_bool (_pb), not a strict bool."""
    from magi_agent.config.flags import FLAGS_BY_NAME

    spec = FLAGS_BY_NAME["MAGI_DEEP_SOLVE_ENABLED"]
    assert spec.kind == "profile_bool"


# ---------------------------------------------------------------------------
# is_deep_solve_enabled — profile-aware reader
# ---------------------------------------------------------------------------


def test_deep_solve_enabled_unset_profile_is_on() -> None:
    """Unset environment -> full profile -> ON."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({}) is True


def test_deep_solve_enabled_explicit_full_profile_is_on() -> None:
    """MAGI_RUNTIME_PROFILE=full -> ON."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_RUNTIME_PROFILE": "full"}) is True


def test_deep_solve_enabled_safe_profile_is_off() -> None:
    """MAGI_RUNTIME_PROFILE=safe -> OFF."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_RUNTIME_PROFILE": "safe"}) is False


def test_deep_solve_enabled_eval_profile_is_off() -> None:
    """MAGI_RUNTIME_PROFILE=eval -> OFF."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_RUNTIME_PROFILE": "eval"}) is False


def test_deep_solve_enabled_minimal_profile_is_off() -> None:
    """MAGI_RUNTIME_PROFILE=minimal -> OFF."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_RUNTIME_PROFILE": "minimal"}) is False


def test_deep_solve_enabled_conservative_profile_is_off() -> None:
    """MAGI_RUNTIME_PROFILE=conservative -> OFF."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_RUNTIME_PROFILE": "conservative"}) is False


def test_deep_solve_enabled_off_profile_is_off() -> None:
    """MAGI_RUNTIME_PROFILE=off -> OFF."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_RUNTIME_PROFILE": "off"}) is False


def test_deep_solve_enabled_explicit_zero_overrides_full_profile() -> None:
    """Explicit MAGI_DEEP_SOLVE_ENABLED=0 wins over profile (turns OFF)."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_ENABLED": "0"}) is False


def test_deep_solve_enabled_explicit_one_overrides_safe_profile() -> None:
    """Explicit MAGI_DEEP_SOLVE_ENABLED=1 wins over safe profile (turns ON)."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert (
        is_deep_solve_enabled(
            {"MAGI_RUNTIME_PROFILE": "safe", "MAGI_DEEP_SOLVE_ENABLED": "1"}
        )
        is True
    )


# ---------------------------------------------------------------------------
# Kill-switch — wins over enabled
# ---------------------------------------------------------------------------


def test_kill_switch_set_overrides_enabled() -> None:
    """MAGI_DEEP_SOLVE_KILL_SWITCH=1 disables even when the flag is ON."""
    from magi_agent.config.env import is_deep_solve_enabled

    # Full profile (ON by default) + kill-switch active -> OFF
    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": "1"}) is False


def test_kill_switch_true_string_overrides_enabled() -> None:
    """Kill-switch accepts truthy strings: 'true'."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": "true"}) is False


def test_kill_switch_yes_overrides_enabled() -> None:
    """Kill-switch accepts truthy strings: 'yes'."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": "yes"}) is False


def test_kill_switch_on_overrides_enabled() -> None:
    """Kill-switch accepts truthy strings: 'on'."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": "on"}) is False


def test_kill_switch_unset_has_no_effect() -> None:
    """Absent kill-switch -> no effect; full profile -> ON."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({}) is True


def test_kill_switch_zero_has_no_effect() -> None:
    """Kill-switch=0 is not truthy -> full profile -> ON."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": "0"}) is True


def test_kill_switch_empty_string_has_no_effect() -> None:
    """Kill-switch='' is not truthy -> full profile -> ON."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": ""}) is True


def test_kill_switch_case_insensitive() -> None:
    """Kill-switch matching is case-insensitive (strip().lower())."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": "TRUE"}) is False
    assert is_deep_solve_enabled({"MAGI_DEEP_SOLVE_KILL_SWITCH": "  1  "}) is False


def test_kill_switch_overrides_explicit_enabled_flag() -> None:
    """Kill-switch wins even when MAGI_DEEP_SOLVE_ENABLED=1 is set explicitly."""
    from magi_agent.config.env import is_deep_solve_enabled

    assert (
        is_deep_solve_enabled(
            {"MAGI_DEEP_SOLVE_ENABLED": "1", "MAGI_DEEP_SOLVE_KILL_SWITCH": "1"}
        )
        is False
    )
