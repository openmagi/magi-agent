from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from urllib.parse import quote

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    AuthorityResumeBinding,
    UserDecisionReceipt,
    UserDecisionRequest,
    canonical_authority_contract_digest,
    canonical_authority_resume_binding_digest,
    canonical_user_decision_receipt_digest,
    canonical_user_decision_request_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionDenialRecording,
    ActionIntent,
    ActionResolution,
    ActionResolutionRecording,
    EffectDeclarationBinding,
    ExecutionPreparation,
    ExecutionStart,
    JournalEvent,
    UserApprovalConsumption,
    UserDecisionExpirationRequest,
    UserDecisionInvalidationRequest,
    UserDecisionRecording,
    UserDecisionRequestRecording,
    UserDecisionSnapshot,
    UserDecisionTransition,
    VerifiedAuthorityResumeBinding,
    VerifiedUserDecisionReceipt,
    canonical_action_intent_digest,
    canonical_provider_guarantees_digest,
    canonical_resource_refs_digest,
    _draft_lifecycle_journal_event,
)
from magi_agent.execution_authority.state_machine import (
    ActionState,
    IdempotencyCapability,
    ProviderGuarantee,
    RecoveryStrategy,
    ResourceSemantics,
)


NOW = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)
D0 = "sha256:" + "0" * 64
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64
D5 = "sha256:" + "5" * 64
D6 = "sha256:" + "6" * 64
D7 = "sha256:" + "7" * 64
D8 = "sha256:" + "8" * 64
D9 = "sha256:" + "9" * 64
WORKSPACE_ROOT_REF = f"workspace://{D0}/"
WORKSPACE_A_REF = WORKSPACE_ROOT_REF + "a.txt"
WORKSPACE_UNICODE_REF = WORKSPACE_ROOT_REF + quote("한글🧪.txt", safe="")


def _request(*, unicode_values: bool = False) -> UserDecisionRequest:
    resource = WORKSPACE_UNICODE_REF if unicode_values else WORKSPACE_A_REF
    reason = "사용자 승인 필요" if unicode_values else "user_approval_required"
    capability = AuthorityCapability(
        effectClass="workspace.write",
        resourceRef=resource,
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=D8,
    )
    return UserDecisionRequest(
        schemaId="magi.user_decision_request.v1",
        decisionRequestId="decision_01",
        principalId="actor_01",
        tenantId="tenant_01",
        sessionId="session_01",
        turnId="turn_01",
        taskContractId="task_01",
        taskVersion=1,
        taskContractDigest=D1,
        completionEpochId="epoch_01",
        actionId="action_01",
        authorityPartitionId="workspace_01",
        normalizedRequestDigest=D2,
        capabilities=(capability,),
        workspaceViewBindingDigest=D8,
        authorityCeilingDigest=D3,
        policyDigest=D4,
        pendingEventId="event_pending",
        reasonCodes=(reason,),
        createdAt=NOW,
        expiresAt=NOW + timedelta(minutes=10),
        compareVersion=0,
    )


def _receipt(
    request: UserDecisionRequest,
    *,
    decision: str = "approve",
    receipt_id: str = "receipt_approve",
    revokes_receipt_digest: str | None = None,
) -> UserDecisionReceipt:
    return UserDecisionReceipt(
        schemaId="magi.user_decision_receipt.v1",
        receiptId=receipt_id,
        decision=decision,
        decisionRequestId=request.decision_request_id,
        authenticatedActorId=request.principal_id,
        authenticationKeyId="key_01",
        authenticationContextDigest=D5,
        authenticationNonceDigest=D6,
        transportReceiptDigest=D7,
        principalId=request.principal_id,
        tenantId=request.tenant_id,
        sessionId=request.session_id,
        turnId=request.turn_id,
        taskContractId=request.task_contract_id,
        taskVersion=request.task_version,
        taskContractDigest=request.task_contract_digest,
        completionEpochId=request.completion_epoch_id,
        actionId=request.action_id,
        authorityPartitionId=request.authority_partition_id,
        normalizedRequestDigest=request.normalized_request_digest,
        authorityCeilingDigest=request.authority_ceiling_digest,
        policyDigest=request.policy_digest,
        capabilitiesDigest=request.capabilities_digest,
        workspaceViewBindingDigest=request.workspace_view_binding_digest,
        issuedAt=NOW + timedelta(seconds=1),
        expiresAt=NOW + timedelta(minutes=9),
        revokesReceiptDigest=revokes_receipt_digest,
    )


