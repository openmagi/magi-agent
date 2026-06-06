# tests/benchmarks/test_legal_eval.py
from __future__ import annotations

from magi_agent.benchmarks.legal_eval import (
    AnswerRecord,
    lift,
    score,
)


def _records(pred_map: dict[str, str]) -> list[AnswerRecord]:
    # Two-class task; gold alternates Yes/No.
    gold = ["Yes", "No", "Yes", "No"]
    return [
        AnswerRecord(
            task_id="abercrombie",
            reasoning_type="rule-conclusion",
            index=i,
            predicted=pred_map.get(str(i)),
            gold=gold[i],
        )
        for i in range(4)
    ]


def test_perfect_predictions_score_one() -> None:
    recs = _records({"0": "Yes", "1": "No", "2": "Yes", "3": "No"})
    report = score(recs)
    assert report.overall_balanced_accuracy == 1.0
    assert report.by_reasoning_type["rule-conclusion"] == 1.0


def test_balanced_accuracy_handles_class_imbalance() -> None:
    # Predict everything "Yes": Yes-recall=1.0, No-recall=0.0 -> balanced 0.5
    recs = _records({"0": "Yes", "1": "Yes", "2": "Yes", "3": "Yes"})
    report = score(recs)
    assert report.overall_balanced_accuracy == 0.5


def test_lift_is_harness_minus_baseline() -> None:
    harness = score(_records({"0": "Yes", "1": "No", "2": "Yes", "3": "No"}))
    baseline = score(_records({"0": "Yes", "1": "Yes", "2": "Yes", "3": "Yes"}))
    assert lift(harness=harness, baseline=baseline).overall == 0.5
