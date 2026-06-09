from __future__ import annotations

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.types import EvidenceRecord
from magi_agent.introspection import SessionEvidenceView, project_session_evidence
from magi_agent.tools.read_ledger import ReadLedger, ReadLedgerConfig


def _base_ledger(turn_id: str = "turn-1") -> EvidenceLedger:
    return EvidenceLedger.model_validate(
        {
            "ledgerId": "introspection-ledger",
            "sessionId": "introspection-session",
            "turnId": turn_id,
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
            "metadata": {},
        }
    )


def _file_read_record(
    *,
    path: str,
    digest: str,
    size_bytes: int,
    observed_at: int = 1_779_000_000,
) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "SourceInspection",
            "status": "ok",
            "observedAt": observed_at,
            "source": {"kind": "tool_trace", "toolName": "Read"},
            "fields": {
                "path": path,
                "sha256": digest,
                "sizeBytes": size_bytes,
            },
        }
    )


def _tool_call_record(
    *,
    name: str,
    status: str = "ok",
    observed_at: int = 1_779_000_001,
) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "TestRun",
            "status": status,
            "observedAt": observed_at,
            "source": {"kind": "tool_trace", "toolName": name},
            "fields": {"command": "python -m pytest", "exitCode": 0},
        }
    )


def _phase_record(
    *,
    name: str,
    observed_at: int = 1_779_000_002,
) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "PlanVerifier",
            "status": "ok",
            "observedAt": observed_at,
            "source": {"kind": "verifier", "verifierName": "plan"},
            "fields": {"phase": name},
        }
    )


def test_empty_ledger_yields_empty_view() -> None:
    view = project_session_evidence(_base_ledger())

    assert isinstance(view, SessionEvidenceView)
    assert view.scope.session_id == "introspection-session"
    assert view.scope.turns_covered == ()
    assert view.files_read == ()
    assert view.tool_calls == ()
    assert view.phases == ()
    assert view.verdicts == ()
    assert view.note == "projection of session ledger; not raw transcript"


def test_file_read_evidence_is_projected() -> None:
    digest = "sha256:" + "a" * 64
    ledger = _base_ledger().append_evidence_record(
        _file_read_record(path="docs/X.md", digest=digest, size_bytes=1234)
    )

    view = project_session_evidence(ledger)

    assert len(view.files_read) == 1
    entry = view.files_read[0]
    assert entry.path == "docs/X.md"
    assert entry.sha256 == digest
    assert entry.bytes == 1234
    assert entry.turn_id == "turn-1"
    assert view.tool_calls == ()
    assert view.scope.turns_covered == ("turn-1",)


def test_tool_calls_phases_and_verdicts_are_projected() -> None:
    ok_digest = "sha256:" + "b" * 64
    ledger = (
        _base_ledger()
        .append_evidence_record(
            _file_read_record(path="src/a.py", digest=ok_digest, size_bytes=10)
        )
        .append_evidence_record(_tool_call_record(name="Grep", status="ok"))
        .append_evidence_record(_phase_record(name="B"))
    )

    view = project_session_evidence(ledger)

    assert [t.name for t in view.tool_calls] == ["Grep"]
    assert view.tool_calls[0].status == "ok"
    assert view.tool_calls[0].turn_id == "turn-1"
    assert [p.name for p in view.phases] == ["B"]
    assert view.phases[0].reached is True
    assert view.phases[0].turn_id == "turn-1"


def test_failed_tool_call_status_is_preserved() -> None:
    ledger = _base_ledger().append_evidence_record(
        _tool_call_record(name="Bash", status="failed")
    )

    view = project_session_evidence(ledger)

    assert len(view.tool_calls) == 1
    assert view.tool_calls[0].name == "Bash"
    assert view.tool_calls[0].status == "failed"


def test_verifier_verdict_is_projected() -> None:
    from magi_agent.evidence.types import EvidenceContractVerdict

    digest = "sha256:" + "c" * 64
    ledger = _base_ledger().append_evidence_record(
        _file_read_record(path="docs/Y.md", digest=digest, size_bytes=5)
    )
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
    ledger = ledger.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(ledger.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    view = project_session_evidence(ledger)

    assert len(view.verdicts) == 1
    assert view.verdicts[0].stage == "tool_evidence_contract"
    assert view.verdicts[0].result == "pass"
    assert view.verdicts[0].turn_id == "turn-1"


def test_turn_filter_restricts_to_one_turn() -> None:
    # EvidenceLedger entries all belong to one turn, so multi-turn coverage is
    # expressed by projecting across distinct ledgers / read-ledger entries.
    digest_a = "sha256:" + "d" * 64
    digest_b = "sha256:" + "e" * 64
    read_ledger = ReadLedger(
        ReadLedgerConfig(enabled=True, localInMemoryEnabled=True)
    )
    read_ledger.record_read(
        session_id="introspection-session",
        workspace_ref="ws-ref",
        path="docs/turn4.md",
        digest=digest_a,
        size_bytes=11,
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-4",
        tool_use_id="tool-4",
    )
    read_ledger.record_read(
        session_id="introspection-session",
        workspace_ref="ws-ref",
        path="docs/turn5.md",
        digest=digest_b,
        size_bytes=22,
        mtime_ns=2,
        read_mode="full",
        turn_id="turn-5",
        tool_use_id="tool-5",
    )

    full = project_session_evidence(_base_ledger(), read_ledger=read_ledger)
    assert {f.path for f in full.files_read} == {"docs/turn4.md", "docs/turn5.md"}
    assert set(full.scope.turns_covered) == {"turn-4", "turn-5"}

    filtered = project_session_evidence(
        _base_ledger(), read_ledger=read_ledger, turn_filter="turn-4"
    )
    assert [f.path for f in filtered.files_read] == ["docs/turn4.md"]
    assert filtered.scope.turns_covered == ("turn-4",)


def test_read_ledger_file_reads_are_merged_with_ledger_reads() -> None:
    ledger_digest = "sha256:" + "1" * 64
    rl_digest = "sha256:" + "2" * 64
    ledger = _base_ledger().append_evidence_record(
        _file_read_record(path="docs/ledger.md", digest=ledger_digest, size_bytes=7)
    )
    read_ledger = ReadLedger(
        ReadLedgerConfig(enabled=True, localInMemoryEnabled=True)
    )
    read_ledger.record_read(
        session_id="introspection-session",
        workspace_ref="ws-ref",
        path="docs/readledger.md",
        digest=rl_digest,
        size_bytes=9,
        mtime_ns=1,
        read_mode="full",
        turn_id="turn-1",
        tool_use_id="tool-1",
    )

    view = project_session_evidence(ledger, read_ledger=read_ledger)

    paths = {f.path for f in view.files_read}
    assert paths == {"docs/ledger.md", "docs/readledger.md"}


def test_view_round_trips_through_serialization() -> None:
    digest = "sha256:" + "f" * 64
    ledger = (
        _base_ledger()
        .append_evidence_record(
            _file_read_record(path="docs/Z.md", digest=digest, size_bytes=3)
        )
        .append_evidence_record(_tool_call_record(name="Grep"))
        .append_evidence_record(_phase_record(name="C"))
    )
    view = project_session_evidence(ledger)

    dumped = view.model_dump(by_alias=True, mode="json")
    restored = SessionEvidenceView.model_validate(dumped)

    assert restored == view
    assert restored.model_dump(by_alias=True, mode="json") == dumped


def test_view_is_frozen() -> None:
    import pytest

    view = project_session_evidence(_base_ledger())
    with pytest.raises(Exception):
        view.note = "mutated"  # type: ignore[misc]
