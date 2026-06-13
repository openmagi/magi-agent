from __future__ import annotations

import json

from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus
from magi_agent.tools.result import ToolResult


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
