# magi_agent/benchmarks/legal_eval.py
"""LegalBench post-hoc evaluator. No provider/model calls are made here; it
scores recorded answer records against gold labels (mirrors coding_eval.py)."""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict

from magi_agent.benchmarks.legalbench.models import ReasoningType

LEGAL_BENCHMARK_SCHEMA_VERSION = "legalBenchTasks.v1"

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class AnswerRecord(BaseModel):
    model_config = _FROZEN
    task_id: str
    reasoning_type: ReasoningType
    index: int
    predicted: str | None
    gold: str


class LegalReport(BaseModel):
    model_config = _FROZEN
    schema_version: str = LEGAL_BENCHMARK_SCHEMA_VERSION
    overall_balanced_accuracy: float
    by_reasoning_type: dict[ReasoningType, float]
    by_task: dict[str, float]


class LegalLift(BaseModel):
    model_config = _FROZEN
    overall: float
    by_reasoning_type: dict[ReasoningType, float]


def _balanced_accuracy(pairs: list[tuple[str | None, str]]) -> float:
    by_class_total: dict[str, int] = defaultdict(int)
    by_class_correct: dict[str, int] = defaultdict(int)
    for predicted, gold in pairs:
        by_class_total[gold] += 1
        if predicted == gold:
            by_class_correct[gold] += 1
    if not by_class_total:
        return 0.0
    recalls = [by_class_correct[c] / by_class_total[c] for c in by_class_total]
    return sum(recalls) / len(recalls)


def score(records: list[AnswerRecord]) -> LegalReport:
    """Compute a two-level macro-averaged balanced accuracy over *records*.

    Assumes each ``task_id`` maps to exactly one ``reasoning_type`` (tasks are
    single-reasoning-type by construction — mixing is not supported).  Scoring
    proceeds as: (1) per-task balanced accuracy (mean of per-gold-class recall);
    (2) mean over tasks within each reasoning type; (3) mean over reasoning types
    for ``overall_balanced_accuracy``.
    """
    by_task_pairs: dict[str, list[tuple[str | None, str]]] = defaultdict(list)
    task_reasoning: dict[str, ReasoningType] = {}
    for rec in records:
        by_task_pairs[rec.task_id].append((rec.predicted, rec.gold))
        task_reasoning[rec.task_id] = rec.reasoning_type

    by_task = {tid: _balanced_accuracy(pairs) for tid, pairs in by_task_pairs.items()}

    rt_scores: dict[ReasoningType, list[float]] = defaultdict(list)
    for tid, acc in by_task.items():
        rt_scores[task_reasoning[tid]].append(acc)
    by_reasoning_type = {rt: sum(v) / len(v) for rt, v in rt_scores.items()}

    overall = (
        sum(by_reasoning_type.values()) / len(by_reasoning_type)
        if by_reasoning_type
        else 0.0
    )
    return LegalReport(
        overall_balanced_accuracy=overall,
        by_reasoning_type=by_reasoning_type,
        by_task=by_task,
    )


def lift(*, harness: LegalReport, baseline: LegalReport) -> LegalLift:
    rt = {
        key: harness.by_reasoning_type.get(key, 0.0) - baseline.by_reasoning_type.get(key, 0.0)
        for key in harness.by_reasoning_type
    }
    return LegalLift(
        overall=harness.overall_balanced_accuracy - baseline.overall_balanced_accuracy,
        by_reasoning_type=rt,
    )
