from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    canonical_authority_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionIntent,
    ActionSnapshot,
    AttemptSnapshot,
    EffectDeclarationBinding,
    IntegrityScanResult,
    JournalEvent,
    JournalHead,
    LeaseSnapshot,
    NonExecutionProof,
    PartitionGate,
    PartitionRecoveryPlan,
    ProjectionCursorSnapshot,
    RequiredProjection,
    WorkspaceCommitDecisionRequest,
    WorkspaceCommitSnapshot,
    WorkspaceSnapshot,
    canonical_action_intent_digest,
    canonical_provider_guarantees_digest,
    canonical_resource_refs_digest,
    canonical_workspace_view_binding_digest,
)
from magi_agent.execution_authority.recovery_protocol import (
    BeginRecoveryReceipt,
    OldExecutorFenceAcknowledgement,
    RecoveryAttemptIntent,
    RecoveryAuthorityBinding,
    RecoveryDecision,
    RecoveryDisposition,
    RecoveryExecutionGrant,
    RecoveryExecutionPreparation,
    RecoveryExecutionStart,
    RecoveryNonExecutionProof,
    RecoveryUserDecisionSnapshot,
    RecoveryWorkspaceState,
    ReplayCompleteRecoveryContext,
)
from magi_agent.execution_authority.state_machine import (
    ActionState,
    EffectClass,
    IdempotencyCapability,
    LeaseState,
    ProviderGuarantee,
    RecoveryStrategy,
    ResourceSemantics,
    TransmissionState,
    UserDecisionState,
    WorkspacePublicationState,
)


NOW = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
DIGESTS = tuple(f"sha256:{index:064x}" for index in range(32))
D0, D1, D2, D3, D4, D5, D6, D7, D8, D9 = DIGESTS[:10]
WORKSPACE_REF = "workspace://root"
RESOURCE_REF = f"workspace://sha256:{'a' * 64}/src/main.py"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _model_digest(value: object, *, exclude: set[str] | None = None) -> str:
    payload = value.model_dump(  # type: ignore[attr-defined]
        by_alias=True,
        mode="json",
        exclude=exclude or set(),
    )
    return "sha256:" + sha256(_canonical_json(payload).encode()).hexdigest()


def _workspace_snapshot(
    *,
    generation: int = 1,
    state_root: str = D1,
    publication_state: WorkspacePublicationState = WorkspacePublicationState.READY,
    commit_id: str | None = None,
    pending_generation: int | None = None,
    pending_state_root: str | None = None,
    active_fence: int | None = None,
    compare_version: int = 4,
) -> WorkspaceSnapshot:
    binding = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_REF,
        authority_partition_id="partition_01",
        generation=generation,
        state_root=state_root,
    )
    pending_binding = None
    if pending_generation is not None and pending_state_root is not None:
        pending_binding = canonical_workspace_view_binding_digest(
            workspace_id="workspace_01",
            workspace_ref=WORKSPACE_REF,
            authority_partition_id="partition_01",
            generation=pending_generation,
            state_root=pending_state_root,
        )
    return WorkspaceSnapshot(
        workspaceId="workspace_01",
        workspaceRef=WORKSPACE_REF,
        authorityPartitionId="partition_01",
        currentGeneration=generation,
        stateRoot=state_root,
        workspaceViewBindingDigest=binding,
        publicationState=publication_state,
        activeCommitId=commit_id,
        pendingGeneration=pending_generation,
        pendingStateRoot=pending_state_root,
        pendingWorkspaceViewBindingDigest=pending_binding,
        activeFencingToken=active_fence,
        compareVersion=compare_version,
    )


