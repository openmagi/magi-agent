from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.envelopes import (
    PartitionRecoveryPlan,
    RecoveryDecision,
    JournalEvent,
    LeaseSnapshot,
    WorkspaceCommitDecision,
    WorkspaceCommitDecisionRequest,
    WorkspaceCommitSnapshot,
    WorkspaceCommitRecoveryClaimRequest,
    WorkspaceCommitRecoveryClaim,
    WorkspaceSnapshot,
    WorkspacePublicationObservation,
    WorkspacePublicationReceipt,
    WorkspaceQuarantineReceipt,
    WorkspaceTransactionRequest,
    WorkspaceTransactionResult,
    canonical_resource_refs_digest,
    canonical_workspace_commit_decision_digest,
    canonical_workspace_transaction_result_digest,
    canonical_workspace_view_binding_digest,
    _draft_lifecycle_journal_event,
)
from magi_agent.execution_authority.state_machine import (
    ActionState,
    LeaseState,
    RecoveryDisposition,
)


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
NOW = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)
WORKSPACE_ROOT_REF = f"workspace://{D0}/"
WORKSPACE_A_REF = WORKSPACE_ROOT_REF + "a.txt"


def _stored_event(
    event_type: str,
    *,
    event_id: str,
    payload: dict[str, object],
    sequence: int,
    previous_hash: str,
    event_hash: str,
    causation_id: str,
    fencing_token: int = 7,
    actor_id: str = "actor_01",
    action_id: str | None = "act_01",
    attempt_id: str | None = "try_01",
    created_at: datetime = NOW,
) -> JournalEvent:
    draft = _draft_lifecycle_journal_event(
        event_id=event_id,
        partition_id="workspace_01",
        event_type=event_type,
        action_id=action_id,
        attempt_id=attempt_id,
        task_contract_id="task_01",
        task_version=3,
        task_contract_digest=D2,
        completion_epoch_id="epoch_01",
        admission_sequence=5,
        authority_contract_id="authority_01",
        request_digest=D1,
        idempotency_key_digest=D2,
        fencing_token=fencing_token,
        actor_id=actor_id,
        policy_digest=D1,
        causation_id=causation_id,
        correlation_id="run_01",
        identity_digest=D2,
        payload=payload,
    )
    return JournalEvent(
        **draft.model_dump(by_alias=True),
        sequence=sequence,
        previousHash=previous_hash,
        eventHash=event_hash,
        rowChecksum=D1,
        createdAt=created_at,
    )


