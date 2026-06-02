from __future__ import annotations

import hashlib
import json

import pytest

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    ChildRuntimeEnvelopeAuthorityFlags,
)
from magi_agent.evidence.subagent import (
    DelegatedEvidenceRequirement,
    EvidenceBoundaryLedgerRef,
    ExecutionBoundaryIdentity,
)
from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    RuntimeIssuedChildResult,
    accept_child_result,
    issue_runtime_child_result,
)
from magi_agent.runtime.child_event_projection import (
    project_child_acceptance_verdict_event,
    project_child_runner_result_events,
    project_child_runtime_envelope_events,
)
from magi_agent.runtime.child_runner_boundary import (
    ChildRunnerEnvelopeRef,
    ChildRunnerResult,
    ChildTaskRequest,
)
from magi_agent.transport.sse import InMemorySseWriter

from runtime_issuance_support import issue_test_runtime_authority


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-child-event-projection",
        scopes=scopes,
    )


CHILD_RECEIPT_REF = "receipt:child-envelope-1"
BACKGROUND_CHILD_RECEIPT_REF = "receipt:background-child-envelope-1"
OTHER_CHILD_RECEIPT_REF = "receipt:other-child-envelope"


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
            "allowedToolNames": ("FileRead", "Bash"),
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
            "privateNotes": (
                "raw child transcript at /workspace/private with Bearer unsafe-token",
            ),
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
            "allowedToolNames": ("FileRead", "Bash"),
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


def _envelope(**overrides: object) -> ChildRuntimeEnvelope:
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **_envelope_payload(**overrides),
    )


def _request(**overrides: object) -> ChildTaskRequest:
    payload = {
        "parentExecutionId": "parent-exec-1",
        "turnId": "turn-1",
        "taskId": "task-1",
        "objective": "Review the patch without exposing raw child logs.",
        "role": "reviewer",
        "delivery": "return",
    }
    payload.update(overrides)
    return ChildTaskRequest.model_validate(payload)


def _runner_result(
    *,
    status: str = "completed",
    parent_execution_id: str = "parent-exec-1",
    result_status: str = "ok",
) -> ChildRunnerResult:
    envelope = ChildRunnerEnvelopeRef(
        childRef="child:runner-safe",
        taskId="task-1",
        childExecutionId="child-exec-1",
        parentExecutionId=parent_execution_id,
        status=status,
        summary=(
            "review complete\n"
            "raw_child_transcript: /workspace/private\n"
            "hidden_reasoning: do not expose"
        ),
        evidenceRefs=("evidence:child-review-1",),
        artifactRefs=("artifact:child-report-1",),
        auditEventRefs=("audit:child-run-local",),
    )
    return ChildRunnerResult(
        status=result_status,
        taskId="task-1",
        promptRef="prompt:safe",
        envelope=envelope,
    )


def _policy(**overrides: object) -> ChildAcceptancePolicy:
    payload = {
        "parentExecutionId": "parent-exec-1",
        "childExecutionId": "child-exec-1",
        "taskId": "task-1",
        "parentPolicySnapshotId": "policy-parent-1",
        "childPolicySnapshotId": "policy-parent-1",
        "runtimeReceiptRef": CHILD_RECEIPT_REF,
        "requiredEvidenceRefs": (
            "ledger:child-exec-1",
            CHILD_RECEIPT_REF,
            "audit:child-envelope-issued",
        ),
        "maxRetryBudget": 1,
        "currentAttempt": 0,
    }
    payload.update(overrides)
    return ChildAcceptancePolicy.model_validate(payload)


def _issued_child_result(
    envelope: ChildRuntimeEnvelope | None = None,
    *,
    receipt_ref: str = CHILD_RECEIPT_REF,
) -> RuntimeIssuedChildResult:
    return issue_runtime_child_result(envelope or _envelope(), receipt_ref=receipt_ref)


