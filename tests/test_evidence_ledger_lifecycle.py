"""Stage 0+1 — per-turn EvidenceLedger lifecycle -> real ``tool_calls``.

Covers:
  - env flag ``MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED`` (default OFF, "1"->ON);
  - ``LocalToolEvidenceCollector`` builds one single-turn EvidenceLedger per
    ``(session, turn)`` with the synthesized tool-trace records (flag ON), and
    builds NOTHING (returns ``()``) when the flag is OFF;
  - end-to-end via the REAL ``cli.wiring`` tool-context factory + REAL collector:
    ``inspect_self_evidence(query_type="tools_called")`` returns the REAL tool
    calls (name/status/turn) projected from those ledgers;
  - flag-OFF byte-identical: the factory yields ``source_ledger == ()`` and the
    tool returns empty ``tool_calls`` (today's behavior).
"""
from __future__ import annotations

import pytest

from magi_agent.config.env import is_evidence_ledger_lifecycle_enabled
from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.introspection.tool import inspect_self_evidence
from magi_agent.tools.result import ToolResult


_ENV = "MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED"


# ---------------------------------------------------------------------------
# Stage 0 — env flag
# ---------------------------------------------------------------------------


def test_lifecycle_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    assert is_evidence_ledger_lifecycle_enabled() is False
    # Explicit empty/false-y env mapping is also off.
    assert is_evidence_ledger_lifecycle_enabled({}) is False
    assert is_evidence_ledger_lifecycle_enabled({_ENV: "off"}) is False
    assert is_evidence_ledger_lifecycle_enabled({_ENV: "0"}) is False


def test_lifecycle_flag_truthy_opt_in() -> None:
    for value in ("1", "true", "yes", "on", "TRUE", "On"):
        assert is_evidence_ledger_lifecycle_enabled({_ENV: value}) is True


# ---------------------------------------------------------------------------
# Stage 1 — collector ledger lifecycle
# ---------------------------------------------------------------------------


def _record(collector: LocalToolEvidenceCollector, *, turn_id: str, tool_name: str, status: str = "ok") -> None:
    collector.record_tool_result(
        session_id="session-1",
        turn_id=turn_id,
        tool_call_id=f"call-{tool_name}-{turn_id}",
        tool_name=tool_name,
        result=ToolResult(status=status, metadata={"toolName": tool_name}),
    )


def test_collector_builds_one_single_turn_ledger_per_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    collector = LocalToolEvidenceCollector()

    _record(collector, turn_id="turn-1", tool_name="Grep")
    _record(collector, turn_id="turn-1", tool_name="FileRead")
    _record(collector, turn_id="turn-2", tool_name="Bash", status="error")

    ledgers = collector.evidence_ledgers_for_session("session-1")
    assert len(ledgers) == 2
    for ledger in ledgers:
        assert isinstance(ledger, EvidenceLedger)
        # single-turn constraint: every entry shares the ledger's turn.
        assert all(e.turn_id == ledger.turn_id for e in ledger.entries)

    by_turn = {ledger.turn_id: ledger for ledger in ledgers}
    assert by_turn["turn-1"].agent_role == "general"
    # turn-1 captured both tool calls into ONE single-turn ledger.
    turn1_tools = [
        (e.payload["record"]["source"]["toolName"], e.payload["record"]["status"])
        for e in by_turn["turn-1"].entries
        if e.kind == "evidence_record"
    ]
    assert turn1_tools == [("Grep", "ok"), ("FileRead", "ok")]
    # turn-2: error status maps to the evidence "failed" vocabulary.
    turn2_tools = [
        (e.payload["record"]["source"]["toolName"], e.payload["record"]["status"])
        for e in by_turn["turn-2"].entries
        if e.kind == "evidence_record"
    ]
    assert turn2_tools == [("Bash", "failed")]


def test_collector_builds_no_ledger_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    collector = LocalToolEvidenceCollector()

    _record(collector, turn_id="turn-1", tool_name="Grep")
    _record(collector, turn_id="turn-2", tool_name="Bash")

    assert collector.evidence_ledgers_for_session("session-1") == ()


# ---------------------------------------------------------------------------
# Stage 1 — end-to-end via the REAL wiring factory + REAL collector
# ---------------------------------------------------------------------------


def test_end_to_end_real_tool_calls_via_real_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    collector = LocalToolEvidenceCollector()
    _record(collector, turn_id="turn-4", tool_name="Grep")
    _record(collector, turn_id="turn-5", tool_name="Bash", status="error")

    # Build the REAL CLI tool runtime and use its REAL tool_context_factory
    # (no synthetic source_ledger) — exactly the seam production dispatches use.
    runtime = build_cli_tool_runtime(
        workspace_root=str(tmp_path),
        session_id="session-1",
        local_tool_evidence_collector=collector,
    )
    context = runtime.tool_context_factory(adk_tool_context=None)

    result = inspect_self_evidence(query_type="tools_called", context=context)
    tool_calls = result["tool_calls"]
    assert {(c["name"], c["status"], c["turn"]) for c in tool_calls} == {
        ("Grep", "ok", "turn-4"),
        ("Bash", "error", "turn-5"),
    }


def test_flag_off_byte_identical_empty_source_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    collector = LocalToolEvidenceCollector()
    _record(collector, turn_id="turn-4", tool_name="Grep")

    runtime = build_cli_tool_runtime(
        workspace_root=str(tmp_path),
        session_id="session-1",
        local_tool_evidence_collector=collector,
    )
    context = runtime.tool_context_factory(adk_tool_context=None)

    # Flag off: factory yields the empty tuple (byte-identical to today).
    assert context.source_ledger == ()
    result = inspect_self_evidence(query_type="tools_called", context=context)
    assert result["tool_calls"] == []
