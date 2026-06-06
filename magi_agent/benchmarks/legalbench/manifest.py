"""Loads a curated subset of LegalBench tasks from a JSON manifest file."""
from __future__ import annotations

import json
import re
from pathlib import Path

from magi_agent.benchmarks.legalbench.loader import load_task
from magi_agent.benchmarks.legalbench.models import LegalTask

_VALID_TASK_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def load_subset(*, data_root: Path, manifest_path: Path) -> tuple[LegalTask, ...]:
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    tasks: list[LegalTask] = []
    for entry in entries:
        task_id = entry["task_id"]
        if not _VALID_TASK_ID.match(task_id):
            raise ValueError(f"invalid task_id: {task_id!r}")
        task_dir = data_root / task_id
        tasks.append(load_task(task_dir, reasoning_type=entry["reasoning_type"]))
    return tuple(tasks)
