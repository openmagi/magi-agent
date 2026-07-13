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


def test_backstop_mode_parses_off_repair_high_block_high(caplog) -> None:
    """MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE accepts 'off' (default),
    'repair_high', and 'block_high' (WS-B implemented the seam). Any unknown
    value logs a WARNING and falls back to 'off'."""
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

    # Implemented value "block_high" -> passes through, NO warning
    with caplog.at_level(logging.WARNING):
        result = parse_verify_before_replying_backstop_mode(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE": "block_high"}
        )
    assert result == "block_high"
    assert not caplog.records, "block_high is implemented; no warning expected"

    caplog.clear()

    # Implemented value "repair_high" -> passes through, NO warning
    with caplog.at_level(logging.WARNING):
        result = parse_verify_before_replying_backstop_mode(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE": "repair_high"}
        )
    assert result == "repair_high"
    assert not caplog.records, "repair_high is implemented; no warning expected"

    caplog.clear()

    # Garbage value -> warns and falls back to "off"
    with caplog.at_level(logging.WARNING):
        result = parse_verify_before_replying_backstop_mode(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MODE": "garbage_value"}
        )
    assert result == "off"
    assert caplog.records, "Expected a WARNING for unknown value 'garbage_value'"


def test_backstop_max_attempts_default_and_clamp() -> None:
    """MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MAX_ATTEMPTS defaults to 2 and clamps
    to [1, 5]."""
    from magi_agent.config.env import (
        parse_verify_before_replying_backstop_max_attempts,
    )

    assert parse_verify_before_replying_backstop_max_attempts({}) == 2
    assert (
        parse_verify_before_replying_backstop_max_attempts(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MAX_ATTEMPTS": "4"}
        )
        == 4
    )
    # clamp high
    assert (
        parse_verify_before_replying_backstop_max_attempts(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MAX_ATTEMPTS": "99"}
        )
        == 5
    )
    # clamp low
    assert (
        parse_verify_before_replying_backstop_max_attempts(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MAX_ATTEMPTS": "0"}
        )
        == 1
    )
    # garbage -> default
    assert (
        parse_verify_before_replying_backstop_max_attempts(
            {"MAGI_VERIFY_BEFORE_REPLYING_BACKSTOP_MAX_ATTEMPTS": "xyz"}
        )
        == 2
    )


def test_backstop_repair_message_is_directive_no_ship_escape() -> None:
    """The repair_high directive message demands a revision and offers NO
    SHIP_AS_IS escape (unlike the advisory nudge)."""
    from magi_agent.evidence.verify_audit import (
        VerifyFinding,
        build_backstop_repair_message,
    )

    f = VerifyFinding(
        finding_id="x",
        rule_id="verify_before_replying.evidence_consistency",
        confidence="high",
        claim_class="numeric",
        claim_text="revenue was 5B",
        span=(0, 14),
        evidence_refs=("tool:calc#1",),
        expected="4.2B",
        observed="5B",
        detail="ledger says 4.2B",
        suggested_action="revise",
    )
    msg = build_backstop_repair_message([f], attempt=1, max_attempts=2)
    assert 'backstop="repair_high"' in msg
    assert "attempt 1 of 2" in msg
    assert "revenue was 5B" in msg
    # directive: NO SHIP_AS_IS escape while budget remains
    assert "Do NOT respond with SHIP_AS_IS" in msg


def test_backstop_block_notice_directs_honest_answer() -> None:
    """The block_high exhaustion notice directs an honest partial answer, never
    a fabrication, and never a silent ship."""
    from magi_agent.evidence.verify_audit import (
        VerifyFinding,
        build_backstop_block_notice,
    )

    f = VerifyFinding(
        finding_id="x",
        rule_id="verify_before_replying.activity_grounding",
        confidence="high",
        claim_class="activity",
        claim_text="I edited config.py",
        span=(0, 18),
        evidence_refs=(),
        expected=None,
        observed=None,
        detail="no edit receipt",
        suggested_action="revise",
    )
    msg = build_backstop_block_notice([f])
    assert 'backstop="block_high"' in msg
    assert "exhausted" in msg
    assert "Never fabricate" in msg
    assert "I edited config.py" in msg