def _intent(
    strategy: RecoveryStrategy = RecoveryStrategy.READ_ONLY_REPLAY,
    *,
    workspace_view_binding_digest: str | None = None,
) -> ActionIntent:
    if strategy is RecoveryStrategy.PROVIDER_RECONCILIATION:
        effect_class = EffectClass.NETWORK_WRITE
        semantics = ResourceSemantics.REMOTE_EFFECT
        guarantee = ProviderGuarantee.RECONCILABLE
        idempotency = IdempotencyCapability.RECONCILIATION_ONLY
        resource_ref = "https://api.example.com/messages"
        capabilities = (
            AuthorityCapability(
                effectClass=effect_class,
                resourceRef=resource_ref,
                networkRefs=(resource_ref,),
                credentialRefs=(),
                workspaceViewBindingDigest=None,
            ),
        )
        read_set: tuple[str, ...] = ()
        write_set: tuple[str, ...] = ()
        egress_set = (resource_ref,)
        evidence = ("action_receipt",)
    elif strategy is RecoveryStrategy.WORKSPACE_TRANSACTION:
        effect_class = EffectClass.WORKSPACE_WRITE
        semantics = ResourceSemantics.WORKSPACE_TRANSACTION
        guarantee = ProviderGuarantee.LOCAL_ATOMIC
        idempotency = IdempotencyCapability.LOCAL_GENERATION_CAS
        capabilities = (
            AuthorityCapability(
                effectClass=effect_class,
                resourceRef=RESOURCE_REF,
                networkRefs=(),
                credentialRefs=(),
                workspaceViewBindingDigest=workspace_view_binding_digest,
            ),
        )
        read_set = (RESOURCE_REF,)
        write_set = (RESOURCE_REF,)
        egress_set = ()
        evidence = ("action_receipt", "workspace_postcondition")
    else:
        effect_class = EffectClass.WORKSPACE_READ
        semantics = ResourceSemantics.READ_ONLY
        guarantee = ProviderGuarantee.NONE
        idempotency = IdempotencyCapability.NONE
        capabilities = (
            AuthorityCapability(
                effectClass=effect_class,
                resourceRef=RESOURCE_REF,
                networkRefs=(),
                credentialRefs=(),
                workspaceViewBindingDigest=workspace_view_binding_digest,
            ),
        )
        read_set = (RESOURCE_REF,)
        write_set = ()
        egress_set = ()
        evidence = ()

    guarantees = (guarantee,)
    declaration = EffectDeclarationBinding(
        effectName="recovery-fixture",
        effectClass=effect_class,
        resourceSemantics=semantics,
        handlerDigest=D1,
        normalizerDigest=D2,
        resourceDeriverDigest=D3,
        executorDigest=D4,
        recoveryAdapterDigest=D5,
        providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
        providerGuarantees=guarantees,
        idempotencyCapability=idempotency,
        recoveryStrategy=strategy,
    )
    return ActionIntent(
        schemaId="magi.action_intent.v1",
        actionId="action_01",
        attemptId="attempt_source",
        partitionId="partition_01",
        actorId="actor_01",
        identityDigest=D6,
        policyDigest=D7,
        sessionId="session_01",
        turnId="turn_01",
        runId="run_01",
        taskContractId="task_01",
        taskVersion=1,
        taskContractDigest=D0,
        completionEpochId="epoch_01",
        declaration=declaration,
        capabilities=capabilities,
        normalizedInputDigest=D2,
        normalizedRequestSnapshotRef=f"authority-input://{D2}",
        readSet=read_set,
        absenceSet=(),
        writeSet=write_set,
        egressSet=egress_set,
        readSetDigest=canonical_resource_refs_digest(read_set),
        absenceSetDigest=canonical_resource_refs_digest(()),
        writeSetDigest=canonical_resource_refs_digest(write_set),
        egressSetDigest=canonical_resource_refs_digest(egress_set),
        workspaceViewBindingDigest=workspace_view_binding_digest,
        idempotencyKeyDigest=D8,
        evidenceObligations=evidence,
        compensatesActionId=None,
        admissionSequence=3,
    )


def _workspace_commit(
    source: WorkspaceSnapshot,
    *,
    state: str,
) -> WorkspaceCommitSnapshot:
    request = WorkspaceCommitDecisionRequest(
        transactionId="transaction_01",
        workspaceRef=source.workspace_ref,
        authorityPartitionId=source.authority_partition_id,
        actionId="action_01",
        attemptId="attempt_source",
        stagingManifestRef=f"authority-manifest://{D3}",
        stagingManifestDigest=D3,
        changedResourceRefsDigest=canonical_resource_refs_digest((RESOURCE_REF,)),
        workspaceViewBindingDigest=source.workspace_view_binding_digest,
        commitId="commit_01",
        workspaceId=source.workspace_id,
        expectedGeneration=source.current_generation,
        targetGeneration=source.current_generation + 1,
        expectedWorkspaceCompareVersion=source.compare_version,
        expectedTransactionCompareVersion=2,
        stateRootBefore=source.state_root,
        stateRootAfter=D2,
        decisionFencingToken=11,
        mutationPlanDigest=D4,
        changedResourceRefs=(RESOURCE_REF,),
    )
    return WorkspaceCommitSnapshot(
        request=request,
        state=state,
        activeFencingToken=11,
        activeFenceEventId="event_commit",
        activeFenceEventSequence=18,
        activeFenceEventHash=D4,
        commitCompareVersion=2 if state != "decided" else 1,
    )