def _request_json(request: UserDecisionRequest) -> str:
    return json.dumps(
        request.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _verified_receipt(receipt: UserDecisionReceipt) -> VerifiedUserDecisionReceipt:
    return VerifiedUserDecisionReceipt(
        schemaVersion=1,
        receipt=receipt,
        receiptDigest=canonical_user_decision_receipt_digest(receipt),
        verifierId="decision-ingress",
        verifierArtifactDigest=D8,
        verifiedAt=NOW + timedelta(seconds=1),
    )


def _verified_resume(
    request: UserDecisionRequest,
    resume: AuthorityResumeBinding,
) -> VerifiedAuthorityResumeBinding:
    return VerifiedAuthorityResumeBinding(
        schemaVersion=1,
        binding=resume,
        bindingDigest=canonical_authority_resume_binding_digest(resume),
        currentPolicyDigest=request.policy_digest,
        currentCapabilitiesDigest=request.capabilities_digest,
        verifierId="resume-binding-verifier",
        verifierArtifactDigest=D9,
        verifiedAt=NOW + timedelta(seconds=3),
    )


def _model_digest(
    value: (UserDecisionInvalidationRequest | UserDecisionExpirationRequest | ActionResolution),
) -> str:
    payload = value.model_dump(by_alias=True, mode="json")
    return (
        "sha256:"
        + sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )


def _transition_payload(
    transition_request: UserDecisionInvalidationRequest | UserDecisionExpirationRequest,
    *,
    from_state: str,
    to_state: str,
    previous: UserDecisionSnapshot,
    current: UserDecisionSnapshot,
    resolution: ActionResolution,
) -> dict[str, object]:
    return {
        "actionResolutionDigest": _model_digest(resolution),
        "currentSnapshotCompareVersion": current.compare_version,
        "decisionRequestId": transition_request.decision_request_id,
        "fromState": from_state,
        "previousSnapshotCompareVersion": previous.compare_version,
        "toState": to_state,
        "transitionRequestDigest": _model_digest(transition_request),
    }


def _snapshot(
    request: UserDecisionRequest,
    *,
    state: str,
    approval_receipt_digest: str | None,
    latest_receipt_id: str | None,
    latest_receipt_digest: str | None,
    compare_version: int,
) -> UserDecisionSnapshot:
    return UserDecisionSnapshot(
        schemaVersion=1,
        request=request,
        requestJson=_request_json(request),
        decisionRequestDigest=canonical_user_decision_request_digest(request),
        state=state,
        approvalReceiptDigest=approval_receipt_digest,
        latestReceiptId=latest_receipt_id,
        latestReceiptDigest=latest_receipt_digest,
        compareVersion=compare_version,
    )


def _event(
    event_type: str,
    request: UserDecisionRequest,
    *,
    event_id: str,
    attempt_id: str = "attempt_01",
    authority_contract_id: str | None = None,
    fencing_token: int = 7,
    causation_id: str | None = None,
    payload: dict[str, object] | None = None,
    sequence: int = 1,
    previous_hash: str = D0,
    event_hash: str = D1,
    row_checksum: str = D2,
    created_at: datetime = NOW + timedelta(seconds=2),
) -> JournalEvent:
    draft = _draft_lifecycle_journal_event(
        event_id=event_id,
        partition_id=request.authority_partition_id,
        event_type=event_type,
        action_id=request.action_id,
        attempt_id=attempt_id,
        task_contract_id=request.task_contract_id,
        task_version=request.task_version,
        task_contract_digest=request.task_contract_digest,
        completion_epoch_id=request.completion_epoch_id,
        admission_sequence=1,
        authority_contract_id=authority_contract_id,
        request_digest=request.normalized_request_digest,
        idempotency_key_digest=D5,
        fencing_token=fencing_token,
        actor_id=request.principal_id,
        policy_digest=request.policy_digest,
        causation_id=causation_id or request.turn_id,
        correlation_id="run_01",
        identity_digest=D6,
        payload={} if payload is None else payload,
    )
    return JournalEvent(
        **draft.model_dump(by_alias=True),
        sequence=sequence,
        previousHash=previous_hash,
        eventHash=event_hash,
        rowChecksum=row_checksum,
        createdAt=created_at,
    )


def _resume(request: UserDecisionRequest) -> AuthorityResumeBinding:
    return AuthorityResumeBinding(
        decisionRequestId=request.decision_request_id,
        authenticatedActorId=request.principal_id,
        sessionId=request.session_id,
        turnId=request.turn_id,
        runId="run_01",
        actionId=request.action_id,
        taskContractId=request.task_contract_id,
        taskVersion=request.task_version,
        taskContractDigest=request.task_contract_digest,
        completionEpochId=request.completion_epoch_id,
        transcriptDigest=D1,
        checkpointDigest=D2,
        authorityPartitionId=request.authority_partition_id,
        expectedHeadSequence=10,
        expectedHeadHash=D3,
        expectedHeadCompareVersion=11,
        stateProjectionId="projection_01",
        expectedStateSequence=8,
        expectedStateEventHash=D4,
        expectedStateRoot=D5,
        expectedStateCompareVersion=9,
    )


def _authority(
    request: UserDecisionRequest,
    resume: AuthorityResumeBinding | None,
    *,
    attempt_id: str = "attempt_01",
) -> AuthorityContract:
    return AuthorityContract(
        schemaVersion=1,
        authorityContractId="authority_01",
        issuerId="broker_01",
        principalId=request.principal_id,
        tenantId=request.tenant_id,
        sessionId=request.session_id,
        turnId=request.turn_id,
        childActorId=None,
        taskContractId=request.task_contract_id,
        taskVersion=request.task_version,
        taskContractDigest=request.task_contract_digest,
        completionEpochId=request.completion_epoch_id,
        authorityPartitionId=request.authority_partition_id,
        actionId=request.action_id,
        attemptId=attempt_id,
        policyDigest=request.policy_digest,
        normalizedRequestDigest=request.normalized_request_digest,
        commandDigest=D1,
        argumentsDigest=D2,
        workingDirectoryDigest=D3,
        environmentDigest=D4,
        requestBodyDigest=None,
        credentialScopeDigest=None,
        networkDigest=None,
        disclosureDigest=D5,
        capabilities=request.capabilities,
        workspaceViewBindingDigest=request.workspace_view_binding_digest,
        sandboxProfileDigest=D6,
        guardianCeilingDigest=request.authority_ceiling_digest,
        expiresAt=NOW + timedelta(minutes=8),
        revokedAt=None,
        revocationDigest=None,
        fencingToken=7,
        maximumUses=1,
        decisionRequestId=(request.decision_request_id if resume is not None else None),
        resumeBindingDigest=(
            canonical_authority_resume_binding_digest(resume)
            if resume is not None
            else None
        ),
        parentAuthorityDigest=None,
        delegationChain=(),
    )


def _intent(request: UserDecisionRequest, authority: AuthorityContract) -> ActionIntent:
    guarantees = (ProviderGuarantee.LOCAL_ATOMIC,)
    declaration = EffectDeclarationBinding(
        effectName="workspace.patch",
        effectClass="workspace.write",
        resourceSemantics=ResourceSemantics.WORKSPACE_TRANSACTION,
        handlerDigest=D1,
        normalizerDigest=D2,
        resourceDeriverDigest=D3,
        executorDigest=D4,
        recoveryAdapterDigest=D5,
        providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
        providerGuarantees=guarantees,
        idempotencyCapability=IdempotencyCapability.LOCAL_GENERATION_CAS,
        recoveryStrategy=RecoveryStrategy.WORKSPACE_TRANSACTION,
    )
    resource_refs = (request.capabilities[0].resource_ref,)
    return ActionIntent(
        schemaId="magi.action_intent.v1",
        actionId=request.action_id,
        attemptId=authority.attempt_id,
        partitionId=request.authority_partition_id,
        actorId=request.principal_id,
        identityDigest=D6,
        policyDigest=request.policy_digest,
        sessionId=request.session_id,
        turnId=request.turn_id,
        runId="run_01",
        taskContractId=request.task_contract_id,
        taskVersion=request.task_version,
        taskContractDigest=request.task_contract_digest,
        completionEpochId=request.completion_epoch_id,
        declaration=declaration,
        capabilities=request.capabilities,
        normalizedInputDigest=request.normalized_request_digest,
        normalizedRequestSnapshotRef=f"authority-input://{request.normalized_request_digest}",
        readSet=resource_refs,
        absenceSet=(),
        writeSet=resource_refs,
        egressSet=(),
        readSetDigest=canonical_resource_refs_digest(resource_refs),
        absenceSetDigest=canonical_resource_refs_digest(()),
        writeSetDigest=canonical_resource_refs_digest(resource_refs),
        egressSetDigest=canonical_resource_refs_digest(()),
        workspaceViewBindingDigest=request.workspace_view_binding_digest,
        idempotencyKeyDigest=D5,
        evidenceObligations=("action_receipt", "workspace_postcondition"),
        compensatesActionId=None,
        admissionSequence=1,
    )


def _preparation(
    request: UserDecisionRequest,
    authority: AuthorityContract,
    *,
    payload_resume_digest: str | None = None,
) -> ExecutionPreparation:
    authority_digest = canonical_authority_contract_digest(authority)
    intent = _intent(request, authority)
    intent_digest = canonical_action_intent_digest(intent)
    resume_digest = authority.resume_binding_digest
    payload = {
        "authorityContractDigest": authority_digest,
        "actionIntentDigest": intent_digest,
    }
    if resume_digest is not None:
        payload.update(
            {
                "decisionRequestId": request.decision_request_id,
                "resumeBindingDigest": payload_resume_digest or resume_digest,
            }
        )
    approval_flow = resume_digest is not None
    return ExecutionPreparation(
        schemaVersion=1,
        intent=intent,
        authorityContract=authority,
        actionId=request.action_id,
        attemptId=authority.attempt_id,
        partitionId=request.authority_partition_id,
        taskContractDigest=request.task_contract_digest,
        actionIntentDigest=intent_digest,
        requestDigest=request.normalized_request_digest,
        authorityContractId=authority.authority_contract_id,
        authorityContractDigest=authority_digest,
        fencingToken=authority.fencing_token,
        actionCompareVersion=3,
        attemptCompareVersion=4,
        partitionCompareVersion=5,
        authorityEvent=_event(
            "action.authorized",
            request,
            event_id="event_authorized",
            attempt_id=authority.attempt_id,
            authority_contract_id=authority.authority_contract_id,
            causation_id=("event_approval_recorded" if approval_flow else request.turn_id),
            payload=payload,
            sequence=(2 if approval_flow else 1),
            previous_hash=(D1 if approval_flow else D0),
            event_hash=(D2 if approval_flow else D1),
            row_checksum=(D3 if approval_flow else D2),
            created_at=NOW + timedelta(seconds=(4 if approval_flow else 1)),
        ),
        preparedEvent=_event(
            "action.prepared",
            request,
            event_id="event_prepared",
            attempt_id=authority.attempt_id,
            authority_contract_id=authority.authority_contract_id,
            causation_id="event_authorized",
            payload=payload,
            sequence=(3 if approval_flow else 2),
            previous_hash=(D2 if approval_flow else D1),
            event_hash=(D3 if approval_flow else D2),
            row_checksum=(D4 if approval_flow else D3),
            created_at=NOW + timedelta(seconds=(5 if approval_flow else 2)),
        ),
    )


def _approval_recording(
    request: UserDecisionRequest,
    receipt: UserDecisionReceipt,
) -> UserDecisionRecording:
    receipt_digest = canonical_user_decision_receipt_digest(receipt)
    verified = _verified_receipt(receipt)
    previous = _snapshot(
        request,
        state="pending",
        approval_receipt_digest=None,
        latest_receipt_id=None,
        latest_receipt_digest=None,
        compare_version=0,
    )
    current = _snapshot(
        request,
        state="approved",
        approval_receipt_digest=receipt_digest,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=1,
    )
    return UserDecisionRecording(
        schemaVersion=1,
        verifiedReceipt=verified,
        receipt=receipt,
        appliedFromState="pending",
        appliedToState="approved",
        previousSnapshot=previous,
        expectedDecisionCompareVersion=0,
        decisionCompareVersion=1,
        recordedEvent=_event(
            "user_decision.recorded",
            request,
            event_id="event_approval_recorded",
            causation_id=request.pending_event_id,
            payload={
                "decision": receipt.decision,
                "decisionRequestId": receipt.decision_request_id,
                "decisionRequestDigest": current.decision_request_digest,
                "receiptId": receipt.receipt_id,
                "receiptDigest": receipt_digest,
                "verifiedReceiptDigest": _model_digest(verified),
                "authenticationNonceDigest": receipt.authentication_nonce_digest,
                "appliedFromState": "pending",
                "appliedToState": "approved",
                "previousSnapshotCompareVersion": 0,
                "currentSnapshotCompareVersion": 1,
            },
            created_at=NOW + timedelta(seconds=2),
        ),
        currentSnapshot=current,
        replayed=False,
    )


def _consumption_payload() -> dict[str, object]:
    request = _request()
    receipt = _receipt(request)
    receipt_digest = canonical_user_decision_receipt_digest(receipt)
    snapshot = _snapshot(
        request,
        state="approved",
        approval_receipt_digest=receipt_digest,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=1,
    )
    resume = _resume(request)
    verified_resume = _verified_resume(request, resume)
    authority = _authority(request, resume)
    authority_digest = canonical_authority_contract_digest(authority)
    resume_digest = canonical_authority_resume_binding_digest(resume)
    preparation = _preparation(request, authority)
    approval_recording = _approval_recording(request, receipt)
    consumed_snapshot = _snapshot(
        request,
        state="consumed",
        approval_receipt_digest=receipt_digest,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=2,
    )
    return {
        "schemaVersion": 1,
        "decisionRequestId": request.decision_request_id,
        "taskContractDigest": request.task_contract_digest,
        "approvalReceipt": receipt,
        "approvalReceiptDigest": receipt_digest,
        "approvalRecording": approval_recording,
        "approvedSnapshot": snapshot,
        "resumeBinding": resume,
        "resumeBindingDigest": resume_digest,
        "verifiedResumeBinding": verified_resume,
        "currentPolicyDigest": request.policy_digest,
        "currentCapabilitiesDigest": request.capabilities_digest,
        "authorityContract": authority,
        "authorityContractDigest": authority_digest,
        "expectedActionCompareVersion": 2,
        "expectedAttemptCompareVersion": 3,
        "expectedPartitionCompareVersion": 4,
        "decisionCompareVersion": 2,
        "preparation": preparation,
        "consumedSnapshot": consumed_snapshot,
        "consumedEvent": _event(
            "user_decision.consumed",
            request,
            event_id="event_decision_consumed",
            attempt_id=authority.attempt_id,
            authority_contract_id=authority.authority_contract_id,
            payload={
                "decisionRequestId": request.decision_request_id,
                "approvalReceiptDigest": receipt_digest,
                "resumeBindingDigest": resume_digest,
                "verifiedResumeBindingDigest": _model_digest(verified_resume),
                "currentPolicyDigest": request.policy_digest,
                "currentCapabilitiesDigest": request.capabilities_digest,
                "authorityContractDigest": authority_digest,
                "preparedEventId": preparation.prepared_event.event_id,
            },
            sequence=4,
            previous_hash=D3,
            event_hash=D4,
            row_checksum=D5,
            causation_id=preparation.prepared_event.event_id,
            created_at=NOW + timedelta(seconds=6),
        ),
    }


def test_snapshot_uses_the_contract_canonical_digest_for_unicode_request() -> None:
    request = _request(unicode_values=True)
    request_json = _request_json(request)
    helper_digest = canonical_user_decision_request_digest(request)
    utf8_json_digest = "sha256:" + sha256(request_json.encode("utf-8")).hexdigest()

    assert helper_digest == utf8_json_digest
    snapshot = _snapshot(
        request,
        state="pending",
        approval_receipt_digest=None,
        latest_receipt_id=None,
        latest_receipt_digest=None,
        compare_version=0,
    )
    assert snapshot.decision_request_digest == helper_digest


def test_request_user_decision_returns_an_event_bound_persistence_receipt() -> None:
    request = _request()
    snapshot = _snapshot(
        request,
        state="pending",
        approval_receipt_digest=None,
        latest_receipt_id=None,
        latest_receipt_digest=None,
        compare_version=0,
    )
    recording = UserDecisionRequestRecording(
        schemaVersion=1,
        request=request,
        snapshot=snapshot,
        decisionCompareVersion=0,
        requestedEvent=_event(
            "user_decision.pending",
            request,
            event_id=request.pending_event_id,
            payload={
                "decisionRequestDigest": canonical_user_decision_request_digest(request),
                "decisionRequestId": request.decision_request_id,
                "decisionCompareVersion": 0,
            },
        ),
    )

    assert recording.snapshot == snapshot


def test_deny_and_resolve_persistence_receipts_bind_events_and_cas() -> None:
    request = _request()
    denial = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId=request.action_id,
        taskContractDigest=request.task_contract_digest,
        sourceAttemptIds=("attempt_01",),
        resolutionAttemptId=None,
        logicalState="denied",
        reasonCodes=("policy_denied",),
    )
    denial_digest = _model_digest(denial)
    denied_event = _event(
        "action.denied",
        request,
        event_id="event_denied",
        payload={
            "actionResolutionDigest": denial_digest,
            "reasonCodes": list(denial.reason_codes),
            "sourceAttemptIds": list(denial.source_attempt_ids),
        },
    )
    resolution_event = _event(
        "action.resolved",
        request,
        event_id="event_resolved",
        causation_id=denied_event.event_id,
        payload={
            "actionResolutionDigest": denial_digest,
            "logicalState": "denied",
            "sourceEventId": denied_event.event_id,
        },
        sequence=2,
        previous_hash=denied_event.event_hash,
        event_hash=D2,
        row_checksum=D3,
    )
    denial_recording = ActionDenialRecording(
        schemaVersion=1,
        resolution=denial,
        expectedActionCompareVersion=1,
        expectedAttemptCompareVersion=2,
        expectedPartitionCompareVersion=3,
        actionCompareVersion=2,
        attemptCompareVersion=3,
        partitionCompareVersion=4,
        deniedEvent=denied_event,
        resolutionEvent=resolution_event,
    )
    assert denial_recording.action_compare_version == 2

    terminal = _event(
        "action.verified",
        request,
        event_id="event_verified",
    )
    verified_resolution = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId=request.action_id,
        taskContractDigest=request.task_contract_digest,
        sourceAttemptIds=("attempt_01",),
        resolutionAttemptId=None,
        logicalState="verified",
        reasonCodes=("verified",),
    )
    verified_digest = _model_digest(verified_resolution)
    resolved = _event(
        "action.resolved",
        request,
        event_id="event_verified_resolved",
        causation_id=terminal.event_id,
        payload={
            "actionResolutionDigest": verified_digest,
            "logicalState": "verified",
            "sourceEventId": terminal.event_id,
        },
        sequence=2,
        previous_hash=terminal.event_hash,
        event_hash=D2,
        row_checksum=D3,
    )
    resolution_recording = ActionResolutionRecording(
        schemaVersion=1,
        resolution=verified_resolution,
        expectedActionCompareVersion=4,
        expectedPartitionCompareVersion=5,
        actionCompareVersion=5,
        partitionCompareVersion=6,
        sourceEvent=terminal,
        resolutionEvent=resolved,
    )
    assert resolution_recording.resolution == verified_resolution


