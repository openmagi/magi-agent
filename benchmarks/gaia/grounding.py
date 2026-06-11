"""GAIA benchmark-layer adapter for the grounded-answer guard.

BENCHMARK-LAYER only. This module decides *whether* to run the general
:mod:`magi_agent.research.grounded_answer_guard` for a GAIA run and packages its
verdict as OUT-OF-BAND metadata. It NEVER mutates the answer string returned by
:func:`benchmarks.gaia.answer.extract_final_answer` / scored by
:func:`benchmarks.gaia.scorer.question_scorer`.

This preserves the GAIA forced-answer philosophy ("an incorrect best-guess
beats a non-answer"): a coincidentally-correct guess still scores 1, because the
scorer always sees the bare committed answer. The grounding verdict is recorded
separately (``verifierEvidenceStatus``) for ledger/analysis only.

The guard runs AFTER a committed answer exists (the caller passes the committed
answer + the tool corpus it collected), so it never re-triggers abstention or
empties the answer.

GAIA-specific concerns (advertisement text) would live in the GAIA prompt layer
only; the grounding *logic* here is provider/benchmark-neutral.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from magi_agent.config.env import is_grounded_answer_guard_enabled
from magi_agent.research.grounded_answer_guard import evaluate_answer_grounding


def gaia_grounding_metadata(
    *,
    answer: str,
    tool_corpus: Iterable[str],
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return out-of-band grounding metadata for a committed GAIA *answer*.

    Returns an empty dict (zero behaviour change) when the
    ``MAGI_GROUNDED_ANSWER_GUARD_ENABLED`` flag is OFF. When ON, returns the
    verdict metadata (``verifierEvidenceStatus`` etc.) for the committed answer.

    The returned dict is metadata only: the caller must NOT inject it into the
    scored answer string.
    """
    if not is_grounded_answer_guard_enabled(env):
        return {}
    verdict = evaluate_answer_grounding(answer, tool_corpus)
    return verdict.as_metadata()


__all__ = ["gaia_grounding_metadata"]
