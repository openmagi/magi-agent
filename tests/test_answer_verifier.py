"""Tests for AnswerVerifier — value-level verification gate (PR 1 + PR 2 + PR 3).

Anti-overfitting firewall: this file MUST NOT import anything from
magi_agent.benchmarks.  A dedicated test enforces this at the module level.

TDD: tests were written BEFORE the implementation.  Run order:
  RED   — tests fail (module doesn't exist)
  GREEN — tests pass after answer_verifier.py is written
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest

from magi_agent.research.answer_verifier import (
    AnswerTypeHint,
    AnswerVerifierEvidencePayload,
    AnswerVerifierExecutionPosture,
    AnswerVerifierMode,
    AnswerVerifierRequest,
    AnswerVerifierResult,
    AnswerVerifierStatus,
    evaluate_answer_verifier,
)


# ---------------------------------------------------------------------------
# Anti-overfitting firewall
# ---------------------------------------------------------------------------


def test_answer_verifier_does_not_import_gaia_scorer() -> None:
    """answer_verifier.py must not import from benchmarks.gaia.scorer — structural firewall."""
    mod = importlib.import_module("magi_agent.research.answer_verifier")
    scorer_name = "benchmarks.gaia.scorer"

    # Check module-level attribute references
    imported_names: set[str] = set()
    for attr in dir(mod):
        val = getattr(mod, attr, None)
        if isinstance(val, types.ModuleType):
            imported_names.add(val.__name__)
    assert scorer_name not in imported_names

    # Check source for actual import statements only (docstring mentions are OK)
    if mod.__file__:
        with open(mod.__file__) as fh:
            source = fh.read()
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from ")) and "benchmarks.gaia" in line
        ]
        assert import_lines == [], (
            f"answer_verifier.py must not import from benchmarks.gaia: {import_lines}"
        )


def test_answer_verifier_checks_does_not_import_gaia_scorer() -> None:
    """answer_verifier_checks.py must not import from benchmarks.gaia.scorer."""
    mod = importlib.import_module("magi_agent.research.answer_verifier_checks")
    if mod.__file__:
        with open(mod.__file__) as fh:
            source = fh.read()
        # Check for actual import statements only (not docstring references)
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith(("import ", "from ")) and "benchmarks.gaia" in line
        ]
        assert import_lines == [], (
            f"answer_verifier_checks.py must not import from benchmarks.gaia: {import_lines}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payload(
    snippets: tuple[str, ...] = ("evidence text",),
    *,
    question: str = "How many items?",
    final_answer: str = "7",
    answer_type: AnswerTypeHint = "count",
) -> AnswerVerifierEvidencePayload:
    return AnswerVerifierEvidencePayload(
        question=question,
        final_answer=final_answer,
        evidence_snippets=snippets,
        answer_type_hint=answer_type,
    )


class _FakeProvider:
    """Fake model provider: returns a fixed string for any prompt."""

    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, prompt: str) -> str:  # noqa: ARG002
        return self._response


def _fake_confirmed(_prompt: str) -> str:
    return "VERDICT: CONFIRMED"


def _fake_mismatch_count(_prompt: str) -> str:
    return (
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: 6\n"
        "EVIDENCE_BASIS: Evidence lists A, B, C, D, E, F — total 6 species"
    )


def _fake_mismatch_plural(_prompt: str) -> str:
    return (
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: inference\n"
        "EVIDENCE_BASIS: Source uses singular 'inference' not 'inferences'"
    )


def _fake_mismatch_entity(_prompt: str) -> str:
    return (
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: Braintree, Honolulu\n"
        "EVIDENCE_BASIS: Quincy was historically known as Braintree; correct order is Braintree then Honolulu"
    )


def _fake_mismatch_ordinal(_prompt: str) -> str:
    return (
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: 2\n"
        "EVIDENCE_BASIS: Evidence shows the event occurred in stanza 2, not stanza 1"
    )


def _fake_mismatch_extreme(_prompt: str) -> str:
    # Proposes wildly different number — safety guard should reject
    return (
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: 700\n"
        "EVIDENCE_BASIS: Some extreme claim"
    )


def _fake_mismatch_extreme_text(_prompt: str) -> str:
    # Proposes completely different text — Jaccard guard should reject
    return (
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: completely unrelated xyz answer nothing in common\n"
        "EVIDENCE_BASIS: Some wild claim"
    )


def _make_request(
    *,
    mode: AnswerVerifierMode = "enforce",
    final_answer: str = "7",
    question: str = "How many non-indigenous crocodile species?",
    snippets: tuple[str, ...] = ("Found species: A, B, C, D, E, F",),
    answer_type: AnswerTypeHint = "count",
    provider_fn: object = None,
) -> AnswerVerifierRequest:
    payload = _make_payload(
        snippets,
        question=question,
        final_answer=final_answer,
        answer_type=answer_type,
    )
    return AnswerVerifierRequest(
        verifier_id="test-verifier",
        mode=mode,
        question=question,
        final_answer=final_answer,
        evidence_payload=payload,
        model_provider=provider_fn,
    )


# ---------------------------------------------------------------------------
# Mode=off → passthrough (default-OFF)
# ---------------------------------------------------------------------------


def test_mode_off_returns_skipped() -> None:
    req = _make_request(mode="off")
    result = evaluate_answer_verifier(req)
    assert result.status == "skipped"
    assert result.verified_answer == "7"
    assert not result.correction_applied
    assert result.ok


def test_mode_off_no_model_call() -> None:
    """mode=off must not call the model provider even if one is supplied."""
    called: list[str] = []

    def _spy(prompt: str) -> str:
        called.append(prompt)
        return "VERDICT: CONFIRMED"

    req = _make_request(mode="off", provider_fn=_spy)
    evaluate_answer_verifier(req)
    assert called == [], "mode=off must not invoke model provider"


# ---------------------------------------------------------------------------
# mode=enforce — confirmed (no correction)
# ---------------------------------------------------------------------------


def test_enforce_confirmed_returns_original() -> None:
    req = _make_request(mode="enforce", final_answer="7", provider_fn=_fake_confirmed)
    result = evaluate_answer_verifier(req)
    assert result.status == "confirmed"
    assert result.verified_answer == "7"
    assert not result.correction_applied
    assert result.ok


def test_enforce_no_provider_is_skipped() -> None:
    """When mode=enforce but no provider is given, fail-open (skipped)."""
    req = _make_request(mode="enforce", final_answer="7", provider_fn=None)
    result = evaluate_answer_verifier(req)
    assert result.status == "skipped"
    assert result.verified_answer == "7"
    assert result.ok


# ---------------------------------------------------------------------------
# mode=enforce — mismatch corrections (near-miss recovery)
# ---------------------------------------------------------------------------


def test_enforce_count_mismatch_corrects_7_to_6() -> None:
    """Planted near-miss: off-by-one count 7→6 should be corrected."""
    req = _make_request(
        mode="enforce",
        final_answer="7",
        snippets=("Found species: A, B, C, D, E, F",),
        answer_type="count",
        provider_fn=_fake_mismatch_count,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "mismatch_corrected"
    assert result.verified_answer == "6"
    assert result.correction_applied
    assert result.ok


def test_enforce_singular_plural_corrects_inferences_to_inference() -> None:
    """Planted near-miss: plural 'inferences'→singular 'inference' should be corrected."""
    req = _make_request(
        mode="enforce",
        final_answer="inferences",
        question="What is the term used?",
        snippets=("The study uses the term 'inference' throughout",),
        answer_type="singular_plural",
        provider_fn=_fake_mismatch_plural,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "mismatch_corrected"
    assert result.verified_answer == "inference"
    assert result.correction_applied


def test_enforce_entity_equiv_corrects_quincy_to_braintree() -> None:
    """Planted near-miss: entity equiv — Quincy was historically Braintree."""
    req = _make_request(
        mode="enforce",
        final_answer="Honolulu, Quincy",
        question="What were the birthplaces in order?",
        snippets=("Born in Braintree (now Quincy), then moved to Honolulu",),
        answer_type="entity",
        provider_fn=_fake_mismatch_entity,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "mismatch_corrected"
    assert result.verified_answer == "Braintree, Honolulu"
    assert result.correction_applied


def test_enforce_ordinal_corrects_stanza_1_to_2() -> None:
    """Planted near-miss: ordinal stanza 1→2."""
    req = _make_request(
        mode="enforce",
        final_answer="1",
        question="In which stanza does the event occur?",
        snippets=("The event is described in the second stanza of the poem",),
        answer_type="ordinal",
        provider_fn=_fake_mismatch_ordinal,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "mismatch_corrected"
    assert result.verified_answer == "2"
    assert result.correction_applied


# ---------------------------------------------------------------------------
# Safety guards — over-correction protection
# ---------------------------------------------------------------------------


def test_safety_guard_rejects_extreme_numeric_correction() -> None:
    """Guard A: numeric correction >50% ratio from original must be refused."""
    # original "7", proposed "700" — ratio 700/7 = 100x, well outside [0.5, 2.0]
    req = _make_request(
        mode="enforce",
        final_answer="7",
        snippets=("some evidence",),
        answer_type="count",
        provider_fn=_fake_mismatch_extreme,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "mismatch_refused"
    assert result.verified_answer == "7"
    assert not result.correction_applied
    assert result.ok


def test_safety_guard_rejects_extreme_text_correction() -> None:
    """Guard B: text correction with Jaccard < 0.2 from original must be refused."""
    req = _make_request(
        mode="enforce",
        final_answer="inference",
        question="What term is used?",
        snippets=("some evidence",),
        answer_type="singular_plural",
        provider_fn=_fake_mismatch_extreme_text,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "mismatch_refused"
    assert result.verified_answer == "inference"
    assert not result.correction_applied
    assert result.ok


def test_correct_answer_not_changed_by_confirmed_verdict() -> None:
    """A correct answer returned as CONFIRMED must not be modified."""
    req = _make_request(
        mode="enforce",
        final_answer="6",
        snippets=("Found species: A, B, C, D, E, F — total 6",),
        answer_type="count",
        provider_fn=_fake_confirmed,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "confirmed"
    assert result.verified_answer == "6"
    assert not result.correction_applied


# ---------------------------------------------------------------------------
# mode=audit — log only, no correction
# ---------------------------------------------------------------------------


def test_audit_mode_does_not_apply_correction() -> None:
    """mode=audit: mismatch is found but no correction is applied."""
    req = _make_request(
        mode="audit",
        final_answer="7",
        snippets=("Found species: A, B, C, D, E, F",),
        answer_type="count",
        provider_fn=_fake_mismatch_count,
    )
    result = evaluate_answer_verifier(req)
    assert result.status == "audit"
    # Audit records the mismatch but returns original answer unchanged
    assert result.verified_answer == "7"
    assert not result.correction_applied
    assert result.ok


def test_audit_mode_confirmed_still_ok() -> None:
    req = _make_request(mode="audit", provider_fn=_fake_confirmed)
    result = evaluate_answer_verifier(req)
    assert result.ok
    assert result.status == "audit"
    assert result.verified_answer == "7"


# ---------------------------------------------------------------------------
# Data model validation
# ---------------------------------------------------------------------------


def test_execution_posture_default_off() -> None:
    posture = AnswerVerifierExecutionPosture()
    assert posture.default_off is True
    assert posture.live_search_allowed is False


def test_result_answer_digest_is_sha256() -> None:
    req = _make_request(mode="off")
    result = evaluate_answer_verifier(req)
    assert result.answer_digest.startswith("sha256:")
    assert len(result.answer_digest) == len("sha256:") + 64


def test_result_verifier_id_preserved() -> None:
    req = _make_request(mode="off")
    result = evaluate_answer_verifier(req)
    assert result.verifier_id == "test-verifier"


def test_evidence_payload_immutable() -> None:
    payload = _make_payload()
    with pytest.raises((AttributeError, TypeError)):
        payload.question = "changed"  # type: ignore[misc]
