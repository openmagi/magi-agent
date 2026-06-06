# tests/benchmarks/legalbench/test_loader.py
from __future__ import annotations

from pathlib import Path

from magi_agent.benchmarks.legalbench.loader import load_task
from magi_agent.benchmarks.legalbench.models import LegalTask


def _write_task(root: Path) -> Path:
    task = root / "abercrombie"
    task.mkdir(parents=True)
    (task / "base_prompt.txt").write_text(
        "Mark: {text}\nIs it generic? Answer Yes or No.\nAnswer:"
    )
    (task / "train.tsv").write_text(
        "text\tanswer\n" "soft soap for soap\tYes\n" "STAR for cars\tNo\n"
    )
    (task / "test.tsv").write_text("text\tanswer\n" "ivory for ivory\tYes\n")
    return task


def test_load_task_reads_splits_prompt_and_labels(tmp_path: Path) -> None:
    task_dir = _write_task(tmp_path)
    task = load_task(task_dir, reasoning_type="rule-conclusion")
    assert isinstance(task, LegalTask)
    assert task.task_id == "abercrombie"
    assert task.reasoning_type == "rule-conclusion"
    assert "{text}" in task.base_prompt
    assert len(task.train) == 2
    assert task.train[0].fields["text"] == "soft soap for soap"
    assert task.train[0].answer == "Yes"
    assert len(task.test) == 1
    assert task.labels == ("No", "Yes")  # sorted unique answers from train
