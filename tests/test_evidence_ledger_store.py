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
    serve_evidence_ledger_dir,
    write_evidence_records,
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


# --- write_evidence_records ------------------------------------------------


def test_write_evidence_records_round_trip(tmp_path):
    """write_evidence_records writes JSONL that EvidenceLedgerReader.read() returns."""
    records = [{"toolCallId": "c1", "toolName": "Read", "status": "ok", "record": {"x": 1}}]
    write_evidence_records(tmp_path, session_id="s1", turn_id="t1", records=records)

    reader = EvidenceLedgerReader(tmp_path)
    rows = reader.read("s1")
    assert len(rows) == 1
    row = rows[0]
    assert row["sessionId"] == "s1"
    assert row["turnId"] == "t1"
    assert row["toolCallId"] == "c1"
    assert row["toolName"] == "Read"
    assert row["status"] == "ok"
    assert row["record"] == {"x": 1}


def test_write_evidence_records_multiple_records(tmp_path):
    """Multiple records produce multiple JSONL lines."""
    records = [
        {"toolCallId": "c1", "toolName": "Read", "status": "ok", "record": {"a": 1}},
        {"toolCallId": "c2", "toolName": "Bash", "status": "error", "record": {"b": 2}},
    ]
    write_evidence_records(tmp_path, session_id="sess", turn_id="turn-1", records=records)

    rows = EvidenceLedgerReader(tmp_path).read("sess")
    assert len(rows) == 2
    tool_names = {r["toolName"] for r in rows}
    assert tool_names == {"Read", "Bash"}


def test_write_evidence_records_appends(tmp_path):
    """Successive calls append to the same file."""
    write_evidence_records(
        tmp_path, session_id="s2", turn_id="t1",
        records=[{"toolCallId": "c1", "toolName": "A", "status": "ok", "record": {}}],
    )
    write_evidence_records(
        tmp_path, session_id="s2", turn_id="t2",
        records=[{"toolCallId": "c2", "toolName": "B", "status": "ok", "record": {}}],
    )

    rows = EvidenceLedgerReader(tmp_path).read("s2")
    assert len(rows) == 2
    assert {r["turnId"] for r in rows} == {"t1", "t2"}


def test_write_evidence_records_owner_only_permissions(tmp_path):
    """The written file has 0o600 permissions and directory has 0o700."""
    write_evidence_records(
        tmp_path, session_id="perm-test", turn_id="t1",
        records=[{"toolCallId": "x", "toolName": "T", "status": "ok", "record": {}}],
    )
    from pathlib import Path
    file = tmp_path / evidence_ledger_filename("perm-test")
    assert file.exists()
    assert oct(file.stat().st_mode & 0o777) == oct(0o600)
    assert oct(tmp_path.stat().st_mode & 0o777) == oct(0o700)


def test_write_evidence_records_fail_open_on_bad_dir(tmp_path):
    """write_evidence_records never raises even with a bad path."""
    bad_dir = tmp_path / "nonexistent" / "deeply" / "nested"
    # Should not raise; will create dirs or silently fail
    try:
        write_evidence_records(
            bad_dir, session_id="s", turn_id="t",
            records=[{"toolCallId": "x", "toolName": "T", "status": "ok", "record": {}}],
        )
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"write_evidence_records should be fail-open, got: {e}") from e


def test_write_evidence_records_empty_records(tmp_path):
    """Empty records list produces no file."""
    write_evidence_records(tmp_path, session_id="empty", turn_id="t1", records=[])
    # No file should exist for this session (no records written)
    file = tmp_path / evidence_ledger_filename("empty")
    assert not file.exists()


# --- serve_evidence_ledger_dir ---------------------------------------------

_DIR_ENV = "MAGI_EVIDENCE_LEDGER_DIR"


def test_serve_evidence_ledger_dir_default_uses_default_dir(tmp_path):
    """When env is unset (or empty), returns the default_dir."""
    result = serve_evidence_ledger_dir(default_dir=tmp_path, env={})
    assert result == tmp_path


def test_serve_evidence_ledger_dir_explicit_path_overrides(tmp_path):
    """An explicit MAGI_EVIDENCE_LEDGER_DIR path is honored."""
    override = tmp_path / "custom"
    result = serve_evidence_ledger_dir(default_dir=tmp_path, env={_DIR_ENV: str(override)})
    assert result == override


def test_serve_evidence_ledger_dir_disable_tokens_return_none(tmp_path):
    """All disable tokens return None."""
    for token in ("off", "0", "false", "none", "disable", "disabled", "OFF", "False"):
        result = serve_evidence_ledger_dir(default_dir=tmp_path, env={_DIR_ENV: token})
        assert result is None, f"Expected None for token {token!r}, got {result}"


def test_serve_evidence_ledger_dir_none_env_uses_os_environ(tmp_path, monkeypatch):
    """When env=None, falls back to os.environ."""
    monkeypatch.delenv(_DIR_ENV, raising=False)
    result = serve_evidence_ledger_dir(default_dir=tmp_path, env=None)
    # With no env var, should use the default_dir (NOT cwd/.magi/evidence like resolve_evidence_ledger_dir)
    assert result == tmp_path