def test_fresh_approval_recording_binds_approval_pointer_to_canonical_receipt() -> None:
    request = _request()
    receipt = _receipt(request)
    receipt_digest = canonical_user_decision_receipt_digest(receipt)
    snapshot = _snapshot(
        request,
        state="approved",
        approval_receipt_digest=D9,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=1,
    )
    recording = _approval_recording(request, receipt)
    payload = recording.model_dump(by_alias=True, mode="json")
    payload["currentSnapshot"] = snapshot.model_dump(by_alias=True, mode="json")
    with pytest.raises(ValidationError, match="approvalReceiptDigest"):
        UserDecisionRecording.model_validate(payload)


def test_approval_recording_embeds_verified_ingress_and_exact_cas_transition() -> None:
    request = _request()
    receipt = _receipt(request)
    receipt_digest = canonical_user_decision_receipt_digest(receipt)
    verified = VerifiedUserDecisionReceipt(
        schemaVersion=1,
        receipt=receipt,
        receiptDigest=receipt_digest,
        verifierId="decision-ingress",
        verifierArtifactDigest=D8,
        verifiedAt=NOW + timedelta(seconds=1),
    )
    previous = _snapshot(
        request,
        state="pending",
        approval_receipt_digest=None,
        latest_receipt_id=None,
        latest_receipt_digest=None,
        compare_version=0,
    )
    current = _snapshot(
        request,
        state="approved",
        approval_receipt_digest=receipt_digest,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=1,
    )
    verified_digest = _model_digest(verified)
    payload = {
        "decision": receipt.decision,
        "decisionRequestId": receipt.decision_request_id,
        "decisionRequestDigest": current.decision_request_digest,
        "receiptId": receipt.receipt_id,
        "receiptDigest": receipt_digest,
        "verifiedReceiptDigest": verified_digest,
        "authenticationNonceDigest": receipt.authentication_nonce_digest,
        "appliedFromState": "pending",
        "appliedToState": "approved",
        "previousSnapshotCompareVersion": 0,
        "currentSnapshotCompareVersion": 1,
    }

    recording = UserDecisionRecording(
        schemaVersion=1,
        verifiedReceipt=verified,
        receipt=receipt,
        appliedFromState="pending",
        appliedToState="approved",
        previousSnapshot=previous,
        expectedDecisionCompareVersion=0,
        decisionCompareVersion=1,
        recordedEvent=_event(
            "user_decision.recorded",
            request,
            event_id="event_approval_recorded",
            causation_id=request.pending_event_id,
            payload=payload,
        ),
        currentSnapshot=current,
        replayed=False,
    )

    assert recording.previous_snapshot == previous
    assert recording.decision_compare_version == 1

    drifted = recording.model_dump(by_alias=True, mode="json")
    drifted["expectedDecisionCompareVersion"] = 1
    with pytest.raises(ValidationError, match="expected decision CAS|decisionCompareVersion"):
        UserDecisionRecording.model_validate(drifted)


