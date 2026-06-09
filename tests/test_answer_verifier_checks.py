"""Tests for answer_verifier_checks.py — unit tests for each check type.

TDD: tests written before implementation.  All tests must be hermetic (no network).
"""
from __future__ import annotations

import pytest

from magi_agent.research.answer_verifier_checks import (
    VerifierCheckResult,
    build_verifier_prompt,
    detect_answer_type,
    parse_verifier_response,
    safety_guard_check,
)


# ---------------------------------------------------------------------------
# detect_answer_type
# ---------------------------------------------------------------------------


class TestDetectAnswerType:
    def test_how_many_is_count(self) -> None:
        assert detect_answer_type("How many species are there?", "7") == "count"

    def test_how_many_number_answer(self) -> None:
        assert detect_answer_type("How many pages?", "3") == "count"

    def test_list_answer_with_comma(self) -> None:
        t = detect_answer_type("What are the birthplaces?", "Honolulu, Quincy")
        assert t == "list"

    def test_number_word_answer_is_count(self) -> None:
        # Pure digit answers default to count when question asks for a count
        assert detect_answer_type("What is the count of items?", "10") == "count"

    def test_single_noun_is_singular_plural(self) -> None:
        t = detect_answer_type("What term is used?", "inference")
        assert t in ("singular_plural", "unspecified")

    def test_plural_noun_is_singular_plural(self) -> None:
        t = detect_answer_type("What term is used?", "inferences")
        assert t in ("singular_plural", "unspecified")

    def test_ordinal_stanza_is_ordinal(self) -> None:
        t = detect_answer_type("In which stanza does this occur?", "2")
        assert t in ("ordinal", "count")

    def test_entity_name_answer(self) -> None:
        # Proper noun (capitalized multi-word) → entity or unspecified
        t = detect_answer_type("Where was he born?", "New York")
        assert t in ("entity", "unspecified")

    def test_arithmetic_sum_question(self) -> None:
        t = detect_answer_type("What is the sum of the values?", "42")
        assert t in ("arithmetic", "count")


# ---------------------------------------------------------------------------
# build_verifier_prompt
# ---------------------------------------------------------------------------


class TestBuildVerifierPrompt:
    def test_prompt_contains_question(self) -> None:
        prompt = build_verifier_prompt(
            question="How many items?",
            final_answer="7",
            answer_type_hint="count",
            evidence_snippets=("Found A, B, C",),
        )
        assert "How many items?" in prompt

    def test_prompt_contains_final_answer(self) -> None:
        prompt = build_verifier_prompt(
            question="Q",
            final_answer="42",
            answer_type_hint="count",
            evidence_snippets=("some evidence",),
        )
        assert "42" in prompt

    def test_prompt_contains_verdict_instructions(self) -> None:
        prompt = build_verifier_prompt(
            question="Q",
            final_answer="7",
            answer_type_hint="count",
            evidence_snippets=("evidence",),
        )
        assert "VERDICT" in prompt
        assert "CONFIRMED" in prompt
        assert "MISMATCH" in prompt

    def test_prompt_contains_evidence(self) -> None:
        prompt = build_verifier_prompt(
            question="Q",
            final_answer="7",
            answer_type_hint="count",
            evidence_snippets=("Species: A, B, C, D, E, F",),
        )
        assert "Species: A, B, C, D, E, F" in prompt

    def test_prompt_truncates_long_evidence(self) -> None:
        long_snippet = "x" * 20_000
        prompt = build_verifier_prompt(
            question="Q",
            final_answer="7",
            answer_type_hint="count",
            evidence_snippets=(long_snippet,),
        )
        # Should be truncated to fit within token budget
        assert len(prompt) < 40_000

    def test_prompt_requires_corrected_value_on_mismatch(self) -> None:
        prompt = build_verifier_prompt(
            question="Q",
            final_answer="7",
            answer_type_hint="count",
            evidence_snippets=("evidence",),
        )
        assert "CORRECTED_VALUE" in prompt


# ---------------------------------------------------------------------------
# parse_verifier_response
# ---------------------------------------------------------------------------


class TestParseVerifierResponse:
    def test_parse_confirmed(self) -> None:
        raw = "VERDICT: CONFIRMED"
        verdict, corrected, basis = parse_verifier_response(raw)
        assert verdict == "confirmed"
        assert corrected is None
        assert basis == ""

    def test_parse_mismatch_with_fields(self) -> None:
        raw = (
            "VERDICT: MISMATCH\n"
            "CORRECTED_VALUE: 6\n"
            "EVIDENCE_BASIS: Found 6 items in the list"
        )
        verdict, corrected, basis = parse_verifier_response(raw)
        assert verdict == "mismatch"
        assert corrected == "6"
        assert "6 items" in basis

    def test_parse_mismatch_missing_corrected_value_is_confirmed(self) -> None:
        # If MISMATCH but no CORRECTED_VALUE, treat as confirmed (fail-open)
        raw = "VERDICT: MISMATCH\nEVIDENCE_BASIS: something"
        verdict, corrected, basis = parse_verifier_response(raw)
        assert verdict == "confirmed"
        assert corrected is None

    def test_parse_garbage_is_confirmed(self) -> None:
        """Unparseable response → fail-open CONFIRMED."""
        verdict, corrected, basis = parse_verifier_response("some random text")
        assert verdict == "confirmed"

    def test_parse_case_insensitive(self) -> None:
        raw = "verdict: confirmed"
        verdict, _, _ = parse_verifier_response(raw)
        assert verdict == "confirmed"

    def test_parse_mismatch_strips_whitespace(self) -> None:
        raw = "VERDICT: MISMATCH\nCORRECTED_VALUE:  inference  \nEVIDENCE_BASIS: found it"
        _, corrected, _ = parse_verifier_response(raw)
        assert corrected == "inference"


# ---------------------------------------------------------------------------
# safety_guard_check
# ---------------------------------------------------------------------------


class TestSafetyGuardCheck:
    # Numeric guard: ratio must be in [0.5, 2.0]
    def test_numeric_within_ratio_allowed(self) -> None:
        assert safety_guard_check("7", "8") is True  # 8/7 ≈ 1.14 — allowed

    def test_numeric_ratio_too_large_rejected(self) -> None:
        assert safety_guard_check("7", "700") is False  # 100x — rejected

    def test_numeric_ratio_too_small_rejected(self) -> None:
        assert safety_guard_check("100", "10") is False  # 0.1x — rejected

    def test_numeric_zero_original_falls_back_to_text_check(self) -> None:
        # original "0" → numeric ratio is undefined; fall back to Jaccard
        # "0" vs "0" — Jaccard=1.0 → allowed
        assert safety_guard_check("0", "0") is True

    def test_numeric_off_by_one_allowed(self) -> None:
        assert safety_guard_check("9", "10") is True  # 10/9 ≈ 1.11 — allowed

    # Text guard: Jaccard >= 0.2
    def test_text_similar_allowed(self) -> None:
        assert safety_guard_check("inference", "inferences") is True

    def test_text_completely_different_rejected(self) -> None:
        result = safety_guard_check(
            "inference",
            "completely unrelated xyz answer nothing in common",
        )
        assert result is False

    def test_text_same_value_allowed(self) -> None:
        assert safety_guard_check("hello world", "hello world") is True

    def test_entity_list_correction_within_jaccard(self) -> None:
        # "Honolulu, Quincy" vs "Braintree, Honolulu" — share "Honolulu"
        assert safety_guard_check("Honolulu, Quincy", "Braintree, Honolulu") is True
