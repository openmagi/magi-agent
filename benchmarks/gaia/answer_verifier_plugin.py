"""GAIA Answer Verifier Plugin — evidence payload construction + fail-open wrapper.

This module is the ONLY place where the general answer_verifier gate is wired
to GAIA-specific inputs (tool_call_log, fetched_sources).

The answer_verifier.py module MUST remain benchmark-agnostic.

Environment variable: MAGI_ANSWER_VERIFIER_MODE
  values: off | audit | enforce   (default: off when unset)
  Audit-first: truthy values (1/true/yes/on) resolve to "audit" — never to
  "enforce".  Use the explicit string "enforce" to opt into mutation.
  See ``magi_agent.research.answer_verifier.read_verifier_mode_from_env``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from magi_agent.research.answer_verifier import (
    AnswerVerifierEvidencePayload,
    AnswerVerifierRequest,
    evaluate_answer_verifier,
    read_verifier_mode_from_env,
)
from magi_agent.research.answer_verifier_checks import detect_answer_type

_logger = logging.getLogger(__name__)

# Evidence budget: ~8 000 tokens ≈ 32 000 chars
_EVIDENCE_CHAR_BUDGET = 32_000

# Default verifier ID for GAIA runs
_GAIA_VERIFIER_ID = "gaia-answer-verifier"


def _get_mode() -> str:
    return read_verifier_mode_from_env()


# ---------------------------------------------------------------------------
# Evidence payload builder
# ---------------------------------------------------------------------------


def build_evidence_payload(
    *,
    question: str,
    tool_call_log: list[dict[str, Any]],
    fetched_sources: list[str],
) -> AnswerVerifierEvidencePayload:
    """Construct an evidence payload from tool call logs and fetched source texts.

    Applies the token budget cap (32 000 chars) across all snippets combined.
    Detects the answer type from the question.

    Parameters
    ----------
    question:
        Original question text.
    tool_call_log:
        List of tool call entries.  Each entry is a dict with at minimum a
        "content" key (str) and optionally a "type" key.
    fetched_sources:
        Raw source texts fetched by the agent (e.g. from web_search results).

    Returns
    -------
    AnswerVerifierEvidencePayload
    """
    snippets: list[str] = []
    total_chars = 0

    # Collect text from tool call log first (most structured / relevant)
    for entry in tool_call_log:
        content = entry.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        remaining = _EVIDENCE_CHAR_BUDGET - total_chars
        if remaining <= 0:
            break
        chunk = content[:remaining]
        snippets.append(chunk)
        total_chars += len(chunk)

    # Then add fetched source text
    for source_text in fetched_sources:
        if not isinstance(source_text, str) or not source_text.strip():
            continue
        remaining = _EVIDENCE_CHAR_BUDGET - total_chars
        if remaining <= 0:
            break
        chunk = source_text[:remaining]
        snippets.append(chunk)
        total_chars += len(chunk)

    # Detect answer type from question (answer not known here, use question only)
    # We pass an empty string for the answer — detect_answer_type will use the question
    answer_type = detect_answer_type(question, "")

    return AnswerVerifierEvidencePayload(
        question=question,
        final_answer="",  # filled in by apply_answer_verifier
        evidence_snippets=tuple(snippets),
        answer_type_hint=answer_type,
    )


# ---------------------------------------------------------------------------
# Fail-open apply wrapper (used by full_capability pipeline)
# ---------------------------------------------------------------------------


def apply_answer_verifier(
    *,
    raw_answer: str,
    question: str,
    evidence: AnswerVerifierEvidencePayload,
    model_provider: Callable[[str], str] | None,
) -> str:
    """Apply the answer verifier to raw_answer and return the (possibly corrected) answer.

    This wrapper is fail-open: any exception → returns raw_answer unchanged.

    mode=off  (default): returns raw_answer immediately (no LLM call).
    mode=audit:          calls LLM, logs mismatch, returns raw_answer unchanged.
    mode=enforce:        calls LLM, applies correction if safe.

    Parameters
    ----------
    raw_answer:
        The agent's draft answer string.
    question:
        Original question text.
    evidence:
        Evidence payload (from build_evidence_payload).
    model_provider:
        Callable(prompt: str) -> str, or None.  Required for audit/enforce.

    Returns
    -------
    str
        Verified (possibly corrected) answer, or raw_answer on any failure.
    """
    mode = _get_mode()

    if mode == "off":
        return raw_answer

    # Rebuild payload with the actual final_answer for the request
    # (build_evidence_payload was called without knowing the answer yet)
    filled_evidence = AnswerVerifierEvidencePayload(
        question=evidence.question,
        final_answer=raw_answer,
        evidence_snippets=evidence.evidence_snippets,
        answer_type_hint=detect_answer_type(question, raw_answer),
    )

    request = AnswerVerifierRequest(
        verifier_id=_GAIA_VERIFIER_ID,
        mode=mode,  # type: ignore[arg-type]
        question=question,
        final_answer=raw_answer,
        evidence_payload=filled_evidence,
        model_provider=model_provider,
    )

    try:
        result = evaluate_answer_verifier(request)
    except Exception as exc:  # noqa: BLE001
        _logger.warning(
            "answer_verifier: exception during evaluation (fail-open): %s", exc
        )
        return raw_answer

    if result.status == "mismatch_corrected" and result.correction_applied:
        _logger.info(
            "answer_verifier: corrected %r → %r (evidence: %s)",
            raw_answer,
            result.verified_answer,
            result.evidence_basis[:120] if result.evidence_basis else "(none)",
        )
        return result.verified_answer

    if result.status in ("mismatch_refused",):
        _logger.info(
            "answer_verifier: mismatch refused (safety guard) for answer=%r",
            raw_answer,
        )

    return raw_answer


__all__ = ["apply_answer_verifier", "build_evidence_payload"]
