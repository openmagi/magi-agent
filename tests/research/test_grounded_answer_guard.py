"""Tests for the general grounded-answer guard (anti-fabrication lever).

The guard answers ONE general agent-honesty question: does the committed
answer assert a specific numeric/identifier value that is NOT supported
anywhere in the tool/evidence corpus the agent actually collected?

These tests are hermetic (no network, no model, no ADK runner). They drive the
pure detector in :mod:`magi_agent.research.grounded_answer_guard` directly.
"""
from __future__ import annotations

import pytest

from magi_agent.research.grounded_answer_guard import (
    GroundedAnswerVerdict,
    evaluate_answer_grounding,
)


# ---------------------------------------------------------------------------
# Motivating case: YouTube CFM "776,665" with NO supporting tool corpus.
# This is the case the review proved the contract CANNOT catch. The new
# detector MUST flag it as a GUESS.
# ---------------------------------------------------------------------------


def test_specific_number_without_supporting_corpus_is_guess() -> None:
    verdict = evaluate_answer_grounding(
        answer="776665",
        tool_corpus=[
            "VideoFrames is only available for local files in the workspace.",
            "AudioTranscribe requires a local audio file path.",
        ],
    )
    assert isinstance(verdict, GroundedAnswerVerdict)
    assert verdict.status == "guess"
    assert verdict.extracted_value == "776665"
    assert verdict.reason_code == "specific_value_unsupported_by_corpus"


def test_specific_number_present_in_corpus_is_grounded() -> None:
    verdict = evaluate_answer_grounding(
        answer="776665",
        tool_corpus=[
            "The view counter on the page reads 776,665 views as of today.",
        ],
    )
    assert verdict.status == "grounded"
    assert verdict.reason_code == "value_supported_by_corpus"


def test_number_supported_with_thousands_separator_in_answer() -> None:
    # Answer uses comma grouping, corpus uses bare digits — still grounded.
    verdict = evaluate_answer_grounding(
        answer="The total is 776,665.",
        tool_corpus=["raw count: 776665"],
    )
    assert verdict.status == "grounded"


def test_identifier_without_supporting_corpus_is_guess() -> None:
    verdict = evaluate_answer_grounding(
        answer="The model is gpt-4o-mini",
        tool_corpus=["The page did not load; fetch returned a 403."],
    )
    assert verdict.status == "guess"
    assert verdict.extracted_value == "gpt-4o-mini"


def test_identifier_present_in_corpus_is_grounded() -> None:
    verdict = evaluate_answer_grounding(
        answer="gpt-4o-mini",
        tool_corpus=["config model field: gpt-4o-mini"],
    )
    assert verdict.status == "grounded"


# ---------------------------------------------------------------------------
# Anti-false-positive: do NOT flag legitimate non-specific or general answers.
# This protects the forced-answer philosophy + currently-correct answers.
# ---------------------------------------------------------------------------


def test_no_specific_value_extracted_is_grounded() -> None:
    # A bare word answer with no number/identifier is not a fabrication signal.
    verdict = evaluate_answer_grounding(
        answer="Paris",
        tool_corpus=["The page was unreachable."],
    )
    assert verdict.status == "grounded"
    assert verdict.extracted_value is None
    assert verdict.reason_code == "no_specific_value_to_ground"


def test_small_integer_year_like_is_not_flagged() -> None:
    # Small / common values (single/double digit) are too noisy to flag.
    verdict = evaluate_answer_grounding(
        answer="7",
        tool_corpus=["nothing relevant"],
    )
    assert verdict.status == "grounded"


def test_empty_corpus_with_specific_value_is_guess() -> None:
    # No evidence at all + a specific value => cannot be grounded.
    verdict = evaluate_answer_grounding(
        answer="429183",
        tool_corpus=[],
    )
    assert verdict.status == "guess"


def test_empty_answer_is_grounded_noop() -> None:
    verdict = evaluate_answer_grounding(answer="", tool_corpus=["x"])
    assert verdict.status == "grounded"
    assert verdict.extracted_value is None


# ---------------------------------------------------------------------------
# Metadata projection: the verdict serialises to an out-of-band metadata dict
# (verifierEvidenceStatus) — never a mutation of the answer string.
# ---------------------------------------------------------------------------


def test_verdict_metadata_projection_shape() -> None:
    verdict = evaluate_answer_grounding(answer="776665", tool_corpus=[])
    meta = verdict.as_metadata()
    assert meta["verifierEvidenceStatus"] == "guess"
    assert meta["groundedAnswerGuard"] == "specific_value_unsupported_by_corpus"
    assert meta["extractedValue"] == "776665"
    # The metadata MUST NOT contain the scored answer string under an
    # answer-like key (it is metadata only).
    assert "answer" not in meta


@pytest.mark.parametrize("status", ["grounded", "guess"])
def test_verdict_status_round_trips(status: str) -> None:
    if status == "guess":
        verdict = evaluate_answer_grounding(answer="123456", tool_corpus=[])
    else:
        verdict = evaluate_answer_grounding(answer="123456", tool_corpus=["123456"])
    assert verdict.as_metadata()["verifierEvidenceStatus"] == status
