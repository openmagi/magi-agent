"""Resumable GAIA benchmark runner.

Drives a list of :class:`~magi_agent.benchmarks.gaia.dataset.GaiaQuestion`
objects through a runner function, scores each answer, and persists results to
``<output_dir>/results.jsonl``.  A run can be resumed safely: questions whose
``task_id`` already appears in ``results.jsonl`` are skipped.

After the loop, ``<output_dir>/manifest.json`` is written with run metadata, and
a per-level / overall accuracy report is returned.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from typing import Any

from magi_agent.benchmarks.gaia.best_of_n import majority_vote
from magi_agent.benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.benchmarks.gaia.scorer import question_scorer


def _default_runner(
    question: GaiaQuestion,
    *,
    workspace_root: str,
    model: str,
) -> str:
    """Thin wrapper around the real harness; only imported when needed so tests
    that inject a stub never trigger heavy ADK imports."""
    from magi_agent.benchmarks.gaia.harness import run_gaia_question  # noqa: PLC0415

    return run_gaia_question(question, workspace_root=workspace_root, model=model)


def run_benchmark(
    questions: Sequence[GaiaQuestion],
    *,
    output_dir: str,
    runner_fn: Callable[..., str] | None = None,
    n: int = 1,
    model: str = "claude-opus-4-7",
) -> dict[str, Any]:
    """Run (or resume) a GAIA benchmark evaluation.

    Parameters
    ----------
    questions:
        Questions to evaluate.
    output_dir:
        Directory for ``results.jsonl`` and ``manifest.json``.  Created if
        missing.
    runner_fn:
        ``(question, *, workspace_root, model) -> str``.  Defaults to the real
        :func:`~magi_agent.benchmarks.gaia.harness.run_gaia_question` wrapper.
        Tests always inject a stub.
    n:
        Number of independent runs per question; the majority vote is scored.
    model:
        Model identifier forwarded to the runner.

    Returns
    -------
    dict
        ``{"per_level": {str(level): {"correct": int, "total": int}},
           "overall": {"correct": int, "total": int}}``
        computed over ALL records in ``results.jsonl`` (including resumed ones).
    """
    if runner_fn is None:
        runner_fn = _default_runner

    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, "results.jsonl")
    manifest_path = os.path.join(output_dir, "manifest.json")

    # --- Resume: collect already-scored task_ids ---
    scored_ids: set[str] = set()
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if raw:
                    try:
                        record = json.loads(raw)
                        scored_ids.add(record["task_id"])
                    except (json.JSONDecodeError, KeyError):
                        pass

    # --- Score remaining questions ---
    with open(results_path, "a", encoding="utf-8") as fh:
        for question in questions:
            if question.task_id in scored_ids:
                continue

            # Run n times in a fresh temp workspace each time.
            answers: list[str] = []
            for _ in range(n):
                workspace = tempfile.mkdtemp()
                ans = runner_fn(question, workspace_root=workspace, model=model)
                answers.append(ans)

            answer = majority_vote(answers)
            correct = question_scorer(answer, question.final_answer)

            record: dict[str, Any] = {
                "task_id": question.task_id,
                "level": question.level,
                "answer": answer,
                "ground_truth": question.final_answer,
                "correct": correct,
            }
            fh.write(json.dumps(record) + "\n")
            fh.flush()

    # --- Build report over ALL records (including prior runs) ---
    per_level: dict[str, dict[str, int]] = {}
    overall_correct = 0
    overall_total = 0

    with open(results_path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            level_key = str(record["level"])
            if level_key not in per_level:
                per_level[level_key] = {"correct": 0, "total": 0}
            per_level[level_key]["total"] += 1
            if record.get("correct"):
                per_level[level_key]["correct"] += 1
                overall_correct += 1
            overall_total += 1

    # --- Write manifest ---
    manifest: dict[str, Any] = {
        "model": model,
        "n": n,
        "count": overall_total,
        "output_dir": output_dir,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    return {
        "per_level": per_level,
        "overall": {"correct": overall_correct, "total": overall_total},
    }


__all__ = ["run_benchmark"]
