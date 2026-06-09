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


# ---------------------------------------------------------------------------
# Stage 3 — phase-reached evidence
# ---------------------------------------------------------------------------


def _phase_entries(ledger: EvidenceLedger) -> list[tuple[str, object]]:
    return [
        (e.payload["record"]["fields"]["phaseName"], e.payload["record"]["fields"]["reached"])
        for e in ledger.entries
        if e.kind == "evidence_record"
        and e.payload["record"]["type"] == "custom:PhaseReached"
    ]


def test_record_phase_reached_appends_phase_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    collector = LocalToolEvidenceCollector()

    collector.record_phase_reached("session-1", "turn-1", "patch_generation")

    ledgers = collector.evidence_ledgers_for_session("session-1")
    assert len(ledgers) == 1
    assert _phase_entries(ledgers[0]) == [("patch_generation", True)]


def test_record_phase_reached_shares_turn_ledger_with_tool_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    collector = LocalToolEvidenceCollector()

    _record(collector, turn_id="turn-1", tool_name="Grep")
    collector.record_phase_reached("session-1", "turn-1", "analysis")
    _record(collector, turn_id="turn-1", tool_name="Bash")

    ledgers = collector.evidence_ledgers_for_session("session-1")
    # One single-turn ledger holds BOTH tool traces and the phase marker, with a
    # contiguous append-only sequence preserved.
    assert len(ledgers) == 1
    ledger = ledgers[0]
    assert [e.sequence for e in ledger.entries] == [1, 2, 3]
    kinds = [
        e.payload["record"]["type"]
        for e in ledger.entries
        if e.kind == "evidence_record"
    ]
    assert kinds == ["custom:ToolTrace", "custom:PhaseReached", "custom:ToolTrace"]


def test_record_phase_reached_no_record_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    collector = LocalToolEvidenceCollector()

    collector.record_phase_reached("session-1", "turn-1", "analysis")

    assert collector.evidence_ledgers_for_session("session-1") == ()


def test_record_phase_reached_ignores_empty_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    collector = LocalToolEvidenceCollector()

    collector.record_phase_reached("", "turn-1", "analysis")
    collector.record_phase_reached("session-1", "", "analysis")
    collector.record_phase_reached("session-1", "turn-1", "")

    assert collector.evidence_ledgers_for_session("session-1") == ()


def test_end_to_end_phases_via_real_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    collector = LocalToolEvidenceCollector()
    _record(collector, turn_id="turn-7", tool_name="Grep")
    collector.record_phase_reached("session-1", "turn-7", "patch_generation")

    runtime = build_cli_tool_runtime(
        workspace_root=str(tmp_path),
        session_id="session-1",
        local_tool_evidence_collector=collector,
    )
    context = runtime.tool_context_factory(adk_tool_context=None)

    result = inspect_self_evidence(query_type="phases", context=context)
    phases = result["phases"]
    assert {(p["name"], p["reached"], p["turn"]) for p in phases} == {
        ("patch_generation", True, "turn-7"),
    }
    # The phase marker did not leak into tool_calls.
    tools = inspect_self_evidence(query_type="tools_called", context=context)["tool_calls"]
    assert {(c["name"], c["turn"]) for c in tools} == {("Grep", "turn-7")}


# ---------------------------------------------------------------------------
# Stage 2 — verifier-verdict evidence
# ---------------------------------------------------------------------------


def _verdict_entries(ledger: EvidenceLedger) -> list[tuple[str, str]]:
    return [
        (e.payload["record"]["fields"]["stage"], e.payload["record"]["fields"]["result"])
        for e in ledger.entries
        if e.kind == "evidence_record"
        and e.payload["record"]["type"] == "custom:VerifierVerdict"
    ]


def test_record_verifier_verdict_appends_verdict_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    collector = LocalToolEvidenceCollector()

    collector.record_verifier_verdict(
        "session-1", "turn-1", "tool_evidence_contract", "pass"
    )

    ledgers = collector.evidence_ledgers_for_session("session-1")
    assert len(ledgers) == 1
    assert _verdict_entries(ledgers[0]) == [("tool_evidence_contract", "pass")]


def test_record_verifier_verdict_no_record_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    collector = LocalToolEvidenceCollector()

    collector.record_verifier_verdict(
        "session-1", "turn-1", "tool_evidence_contract", "pass"
    )

    assert collector.evidence_ledgers_for_session("session-1") == ()


def test_record_verifier_verdict_ignores_empty_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    collector = LocalToolEvidenceCollector()

    collector.record_verifier_verdict("", "turn-1", "stage", "pass")
    collector.record_verifier_verdict("session-1", "", "stage", "pass")
    collector.record_verifier_verdict("session-1", "turn-1", "", "pass")
    collector.record_verifier_verdict("session-1", "turn-1", "stage", "")

    assert collector.evidence_ledgers_for_session("session-1") == ()


