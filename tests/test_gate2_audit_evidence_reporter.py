from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.evidence.types import (
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceRecord,
    EvidenceSource,
)
from magi_agent.shadow.audit_reporter import (
    Gate2AuditEvidenceOutputFlags,
    Gate2AuditVerifierEntryReport,
    build_gate2_audit_evidence_report,
)


ATTACHMENT_FLAGS = {
    "trafficAttached",
    "executionAttached",
    "routeAttached",
    "canaryAttached",
    "productionAttached",
    "userVisible",
    "productionTranscriptAppend",
    "networkSse",
    "blockModeEnabledForLiveTraffic",
}


def _record(
    *,
    preview: str = "pytest passed",
    metadata: Mapping[str, object] | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=1_779_999_999,
        source=EvidenceSource(kind="tool_trace", toolName="Bash", toolCallId="call-1"),
        fields={"command": "pytest", "exitCode": 0, "api_token": "sk-field-secret"},
        preview=preview,
        metadata={"publicSafeFields": ("command", "exitCode"), **dict(metadata or {})},
    )


def _ledger() -> EvidenceLedger:
    return EvidenceLedger(
        ledgerId="ledger-session-1-turn-1",
        sessionId="session-1",
        turnId="turn-1",
        runOn="main",
        agentRole="coding",
        spawnDepth=0,
        sourceKind="tool_trace",
        producerSurface="tool_host",
    )


def _verdict(
    record: EvidenceRecord,
    *,
    state: str = "pass",
    enforcement: str = "audit",
) -> EvidenceContractVerdict:
    return EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": state == "pass",
            "state": state,
            "enforcement": enforcement,
            "missingRequirements": [] if state == "pass" else [{"type": "TestRun"}],
            "matchedEvidence": [record],
            "failures": []
            if state == "pass"
            else [
                EvidenceContractFailure(
                    code="EVIDENCE_CONTRACT_MISSING",
                    contractId="coding-basic",
                    requirementType="TestRun",
                    message="missing Authorization: Bearer verdict-secret",
                    metadata={
                        "field": "api_token",
                        "actual": "ghp_verdictsecret012345678901234",
                    },
                )
            ],
        }
    )


def _report_dump(
    *,
    record: EvidenceRecord | None = None,
    verdict: EvidenceContractVerdict | None = None,
    diagnostic_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    record = record or _record()
    ledger = _ledger().append_evidence_record(record)
    verdict = verdict or _verdict(record)
    ledger = ledger.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(ledger.entries[0].evidence_ref,),
        verdict_id="verdict-1",
        metadata={"block_ready": verdict.state == "block_ready"},
    )
    report = build_gate2_audit_evidence_report(
        ledger,
        verifier_verdicts={"verdict-1": verdict},
        diagnostic_metadata=diagnostic_metadata or {},
    )
    return report.model_dump(by_alias=True, mode="json")


def test_builds_local_diagnostic_audit_report_with_all_attachment_flags_false() -> None:
    dumped = _report_dump()

    assert dumped["posture"] == "diagnostic_non_authoritative"
    assert dumped["scope"] == "local_fixture_only"
    assert dumped["authority"] == "audit_only"
    assert dumped["ledgerId"] == "ledger-session-1-turn-1"
    assert dumped["evidenceRecords"][0]["type"] == "TestRun"
    assert dumped["verifierVerdicts"][0]["contractId"] == "coding-basic"
    assert dumped["outputFlags"] == {flag: False for flag in ATTACHMENT_FLAGS}


def test_public_report_redacts_record_verdict_and_reporter_diagnostic_metadata() -> None:
    record = _record(
        preview="Authorization: Bearer preview-secret "
        "ghp_previewsecret012345678901234 "
        "sk-preview-secret123456789 "
        "TOKEN=env-secret "
        + ("x" * 500),
        metadata={
            "authorization": "Bearer metadata-secret",
            "github_token": "ghp_metadatasecret012345678901234",
        },
    )
    verdict = _verdict(record, state="failed", enforcement="audit")

    dumped = _report_dump(
        record=record,
        verdict=verdict,
        diagnostic_metadata={
            "authorization": "Bearer diagnostic-secret",
            "note": "client_secret=diagnostic-secret " + ("y" * 500),
        },
    )

    public_text = json.dumps(dumped, sort_keys=True)
    for leaked in (
        "preview-secret",
        "previewsecret",
        "sk-preview-secret",
        "env-secret",
        "metadata-secret",
        "metadatasecret",
        "verdict-secret",
        "verdictsecret",
        "diagnostic-secret",
    ):
        assert leaked not in public_text
    assert len(dumped["evidenceRecords"][0]["preview"]) <= 400
    assert dumped["evidenceRecords"][0]["metadata"]["authorization"] == "[redacted]"
    assert dumped["verifierVerdicts"][0]["failures"][0]["metadata"]["actual"] == "[redacted]"
    assert dumped["diagnosticMetadata"]["authorization"] == "[redacted]"