def _authority(
    context: ReplayCompleteRecoveryContext, decision: RecoveryDecision
) -> AuthorityContract:
    intent = context.source_intent
    attempt_id = decision.resolution_attempt_id or intent.attempt_id
    return AuthorityContract(
        schemaVersion=1,
        authorityContractId=f"authority_{attempt_id}",
        issuerId="recovery-broker",
        principalId=intent.actor_id,
        tenantId="tenant_01",
        sessionId=intent.session_id,
        turnId=intent.turn_id,
        childActorId=None,
        taskContractId=intent.task_contract_id,
        taskVersion=intent.task_version,
        taskContractDigest=intent.task_contract_digest,
        completionEpochId=intent.completion_epoch_id,
        authorityPartitionId=intent.partition_id,
        actionId=intent.action_id,
        attemptId=attempt_id,
        policyDigest=intent.policy_digest,
        normalizedRequestDigest=intent.normalized_input_digest,
        commandDigest=None,
        argumentsDigest=D1,
        workingDirectoryDigest=D2,
        environmentDigest=D3,
        requestBodyDigest=None,
        credentialScopeDigest=None,
        networkDigest=None,
        disclosureDigest=D4,
        capabilities=intent.capabilities,
        workspaceViewBindingDigest=intent.workspace_view_binding_digest,
        sandboxProfileDigest=context.current_sandbox_profile_digest,
        guardianCeilingDigest=D5,
        expiresAt=NOW + timedelta(minutes=10),
        revokedAt=None,
        revocationDigest=None,
        fencingToken=context.lease.fencing_token,
        maximumUses=1,
        decisionRequestId=None,
        resumeBindingDigest=None,
        parentAuthorityDigest=None,
        delegationChain=(),
    )


def _event(
    *,
    event_id: str,
    event_type: str,
    attempt_id: str,
    request_digest: str,
    authority_contract_id: str,
    causation_id: str,
    payload: dict[str, object],
    sequence: int,
    previous_hash: str,
    event_hash: str,
    created_at: datetime,
) -> JournalEvent:
    payload_json = _canonical_json(payload)
    return JournalEvent(
        eventId=event_id,
        partitionId="partition_01",
        eventType=event_type,
        actionId="action_01",
        attemptId=attempt_id,
        taskContractId="task_01",
        taskVersion=1,
        taskContractDigest=D0,
        completionEpochId="epoch_01",
        admissionSequence=3,
        authorityContractId=authority_contract_id,
        requestDigest=request_digest,
        idempotencyKeyDigest=D8,
        fencingToken=11,
        actorId="actor_01",
        policyDigest=D7,
        causationId=causation_id,
        correlationId="run_01",
        identityDigest=D6,
        payloadDigest="sha256:" + sha256(payload_json.encode()).hexdigest(),
        payloadJson=payload_json,
        sequence=sequence,
        previousHash=previous_hash,
        eventHash=event_hash,
        rowChecksum=D9,
        createdAt=created_at,
    )


