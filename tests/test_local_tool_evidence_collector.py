from __future__ import annotations

import json

from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus
from magi_agent.tools.result import ToolResult


def _source_projection_result() -> ToolResult:
    """A read-only tool result carrying a source-ledger public projection,
    exactly as FileRead / DocumentRead emit it."""
    from magi_agent.evidence.source_ledger import (
        LocalResearchSourceLedger,
        public_source_ledger_report,
    )

    ledger = LocalResearchSourceLedger(
        ledgerId="ledger:proj",
        sessionId="session:proj",
        turnId="turn-src",
    )
    ledger.record_source(
        {
            "turnId": "turn-src",
            "toolName": "FileRead",
            "toolUseId": "FileRead:local",
            "evidenceType": "SourceInspection",
            "kind": "file",
            "uri": "workspace://notes.md",
            "inspected": True,
            "contentType": "text/plain",
        }
    )
    projection = public_source_ledger_report(ledger).model_dump(
        by_alias=True, mode="json", warnings=False
    )
    return ToolResult(
        status="ok",
        output={"text": "summary"},
        metadata={"toolName": "FileRead", "sourceProjection": projection},
    )


def test_source_projection_flag_off_not_projected(monkeypatch) -> None:
    """Item 4: BOTH gates OFF => source-ledger projection does NOT enter _records.

    The projection fires when EITHER MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED OR
    the citation master switch MAGI_SOURCE_CITATION_ENABLED (profile-aware
    default-ON) is on (design 14, Wave 1 deferral), so the true flag-off
    (byte-identical-to-main) path disables both.
    """
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="session:proj",
        turn_id="turn-src",
        tool_call_id="call-src",
        tool_name="FileRead",
        result=_source_projection_result(),
    )
    records = collector.collect_for_turn("turn-src")
    types = {getattr(r, "type", None) for r in records}
    assert "SourceInspection" not in types


