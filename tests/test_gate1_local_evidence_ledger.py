from __future__ import annotations

import json
from pathlib import Path

from openmagi_core_agent.evidence.extraction import (
    evidence_from_projected_event,
    evidence_from_tool_result,
)
from openmagi_core_agent.evidence.ledger import EvidenceLedger
from openmagi_core_agent.tools.result import ToolResult


FIXTURES = Path(__file__).parent / "fixtures" / "gate1"


def _fixture_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _base_ledger() -> EvidenceLedger:
    return EvidenceLedger.model_validate(
        {
            "ledgerId": "gate1-ledger-audit",
            "sessionId": "gate1-session",
            "turnId": "gate1-turn-evidence",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
            "metadata": {"mode": "audit-only"},
        }
    )


def test_audit_only_evidence_ledger_matches_gate1_golden_and_replays() -> None:
    projected_record = evidence_from_projected_event(
        {
            "type": "tool_end",
            "id": "tool-test-synthetic-1",
            "eventId": "evt-evidence-tool-end",
            "name": "SyntheticTest",
            "status": "ok",
            "output_preview": "pytest passed with token=synthetic-evidence-token",
            "ts": 1_779_000_040,
            "metadata": {
                "evidence": {
                    "type": "TestRun",
                    "fields": {
                        "command": "python -m pytest tests/test_gate1_local_event_projection.py",
                        "exitCode": 0,
                    },
                    "source": {"kind": "tool_trace"},
                    "metadata": {"lastCodeMutation": 1_779_000_000},
                }
            },
        }
    )
    tool_result_record = evidence_from_tool_result(
        ToolResult(
            status="ok",
            transcriptOutput="git diff inspected locally",
            metadata={
                "evidence": {
                    "type": "GitDiff",
                    "fields": {"changedFiles": 2, "productionImpact": False},
                    "source": {"kind": "tool_trace"},
                    "metadata": {"contractStart": 1_779_000_000},
                }
            },
        ),
        tool_call_id="tool-diff-synthetic-1",
        tool_name="SyntheticDiff",
    )

    assert projected_record is not None
    assert tool_result_record is not None
    ledger = (
        _base_ledger()
        .append_evidence_record(projected_record, metadata={"source": "projected-event"})
        .append_evidence_record(tool_result_record, metadata={"source": "tool-result"})
    )
    dumped = ledger.model_dump(by_alias=True, mode="json")

    assert (
        EvidenceLedger.model_validate(dumped).model_dump(by_alias=True, mode="json")
        == dumped
    )
    assert _base_ledger().entries == ()
    assert [entry["kind"] for entry in dumped["entries"]] == [
        "evidence_record",
        "evidence_record",
    ]
    assert "verifier_verdict" not in json.dumps(dumped, sort_keys=True)
    assert "final_action" not in json.dumps(dumped, sort_keys=True)
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["routeAttached"] is False
    for entry in dumped["entries"]:
        assert entry["trafficAttached"] is False
        assert entry["executionAttached"] is False
        assert entry["routeAttached"] is False

    assert dumped == _fixture_json("audit_only_evidence_ledger.json")
