from __future__ import annotations

import json
import logging

from magi_agent.benchmarks.multibug.dataset import GoldProblem, MultiProblemInstance
from magi_agent.benchmarks.multibug.run import run_benchmark
from magi_agent.discovery.models import DiscoveryPrediction


def _instance(instance_id: str, n_gold: int = 2) -> MultiProblemInstance:
    return MultiProblemInstance(
        instance_id=instance_id,
        repo="octo/cat",
        anchor_commit="abc",
        candidates={"c1": "a", "c2": "b"},
        gold_problems=tuple(
            GoldProblem(problem_id=f"{instance_id}-{i}", evidence_ids=(f"c{i + 1}",))
            for i in range(n_gold)
        ),
    )


def _stub_runner(instance, *, mode, grounding, model):
    # Hit every gold exactly.
    return tuple(
        DiscoveryPrediction(description="p", evidence_ids=g.evidence_ids)
        for g in instance.gold_problems
    )


def test_run_benchmark_writes_results_and_scores(tmp_path) -> None:
    out = tmp_path / "out"
    result = run_benchmark(
        [_instance("a"), _instance("b")],
        output_dir=str(out),
        runner_fn=_stub_runner,
    )
    assert result["count"] == 2
    assert abs(result["report"]["coverage"] - 1.0) < 1e-9

    results_file = out / "multibug_results.jsonl"
    lines = [
        json.loads(line)
        for line in results_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {r["instance_id"] for r in lines} == {"a", "b"}
    assert (out / "manifest.json").exists()


def test_run_benchmark_resumes(tmp_path) -> None:
    out = tmp_path / "out"
    run_benchmark([_instance("a")], output_dir=str(out), runner_fn=_stub_runner)

    seen: list[str] = []

    def tracking_runner(instance, *, mode, grounding, model):
        seen.append(instance.instance_id)
        return _stub_runner(instance, mode=mode, grounding=grounding, model=model)

    run_benchmark(
        [_instance("a"), _instance("b")],
        output_dir=str(out),
        runner_fn=tracking_runner,
    )
    # "a" already completed -> only "b" re-run.
    assert seen == ["b"]


def test_run_benchmark_skips_and_logs_under_two_gold(tmp_path, caplog) -> None:
    out = tmp_path / "out"
    # Build a <2 gold instance bypassing the dataset validator (construct via
    # model_construct so we exercise the runner's defensive skip).
    one_gold = MultiProblemInstance.model_construct(
        instance_id="lonely",
        repo="octo/cat",
        anchor_commit="abc",
        candidates={"c1": "a"},
        gold_problems=(GoldProblem(problem_id="x", evidence_ids=("c1",)),),
    )
    with caplog.at_level(logging.WARNING):
        result = run_benchmark(
            [one_gold, _instance("ok")],
            output_dir=str(out),
            runner_fn=_stub_runner,
        )
    assert result["skipped"] == 1
    assert result["count"] == 1
    assert any("lonely" in rec.message for rec in caplog.records)
