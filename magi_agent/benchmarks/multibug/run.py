"""Resumable multi-problem benchmark runner (mirrors ``gaia/run.py``).

Drives a list of :class:`MultiProblemInstance` objects through the harness,
persists per-instance predictions to ``<output_dir>/multibug_results.jsonl``, and
scores the full result set. A run resumes safely: instances whose
``instance_id`` already appears in the results file are skipped. Instances with
fewer than two gold problems are logged and skipped (never silently dropped).
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

from magi_agent.benchmarks.multibug.dataset import GoldProblem, MultiProblemInstance
from magi_agent.benchmarks.multibug.scorer import InstanceResult, score
from magi_agent.discovery.grounding import GroundingMode
from magi_agent.discovery.models import DiscoveryPrediction

_LOG = logging.getLogger(__name__)

_RESULTS_FILENAME = "multibug_results.jsonl"
_MANIFEST_FILENAME = "manifest.json"


def _default_runner(
    instance: MultiProblemInstance,
    *,
    mode: str,
    grounding: GroundingMode,
    model: str,
) -> tuple[DiscoveryPrediction, ...]:
    """Thin wrapper around the real harness; imported lazily so tests that inject
    a stub never trigger the heavy ADK import path."""
    from magi_agent.benchmarks.multibug.harness import run_multiproblem  # noqa: PLC0415

    return run_multiproblem(
        instance, mode=mode, grounding=grounding, model=model
    )


def run_benchmark(
    instances: Sequence[MultiProblemInstance],
    *,
    output_dir: str,
    mode: str = "tide",
    grounding: GroundingMode = "audit",
    runner_fn: Callable[..., tuple[DiscoveryPrediction, ...]] | None = None,
    model: str = "claude-opus-4-7",
) -> dict[str, Any]:
    """Run (or resume) a multi-problem discovery evaluation.

    Returns a dict with the macro-averaged ``report`` (see
    :func:`magi_agent.benchmarks.multibug.scorer.score`) plus run metadata.
    """
    if runner_fn is None:
        runner_fn = _default_runner

    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, _RESULTS_FILENAME)
    manifest_path = os.path.join(output_dir, _MANIFEST_FILENAME)

    # --- Resume: collect already-completed instance ids ---
    completed_ids: set[str] = set()
    if os.path.exists(results_path):
        with open(results_path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    completed_ids.add(json.loads(raw)["instance_id"])
                except (json.JSONDecodeError, KeyError):
                    pass

    skipped = 0
    with open(results_path, "a", encoding="utf-8") as fh:
        for instance in instances:
            if instance.instance_id in completed_ids:
                continue
            if len(instance.gold_problems) < 2:
                _LOG.warning(
                    "skipping instance %s: only %d gold problem(s) (need >=2)",
                    instance.instance_id,
                    len(instance.gold_problems),
                )
                skipped += 1
                continue

            preds = runner_fn(
                instance, mode=mode, grounding=grounding, model=model
            )
            record = {
                "instance_id": instance.instance_id,
                "gold_problems": [g.model_dump() for g in instance.gold_problems],
                "predictions": [p.model_dump() for p in preds],
            }
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            completed_ids.add(instance.instance_id)

    # --- Score over ALL records (including resumed ones) ---
    results: list[InstanceResult] = []
    with open(results_path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            results.append(
                InstanceResult(
                    instance_id=record["instance_id"],
                    gold_problems=tuple(
                        GoldProblem.model_validate(g)
                        for g in record.get("gold_problems", [])
                    ),
                    predictions=tuple(
                        DiscoveryPrediction.model_validate(p)
                        for p in record.get("predictions", [])
                    ),
                )
            )

    report = score(results)

    manifest = {
        "mode": mode,
        "grounding": grounding,
        "model": model,
        "count": len(results),
        "skipped": skipped,
        "output_dir": output_dir,
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    return {
        "report": report.model_dump(),
        "count": len(results),
        "skipped": skipped,
    }


__all__ = ["run_benchmark"]