def _workspace_transaction_result() -> WorkspaceTransactionResult:
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_ROOT_REF,
        authority_partition_id="workspace_01",
        generation=1,
        state_root=D1,
    )
    request = WorkspaceTransactionRequest(
        schemaVersion=1,
        transactionId="txn_01",
        workspaceRef=WORKSPACE_ROOT_REF,
        authorityPartitionId="workspace_01",
        actionId="act_01",
        attemptId="try_01",
        stagingManifestRef=f"authority-manifest://{D2}",
        stagingManifestDigest=D2,
        changedResourceRefsDigest=canonical_resource_refs_digest((WORKSPACE_A_REF,)),
        workspaceViewBindingDigest=view_digest,
    )
    request_json = json.dumps(
        request.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    transaction_request_digest = "sha256:" + sha256(request_json.encode()).hexdigest()
    payload = {
        "actionId": "act_01",
        "attemptId": "try_01",
        "authorityContractDigest": D1,
        "authorityPartitionId": "workspace_01",
        "changedResourceRefsDigest": request.changed_resource_refs_digest,
        "expectedGeneration": 1,
        "mutationPlanDigest": D2,
        "stagingManifestDigest": D2,
        "stateRootAfter": D2,
        "stateRootBefore": D1,
        "targetGeneration": 2,
        "transactionId": "txn_01",
        "transactionRequestDigest": transaction_request_digest,
        "transactionVersion": 1,
        "workspaceId": "workspace_01",
        "workspaceViewBindingDigest": view_digest,
    }
    staged_event = _stored_event(
        "workspace.transaction_staged",
        event_id="event_staged",
        payload=payload,
        sequence=1,
        previous_hash=D0,
        event_hash=D1,
        causation_id="event_prepared",
    )
    return WorkspaceTransactionResult(
        schemaId="magi.workspace_transaction_result.v1",
        request=request,
        workspaceId="workspace_01",
        expectedGeneration=1,
        targetGeneration=2,
        stateRootBefore=D1,
        stateRootAfter=D2,
        mutationPlanDigest=D2,
        changedResourceRefs=(WORKSPACE_A_REF,),
        taskContractId="task_01",
        taskVersion=3,
        taskContractDigest=D2,
        completionEpochId="epoch_01",
        admissionSequence=5,
        authorityContractId="authority_01",
        authorityContractDigest=D1,
        transactionCompareVersion=1,
        stagedEvent=staged_event,
    )


def _workspace_commit_decision() -> WorkspaceCommitDecision:
    staged = _workspace_transaction_result()
    request = WorkspaceCommitDecisionRequest(
        schemaId="magi.workspace_commit_decision_request.v1",
        commitId="commit_01",
        transactionId=staged.request.transaction_id,
        workspaceId=staged.workspace_id,
        workspaceRef=staged.request.workspace_ref,
        authorityPartitionId=staged.request.authority_partition_id,
        actionId=staged.request.action_id,
        attemptId=staged.request.attempt_id,
        expectedGeneration=staged.expected_generation,
        targetGeneration=staged.target_generation,
        expectedWorkspaceCompareVersion=3,
        expectedTransactionCompareVersion=staged.transaction_compare_version,
        stagedTransactionDigest=canonical_workspace_transaction_result_digest(staged),
        stateRootBefore=staged.state_root_before,
        stateRootAfter=staged.state_root_after,
        decisionFencingToken=7,
        mutationPlanDigest=staged.mutation_plan_digest,
        stagingManifestRef=staged.request.staging_manifest_ref,
        stagingManifestDigest=staged.request.staging_manifest_digest,
        changedResourceRefsDigest=staged.request.changed_resource_refs_digest,
        workspaceViewBindingDigest=staged.request.workspace_view_binding_digest,
        changedResourceRefs=staged.changed_resource_refs,
    )
    request_json = json.dumps(
        request.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    commit_event = _stored_event(
        "workspace.commit_decided",
        event_id="event_commit_decided",
        payload={
            "actionId": request.action_id,
            "attemptId": request.attempt_id,
            "authorityPartitionId": request.authority_partition_id,
            "changedResourceRefsDigest": request.changed_resource_refs_digest,
            "commitId": request.commit_id,
            "decisionFence": request.decision_fencing_token,
            "expectedGeneration": request.expected_generation,
            "expectedTransactionCompareVersion": request.expected_transaction_compare_version,
            "expectedWorkspaceCompareVersion": request.expected_workspace_compare_version,
            "mutationPlanDigest": request.mutation_plan_digest,
            "requestDigest": "sha256:" + sha256(request_json.encode()).hexdigest(),
            "stagedTransactionDigest": request.staged_transaction_digest,
            "stagingManifestDigest": request.staging_manifest_digest,
            "stateRootAfter": request.state_root_after,
            "stateRootBefore": request.state_root_before,
            "targetGeneration": request.target_generation,
            "transactionId": request.transaction_id,
            "workspaceId": request.workspace_id,
            "workspaceViewBindingDigest": request.workspace_view_binding_digest,
        },
        sequence=2,
        previous_hash=staged.staged_event.event_hash,
        event_hash=D2,
        causation_id=staged.staged_event.event_id,
        created_at=NOW + timedelta(seconds=1),
    )
    snapshot = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=request,
        state="decided",
        activeFencingToken=7,
        activeFenceEventId=commit_event.event_id,
        activeFenceEventSequence=commit_event.sequence,
        activeFenceEventHash=commit_event.event_hash,
        commitCompareVersion=1,
    )
    return WorkspaceCommitDecision(
        schemaVersion=1,
        snapshot=snapshot,
        stagedTransaction=staged,
        workspaceCompareVersion=4,
        commitEvent=commit_event,
    )


def _workspace_publication_observation() -> WorkspacePublicationObservation:
    decision = _workspace_commit_decision()
    request = decision.snapshot.request
    return WorkspacePublicationObservation(
        schemaVersion=1,
        commitDecision=decision,
        commitId=request.commit_id,
        transactionId=request.transaction_id,
        workspaceId=request.workspace_id,
        workspaceRef=request.workspace_ref,
        authorityPartitionId=request.authority_partition_id,
        actionId=request.action_id,
        attemptId=request.attempt_id,
        expectedWorkspaceCompareVersion=decision.workspace_compare_version,
        expectedCommitCompareVersion=decision.snapshot.commit_compare_version,
        activeFencingToken=decision.snapshot.active_fencing_token,
        activeFenceEventId=decision.commit_event.event_id,
        publishedGeneration=request.target_generation,
        stateRootBefore=request.state_root_before,
        stateRootAfter=request.state_root_after,
        changedResourceRefs=request.changed_resource_refs,
        changedResourceRefsDigest=request.changed_resource_refs_digest,
        workspaceViewBindingDigest=canonical_workspace_view_binding_digest(
            workspace_id=request.workspace_id,
            workspace_ref=request.workspace_ref,
            authority_partition_id=request.authority_partition_id,
            generation=request.target_generation,
            state_root=request.state_root_after,
        ),
        durabilityEvidenceDigest=D5,
        observationRefs=("fsync://commit_01",),
    )


def _workspace_recovery_claim() -> WorkspaceCommitRecoveryClaim:
    original = _workspace_commit_decision()
    plan = PartitionRecoveryPlan(
        schemaVersion=1,
        recoveryEpochId="recovery_epoch_01",
        partitionId="workspace_01",
        taskContractDigest=D2,
        selectedSourceAttemptIds=("try_01",),
        requiredProjections=(),
    )
    decision = RecoveryDecision(
        schemaId="magi.recovery_decision.v1",
        decisionId="recovery:recovery_epoch_01:act_01:try_01",
        recoveryEpochId=plan.recovery_epoch_id,
        recoveryPlanDigest=plan.recovery_plan_digest,
        recoveryOwnerId="recovery_01",
        recoveryLeaseName="partition-recovery",
        partitionId="workspace_01",
        expectedPartitionCompareVersion=8,
        recoveryFencingToken=11,
        actionId="act_01",
        expectedActionCompareVersion=5,
        taskContractDigest=D2,
        sourceAttemptId="try_01",
        expectedSourceState=ActionState.UNKNOWN,
        expectedSourceVersion=4,
        sourceTerminal=True,
        terminalizeSourceTo=None,
        resolutionAttemptId="try_recovery_01",
        disposition=RecoveryDisposition.REDO_COMMIT,
        contextDigest=D4,
        nonExecutionProofDigest=None,
        reasonCodes=("redo_durable_commit",),
    )
    lease = LeaseSnapshot(
        schemaVersion=1,
        partitionId="workspace_01",
        leaseName="partition-recovery",
        state=LeaseState.HELD,
        ownerId="recovery_01",
        fencingToken=11,
        highWaterFencingToken=11,
        expiresAt=NOW + timedelta(minutes=5),
        compareVersion=9,
    )
    request = WorkspaceCommitRecoveryClaimRequest(
        schemaId="magi.workspace_commit_recovery_claim_request.v1",
        claimId="claim_commit_01_fence_11",
        commitId="commit_01",
        workspaceId="workspace_01",
        authorityPartitionId="workspace_01",
        recoveryOwnerId="recovery_01",
        expectedWorkspaceCompareVersion=4,
        expectedCommitCompareVersion=1,
        expectedActiveFencingToken=7,
        expectedActiveFenceEventId=original.commit_event.event_id,
        expectedActiveFenceEventSequence=original.commit_event.sequence,
        expectedActiveFenceEventHash=original.commit_event.event_hash,
        newFencingToken=11,
        workspaceViewBindingDigest=original.snapshot.request.workspace_view_binding_digest,
        recoveryDecision=decision,
        recoveryPlan=plan,
        recoveryLease=lease,
    )
    payload = {
        "actionId": "act_01",
        "activeFence": 11,
        "attemptId": "try_01",
        "authorityPartitionId": "workspace_01",
        "claimId": request.claim_id,
        "commitId": request.commit_id,
        "expectedActiveFenceEventHash": original.commit_event.event_hash,
        "expectedActiveFenceEventId": original.commit_event.event_id,
        "expectedActiveFenceEventSequence": original.commit_event.sequence,
        "expectedCommitCompareVersion": 1,
        "expectedWorkspaceCompareVersion": 4,
        "priorActiveFence": 7,
        "recoveryDecisionDigest": decision.decision_digest,
        "recoveryEpochId": decision.recovery_epoch_id,
        "recoveryLeaseCompareVersion": lease.compare_version,
        "recoveryLeaseExpiresAt": lease.expires_at.isoformat(),
        "recoveryOwnerId": "recovery_01",
        "recoveryPlanDigest": plan.recovery_plan_digest,
        "resolutionAttemptId": "try_recovery_01",
        "sourceAttemptId": "try_01",
        "stateRootAfter": D2,
        "stateRootBefore": D1,
        "targetGeneration": 2,
        "transactionId": "txn_01",
        "workspaceId": "workspace_01",
        "workspaceViewBindingDigest": original.snapshot.request.workspace_view_binding_digest,
    }
    claim_event = _stored_event(
        "workspace.commit_recovery_claimed",
        event_id="event_commit_recovery_claimed",
        payload=payload,
        sequence=original.commit_event.sequence + 1,
        previous_hash=original.commit_event.event_hash,
        event_hash=D3,
        causation_id=original.commit_event.event_id,
        fencing_token=11,
        actor_id="recovery_01",
        attempt_id="try_recovery_01",
        created_at=NOW + timedelta(seconds=2),
    )
    snapshot = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=original.snapshot.request,
        state="decided",
        activeFencingToken=11,
        activeFenceEventId=claim_event.event_id,
        activeFenceEventSequence=claim_event.sequence,
        activeFenceEventHash=claim_event.event_hash,
        commitCompareVersion=2,
    )
    return WorkspaceCommitRecoveryClaim(
        schemaVersion=1,
        request=request,
        originalDecision=original,
        priorSnapshot=original.snapshot,
        priorFenceEvent=original.commit_event,
        snapshot=snapshot,
        workspaceCompareVersion=5,
        claimEvent=claim_event,
    )


