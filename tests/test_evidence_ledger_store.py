"""Tests for the durable evidence-ledger reader + retention + shared path.

The CLI collector already WRITES durable evidence (default-ON). These tests
verify the new read/prune surface over those exact files, the shared path
resolver, and that the writer still behaves identically after the refactor.
"""
from __future__ import annotations

import os
import time

from magi_agent.evidence.ledger_store import (
    EvidenceLedgerReader,
    evidence_ledger_filename,
    evidence_ledger_path,
    resolve_evidence_ledger_dir,
)
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.tools.result import ToolResult

_LIFECYCLE = "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED"
_DIR = "MAGI_EVIDENCE_LEDGER_DIR"


def _record(collector, *, session_id="sess-1", turn_id="turn-1", tool_name="Read"):
    collector.record_tool_result(
        session_id=session_id,
        turn_id=turn_id,
        tool_call_id=f"call-{tool_name}-{turn_id}",
        tool_name=tool_name,
        result=ToolResult(status="ok", metadata={"toolName": tool_name}),
    )


# --- shared path resolver --------------------------------------------------

def test_resolve_dir_defaults_to_cwd_magi_evidence(monkeypatch):
    monkeypatch.delenv(_DIR, raising=False)
    base = resolve_evidence_ledger_dir({})
    assert base is not None
    assert base.parts[-2:] == (".magi", "evidence")


def test_resolve_dir_disabled_returns_none():
    assert resolve_evidence_ledger_dir({_DIR: "off"}) is None
    assert resolve_evidence_ledger_dir({_DIR: "0"}) is None


def test_resolve_dir_honors_explicit_path(tmp_path):
    assert resolve_evidence_ledger_dir({_DIR: str(tmp_path)}) == tmp_path


def test_path_disabled_is_none():
    assert evidence_ledger_path("s", env={_DIR: "none"}) is None


def test_filename_is_sanitized():
    # Path separators collapse to "_" (dots are allowed, matching the writer),
    # so no traversal survives — the file stays inside the store dir.
    name = evidence_ledger_filename("../../etc/passwd")
    assert name == ".._.._etc_passwd.jsonl"
    assert "/" not in name and "\\" not in name
    assert evidence_ledger_filename("") == "session.jsonl"


# --- reader round-trips what the REAL writer wrote -------------------------

def test_reader_reads_what_writer_wrote(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIFECYCLE, "1")
    monkeypatch.setenv(_DIR, str(tmp_path))
    collector = LocalToolEvidenceCollector()

    _record(collector, session_id="sess-1", turn_id="turn-1", tool_name="Read")
    _record(collector, session_id="sess-1", turn_id="turn-2", tool_name="Bash")

    reader = EvidenceLedgerReader(tmp_path)
    rows = reader.read("sess-1")
    assert len(rows) >= 2
    assert all(r["sessionId"] == "sess-1" for r in rows)
    assert {"turn-1", "turn-2"}.issubset({r["turnId"] for r in rows})
    assert {"Read", "Bash"}.issubset({r["toolName"] for r in rows})


def test_reader_unknown_session_returns_empty(tmp_path):
    assert EvidenceLedgerReader(tmp_path).read("nope") == []


def test_writer_disabled_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(_LIFECYCLE, "1")
    monkeypatch.setenv(_DIR, "off")
    collector = LocalToolEvidenceCollector()
    _record(collector, session_id="sess-1")
    # In-memory ledger still built; nothing on disk under tmp_path.
    assert not list(tmp_path.glob("*.jsonl"))


# --- prune / retention -----------------------------------------------------

def test_prune_removes_files_older_than_retention(tmp_path):
    old = tmp_path / "old.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 10 * 86400
    os.utime(old, (stale, stale))

    removed = EvidenceLedgerReader(tmp_path).prune(retention_days=1, max_files=0)
    assert removed == 1
    assert not old.exists()


def test_prune_keeps_newest_max_files(tmp_path):
    for i in range(5):
        p = tmp_path / f"s{i}.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))

    removed = EvidenceLedgerReader(tmp_path).prune(retention_days=0, max_files=2)
    assert removed == 3
    assert sorted(p.stem for p in tmp_path.glob("*.jsonl")) == ["s3", "s4"]
