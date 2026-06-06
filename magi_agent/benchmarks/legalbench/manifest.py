# magi_agent/benchmarks/legalbench/manifest.py
from __future__ import annotations

import json
from pathlib import Path

from magi_agent.benchmarks.legalbench.loader import load_task
from magi_agent.benchmarks.legalbench.models import LegalTask


def load_subset(*, data_root: Path, manifest_path: Path) -> tuple[LegalTask, ...]:
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks: list[LegalTask] = []
    for entry in entries:
        task_dir = data_root / entry["task_id"]
        tasks.append(load_task(task_dir, reasoning_type=entry["reasoning_type"]))
    return tuple(tasks)
