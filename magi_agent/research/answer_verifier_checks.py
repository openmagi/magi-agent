"""Answer Verifier Checks — detect type, build prompt, parse response, safety guards.

Anti-overfitting firewall: this module MUST NOT import from any benchmark
scoring layer.  Any PR that adds a benchmarks.gaia import is a violation.

Used exclusively by answer_verifier.py (and indirectly by the GAIA plugin).
"""
from __future__ import annotations

import re
from typing import NamedTuple

from magi_agent.research.answer_verifier import AnswerTypeHint

# ---------------------------------------------------------------------------
# Token budget: ~8 000 tokens ≈ 32 000 chars (conservative)
# ---------------------------------------------------------------------------

_EVIDENCE_CHAR_LIMIT = 32_000

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_DIGITS_RE = re.compile(r"^-?\d+(\.\d+)?$")
_HOW_MANY_RE = re.compile(r"\bhow\s+many\b", re.IGNORECASE)
_COUNT_KEYWORD_RE = re.compile(r"\bcount\b|\bnumber\s+of\b|\btotal\b", re.IGNORECASE)
_ORDINAL_KEYWORD_RE = re.compile(
    r"\bwhich\s+stanza\b|\bwhich\s+chapter\b|\bwhich\s+verse\b"
    r"|\brank\b|\bordinal\b|\bfirst\b|\bsecond\b|\bthird\b",
    re.IGNORECASE,
)
_ARITHMETIC_RE = re.compile(r"\bsum\b|\bproduct\b|\bdifference\b|\bquotient\b|\bcalculate\b|\bcompute\b", re.IGNORECASE)
_ENTITY_NAME_RE = re.compile(r"^[A-Z][A-Za-z,\.\s\-']+$")


# ---------------------------------------------------------------------------
# VerifierCheckResult (simple NamedTuple for internal use)
# ---------------------------------------------------------------------------


class VerifierCheckResult(NamedTuple):
    verdict: str          # "confirmed" | "mismatch"
    corrected_value: str | None
    evidence_basis: str


# ---------------------------------------------------------------------------
# detect_answer_type
# ---------------------------------------------------------------------------


def detect_answer_type(question: str, answer: str) -> AnswerTypeHint:
    """Infer the answer type hint from question text and answer content.

    Returns a best-effort AnswerTypeHint.  Falls back to 'unspecified'.
    """
    answer_stripped = answer.strip()

    # List: multiple comma-separated items
    if "," in answer_stripped:
        return "list"

    # Count check: how many / count / number of
    if _HOW_MANY_RE.search(question) or _COUNT_KEYWORD_RE.search(question):
        return "count"

    # Arithmetic
    if _ARITHMETIC_RE.search(question):
        return "arithmetic"

    # Ordinal
    if _ORDINAL_KEYWORD_RE.search(question):
        return "ordinal"

    # Pure digit → could be count
    if _DIGITS_RE.fullmatch(answer_stripped):
        return "count"

    # Multi-word with capitals → entity
    if _ENTITY_NAME_RE.fullmatch(answer_stripped) and " " in answer_stripped:
        return "entity"

    # Single word (or plural noun) → singular_plural
    if " " not in answer_stripped and answer_stripped.isalpha():
        return "singular_plural"

    return "unspecified"


# ---------------------------------------------------------------------------
# build_verifier_prompt
# ---------------------------------------------------------------------------


def build_verifier_prompt(
    *,
    question: str,
    final_answer: str,
    answer_type_hint: AnswerTypeHint,
    evidence_snippets: tuple[str, ...],
) -> str:
    """Construct the verification prompt for the LLM.

    The prompt instructs the model to:
    - use only the provided evidence (no new reasoning or search)
    - return VERDICT: CONFIRMED or VERDICT: MISMATCH
    - on MISMATCH: supply CORRECTED_VALUE and EVIDENCE_BASIS
    - when evidence is insufficient: return CONFIRMED (fail-open)
    """
    # Truncate evidence to fit token budget
    combined_evidence = "\n\n".join(evidence_snippets)
    if len(combined_evidence) > _EVIDENCE_CHAR_LIMIT:
        combined_evidence = combined_evidence[:_EVIDENCE_CHAR_LIMIT] + "\n[... truncated ...]"

    prompt = (
        "You are a final answer verifier. Your only job is to check whether the "
        "FINAL ANSWER value is supported by the EVIDENCE below.\n"
        "Rules:\n"
        "  1. Use ONLY the evidence provided — do not use outside knowledge or search.\n"
        "  2. If the evidence is insufficient or ambiguous, return CONFIRMED (fail-open).\n"
        "  3. Check the value (count, name, order, unit) against the evidence.\n"
        "  4. Return exactly one of the two verdict formats below.\n\n"
        f"ORIGINAL QUESTION: {question}\n"
        f"FINAL ANSWER: {final_answer}\n"
        f"ANSWER TYPE: {answer_type_hint}\n\n"
        "--- EVIDENCE ---\n"
        f"{combined_evidence}\n"
        "--- END EVIDENCE ---\n\n"
        "If the answer is supported by the evidence, respond with:\n"
        "VERDICT: CONFIRMED\n\n"
        "If the answer is NOT supported and you can determine the correct value "
        "from the evidence, respond with:\n"
        "VERDICT: MISMATCH\n"
        "CORRECTED_VALUE: <the correct value extracted from evidence>\n"
        "EVIDENCE_BASIS: <direct quote or specific reference from the evidence>\n\n"
        "Begin your response now (start with 'VERDICT:'):"
    )
    return prompt


