from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.runtime.checkpointing import (
    ExecutionCheckpoint,
    ForkedRunLineage,
    ReplayModeDecision,
    ResumeVerificationReport,
    ResumeVerificationRequest,
    verify_resume_request,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def _checkpoint() -> ExecutionCheckpoint:
    return ExecutionCheckpoint(
        runId="run-001",
        checkpointId="checkpoint-001",
        stepId="step-003",
        workflowVersion="1.0.0",
        stateDigest="sha256:" + "1" * 64,
        ledgerHeadDigest="sha256:" + "2" * 64,
        effectivePolicySnapshotDigest="sha256:" + "3" * 64,
        contextProjectionDigest="sha256:" + "4" * 64,
        pendingApprovalRefs=("approval:read-file",),
        resumable=True,
        createdAt=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
    )


def test_resume_verifies_ledger_policy_context_and_authority_scope() -> None:
    checkpoint = _checkpoint()
    report = verify_resume_request(
        checkpoint,
        ledgerHeadDigest="sha256:" + "2" * 64,
        effectivePolicySnapshotDigest="sha256:" + "3" * 64,
        effectivePolicySnapshotAvailable=True,
        authorityScopeWouldExpand=False,
        pendingApprovalExpired=False,
        requiredEvidenceMissing=False,
    )

    assert report.ok is True


def test_resume_fails_closed_on_mismatches_or_expired_approval() -> None:
    checkpoint = _checkpoint()
    report = verify_resume_request(
        checkpoint,
        ledgerHeadDigest="sha256:" + "9" * 64,
        effectivePolicySnapshotDigest="sha256:" + "8" * 64,
        effectivePolicySnapshotAvailable=False,
        authorityScopeWouldExpand=True,
        pendingApprovalExpired=True,
        requiredEvidenceMissing=True,
    )

    assert report.ok is False
    assert report.reason_codes == (
        "ledger_head_digest_mismatch",
        "effective_policy_snapshot_unavailable",
        "effective_policy_snapshot_digest_mismatch",
        "authority_scope_would_expand",
        "pending_approval_expired",
        "required_evidence_missing",
    )


def test_resume_request_contract_rejects_non_strict_booleans_and_bad_policy_digest() -> None:
    with pytest.raises(ValidationError, match="effectivePolicySnapshotAvailable"):
        ResumeVerificationRequest.model_validate(
            {
                "ledgerHeadDigest": "sha256:" + "2" * 64,
                "effectivePolicySnapshotDigest": "sha256:" + "3" * 64,
                "effectivePolicySnapshotAvailable": "true",
                "authorityScopeWouldExpand": False,
                "pendingApprovalExpired": False,
                "requiredEvidenceMissing": False,
            }
        )
    with pytest.raises(ValidationError, match="sha256"):
        ResumeVerificationRequest.model_validate(
            {
                "ledgerHeadDigest": "sha256:" + "2" * 64,
                "effectivePolicySnapshotDigest": "raw-policy",
                "effectivePolicySnapshotAvailable": True,
                "authorityScopeWouldExpand": False,
                "pendingApprovalExpired": False,
                "requiredEvidenceMissing": False,
            }
        )


def test_replay_mode_is_read_only_and_blocks_side_effects() -> None:
    decision = ReplayModeDecision(mode="replay", allowSideEffects=False, appendReplayObservation=True)

    assert decision.mode == "replay"
    assert decision.allow_side_effects is False
    assert decision.append_replay_observation is True


def test_fork_records_parent_lineage() -> None:
    fork = ForkedRunLineage(
        parentRunId="run-001",
        parentCheckpointId="checkpoint-001",
        parentLedgerHeadDigest="sha256:" + "2" * 64,
        forkReason="time_travel_debug_continuation",
        newRunId="run-002",
        newEffectivePolicySnapshotDigest="sha256:" + "5" * 64,
    )

    assert fork.parent_run_id == "run-001"
    assert fork.new_run_id == "run-002"


def test_checkpoint_rejects_protected_identifiers_and_bad_digests() -> None:
    with pytest.raises(ValidationError, match="protected"):
        ExecutionCheckpoint(
            runId="run-" + "sess" + "ion-" + "to" + "ken",
            checkpointId="checkpoint-001",
            stepId="step-003",
            workflowVersion="1.0.0",
            stateDigest="sha256:" + "1" * 64,
            ledgerHeadDigest="sha256:" + "2" * 64,
            effectivePolicySnapshotDigest="sha256:" + "3" * 64,
            contextProjectionDigest="sha256:" + "4" * 64,
            pendingApprovalRefs=("approval:read-file",),
            resumable=True,
            createdAt=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="sha256"):
        ExecutionCheckpoint(
            runId="run-001",
            checkpointId="checkpoint-001",
            stepId="step-003",
            workflowVersion="1.0.0",
            stateDigest="raw-state",
            ledgerHeadDigest="sha256:" + "2" * 64,
            effectivePolicySnapshotDigest="sha256:" + "3" * 64,
            contextProjectionDigest="sha256:" + "4" * 64,
            pendingApprovalRefs=("approval:read-file",),
            resumable=True,
            createdAt=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        )


def test_checkpoint_report_and_replay_reject_coerced_boolean_fields() -> None:
    checkpoint_payload = _checkpoint().model_dump(by_alias=True, mode="json")
    checkpoint_payload["resumable"] = "false"
    with pytest.raises(ValidationError, match="resumable"):
        ExecutionCheckpoint.model_validate(checkpoint_payload)

    with pytest.raises(ValidationError, match="ok"):
        ResumeVerificationReport(ok="false")

    with pytest.raises(ValidationError, match="allowSideEffects"):
        ReplayModeDecision(mode="replay", allowSideEffects=0, appendReplayObservation=True)
    with pytest.raises(ValidationError, match="appendReplayObservation"):
        ReplayModeDecision(mode="replay", allowSideEffects=False, appendReplayObservation=1)


def test_replay_and_lineage_model_copy_update_are_disabled() -> None:
    replay = ReplayModeDecision(mode="replay", allowSideEffects=False, appendReplayObservation=True)
    fork = ForkedRunLineage(
        parentRunId="run-001",
        parentCheckpointId="checkpoint-001",
        parentLedgerHeadDigest="sha256:" + "2" * 64,
        forkReason="time_travel_debug_continuation",
        newRunId="run-002",
        newEffectivePolicySnapshotDigest="sha256:" + "5" * 64,
    )

    with pytest.raises(ValueError, match="model_copy update"):
        replay.model_copy(update={"allow_side_effects": True})
    with pytest.raises(ValueError, match="model_copy update"):
        fork.model_copy(update={"new_run_id": "run-" + "sess" + "ion-" + "to" + "ken"})


def test_checkpoint_fixture_validates_without_raw_payloads() -> None:
    payload = json.loads((FIXTURE_DIR / "checkpoint_resume.json").read_text())
    checkpoint = ExecutionCheckpoint.model_validate(payload["checkpoint"])
    report = verify_resume_request(checkpoint, **payload["resumeRequest"])

    assert report.ok is True
    encoded = json.dumps(_string_values(payload), sort_keys=True).lower()
    forbidden_fragments = (
        "pro" + "mpt",
        "author" + "ization",
        "coo" + "kie",
        "to" + "ken",
        "sess" + "ion",
        "priv" + "ate",
    )
    assert all(fragment not in encoded for fragment in forbidden_fragments)


def test_lineage_and_report_reject_path_like_or_raw_reason_values() -> None:
    with pytest.raises(ValidationError, match="path-like"):
        ForkedRunLineage(
            parentRunId="/Users/example/.ssh/id_rsa",
            parentCheckpointId="checkpoint-001",
            parentLedgerHeadDigest="sha256:" + "2" * 64,
            forkReason="time_travel_debug_continuation",
            newRunId="run-002",
            newEffectivePolicySnapshotDigest="sha256:" + "5" * 64,
        )
    with pytest.raises(ValidationError, match="protected"):
        ForkedRunLineage(
            parentRunId="run-001",
            parentCheckpointId="checkpoint-001",
            parentLedgerHeadDigest="sha256:" + "2" * 64,
            forkReason="raw child log /Users/example/.env",
            newRunId="run-002",
            newEffectivePolicySnapshotDigest="sha256:" + "5" * 64,
        )
    with pytest.raises(ValidationError, match="reasonCodes"):
        ResumeVerificationReport(ok=False, reasonCodes=("raw tool log /Users/example/.env",))


def test_lineage_rejects_raw_ref_style_and_camelcase_raw_markers() -> None:
    for fork_reason in (
        "raw:diagnostic",
        "rawRef:diagnostic",
        "rawToolLogDiagnostic",
        "rawChildTranscriptDiagnostic",
        "toolLogDiagnostic",
        "childRawToolLog",
    ):
        with pytest.raises(ValidationError, match="protected"):
            ForkedRunLineage(
                parentRunId="run-001",
                parentCheckpointId="checkpoint-001",
                parentLedgerHeadDigest="sha256:" + "2" * 64,
                forkReason=fork_reason,
                newRunId="run-002",
                newEffectivePolicySnapshotDigest="sha256:" + "5" * 64,
            )


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []
