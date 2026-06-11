"""Opt-in durable sink for the local tool-evidence collector.

The in-memory collector keeps only the last 25 per-turn ledgers — a lean live
view, not an audit store. ``MAGI_EVIDENCE_LEDGER_DIR`` opts into appending every
recorded evidence entry to a per-session JSONL file so a durable audit trail
actually exists when the operator wants one. Fail-soft: persistence errors must
never break the tool path.
"""
from __future__ import annotations

import json

from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector


def _record(collector: LocalToolEvidenceCollector, turn: str = "turn-1") -> None:
    collector.record_tool_result(
        session_id="sess-1",
        turn_id=turn,
        tool_call_id="call-1",
        tool_name="FileRead",
        result={"status": "ok", "output": {"content": "x"}},
    )


def test_no_files_written_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MAGI_EVIDENCE_LEDGER_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _record(LocalToolEvidenceCollector())
    assert list(tmp_path.iterdir()) == []


def test_records_appended_as_jsonl_when_enabled(tmp_path, monkeypatch) -> None:
    ledger_dir = tmp_path / "ledger"
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(ledger_dir))
    collector = LocalToolEvidenceCollector()

    _record(collector, turn="turn-1")
    _record(collector, turn="turn-2")

    path = ledger_dir / "sess-1.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) >= 2
    assert all(entry["sessionId"] == "sess-1" for entry in lines)
    assert {entry["turnId"] for entry in lines} == {"turn-1", "turn-2"}
    assert all(entry["toolName"] == "FileRead" for entry in lines)


def test_persistence_failure_is_fail_soft(tmp_path, monkeypatch) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file blocking mkdir", encoding="utf-8")
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(blocker))

    _record(LocalToolEvidenceCollector())  # must not raise
