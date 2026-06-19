"""Tests for the deny-on-present dashboard custom-check verifier-bus gate."""
from __future__ import annotations

from magi_agent.evidence.types import EvidenceRecord
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus


def _dashboard_record(*, status: str, rule_id: str = "no-ssn") -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "custom:DashboardCheck",
            "status": status,
            "observedAt": 1,
            "source": {"kind": "tool_trace", "toolName": "web_fetch"},
            "fields": {
                "evidenceRef": f"evidence:dashboard:{rule_id}",
                "ruleId": rule_id,
                "action": "block",
            },
        }
    )


def _run(records: tuple[object, ...], *, gate: bool) -> dict[str, object]:
    return execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=records,
        dashboard_gate_enabled=gate,
    )


def test_failed_record_gate_enabled_blocks() -> None:
    out = _run((_dashboard_record(status="failed"),), gate=True)
    assert out["decision"] == "block"
    assert out["failedDashboardChecks"] == 1
    ids = [r["verifierId"] for r in out["results"]]
    assert "dashboard-custom-check" in ids
    block_result = next(
        r for r in out["results"] if r["verifierId"] == "dashboard-custom-check"
    )
    assert "no-ssn" in block_result["publicSummary"]


def test_failed_record_gate_disabled_passes() -> None:
    out = _run((_dashboard_record(status="failed"),), gate=False)
    assert out["decision"] == "pass"
    assert out["failedDashboardChecks"] == 0


def test_ok_record_does_not_block() -> None:
    out = _run((_dashboard_record(status="ok"),), gate=True)
    assert out["decision"] == "pass"
    assert out["failedDashboardChecks"] == 0


def test_absence_passes() -> None:
    out = _run((), gate=True)
    assert out["decision"] == "pass"
    assert out["failedDashboardChecks"] == 0


def test_malformed_record_no_raise() -> None:
    out = _run(("not-a-record", {"type": "custom:DashboardCheck"}), gate=True)
    assert out["decision"] == "pass"
    assert out["failedDashboardChecks"] == 0
