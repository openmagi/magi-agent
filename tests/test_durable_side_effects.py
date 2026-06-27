"""WS1 PR1c - side-effecting-tool classifier (design section 0.5).

A checkpoint is auto-resumable ONLY when there is no pending (started-but-
unfinished) tool AND the most recent completed tool is not side-effecting. The
classifier is fail-closed: an UNKNOWN tool name is treated as side-effecting so
an unclassified tool blocks auto-resume rather than risking a double-send.
"""
from __future__ import annotations

from magi_agent.runtime.durable_side_effects import (
    SIDE_EFFECTING_TOOL_NAMES,
    is_turn_resumable,
)


def test_known_pure_no_pending_is_resumable() -> None:
    # A read-only/pure last tool with no pending tool => resumable.
    assert is_turn_resumable(pending_tool_ids=(), last_completed_tool_name="read_file") is True


def test_side_effecting_last_tool_blocks_resume() -> None:
    # A known side-effecting last completed tool => not resumable.
    for name in ("send_telegram_message", "RunInBackground", "call_tool"):
        assert name in SIDE_EFFECTING_TOOL_NAMES
        assert is_turn_resumable(pending_tool_ids=(), last_completed_tool_name=name) is False


def test_pending_tool_blocks_resume_even_if_pure() -> None:
    # A pending (unfinished) tool => mid-flight => never auto-resume.
    assert (
        is_turn_resumable(pending_tool_ids=("call_1",), last_completed_tool_name="read_file")
        is False
    )


def test_unknown_tool_is_fail_closed() -> None:
    # An unclassified tool name is treated as side-effecting (fail-closed).
    assert "some_unregistered_tool" not in SIDE_EFFECTING_TOOL_NAMES
    assert (
        is_turn_resumable(pending_tool_ids=(), last_completed_tool_name="some_unregistered_tool")
        is False
    )


def test_no_completed_tool_is_resumable() -> None:
    # No tool ran at all (pure text turn) and nothing pending => resumable.
    assert is_turn_resumable(pending_tool_ids=(), last_completed_tool_name=None) is True