# ---------------------------------------------------------------------------
# parse_verifier_response
# ---------------------------------------------------------------------------

_VERDICT_RE = re.compile(r"VERDICT\s*:\s*(CONFIRMED|MISMATCH)", re.IGNORECASE)
_CORRECTED_VALUE_RE = re.compile(r"CORRECTED_VALUE\s*:\s*(.+)", re.IGNORECASE)
_EVIDENCE_BASIS_RE = re.compile(r"EVIDENCE_BASIS\s*:\s*(.+)", re.IGNORECASE)


def parse_verifier_response(
    raw: str,
) -> tuple[str, str | None, str]:
    """Parse the LLM verifier response into (verdict, corrected_value, evidence_basis).

    Fail-open: any unparseable response → ("confirmed", None, "").
    If MISMATCH but no CORRECTED_VALUE → ("confirmed", None, "") [fail-open].
    """
    if not raw or not raw.strip():
        return ("confirmed", None, "")

    verdict_match = _VERDICT_RE.search(raw)
    if verdict_match is None:
        return ("confirmed", None, "")

    verdict = verdict_match.group(1).lower()

    if verdict == "confirmed":
        return ("confirmed", None, "")

    # MISMATCH — extract corrected value (required for correction to apply)
    corrected_match = _CORRECTED_VALUE_RE.search(raw)
    if corrected_match is None:
        # MISMATCH without a corrected value → fail-open
        return ("confirmed", None, "")

    corrected_value = corrected_match.group(1).strip()
    if not corrected_value:
        return ("confirmed", None, "")

    # Evidence basis (optional — empty string if absent)
    basis_match = _EVIDENCE_BASIS_RE.search(raw)
    evidence_basis = basis_match.group(1).strip() if basis_match else ""

    return ("mismatch", corrected_value, evidence_basis)


# ---------------------------------------------------------------------------
# safety_guard_check
# ---------------------------------------------------------------------------

_NUMERIC_RATIO_MIN = 0.5
_NUMERIC_RATIO_MAX = 2.0
_JACCARD_MIN = 0.2


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens for Jaccard computation."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _token_prefix_overlap(ta: set[str], tb: set[str]) -> int:
    """Count token pairs (a, b) where one is a prefix of the other (min 4 chars).

    This handles singular/plural and similar morphological variants:
    e.g. 'inference' is a prefix of 'inferences' → counted as overlap.
    """
    count = 0
    for a in ta:
        for b in tb:
            if a == b:
                continue
            min_len = min(len(a), len(b))
            if min_len >= 4 and (a.startswith(b[:min_len]) or b.startswith(a[:min_len])):
                count += 1
    return count


def _jaccard(a: str, b: str) -> float:
    """Extended Jaccard similarity between token sets of a and b.

    Counts exact token matches plus prefix-overlap pairs (for morphological
    variants like singular/plural: 'inference'/'inferences').
    """
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta and not tb:
        return 1.0
    union = ta | tb
    if not union:
        return 0.0
    exact_overlap = len(ta & tb)
    prefix_bonus = _token_prefix_overlap(ta - tb, tb - ta)
    # Cap total overlap at |union| to keep similarity in [0,1]
    effective_overlap = min(exact_overlap + prefix_bonus, len(union))
    return effective_overlap / len(union)


def safety_guard_check(original: str, corrected: str) -> bool:
    """Return True if the correction is safe (within bounds); False to reject.

    Guard A (numeric): if both values are numeric, corrected/original ratio
    must be in [0.5, 2.0].

    Guard B (text): Jaccard similarity between original and corrected must be
    >= 0.2.

    Special cases:
    - original == "0": numeric ratio is undefined; fall back to Jaccard.
    - If either value fails to parse as float: use Jaccard only.
    """
    orig_s = original.strip()
    corr_s = corrected.strip()

    # Try numeric guard first
    try:
        orig_f = float(orig_s)
        corr_f = float(corr_s)

        if orig_f == 0.0:
            # Ratio undefined → Jaccard fallback
            return _jaccard(orig_s, corr_s) >= _JACCARD_MIN

        ratio = corr_f / orig_f
        if ratio < _NUMERIC_RATIO_MIN or ratio > _NUMERIC_RATIO_MAX:
            return False
        return True

    except ValueError:
        pass

    # Text guard: Jaccard
    return _jaccard(orig_s, corr_s) >= _JACCARD_MIN


__all__ = [
    "VerifierCheckResult",
    "build_verifier_prompt",
    "detect_answer_type",
    "parse_verifier_response",
    "safety_guard_check",
]
