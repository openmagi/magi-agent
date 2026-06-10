"""Tests for P5 audit-first default on the answer verifier.

TDD: these tests are written BEFORE the implementation changes.
RED → GREEN cycle.

Principle 5 (GAIA learnings): self-correction defaults to *audit* (observe/log,
never mutate); *enforce* must be explicit.

Tested behaviour
----------------
* ``read_verifier_mode_from_env`` resolves the env var correctly:
  - unset / "off"        → "off"      (outer gate still OFF by default)
  - "1" / "true" / "on" → "audit"    (truthy = enable, but safe mode)
  - "audit"              → "audit"    (explicit audit)
  - "enforce"            → "enforce"  (explicit enforce — opt-in only)
  - unknown value        → "audit"    (unknown truthy → safe fallback)
* In audit mode a correct answer is NEVER mutated, even when the LLM returns
  a MISMATCH response.
* In audit mode the request's verified_answer equals the original answer.
"""
from __future__ import annotations

import os

import pytest

from magi_agent.research.answer_verifier import (
    AnswerVerifierEvidencePayload,
    AnswerVerifierRequest,
    evaluate_answer_verifier,
    read_verifier_mode_from_env,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    mode: str,
    *,
    final_answer: str = "7",
    provider_fn: object = None,
) -> AnswerVerifierRequest:
    payload = AnswerVerifierEvidencePayload(
        question="How many items?",
        final_answer=final_answer,
        evidence_snippets=("Found 6 items: A B C D E F",),
        answer_type_hint="count",
    )
    return AnswerVerifierRequest(
        verifier_id="audit-default-test",
        mode=mode,  # type: ignore[arg-type]
        question="How many items?",
        final_answer=final_answer,
        evidence_payload=payload,
        model_provider=provider_fn,
    )


def _fake_mismatch(_prompt: str) -> str:
    return (
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: 6\n"
        "EVIDENCE_BASIS: Found 6 items"
    )


def _fake_confirmed(_prompt: str) -> str:
    return "VERDICT: CONFIRMED"


# ---------------------------------------------------------------------------
# read_verifier_mode_from_env — env-var resolution
# ---------------------------------------------------------------------------


class TestReadVerifierModeFromEnv:
    """read_verifier_mode_from_env resolves MAGI_ANSWER_VERIFIER_MODE."""

    def test_unset_returns_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_ANSWER_VERIFIER_MODE", raising=False)
        assert read_verifier_mode_from_env() == "off"

    def test_explicit_off_returns_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "off")
        assert read_verifier_mode_from_env() == "off"

    def test_truthy_1_returns_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Generic enable signal '1' must resolve to the safe 'audit' mode."""
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "1")
        assert read_verifier_mode_from_env() == "audit"

    def test_truthy_true_returns_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "true")
        assert read_verifier_mode_from_env() == "audit"

    def test_truthy_yes_returns_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "yes")
        assert read_verifier_mode_from_env() == "audit"

    def test_truthy_on_returns_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "on")
        assert read_verifier_mode_from_env() == "audit"

    def test_explicit_audit_returns_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "audit")
        assert read_verifier_mode_from_env() == "audit"

    def test_explicit_enforce_returns_enforce(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'enforce' must be explicitly requested — it is the opt-in mode."""
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "enforce")
        assert read_verifier_mode_from_env() == "enforce"

    def test_unknown_value_returns_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any unknown non-empty, non-off value falls back to the safe 'audit' mode."""
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "unknown_mode")
        assert read_verifier_mode_from_env() == "audit"

    def test_empty_string_returns_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "")
        assert read_verifier_mode_from_env() == "off"

    def test_case_insensitive_enforce(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "ENFORCE")
        assert read_verifier_mode_from_env() == "enforce"

    def test_case_insensitive_audit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "AUDIT")
        assert read_verifier_mode_from_env() == "audit"

    def test_accepts_explicit_env_mapping(self) -> None:
        """read_verifier_mode_from_env accepts an explicit env mapping."""
        assert read_verifier_mode_from_env(env={"MAGI_ANSWER_VERIFIER_MODE": "enforce"}) == "enforce"
        assert read_verifier_mode_from_env(env={}) == "off"
        assert read_verifier_mode_from_env(env={"MAGI_ANSWER_VERIFIER_MODE": "1"}) == "audit"


# ---------------------------------------------------------------------------
# Audit mode never mutates a correct answer
# ---------------------------------------------------------------------------


class TestAuditModeNeverMutates:
    """In audit mode a correct answer is NEVER changed, regardless of LLM verdict."""

    def test_audit_never_mutates_on_mismatch(self) -> None:
        """Audit mode: LLM says MISMATCH but verified_answer == original."""
        req = _make_request("audit", final_answer="7", provider_fn=_fake_mismatch)
        result = evaluate_answer_verifier(req)
        assert result.verified_answer == "7", (
            "audit mode must not mutate the answer even when LLM returns MISMATCH"
        )
        assert not result.correction_applied
        assert result.status == "audit"

    def test_audit_never_mutates_on_confirmed(self) -> None:
        """Audit mode: confirmed verdict → original answer preserved."""
        req = _make_request("audit", final_answer="6", provider_fn=_fake_confirmed)
        result = evaluate_answer_verifier(req)
        assert result.verified_answer == "6"
        assert not result.correction_applied

    def test_audit_ok_is_always_true(self) -> None:
        """Audit mode never blocks: ok is always True."""
        for fn in (_fake_mismatch, _fake_confirmed):
            req = _make_request("audit", provider_fn=fn)
            result = evaluate_answer_verifier(req)
            assert result.ok, "audit mode must always return ok=True"

    def test_enforce_still_corrects(self) -> None:
        """enforce mode continues to apply safe corrections (guards still active)."""
        req = _make_request("enforce", final_answer="7", provider_fn=_fake_mismatch)
        result = evaluate_answer_verifier(req)
        # 7 → 6 is within safety bounds (ratio 6/7 ≈ 0.86, in [0.5, 2.0])
        assert result.status == "mismatch_corrected"
        assert result.verified_answer == "6"
        assert result.correction_applied


# ---------------------------------------------------------------------------
# answer_verifier_plugin uses read_verifier_mode_from_env
# ---------------------------------------------------------------------------


class TestPluginUsesAuditDefault:
    """The GAIA plugin wrapper respects the audit-first default via the helper."""

    def test_plugin_truthy_env_does_not_mutate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When MAGI_ANSWER_VERIFIER_MODE=1, plugin should use audit (no mutation)."""
        monkeypatch.setenv("MAGI_ANSWER_VERIFIER_MODE", "1")

        from magi_agent.benchmarks.gaia.answer_verifier_plugin import (
            apply_answer_verifier,
            build_evidence_payload,
        )

        payload = build_evidence_payload(
            question="How many items?",
            tool_call_log=[{"type": "text", "content": "Found 6 items A B C D E F"}],
            fetched_sources=[],
        )

        def _mismatch(_prompt: str) -> str:
            return (
                "VERDICT: MISMATCH\n"
                "CORRECTED_VALUE: 6\n"
                "EVIDENCE_BASIS: Found 6 items"
            )

        result = apply_answer_verifier(
            raw_answer="7",
            question="How many items?",
            evidence=payload,
            model_provider=_mismatch,
        )
        # audit mode → no mutation
        assert result == "7", (
            "With MAGI_ANSWER_VERIFIER_MODE=1 (truthy) the plugin should use audit "
            "mode and NOT mutate the answer"
        )
