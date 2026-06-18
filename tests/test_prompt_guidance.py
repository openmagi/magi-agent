"""D2-D4 — default-OFF prompt guidance block builders (Fable port)."""
from __future__ import annotations

import pytest

from magi_agent.runtime.prompt_guidance import (
    action_discipline_examples_block,
    anti_rationalization_block,
    automation_methodology_block,
    research_methodology_block,
    search_decision_block,
)

_KEYS = {"BRAVE_API_KEY": "k1", "FIRECRAWL_API_KEY": "k2"}


def test_examples_block_matrix() -> None:
    assert action_discipline_examples_block({}) == ""
    assert action_discipline_examples_block(
        {"MAGI_PROMPT_EXAMPLES_ENABLED": "0"}
    ) == ""
    block = action_discipline_examples_block({"MAGI_PROMPT_EXAMPLES_ENABLED": "1"})
    assert block.startswith("<action_discipline_examples>")
    assert block.endswith("</action_discipline_examples>")
    assert "Do NOT" in block


def test_search_block_requires_flag_and_both_keys() -> None:
    flag = {"MAGI_PROMPT_SEARCH_RULES_ENABLED": "1"}
    assert search_decision_block({}) == ""
    assert search_decision_block(flag) == ""  # flag without keys
    assert search_decision_block({**flag, "BRAVE_API_KEY": "k"}) == ""
    assert search_decision_block({**flag, "FIRECRAWL_API_KEY": "k"}) == ""
    assert search_decision_block({**_KEYS}) == ""  # keys without flag
    block = search_decision_block({**flag, **_KEYS})
    assert block.startswith("<search_decision>")
    assert "rate of change" in block


def test_redflags_block_matrix() -> None:
    assert anti_rationalization_block({}) == ""
    assert anti_rationalization_block({"MAGI_PROMPT_REDFLAGS_ENABLED": "0"}) == ""
    block = anti_rationalization_block({"MAGI_PROMPT_REDFLAGS_ENABLED": "1"})
    assert block.startswith("<red_flags>")
    assert "->" in block


def test_research_methodology_block_matrix() -> None:
    assert research_methodology_block({}) == ""
    assert research_methodology_block({"MAGI_RESEARCH_METHODOLOGY_ENABLED": "0"}) == ""
    block = research_methodology_block({"MAGI_RESEARCH_METHODOLOGY_ENABLED": "1"})
    assert block.startswith("<research_methodology>")
    assert block.endswith("</research_methodology>")
    assert "two independent sources" in block
    assert "primary sources" in block


def test_automation_methodology_block_matrix() -> None:
    assert automation_methodology_block({}) == ""
    assert automation_methodology_block({"MAGI_AUTOMATION_METHODOLOGY_ENABLED": "0"}) == ""
    block = automation_methodology_block({"MAGI_AUTOMATION_METHODOLOGY_ENABLED": "1"})
    assert block.startswith("<automation_methodology>")
    assert block.endswith("</automation_methodology>")
    assert "deliverable" in block
    assert "evidence produced this turn" in block


def test_blocks_are_lean() -> None:
    blocks = (
        action_discipline_examples_block({"MAGI_PROMPT_EXAMPLES_ENABLED": "1"}),
        search_decision_block({"MAGI_PROMPT_SEARCH_RULES_ENABLED": "1", **_KEYS}),
        anti_rationalization_block({"MAGI_PROMPT_REDFLAGS_ENABLED": "1"}),
        research_methodology_block({"MAGI_RESEARCH_METHODOLOGY_ENABLED": "1"}),
        automation_methodology_block({"MAGI_AUTOMATION_METHODOLOGY_ENABLED": "1"}),
    )
    for block in blocks:
        assert 0 < len(block) <= 800  # tag overhead on top of ~600-char budget


@pytest.mark.parametrize(
    ("builder", "helper_name", "enabled_env"),
    (
        (
            action_discipline_examples_block,
            "is_prompt_examples_enabled",
            {"MAGI_PROMPT_EXAMPLES_ENABLED": "1"},
        ),
        (
            search_decision_block,
            "is_prompt_search_rules_enabled",
            {"MAGI_PROMPT_SEARCH_RULES_ENABLED": "1", **_KEYS},
        ),
        (
            anti_rationalization_block,
            "is_prompt_redflags_enabled",
            {"MAGI_PROMPT_REDFLAGS_ENABLED": "1"},
        ),
        (
            research_methodology_block,
            "is_research_methodology_enabled",
            {"MAGI_RESEARCH_METHODOLOGY_ENABLED": "1"},
        ),
        (
            automation_methodology_block,
            "is_automation_methodology_enabled",
            {"MAGI_AUTOMATION_METHODOLOGY_ENABLED": "1"},
        ),
    ),
)
def test_builders_fail_open(monkeypatch, builder, helper_name, enabled_env) -> None:
    def boom(_env=None):  # noqa: ANN001
        raise RuntimeError("synthetic")

    monkeypatch.setattr(f"magi_agent.config.env.{helper_name}", boom)
    assert builder(enabled_env) == ""


def test_cli_instruction_off_by_default(monkeypatch) -> None:
    for name in (
        "MAGI_PROMPT_EXAMPLES_ENABLED",
        "MAGI_PROMPT_SEARCH_RULES_ENABLED",
        "MAGI_PROMPT_REDFLAGS_ENABLED",
        "MAGI_RESEARCH_METHODOLOGY_ENABLED",
        "MAGI_AUTOMATION_METHODOLOGY_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s")
    assert "<action_discipline_examples>" not in prompt
    assert "<search_decision>" not in prompt
    assert "<red_flags>" not in prompt
    assert "<research_methodology>" not in prompt
    assert "<automation_methodology>" not in prompt


def test_cli_instruction_injects_methodology_blocks(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_RESEARCH_METHODOLOGY_ENABLED", "1")
    monkeypatch.setenv("MAGI_AUTOMATION_METHODOLOGY_ENABLED", "1")
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s")
    assert "<research_methodology>" in prompt
    assert "<automation_methodology>" in prompt


def test_cli_instruction_methodology_blocks_independent(monkeypatch) -> None:
    # Each flag is independent: research on, automation off.
    monkeypatch.setenv("MAGI_RESEARCH_METHODOLOGY_ENABLED", "1")
    monkeypatch.delenv("MAGI_AUTOMATION_METHODOLOGY_ENABLED", raising=False)
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s")
    assert "<research_methodology>" in prompt
    assert "<automation_methodology>" not in prompt


def test_cli_instruction_injects_enabled_blocks(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_PROMPT_EXAMPLES_ENABLED", "1")
    monkeypatch.setenv("MAGI_PROMPT_REDFLAGS_ENABLED", "1")
    monkeypatch.setenv("MAGI_PROMPT_SEARCH_RULES_ENABLED", "1")
    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k2")
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s")
    assert "<action_discipline_examples>" in prompt
    assert "<search_decision>" in prompt
    assert "<red_flags>" in prompt


def test_cli_instruction_search_block_needs_keys(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_PROMPT_SEARCH_RULES_ENABLED", "1")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    from magi_agent.cli.tool_runtime import build_cli_instruction

    prompt = build_cli_instruction(session_id="s")
    assert "<search_decision>" not in prompt
