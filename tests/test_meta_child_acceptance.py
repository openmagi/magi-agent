from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from runtime_issuance_support import issue_test_runtime_authority
from magi_agent.evidence.subagent import (
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    ChildAcceptanceVerdict,
    RuntimeIssuedChildResult,
    accept_child_result,
    issue_runtime_child_result,
)


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-meta-child-acceptance",
        scopes=scopes,
    )


def _parent_boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "parent-exec-1",
        "agentId": "parent-agent",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-parent-1",
        "agentRole": "coding",
        "runOn": "main",
        "spawnDepth": 0,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _child_boundary(**overrides: object) -> ExecutionBoundaryIdentity:
    payload = {
        "executionId": "child-exec-1",
        "agentId": "child-agent",
        "parentExecutionId": "parent-exec-1",
        "taskId": "task-1",
        "turnId": "turn-1",
        "policyScope": "coding",
        "policySnapshotId": "policy-parent-1",
        "agentRole": "coding",
        "runOn": "child",
        "spawnDepth": 1,
    }
    payload.update(overrides)
    return ExecutionBoundaryIdentity.model_validate(payload)


def _ledger_ref(
    child: ExecutionBoundaryIdentity | None = None,
    **overrides: object,
) -> EvidenceBoundaryLedgerRef:
    boundary = child or _child_boundary()
    payload = {
        "ledgerId": f"ledger:{boundary.execution_id}",
        "executionId": boundary.execution_id,
        "agentId": boundary.agent_id,
        "parentExecutionId": boundary.parent_execution_id,
        "taskId": boundary.task_id,
        "policySnapshotId": boundary.policy_snapshot_id,
        "childLedgerRefs": ("ledger:audit-child-proof",),
    }
    payload.update(overrides)
    return EvidenceBoundaryLedgerRef.model_validate(payload)


