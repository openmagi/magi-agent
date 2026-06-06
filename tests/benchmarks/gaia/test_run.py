from __future__ import annotations

import json

from magi_agent.benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.benchmarks.gaia.run import run_benchmark


def _questions() -> list[GaiaQuestion]:
    return [
        GaiaQuestion(task_id="a", question="Q1", level=1, final_answer="paris"),
        GaiaQuestion(task_id="b", question="Q2", level=2, final_answer="42"),
    ]


def test_scores_and_writes_results(tmp_path) -> None:
    answers = {"a": "Paris", "b": "41"}  # a correct, b wrong
    report = run_benchmark(
        _questions(),
        output_dir=str(tmp_path),
        runner_fn=lambda q, **kw: answers[q.task_id],
    )
    assert report["per_level"]["1"]["correct"] == 1
    assert report["per_level"]["2"]["correct"] == 0
    lines = (tmp_path / "results.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["task_id"] == "a"
    assert (tmp_path / "manifest.json").exists()


def test_resume_skips_scored(tmp_path) -> None:
    answers = {"a": "Paris", "b": "42"}
    run_benchmark(_questions()[:1], output_dir=str(tmp_path),
                  runner_fn=lambda q, **kw: answers[q.task_id])
    calls: list[str] = []

    def _runner(q, **kw):
        calls.append(q.task_id)
        return answers[q.task_id]

    run_benchmark(_questions(), output_dir=str(tmp_path), runner_fn=_runner)
    assert calls == ["b"]  # "a" already scored, skipped
