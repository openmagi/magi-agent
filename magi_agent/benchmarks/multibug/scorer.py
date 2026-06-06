"""Pure scorer for the multi-problem discovery harness (TIDE §3.3).

Zero I/O, zero model calls (mirrors ``magi_agent/benchmarks/gaia/scorer.py``).
The retrieval metrics are deterministic and based on evidence-id overlap; the
optional identification / resolution metrics are delegated to an INJECTABLE
``judge`` callable (default ``None`` → those components are omitted), so the
scorer never hard-requires an LLM. The harness reports RELATIVE lift only — no
leaderboard claim.

Scoring scheme (TIDE §3.3, macro-averaged)
-----------------------------------------
For one instance with gold problems ``G`` and predictions ``P``:

* ``coverage`` = mean over ``g in G`` of ``max_{p in P} jaccard(g.evidence, p.evidence)``
  (best-matching prediction per gold). Empty ``P`` → 0.
* ``precision`` = mean over ``p in P`` of ``max_{g in G} jaccard(...)`` but
  crediting only the BEST prediction per gold (so duplicate predictions covering
  the same gold do not inflate the score) and counting every other prediction as
  0 (penalizing extraneous predictions). Empty ``P`` → 0.
* ``f1`` = harmonic mean of ``coverage`` and ``precision``.

Instance scores are then MACRO-averaged across instances.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from magi_agent.benchmarks.multibug.dataset import GoldProblem
from magi_agent.discovery.models import DiscoveryPrediction

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

#: ``(gold_problems, predictions) -> float in [0, 1]`` — an injectable LLM judge
#: for identification / resolution. Default is ``None`` (component omitted).
Judge = Callable[[Sequence[GoldProblem], Sequence[DiscoveryPrediction]], float]


class InstanceResult(BaseModel):
    """One scored instance: its gold problems and the harness predictions."""

    model_config = _MODEL_CONFIG

    instance_id: str
    gold_problems: tuple[GoldProblem, ...]
    predictions: tuple[DiscoveryPrediction, ...] = ()


class InstanceScore(BaseModel):
    """Per-instance retrieval scores (+ optional judged components)."""

    model_config = _MODEL_CONFIG

    instance_id: str
    coverage: float
    precision: float
    f1: float
    identification: float | None = None
    resolution: float | None = None


class MultiBugReport(BaseModel):
    """Macro-averaged report over a set of instances."""

    model_config = _MODEL_CONFIG

    coverage: float
    precision: float
    f1: float
    identification: float | None = None
    resolution: float | None = None
    instance_count: int
    per_instance: tuple[InstanceScore, ...] = ()


class MultiBugLift(BaseModel):
    """Per-component delta of a harness report against a baseline report."""

    model_config = _MODEL_CONFIG

    coverage: float
    precision: float
    f1: float
    identification: float | None = None
    resolution: float | None = None


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    """Jaccard overlap of two evidence-id sets. Empty/empty → 0.0."""
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _harmonic_mean(x: float, y: float) -> float:
    if x <= 0.0 or y <= 0.0:
        return 0.0
    return 2.0 * x * y / (x + y)


def _score_instance(result: InstanceResult, *, judge: Judge | None) -> InstanceScore:
    golds = result.gold_problems
    preds = result.predictions

    # Coverage: best-matching prediction per gold, averaged over gold.
    if preds and golds:
        coverage = sum(
            max(_jaccard(g.evidence_ids, p.evidence_ids) for p in preds)
            for g in golds
        ) / len(golds)
    else:
        coverage = 0.0

    # Precision: credit only the BEST prediction per gold; every other
    # prediction scores 0 (penalizes extraneous predictions). Averaged over
    # predictions.
    if preds and golds:
        # For each gold, find the single best prediction index that covers it.
        best_pred_for_gold: dict[int, float] = {}
        for g in golds:
            best_idx = -1
            best_val = 0.0
            for idx, p in enumerate(preds):
                val = _jaccard(g.evidence_ids, p.evidence_ids)
                if val > best_val:
                    best_val = val
                    best_idx = idx
            if best_idx >= 0:
                # keep the strongest gold-credit assigned to this prediction
                best_pred_for_gold[best_idx] = max(
                    best_pred_for_gold.get(best_idx, 0.0), best_val
                )
        precision = sum(best_pred_for_gold.values()) / len(preds)
    else:
        precision = 0.0

    f1 = _harmonic_mean(coverage, precision)

    identification: float | None = None
    resolution: float | None = None
    if judge is not None:
        identification = float(judge(golds, preds))
        resolution = identification

    return InstanceScore(
        instance_id=result.instance_id,
        coverage=coverage,
        precision=precision,
        f1=f1,
        identification=identification,
        resolution=resolution,
    )


def score(
    results: Sequence[InstanceResult],
    *,
    judge: Judge | None = None,
) -> MultiBugReport:
    """Macro-average retrieval (and optionally judged) scores over instances."""
    per_instance = tuple(_score_instance(r, judge=judge) for r in results)
    n = len(per_instance)
    if n == 0:
        return MultiBugReport(
            coverage=0.0, precision=0.0, f1=0.0, instance_count=0, per_instance=()
        )

    def _mean(attr: str) -> float:
        return sum(getattr(s, attr) for s in per_instance) / n

    identification: float | None = None
    resolution: float | None = None
    if judge is not None:
        identification = sum(s.identification or 0.0 for s in per_instance) / n
        resolution = sum(s.resolution or 0.0 for s in per_instance) / n

    return MultiBugReport(
        coverage=_mean("coverage"),
        precision=_mean("precision"),
        f1=_mean("f1"),
        identification=identification,
        resolution=resolution,
        instance_count=n,
        per_instance=per_instance,
    )


def lift(*, harness: MultiBugReport, baseline: MultiBugReport) -> MultiBugLift:
    """Per-component delta ``harness - baseline`` (relative lift only)."""

    def _delta(
        h: float | None, b: float | None
    ) -> float | None:
        if h is None or b is None:
            return None
        return h - b

    return MultiBugLift(
        coverage=harness.coverage - baseline.coverage,
        precision=harness.precision - baseline.precision,
        f1=harness.f1 - baseline.f1,
        identification=_delta(harness.identification, baseline.identification),
        resolution=_delta(harness.resolution, baseline.resolution),
    )


__all__ = [
    "InstanceResult",
    "InstanceScore",
    "Judge",
    "MultiBugLift",
    "MultiBugReport",
    "lift",
    "score",
]
