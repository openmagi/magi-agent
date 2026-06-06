from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from benchmarks.swebench.dataset import DATASET_NAME


@dataclass(frozen=True)
class EvalOutcome:
    resolved_ids: set[str]
    total: int
    report_path: Path


def run_evaluation(
    predictions_path: Path,
    *,
    run_id: str,
    max_workers: int = 4,
) -> EvalOutcome:
    cmd = [
        "python", "-m", "swebench.harness.run_evaluation",
        "--dataset_name", DATASET_NAME,
        "--predictions_path", str(predictions_path),
        "--max_workers", str(max_workers),
        "--run_id", run_id,
    ]
    subprocess.run(cmd, check=True)
    report = _find_report(run_id)
    data = json.loads(report.read_text(encoding="utf-8"))
    return EvalOutcome(
        resolved_ids=set(data.get("resolved_ids", [])),
        total=int(data.get("total_instances", 0)),
        report_path=report,
    )


def _find_report(run_id: str) -> Path:
    # swebench writes <model>.<run_id>.json in CWD.
    matches = sorted(Path.cwd().glob(f"*.{run_id}.json"))
    if not matches:
        raise FileNotFoundError(f"no swebench report for run_id={run_id}")
    return matches[-1]
