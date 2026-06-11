"""Forced-answer / no-abstention helpers for the GAIA benchmark harness.

DEPRECATED back-compat shim: the mechanism now lives first-party in
:mod:`magi_agent.runtime.best_effort_answer` (policy-gated via
``MAGI_ANSWER_POLICY``). This module preserves the original benchmark-layer
API (``is_abstention``, ``force_answer``) by delegating; new callers should
use :func:`magi_agent.runtime.best_effort_answer.finalize_answer` directly.

This module is BENCHMARK-LAYER only.  Production agents may legitimately
abstain; these helpers exist solely to prevent zero-score abstentions in a
GAIA evaluation context where an incorrect best-guess beats a non-answer —
hence the hardcoded ``commit`` policy below.
"""
from __future__ import annotations

from typing import Callable

from magi_agent.research.answer_policy import ANSWER_POLICY_ENV
from magi_agent.runtime.best_effort_answer import (
    BestEffortConfig,
    finalize_answer,
    is_non_answer,
)

# ---------------------------------------------------------------------------
# Abstention detection — re-export of the first-party generalization.
# ---------------------------------------------------------------------------

is_abstention = is_non_answer


# ---------------------------------------------------------------------------
# Forced-answer re-prompt
# ---------------------------------------------------------------------------


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

    The function is **fail-open**: any failure of *model_provider* results in
    *original* being returned unchanged.

    Delegates to :func:`magi_agent.runtime.best_effort_answer.finalize_answer`
    with an explicit ``commit`` policy (benchmark context — abstention scores
    zero) and no uncertainty label (the GAIA scorer needs bare answers).
    """
    final = finalize_answer(
        question,
        original,
        evidence,
        model_provider,
        env={ANSWER_POLICY_ENV: "commit"},
        config=BestEffortConfig(label_uncertainty=False),
    )
    return final.text


__all__ = ["is_abstention", "force_answer"]
