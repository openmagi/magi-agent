"""U6 RED -- flags and env parsers for the injection_guard policy.

Covers the two FlagSpec entries introduced by U6:

  MAGI_INJECTION_GUARD_ENABLED  (_pb, profile default-ON)
  MAGI_INJECTION_GUARD_MODE     (str, shipped value = 'annotate')
"""
from __future__ import annotations


def test_master_flag_is_profile_bool() -> None:
    """MAGI_INJECTION_GUARD_ENABLED is a _pb gate: ON under the full profile
    (unset profile OR MAGI_RUNTIME_PROFILE=full), OFF under safe/eval, and an
    explicit 0 wins over any profile setting."""
    from magi_agent.config.env import parse_injection_guard_enabled

    assert parse_injection_guard_enabled({}) is True
    assert parse_injection_guard_enabled({"MAGI_RUNTIME_PROFILE": "safe"}) is False
    assert parse_injection_guard_enabled({"MAGI_RUNTIME_PROFILE": "eval"}) is False
    assert (
        parse_injection_guard_enabled({"MAGI_INJECTION_GUARD_ENABLED": "0"}) is False
    )
    assert (
        parse_injection_guard_enabled(
            {"MAGI_RUNTIME_PROFILE": "safe", "MAGI_INJECTION_GUARD_ENABLED": "1"}
        )
        is True
    )


def test_mode_defaults_to_annotate() -> None:
    """MAGI_INJECTION_GUARD_MODE ships with 'annotate' as the default; the two
    other modes are 'record' (evidence only) and 'nudge' (honored in U7).
    Unknown values fall back to 'annotate'."""
    from magi_agent.config.env import parse_injection_guard_mode

    assert parse_injection_guard_mode({}) == "annotate"
    assert (
        parse_injection_guard_mode({"MAGI_INJECTION_GUARD_MODE": "record"}) == "record"
    )
    assert (
        parse_injection_guard_mode({"MAGI_INJECTION_GUARD_MODE": "annotate"})
        == "annotate"
    )
    # nudge is a valid value even though U6 does not act on it (U7 does).
    assert (
        parse_injection_guard_mode({"MAGI_INJECTION_GUARD_MODE": "nudge"}) == "nudge"
    )
    # Case-insensitive + whitespace tolerant.
    assert (
        parse_injection_guard_mode({"MAGI_INJECTION_GUARD_MODE": "  RECORD  "})
        == "record"
    )
    # Garbage falls back to the shipped default.
    assert (
        parse_injection_guard_mode({"MAGI_INJECTION_GUARD_MODE": "garbage"})
        == "annotate"
    )


def test_flags_are_registered_in_the_registry() -> None:
    """Both flags must exist in the single-source-of-truth FLAGS registry with
    the documented kind, stage, and scope."""
    from magi_agent.config.flags import get_flag

    enabled = get_flag("MAGI_INJECTION_GUARD_ENABLED")
    assert enabled.kind == "profile_bool"
    assert enabled.stage == "stage1"
    assert enabled.scope == "public"

    mode = get_flag("MAGI_INJECTION_GUARD_MODE")
    assert mode.kind == "str"
    assert mode.default == "annotate"
    assert mode.stage == "stage1"
    assert mode.scope == "public"