def test_held_lease_fence_is_the_persisted_high_water_mark() -> None:
    with pytest.raises(ValidationError, match="highWaterFencingToken"):
        LeaseSnapshot(
            schemaVersion=1,
            partitionId="workspace_01",
            leaseName="partition-recovery",
            state=LeaseState.HELD,
            ownerId="recovery_01",
            fencingToken=7,
            highWaterFencingToken=8,
            expiresAt=NOW + timedelta(minutes=5),
            compareVersion=4,
        )


def test_workspace_snapshots_require_a_canonical_physical_root_ref() -> None:
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref="workspace://root",
        authority_partition_id="workspace_01",
        generation=1,
        state_root=D1,
    )

    with pytest.raises(ValidationError, match="canonical workspace root"):
        WorkspaceSnapshot(
            schemaVersion=1,
            workspaceId="workspace_01",
            workspaceRef="workspace://root",
            authorityPartitionId="workspace_01",
            currentGeneration=1,
            stateRoot=D1,
            workspaceViewBindingDigest=view_digest,
            publicationState="ready",
            compareVersion=3,
        )


def test_workspace_transactions_reject_a_resource_ref_as_the_workspace_root() -> None:
    with pytest.raises(ValidationError, match="canonical workspace root"):
        WorkspaceTransactionRequest(
            schemaVersion=1,
            transactionId="txn_01",
            workspaceRef=WORKSPACE_A_REF,
            authorityPartitionId="workspace_01",
            actionId="act_01",
            attemptId="try_01",
            stagingManifestRef=f"authority-manifest://{D1}",
            stagingManifestDigest=D1,
            changedResourceRefsDigest=D2,
            workspaceViewBindingDigest=D2,
        )