def test_record_verifier_verdict_shares_turn_ledger_with_tool_and_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    collector = LocalToolEvidenceCollector()

    _record(collector, turn_id="turn-1", tool_name="Grep")
    collector.record_phase_reached("session-1", "turn-1", "analysis")
    collector.record_verifier_verdict(
        "session-1", "turn-1", "tool_evidence_contract", "pass"
    )

    ledgers = collector.evidence_ledgers_for_session("session-1")
    # One single-turn ledger holds the tool trace, phase marker, and verdict,
    # with a contiguous append-only sequence preserved.
    assert len(ledgers) == 1
    ledger = ledgers[0]
    assert [e.sequence for e in ledger.entries] == [1, 2, 3]
    kinds = [
        e.payload["record"]["type"]
        for e in ledger.entries
        if e.kind == "evidence_record"
    ]
    assert kinds == [
        "custom:ToolTrace",
        "custom:PhaseReached",
        "custom:VerifierVerdict",
    ]


def test_end_to_end_verdicts_via_real_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    collector = LocalToolEvidenceCollector()
    _record(collector, turn_id="turn-9", tool_name="Grep")
    collector.record_verifier_verdict(
        "session-1", "turn-9", "tool_evidence_contract", "pass"
    )

    runtime = build_cli_tool_runtime(
        workspace_root=str(tmp_path),
        session_id="session-1",
        local_tool_evidence_collector=collector,
    )
    context = runtime.tool_context_factory(adk_tool_context=None)

    result = inspect_self_evidence(query_type="verifier_verdicts", context=context)
    verdicts = result["verdicts"]
    assert {(v["stage"], v["result"], v["turn"]) for v in verdicts} == {
        ("tool_evidence_contract", "pass", "turn-9"),
    }
    # The verdict marker did not leak into tool_calls.
    tools = inspect_self_evidence(query_type="tools_called", context=context)["tool_calls"]
    assert {(c["name"], c["turn"]) for c in tools} == {("Grep", "turn-9")}


# ---------------------------------------------------------------------------
# Composition — summary returns all three slices together, no cross-contamination
# ---------------------------------------------------------------------------


def test_end_to_end_summary_composes_all_slices_via_real_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    # A single turn carries a tool-trace + a phase + a verifier verdict.
    collector = LocalToolEvidenceCollector()
    _record(collector, turn_id="turn-11", tool_name="Grep")
    collector.record_phase_reached("session-1", "turn-11", "patch_generation")
    collector.record_verifier_verdict(
        "session-1", "turn-11", "tool_evidence_contract", "pass"
    )

    runtime = build_cli_tool_runtime(
        workspace_root=str(tmp_path),
        session_id="session-1",
        local_tool_evidence_collector=collector,
    )
    context = runtime.tool_context_factory(adk_tool_context=None)

    result = inspect_self_evidence(query_type="summary", context=context)

    # All THREE slices populated together from the one composed turn ledger.
    assert {(c["name"], c["status"], c["turn"]) for c in result["tool_calls"]} == {
        ("Grep", "ok", "turn-11"),
    }
    assert {(p["name"], p["reached"], p["turn"]) for p in result["phases"]} == {
        ("patch_generation", True, "turn-11"),
    }
    assert {(v["stage"], v["result"], v["turn"]) for v in result["verdicts"]} == {
        ("tool_evidence_contract", "pass", "turn-11"),
    }

    # No cross-contamination: the phase/verdict markers never appear as tool
    # calls (they carry no toolName, so the tool-call normalizer skips them).
    tool_names = {c["name"] for c in result["tool_calls"]}
    assert "patch_generation" not in tool_names
    assert "tool_evidence_contract" not in tool_names


# ---------------------------------------------------------------------------
# Bounding — evidence_ledgers_for_session caps to the most recent K turns
# ---------------------------------------------------------------------------


def test_evidence_ledgers_for_session_caps_to_most_recent_k_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    from magi_agent.evidence.local_tool_collector import _MAX_SESSION_LEDGERS

    collector = LocalToolEvidenceCollector()
    total = _MAX_SESSION_LEDGERS + 10
    for i in range(total):
        _record(collector, turn_id=f"turn-{i:04d}", tool_name="Grep")

    ledgers = collector.evidence_ledgers_for_session("session-1")
    # Exactly the most recent K turns, in turn (insertion) order — older dropped.
    assert len(ledgers) == _MAX_SESSION_LEDGERS
    assert [ledger.turn_id for ledger in ledgers] == [
        f"turn-{i:04d}" for i in range(total - _MAX_SESSION_LEDGERS, total)
    ]
    # Retention is bounded in the process-lifetime backing map too, not just in
    # the accessor's returned tuple.
    assert len(collector._ledgers) == _MAX_SESSION_LEDGERS
    assert [turn_id for _session_id, turn_id in collector._ledgers] == [
        f"turn-{i:04d}" for i in range(total - _MAX_SESSION_LEDGERS, total)
    ]
