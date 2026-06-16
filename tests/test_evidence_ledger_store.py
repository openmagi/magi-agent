"""Tests for the durable EvidenceLedgerStore (PR1).

The store persists ``EvidenceLedgerEntry`` objects to append-only per-session
JSONL files and reads them back for a control-plane reader. It is pure (no
flags, no callers) and fail-open: a persistence failure must never raise into
the live turn.
"""
from __future__ import annotations

import os
import time

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.ledger_store import EvidenceLedgerStore
from magi_agent.evidence.types import EvidenceRecord


def _ledger(*, session_id: str = "sess-1", turn_id: str = "turn-1") -> EvidenceLedger:
    return EvidenceLedger.model_validate(
        {
            "ledgerId": f"{session_id}:{turn_id}:evidence",
            "sessionId": session_id,
            "turnId": turn_id,
            "runOn": "main",
            "agentRole": "general",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
            "metadata": {},
        }
    )


def _tool_trace_entry(*, session_id: str = "sess-1", turn_id: str = "turn-1", tool_name: str = "Read"):
    record = EvidenceRecord.model_validate(
        {
            "type": "custom:ToolTrace",
            "status": "ok",
            "observedAt": 123.0,
            "source": {"kind": "tool_trace", "toolName": tool_name},
        }
    )
    ledger = _ledger(session_id=session_id, turn_id=turn_id).append_evidence_record(record)
    return ledger.entries[-1]


def test_append_then_read_round_trips_entry(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    entry = _tool_trace_entry(session_id="sess-1", tool_name="Read")

    store.append(entry)
    rows = store.read("sess-1")

    assert len(rows) == 1
    assert rows[0]["kind"] == "evidence_record"
    assert rows[0]["sessionId"] == "sess-1"
    assert rows[0]["turnId"] == "turn-1"
    assert rows[0]["evidenceRef"] == entry.evidence_ref


def test_append_preserves_order(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    ledger = _ledger()
    rec = EvidenceRecord.model_validate(
        {"type": "custom:ToolTrace", "status": "ok", "observedAt": 1.0,
         "source": {"kind": "tool_trace", "toolName": "Read"}}
    )
    for _ in range(3):
        ledger = ledger.append_evidence_record(rec)
    for entry in ledger.entries:
        store.append(entry)

    rows = store.read("sess-1")
    assert [r["sequence"] for r in rows] == [1, 2, 3]


def test_read_unknown_session_returns_empty(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    assert store.read("does-not-exist") == []


def test_append_is_fail_open_on_bad_entry(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    # A non-entry object must not raise (fail-open contract).
    store.append(object())  # type: ignore[arg-type]
    store.append(None)  # type: ignore[arg-type]
    # A valid entry afterwards still persists.
    store.append(_tool_trace_entry(session_id="sess-2"))
    assert len(store.read("sess-2")) == 1


def test_sessions_are_isolated(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    store.append(_tool_trace_entry(session_id="a", tool_name="Read"))
    store.append(_tool_trace_entry(session_id="b", tool_name="Write"))

    assert len(store.read("a")) == 1
    assert len(store.read("b")) == 1
    assert store.read("a")[0]["sessionId"] == "a"


def test_session_id_sanitized_no_path_traversal(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    entry = _tool_trace_entry(session_id="../../etc/passwd")
    store.append(entry)

    # Nothing escaped the base dir.
    escaped = (tmp_path / ".." / ".." / "etc").resolve()
    assert not (escaped / "passwd.jsonl").exists()
    # The file lives under <base>/evidence/.
    written = list((tmp_path / "evidence").glob("*.jsonl"))
    assert len(written) == 1
    assert written[0].resolve().is_relative_to(tmp_path.resolve())


def test_prune_removes_files_older_than_retention(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    store.append(_tool_trace_entry(session_id="old"))
    old_file = tmp_path / "evidence" / "old.jsonl"
    stale = time.time() - 10 * 86400
    os.utime(old_file, (stale, stale))

    removed = store.prune(retention_days=1, max_files=0)

    assert removed == 1
    assert not old_file.exists()


def test_prune_keeps_newest_max_files(tmp_path):
    store = EvidenceLedgerStore(tmp_path)
    for i in range(5):
        store.append(_tool_trace_entry(session_id=f"s{i}"))
        path = tmp_path / "evidence" / f"s{i}.jsonl"
        os.utime(path, (1000 + i, 1000 + i))  # ascending mtime

    removed = store.prune(retention_days=0, max_files=2)

    assert removed == 3
    remaining = sorted(p.stem for p in (tmp_path / "evidence").glob("*.jsonl"))
    assert remaining == ["s3", "s4"]
