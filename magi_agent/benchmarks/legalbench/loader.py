"""Loader functions that read LegalBench task directories into typed models."""
from __future__ import annotations

import csv
from pathlib import Path

from magi_agent.benchmarks.legalbench.models import Example, LegalTask, ReasoningType


def _read_tsv(path: Path) -> tuple[Example, ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    if rows and "answer" not in rows[0]:
        raise ValueError(f"TSV at {path} is missing required 'answer' column")
    examples: list[Example] = []
    for row in rows:
        answer = (row.get("answer") or "").strip()
        fields = {k: (v or "") for k, v in row.items() if k != "answer"}
        examples.append(Example(fields=fields, answer=answer))
    return tuple(examples)


def load_task(task_dir: Path, *, reasoning_type: ReasoningType) -> LegalTask:
    base_prompt = (task_dir / "base_prompt.txt").read_text(encoding="utf-8").strip()
    train = _read_tsv(task_dir / "train.tsv")
    test = _read_tsv(task_dir / "test.tsv")
    labels = tuple(sorted({ex.answer for ex in train if ex.answer}))
    return LegalTask(
        task_id=task_dir.name,
        reasoning_type=reasoning_type,
        base_prompt=base_prompt,
        train=train,
        test=test,
        labels=labels,
    )