def _context(
    strategy: RecoveryStrategy = RecoveryStrategy.READ_ONLY_REPLAY,
    *,
    pending_decision: bool = False,
    lease_expires_at: datetime = NOW + timedelta(minutes=5),
    proof_observed_at: datetime = NOW,
    workspace_commit_state: str | None = None,
) -> ReplayCompleteRecoveryContext:
    source_workspace = _workspace_snapshot()
    workspace_view = (
        None
        if strategy is RecoveryStrategy.PROVIDER_RECONCILIATION
        else source_workspace.workspace_view_binding_digest
    )
    intent = _intent(strategy, workspace_view_binding_digest=workspace_view)
    intent_digest = canonical_action_intent_digest(intent)
    action = ActionSnapshot(
        actionId=intent.action_id,
        partitionId=intent.partition_id,
        taskContractDigest=intent.task_contract_digest,
        completionEpochId=intent.completion_epoch_id,
        admissionSequence=intent.admission_sequence,
        intentDigest=intent_digest,
        resolution=None,
        compareVersion=5,
    )
    attempt = AttemptSnapshot(
        actionId=intent.action_id,
        attemptId=intent.attempt_id,
        partitionId=intent.partition_id,
        taskContractDigest=intent.task_contract_digest,
        actionIntentDigest=intent_digest,
        requestDigest=intent.normalized_input_digest,
        state=ActionState.PREPARED,
        authorityDigest=D7,
        fencingToken=7,
        observation=None,
        verification=None,
        compareVersion=2,
    )
    required = (RequiredProjection(partitionId="partition_01", projectionId="actions"),)
    plan = PartitionRecoveryPlan(
        recoveryEpochId="recovery_epoch_01",
        partitionId=intent.partition_id,
        taskContractDigest=intent.task_contract_digest,
        selectedSourceAttemptIds=(intent.attempt_id,),
        requiredProjections=required,
    )
    gate = PartitionGate(
        partitionId=intent.partition_id,
        state="recovering",
        recoveryEpochId=plan.recovery_epoch_id,
        recoveryPlanDigest=plan.recovery_plan_digest,
        recoveryOwnerId="worker_01",
        recoveryFencingToken=11,
        quarantineReasonDigest=None,
        compareVersion=8,
    )
    lease = LeaseSnapshot(
        partitionId=intent.partition_id,
        leaseName="partition-recovery",
        state=LeaseState.HELD,
        ownerId="worker_01",
        fencingToken=11,
        highWaterFencingToken=11,
        expiresAt=lease_expires_at,
        compareVersion=4,
    )
    head = JournalHead(
        partitionId=intent.partition_id,
        sequence=20,
        eventHash=D5,
        compareVersion=6,
    )
    integrity = IntegrityScanResult(
        partitionId=intent.partition_id,
        status="clean",
        scannedThroughSequence=head.sequence,
        scannedHeadHash=head.event_hash,
        findingDigests=(),
        quarantined=False,
        scannedAt=NOW,
    )

    workspace: RecoveryWorkspaceState | None
    if strategy is RecoveryStrategy.PROVIDER_RECONCILIATION:
        workspace = None
        state_root = D1
    elif workspace_commit_state is None:
        workspace = RecoveryWorkspaceState(
            sourceSnapshot=source_workspace,
            currentSnapshot=source_workspace,
            commitSnapshot=None,
        )
        state_root = source_workspace.state_root
    else:
        commit = _workspace_commit(source_workspace, state=workspace_commit_state)
        if workspace_commit_state == "published":
            current = _workspace_snapshot(
                generation=2,
                state_root=D2,
                compare_version=6,
            )
        else:
            current = _workspace_snapshot(
                generation=1,
                state_root=D1,
                publication_state=WorkspacePublicationState.PUBLISHING,
                commit_id="commit_01",
                pending_generation=2,
                pending_state_root=D2,
                active_fence=11,
                compare_version=5,
            )
        workspace = RecoveryWorkspaceState(
            sourceSnapshot=source_workspace,
            currentSnapshot=current,
            commitSnapshot=commit,
        )
        state_root = current.state_root

    cursor = ProjectionCursorSnapshot(
        partitionId="partition_01",
        projectionId="actions",
        acknowledgedSequence=head.sequence,
        acknowledgedEventHash=head.event_hash,
        stateRoot=state_root,
        compareVersion=3,
    )
    user_decision = None
    if pending_decision:
        user_decision = RecoveryUserDecisionSnapshot(
            decisionRequestId="decision_request_01",
            decisionRequestDigest=D6,
            state=UserDecisionState.PENDING,
            compareVersion=1,
        )

    recovery_proof = None
    if workspace_commit_state is None:
        proof = NonExecutionProof(
            proofId="proof_01",
            partitionId=intent.partition_id,
            actionId=intent.action_id,
            sourceAttemptId=intent.attempt_id,
            expectedSourceState=attempt.state,
            expectedSourceVersion=attempt.compare_version,
            taskContractDigest=intent.task_contract_digest,
            authorityUseRecorded=True,
            preparedRecordRecorded=True,
            executionHandoffRecorded=False,
            providerTransmissionState=TransmissionState.PROVEN_NOT_SENT,
            visibleEffectsAbsent=True,
            evidenceId="evidence_non_execution_01",
            evidenceDigest=D1,
            coverageDigest=D2,
            actionSnapshotDigest=_model_digest(action),
            attemptSnapshotDigest=_model_digest(attempt),
            journalHeadDigest=_model_digest(head),
            producerId="executor-supervisor",
            producerVersion="1.0.0",
            producerSchemaVersion="1",
            producerInvocationEvidenceId="producer_invocation_01",
            producerInvocationEvidenceDigest=D3,
            producerAlive=True,
            observedAt=proof_observed_at,
        )
        acknowledgement = OldExecutorFenceAcknowledgement(
            acknowledgementId="old_executor_ack_01",
            sourceAttemptId=intent.attempt_id,
            oldExecutorId="executor_old",
            oldExecutorVersion="1.0.0",
            oldFencingToken=attempt.fencing_token,
            supersedingRecoveryFencingToken=lease.fencing_token,
            acknowledgementEvidenceDigest=D4,
            observedAt=proof_observed_at,
        )
        recovery_proof = RecoveryNonExecutionProof(
            proof=proof,
            oldExecutorFenceAcknowledgement=acknowledgement,
        )

    return ReplayCompleteRecoveryContext(
        contextId="context_01",
        evaluatedAt=NOW + timedelta(seconds=1),
        plan=plan,
        gate=gate,
        lease=lease,
        sourceIntent=intent,
        actionSnapshot=action,
        sourceAttemptSnapshot=attempt,
        journalHead=head,
        workspace=workspace,
        integrityScan=integrity,
        projectionCursors=(cursor,),
        userDecision=user_decision,
        nonExecutionProof=recovery_proof,
        currentPolicyDigest=intent.policy_digest,
        currentSandboxProfileDigest=D6,
    )