def test_block_final_answer_and_block_ready_are_audit_readiness_metadata_only() -> None:
    record = _record()
    verdict = _verdict(record, state="block_ready", enforcement="block_final_answer")

    dumped = _report_dump(record=record, verdict=verdict)

    assert dumped["outputFlags"]["blockModeEnabledForLiveTraffic"] is False
    assert dumped["blockReadiness"]["blockReady"] is True
    assert dumped["blockReadiness"]["enforcements"] == ["block_final_answer"]
    assert dumped["verifierVerdicts"][0]["state"] == "block_ready"
    assert dumped["verifierVerdicts"][0]["enforcement"] == "block_final_answer"


def test_iterable_verifier_verdicts_are_rejected_instead_of_silently_ignored() -> None:
    record = _record()
    ledger = _ledger().append_evidence_record(record)
    verdict = _verdict(record, state="block_ready", enforcement="block_final_answer")
    ledger = ledger.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(ledger.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    with pytest.raises(ValueError, match="verifier_verdicts must be a mapping"):
        build_gate2_audit_evidence_report(
            ledger,
            verifier_verdicts=(verdict,),
        )


def test_diagnostic_metadata_rejects_non_string_keys_without_attribute_error() -> None:
    with pytest.raises(ValidationError, match="metadata mapping keys must be strings"):
        build_gate2_audit_evidence_report(
            _ledger(),
            diagnostic_metadata={1: "numeric-key"},  # type: ignore[dict-item]
        )


def test_verifier_entry_metadata_rejects_non_string_keys_without_attribute_error() -> None:
    with pytest.raises(ValidationError, match="metadata mapping keys must be strings"):
        Gate2AuditVerifierEntryReport(
            evidenceRef="ledger-session-1-turn-1:0002:verifier_verdict",
            metadata={1: "numeric-key"},  # type: ignore[dict-item]
        )


@pytest.mark.parametrize("flag", sorted(ATTACHMENT_FLAGS))
def test_output_flags_reject_true_values_and_model_copy_cannot_enable_flags(flag: str) -> None:
    with pytest.raises(ValidationError):
        Gate2AuditEvidenceOutputFlags.model_validate({flag: True})

    flags = Gate2AuditEvidenceOutputFlags()
    with pytest.raises(ValidationError):
        flags.model_copy(update={flag: True})


def test_raw_set_output_flags_cannot_serialize_forbidden_true_state() -> None:
    flags = Gate2AuditEvidenceOutputFlags()

    object.__setattr__(flags, "traffic_attached", True)

    dumped = flags.model_dump(by_alias=True, mode="json")
    assert dumped["trafficAttached"] is False
    assert dumped == {flag: False for flag in ATTACHMENT_FLAGS}


def test_raw_set_report_output_flags_cannot_serialize_forbidden_true_state() -> None:
    report = build_gate2_audit_evidence_report(_ledger())
    forged_flags = Gate2AuditEvidenceOutputFlags()

    object.__setattr__(forged_flags, "traffic_attached", True)
    object.__setattr__(report, "output_flags", forged_flags)

    dumped = report.model_dump(by_alias=True, mode="json")
    assert dumped["outputFlags"]["trafficAttached"] is False
    assert dumped["outputFlags"] == {flag: False for flag in ATTACHMENT_FLAGS}


def test_report_rejects_extra_fields_on_validation_and_model_copy() -> None:
    report = build_gate2_audit_evidence_report(_ledger())

    with pytest.raises(ValidationError):
        type(report).model_validate({**report.model_dump(by_alias=True), "route": "/api/live"})
    with pytest.raises(ValidationError):
        report.model_copy(update={"route": "/api/live"})


def test_importing_shadow_audit_reporter_does_not_import_live_runtime_surfaces() -> None:
    code = """
import sys
import magi_agent.shadow.audit_reporter
for forbidden in (
    'routes',
    'api',
    'dashboard',
    'telegram',
    'k8s',
    'provisioning',
    'deploy',
    'runtime_selector',
    'typescript',
):
    matches = [name for name in sys.modules if forbidden in name.lower()]
    if matches:
        raise SystemExit(f'{forbidden}: {matches[:5]}')
"""
    subprocess.run([sys.executable, "-c", code], check=True)
