# tests/benchmarks/legalbench/test_manifest.py
from __future__ import annotations

import json
from pathlib import Path

from magi_agent.benchmarks.legalbench.manifest import load_subset


def _scaffold(root: Path) -> tuple[Path, Path]:
    data = root / "data"
    for name, ans in (("abercrombie", "Yes"), ("hearsay", "No")):
        d = data / name
        d.mkdir(parents=True)
        (d / "base_prompt.txt").write_text("{text}\nAnswer:")
        (d / "train.tsv").write_text(f"text\tanswer\nx\t{ans}\n")
        (d / "test.tsv").write_text(f"text\tanswer\ny\t{ans}\n")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"task_id": "abercrombie", "reasoning_type": "rule-conclusion"},
                {"task_id": "hearsay", "reasoning_type": "rule-application"},
            ]
        )
    )
    return data, manifest


def test_load_subset_returns_tasks_in_manifest_order(tmp_path: Path) -> None:
    data, manifest = _scaffold(tmp_path)
    tasks = load_subset(data_root=data, manifest_path=manifest)
    assert [t.task_id for t in tasks] == ["abercrombie", "hearsay"]
    assert tasks[1].reasoning_type == "rule-application"