def test_workspace_commit_changed_resources_must_share_the_workspace_root() -> None:
    other_resource_ref = f"workspace://{'sha256:' + '9' * 64}/other.txt"
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_ROOT_REF,
        authority_partition_id="workspace_01",
        generation=1,
        state_root=D1,
    )

    with pytest.raises(ValidationError, match="share the workspaceRef root"):
        WorkspaceCommitDecisionRequest(
            schemaId="magi.workspace_commit_decision_request.v1",
            commitId="commit_01",
            transactionId="txn_01",
            workspaceId="workspace_01",
            workspaceRef=WORKSPACE_ROOT_REF,
            authorityPartitionId="workspace_01",
            actionId="act_01",
            attemptId="try_01",
            expectedGeneration=1,
            targetGeneration=2,
            expectedWorkspaceCompareVersion=3,
            expectedTransactionCompareVersion=1,
            stagedTransactionDigest=D2,
            stateRootBefore=D1,
            stateRootAfter=D2,
            decisionFencingToken=7,
            mutationPlanDigest=D2,
            stagingManifestRef=f"authority-manifest://{D1}",
            stagingManifestDigest=D1,
            changedResourceRefsDigest=canonical_resource_refs_digest(
                (other_resource_ref,)
            ),
            workspaceViewBindingDigest=view_digest,
            changedResourceRefs=(other_resource_ref,),
        )


