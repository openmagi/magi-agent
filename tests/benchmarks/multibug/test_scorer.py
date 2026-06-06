from __future__ import annotations

from magi_agent.benchmarks.multibug.dataset import GoldProblem
from magi_agent.benchmarks.multibug.scorer import (
    InstanceResult,
    MultiBugReport,
    lift,
    score,
)
from magi_agent.discovery.models import DiscoveryPrediction


def _gold(pid: str, evidence: list[str]) -> GoldProblem:
    return GoldProblem(problem_id=pid, evidence_ids=tuple(evidence))


def _pred(evidence: list[str]) -> DiscoveryPrediction:
    return DiscoveryPrediction(description="p", evidence_ids=tuple(evidence))


def test_three_gold_two_hit_coverage_two_thirds() -> None:
    result = InstanceResult(
        instance_id="i",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"]), _gold("c", ["c3"])),
        predictions=(_pred(["c1"]), _pred(["c2"])),
    )
    report = score([result])
    assert abs(report.coverage - 2 / 3) < 1e-9
    # precision: both preds perfectly match a gold -> 1.0
    assert abs(report.precision - 1.0) < 1e-9


def test_extraneous_prediction_lowers_precision() -> None:
    result = InstanceResult(
        instance_id="i",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"])),
        predictions=(_pred(["c1"]), _pred(["c2"]), _pred(["junk"])),
    )
    report = score([result])
    assert abs(report.coverage - 1.0) < 1e-9
    # 2 of 3 predictions credited -> precision 2/3
    assert abs(report.precision - 2 / 3) < 1e-9
    assert report.f1 < 1.0


def test_perfect_match_is_one() -> None:
    result = InstanceResult(
        instance_id="i",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"])),
        predictions=(_pred(["c1"]), _pred(["c2"])),
    )
    report = score([result])
    assert abs(report.coverage - 1.0) < 1e-9
    assert abs(report.precision - 1.0) < 1e-9
    assert abs(report.f1 - 1.0) < 1e-9


def test_empty_predictions_zero_coverage() -> None:
    result = InstanceResult(
        instance_id="i",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"])),
        predictions=(),
    )
    report = score([result])
    assert report.coverage == 0.0
    assert report.precision == 0.0
    assert report.f1 == 0.0


def test_duplicate_predictions_do_not_inflate_precision() -> None:
    # Two predictions both hit the same single gold -> only one credited.
    result = InstanceResult(
        instance_id="i",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"])),
        predictions=(_pred(["c1"]), _pred(["c1"])),
    )
    report = score([result])
    # coverage: gold a hit (1.0), gold b missed (0) -> 0.5
    assert abs(report.coverage - 0.5) < 1e-9
    # precision: only one of two predictions credited -> 0.5
    assert abs(report.precision - 0.5) < 1e-9


def test_fake_judge_feeds_identification_resolution() -> None:
    result = InstanceResult(
        instance_id="i",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"])),
        predictions=(_pred(["c1"]),),
    )

    def fake_judge(golds, preds) -> float:
        return 0.75

    report = score([result], judge=fake_judge)
    assert report.identification == 0.75
    assert report.resolution == 0.75


def test_macro_average_across_instances() -> None:
    perfect = InstanceResult(
        instance_id="a",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"])),
        predictions=(_pred(["c1"]), _pred(["c2"])),
    )
    miss = InstanceResult(
        instance_id="b",
        gold_problems=(_gold("a", ["c1"]), _gold("b", ["c2"])),
        predictions=(),
    )
    report = score([perfect, miss])
    assert abs(report.coverage - 0.5) < 1e-9  # (1.0 + 0.0) / 2
    assert report.instance_count == 2


def test_empty_results_set() -> None:
    report = score([])
    assert report.instance_count == 0
    assert report.f1 == 0.0


def test_lift_computes_deltas() -> None:
    harness = MultiBugReport(
        coverage=0.8, precision=0.7, f1=0.75, instance_count=2,
        identification=0.6, resolution=0.5,
    )
    baseline = MultiBugReport(
        coverage=0.5, precision=0.4, f1=0.45, instance_count=2,
        identification=0.3, resolution=0.2,
    )
    delta = lift(harness=harness, baseline=baseline)
    assert abs(delta.coverage - 0.3) < 1e-9
    assert abs(delta.precision - 0.3) < 1e-9
    assert abs(delta.f1 - 0.3) < 1e-9
    assert abs((delta.identification or 0.0) - 0.3) < 1e-9


def test_lift_none_when_judge_components_absent() -> None:
    harness = MultiBugReport(coverage=0.8, precision=0.7, f1=0.75, instance_count=1)
    baseline = MultiBugReport(coverage=0.5, precision=0.4, f1=0.45, instance_count=1)
    delta = lift(harness=harness, baseline=baseline)
    assert delta.identification is None
    assert delta.resolution is None