def test_source_projection_flag_on_projects_source_inspection(monkeypatch) -> None:
    """Item 4 CORE: ON ⇒ each inspected source becomes a SourceInspection record
    in the collector via the EXISTING to_evidence_record()."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="session:proj",
        turn_id="turn-src",
        tool_call_id="call-src",
        tool_name="FileRead",
        result=_source_projection_result(),
    )
    records = collector.collect_for_turn("turn-src")
    source_records = [r for r in records if getattr(r, "type", None) == "SourceInspection"]
    assert len(source_records) == 1


def test_local_tool_evidence_collector_feeds_pre_final_verifier_bus() -> None:
    collector = LocalToolEvidenceCollector()

    collector.record_tool_result(
        session_id="session-1",
        turn_id="turn-1",
        tool_call_id="call-test",
        tool_name="Bash",
        result=ToolResult(
            status="ok",
            output="pytest passed",
            metadata={
                "toolName": "Bash",
                "toolCallId": "call-test",
                "evidence": {
                    "type": "TestRun",
                    "status": "ok",
                    "observedAt": 123,
                    "fields": {"command": "pytest tests/test_example.py", "exitCode": 0},
                    "source": {"kind": "tool_trace"},
                    "metadata": {
                        "evidenceRef": "evidence:test-run",
                        "validatorRef": "verifier:dev-coding:test-evidence",
                    },
                },
            },
        ),
    )

    records = collector.collect_for_turn("turn-1")
    bus = execute_pre_final_verifier_bus(
        required_evidence=("evidence:test-run",),
        required_validators=("verifier:dev-coding:test-evidence",),
        observed_public_refs=(),
        evidence_records=records,
    )

    assert bus["decision"] == "pass"
    assert bus["evidenceRecordCount"] == 1
    assert set(bus["matchedRefs"]) == {
        "evidence:test-run",
        "verifier:dev-coding:test-evidence",
    }


def test_local_tool_evidence_collector_merges_ga_receipts_and_sanitizes_tool_receipts() -> None:
    class _GaReceiptStore:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def entries_for_turn(self, turn_id: str) -> tuple[dict[str, object], ...]:
            self.calls.append(turn_id)
            return ({"evidenceRef": "evidence:ga-control-receipt"},)

    ga_store = _GaReceiptStore()
    collector = LocalToolEvidenceCollector(general_automation_receipts=ga_store)

    collector.record_tool_result(
        session_id="session-1",
        turn_id="turn-1",
        tool_call_id="call-diff",
        tool_name="GitDiff",
        result=ToolResult(
            status="ok",
            output={"raw": "Authorization: Bearer live-token"},
            metadata={
                "toolName": "GitDiff",
                "toolCallId": "call-diff",
                "evidenceRefs": ["evidence:git-diff"],
                "validatorRefs": ["verifier:dev-coding:test-evidence"],
                "toolExecutionReceipt": {
                    "receiptId": "receipt:local-git-diff",
                    "toolName": "GitDiff",
                    "status": "success",
                },
                "rawOutput": "Authorization: Bearer live-token",
            },
        ),
    )

    records = collector.collect_for_turn("turn-1")
    bus = execute_pre_final_verifier_bus(
        required_evidence=("evidence:git-diff", "evidence:ga-control-receipt"),
        required_validators=("verifier:dev-coding:test-evidence",),
        observed_public_refs=(),
        evidence_records=records,
    )

    encoded = json.dumps(records, default=_json_default, sort_keys=True)
    assert ga_store.calls == ["turn-1"]
    assert bus["decision"] == "pass"
    assert "live-token" not in encoded
    assert "rawOutput" not in encoded
    assert "receipt:local-git-diff" in encoded


def _json_default(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, mode="json", warnings=False)
    return repr(value)


# --- first-party activity storage (Task 3) ---
from magi_agent.evidence.first_party_activity import (  # noqa: E402
    FIRST_PARTY_ACTIVITY_REFS,
    FirstPartyActivity,
    build_first_party_activities,
)
from magi_agent.tools.context import ToolContext as _FPContext  # noqa: E402
from magi_agent.tools.result import ToolResult as _FPToolResult  # noqa: E402


def _fp_activity(status: str = "ok", tool: str = "web_search") -> FirstPartyActivity:
    context = _FPContext.model_validate(
        {
            "botId": "b",
            "sessionId": "s-fp",
            "turnId": "t-fp",
            "toolUseId": "c-1",
        }
    )
    (activity,) = build_first_party_activities(
        tool_name=tool,
        arguments={"q": 1},
        context=context,
        result=_FPToolResult(status=status, output={"r": 1}),
        enabled_refs=FIRST_PARTY_ACTIVITY_REFS,
    )
    return activity


def test_record_first_party_activity_appends_and_persists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    assert (
        collector.record_first_party_activity(
            session_id="s-fp",
            turn_id="t-fp",
            activity=_fp_activity(),
        )
        is True
    )
    records = collector.collect_for_turn("t-fp")
    assert any(getattr(r, "type", "") == "custom:FirstPartyToolCall" for r in records)
    jsonl = tmp_path / "s-fp.jsonl"
    assert jsonl.exists()
    line = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[-1])
    assert line["record"]["type"] == "custom:FirstPartyToolCall"
    assert line["record"]["fields"]["v"] == 1


def test_skill_load_dedup_per_turn_and_digest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    skill = FirstPartyActivity.model_validate(
        {
            "recordId": "evd_x",
            "evidenceType": "SkillLoad",
            "publicRef": "evidence:skillLoad@1",
            "name": "SkillLoader",
            "status": "ok",
            "actor": "main",
            "detail": {"skillPath": "bundled/a", "skillSource": "bundled", "bodyDigest": "d1"},
        }
    )
    assert (
        collector.record_first_party_activity(session_id="s", turn_id="t", activity=skill) is True
    )
    assert (
        collector.record_first_party_activity(session_id="s", turn_id="t", activity=skill) is False
    )
    changed = skill.model_copy(update={"detail": {**dict(skill.detail), "bodyDigest": "d2"}})
    assert (
        collector.record_first_party_activity(session_id="s", turn_id="t", activity=changed) is True
    )
    # different turn => records again
    assert (
        collector.record_first_party_activity(session_id="s", turn_id="t2", activity=skill) is True
    )


def test_record_first_party_activity_fail_open(monkeypatch) -> None:
    collector = LocalToolEvidenceCollector()
    assert (
        collector.record_first_party_activity(
            session_id="",
            turn_id="",
            activity=object(),
        )
        is False
    )


def test_first_party_activity_ledger_gated_by_lifecycle_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    # Explicitly disable the lifecycle flag (default is ON in full runtime profile).
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED", "0")
    collector.record_first_party_activity(session_id="s1", turn_id="t1", activity=_fp_activity())
    assert collector.evidence_ledgers_for_session("s1") == ()
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED", "1")
    collector.record_first_party_activity(session_id="s2", turn_id="t2", activity=_fp_activity())
    ledgers = collector.evidence_ledgers_for_session("s2")
    assert len(ledgers) == 1


def test_first_party_state_pruned_after_30_turns(tmp_path, monkeypatch) -> None:
    """Prune bounds first-party records to ≤25 turns; dedup set stays in sync.

    When all records in a turn key are first-party origin (no tool receipts),
    the whole key is evicted from ``_records`` after pruning (the list becomes
    empty → key is popped).  The assertion on ``surviving_keys`` therefore still
    checks first-party-record bounding — identical semantics for a first-party-
    only session.
    """
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    session = "s-prune"
    # Record 30 distinct turns for one session using a SkillLoad activity so the
    # dedup set is also populated.
    for i in range(30):
        turn = f"t-prune-{i:02d}"
        skill = FirstPartyActivity.model_validate(
            {
                "recordId": f"evd_{i:04d}",
                "evidenceType": "SkillLoad",
                "publicRef": "evidence:skillLoad@1",
                "name": "SkillLoader",
                "status": "ok",
                "actor": "main",
                "detail": {
                    "skillPath": f"bundled/skill-{i}",
                    "skillSource": "bundled",
                    "bodyDigest": f"d{i}",
                },
            }
        )
        result = collector.record_first_party_activity(
            session_id=session, turn_id=turn, activity=skill
        )
        assert result is True, f"turn {i} failed"

    # After 30 inserts, ≤25 turn keys must survive for this session in _records.
    # (All evicted keys had first-party records only → empty after filter → popped.)
    surviving_keys = [key for key in collector._records if key[0] == session]
    assert len(surviving_keys) <= 25, f"expected ≤25 surviving turn keys, got {len(surviving_keys)}"

    # The dedup set must only reference (session, turn) pairs that still exist
    surviving_turns = {key[1] for key in surviving_keys}
    for fp_key in collector._first_party_skill_seen:
        if fp_key[0] == session:
            assert fp_key[1] in surviving_turns, f"dedup key references pruned turn {fp_key[1]}"


def test_mixed_origin_tool_receipts_survive_first_party_flood(tmp_path, monkeypatch) -> None:
    """Regression: first-party prune must NEVER evict non-first-party records.

    Setup:
      1. Record one ordinary tool receipt via ``record_tool_result`` for
         (session="s", turn="turn-00") — this is a non-first-party record.
      2. Record first-party activities across 30 distinct turns (turn-01..turn-30)
         in the same session to trigger pruning.

    Assertions:
      - ``collect_for_turn("turn-00")`` still returns the original tool receipt
        (non-first-party record must survive the first-party flood).
      - The count of turns retaining first-party records is ≤ 25 (cap still
        enforced for first-party origin).
    """
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    session = "s"

    # Step 1: record one tool receipt for turn-00 (non-first-party origin).
    collector.record_tool_result(
        session_id=session,
        turn_id="turn-00",
        tool_call_id="call-bash-00",
        tool_name="Bash",
        result=ToolResult(status="ok", output="hello"),
    )
    records_before = collector.collect_for_turn("turn-00")
    assert len(records_before) >= 1, "turn-00 must have at least one record before the flood"

    # Step 2: flood 30 first-party turns (turn-01 … turn-30) for the same session.
    for i in range(1, 31):
        turn = f"turn-{i:02d}"
        skill = FirstPartyActivity.model_validate(
            {
                "recordId": f"evd_flood_{i:04d}",
                "evidenceType": "SkillLoad",
                "publicRef": "evidence:skillLoad@1",
                "name": "SkillLoader",
                "status": "ok",
                "actor": "main",
                "detail": {
                    "skillPath": f"bundled/flood-{i}",
                    "skillSource": "bundled",
                    "bodyDigest": f"fd{i}",
                },
            }
        )
        ok = collector.record_first_party_activity(session_id=session, turn_id=turn, activity=skill)
        assert ok is True, f"flood turn {i} failed to record"

    # Assertion A: the original tool receipt at turn-00 must still be present.
    records_after = collector.collect_for_turn("turn-00")
    assert len(records_after) >= 1, (
        "tool receipt at turn-00 was evicted by first-party prune — regression"
    )
    # Confirm it is not a first-party record (it came from record_tool_result).
    assert all(
        not str(getattr(r, "type", "")).startswith("custom:FirstParty") for r in records_after
    ), "turn-00 must contain only non-first-party records"

    # Assertion B: first-party turn count across the session is ≤ 25.
    fp_turn_keys = [key for key in collector._first_party_turns if key[0] == session]
    assert len(fp_turn_keys) <= 25, f"first-party turns not capped: {len(fp_turn_keys)} > 25"


def test_append_evidence_record_for_turn_is_collectible() -> None:
    collector = LocalToolEvidenceCollector()
    sentinel = object()
    collector.append_evidence_record_for_turn(
        session_id="sess-1", turn_id="turn-append", record=sentinel
    )
    out = collector.collect_for_turn("turn-append")
    assert sentinel in out


def test_append_evidence_record_for_turn_collected_by_turn_id_only() -> None:
    # collect_for_turn filters by turn_id alone (session ignored), so a record
    # appended under any session lands for that turn id.
    collector = LocalToolEvidenceCollector()
    rec = object()
    collector.append_evidence_record_for_turn(
        session_id="cli-session", turn_id="inv-123", record=rec
    )
    assert rec in collector.collect_for_turn("inv-123")
    assert collector.collect_for_turn("other-turn") == ()