def _decision(
    context: ReplayCompleteRecoveryContext,
    disposition: RecoveryDisposition = RecoveryDisposition.REPLAY,
) -> RecoveryDecision:
    creates_attempt = disposition in {
        RecoveryDisposition.REPLAY,
        RecoveryDisposition.RECONCILE,
        RecoveryDisposition.REDO_COMMIT,
    }
    terminal_state = {
        RecoveryDisposition.ABORT: ActionState.ABORTED,
        RecoveryDisposition.REPLAY: ActionState.ABORTED,
        RecoveryDisposition.RECONCILE: ActionState.UNKNOWN,
        RecoveryDisposition.REDO_COMMIT: ActionState.UNKNOWN,
        RecoveryDisposition.CONFIRM_COMMIT: ActionState.COMMITTED,
    }.get(disposition)
    return RecoveryDecision(
        decisionId=(
            f"recovery:{context.plan.recovery_epoch_id}:"
            f"{context.source_intent.action_id}:{context.source_intent.attempt_id}"
        ),
        context=context,
        disposition=disposition,
        terminalizeSourceTo=terminal_state,
        resolutionAttemptId="attempt_resolution" if creates_attempt else None,
        reasonCodes=("recovery_policy",),
    )


def test_recovery_protocol_exposes_the_complete_p0_contract_surface() -> None:
    protocol = importlib.import_module("magi_agent.execution_authority.recovery_protocol")

    assert {
        "BeginRecoveryReceipt",
        "OldExecutorFenceAcknowledgement",
        "RecoveryAttemptIntent",
        "RecoveryAuthorityBinding",
        "RecoveryDecision",
        "RecoveryDisposition",
        "RecoveryExecutionGrant",
        "RecoveryExecutionPreparation",
        "RecoveryExecutionStart",
        "RecoveryNonExecutionProof",
        "RecoveryUserDecisionSnapshot",
        "RecoveryWorkspaceState",
        "ReplayCompleteRecoveryContext",
    }.issubset(dir(protocol))


def test_context_embeds_every_replay_input_and_derives_one_stable_digest() -> None:
    context = _context()

    assert context.plan.selected_source_attempt_ids == ("attempt_source",)
    assert context.gate.recovery_plan_digest == context.plan.recovery_plan_digest
    assert context.lease.owner_id == context.gate.recovery_owner_id
    assert context.action_snapshot.intent_digest == canonical_action_intent_digest(
        context.source_intent
    )
    assert context.source_attempt_snapshot.attempt_id == context.source_intent.attempt_id
    assert context.workspace is not None
    assert context.integrity_scan.scanned_head_hash == context.journal_head.event_hash
    assert context.projection_cursors[0].projection_id == "actions"
    assert context.context_digest == _model_digest(
        context,
        exclude={"context_digest"},
    )

    payload = context.model_dump(by_alias=True)
    payload["plan"] = D0
    with pytest.raises(ValidationError):
        ReplayCompleteRecoveryContext.model_validate(payload)


def test_non_execution_proof_requires_old_executor_fence_ack_and_cannot_look_ahead() -> None:
    context = _context()
    assert context.non_execution_proof is not None
    proof = context.non_execution_proof
    assert (
        proof.old_executor_fence_acknowledgement.old_fencing_token
        == context.source_attempt_snapshot.fencing_token
    )
    assert (
        proof.old_executor_fence_acknowledgement.superseding_recovery_fencing_token
        == context.lease.fencing_token
    )

    payload = proof.model_dump(by_alias=True)
    payload.pop("oldExecutorFenceAcknowledgement")
    with pytest.raises(ValidationError, match="oldExecutorFenceAcknowledgement"):
        RecoveryNonExecutionProof.model_validate(payload)

    with pytest.raises(ValidationError, match="observedAt|evaluatedAt"):
        _context(proof_observed_at=NOW + timedelta(minutes=1))


def test_pending_user_decision_cannot_be_laundered_through_reconciliation() -> None:
    context = _context(
        RecoveryStrategy.PROVIDER_RECONCILIATION,
        pending_decision=True,
    )

    with pytest.raises(ValidationError, match="pending user decision|RECONCILE"):
        _decision(context, RecoveryDisposition.RECONCILE)


@pytest.mark.parametrize("disposition", tuple(RecoveryDisposition))
def test_every_state_changing_disposition_requires_a_current_positive_fence(
    disposition: RecoveryDisposition,
) -> None:
    stale = _context(lease_expires_at=NOW)

    with pytest.raises(ValidationError, match="current positive recovery fence"):
        _decision(stale, disposition)