def test_workspace_commit_decision_requires_the_staged_transaction_result() -> None:
    assert "staged_transaction_digest" in WorkspaceCommitDecisionRequest.model_fields
    assert "staged_transaction" in WorkspaceCommitDecision.model_fields


def test_workspace_commit_decision_binds_staged_result_and_event_lineage() -> None:
    staged = _workspace_transaction_result()
    request = WorkspaceCommitDecisionRequest(
        schemaId="magi.workspace_commit_decision_request.v1",
        commitId="commit_01",
        transactionId=staged.request.transaction_id,
        workspaceId=staged.workspace_id,
        workspaceRef=staged.request.workspace_ref,
        authorityPartitionId=staged.request.authority_partition_id,
        actionId=staged.request.action_id,
        attemptId=staged.request.attempt_id,
        expectedGeneration=staged.expected_generation,
        targetGeneration=staged.target_generation,
        expectedWorkspaceCompareVersion=3,
        expectedTransactionCompareVersion=staged.transaction_compare_version,
        stagedTransactionDigest=canonical_workspace_transaction_result_digest(staged),
        stateRootBefore=staged.state_root_before,
        stateRootAfter=staged.state_root_after,
        decisionFencingToken=7,
        mutationPlanDigest=staged.mutation_plan_digest,
        stagingManifestRef=staged.request.staging_manifest_ref,
        stagingManifestDigest=staged.request.staging_manifest_digest,
        changedResourceRefsDigest=staged.request.changed_resource_refs_digest,
        workspaceViewBindingDigest=staged.request.workspace_view_binding_digest,
        changedResourceRefs=staged.changed_resource_refs,
    )
    request_json = json.dumps(
        request.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    payload = {
        "actionId": request.action_id,
        "attemptId": request.attempt_id,
        "authorityPartitionId": request.authority_partition_id,
        "changedResourceRefsDigest": request.changed_resource_refs_digest,
        "commitId": request.commit_id,
        "decisionFence": request.decision_fencing_token,
        "expectedGeneration": request.expected_generation,
        "expectedTransactionCompareVersion": request.expected_transaction_compare_version,
        "expectedWorkspaceCompareVersion": request.expected_workspace_compare_version,
        "mutationPlanDigest": request.mutation_plan_digest,
        "requestDigest": "sha256:" + sha256(request_json.encode()).hexdigest(),
        "stagedTransactionDigest": request.staged_transaction_digest,
        "stagingManifestDigest": request.staging_manifest_digest,
        "stateRootAfter": request.state_root_after,
        "stateRootBefore": request.state_root_before,
        "targetGeneration": request.target_generation,
        "transactionId": request.transaction_id,
        "workspaceId": request.workspace_id,
        "workspaceViewBindingDigest": request.workspace_view_binding_digest,
    }
    commit_event = _stored_event(
        "workspace.commit_decided",
        event_id="event_commit_decided",
        payload=payload,
        sequence=2,
        previous_hash=staged.staged_event.event_hash,
        event_hash=D2,
        causation_id=staged.staged_event.event_id,
        created_at=NOW + timedelta(seconds=1),
    )
    snapshot = {
        "schemaVersion": 1,
        "request": request,
        "state": "decided",
        "activeFencingToken": 7,
        "activeFenceEventId": commit_event.event_id,
        "activeFenceEventSequence": commit_event.sequence,
        "activeFenceEventHash": commit_event.event_hash,
        "commitCompareVersion": 1,
    }
    decision = WorkspaceCommitDecision(
        schemaVersion=1,
        snapshot=snapshot,
        stagedTransaction=staged,
        workspaceCompareVersion=4,
        commitEvent=commit_event,
    )
    assert decision.staged_transaction.result_digest == request.staged_transaction_digest

    stale_lineage = decision.model_dump(by_alias=True, mode="json")
    stale_lineage["commitEvent"]["taskVersion"] = 4
    with pytest.raises(ValidationError, match="commitEvent.taskVersion"):
        WorkspaceCommitDecision.model_validate(stale_lineage)

    time_travel = decision.model_dump(by_alias=True, mode="json")
    time_travel["commitEvent"]["createdAt"] = (NOW - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError, match="createdAt cannot precede"):
        WorkspaceCommitDecision.model_validate(time_travel)


def test_workspace_recovery_claim_requires_decision_plan_and_lease_bindings() -> None:
    fields = WorkspaceCommitRecoveryClaimRequest.model_fields
    assert "recovery_decision" in fields
    assert "recovery_plan" in fields
    assert "recovery_lease" in fields


def test_workspace_recovery_claim_embeds_its_prior_fence_event() -> None:
    assert "prior_fence_event" in WorkspaceCommitRecoveryClaim.model_fields


def test_workspace_recovery_claim_binds_exact_decision_plan_lease_and_source() -> None:
    claim = _workspace_recovery_claim()
    assert claim.request.new_fencing_token == claim.request.recovery_lease.fencing_token
    assert (
        claim.request.recovery_plan.recovery_plan_digest
        == claim.request.recovery_decision.recovery_plan_digest
    )
    assert claim.claim_event.attempt_id == (
        claim.request.recovery_decision.resolution_attempt_id
    )

    wrong_plan = claim.request.model_dump(by_alias=True, mode="json")
    wrong_plan["recoveryDecision"].pop("decisionDigest")
    wrong_plan["recoveryDecision"]["recoveryPlanDigest"] = D9
    with pytest.raises(ValidationError, match="recoveryPlanDigest"):
        WorkspaceCommitRecoveryClaimRequest.model_validate(wrong_plan)

    expired_lease = claim.model_dump(by_alias=True, mode="json")
    expired_lease["request"]["recoveryLease"]["expiresAt"] = (
        NOW + timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValidationError, match="before the recovery lease expires"):
        WorkspaceCommitRecoveryClaim.model_validate(expired_lease)


def test_publication_observation_embeds_the_commit_authority_chain() -> None:
    fields = WorkspacePublicationObservation.model_fields
    assert "commit_decision" in fields
    assert "recovery_claim" in fields


def test_recovered_publication_derives_the_claim_fence_and_resolution_attempt() -> None:
    claim = _workspace_recovery_claim()
    request = claim.original_decision.snapshot.request
    observation = WorkspacePublicationObservation(
        schemaVersion=1,
        commitDecision=claim.original_decision,
        recoveryClaim=claim,
        commitId=request.commit_id,
        transactionId=request.transaction_id,
        workspaceId=request.workspace_id,
        workspaceRef=request.workspace_ref,
        authorityPartitionId=request.authority_partition_id,
        actionId=request.action_id,
        attemptId=claim.request.recovery_decision.resolution_attempt_id,
        expectedWorkspaceCompareVersion=claim.workspace_compare_version,
        expectedCommitCompareVersion=claim.snapshot.commit_compare_version,
        activeFencingToken=claim.snapshot.active_fencing_token,
        activeFenceEventId=claim.claim_event.event_id,
        publishedGeneration=request.target_generation,
        stateRootBefore=request.state_root_before,
        stateRootAfter=request.state_root_after,
        changedResourceRefs=request.changed_resource_refs,
        changedResourceRefsDigest=request.changed_resource_refs_digest,
        workspaceViewBindingDigest=canonical_workspace_view_binding_digest(
            workspace_id=request.workspace_id,
            workspace_ref=request.workspace_ref,
            authority_partition_id=request.authority_partition_id,
            generation=request.target_generation,
            state_root=request.state_root_after,
        ),
        durabilityEvidenceDigest=D5,
        observationRefs=("fsync://commit_01",),
    )
    assert observation.active_commit_snapshot == claim.snapshot
    assert observation.active_fencing_token > (
        observation.commit_decision.snapshot.active_fencing_token
    )

    stale_fence = observation.model_dump(by_alias=True, mode="json")
    stale_fence["activeFencingToken"] = 7
    with pytest.raises(ValidationError, match="activeFencingToken"):
        WorkspacePublicationObservation.model_validate(stale_fence)


def test_publication_receipt_embeds_resulting_workspace_and_commit_snapshots() -> None:
    fields = WorkspacePublicationReceipt.model_fields
    assert "workspace_snapshot" in fields
    assert "commit_snapshot" in fields


def test_publication_receipt_is_an_exact_successor_with_exact_cas_results() -> None:
    observation = _workspace_publication_observation()
    decision = observation.commit_decision
    event = _stored_event(
        "workspace.published",
        event_id="event_published",
        payload={
            "actionId": observation.action_id,
            "activeFence": observation.active_fencing_token,
            "activeFenceEventId": observation.active_fence_event_id,
            "attemptId": observation.attempt_id,
            "authorityPartitionId": observation.authority_partition_id,
            "changedResourceRefsDigest": observation.changed_resource_refs_digest,
            "commitCompareVersion": 2,
            "commitDecisionDigest": observation.commit_decision_digest,
            "commitId": observation.commit_id,
            "durabilityEvidenceDigest": observation.durability_evidence_digest,
            "expectedCommitCompareVersion": observation.expected_commit_compare_version,
            "expectedWorkspaceCompareVersion": observation.expected_workspace_compare_version,
            "observationRefs": list(observation.observation_refs),
            "publicationObservationDigest": observation.publication_observation_digest,
            "publishedGeneration": observation.published_generation,
            "recoveryClaimDigest": None,
            "stateRootAfter": observation.state_root_after,
            "stateRootBefore": observation.state_root_before,
            "transactionId": observation.transaction_id,
            "workspaceCompareVersion": 5,
            "workspaceId": observation.workspace_id,
            "workspaceRef": observation.workspace_ref,
            "workspaceViewBindingDigest": observation.workspace_view_binding_digest,
        },
        sequence=decision.commit_event.sequence + 1,
        previous_hash=decision.commit_event.event_hash,
        event_hash=D3,
        causation_id=decision.commit_event.event_id,
        created_at=NOW + timedelta(seconds=2),
    )
    workspace_snapshot = WorkspaceSnapshot(
        schemaVersion=1,
        workspaceId=observation.workspace_id,
        workspaceRef=observation.workspace_ref,
        authorityPartitionId=observation.authority_partition_id,
        currentGeneration=observation.published_generation,
        stateRoot=observation.state_root_after,
        workspaceViewBindingDigest=observation.workspace_view_binding_digest,
        publicationState="ready",
        compareVersion=5,
    )
    commit_snapshot = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=decision.snapshot.request,
        state="published",
        activeFencingToken=observation.active_fencing_token,
        activeFenceEventId=event.event_id,
        activeFenceEventSequence=event.sequence,
        activeFenceEventHash=event.event_hash,
        commitCompareVersion=2,
    )
    receipt = WorkspacePublicationReceipt(
        **observation.model_dump(by_alias=True),
        workspaceCompareVersion=5,
        commitCompareVersion=2,
        publicationEvent=event,
        workspaceSnapshot=workspace_snapshot,
        commitSnapshot=commit_snapshot,
    )
    assert receipt.workspace_snapshot.compare_version == (
        receipt.expected_workspace_compare_version + 1
    )

    extra_payload = receipt.model_dump(by_alias=True, mode="json")
    payload = json.loads(extra_payload["publicationEvent"]["payloadJson"])
    payload["untrustedExtra"] = D9
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    extra_payload["publicationEvent"]["payloadJson"] = payload_json
    extra_payload["publicationEvent"]["payloadDigest"] = (
        "sha256:" + sha256(payload_json.encode()).hexdigest()
    )
    with pytest.raises(ValidationError, match="exact publication receipt"):
        WorkspacePublicationReceipt.model_validate(extra_payload)

    cas_leap = receipt.model_dump(by_alias=True, mode="json")
    cas_leap["workspaceSnapshot"]["compareVersion"] = 6
    with pytest.raises(ValidationError, match="workspaceSnapshot.compareVersion"):
        WorkspacePublicationReceipt.model_validate(cas_leap)

    arbitrary_snapshot = observation.model_dump(by_alias=True, mode="json")
    arbitrary_snapshot["activeCommitSnapshot"]["activeFencingToken"] = 6
    with pytest.raises(ValidationError, match="activeCommitSnapshot"):
        WorkspacePublicationObservation.model_validate(arbitrary_snapshot)


