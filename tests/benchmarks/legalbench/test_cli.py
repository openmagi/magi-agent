from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.benchmarks.legalbench.cli import GateDisabledError, ensure_enabled


def test_gate_blocks_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_LEGAL_HARNESS_ENABLED", raising=False)
    with pytest.raises(GateDisabledError):
        ensure_enabled()


def test_gate_allows_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_LEGAL_HARNESS_ENABLED", "1")
    ensure_enabled()  # does not raise


def test_run_eval_returns_harness_and_baseline_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_LEGAL_HARNESS_ENABLED", "1")
    data = tmp_path / "data" / "abercrombie"
    data.mkdir(parents=True)
    (data / "base_prompt.txt").write_text("Mark: {text}\nAnswer:")
    (data / "train.tsv").write_text("text\tanswer\nsoft\tYes\nstar\tNo\n")
    (data / "test.tsv").write_text("text\tanswer\nivory\tYes\n")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps([{"task_id": "abercrombie", "reasoning_type": "rule-conclusion"}])
    )

    from magi_agent.benchmarks.legalbench.cli import run_eval

    harness, baseline = run_eval(
        data_root=tmp_path / "data",
        manifest_path=manifest,
        complete=lambda prompt: "Yes",
    )
    assert harness.overall_balanced_accuracy == 1.0
    assert baseline.overall_balanced_accuracy == 1.0
