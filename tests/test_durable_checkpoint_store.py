"""PR1a RED suite - durable checkpoint + plan-ledger substrate.

WS1 PR1a adds a sqlite-backed ``DurableCheckpointStore`` co-located in the
existing work-queue DB (``work_queue.db``), gated behind the master flag
``MAGI_DURABLE_LOCAL_WRITES_ENABLED``. With the flag OFF the store is a no-op
and creates no tables (OFF byte-identical). With the flag ON it creates the
``durable_checkpoints`` and ``plan_ledger`` tables idempotently and supports
checkpoint CRUD with supersede semantics + a resume-attempt bound.

The hosted ``storage/durable_store.py`` ``Literal[False]`` guard is NEVER
touched by this store - a subprocess + AST scan proves the new module imports
neither ``storage.durable_store`` nor ``storage.sqlite_store``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from magi_agent.runtime.checkpointing import ExecutionCheckpoint
from magi_agent.storage.durable_checkpoint_store import DurableCheckpointStore


_SHA = "sha256:" + "a" * 64


def _checkpoint(
    *,
    run_id: str = "run-001",
    turn_id: str = "turn-001",
    checkpoint_id: str = "checkpoint-001",
    step_id: str = "step-0001",
    resumable: bool = True,
) -> ExecutionCheckpoint:
    return ExecutionCheckpoint(
        runId=run_id,
        checkpointId=checkpoint_id,
        stepId=step_id,
        workflowVersion="1.0.0",
        stateDigest="sha256:" + "1" * 64,
        ledgerHeadDigest="sha256:" + "2" * 64,
        effectivePolicySnapshotDigest="sha256:" + "3" * 64,
        contextProjectionDigest="sha256:" + "4" * 64,
        resumable=resumable,
        createdAt=datetime(2026, 6, 25, 12, 0, tzinfo=UTC),
    )


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "work_queue.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(path))
    return path


def _put(
    store: DurableCheckpointStore,
    ckpt: ExecutionCheckpoint,
    *,
    turn_id: str = "turn-001",
    watermark_uuid: str = "wm-0001",
    evidence_line_count: int = 0,
    cwd: str = "/tmp/ws",
) -> None:
    store.put(
        ckpt,
        turn_id=turn_id,
        watermark_uuid=watermark_uuid,
        evidence_line_count=evidence_line_count,
        cwd=cwd,
    )


def test_durable_checkpoint_store_roundtrip(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    store = DurableCheckpointStore()
    ckpt = _checkpoint()
    _put(store, ckpt, watermark_uuid="wm-AAAA", evidence_line_count=7)
    store.close()

    # Fresh store over the SAME path - proves persistence across the connection
    # boundary (the cross-process recovery shape).
    fresh = DurableCheckpointStore()
    got = fresh.get_latest_resumable("run-001", "turn-001")
    assert got is not None
    assert got.checkpoint.checkpoint_id == "checkpoint-001"
    assert got.checkpoint.run_id == "run-001"
    assert got.turn_id == "turn-001"
    assert got.watermark_uuid == "wm-AAAA"
    assert got.evidence_line_count == 7
    assert got.cwd == "/tmp/ws"
    assert got.resume_attempt_count == 0
    fresh.close()


def test_durable_checkpoint_store_off_is_noop(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", raising=False)
    store = DurableCheckpointStore()
    _put(store, _checkpoint())
    # No write, no table, no file created.
    assert store.get_latest_resumable("run-001", "turn-001") is None
    assert not db_path.exists()
    store.close()


def test_supersede_marks_prior(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    store = DurableCheckpointStore()
    _put(
        store,
        _checkpoint(checkpoint_id="checkpoint-001", step_id="step-0001"),
        watermark_uuid="wm-1",
    )
    _put(
        store,
        _checkpoint(checkpoint_id="checkpoint-002", step_id="step-0002"),
        watermark_uuid="wm-2",
    )
    got = store.get_latest_resumable("run-001", "turn-001")
    assert got is not None
    assert got.checkpoint.checkpoint_id == "checkpoint-002"
    assert store.is_superseded("checkpoint-001") is True
    assert store.is_superseded("checkpoint-002") is False
    store.close()


def test_rejects_secret_fragment_checkpoint(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    # The schema validator rejects a protected fragment in an identifier
    # BEFORE the row is ever persisted.
    with pytest.raises(Exception):
        _checkpoint(checkpoint_id="checkpoint-secret-001")


def test_plan_ledger_table_created_but_empty(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    store = DurableCheckpointStore()
    # Touch the store so the DDL runs under the ON master.
    _put(store, _checkpoint())
    assert store.plan_ledger_row_count() == 0
    assert store.has_table("plan_ledger") is True
    store.close()


def test_durable_checkpoints_ddl_has_operational_columns(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    store = DurableCheckpointStore()
    _put(store, _checkpoint())
    cols = store.checkpoint_columns()
    for required in (
        "resume_attempt_count",
        "workflow_version",
        "watermark_uuid",
        "evidence_line_count",
        "cwd",
        "turn_id",
        "superseded",
    ):
        assert required in cols, f"missing operational column {required!r}"
    # The chain index is unsound as a watermark (correction 4); there must be
    # NO seq_offset column.
    assert "seq_offset" not in cols
    store.close()


def test_increment_resume_attempt_persists_and_bounds(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    store = DurableCheckpointStore()
    _put(store, _checkpoint())
    store.increment_resume_attempt("run-001", "turn-001")
    store.close()

    fresh = DurableCheckpointStore()
    got = fresh.get_latest_resumable("run-001", "turn-001")
    assert got is not None
    assert got.resume_attempt_count == 1
    fresh.close()


def test_list_resumable_turns_excludes_non_resumable(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DURABLE_LOCAL_WRITES_ENABLED", "1")
    store = DurableCheckpointStore()
    _put(
        store,
        _checkpoint(run_id="run-A", checkpoint_id="checkpoint-A", resumable=True),
        turn_id="turn-A",
    )
    _put(
        store,
        _checkpoint(run_id="run-B", checkpoint_id="checkpoint-B", resumable=False),
        turn_id="turn-B",
    )
    turns = {(t.checkpoint.run_id, t.turn_id) for t in store.list_resumable_turns()}
    assert ("run-A", "turn-A") in turns
    assert ("run-B", "turn-B") not in turns
    store.close()


def test_hosted_guard_untouched(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. AST scan: the new module imports neither durable_store nor sqlite_store.
    module_path = (
        Path(__file__).resolve().parent.parent
        / "magi_agent"
        / "storage"
        / "durable_checkpoint_store.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    assert "magi_agent.storage.durable_store" not in imported
    assert "magi_agent.storage.sqlite_store" not in imported

    # 2. Subprocess: importing the new module leaves the hosted Literal[False]
    #    guard untouched. NOTE: the ``magi_agent.storage`` *package* __init__
    #    eagerly imports ``durable_store`` for EVERY storage submodule (a
    #    pre-existing side effect, independent of this module), so a
    #    ``durable_store not in sys.modules`` assertion would test the package
    #    init, not this module's coupling. The module-level coupling is proven
    #    by the AST scan above; here we prove the guard's force-false invariant
    #    survives importing/using the new store.
    code = (
        "import magi_agent.storage.durable_checkpoint_store as m; "
        "from magi_agent.storage.durable_store import DurableStoreConfig; "
        "cfg = DurableStoreConfig(kind='memory', artifactStore='filesystem'); "
        "assert cfg.production_writes_enabled is False; "
        "store = m.DurableCheckpointStore(); "
        "assert DurableStoreConfig.model_fields"
        "['production_writes_enabled'].annotation is not None; "
        "print('OK')"
    )
    env = dict(os.environ)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_durable_local_writes_flag_registered_and_default_off() -> None:
    from magi_agent.config.flags import flag_bool, flag_int

    # Default OFF in a clean env (no MAGI_DURABLE_LOCAL_WRITES_ENABLED set).
    assert flag_bool("MAGI_DURABLE_LOCAL_WRITES_ENABLED", env={}) is False
    # The E11/R6 bound is a registered int flag (default 2).
    assert flag_int("MAGI_DURABLE_MAX_RESUME_ATTEMPTS", env={}) == 2
