# tests/benchmarks/legalbench/test_loader.py
from __future__ import annotations

from pathlib import Path

import pytest

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


def test_tsv_missing_answer_column_raises(tmp_path: Path) -> None:
    task = tmp_path / "bad_task"
    task.mkdir()
    (task / "base_prompt.txt").write_text("Prompt: {text}\nAnswer:")
    # train.tsv has no 'answer' column
    (task / "train.tsv").write_text("text\nnote on soap\n")
    (task / "test.tsv").write_text("text\tanswer\nsome text\tYes\n")
    with pytest.raises(ValueError, match="missing required 'answer' column"):
        load_task(task, reasoning_type="rule-recall")


def test_empty_train_tsv_yields_empty_labels(tmp_path: Path) -> None:
    task = tmp_path / "empty_task"
    task.mkdir()
    (task / "base_prompt.txt").write_text("Prompt: {text}\nAnswer:")
    # train.tsv has header only, no data rows
    (task / "train.tsv").write_text("text\tanswer\n")
    (task / "test.tsv").write_text("text\tanswer\nsome text\tYes\n")
    loaded = load_task(task, reasoning_type="issue")
    assert loaded.labels == ()
