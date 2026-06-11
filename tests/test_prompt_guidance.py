"""D2-D4 — default-OFF prompt guidance block builders (Fable port)."""
from __future__ import annotations

from magi_agent.runtime.prompt_guidance import (
    action_discipline_examples_block,
    anti_rationalization_block,
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


def test_blocks_are_lean() -> None:
    blocks = (
        action_discipline_examples_block({"MAGI_PROMPT_EXAMPLES_ENABLED": "1"}),
        search_decision_block({"MAGI_PROMPT_SEARCH_RULES_ENABLED": "1", **_KEYS}),
        anti_rationalization_block({"MAGI_PROMPT_REDFLAGS_ENABLED": "1"}),
    )
    for block in blocks:
        assert 0 < len(block) <= 800  # tag overhead on top of ~600-char budget


def test_builders_fail_open(monkeypatch) -> None:
    def boom(_env=None):  # noqa: ANN001
        raise RuntimeError("synthetic")

    monkeypatch.setattr("magi_agent.config.env.is_prompt_examples_enabled", boom)
    assert action_discipline_examples_block(
        {"MAGI_PROMPT_EXAMPLES_ENABLED": "1"}
    ) == ""
