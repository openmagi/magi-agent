# tests/recipes/first_party/legal/test_output_parser.py
from __future__ import annotations

from magi_agent.recipes.first_party.legal.output_parser import parse_answer


def test_exact_label_after_prose() -> None:
    assert parse_answer("The answer is: Yes.", labels=("Yes", "No")) == "Yes"


def test_case_insensitive_match() -> None:
    assert parse_answer("no", labels=("Yes", "No")) == "No"


def test_prefers_first_label_token_when_both_present() -> None:
    # Model echoes options then concludes — take the last standalone label.
    assert parse_answer("Yes or No? No", labels=("Yes", "No")) == "No"


def test_no_label_returns_none() -> None:
    assert parse_answer("I am not sure.", labels=("Yes", "No")) is None


def test_multiword_label_matched() -> None:
    assert parse_answer(
        "The result is: Breach of contract.",
        labels=("No breach", "Breach of contract"),
    ) == "Breach of contract"


def test_prefix_overlap_prefers_longer_label() -> None:
    assert parse_answer(
        "Yes, with conditions",
        labels=("Yes", "Yes, with conditions"),
    ) == "Yes, with conditions"
