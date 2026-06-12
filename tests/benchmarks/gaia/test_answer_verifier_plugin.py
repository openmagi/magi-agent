"""Tests for GAIA answer verifier plugin — build_evidence_payload + apply wrapper.

Hermetic: fake model provider, no network calls.
"""
from __future__ import annotations

import os

import pytest

from benchmarks.gaia.answer_verifier_plugin import (
    apply_answer_verifier,
    build_evidence_payload,
)
from magi_agent.research.answer_verifier import AnswerVerifierEvidencePayload


# ---------------------------------------------------------------------------
# build_evidence_payload
# ---------------------------------------------------------------------------


class TestBuildEvidencePayload:
    def test_returns_evidence_payload(self) -> None:
        payload = build_evidence_payload(
            question="How many items?",
            tool_call_log=[
                {"type": "text", "content": "I found 6 items: A, B, C, D, E, F"},
            ],
            fetched_sources=[],
        )
        assert isinstance(payload, AnswerVerifierEvidencePayload)
        assert payload.question == "How many items?"

    def test_extracts_text_from_tool_call_log(self) -> None:
        payload = build_evidence_payload(
            question="Q?",
            tool_call_log=[
                {"type": "text", "content": "Species found: A B C"},
                {"type": "tool_result", "content": "Additional data D E F"},
            ],
            fetched_sources=[],
        )
        combined = " ".join(payload.evidence_snippets)
        assert "Species found" in combined or "Additional data" in combined

    def test_includes_fetched_sources(self) -> None:
        payload = build_evidence_payload(
            question="Q?",
            tool_call_log=[],
            fetched_sources=["Source text with key fact here"],
        )
        combined = " ".join(payload.evidence_snippets)
        assert "key fact" in combined

    def test_truncates_to_token_budget(self) -> None:
        big_source = "word " * 10_000
        payload = build_evidence_payload(
            question="Q?",
            tool_call_log=[],
            fetched_sources=[big_source],
        )
        total_chars = sum(len(s) for s in payload.evidence_snippets)
        # 8000 tokens ~ 32000 chars; should be well under
        assert total_chars < 50_000

    def test_detects_count_answer_type(self) -> None:
        payload = build_evidence_payload(
            question="How many species?",
            tool_call_log=[{"type": "text", "content": "Found 6 species"}],
            fetched_sources=[],
        )
        assert payload.answer_type_hint in ("count", "unspecified")

    def test_empty_inputs_produce_valid_payload(self) -> None:
        payload = build_evidence_payload(
            question="Q?",
            tool_call_log=[],
            fetched_sources=[],
        )
        assert isinstance(payload, AnswerVerifierEvidencePayload)


# ---------------------------------------------------------------------------
# apply_answer_verifier
# ---------------------------------------------------------------------------


class TestApplyAnswerVerifier:
    def test_mode_off_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default-OFF: when env var not set, answer passes through unchanged."""
        monkeypatch.delenv("MAGI_ANSWER_VERIFIER_MODE", raising=False)
        payload = build_evidence_payload(
            question="Q?",
            tool_call_log=[],
            fetched_sources=[],
        )
        result = apply_answer_verifier(
            raw_answer="7",
            question="Q?",
            evidence=payload,
            model_provider=None,
        )
        assert result == "7"

    def test_mode_enforce_with_fake_provider_corrects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "enforce")

        def _fake_mismatch(_prompt: str) -> str:
            return (
                "VERDICT: MISMATCH\n"
                "CORRECTED_VALUE: 6\n"
                "EVIDENCE_BASIS: Found 6 items"
            )

        payload = build_evidence_payload(
            question="How many items?",
            tool_call_log=[{"type": "text", "content": "A B C D E F — 6 items"}],
            fetched_sources=[],
        )
        result = apply_answer_verifier(
            raw_answer="7",
            question="How many items?",
            evidence=payload,
            model_provider=_fake_mismatch,
        )
        assert result == "6"

    def test_fail_open_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """apply_answer_verifier must not raise — fail-open returns original."""
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "enforce")

        def _raises(_prompt: str) -> str:
            raise RuntimeError("model exploded")

        payload = build_evidence_payload(
            question="Q?", tool_call_log=[], fetched_sources=[]
        )
        result = apply_answer_verifier(
            raw_answer="original",
            question="Q?",
            evidence=payload,
            model_provider=_raises,
        )
        assert result == "original"

    def test_mode_audit_does_not_change_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "audit")

        def _fake_mismatch(_prompt: str) -> str:
            return (
                "VERDICT: MISMATCH\n"
                "CORRECTED_VALUE: 6\n"
                "EVIDENCE_BASIS: Found 6 items"
            )

        payload = build_evidence_payload(
            question="How many items?",
            tool_call_log=[],
            fetched_sources=[],
        )
        result = apply_answer_verifier(
            raw_answer="7",
            question="How many items?",
            evidence=payload,
            model_provider=_fake_mismatch,
        )
        # audit mode: original answer preserved
        assert result == "7"
