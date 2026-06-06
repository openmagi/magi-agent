from __future__ import annotations

import os
from pathlib import Path

from magi_agent.benchmarks.legal_eval import LegalReport, score
from magi_agent.benchmarks.legalbench.manifest import load_subset
from magi_agent.benchmarks.legalbench.runner import (
    Complete,
    baseline_checkpoints,
    run_subset,
)
from magi_agent.recipes.first_party.legal.recipe import LegalCheckpoints

_GATE_ENV = "MAGI_LEGAL_HARNESS_ENABLED"


class GateDisabledError(RuntimeError):
    pass


def ensure_enabled() -> None:
    """Raise GateDisabledError unless MAGI_LEGAL_HARNESS_ENABLED=1 is set.

    This is the default-OFF gate for the legal harness.  All entry points that
    run the harness against real data must call this before doing any work.
    """
    if os.environ.get(_GATE_ENV) != "1":
        raise GateDisabledError(
            f"Legal harness is gated off. Set {_GATE_ENV}=1 to run."
        )


def run_eval(
    *,
    data_root: Path,
    manifest_path: Path,
    complete: Complete,
    max_tasks: int | None = None,
) -> tuple[LegalReport, LegalReport]:
    """Run the harness + baseline evaluation and return both reports.

    Performs TWO full sweeps (harness pass + baseline pass), so the total
    number of model calls is roughly ``2 * (total test instances)`` — cost on
    a paid provider is approximately double a single pass.

    Args:
        data_root: Path to the directory containing per-task subdirectories
            (each with train.tsv, test.tsv, base_prompt.txt).
        manifest_path: Path to the JSON manifest listing task_id/reasoning_type
            entries to evaluate.
        complete: A callable ``(prompt: str) -> str`` that calls the model.
            Use a fake in tests; wire _real_complete in the CLI entry point.
        max_tasks: If set, evaluate only the first N tasks from the manifest.

    Returns:
        (harness_report, baseline_report) — both are LegalReport instances.
        Use lift(harness=harness_report, baseline=baseline_report) to compute
        the per-reasoning-type and overall lift.

    Raises:
        GateDisabledError: if MAGI_LEGAL_HARNESS_ENABLED != "1".
    """
    ensure_enabled()
    tasks = load_subset(data_root=data_root, manifest_path=manifest_path)
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    harness = score(
        run_subset(tasks, complete=complete, checkpoints=LegalCheckpoints())
    )
    baseline = score(
        run_subset(tasks, complete=complete, checkpoints=baseline_checkpoints())
    )
    return harness, baseline