def test_replayed_approval_cannot_claim_an_unreachable_snapshot_state() -> None:
    request = _request()
    receipt = _receipt(request)
    receipt_digest = canonical_user_decision_receipt_digest(receipt)
    snapshot_payload = {
        "schemaVersion": 1,
        "request": request,
        "requestJson": _request_json(request),
        "decisionRequestDigest": canonical_user_decision_request_digest(request),
        "state": "denied",
        "approvalReceiptDigest": receipt_digest,
        "latestReceiptId": receipt.receipt_id,
        "latestReceiptDigest": receipt_digest,
        "compareVersion": 1,
    }

    with pytest.raises(ValidationError, match="denied|replayed|approvalReceiptDigest"):
        UserDecisionRecording(
            schemaVersion=1,
            receipt=receipt,
            appliedFromState="pending",
            appliedToState="approved",
            recordedEvent=_event(
                "user_decision.recorded",
                request,
                event_id="event_approval_recorded",
            ),
            currentSnapshot=snapshot_payload,
            replayed=True,
        )


def test_denial_recording_exactly_binds_terminal_event_payloads_and_causation() -> None:
    request = _request()
    receipt = _receipt(request, decision="deny", receipt_id="receipt_deny")
    verified = _verified_receipt(receipt)
    receipt_digest = canonical_user_decision_receipt_digest(receipt)
    previous = _snapshot(
        request,
        state="pending",
        approval_receipt_digest=None,
        latest_receipt_id=None,
        latest_receipt_digest=None,
        compare_version=0,
    )
    current = _snapshot(
        request,
        state="denied",
        approval_receipt_digest=None,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=1,
    )
    resolution = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId=request.action_id,
        taskContractDigest=request.task_contract_digest,
        sourceAttemptIds=("attempt_01",),
        resolutionAttemptId=None,
        logicalState="denied",
        reasonCodes=("user_denied",),
    )
    resolution_digest = _model_digest(resolution)
    recorded_event = _event(
        "user_decision.recorded",
        request,
        event_id="event_denial_recorded",
        causation_id=request.pending_event_id,
        payload={
            "decision": receipt.decision,
            "decisionRequestId": receipt.decision_request_id,
            "decisionRequestDigest": current.decision_request_digest,
            "receiptId": receipt.receipt_id,
            "receiptDigest": receipt_digest,
            "verifiedReceiptDigest": _model_digest(verified),
            "authenticationNonceDigest": receipt.authentication_nonce_digest,
            "appliedFromState": "pending",
            "appliedToState": "denied",
            "previousSnapshotCompareVersion": 0,
            "currentSnapshotCompareVersion": 1,
        },
    )
    denied_event = _event(
        "action.denied",
        request,
        event_id="event_action_denied",
        causation_id=recorded_event.event_id,
        payload={
            "actionResolutionDigest": resolution_digest,
            "decisionReceiptDigest": receipt_digest,
            "decisionRequestId": request.decision_request_id,
            "recordedEventId": recorded_event.event_id,
        },
        sequence=2,
        previous_hash=recorded_event.event_hash,
        event_hash=D2,
        row_checksum=D3,
    )
    resolution_event = _event(
        "action.resolved",
        request,
        event_id="event_action_resolved",
        causation_id=denied_event.event_id,
        payload={
            "actionResolutionDigest": resolution_digest,
            "deniedEventId": denied_event.event_id,
            "logicalState": "denied",
        },
        sequence=3,
        previous_hash=denied_event.event_hash,
        event_hash=D3,
        row_checksum=D4,
    )
    payload = {
        "schemaVersion": 1,
        "verifiedReceipt": verified,
        "receipt": receipt,
        "appliedFromState": "pending",
        "appliedToState": "denied",
        "previousSnapshot": previous,
        "expectedDecisionCompareVersion": 0,
        "decisionCompareVersion": 1,
        "recordedEvent": recorded_event,
        "actionResolution": resolution,
        "deniedEvent": denied_event,
        "resolutionEvent": resolution_event,
        "currentSnapshot": current,
        "replayed": False,
    }

    recording = UserDecisionRecording.model_validate(payload)
    assert recording.resolution_event.causation_id == denied_event.event_id

    drifted = resolution_event.model_dump(by_alias=True, mode="json")
    drifted["causationId"] = request.turn_id
    with pytest.raises(ValidationError, match="caused by deniedEvent"):
        UserDecisionRecording.model_validate({**payload, "resolutionEvent": drifted})


