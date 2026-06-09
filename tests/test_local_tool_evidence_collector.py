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
