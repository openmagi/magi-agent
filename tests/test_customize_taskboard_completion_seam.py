"""H2-C8: taskboard-completion gate + opt_in seam.

``_task_board_completion_block_labels`` reads the workspace
``<cwd>/.magi/taskboard.jsonl`` (where the TaskBoard native tool appends
{action,title,status} records), folds by title to the latest status, and blocks
when any title's latest status is non-terminal. Gated by
``MAGI_VERIFY_TASKBOARD_COMPLETION`` OR the ``task-board-completion`` preset.
FAIL-OPEN (missing/unreadable ledger ⇒ no block). Byte-identical when off (no
file read).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_BLOCK = "task_board:incomplete_tasks"


def _driver() -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("user.quote",),
            evidenceRequirements=(),
            requiredValidators=(),
            missingEvidenceAction="block",
        ),
        evidence_collector=lambda _turn: (),
    )


def _write_ledger(records: list[dict[str, str]]) -> None:
    # Written relative to cwd (tests chdir into a tmp workspace first).
    ledger = Path.cwd() / ".magi" / "taskboard.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


@pytest.fixture(autouse=True)
def _workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return tmp_path


def _enable_preset() -> None:
    import os

    set_verification_override(
        "harness_presets", "task-board-completion", True, path=Path(os.environ["MAGI_CUSTOMIZE"])
    )


def _labels() -> list[str]:
    return _driver()._task_board_completion_block_labels()


def test_inert_when_all_off(monkeypatch):
    monkeypatch.setenv("MAGI_VERIFY_TASKBOARD_COMPLETION", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    _write_ledger([{"title": "t1", "status": "pending"}])
    assert _labels() == []  # no file read at all when gated off


def test_no_ledger_fails_open(monkeypatch):
    monkeypatch.setenv("MAGI_VERIFY_TASKBOARD_COMPLETION", "1")
    assert _labels() == []  # missing ledger ⇒ no block


def test_env_flag_blocks_incomplete(monkeypatch):
    monkeypatch.setenv("MAGI_VERIFY_TASKBOARD_COMPLETION", "1")
    _write_ledger([{"title": "t1", "status": "pending"}])
    assert _labels() == [_BLOCK]


def test_env_flag_passes_all_done(monkeypatch):
    monkeypatch.setenv("MAGI_VERIFY_TASKBOARD_COMPLETION", "1")
    _write_ledger(
        [
            {"title": "t1", "status": "pending"},
            {"title": "t1", "status": "done"},  # latest per title wins
            {"title": "t2", "status": "completed"},
        ]
    )
    assert _labels() == []


def test_latest_status_per_title_wins(monkeypatch):
    monkeypatch.setenv("MAGI_VERIFY_TASKBOARD_COMPLETION", "1")
    _write_ledger(
        [
            {"title": "t1", "status": "done"},
            {"title": "t1", "status": "in_progress"},  # reopened → incomplete
        ]
    )
    assert _labels() == [_BLOCK]


def test_preset_toggle_activates(monkeypatch):
    monkeypatch.setenv("MAGI_VERIFY_TASKBOARD_COMPLETION", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset()
    _write_ledger([{"title": "t1", "status": "pending"}])
    assert _labels() == [_BLOCK]


def test_not_activated_when_master_flag_off(monkeypatch):
    monkeypatch.setenv("MAGI_VERIFY_TASKBOARD_COMPLETION", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable_preset()
    _write_ledger([{"title": "t1", "status": "pending"}])
    assert _labels() == []
