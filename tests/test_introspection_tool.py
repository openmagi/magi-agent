"""PR2 — self-introspection tool (``InspectSelfEvidence``).

Covers:
  - each query_type returns the right slice (+ scope + note always);
  - turn filter restricts results to one turn;
  - ref filter post-filters the relevant slice;
  - multi-ledger session merge (2+ single-turn EvidenceLedgers in source_ledger);
  - files_read populated via ReadLedger reachable on ToolContext;
  - flag OFF ⇒ tool bound-but-not-advertised; flag ON ⇒ advertised;
  - handler returns blocked when gate off / invalid query_type.
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.types import EvidenceContractVerdict, EvidenceRecord
from magi_agent.introspection.tool import (
    InspectSelfEvidenceToolHost,
    bind_inspect_self_evidence_handler,
    inspect_self_evidence,
    project_context_session_evidence,
)
from magi_agent.tools.catalog import register_core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.read_ledger import ReadLedger, ReadLedgerConfig
from magi_agent.tools.registry import ToolRegistry


_SESSION = "introspection-session"


def _ledger(turn_id: str) -> EvidenceLedger:
    return EvidenceLedger.model_validate(
        {
            "ledgerId": f"ledger-{turn_id}",
            "sessionId": _SESSION,
            "turnId": turn_id,
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
            "metadata": {},
        }
    )


def _tool_record(name: str, status: str = "ok") -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "TestRun",
            "status": status,
            "observedAt": 1_779_000_001,
            "source": {"kind": "tool_trace", "toolName": name},
            "fields": {"command": "python -m pytest", "exitCode": 0},
        }
    )


def _ledger_with_tool(turn_id: str, name: str, status: str = "ok") -> EvidenceLedger:
    return _ledger(turn_id).append_evidence_record(_tool_record(name, status))


def _ledger_with_verdict(turn_id: str, name: str) -> EvidenceLedger:
    ledger = _ledger_with_tool(turn_id, name)
    matched_record = ledger.entries[0].payload["record"]
    verdict = EvidenceContractVerdict.model_validate(
        {
            "contractId": "tool_evidence_contract",
            "ok": True,
            "state": "pass",
            "enforcement": "audit",
            "missingRequirements": [],
            "matchedEvidence": [matched_record],
            "failures": [],
        }
    )
    return ledger.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(ledger.entries[0].evidence_ref,),
        verdict_id=f"verdict-{turn_id}",
    )


def _read_ledger() -> ReadLedger:
    return ReadLedger(ReadLedgerConfig(enabled=True, localInMemoryEnabled=True))


def _record_read(ledger: ReadLedger, *, path: str, turn_id: str, digest_char: str) -> None:
    ledger.record_read(
        session_id=_SESSION,
        workspace_ref="ws-ref",
        path=path,
        digest="sha256:" + digest_char * 64,
        size_bytes=100,
        mtime_ns=1,
        read_mode="full",
        turn_id=turn_id,
        tool_use_id=f"tool-{turn_id}",
    )


def _context(
    *,
    source_ledger: tuple[object, ...] = (),
    read_ledger: object | None = None,
) -> ToolContext:
    return ToolContext(
        bot_id="bot-test",
        sessionId=_SESSION,
        turnId="turn-1",
        workspaceRoot="/tmp/magi-test",
        sourceLedger=source_ledger,
        readLedger=read_ledger,
    )


# ---------------------------------------------------------------------------
# Pure projection / query-type slices
# ---------------------------------------------------------------------------


def test_summary_returns_all_slices_with_scope_and_note() -> None:
    context = _context(source_ledger=(_ledger_with_verdict("turn-1", "Grep"),))

    result = inspect_self_evidence(query_type="summary", context=context)

    assert set(result) == {"scope", "note", "files_read", "tool_calls", "phases", "verdicts"}
    assert result["scope"]["session_id"] == _SESSION
    assert result["note"] == "projection of session ledger; not raw transcript"
    assert [t["name"] for t in result["tool_calls"]] == ["Grep"]
    assert result["verdicts"][0]["stage"] == "tool_evidence_contract"


def test_tools_called_returns_only_tool_slice() -> None:
    context = _context(source_ledger=(_ledger_with_tool("turn-1", "Grep"),))

    result = inspect_self_evidence(query_type="tools_called", context=context)

    assert set(result) == {"scope", "note", "tool_calls"}
    assert result["tool_calls"][0]["name"] == "Grep"
    assert result["tool_calls"][0]["status"] == "ok"


def test_verifier_verdicts_returns_only_verdict_slice() -> None:
    context = _context(source_ledger=(_ledger_with_verdict("turn-1", "Grep"),))

    result = inspect_self_evidence(query_type="verifier_verdicts", context=context)

    assert set(result) == {"scope", "note", "verdicts"}
    assert result["verdicts"][0]["result"] == "pass"


def test_phases_slice_is_empty_until_pr3() -> None:
    context = _context(source_ledger=(_ledger_with_tool("turn-1", "Grep"),))

    result = inspect_self_evidence(query_type="phases", context=context)

    assert set(result) == {"scope", "note", "phases"}
    assert result["phases"] == []


# ---------------------------------------------------------------------------
# files_read via ReadLedger reachable on ToolContext
# ---------------------------------------------------------------------------


def test_files_read_populated_from_context_read_ledger() -> None:
    read_ledger = _read_ledger()
    _record_read(read_ledger, path="docs/X.pdf", turn_id="turn-1", digest_char="a")
    context = _context(
        source_ledger=(_ledger("turn-1"),),
        read_ledger=read_ledger,
    )

    result = inspect_self_evidence(query_type="files_read", context=context)

    assert set(result) == {"scope", "note", "files_read"}
    assert len(result["files_read"]) == 1
    entry = result["files_read"][0]
    assert entry["path"] == "docs/X.pdf"
    assert entry["sha256"] == "sha256:" + "a" * 64
    assert entry["bytes"] == 100
    assert entry["turn"] == "turn-1"


def test_files_read_works_without_any_evidence_ledger() -> None:
    read_ledger = _read_ledger()
    _record_read(read_ledger, path="docs/only.pdf", turn_id="turn-1", digest_char="b")
    context = _context(read_ledger=read_ledger)  # no source_ledger

    result = inspect_self_evidence(query_type="files_read", context=context)

    assert [f["path"] for f in result["files_read"]] == ["docs/only.pdf"]
    assert result["scope"]["session_id"] == _SESSION


# ---------------------------------------------------------------------------
# Multi-ledger session merge
# ---------------------------------------------------------------------------


def test_multi_ledger_merge_covers_whole_session() -> None:
    ledgers = (
        _ledger_with_tool("turn-4", "Grep"),
        _ledger_with_tool("turn-5", "Bash", status="failed"),
    )
    read_ledger = _read_ledger()
    _record_read(read_ledger, path="docs/turn4.md", turn_id="turn-4", digest_char="d")
    _record_read(read_ledger, path="docs/turn5.md", turn_id="turn-5", digest_char="e")
    context = _context(source_ledger=ledgers, read_ledger=read_ledger)

    view = project_context_session_evidence(context)
    assert {t.name for t in view.tool_calls} == {"Grep", "Bash"}
    assert {f.path for f in view.files_read} == {"docs/turn4.md", "docs/turn5.md"}
    assert set(view.scope.turns_covered) == {"turn-4", "turn-5"}

    summary = inspect_self_evidence(query_type="summary", context=context)
    assert {t["name"] for t in summary["tool_calls"]} == {"Grep", "Bash"}
    assert {f["path"] for f in summary["files_read"]} == {"docs/turn4.md", "docs/turn5.md"}


def test_read_ledger_not_duplicated_across_multiple_ledgers() -> None:
    ledgers = (_ledger("turn-4"), _ledger("turn-5"))
    read_ledger = _read_ledger()
    _record_read(read_ledger, path="docs/once.md", turn_id="turn-4", digest_char="c")
    context = _context(source_ledger=ledgers, read_ledger=read_ledger)

    result = inspect_self_evidence(query_type="files_read", context=context)

    # Despite two EvidenceLedgers, the single read entry appears exactly once.
    assert [f["path"] for f in result["files_read"]] == ["docs/once.md"]


# ---------------------------------------------------------------------------
# turn + ref filters
# ---------------------------------------------------------------------------


def test_turn_filter_restricts_to_one_turn() -> None:
    ledgers = (
        _ledger_with_tool("turn-4", "Grep"),
        _ledger_with_tool("turn-5", "Bash"),
    )
    context = _context(source_ledger=ledgers)

    result = inspect_self_evidence(query_type="tools_called", context=context, turn="turn-4")

    assert [t["name"] for t in result["tool_calls"]] == ["Grep"]
    assert result["scope"]["turns_covered"] == ["turn-4"]


def test_ref_filter_post_filters_files_read() -> None:
    read_ledger = _read_ledger()
    _record_read(read_ledger, path="docs/report.pdf", turn_id="turn-1", digest_char="a")
    _record_read(read_ledger, path="docs/notes.md", turn_id="turn-1", digest_char="b")
    context = _context(source_ledger=(_ledger("turn-1"),), read_ledger=read_ledger)

    result = inspect_self_evidence(query_type="files_read", context=context, ref="report")

    assert [f["path"] for f in result["files_read"]] == ["docs/report.pdf"]


def test_ref_filter_is_case_insensitive_on_tool_name() -> None:
    ledgers = (
        _ledger_with_tool("turn-1", "Grep"),
        _ledger_with_tool("turn-1", "Bash"),
    )
    context = _context(source_ledger=ledgers)

    result = inspect_self_evidence(query_type="tools_called", context=context, ref="gr")

    assert [t["name"] for t in result["tool_calls"]] == ["Grep"]


# ---------------------------------------------------------------------------
# Flag gating: bound-but-not-advertised vs advertised
# ---------------------------------------------------------------------------


def test_tool_not_advertised_when_gate_off() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    InspectSelfEvidenceToolHost(enabled=False).bind(registry)

    registration = registry.resolve_registration("InspectSelfEvidence")
    assert registration is not None
    assert registration.handler is not None  # bound for structured blocked
    assert registry.is_enabled("InspectSelfEvidence") is False
    available = registry.list_available(mode="act")
    assert all(m.name != "InspectSelfEvidence" for m in available)


def test_tool_advertised_when_gate_on() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    InspectSelfEvidenceToolHost(enabled=True).bind(registry)

    assert registry.is_enabled("InspectSelfEvidence") is True
    available = registry.list_available(mode="act")
    assert any(m.name == "InspectSelfEvidence" for m in available)
    # Also advertised in plan mode (read-only/introspective).
    plan_available = registry.list_available(mode="plan")
    assert any(m.name == "InspectSelfEvidence" for m in plan_available)


def test_binder_reads_env_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_SELF_INTROSPECTION_ENABLED", raising=False)
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    bind_inspect_self_evidence_handler(registry)

    assert registry.is_enabled("InspectSelfEvidence") is False


def test_binder_reads_env_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_SELF_INTROSPECTION_ENABLED", "1")
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    bind_inspect_self_evidence_handler(registry)

    assert registry.is_enabled("InspectSelfEvidence") is True


# ---------------------------------------------------------------------------
# Handler dispatch behavior
# ---------------------------------------------------------------------------


def test_handler_blocked_when_gate_off() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    InspectSelfEvidenceToolHost(enabled=False).bind(registry)

    registration = registry.resolve_registration("InspectSelfEvidence")
    assert registration is not None and registration.handler is not None

    result = asyncio.run(
        registration.handler({"query_type": "summary"}, _context())
    )
    assert result.status == "blocked"
    assert result.error_code == "self_introspection_disabled"


def test_handler_ok_when_gate_on_returns_projection() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    InspectSelfEvidenceToolHost(enabled=True).bind(registry)

    registration = registry.resolve_registration("InspectSelfEvidence")
    assert registration is not None and registration.handler is not None

    context = _context(source_ledger=(_ledger_with_tool("turn-1", "Grep"),))
    result = asyncio.run(
        registration.handler({"query_type": "tools_called"}, context)
    )
    assert result.status == "ok"
    assert isinstance(result.output, dict)
    assert result.output["tool_calls"][0]["name"] == "Grep"


def test_handler_blocked_on_invalid_query_type() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    InspectSelfEvidenceToolHost(enabled=True).bind(registry)

    registration = registry.resolve_registration("InspectSelfEvidence")
    assert registration is not None and registration.handler is not None

    result = asyncio.run(
        registration.handler({"query_type": "nonsense"}, _context())
    )
    assert result.status == "blocked"
    assert result.error_code == "self_introspection_invalid_query_type"
