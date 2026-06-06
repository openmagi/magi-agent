# tests/recipes/first_party/legal/test_prompt_variants.py
from __future__ import annotations

from magi_agent.recipes.first_party.legal.prompt_variants import (
    PROMPT_VARIANTS,
    phrase_instruction,
    select_variant,
)


def test_frozen_choice_is_returned_for_known_task(monkeypatch) -> None:
    monkeypatch.setitem(PROMPT_VARIANTS, "abercrombie", "technical")
    assert select_variant("abercrombie") == "technical"


def test_unknown_task_defaults_to_plain() -> None:
    assert select_variant("no_such_task") == "plain"


def test_phrase_instruction_differs_by_variant() -> None:
    plain = phrase_instruction("Decide the answer.", variant="plain")
    technical = phrase_instruction("Decide the answer.", variant="technical")
    assert plain != technical
    assert "Decide the answer." in plain
