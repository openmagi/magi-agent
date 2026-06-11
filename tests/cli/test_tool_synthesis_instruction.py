"""build_cli_instruction integration tests for the tool-synthesis recipe block.

Hard requirement: with ``MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED`` OFF (default) the
assembled system prompt contains NO tool-synthesis block — zero behavior
change. With the flag ON the block appears only for frontier-tier models.
"""

from __future__ import annotations

from magi_agent.cli.tool_runtime import build_cli_instruction

_FLAG = "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED"
_FRONTIER_LABEL = "anthropic/claude-sonnet-4-6"
_CHEAP_LABEL = "anthropic/haiku"
_TAG = "<creating_your_own_tools>"


def test_block_absent_by_default(monkeypatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)
    instruction = build_cli_instruction(
        session_id="test-session", model=_FRONTIER_LABEL
    )
    assert _TAG not in instruction


def test_block_present_when_flag_on_and_frontier(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    instruction = build_cli_instruction(
        session_id="test-session", model=_FRONTIER_LABEL
    )
    assert _TAG in instruction
    assert ".magi/tools/" in instruction


def test_block_absent_when_flag_on_but_cheap_tier(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    instruction = build_cli_instruction(session_id="test-session", model=_CHEAP_LABEL)
    assert _TAG not in instruction


def test_block_absent_when_flag_on_but_no_model(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    instruction = build_cli_instruction(session_id="test-session")
    assert _TAG not in instruction
