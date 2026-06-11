"""Tests for forced-answer / no-abstention helpers (TDD — written before implementation)."""
from __future__ import annotations

import pytest

from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT
from benchmarks.gaia.forced_answer import force_answer, is_abstention


# ---------------------------------------------------------------------------
# is_abstention
# ---------------------------------------------------------------------------


class TestIsAbstention:
    def test_empty_string(self) -> None:
        assert is_abstention("") is True

    def test_whitespace_only(self) -> None:
        assert is_abstention("   \n\t  ") is True

    def test_unable_to_determine(self) -> None:
        assert is_abstention("I am unable to determine the answer.") is True

    def test_cannot_determine(self) -> None:
        assert is_abstention("I cannot determine the final answer.") is True

    def test_can_not_determine(self) -> None:
        assert is_abstention("I can not determine this.") is True

    def test_not_able_to(self) -> None:
        assert is_abstention("I am not able to answer this question.") is True

    def test_insufficient_information(self) -> None:
        assert is_abstention("There is insufficient information to answer.") is True

    def test_awaiting_approval(self) -> None:
        assert is_abstention("Awaiting approval from the user.") is True

    def test_awaiting_ellipsis(self) -> None:
        assert is_abstention("awaiting...") is True

    def test_real_answer_number(self) -> None:
        assert is_abstention("42") is False

    def test_real_answer_city(self) -> None:
        assert is_abstention("Paris") is False

    def test_real_answer_phrase(self) -> None:
        assert is_abstention("the quick brown fox") is False

    def test_case_insensitive_unable(self) -> None:
        assert is_abstention("UNABLE TO DETERMINE") is True

    def test_partial_match_in_sentence(self) -> None:
        # A real answer that happens to contain "not" should NOT be flagged
        assert is_abstention("knot") is False


# ---------------------------------------------------------------------------
# force_answer
# ---------------------------------------------------------------------------


def _fake_model(prompt: str) -> str:
    """Fake model that always returns 'Paris'."""
    return "Paris"


def _raising_model(prompt: str) -> str:
    """Fake model that raises to exercise fail-open path."""
    raise RuntimeError("network down")


class TestForceAnswer:
    def test_returns_model_answer_for_empty_input(self) -> None:
        result = force_answer(
            question="What is the capital of France?",
            evidence="",
            model_provider=_fake_model,
        )
        assert result == "Paris"

    def test_returns_model_answer_for_abstaining_input(self) -> None:
        result = force_answer(
            question="What is the capital of France?",
            evidence="Some research was done.",
            model_provider=_fake_model,
        )
        assert result == "Paris"

    def test_fail_open_on_exception(self) -> None:
        original = "unable to determine"
        result = force_answer(
            question="What is 1+1?",
            evidence="",
            model_provider=_raising_model,
            original=original,
        )
        # Must not raise; returns original on exception
        assert result == original

    def test_fail_open_returns_empty_when_no_original(self) -> None:
        result = force_answer(
            question="What is 1+1?",
            evidence="",
            model_provider=_raising_model,
        )
        assert result == ""

    def test_prompt_includes_question(self) -> None:
        """Verify the re-prompt sent to the model contains the question text."""
        prompts: list[str] = []

        def recording_model(prompt: str) -> str:
            prompts.append(prompt)
            return "Berlin"

        force_answer(
            question="What city is this?",
            evidence="Evidence text here.",
            model_provider=recording_model,
        )
        assert len(prompts) == 1
        assert "What city is this?" in prompts[0]

    def test_prompt_includes_evidence(self) -> None:
        prompts: list[str] = []

        def recording_model(prompt: str) -> str:
            prompts.append(prompt)
            return "London"

        force_answer(
            question="Q?",
            evidence="Gathered evidence XYZ.",
            model_provider=recording_model,
        )
        assert "Gathered evidence XYZ." in prompts[0]

    def test_prompt_forbids_hedging(self) -> None:
        prompts: list[str] = []

        def recording_model(prompt: str) -> str:
            prompts.append(prompt)
            return "Rome"

        force_answer(
            question="Q?",
            evidence="",
            model_provider=recording_model,
        )
        lowered = prompts[0].lower()
        assert "best-guess" in lowered or "best guess" in lowered


# ---------------------------------------------------------------------------
# GAIA_SYSTEM_PROMPT — no-abstention instruction
# ---------------------------------------------------------------------------


class TestGaiaSystemPromptNoAbstention:
    def test_prompt_has_final_answer_contract(self) -> None:
        assert "FINAL ANSWER" in GAIA_SYSTEM_PROMPT

    def test_prompt_forbids_abstention(self) -> None:
        lowered = GAIA_SYSTEM_PROMPT.lower()
        assert "never" in lowered or "must not" in lowered or "do not" in lowered

    def test_prompt_instructs_best_guess(self) -> None:
        lowered = GAIA_SYSTEM_PROMPT.lower()
        assert "best" in lowered and "guess" in lowered

    def test_prompt_forbids_unable_to_determine(self) -> None:
        lowered = GAIA_SYSTEM_PROMPT.lower()
        assert "unable to determine" in lowered or "cannot determine" in lowered
