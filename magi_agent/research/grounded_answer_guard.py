"""General grounded-answer guard (anti-fabrication lever).

This module answers ONE general agent-honesty question, with no benchmark- or
provider-specific logic:

    Does a committed answer assert a *specific* numeric or identifier value
    that is NOT supported anywhere in the tool/evidence corpus the agent
    actually collected?

When a required source cannot be reached by the available tools (the motivating
case: a YouTube view count where ``VideoFrames`` / ``AudioTranscribe`` are
local-file-only), agents sometimes fabricate a plausible specific value with
false confidence. This detector distinguishes a *grounded* answer (the value
appears in the collected corpus) from a *guess* (a specific value with no
corroborating evidence) so the runtime can flag the answer as an ungrounded
guess instead of asserting a fabricated value.

Design notes (per review):
- This is a NEW detector built on the harness's own collected tool corpus. It
  deliberately does NOT reuse
  ``shadow.fact_grounding_verifier_contract.deterministic_fact_grounding_verdict``:
  that contract's Mode A returns GROUNDED/"no_tool_results" on an empty corpus
  and only emits DISTORTED on a number *mismatch*; it has no FABRICATED path for
  a bare numeric value, so it cannot fire on the motivating case.
- The detector is pure and side-effect-free. It performs no I/O, no network, no
  model call. Activation/gating lives in the caller (the harness / CLI layer).
- The verdict is metadata. Whether it mutates a string (allowed only on
  non-scored chat/CLI surfaces via :func:`apply_guess_label`) or is recorded
  out-of-band (required for the GAIA scored arm) is the caller's decision.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

GroundedAnswerStatus = Literal["grounded", "guess"]

# A "specific value" worth grounding is either:
#   - a number with at least 3 significant digits (counts, ids, large figures),
#     so small/common values (years, single/double digits) are not flagged; or
#   - a hyphenated/dotted identifier token (e.g. gpt-4o-mini, claude-opus-4-7).
# These are deliberately conservative so the guard does not fire on ordinary
# best-effort answers (protecting the forced-answer philosophy).
_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z][\w.]*-[\w.-]+\b")

_GUESS_LABEL = "GUESS:"


@dataclass(frozen=True)
class GroundedAnswerVerdict:
    """Result of grounding a committed answer against a tool corpus."""

    status: GroundedAnswerStatus
    reason_code: str
    extracted_value: str | None = None

    def as_metadata(self) -> dict[str, str]:
        """Out-of-band metadata projection (``verifierEvidenceStatus``).

        Never includes the scored answer string under an answer-like key — this
        is metadata only, suitable for a ledger / response side-channel.
        """
        meta: dict[str, str] = {
            "verifierEvidenceStatus": self.status,
            "groundedAnswerGuard": self.reason_code,
        }
        if self.extracted_value is not None:
            meta["extractedValue"] = self.extracted_value
        return meta


def _normalize_digits(value: str) -> str:
    """Strip grouping separators so '776,665' and '776665' compare equal."""
    return value.replace(",", "").replace(" ", "")


def _extract_specific_value(answer: str) -> tuple[str, str] | None:
    """Return ``(kind, value)`` for the most salient specific value, or None.

    ``kind`` is ``"number"`` or ``"identifier"``. Numbers are preferred when
    both appear because a fabricated count is the canonical failure mode.
    """
    for match in _NUMBER_RE.finditer(answer):
        raw = match.group(0)
        digits = _normalize_digits(raw)
        # Require >=3 significant digits to count as "specific".
        if len(digits.lstrip("0").replace(".", "")) >= 3:
            return ("number", raw.strip())
    ident = _IDENTIFIER_RE.search(answer)
    if ident is not None:
        return ("identifier", ident.group(0))
    return None


def _corpus_supports(kind: str, value: str, corpus_text: str) -> bool:
    if kind == "number":
        target = _normalize_digits(value)
        # Compare against every number token in the corpus (separator-agnostic).
        for match in _NUMBER_RE.finditer(corpus_text):
            if _normalize_digits(match.group(0)) == target:
                return True
        return False
    # identifier: case-insensitive substring is sufficient evidence.
    return value.lower() in corpus_text.lower()


def evaluate_answer_grounding(
    answer: str,
    tool_corpus: Iterable[str],
) -> GroundedAnswerVerdict:
    """Decide whether *answer* is grounded in *tool_corpus*.

    Returns a GUESS verdict only when BOTH hold:
      1. the answer asserts a specific numeric/identifier value, AND
      2. that value is supported by NO entry in the collected corpus.

    Otherwise returns a GROUNDED verdict (including the no-specific-value and
    empty-answer no-ops), so legitimate best-effort answers are never flagged.
    """
    if not answer or not answer.strip():
        return GroundedAnswerVerdict(
            status="grounded",
            reason_code="no_specific_value_to_ground",
            extracted_value=None,
        )

    extracted = _extract_specific_value(answer)
    if extracted is None:
        return GroundedAnswerVerdict(
            status="grounded",
            reason_code="no_specific_value_to_ground",
            extracted_value=None,
        )

    kind, value = extracted
    corpus_text = "\n".join(str(item) for item in tool_corpus)

    if _corpus_supports(kind, value, corpus_text):
        return GroundedAnswerVerdict(
            status="grounded",
            reason_code="value_supported_by_corpus",
            extracted_value=value,
        )

    return GroundedAnswerVerdict(
        status="guess",
        reason_code="specific_value_unsupported_by_corpus",
        extracted_value=value,
    )


def apply_guess_label(answer: str, verdict: GroundedAnswerVerdict) -> str:
    """Prefix a literal ``GUESS:`` label on a NON-SCORED surface only.

    This is for chat/CLI display where flagging an ungrounded guess is helpful.
    It MUST NOT be used on the GAIA scored-answer path (where it would corrupt
    the scored string). Idempotent: never double-prefixes.
    """
    if verdict.status != "guess":
        return answer
    if answer.lstrip().startswith(_GUESS_LABEL):
        return answer
    return f"{_GUESS_LABEL} {answer}"


__all__ = [
    "GroundedAnswerStatus",
    "GroundedAnswerVerdict",
    "evaluate_answer_grounding",
    "apply_guess_label",
]
