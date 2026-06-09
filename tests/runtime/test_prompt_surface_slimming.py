from __future__ import annotations

import magi_agent.runtime.message_builder as mb
from magi_agent.runtime.message_builder import build_system_prompt


def test_tool_preferences_block_removed():
    assert not hasattr(mb, "TOOL_PREFERENCES_BLOCK")


def test_todo_usage_block_removed():
    assert not hasattr(mb, "TODO_USAGE_BLOCK")


def test_coding_prompt_has_no_removed_blocks():
    prompt = build_system_prompt(
        session_key="s", turn_id="t", identity={}, coding_agent=True
    )
    assert "<tool-preferences>" not in prompt
    assert "<todo-usage>" not in prompt
    # Coding discipline is retained — it shapes code quality, not tool plumbing.
    assert "<coding-discipline>" in prompt


def test_tools_md_not_rendered_as_section():
    # Even if a caller passes a legacy ``tools`` identity key, it is not rendered.
    prompt = build_system_prompt(
        session_key="s",
        turn_id="t",
        identity={"tools": "SOME LEGACY TOOLS DOC"},
        coding_agent=True,
    )
    assert "SOME LEGACY TOOLS DOC" not in prompt
