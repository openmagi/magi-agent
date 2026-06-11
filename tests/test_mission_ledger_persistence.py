"""B5 — MissionLedger gets a real local backing.

Previously the tool was contract-only: without MAGI_MISSION_LEDGER_ATTACHED it
blocked (mission_ledger_not_configured), and even WITH the env it returned an
in-memory dict that was never persisted. The durable evidence directory (the
same default-ON ``<cwd>/.magi/evidence`` used by the tool-evidence ledger) is
now a first-class mission backing: records append to ``missions.jsonl`` and the
tool reports honestly that it persisted.
"""
from __future__ import annotations

import json

from magi_agent.plugins.native.missions import mission_ledger
from magi_agent.tools.context import ToolContext


def _context() -> ToolContext:
    return ToolContext(bot_id="bot-1", session_id="sess-1", workspace_root=".")


def test_mission_record_persists_to_local_ledger(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / "ev"))
    monkeypatch.delenv("MAGI_MISSION_LEDGER_ATTACHED", raising=False)

    result = mission_ledger({"objective": "watch the deploy"}, _context())

    assert result.status == "ok"
    lines = (tmp_path / "ev" / "missions.jsonl").read_text().splitlines()
    entry = json.loads(lines[0])
    assert entry["record"]["objective"] == "watch the deploy"
    assert entry["record"]["status"] == "local_recorded"


def test_mission_blocked_when_persistence_off_and_unattached(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", "off")
    monkeypatch.delenv("MAGI_MISSION_LEDGER_ATTACHED", raising=False)
    monkeypatch.chdir(tmp_path)

    result = mission_ledger({"objective": "x"}, _context())

    assert result.status == "blocked"
    assert list(tmp_path.iterdir()) == []


def test_mission_attached_env_still_routes_past_block(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", "off")
    monkeypatch.setenv("MAGI_MISSION_LEDGER_ATTACHED", "1")
    monkeypatch.chdir(tmp_path)

    result = mission_ledger({"objective": "x"}, _context())

    assert result.status == "ok"