def test_transition_carries_both_cas_snapshots_and_preserves_approval_history() -> None:
    request = _request()
    receipt = _receipt(request)
    receipt_digest = canonical_user_decision_receipt_digest(receipt)
    previous = _snapshot(
        request,
        state="approved",
        approval_receipt_digest=receipt_digest,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=1,
    )
    current = _snapshot(
        request,
        state="expired",
        approval_receipt_digest=receipt_digest,
        latest_receipt_id=receipt.receipt_id,
        latest_receipt_digest=receipt_digest,
        compare_version=2,
    )
    transition_request = UserDecisionExpirationRequest(
        schemaVersion=1,
        decisionRequestId=request.decision_request_id,
        taskContractDigest=request.task_contract_digest,
        actionId=request.action_id,
        partitionId=request.authority_partition_id,
        expectedDecisionCompareVersion=1,
        expectedActionCompareVersion=2,
        expectedPartitionCompareVersion=3,
        reasonCodes=("approval_expired",),
    )
    resolution = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId=request.action_id,
        taskContractDigest=request.task_contract_digest,
        sourceAttemptIds=("attempt_01",),
        resolutionAttemptId=None,
        logicalState=ActionState.DENIED,
        reasonCodes=("approval_expired",),
    )
    payload = {
        "schemaVersion": 1,
        "request": transition_request,
        "fromState": "approved",
        "toState": "expired",
        "previousSnapshot": previous,
        "currentSnapshot": current,
        "decisionCompareVersion": 2,
        "actionCompareVersion": 3,
        "partitionCompareVersion": 4,
        "transitionEvent": _event(
            "user_decision.expired",
            request,
            event_id="event_decision_expired",
            payload=_transition_payload(
                transition_request,
                from_state="approved",
                to_state="expired",
                previous=previous,
                current=current,
                resolution=resolution,
            ),
            created_at=request.expires_at,
        ),
        "actionResolution": resolution,
        "deniedEvent": _event(
            "action.denied",
            request,
            event_id="event_action_denied",
            causation_id="event_decision_expired",
            payload={
                "actionResolutionDigest": _model_digest(resolution),
                "transitionEventId": "event_decision_expired",
                "transitionRequestDigest": _model_digest(transition_request),
            },
            created_at=request.expires_at,
            sequence=2,
            previous_hash=D1,
            event_hash=D2,
            row_checksum=D3,
        ),
        "resolutionEvent": _event(
            "action.resolved",
            request,
            event_id="event_action_resolved",
            causation_id="event_action_denied",
            payload={
                "actionResolutionDigest": _model_digest(resolution),
                "deniedEventId": "event_action_denied",
                "logicalState": "denied",
            },
            created_at=request.expires_at,
            sequence=3,
            previous_hash=D2,
            event_hash=D3,
            row_checksum=D4,
        ),
    }

    transition = UserDecisionTransition.model_validate(payload)
    assert transition.current_snapshot.approval_receipt_digest == receipt_digest

    early = transition.model_dump(by_alias=True, mode="json")
    early["transitionEvent"]["createdAt"] = (
        request.expires_at - timedelta(microseconds=1)
    ).isoformat()
    with pytest.raises(ValidationError, match="expiresAt"):
        UserDecisionTransition.model_validate(early)

    drifted = current.model_dump(by_alias=True, mode="json")
    drifted["approvalReceiptDigest"] = D9
    with pytest.raises(ValidationError, match="receipt history"):
        UserDecisionTransition.model_validate({**payload, "currentSnapshot": drifted})


