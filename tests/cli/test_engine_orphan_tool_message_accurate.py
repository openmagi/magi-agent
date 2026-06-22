"""Orphan ``tool_end`` events must not falsely blame the user.

``_synthesize_orphan_tool_results`` balances the transcript when the turn
ends with pending tool calls (no matching tool_result). The pre-fix code
hard-coded the output_preview to "tool interrupted by user cancellation"
regardless of cause — so engine errors / repair-fork aborts also showed
up in the dashboard as if the user had cancelled. This test pins the
correct message per cause.

The fix takes a ``reason`` argument. Three causes today:
  * "user_interrupt" — real cancel via ESC/stop button.
  * "engine_error"  — the underlying runner raised; we sweep dangling tools.
  * "repair_fork"   — a repair attempt aborted before the tool completed.
"""
from __future__ import annotations

from magi_agent.cli.engine import MagiEngineDriver


def test_user_interrupt_keeps_legacy_phrasing_for_back_compat() -> None:
    # Existing surfaces (export transcripts, downstream consumers) recognise
    # the legacy phrasing on a real user interrupt — keep it identical so the
    # only behavior change is on the NON-user-cancel paths.
    [event] = MagiEngineDriver._synthesize_orphan_tool_results(
        {"tu-1": "FileEdit"},
        turn_id="turn-x",
        reason="user_interrupt",
    )
    assert event["type"] == "tool_end"
    assert event["id"] == "tu-1"
    assert event["status"] == "error"
    assert event["interrupted"] is True
    assert event["output_preview"] == "tool interrupted by user cancellation"
    # The structured reason field is the authoritative signal for new consumers.
    assert event["reason"] == "user_interrupt"


def test_engine_error_does_not_blame_the_user() -> None:
    [event] = MagiEngineDriver._synthesize_orphan_tool_results(
        {"tu-2": "Bash"},
        turn_id="turn-y",
        reason="engine_error",
    )
    assert "user cancellation" not in event["output_preview"], event
    # The new phrasing must be self-explanatory and name engine error.
    assert "engine" in str(event["output_preview"]).lower()
    assert event["reason"] == "engine_error"
    assert event["interrupted"] is True


def test_repair_fork_does_not_blame_the_user() -> None:
    [event] = MagiEngineDriver._synthesize_orphan_tool_results(
        {"tu-3": "PythonExec"},
        turn_id="turn-z",
        reason="repair_fork",
    )
    assert "user cancellation" not in event["output_preview"], event
    assert "repair" in str(event["output_preview"]).lower()
    assert event["reason"] == "repair_fork"


def test_unknown_reason_falls_back_to_neutral_phrasing() -> None:
    # A future cause that we forgot to translate must NOT default back to the
    # user-cancellation lie — fall through to a neutral string + carry the raw
    # reason in the structured field.
    [event] = MagiEngineDriver._synthesize_orphan_tool_results(
        {"tu-4": "FileWrite"},
        turn_id="turn-q",
        reason="some_future_cause",
    )
    preview = str(event["output_preview"]).lower()
    assert "user cancellation" not in preview, preview
    assert "interrupted" in preview
    assert event["reason"] == "some_future_cause"


def test_multiple_pending_tools_each_get_their_own_event() -> None:
    pending = {"tu-a": "FileRead", "tu-b": "Bash"}
    events = MagiEngineDriver._synthesize_orphan_tool_results(
        pending, turn_id="turn-multi", reason="engine_error"
    )
    assert len(events) == 2
    ids = {e["id"] for e in events}
    assert ids == {"tu-a", "tu-b"}
    for e in events:
        assert "user cancellation" not in e["output_preview"], e


def test_helper_clears_pending_dict_as_before() -> None:
    pending = {"tu-1": "X"}
    MagiEngineDriver._synthesize_orphan_tool_results(
        pending, turn_id="turn-clear", reason="user_interrupt"
    )
    # Same invariant the existing implementation guarantees: caller's dict
    # is drained so a subsequent attempt won't re-sweep the same tool ids.
    assert pending == {}
