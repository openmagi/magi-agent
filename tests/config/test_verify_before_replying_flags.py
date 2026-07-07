"""PR-V2 RED -- flags and env parsers for verify-before-replying policy.

Covers the three FlagSpec entries introduced by PR-V2:

  MAGI_VERIFY_BEFORE_REPLYING_ENABLED       (_pb, profile default-ON)
  MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED (_b, strict default-OFF)
  MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE  (str, shipped value = 'off')
"""
from __future__ import annotations

import logging


def test_master_flag_is_profile_bool() -> None:
    """MAGI_VERIFY_BEFORE_REPLYING_ENABLED is a _pb gate: ON under the full
    profile (unset profile OR MAGI_RUNTIME_PROFILE=full), OFF under safe/eval,
    and explicit 0 wins over any profile setting."""
    from magi_agent.config.env import parse_verify_before_replying_enabled

    # Unset profile -> full profile -> True
    assert parse_verify_before_replying_enabled({}) is True
    # Safe profile -> OFF
    assert parse_verify_before_replying_enabled({"MAGI_RUNTIME_PROFILE": "safe"}) is False
    # Eval profile -> OFF
    assert parse_verify_before_replying_enabled({"MAGI_RUNTIME_PROFILE": "eval"}) is False
    # Explicit flag-off wins
    assert (
        parse_verify_before_replying_enabled({"MAGI_VERIFY_BEFORE_REPLYING_ENABLED": "0"})
        is False
    )


def test_skeptic_flag_is_strict_default_off() -> None:
    """MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED is a _b (strict bool):
    OFF by default regardless of profile, ON only when explicitly set to 1."""
    from magi_agent.config.env import parse_verify_before_replying_skeptic_enabled

    # Default OFF -- no env
    assert parse_verify_before_replying_skeptic_enabled({}) is False
    # Profile 'full' does NOT auto-enable a _b flag
    assert (
        parse_verify_before_replying_skeptic_enabled({"MAGI_RUNTIME_PROFILE": "full"})
        is False
    )
    # Explicit 1 enables it
    assert (
        parse_verify_before_replying_skeptic_enabled(
            {"MAGI_VERIFY_BEFORE_REPLYING_SKEPTIC_ENABLED": "1"}
        )
        is True
    )


def test_backstop_mode_parses_off_and_warns_on_reserved_values(caplog) -> None:
    """MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE ships with only 'off' as a
    valid value.  'block_high' and 'repair_high' are reserved but not
    implemented; any non-off value (including reserved ones) must log a WARNING
    and return 'off'."""
    from magi_agent.config.env import parse_verify_before_replying_backstop_mode

    # Default (unset) -> "off" with no warning
    assert parse_verify_before_replying_backstop_mode({}) == "off"

    # Explicit "off" -> "off" with no warning
    assert (
        parse_verify_before_replying_backstop_mode(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE": "off"}
        )
        == "off"
    )

    # Reserved value "block_high" -> warns and falls back to "off"
    with caplog.at_level(logging.WARNING):
        result = parse_verify_before_replying_backstop_mode(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE": "block_high"}
        )
    assert result == "off"
    assert caplog.records, "Expected a WARNING for reserved value 'block_high'"

    caplog.clear()

    # Reserved value "repair_high" -> warns and falls back to "off"
    with caplog.at_level(logging.WARNING):
        result = parse_verify_before_replying_backstop_mode(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE": "repair_high"}
        )
    assert result == "off"
    assert caplog.records, "Expected a WARNING for reserved value 'repair_high'"

    caplog.clear()

    # Garbage value -> warns and falls back to "off"
    with caplog.at_level(logging.WARNING):
        result = parse_verify_before_replying_backstop_mode(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE": "garbage_value"}
        )
    assert result == "off"
    assert caplog.records, "Expected a WARNING for unknown value 'garbage_value'"