def test_user_approval_consumption_binds_every_approved_resume_and_preparation_input() -> None:
    payload = _consumption_payload()
    consumption = UserApprovalConsumption.model_validate(payload)

    assert consumption.decision_compare_version == (
        consumption.approved_snapshot.compare_version + 1
    )
    assert consumption.preparation.authority_contract_digest == (
        consumption.authority_contract_digest
    )

    for field, value, message in (
        ("approvalReceiptDigest", D9, "approvalReceiptDigest"),
        ("resumeBindingDigest", D9, "resumeBindingDigest"),
        ("authorityContractDigest", D9, "authorityContractDigest"),
        ("expectedAttemptCompareVersion", 2, "attemptCompareVersion"),
    ):
        with pytest.raises(ValidationError, match=message):
            UserApprovalConsumption.model_validate({**payload, field: value})


def test_user_approval_consumption_rejects_stale_current_state_or_expired_chronology() -> None:
    payload = _consumption_payload()
    verified = payload["verifiedResumeBinding"].model_dump(by_alias=True, mode="json")
    verified["currentPolicyDigest"] = D9
    with pytest.raises(ValidationError, match="currentPolicyDigest"):
        UserApprovalConsumption.model_validate(
            {**payload, "verifiedResumeBinding": verified}
        )

    consumed = payload["consumedEvent"].model_dump(by_alias=True, mode="json")
    consumed["createdAt"] = (
        payload["approvalReceipt"].expires_at + timedelta(microseconds=1)
    ).isoformat()
    with pytest.raises(ValidationError, match="expiresAt"):
        UserApprovalConsumption.model_validate({**payload, "consumedEvent": consumed})


