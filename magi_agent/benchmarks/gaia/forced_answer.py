"""Forced-answer / no-abstention helpers for the GAIA benchmark harness.

This module is BENCHMARK-LAYER only.  Production agents may legitimately
abstain; these helpers exist solely to prevent zero-score abstentions in a
GAIA evaluation context where an incorrect best-guess beats a non-answer.
"""
from __future__ import annotations

import re
from typing import Callable

# ---------------------------------------------------------------------------
# Abstention detection
# ---------------------------------------------------------------------------

_ABSTENTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"unable\s+to\s+(determine|answer|provide|find)", re.IGNORECASE),
    re.compile(r"cannot\s+determine", re.IGNORECASE),
    re.compile(r"can\s+not\s+determine", re.IGNORECASE),
    re.compile(r"not\s+able\s+to\s+(determine|answer|provide)", re.IGNORECASE),
    re.compile(r"insufficient\s+information", re.IGNORECASE),
    re.compile(r"awaiting\b", re.IGNORECASE),
    re.compile(r"^$"),  # empty string (matched after strip)
]


def is_abstention(text: str) -> bool:
    """Return ``True`` if *text* looks like an abstention or non-answer.

    Matches common hedging phrases emitted by language models when they decline
    to commit to a single answer (e.g. "unable to determine", "cannot determine",
    "insufficient information", "awaiting approval", or an empty/whitespace-only
    string).

    Parameters
    ----------
    text:
        The extracted answer candidate (already stripped by the caller, but the
        function also strips internally for safety).

    Returns
    -------
    bool
        ``True`` when the text is empty or matches a known abstention pattern.
    """
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in _ABSTENTION_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


# ---------------------------------------------------------------------------
# Forced-answer re-prompt
# ---------------------------------------------------------------------------

_FORCE_PROMPT_TEMPLATE = (
    "You previously gathered the following evidence about the question below.\n\n"
    "QUESTION:\n{question}\n\n"
    "EVIDENCE:\n{evidence}\n\n"
    "Based on the above, give your single best-guess final answer now. "
    "Output ONLY the answer — no hedging, no explanation, no 'I cannot determine'. "
    "If you are uncertain, still provide your single most probable best guess."
)


def force_answer(
    question: str,
    evidence: str,
    model_provider: Callable[[str], str],
    *,
    original: str = "",
) -> str:
    """Re-prompt *model_provider* to commit to a single best-guess answer.

    Call this when ``extract_final_answer`` returns an empty string or when
    :func:`is_abstention` returns ``True`` for the extracted answer.

    The function is **fail-open**: any exception raised by *model_provider* is
    swallowed and *original* is returned unchanged.

    Parameters
    ----------
    question:
        The original GAIA question text.
    evidence:
        All text gathered by the agent during its run (used as context).
    model_provider:
        A callable ``(prompt: str) -> str`` that calls the underlying model.
        Tests supply a fake; production callers inject the real provider.
    original:
        The original (abstaining / empty) answer.  Returned on exception.

    Returns
    -------
    str
        The model's best-guess answer, or *original* if *model_provider* raises.
    """
    prompt = _FORCE_PROMPT_TEMPLATE.format(
        question=question,
        evidence=evidence if evidence.strip() else "(no additional evidence gathered)",
    )
    try:
        return model_provider(prompt).strip()
    except Exception:  # noqa: BLE001 — fail-open by design
        return original


__all__ = ["is_abstention", "force_answer"]
