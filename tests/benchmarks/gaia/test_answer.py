from __future__ import annotations

from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, extract_final_answer


def test_extracts_last_final_answer() -> None:
    text = "thinking...\nFINAL ANSWER: 42\nnoise\nFINAL ANSWER: egalitarian"
    assert extract_final_answer(text) == "egalitarian"


def test_strips_trailing_period_and_space() -> None:
    assert extract_final_answer("FINAL ANSWER:  Paris . ") == "Paris"


def test_returns_empty_when_absent() -> None:
    assert extract_final_answer("no answer here") == ""


def test_prompt_mentions_final_answer_contract() -> None:
    assert "FINAL ANSWER" in GAIA_SYSTEM_PROMPT