def test_published_workspace_crash_uses_explicit_confirm_commit_disposition() -> None:
    published = _context(
        RecoveryStrategy.WORKSPACE_TRANSACTION,
        workspace_commit_state="published",
    )
    decision = _decision(published, RecoveryDisposition.CONFIRM_COMMIT)

    assert decision.terminalize_source_to is ActionState.COMMITTED
    assert decision.resolution_attempt_id is None
    assert decision.decision_digest == _model_digest(
        decision,
        exclude={"decision_digest"},
    )

    with pytest.raises(ValidationError, match="published workspace commit"):
        _decision(_context(), RecoveryDisposition.CONFIRM_COMMIT)


def test_recovery_attempt_intent_preserves_logical_action_and_uses_resolution_attempt() -> None:
    context = _context()
    decision = _decision(context)
    attempt = RecoveryAttemptIntent(
        attemptIntentId="recovery_attempt_intent_01",
        sourceIntent=context.source_intent,
        context=context,
        decision=decision,
        actionId=context.source_intent.action_id,
        sourceAttemptId=context.source_intent.attempt_id,
        resolutionAttemptId=decision.resolution_attempt_id,
        partitionId=context.source_intent.partition_id,
        taskContractDigest=context.source_intent.task_contract_digest,
        normalizedRequestDigest=context.source_intent.normalized_input_digest,
        policyDigest=context.source_intent.policy_digest,
        identityDigest=context.source_intent.identity_digest,
        workspaceViewBindingDigest=context.source_intent.workspace_view_binding_digest,
    )

    assert attempt.action_id == context.source_intent.action_id
    assert attempt.source_attempt_id == context.source_intent.attempt_id
    assert attempt.resolution_attempt_id == "attempt_resolution"
    assert attempt.attempt_intent_digest == _model_digest(
        attempt,
        exclude={"attempt_intent_digest"},
    )

    payload = attempt.model_dump(by_alias=True)
    payload["resolutionAttemptId"] = context.source_intent.attempt_id
    with pytest.raises(ValidationError, match="resolutionAttemptId"):
        RecoveryAttemptIntent.model_validate(payload)


def _authority_binding(
    context: ReplayCompleteRecoveryContext,
    decision: RecoveryDecision,
) -> RecoveryAuthorityBinding:
    authority = _authority(context, decision)
    return RecoveryAuthorityBinding(
        bindingId="recovery_authority_binding_01",
        context=context,
        decision=decision,
        authorityContract=authority,
        authorityContractDigest=canonical_authority_contract_digest(authority),
        recoveryEpochId=context.plan.recovery_epoch_id,
        recoveryPlanDigest=context.plan.recovery_plan_digest,
        recoveryOwnerId=context.gate.recovery_owner_id,
        contextDigest=context.context_digest,
        decisionDigest=decision.decision_digest,
        boundAt=NOW + timedelta(seconds=2),
    )


def test_recovery_authority_binds_epoch_plan_owner_context_decision_and_target() -> None:
    context = _context()
    decision = _decision(context)
    binding = _authority_binding(context, decision)

    assert binding.authority_contract.attempt_id == decision.resolution_attempt_id
    assert binding.authority_contract.fencing_token == context.lease.fencing_token
    assert binding.binding_digest == _model_digest(
        binding,
        exclude={"binding_digest"},
    )

    payload = binding.model_dump(by_alias=True)
    payload["recoveryOwnerId"] = "attacker"
    with pytest.raises(ValidationError, match="recoveryOwnerId"):
        RecoveryAuthorityBinding.model_validate(payload)


def test_recovery_authority_cannot_be_bound_after_the_recovery_lease_expires() -> None:
    context = _context(lease_expires_at=NOW + timedelta(milliseconds=1500))
    decision = _decision(context)

    with pytest.raises(ValidationError, match="lease"):
        _authority_binding(context, decision)


def _attempt_intent(
    context: ReplayCompleteRecoveryContext,
    decision: RecoveryDecision,
) -> RecoveryAttemptIntent:
    intent = context.source_intent
    return RecoveryAttemptIntent(
        attemptIntentId="recovery_attempt_intent_01",
        sourceIntent=intent,
        context=context,
        decision=decision,
        actionId=intent.action_id,
        sourceAttemptId=intent.attempt_id,
        resolutionAttemptId=decision.resolution_attempt_id,
        partitionId=intent.partition_id,
        taskContractDigest=intent.task_contract_digest,
        normalizedRequestDigest=intent.normalized_input_digest,
        policyDigest=intent.policy_digest,
        identityDigest=intent.identity_digest,
        workspaceViewBindingDigest=intent.workspace_view_binding_digest,
    )


