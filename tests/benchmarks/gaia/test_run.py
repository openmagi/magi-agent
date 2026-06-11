from __future__ import annotations

import json

from benchmarks.gaia.dataset import GaiaQuestion
from benchmarks.gaia.run import run_benchmark


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


def test_manifest_count_matches(tmp_path) -> None:
    answers = {"a": "Paris", "b": "42"}
    run_benchmark(
        _questions(),
        output_dir=str(tmp_path),
        runner_fn=lambda q, **kw: answers[q.task_id],
        n=1,
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["count"] == 2
    assert manifest["n"] == 1


def test_best_of_n_majority_used(tmp_path) -> None:
    # Task "a" always returns "Paris" (correct, ground_truth="paris").
    # Task "b" returns "41", "42", "42" across 3 calls — majority vote picks "42"
    # which matches ground_truth "42".
    call_counts: dict[str, int] = {"a": 0, "b": 0}
    b_sequence = ["41", "42", "42"]

    def _runner(q, **kw) -> str:
        idx = call_counts[q.task_id]
        call_counts[q.task_id] += 1
        if q.task_id == "b":
            return b_sequence[idx]
        return "Paris"

    report = run_benchmark(
        _questions(),
        output_dir=str(tmp_path),
        runner_fn=_runner,
        n=3,
    )
    # "b" must be scored correct because majority_vote(["41","42","42"]) == "42"
    assert report["per_level"]["2"]["correct"] == 1


def test_skips_corrupt_jsonl_line(tmp_path) -> None:
    # Pre-populate results.jsonl: one valid record for "a" + one corrupt line.
    results_path = tmp_path / "results.jsonl"
    valid_record = json.dumps(
        {"task_id": "a", "level": 1, "answer": "Paris", "ground_truth": "paris", "correct": True}
    )
    results_path.write_text(valid_record + "\n{garbage\n", encoding="utf-8")

    answers = {"a": "Paris", "b": "42"}
    # run_benchmark must not crash; "a" is skipped (already in scored_ids),
    # "b" is scored; corrupt line is silently ignored.
    report = run_benchmark(
        _questions(),
        output_dir=str(tmp_path),
        runner_fn=lambda q, **kw: answers[q.task_id],
    )
    # Report counts only valid records (a + b = 2); the corrupt line is excluded.
    assert report["overall"]["total"] == 2