def _sse_sanitized(events: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    writer = InMemorySseWriter()
    for event in events:
        writer.agent(event)
    return [
        json.loads(line.removeprefix("data: "))
        for line in writer.body.splitlines()
        if line.startswith("data: ")
    ]


def _public_child_receipt_ref(value: str) -> str:
    if value.startswith("receipt:sha256:"):
        return value
    return "receipt:sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _public_child_id(value: str) -> str:
    return "child:" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _assert_no_private_payload(value: object) -> None:
    dumped = json.dumps(value, sort_keys=True)
    for unsafe in (
        "raw child transcript",
        "raw_child_transcript",
        "rawTranscriptRef",
        "privateMetadata",
        "toolArgs",
        "hidden_reasoning",
        "Bearer unsafe-token",
        "sk-child-secret",
        "/workspace",
        "/Users/",
        "FileRead",
        "Bash",
        "C:\\",
        "\\\\host",
    ):
        assert unsafe not in dumped


def test_runtime_issued_child_envelope_projects_public_child_lifecycle_events() -> None:
    events = project_child_runtime_envelope_events(
        _envelope(),
        receipt_ref=CHILD_RECEIPT_REF,
    )

    assert [event["type"] for event in events] == ["spawn_started", "child_progress"]
    assert {event["taskId"] for event in events} == {_public_child_id("task-1")}
    assert str(events[1]["childReceiptRef"]).startswith("receipt:sha256:")
    assert str(events[1]["childReceiptRef"]) in json.dumps(events)
    assert "role=coding" in json.dumps(events)
    assert "child_completed" not in json.dumps(events)
    assert events[1]["childReceiptRef"] == _public_child_receipt_ref(CHILD_RECEIPT_REF)

    sanitized = _sse_sanitized(events)
    assert [event["type"] for event in sanitized] == [event["type"] for event in events]
    assert sanitized[1]["childReceiptRef"] == _public_child_receipt_ref(CHILD_RECEIPT_REF)
    _assert_no_private_payload(events)
    _assert_no_private_payload(sanitized)


def test_background_child_envelope_projects_schedule_only_without_live_authority() -> None:
    child = _child_boundary(agentRole="research")
    events = project_child_runtime_envelope_events(
        _envelope(
            mode="background",
            status="accepted",
            childBoundary=child,
            ledgerRef=_ledger_ref(child),
            task={
                "taskId": "task-1",
                "persona": "research",
                "role": "research",
                "spawnDepth": 1,
                "deliver": "background",
                "promptRef": "prompt:task-1",
            },
            auditEventRefs=("audit:background-child-planned", "audit:background-issued"),
        ),
        receipt_ref=BACKGROUND_CHILD_RECEIPT_REF,
    )

    assert [event["type"] for event in events] == ["spawn_started", "child_progress"]
    assert "background_task" not in json.dumps(events)
    _assert_no_private_payload(_sse_sanitized(events))


def test_forged_child_envelope_is_revalidated_and_rejected() -> None:
    valid = _envelope()
    forged = ChildRuntimeEnvelope.model_validate(
        valid.model_dump(by_alias=True, mode="python", warnings=False)
    )
    assert forged.is_runtime_boundary_issued is False

    with pytest.raises(ValueError, match="runtime-issued"):
        project_child_runtime_envelope_events(forged, receipt_ref=CHILD_RECEIPT_REF)


def test_child_runtime_envelope_events_redact_windows_and_unc_persona_paths() -> None:
    windows_path = "C:" + "\\Users\\kevin\\secret\\persona.txt"
    unc_path = "\\\\" + "host\\share\\persona.txt"
    valid = _envelope(
        task={
            "taskId": "task-1",
            "persona": f"reviewer {windows_path} {unc_path}",
            "role": "coding",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        }
    )
    events = project_child_runtime_envelope_events(
        valid,
        receipt_ref=CHILD_RECEIPT_REF,
    )

    dumped = json.dumps(_sse_sanitized(events), sort_keys=True)

    assert windows_path not in dumped
    assert unc_path not in dumped
    assert "C:" not in dumped
    assert "\\\\host" not in dumped
    with pytest.raises(TypeError, match="model_construct is disabled"):
        ChildRuntimeEnvelope.model_construct(
            issuer="openmagi_runtime_boundary",
            mode=valid.mode,
            status=valid.status,
            parent_boundary=valid.parent_boundary,
            child_boundary=valid.child_boundary,
            task=valid.task,
            policy_snapshot=valid.policy_snapshot,
            ledger_ref=valid.ledger_ref,
            delegated_evidence_requirements=valid.delegated_evidence_requirements,
            workspace_isolation=valid.workspace_isolation,
            completion_contract=valid.completion_contract,
            audit_event_refs=valid.audit_event_refs,
            adk_primitive_ownership=valid.adk_primitive_ownership,
            authority_flags=valid.authority_flags,
            raw_transcript_ref=valid.raw_transcript_ref,
            private_metadata=valid.private_metadata,
        )

    with pytest.raises(TypeError, match="model_construct is disabled"):
        ChildRuntimeEnvelope.model_construct(**_envelope_payload())


def test_child_runtime_envelope_events_redact_url_source_locators() -> None:
    valid = _envelope(
        task={
            "taskId": "task-1",
            "persona": "researcher https://private.example/customer/acme",
            "role": "coding",
            "spawnDepth": 1,
            "deliver": "return",
            "promptRef": "prompt:task-1",
        }
    )
    events = project_child_runtime_envelope_events(
        valid,
        receipt_ref=CHILD_RECEIPT_REF,
    )

    dumped = json.dumps(_sse_sanitized(events), sort_keys=True)

    assert "private.example" not in dumped
    assert "https://" not in dumped
    assert "researcher" in dumped


def test_child_runner_result_lifecycle_requires_matching_parent_child_receipt() -> None:
    completed = project_child_runner_result_events(_request(), _runner_result())
    failed = project_child_runner_result_events(_request(), _runner_result(status="failed"))
    blocked = project_child_runner_result_events(_request(), _runner_result(status="blocked"))

    assert [event["type"] for event in completed] == [
        "spawn_started",
        "child_started",
        "child_progress",
        "spawn_result",
        "child_completed",
    ]
    assert completed[-2]["status"] == "ok"
    assert completed[-2]["toolCallCount"] == 0
    assert failed[-1]["type"] == "child_failed"
    assert failed[-2]["status"] == "error"
    assert failed[-2]["toolCallCount"] == 0
    assert blocked[-1]["type"] == "child_cancelled"
    assert blocked[-2]["status"] == "aborted"
    assert blocked[-2]["toolCallCount"] == 0
    for event in completed + failed + blocked:
        if str(event["type"]).startswith("child_"):
            assert str(event["childReceiptRef"]).startswith("receipt:sha256:")
    _assert_no_private_payload(_sse_sanitized(completed + failed + blocked))

    with pytest.raises(ValueError, match="parentExecutionId"):
        project_child_runner_result_events(
            _request(),
            _runner_result(parent_execution_id="other-parent"),
        )
    with pytest.raises(ValueError, match="status must be ok"):
        project_child_runner_result_events(
            _request(),
            _runner_result(result_status="error"),
        )


def test_background_child_runner_result_uses_ts_background_status_contract() -> None:
    completed = project_child_runner_result_events(
        _request(delivery="background"),
        _runner_result(),
    )
    failed = project_child_runner_result_events(
        _request(delivery="background"),
        _runner_result(status="failed"),
    )
    blocked = project_child_runner_result_events(
        _request(delivery="background"),
        _runner_result(status="blocked"),
    )

    completed_background = completed[1]
    failed_background = failed[1]
    blocked_background = blocked[1]

    assert completed_background == {
        "type": "background_task",
        "taskId": _public_child_id("task-1"),
        "persona": "reviewer",
        "status": "completed",
        "detail": completed_background["detail"],
    }
    assert failed_background["status"] == "failed"
    assert blocked_background["status"] == "aborted"


def test_child_acceptance_verdicts_project_accept_retry_reject_without_raw_child_output() -> None:
    accepted = accept_child_result(_issued_child_result(), _policy())
    retry = accept_child_result(
        _issued_child_result(),
        _policy(requiredEvidenceRefs=("ledger:child-exec-1", "audit:missing")),
    )
    rejected = accept_child_result(
        _issued_child_result(receipt_ref=OTHER_CHILD_RECEIPT_REF),
        _policy(),
    )

    accepted_event = project_child_acceptance_verdict_event(accepted, task_id="task-1")
    retry_event = project_child_acceptance_verdict_event(
        retry,
        task_id="task-1",
        receipt_ref=CHILD_RECEIPT_REF,
    )
    rejected_event = project_child_acceptance_verdict_event(
        rejected,
        task_id="task-1",
        receipt_ref=OTHER_CHILD_RECEIPT_REF,
    )

    assert accepted_event["type"] == "child_progress"
    assert accepted_event["taskId"] == _public_child_id("task-1")
    assert accepted_event["childReceiptRef"] == _public_child_receipt_ref(CHILD_RECEIPT_REF)
    assert accepted_event["detail"] == (
        "child_result status=accepted acceptedRefs=3 missingRefs=0 "
        f"retryBudgetRemaining=1 receipt={accepted_event['childReceiptRef']}"
    )
    assert retry_event["type"] == "child_progress"
    assert str(retry_event["childReceiptRef"]).startswith("receipt:sha256:")
    assert "status=retry" in retry_event["detail"]
    assert retry_event["childReceiptRef"] == _public_child_receipt_ref(CHILD_RECEIPT_REF)
    assert rejected_event["type"] == "child_failed"
    assert str(rejected_event["childReceiptRef"]).startswith("receipt:sha256:")
    assert rejected_event["errorMessage"] == (
        f"child_result rejected reason={_public_child_id('runtime_receipt_mismatch')}"
    )
    assert "runtime_receipt_mismatch" not in json.dumps(rejected_event, sort_keys=True)
    _assert_no_private_payload(_sse_sanitized((accepted_event, retry_event, rejected_event)))


def test_accepted_child_projection_ignores_unbound_caller_receipt_ref() -> None:
    accepted = accept_child_result(_issued_child_result(), _policy())

    event = project_child_acceptance_verdict_event(
        accepted,
        task_id="task-1",
        receipt_ref=OTHER_CHILD_RECEIPT_REF,
    )

    dumped = json.dumps(event, sort_keys=True)
    assert event["childReceiptRef"] == _public_child_receipt_ref(CHILD_RECEIPT_REF)
    assert OTHER_CHILD_RECEIPT_REF not in dumped
    assert _public_child_receipt_ref(OTHER_CHILD_RECEIPT_REF) not in dumped


def test_child_acceptance_projection_rejects_structural_fakes() -> None:
    class FakeVerdict:
        def public_projection(self) -> dict[str, object]:
            return {
                "status": "accepted",
                "reasonCodes": ("accepted",),
                "acceptedEvidenceRefs": ("ledger:child-exec-1",),
                "missingEvidenceRefs": (),
                "retryable": False,
                "retryBudgetRemaining": 0,
            }

    with pytest.raises(ValueError, match="evaluated child acceptance verdict"):
        project_child_acceptance_verdict_event(FakeVerdict(), task_id="task-1")
