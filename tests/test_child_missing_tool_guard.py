"""Unit tests for the child missing-tool streak guard (Fix F)."""

from __future__ import annotations

from magi_agent.runtime.child_missing_tool_guard import (
    MISSING_TOOL_STREAK_REASON,
    MissingToolStreak,
    classify_missing_tool_response,
    resolve_missing_tool_streak_cap,
)


def test_classify_soft_fail_corrective_dict() -> None:
    assert (
        classify_missing_tool_response(
            {"response_type": "MAGI_TOOL_NOT_FOUND_SOFT_FAIL", "error_code": "tool_not_found"}
        )
        is True
    )


def test_classify_dispatcher_codes() -> None:
    assert classify_missing_tool_response({"error_code": "tool_not_found"}) is True
    assert classify_missing_tool_response({"errorCode": "tool_not_exposed"}) is True


def test_classify_success_and_other_errors_are_false() -> None:
    assert classify_missing_tool_response({"status": "ok"}) is False
    # A real tool error (missing file) is NOT a missing-tool marker: resets.
    assert classify_missing_tool_response({"error_code": "path_not_found"}) is False


def test_classify_non_dict_is_none() -> None:
    assert classify_missing_tool_response("nope") is None
    assert classify_missing_tool_response(None) is None


def test_streak_trips_exactly_at_cap() -> None:
    s = MissingToolStreak(4)
    assert [s.update(True) for _ in range(5)] == [False, False, False, True, True]


def test_streak_resets_on_non_missing() -> None:
    s = MissingToolStreak(4)
    s.update(True)
    s.update(True)
    assert s.update(False) is False
    assert s.count == 0
    # After reset it takes a fresh full run to trip.
    assert [s.update(True) for _ in range(4)] == [False, False, False, True]


def test_streak_none_marker_ignored() -> None:
    s = MissingToolStreak(2)
    assert s.update(None) is False
    s.update(True)
    assert s.update(None) is False  # ignored, count unchanged
    assert s.update(True) is True


def test_streak_cap_zero_never_trips() -> None:
    s = MissingToolStreak(0)
    assert all(s.update(True) is False for _ in range(10))


def test_resolve_cap_default_and_env() -> None:
    assert resolve_missing_tool_streak_cap({}) == 4
    assert resolve_missing_tool_streak_cap({"MAGI_CHILD_MISSING_TOOL_STREAK_CAP": "6"}) == 6
    assert resolve_missing_tool_streak_cap({"MAGI_CHILD_MISSING_TOOL_STREAK_CAP": "0"}) == 0
    # Invalid / negative -> default.
    assert resolve_missing_tool_streak_cap({"MAGI_CHILD_MISSING_TOOL_STREAK_CAP": "x"}) == 4
    assert resolve_missing_tool_streak_cap({"MAGI_CHILD_MISSING_TOOL_STREAK_CAP": "-3"}) == 4


def test_reason_token_value() -> None:
    assert MISSING_TOOL_STREAK_REASON == "child_llm_missing_tool_streak_exhausted"


def test_soft_fail_marker_matches_source() -> None:
    """The inlined marker must match the canonical constant (drift guard). This
    import is heavy (google.adk) so it lives ONLY in the test, keeping the guard
    module import light for child_runner_live's import-light contract."""
    from magi_agent.adk_bridge.tool_not_found_soft_fail import (
        TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE as _CANONICAL,
    )
    from magi_agent.runtime.child_missing_tool_guard import (
        TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE as _INLINED,
    )

    assert _INLINED == _CANONICAL