def _begin_receipt(
    context: ReplayCompleteRecoveryContext,
    decision: RecoveryDecision,
    binding: RecoveryAuthorityBinding,
) -> BeginRecoveryReceipt:
    payload = {
        "actionCompareVersion": 6,
        "authorityBindingDigest": binding.binding_digest,
        "contextDigest": context.context_digest,
        "decisionDigest": decision.decision_digest,
        "disposition": decision.disposition.value,
        "expectedActionCompareVersion": 5,
        "expectedPartitionCompareVersion": 8,
        "expectedSourceAttemptCompareVersion": 2,
        "partitionCompareVersion": 9,
        "recoveryEpochId": context.plan.recovery_epoch_id,
        "recoveryOwnerId": context.gate.recovery_owner_id,
        "recoveryPlanDigest": context.plan.recovery_plan_digest,
        "resolutionAttemptCompareVersion": 1,
        "resolutionAttemptId": decision.resolution_attempt_id,
        "sourceAttemptCompareVersion": 3,
        "sourceAttemptId": context.source_intent.attempt_id,
    }
    event = _event(
        event_id="event_recovery_begun",
        event_type="recovery.begun",
        attempt_id=context.source_intent.attempt_id,
        request_digest=decision.decision_digest,
        authority_contract_id=binding.authority_contract.authority_contract_id,
        causation_id=decision.decision_id,
        payload=payload,
        sequence=21,
        previous_hash=context.journal_head.event_hash,
        event_hash=D8,
        created_at=NOW + timedelta(seconds=3),
    )
    return BeginRecoveryReceipt(
        decision=decision,
        authorityBinding=binding,
        expectedActionCompareVersion=5,
        actionCompareVersion=6,
        expectedSourceAttemptCompareVersion=2,
        sourceAttemptCompareVersion=3,
        expectedPartitionCompareVersion=8,
        partitionCompareVersion=9,
        resolutionAttemptCompareVersion=1,
        recoveryEvent=event,
    )


def test_begin_recovery_receipt_proves_exact_cas_and_journal_append() -> None:
    context = _context()
    decision = _decision(context)
    binding = _authority_binding(context, decision)
    receipt = _begin_receipt(context, decision, binding)

    assert receipt.action_compare_version == receipt.expected_action_compare_version + 1
    assert receipt.source_attempt_compare_version == (
        receipt.expected_source_attempt_compare_version + 1
    )
    assert receipt.partition_compare_version == receipt.expected_partition_compare_version + 1
    assert receipt.recovery_event.sequence == context.journal_head.sequence + 1
    assert receipt.receipt_digest == _model_digest(
        receipt,
        exclude={"receipt_digest"},
    )

    payload = receipt.model_dump(by_alias=True)
    payload["partitionCompareVersion"] = 12
    with pytest.raises(ValidationError, match="partitionCompareVersion"):
        BeginRecoveryReceipt.model_validate(payload)

    payload = receipt.model_dump(by_alias=True)
    event_payload = json.loads(payload["recoveryEvent"]["payloadJson"])
    event_payload["contextDigest"] = D0
    payload_json = _canonical_json(event_payload)
    payload["recoveryEvent"]["payloadJson"] = payload_json
    payload["recoveryEvent"]["payloadDigest"] = (
        "sha256:" + sha256(payload_json.encode()).hexdigest()
    )
    with pytest.raises(ValidationError, match="payload"):
        BeginRecoveryReceipt.model_validate(payload)


def test_begin_recovery_event_cannot_land_after_the_recovery_lease_expires() -> None:
    context = _context(lease_expires_at=NOW + timedelta(milliseconds=2500))
    decision = _decision(context)
    binding = _authority_binding(context, decision)

    with pytest.raises(ValidationError, match="lease"):
        _begin_receipt(context, decision, binding)


def _preparation(
    context: ReplayCompleteRecoveryContext,
    decision: RecoveryDecision,
    binding: RecoveryAuthorityBinding,
    attempt_intent: RecoveryAttemptIntent,
    receipt: BeginRecoveryReceipt,
) -> RecoveryExecutionPreparation:
    payload = {
        "actionCompareVersion": 7,
        "attemptIntentDigest": attempt_intent.attempt_intent_digest,
        "authorityBindingDigest": binding.binding_digest,
        "beginReceiptDigest": receipt.receipt_digest,
        "contextDigest": context.context_digest,
        "decisionDigest": decision.decision_digest,
        "expectedActionCompareVersion": 6,
        "expectedPartitionCompareVersion": 9,
        "expectedResolutionAttemptCompareVersion": 1,
        "partitionCompareVersion": 10,
        "resolutionAttemptCompareVersion": 2,
    }
    event = _event(
        event_id="event_recovery_prepared",
        event_type="recovery.action_prepared",
        attempt_id=attempt_intent.resolution_attempt_id,
        request_digest=context.source_intent.normalized_input_digest,
        authority_contract_id=binding.authority_contract.authority_contract_id,
        causation_id=receipt.recovery_event.event_id,
        payload=payload,
        sequence=22,
        previous_hash=receipt.recovery_event.event_hash,
        event_hash=D9,
        created_at=NOW + timedelta(seconds=4),
    )
    return RecoveryExecutionPreparation(
        attemptIntent=attempt_intent,
        beginReceipt=receipt,
        authorityBinding=binding,
        expectedActionCompareVersion=6,
        actionCompareVersion=7,
        expectedResolutionAttemptCompareVersion=1,
        resolutionAttemptCompareVersion=2,
        expectedPartitionCompareVersion=9,
        partitionCompareVersion=10,
        preparedEvent=event,
    )


