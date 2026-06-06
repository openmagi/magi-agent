# tests/benchmarks/test_legal_eval.py
from __future__ import annotations

import pytest

from magi_agent.benchmarks.legal_eval import (
    AnswerRecord,
    LegalReport,
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


def test_score_empty_records_returns_zero() -> None:
    assert score([]).overall_balanced_accuracy == 0.0


def test_single_class_task_balanced_accuracy() -> None:
    # All gold labels are "Yes"; 2 correct of 4 predictions → plain accuracy = 0.5.
    # With a single gold class, balanced accuracy == plain accuracy.
    recs = [
        AnswerRecord(task_id="t1", reasoning_type="rule-conclusion", index=i,
                     predicted=("Yes" if i < 2 else "No"), gold="Yes")
        for i in range(4)
    ]
    report = score(recs)
    assert report.by_task["t1"] == 0.5


def test_none_predicted_counts_as_wrong() -> None:
    # 3 records: predicted=None on first, then correct Yes/No.
    recs = [
        AnswerRecord(task_id="t1", reasoning_type="rule-recall", index=0,
                     predicted=None, gold="Yes"),
        AnswerRecord(task_id="t1", reasoning_type="rule-recall", index=1,
                     predicted="Yes", gold="Yes"),
        AnswerRecord(task_id="t1", reasoning_type="rule-recall", index=2,
                     predicted="No", gold="No"),
    ]
    # Yes-recall = 1/2 = 0.5; No-recall = 1/1 = 1.0 → balanced = 0.75
    report = score(recs)
    assert report.by_task["t1"] == pytest.approx(0.75)


def test_overall_is_macro_over_reasoning_types() -> None:
    # Type A: 2 tasks with balanced accuracies 1.0 and 0.0 → type mean = 0.5
    # Type B: 1 task with balanced accuracy 1.0 → type mean = 1.0
    # overall = mean(0.5, 1.0) = 0.75  (NOT flat mean over 3 tasks = 0.667)
    recs = [
        # Type A, task a1: all correct → 1.0
        AnswerRecord(task_id="a1", reasoning_type="rule-conclusion", index=0,
                     predicted="Yes", gold="Yes"),
        AnswerRecord(task_id="a1", reasoning_type="rule-conclusion", index=1,
                     predicted="No", gold="No"),
        # Type A, task a2: all wrong → 0.0
        AnswerRecord(task_id="a2", reasoning_type="rule-conclusion", index=0,
                     predicted="No", gold="Yes"),
        AnswerRecord(task_id="a2", reasoning_type="rule-conclusion", index=1,
                     predicted="Yes", gold="No"),
        # Type B, task b1: all correct → 1.0
        AnswerRecord(task_id="b1", reasoning_type="rule-recall", index=0,
                     predicted="Yes", gold="Yes"),
        AnswerRecord(task_id="b1", reasoning_type="rule-recall", index=1,
                     predicted="No", gold="No"),
    ]
    report = score(recs)
    assert report.overall_balanced_accuracy == pytest.approx(0.75)


def test_parse_rate_reflects_unparseable_predictions() -> None:
    recs = [
        AnswerRecord(task_id="t", reasoning_type="rule-conclusion", index=0,
                     predicted="Yes", gold="Yes"),
        AnswerRecord(task_id="t", reasoning_type="rule-conclusion", index=1,
                     predicted=None, gold="No"),
    ]
    report = score(recs)
    assert report.parse_rate == 0.5
    assert report.parse_rate_by_task["t"] == 0.5


def test_lift_only_reports_harness_keys() -> None:
    # Harness covers only type A; baseline covers A and B.
    harness_report = LegalReport(
        overall_balanced_accuracy=0.8,
        by_reasoning_type={"rule-conclusion": 0.8},
        by_task={"t1": 0.8},
        parse_rate=1.0,
        parse_rate_by_task={"t1": 1.0},
    )
    baseline_report = LegalReport(
        overall_balanced_accuracy=0.6,
        by_reasoning_type={"rule-conclusion": 0.6, "rule-recall": 0.7},
        by_task={"t1": 0.6, "t2": 0.7},
        parse_rate=1.0,
        parse_rate_by_task={"t1": 1.0, "t2": 1.0},
    )
    result = lift(harness=harness_report, baseline=baseline_report)
    assert set(result.by_reasoning_type.keys()) == {"rule-conclusion"}