def test_user_approval_consumption_rejects_authority_attempt_and_event_payload_drift() -> None:
    payload = _consumption_payload()
    request = payload["approvedSnapshot"].request
    resume = payload["resumeBinding"]
    drifted_authority = _authority(request, resume, attempt_id="attempt_other")
    drifted_authority_digest = canonical_authority_contract_digest(drifted_authority)

    with pytest.raises(ValidationError, match="attemptId"):
        UserApprovalConsumption.model_validate(
            {
                **payload,
                "authorityContract": drifted_authority,
                "authorityContractDigest": drifted_authority_digest,
            }
        )

    authority = payload["authorityContract"]
    with pytest.raises(ValidationError, match="payload"):
        _preparation(request, authority, payload_resume_digest=D9)


@pytest.mark.parametrize(
    ("field", "value"),
    (("authenticatedActorId", "actor_other"), ("runId", "run_other")),
)
def test_user_approval_consumption_rejects_resume_actor_or_run_substitution(
    field: str,
    value: str,
) -> None:
    payload = _consumption_payload()
    request = payload["approvedSnapshot"].request
    resume_payload = payload["resumeBinding"].model_dump(by_alias=True, mode="json")
    resume_payload[field] = value
    resume = AuthorityResumeBinding.model_validate(resume_payload)
    authority = _authority(request, resume)

    with pytest.raises(ValidationError, match="authenticatedActorId|runId"):
        UserApprovalConsumption.model_validate(
            {
                **payload,
                "resumeBinding": resume,
                "resumeBindingDigest": canonical_authority_resume_binding_digest(resume),
                "verifiedResumeBinding": _verified_resume(request, resume),
                "authorityContract": authority,
                "authorityContractDigest": canonical_authority_contract_digest(authority),
                "preparation": _preparation(request, authority),
            }
        )


def test_user_decision_record_event_payload_commits_request_receipt_and_nonce() -> None:
    request = _request()
    receipt = _receipt(request)
    recording = _approval_recording(request, receipt)
    payload = recording.model_dump(by_alias=True, mode="json")
    payload["recordedEvent"] = _event(
        "user_decision.recorded",
        request,
        event_id="event_approval_recorded",
        causation_id=request.pending_event_id,
        payload={},
    ).model_dump(by_alias=True, mode="json")
    with pytest.raises(ValidationError, match="recordedEvent payload"):
        UserDecisionRecording.model_validate(payload)


def test_execution_preparation_rejects_digest_transplant_and_context_drift() -> None:
    request = _request()
    resume = _resume(request)
    authority = _authority(request, resume)
    preparation = _preparation(request, authority)

    transplanted = preparation.model_dump(by_alias=True, mode="json")
    transplanted["actionIntentDigest"] = D9
    with pytest.raises(ValidationError, match="actionIntentDigest"):
        ExecutionPreparation.model_validate(transplanted)

    drifted = preparation.model_dump(by_alias=True, mode="json")
    drifted["preparedEvent"]["correlationId"] = "run_other"
    with pytest.raises(ValidationError, match="correlationId"):
        ExecutionPreparation.model_validate(drifted)


def test_execution_start_binds_recorded_preparation_executor_and_token_digest() -> None:
    consumption = UserApprovalConsumption.model_validate(_consumption_payload())
    request = consumption.approved_snapshot.request
    authority = consumption.authority_contract
    preparation = consumption.preparation
    payload = {
        "actionIntentDigest": preparation.action_intent_digest,
        "authorityContractDigest": preparation.authority_contract_digest,
        "preparedEventId": preparation.prepared_event.event_id,
        "preparedEventSequence": preparation.prepared_event.sequence,
        "preparedEventHash": preparation.prepared_event.event_hash,
        "approvalConsumedEventId": consumption.consumed_event.event_id,
        "approvalConsumedEventSequence": consumption.consumed_event.sequence,
        "approvalConsumedEventHash": consumption.consumed_event.event_hash,
        "executorId": "executor_01",
        "executorVersion": "1.0.0",
        "sandboxProfileDigest": authority.sandbox_profile_digest,
        "providerId": None,
        "providerVersion": None,
        "providerCapabilitiesDigest": None,
        "executionGrantDigest": D8,
    }
    start = ExecutionStart(
        schemaVersion=1,
        preparation=preparation,
        approvalConsumption=consumption,
        actionId=request.action_id,
        attemptId=authority.attempt_id,
        partitionId=request.authority_partition_id,
        taskContractDigest=request.task_contract_digest,
        actionIntentDigest=preparation.action_intent_digest,
        requestDigest=request.normalized_request_digest,
        authorityContractId=authority.authority_contract_id,
        authorityContractDigest=preparation.authority_contract_digest,
        fencingToken=authority.fencing_token,
        executorId="executor_01",
        executorVersion="1.0.0",
        sandboxProfileDigest=authority.sandbox_profile_digest,
        providerId=None,
        providerVersion=None,
        providerCapabilitiesDigest=None,
        executionTokenDigest=D8,
        actionCompareVersion=4,
        attemptCompareVersion=5,
        partitionCompareVersion=6,
        executingEvent=_event(
            "action.executing",
            request,
            event_id="event_executing",
            attempt_id=authority.attempt_id,
            authority_contract_id=authority.authority_contract_id,
            causation_id=consumption.consumed_event.event_id,
            payload=payload,
            sequence=5,
            previous_hash=consumption.consumed_event.event_hash,
            event_hash=D5,
            row_checksum=D6,
            created_at=NOW + timedelta(seconds=7),
        ),
    )
    assert start.preparation == preparation

    drifted = start.model_dump(by_alias=True, mode="json")
    drifted["executingEvent"]["payloadJson"] = drifted["executingEvent"]["payloadJson"].replace(
        "executor_01", "executor_02"
    )
    drifted["executingEvent"]["payloadDigest"] = (
        "sha256:" + sha256(drifted["executingEvent"]["payloadJson"].encode()).hexdigest()
    )
    with pytest.raises(ValidationError, match="payload"):
        ExecutionStart.model_validate(drifted)