def _envelope_payload(**overrides: object) -> dict[str, object]:
    parent = _parent_boundary()
    child = _child_boundary()
    payload: dict[str, object] = {
        "issuer": "openmagi_runtime_boundary",
        "mode": "return",
        "status": "accepted",
        "parentBoundary": parent,
        "childBoundary": child,
        "task": {
            "taskId": "task-1",
            "persona": "coding",
            "role": "coding",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        },
        "policySnapshot": {
            "parentPolicySnapshotId": "policy-parent-1",
            "childPolicySnapshotId": "policy-parent-1",
            "taskLocalPolicyCompatibilityRefs": (),
            "allowedToolNames": ("FileRead",),
            "permissionRefs": ("permission:read-only",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "ledgerRef": _ledger_ref(child),
        "delegatedEvidenceRequirements": (
            DelegatedEvidenceRequirement(type="TestRun", delegation="delegated_required"),
        ),
        "workspaceIsolation": {
            "workspacePolicy": "trusted",
            "isolationRef": "workspace-isolation:task-1",
            "parentWorkspaceRef": "workspace:parent-redacted",
            "childWorkspaceRef": "workspace:child-redacted",
            "descriptiveOnly": True,
            "adoptionAttached": False,
            "workspaceMutated": False,
            "privateNotes": ("private /workspace/path and Bearer unsafe-token",),
        },
        "completionContract": {
            "requiredEvidence": "tool_call",
            "requiredFiles": (),
            "requireNonEmptyResult": True,
            "summaryIsEvidence": False,
            "acceptedEvidenceMetadataOnly": True,
        },
        "auditEventRefs": ("audit:child-spawn-planned", "audit:child-envelope-issued"),
        "adkPrimitiveOwnership": {
            "agentOwner": "adk_future_agent",
            "runnerOwner": "adk_future_runner",
            "eventOwner": "adk_event_bridge",
            "toolOwner": "adk_function_tool_future",
            "callbackOwner": "adk_callbacks_future",
            "runnerAttached": False,
            "childExecutionAttached": False,
            "allowedToolNames": ("FileRead",),
            "callbackHookRefs": ("callback:before-tool-policy",),
        },
        "authorityFlags": ChildRuntimeEnvelopeAuthorityFlags(),
        "rawTranscriptRef": "transcript:private-child-turn",
        "privateMetadata": {
            "rawTranscriptPreview": "raw child transcript with sk-child-secret",
            "toolArgs": {"authorization": "Bearer unsafe-token"},
            "workspacePath": "/workspace/private",
        },
    }
    payload.update(overrides)
    return payload


def _policy(**overrides: object) -> ChildAcceptancePolicy:
    payload = {
        "parentExecutionId": "parent-exec-1",
        "childExecutionId": "child-exec-1",
        "taskId": "task-1",
        "parentPolicySnapshotId": "policy-parent-1",
        "childPolicySnapshotId": "policy-parent-1",
        "runtimeReceiptRef": "receipt:child-envelope-1",
        "requiredEvidenceRefs": (
            "ledger:child-exec-1",
            "receipt:child-envelope-1",
            "audit:child-envelope-issued",
        ),
        "maxRetryBudget": 1,
        "currentAttempt": 0,
    }
    payload.update(overrides)
    return ChildAcceptancePolicy.model_validate(payload)


def _issued_child_result(
    payload: dict[str, object] | None = None,
    *,
    receipt_ref: str = "receipt:child-envelope-1",
) -> RuntimeIssuedChildResult:
    envelope = ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **(payload or _envelope_payload()),
    )
    return issue_runtime_child_result(envelope, receipt_ref=receipt_ref)


def test_raw_child_summary_cannot_satisfy_completion() -> None:
    verdict = accept_child_result(
        {"summary": "I ran tests and everything passed.", "status": "accepted"},
        _policy(),
    )

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("invalid_child_envelope",)
    assert verdict.accepted_evidence_refs == ()
    assert verdict.retryable is False


def test_raw_envelope_shaped_mapping_cannot_satisfy_completion() -> None:
    verdict = accept_child_result(_envelope_payload(), _policy())

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("invalid_child_envelope",)
    assert verdict.accepted_evidence_refs == ()


def test_forged_envelope_issuer_fails_acceptance() -> None:
    verdict = accept_child_result(_envelope_payload(issuer="child_authored_json"), _policy())

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("invalid_child_envelope",)
    assert verdict.accepted_evidence_refs == ()


def test_constructed_child_envelope_instance_is_revalidated_before_acceptance() -> None:
    forged = ChildRuntimeEnvelope.model_validate(_envelope_payload())

    verdict = accept_child_result(forged, _policy())

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("invalid_child_envelope",)
    assert verdict.accepted_evidence_refs == ()

    with pytest.raises(ValueError):
        issue_runtime_child_result(forged, receipt_ref="receipt:child-envelope-1")

    with pytest.raises(TypeError):
        ChildRuntimeEnvelope.model_construct(**_envelope_payload())


def test_child_envelope_with_mismatched_parent_execution_id_fails() -> None:
    verdict = accept_child_result(
        _envelope_payload(childBoundary=_child_boundary(parentExecutionId="parent-exec-2")),
        _policy(),
    )

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("invalid_child_envelope",)
    assert verdict.accepted_evidence_refs == ()


def test_runtime_receipt_mismatch_fails_acceptance() -> None:
    verdict = accept_child_result(
        _issued_child_result(receipt_ref="receipt:other-child-envelope"),
        _policy(),
    )

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("runtime_receipt_mismatch",)
    assert verdict.accepted_evidence_refs == ()


def test_mutated_runtime_child_result_cannot_be_accepted() -> None:
    issued = _issued_child_result(receipt_ref="receipt:other-child-envelope")
    issued.__dict__["receipt_ref"] = "receipt:child-envelope-1"

    verdict = accept_child_result(issued, _policy())

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("invalid_child_envelope",)
    assert verdict.accepted_evidence_refs == ()


def test_child_execution_mismatch_fails_acceptance() -> None:
    child = _child_boundary(executionId="child-exec-2")
    verdict = accept_child_result(
        _issued_child_result(_envelope_payload(childBoundary=child, ledgerRef=_ledger_ref(child))),
        _policy(),
    )

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("child_execution_mismatch",)
    assert verdict.accepted_evidence_refs == ()


def test_policy_snapshot_mismatch_fails_acceptance() -> None:
    child = _child_boundary(policySnapshotId="policy-child-2")
    verdict = accept_child_result(
        _issued_child_result(
            _envelope_payload(
                childBoundary=child,
                ledgerRef=_ledger_ref(child),
                policySnapshot={
                    "parentPolicySnapshotId": "policy-parent-1",
                    "childPolicySnapshotId": "policy-child-2",
                    "taskLocalPolicyCompatibilityRefs": (
                        {
                            "parentPolicySnapshotId": "policy-parent-1",
                            "childPolicySnapshotId": "policy-child-2",
                            "childExecutionId": "child-exec-1",
                            "taskId": "task-1",
                            "reason": "task_local_contracts",
                        },
                    ),
                    "allowedToolNames": ("FileRead",),
                    "permissionRefs": ("permission:read-only",),
                    "callbackHookRefs": ("callback:before-tool-policy",),
                },
            )
        ),
        _policy(),
    )

    assert verdict.status == "rejected"
    assert verdict.reason_codes == ("policy_snapshot_mismatch",)
    assert verdict.accepted_evidence_refs == ()


def test_policy_requires_required_evidence_refs() -> None:
    with pytest.raises(ValidationError):
        _policy(requiredEvidenceRefs=())


def test_missing_required_evidence_retries_while_budget_remains_then_rejects() -> None:
    retry = accept_child_result(
        _issued_child_result(),
        _policy(requiredEvidenceRefs=("ledger:child-exec-1", "audit:missing")),
    )
    rejected = accept_child_result(
        _issued_child_result(),
        _policy(
            requiredEvidenceRefs=("ledger:child-exec-1", "audit:missing"),
            maxRetryBudget=1,
            currentAttempt=1,
        ),
    )

    assert retry.status == "retry"
    assert retry.reason_codes == ("missing_required_evidence",)
    assert retry.accepted_evidence_refs == ("ledger:child-exec-1",)
    assert retry.missing_evidence_refs == ("audit:missing",)
    assert retry.retryable is True
    assert retry.retry_budget_remaining == 1

    assert rejected.status == "rejected"
    assert rejected.reason_codes == ("missing_required_evidence", "retry_budget_exhausted")
    assert rejected.accepted_evidence_refs == ("ledger:child-exec-1",)
    assert rejected.missing_evidence_refs == ("audit:missing",)
    assert rejected.retryable is False
    assert rejected.retry_budget_remaining == 0


def test_child_ledger_refs_do_not_satisfy_required_evidence_without_runtime_receipt() -> None:
    verdict = accept_child_result(
        _issued_child_result(),
        _policy(requiredEvidenceRefs=("ledger:child-exec-1", "ledger:audit-child-proof")),
    )

    assert verdict.status == "retry"
    assert verdict.reason_codes == ("missing_required_evidence",)
    assert verdict.accepted_evidence_refs == ("ledger:child-exec-1",)
    assert verdict.missing_evidence_refs == ("ledger:audit-child-proof",)


def test_accepted_verdict_includes_only_digest_safe_public_refs() -> None:
    verdict = accept_child_result(_issued_child_result(), _policy())

    assert verdict.status == "accepted"
    assert verdict.reason_codes == ("accepted",)
    assert verdict.accepted_evidence_refs == (
        "ledger:child-exec-1",
        "receipt:child-envelope-1",
        "audit:child-envelope-issued",
    )
    assert verdict.missing_evidence_refs == ()

    projection = verdict.public_projection()
    dumped = json.dumps(projection, sort_keys=True)
    for unsafe in (
        "raw child transcript",
        "rawTranscriptRef",
        "privateMetadata",
        "toolArgs",
        "Bearer unsafe-token",
        "sk-child-secret",
        "/workspace",
    ):
        assert unsafe not in dumped
    assert projection == {
        "status": "accepted",
        "reasonCodes": ("accepted",),
        "acceptedEvidenceRefs": (
            "ledger:child-exec-1",
            "receipt:child-envelope-1",
            "audit:child-envelope-issued",
        ),
        "missingEvidenceRefs": (),
        "retryable": False,
        "retryBudgetRemaining": 1,
    }


def test_direct_model_text_or_dictionary_cannot_forge_accepted_status() -> None:
    forged = {
        "status": "accepted",
        "reasonCodes": ("accepted",),
        "acceptedEvidenceRefs": ("ledger:child-exec-1",),
        "missingEvidenceRefs": (),
        "retryable": False,
        "retryBudgetRemaining": 0,
    }

    with pytest.raises(ValidationError):
        ChildAcceptanceVerdict.model_validate("accepted")
    with pytest.raises(ValidationError):
        ChildAcceptanceVerdict.model_validate(forged)
    with pytest.raises(TypeError):
        ChildAcceptanceVerdict.model_construct(**forged)

    retry_verdict = accept_child_result(
        _issued_child_result(),
        _policy(requiredEvidenceRefs=("audit:missing",)),
    )
    with pytest.raises(ValidationError):
        retry_verdict.model_copy(
            update={
                "status": "accepted",
                "reasonCodes": ("accepted",),
                "acceptedEvidenceRefs": ("ledger:child-exec-1",),
                "missingEvidenceRefs": (),
                "retryable": False,
            }
        )

    with pytest.raises(ValidationError):
        ChildAcceptanceVerdict.model_validate(
            {
                "status": "retry",
                "reasonCodes": ("accepted",),
                "acceptedEvidenceRefs": (),
                "missingEvidenceRefs": ("audit:missing",),
                "retryable": True,
                "retryBudgetRemaining": 1,
            }
        )


def test_accepted_verdict_cannot_be_copied_with_mutated_evidence_refs() -> None:
    verdict = accept_child_result(_issued_child_result(), _policy())

    with pytest.raises(TypeError):
        verdict.model_copy(update={"acceptedEvidenceRefs": ("ledger:unverified-child",)})


def test_accepted_verdict_public_projection_rejects_post_evaluation_mutation() -> None:
    verdict = accept_child_result(_issued_child_result(), _policy())
    verdict.__dict__["accepted_evidence_refs"] = ("ledger:unverified-child",)

    with pytest.raises(ValueError, match="modified after child acceptance evaluation"):
        verdict.public_projection()