def test_workspace_quarantine_always_embeds_prior_workspace_cas_and_event() -> None:
    fields = WorkspaceQuarantineReceipt.model_fields
    assert "prior_workspace_snapshot" in fields
    assert fields["expected_workspace_compare_version"].is_required()
    assert fields["quarantine_event"].is_required()


def test_workspace_only_quarantine_binds_prior_snapshot_exact_event_and_cas() -> None:
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_ROOT_REF,
        authority_partition_id="workspace_01",
        generation=1,
        state_root=D1,
    )
    prior = WorkspaceSnapshot(
        schemaVersion=1,
        workspaceId="workspace_01",
        workspaceRef=WORKSPACE_ROOT_REF,
        authorityPartitionId="workspace_01",
        currentGeneration=1,
        stateRoot=D1,
        workspaceViewBindingDigest=view_digest,
        publicationState="ready",
        compareVersion=3,
    )
    prior_json = json.dumps(
        prior.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    event = _stored_event(
        "workspace.quarantined",
        event_id="event_workspace_quarantined",
        payload={
            "expectedWorkspaceCompareVersion": 3,
            "priorGeneration": 1,
            "priorPublicationState": "ready",
            "priorStateRoot": D1,
            "priorWorkspaceSnapshotDigest": (
                "sha256:" + sha256(prior_json.encode()).hexdigest()
            ),
            "quarantinedAt": NOW.isoformat(),
            "reasonDigest": D6,
            "workspaceCompareVersion": 4,
            "workspaceId": "workspace_01",
            "workspaceRef": WORKSPACE_ROOT_REF,
        },
        sequence=4,
        previous_hash=D2,
        event_hash=D3,
        causation_id="workspace:workspace_01:v3",
        fencing_token=0,
        action_id=None,
        attempt_id=None,
    )
    receipt = WorkspaceQuarantineReceipt(
        schemaVersion=1,
        workspaceId="workspace_01",
        commitId=None,
        authorityPartitionId="workspace_01",
        reasonDigest=D6,
        fencingToken=0,
        quarantinedAt=NOW,
        expectedWorkspaceCompareVersion=3,
        priorWorkspaceSnapshot=prior,
        workspaceCompareVersion=4,
        quarantineEvent=event,
    )
    assert receipt.workspace_compare_version == prior.compare_version + 1

    stale_cas = receipt.model_dump(by_alias=True, mode="json")
    stale_cas["workspaceCompareVersion"] = 5
    with pytest.raises(ValidationError, match="workspaceCompareVersion"):
        WorkspaceQuarantineReceipt.model_validate(stale_cas)

    extra_payload = receipt.model_dump(by_alias=True, mode="json")
    payload = json.loads(extra_payload["quarantineEvent"]["payloadJson"])
    payload["untrustedExtra"] = True
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    extra_payload["quarantineEvent"]["payloadJson"] = payload_json
    extra_payload["quarantineEvent"]["payloadDigest"] = (
        "sha256:" + sha256(payload_json.encode()).hexdigest()
    )
    with pytest.raises(ValidationError, match="exact workspace quarantine"):
        WorkspaceQuarantineReceipt.model_validate(extra_payload)