def test_execution_start_without_approval_directly_follows_prepared_event() -> None:
    request = _request()
    authority = _authority(request, None)
    preparation = _preparation(request, authority)
    payload = {
        "actionIntentDigest": preparation.action_intent_digest,
        "authorityContractDigest": preparation.authority_contract_digest,
        "preparedEventId": preparation.prepared_event.event_id,
        "preparedEventSequence": preparation.prepared_event.sequence,
        "preparedEventHash": preparation.prepared_event.event_hash,
        "executorId": "executor_01",
        "executorVersion": "1.0.0",
        "sandboxProfileDigest": authority.sandbox_profile_digest,
        "providerId": None,
        "providerVersion": None,
        "providerCapabilitiesDigest": None,
        "executionGrantDigest": D8,
    }
    executing_event = _event(
        "action.executing",
        request,
        event_id="event_executing",
        attempt_id=authority.attempt_id,
        authority_contract_id=authority.authority_contract_id,
        causation_id=preparation.prepared_event.event_id,
        payload=payload,
        sequence=3,
        previous_hash=preparation.prepared_event.event_hash,
        event_hash=D3,
        row_checksum=D4,
        created_at=NOW + timedelta(seconds=3),
    )

    start = ExecutionStart(
        schemaVersion=1,
        preparation=preparation,
        actionId=request.action_id,
        attemptId=authority.attempt_id,
        partitionId=request.authority_partition_id,
        taskContractDigest=request.task_contract_digest,
        actionIntentDigest=preparation.action_intent_digest,
        requestDigest=request.normalized_request_digest,
        authorityContractId=authority.authority_contract_id,
        authorityContractDigest=preparation.authority_contract_digest,
        fencingToken=authority.fencing_token,
        executorId="executor_01",
        executorVersion="1.0.0",
        sandboxProfileDigest=authority.sandbox_profile_digest,
        providerId=None,
        providerVersion=None,
        providerCapabilitiesDigest=None,
        executionTokenDigest=D8,
        actionCompareVersion=4,
        attemptCompareVersion=5,
        partitionCompareVersion=6,
        executingEvent=executing_event,
    )

    assert start.approval_consumption is None

    skipped = start.model_dump(by_alias=True, mode="json")
    skipped["executingEvent"]["previousHash"] = D1
    with pytest.raises(ValidationError, match="previousHash"):
        ExecutionStart.model_validate(skipped)


def test_invalidation_transition_preserves_pending_snapshot_without_inventing_receipts() -> None:
    request = _request()
    previous = _snapshot(
        request,
        state="pending",
        approval_receipt_digest=None,
        latest_receipt_id=None,
        latest_receipt_digest=None,
        compare_version=0,
    )
    current = _snapshot(
        request,
        state="invalidated",
        approval_receipt_digest=None,
        latest_receipt_id=None,
        latest_receipt_digest=None,
        compare_version=1,
    )
    transition_request = UserDecisionInvalidationRequest(
        schemaVersion=1,
        decisionRequestId=request.decision_request_id,
        taskContractDigest=request.task_contract_digest,
        actionId=request.action_id,
        partitionId=request.authority_partition_id,
        expectedDecisionCompareVersion=0,
        expectedActionCompareVersion=2,
        expectedPartitionCompareVersion=3,
        invalidatedBindingKind="policy",
        previousBindingDigest=D1,
        currentBindingDigest=D2,
        reasonCodes=("policy_changed",),
    )
    resolution = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId=request.action_id,
        taskContractDigest=request.task_contract_digest,
        sourceAttemptIds=("attempt_01",),
        resolutionAttemptId=None,
        logicalState=ActionState.DENIED,
        reasonCodes=("policy_changed",),
    )

    transition = UserDecisionTransition(
        schemaVersion=1,
        request=transition_request,
        fromState="pending",
        toState="invalidated",
        previousSnapshot=previous,
        currentSnapshot=current,
        decisionCompareVersion=1,
        actionCompareVersion=3,
        partitionCompareVersion=4,
        transitionEvent=_event(
            "user_decision.invalidated",
            request,
            event_id="event_decision_invalidated",
            payload=_transition_payload(
                transition_request,
                from_state="pending",
                to_state="invalidated",
                previous=previous,
                current=current,
                resolution=resolution,
            ),
        ),
        actionResolution=resolution,
        deniedEvent=_event(
            "action.denied",
            request,
            event_id="event_action_denied",
            causation_id="event_decision_invalidated",
            payload={
                "actionResolutionDigest": _model_digest(resolution),
                "transitionEventId": "event_decision_invalidated",
                "transitionRequestDigest": _model_digest(transition_request),
            },
            sequence=2,
            previous_hash=D1,
            event_hash=D2,
            row_checksum=D3,
        ),
        resolutionEvent=_event(
            "action.resolved",
            request,
            event_id="event_action_resolved",
            causation_id="event_action_denied",
            payload={
                "actionResolutionDigest": _model_digest(resolution),
                "deniedEventId": "event_action_denied",
                "logicalState": "denied",
            },
            sequence=3,
            previous_hash=D2,
            event_hash=D3,
            row_checksum=D4,
        ),
    )

    assert transition.current_snapshot.approval_receipt_digest is None
    unchained = transition.model_dump(by_alias=True, mode="json")
    unchained["deniedEvent"]["sequence"] = transition.transition_event.sequence
    unchained["deniedEvent"]["previousHash"] = D0
    with pytest.raises(ValidationError, match="directly follow"):
        UserDecisionTransition.model_validate(unchained)