def _grant(
    preparation: RecoveryExecutionPreparation,
) -> RecoveryExecutionGrant:
    return RecoveryExecutionGrant(
        grantId="recovery_execution_grant_01",
        preparation=preparation,
        attemptIntentDigest=preparation.attempt_intent.attempt_intent_digest,
        preparationDigest=preparation.preparation_digest,
        authorityBindingDigest=preparation.authority_binding.binding_digest,
        executorId="recovery-executor",
        executorVersion="2.0.0",
        executableArtifactDigest=D4,
        sandboxProfileDigest=preparation.authority_binding.authority_contract.sandbox_profile_digest,
        executionTokenDigest=D5,
        issuedAt=NOW + timedelta(seconds=4),
        expiresAt=NOW + timedelta(minutes=1),
    )


def _start(
    preparation: RecoveryExecutionPreparation,
    grant: RecoveryExecutionGrant,
) -> RecoveryExecutionStart:
    payload = {
        "actionCompareVersion": 8,
        "attemptIntentDigest": preparation.attempt_intent.attempt_intent_digest,
        "authorityBindingDigest": preparation.authority_binding.binding_digest,
        "executableArtifactDigest": grant.executable_artifact_digest,
        "executionGrantDigest": grant.grant_digest,
        "executorId": grant.executor_id,
        "executorVersion": grant.executor_version,
        "expectedActionCompareVersion": 7,
        "expectedPartitionCompareVersion": 10,
        "expectedResolutionAttemptCompareVersion": 2,
        "partitionCompareVersion": 11,
        "preparationDigest": preparation.preparation_digest,
        "resolutionAttemptCompareVersion": 3,
        "sandboxProfileDigest": grant.sandbox_profile_digest,
    }
    event = _event(
        event_id="event_recovery_executing",
        event_type="recovery.action_executing",
        attempt_id=preparation.attempt_intent.resolution_attempt_id,
        request_digest=preparation.attempt_intent.normalized_request_digest,
        authority_contract_id=(
            preparation.authority_binding.authority_contract.authority_contract_id
        ),
        causation_id=preparation.prepared_event.event_id,
        payload=payload,
        sequence=23,
        previous_hash=preparation.prepared_event.event_hash,
        event_hash=D6,
        created_at=NOW + timedelta(seconds=5),
    )
    return RecoveryExecutionStart(
        preparation=preparation,
        grant=grant,
        expectedActionCompareVersion=7,
        actionCompareVersion=8,
        expectedResolutionAttemptCompareVersion=2,
        resolutionAttemptCompareVersion=3,
        expectedPartitionCompareVersion=10,
        partitionCompareVersion=11,
        executingEvent=event,
    )


def test_recovery_prepare_and_start_require_authority_target_and_execution_grant() -> None:
    context = _context()
    decision = _decision(context)
    binding = _authority_binding(context, decision)
    attempt_intent = _attempt_intent(context, decision)
    receipt = _begin_receipt(context, decision, binding)
    preparation = _preparation(context, decision, binding, attempt_intent, receipt)
    grant = _grant(preparation)
    start = _start(preparation, grant)

    assert preparation.preparation_digest == _model_digest(
        preparation,
        exclude={"preparation_digest"},
    )
    assert grant.grant_digest == _model_digest(grant, exclude={"grant_digest"})
    assert start.start_digest == _model_digest(start, exclude={"start_digest"})
    assert start.executing_event.previous_hash == preparation.prepared_event.event_hash

    payload = grant.model_dump(by_alias=True)
    payload["attemptIntentDigest"] = D0
    with pytest.raises(ValidationError, match="attemptIntentDigest"):
        RecoveryExecutionGrant.model_validate(payload)

    payload = start.model_dump(by_alias=True)
    payload["grant"]["preparationDigest"] = D0
    with pytest.raises(ValidationError, match="preparationDigest"):
        RecoveryExecutionStart.model_validate(payload)
