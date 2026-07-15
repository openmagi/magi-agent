from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority import SUPPORTED_SCHEMA_VERSIONS
from magi_agent.execution_authority.contracts import (
    ActionIntent,
    ActionAdmission,
    ActionProposal,
    ActionReceipt,
    ActionResolution,
    ActionSnapshot,
    AttemptObservationRecording,
    AttemptSnapshot,
    AttemptVerificationRecording,
    AuthorityCapability,
    AuthorityContract,
    BackendObservation,
    CompletionVerdict,
    CompletionPersistenceReceipt,
    CoverageDescriptor,
    DependencyHealth,
    DependencyContract,
    EffectDeclarationBinding,
    EpochSeal,
    EpochSnapshot,
    EvidenceNode,
    EvidenceNodeDraft,
    EvidenceEdge,
    EvidenceRecordRecording,
    ExecutionPreparation,
    ExecutionStart,
    FinalizationRequest,
    FinalizationEvaluationRequest,
    FreshnessBinding,
    GenericJournalEventDraft,
    IntegrityScanResult,
    JournalCoverageWindow,
    JournalChainLink,
    JournalEvent,
    LeaseSnapshot,
    NonExecutionProof,
    NormalizedInputDraft,
    NormalizedInputSnapshot,
    OutboxItem,
    PartitionGate,
    PartitionRecoveryPlan,
    ProjectionCursorBinding,
    RecoveryContext,
    RecoveryDecision,
    RequiredProjection,
    Requirement,
    RequirementResult,
    ResearchClaimResult,
    ResearchClaimRequirement,
    ResearchProofObligation,
    ResearchSourceBinding,
    ResponseClaim,
    ResponseClaimManifest,
    SourceSpan,
    TaskContractSnapshot,
    bind_task_contract,
    UserDecisionRequest,
    UserDecisionReceipt,
    UserDecisionRecording,
    UserDecisionExpirationRequest,
    UserDecisionInvalidationRequest,
    UserDecisionSnapshot,
    UserDecisionTransition,
    UserApprovalConsumption,
    VerificationEvidenceBinding,
    WorkspaceCommitDecisionRequest,
    WorkspaceCommitDecision,
    WorkspaceCommitSnapshot,
    WorkspacePublicationObservation,
    WorkspacePublicationReceipt,
    WorkspaceQuarantineReceipt,
    WorkspaceSnapshot,
    canonical_backend_observation_digest,
    canonical_evidence_node_digest,
    canonical_evidence_edges_digest,
    canonical_action_intent_digest,
    canonical_action_proposal_digest,
    canonical_authority_contract_digest,
    canonical_provider_guarantees_digest,
    canonical_required_projections_digest,
    canonical_recovery_decision_digest,
    canonical_resource_refs_digest,
    canonical_task_contract_bytes,
    canonical_task_contract_digest,
    canonical_task_contract_json,
    canonical_workspace_view_binding_digest,
    canonical_workspace_commit_decision_digest,
    canonical_workspace_publication_observation_digest,
    draft_journal_event,
    validate_recovery_decision_context,
    validate_completion_persistence_contract,
    validate_completion_persistence_receipt,
    validate_finalization_request_epoch,
    validate_same_action_identity,
    validate_action_proposal_input_snapshot,
    validate_source_span,
)
from magi_agent.execution_authority.envelopes import _draft_lifecycle_journal_event
from magi_agent.execution_authority.state_machine import (
    ActionState,
    CompletionStatus,
    DependencyStatus,
    EffectClass,
    EvidenceSemanticClass,
    IdempotencyCapability,
    LeaseState,
    OutboxState,
    ProviderGuarantee,
    RecoveryDisposition,
    RecoveryStrategy,
    RequirementState,
    ResourceSemantics,
    TransmissionState,
)
from magi_agent.execution_authority.envelopes import (
    WorkspaceCommitRecoveryClaim,
    WorkspaceCommitRecoveryClaimRequest,
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
NOW = datetime(2026, 7, 14, 3, 0, tzinfo=UTC)
WORKSPACE_ROOT_REF = f"workspace://{D0}/"
WORKSPACE_A_REF = WORKSPACE_ROOT_REF + "a.txt"
WORKSPACE_B_REF = WORKSPACE_ROOT_REF + "b.txt"


def _research_proof() -> ResearchProofObligation:
    return ResearchProofObligation(
        claims=(
            ResearchClaimRequirement(
                claimId="claim_weather",
                claimClass="temporal_fact",
                proposition="서울의 현재 날씨를 공식 출처로 확인한다.",
                freshness="same_retrieval_window",
            ),
        ),
        queryClasses=("official_primary", "reputable_secondary"),
        primarySourceRule="required_when_available",
        conflictHandling="resolve_or_disclose",
        stoppingRules=("claim_coverage_met", "conflicts_resolved_or_disclosed"),
        limitedSnippetAllowance="discovery_only",
    )


def _task(
    *,
    intent: str = "서울 날씨를 검증해줘",
    dependencies: tuple[DependencyContract, ...] = (),
) -> TaskContractSnapshot:
    return TaskContractSnapshot(
        taskContractId="task_01",
        version=1,
        completionEpochId="epoch_01",
        sourceMessageDigests=(D1,),
        intent=intent,
        inclusions=("검증된 결과",),
        exclusions=(),
        constraints=(),
        assumptions=(),
        dependencies=dependencies,
        acceptableBlockedBehavior="report blocked",
        acceptableUnavailableBehavior="report unavailable",
        requirements=(
            Requirement(
                requirementId="req_01",
                text="현재 정보를 검증한다",
                state=RequirementState.PENDING,
                proof={
                    "evidenceKinds": ("source_snapshot", "entailment_verdict"),
                    "freshness": "same_state_root",
                    "research": _research_proof(),
                },
            ),
        ),
    )


def _capability() -> AuthorityCapability:
    return AuthorityCapability(
        effectClass="workspace.write",
        resourceRef=WORKSPACE_A_REF,
        networkRefs=(),
        credentialRefs=(),
        workspaceViewBindingDigest=D2,
    )


def _declaration() -> EffectDeclarationBinding:
    provider_guarantees = (ProviderGuarantee.LOCAL_ATOMIC,)
    return EffectDeclarationBinding(
        effectName="workspace.patch",
        effectClass=EffectClass.WORKSPACE_WRITE,
        resourceSemantics=ResourceSemantics.WORKSPACE_TRANSACTION,
        handlerDigest=D2,
        normalizerDigest=D3,
        resourceDeriverDigest=D4,
        executorDigest=D5,
        recoveryAdapterDigest=D6,
        providerGuaranteesDigest=canonical_provider_guarantees_digest(provider_guarantees),
        providerGuarantees=provider_guarantees,
        idempotencyCapability=IdempotencyCapability.LOCAL_GENERATION_CAS,
        recoveryStrategy=RecoveryStrategy.WORKSPACE_TRANSACTION,
    )


def _proposal_payload() -> dict[str, object]:
    return {
        "schemaId": "magi.action_proposal.v1",
        "actionId": "act_01",
        "attemptId": "try_01",
        "partitionId": "workspace_01",
        "actorId": "actor_01",
        "identityDigest": D8,
        "policyDigest": D9,
        "sessionId": "session_01",
        "turnId": "turn_01",
        "runId": "run_01",
        "taskContractId": "task_01",
        "taskVersion": 1,
        "taskContractDigest": canonical_task_contract_digest(_task()),
        "completionEpochId": "epoch_01",
        "declaration": _declaration(),
        "capabilities": (_capability(),),
        "normalizedInputDigest": D3,
        "normalizedRequestSnapshotRef": f"authority-input://{D3}",
        "readSet": (WORKSPACE_A_REF,),
        "absenceSet": (),
        "writeSet": (WORKSPACE_A_REF,),
        "egressSet": (),
        "readSetDigest": canonical_resource_refs_digest((WORKSPACE_A_REF,)),
        "absenceSetDigest": canonical_resource_refs_digest(()),
        "writeSetDigest": canonical_resource_refs_digest((WORKSPACE_A_REF,)),
        "egressSetDigest": canonical_resource_refs_digest(()),
        "workspaceViewBindingDigest": D2,
        "idempotencyKeyDigest": D8,
        "evidenceObligations": ("action_receipt", "workspace_postcondition"),
        "compensatesActionId": None,
    }


def _recovery_intent(
    recovery_strategy: RecoveryStrategy = RecoveryStrategy.READ_ONLY_REPLAY,
) -> ActionIntent:
    payload = _proposal_payload()
    payload.update(
        {
            "schemaId": "magi.action_intent.v1",
            "admissionSequence": 1,
        }
    )
    if recovery_strategy is RecoveryStrategy.READ_ONLY_REPLAY:
        guarantees = (ProviderGuarantee.NONE,)
        payload.update(
            {
                "declaration": EffectDeclarationBinding(
                    effectName="workspace.read",
                    effectClass=EffectClass.WORKSPACE_READ,
                    resourceSemantics=ResourceSemantics.READ_ONLY,
                    handlerDigest=D2,
                    normalizerDigest=D3,
                    resourceDeriverDigest=D4,
                    executorDigest=D5,
                    recoveryAdapterDigest=D6,
                    providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
                    providerGuarantees=guarantees,
                    idempotencyCapability=IdempotencyCapability.NONE,
                    recoveryStrategy=recovery_strategy,
                ),
                "capabilities": (
                    AuthorityCapability(
                        effectClass=EffectClass.WORKSPACE_READ,
                        resourceRef=WORKSPACE_A_REF,
                        networkRefs=(),
                        credentialRefs=(),
                        workspaceViewBindingDigest=D2,
                    ),
                ),
                "writeSet": (),
                "writeSetDigest": canonical_resource_refs_digest(()),
            }
        )
    elif recovery_strategy is RecoveryStrategy.WORKSPACE_TRANSACTION:
        pass
    elif recovery_strategy is RecoveryStrategy.PROVIDER_RECONCILIATION:
        guarantees = (ProviderGuarantee.RECONCILABLE,)
        resource_ref = "https://api.example.com/messages"
        payload.update(
            {
                "declaration": EffectDeclarationBinding(
                    effectName="message.deliver",
                    effectClass=EffectClass.NETWORK_WRITE,
                    resourceSemantics=ResourceSemantics.REMOTE_EFFECT,
                    handlerDigest=D2,
                    normalizerDigest=D3,
                    resourceDeriverDigest=D4,
                    executorDigest=D5,
                    recoveryAdapterDigest=D6,
                    providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
                    providerGuarantees=guarantees,
                    idempotencyCapability=IdempotencyCapability.RECONCILIATION_ONLY,
                    recoveryStrategy=recovery_strategy,
                ),
                "capabilities": (
                    AuthorityCapability(
                        effectClass=EffectClass.NETWORK_WRITE,
                        resourceRef=resource_ref,
                        networkRefs=(resource_ref,),
                        credentialRefs=(),
                        workspaceViewBindingDigest=None,
                    ),
                ),
                "readSet": (),
                "readSetDigest": canonical_resource_refs_digest(()),
                "writeSet": (),
                "writeSetDigest": canonical_resource_refs_digest(()),
                "egressSet": (resource_ref,),
                "egressSetDigest": canonical_resource_refs_digest((resource_ref,)),
                "workspaceViewBindingDigest": None,
            }
        )
    else:
        raise AssertionError(f"unsupported recovery test strategy: {recovery_strategy}")
    return ActionIntent.model_validate(payload)


def _recovery_authority(
    intent: ActionIntent,
    *,
    attempt_id: str,
    expires_at: datetime = NOW + timedelta(minutes=10),
) -> AuthorityContract:
    return AuthorityContract(
        schemaVersion=1,
        authorityContractId=f"authority_recovery_{attempt_id}",
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
        requestBodyDigest=D5 if any(
            capability.effect_class is EffectClass.NETWORK_WRITE
            for capability in intent.capabilities
        ) else None,
        credentialScopeDigest=None,
        networkDigest=D6 if any(
            capability.effect_class is EffectClass.NETWORK_WRITE
            for capability in intent.capabilities
        ) else None,
        disclosureDigest=D4,
        capabilities=intent.capabilities,
        workspaceViewBindingDigest=intent.workspace_view_binding_digest,
        sandboxProfileDigest=D6,
        guardianCeilingDigest=D7,
        expiresAt=expires_at,
        revokedAt=None,
        revocationDigest=None,
        fencingToken=11,
        maximumUses=1,
        decisionRequestId=None,
        resumeBindingDigest=None,
        parentAuthorityDigest=None,
        delegationChain=(),
    )


def _observation_payload() -> dict[str, object]:
    return {
        "actionId": "act_01",
        "attemptId": "try_01",
        "partitionId": "workspace_01",
        "taskContractDigest": canonical_task_contract_digest(_task()),
        "actionIntentDigest": D0,
        "requestDigest": D3,
        "authorityDigest": D4,
        "fencingToken": 7,
        "executorId": "workspace-executor",
        "executorVersion": "1.0.0",
        "sandboxProfileDigest": D5,
        "providerId": None,
        "providerVersion": None,
        "providerCapabilitiesDigest": None,
        "attemptKind": "execution",
        "sourceAttemptId": None,
        "reconcilesAttemptId": None,
        "effectMayHaveStarted": True,
        "observedOutcome": "committed",
        "transmissionState": "proven_not_sent",
        "providerRequestIdDigest": None,
        "observedEffectRefs": (WORKSPACE_A_REF,),
        "reasonCodes": ("published",),
        "processExitCode": 0,
        "stdoutDigest": D6,
        "stderrDigest": D7,
        "outputTruncated": False,
        "privateWorkspaceDiffDigest": D8,
        "workspacePublicationDigest": D9,
        "providerReceiptDigest": None,
    }


def _non_execution_proof_payload(
    *,
    source_state: ActionState = ActionState.PREPARED,
    source_version: int = 2,
) -> dict[str, object]:
    durable_records = {
        ActionState.PROPOSED: (False, False, False),
        ActionState.AUTHORIZED: (True, False, False),
        ActionState.PREPARED: (True, True, False),
        ActionState.EXECUTING: (True, True, True),
        ActionState.OBSERVED: (True, True, True),
    }[source_state]
    return {
        "schemaVersion": 1,
        "proofId": "nonexec_01",
        "partitionId": "workspace_01",
        "actionId": "act_01",
        "sourceAttemptId": "try_01",
        "expectedSourceState": source_state,
        "expectedSourceVersion": source_version,
        "taskContractDigest": canonical_task_contract_digest(_task()),
        "authorityUseRecorded": durable_records[0],
        "preparedRecordRecorded": durable_records[1],
        "executionHandoffRecorded": durable_records[2],
        "providerTransmissionState": "proven_not_sent",
        "visibleEffectsAbsent": True,
        "evidenceId": "evidence_nonexec_01",
        "evidenceDigest": D7,
        "coverageDigest": D8,
        "actionSnapshotDigest": D1,
        "attemptSnapshotDigest": D2,
        "journalHeadDigest": D3,
        "producerId": "executor-supervisor",
        "producerVersion": "1.0.0",
        "producerSchemaVersion": "1",
        "producerInvocationEvidenceId": "producer_invocation_nonexec_01",
        "producerInvocationEvidenceDigest": D9,
        "producerAlive": True,
        "observedAt": NOW,
    }


def _recovery_context_payload(
    *,
    source_state: ActionState = ActionState.PREPARED,
    source_version: int = 2,
    resolution_attempt_id: str | None = "try_02",
    include_non_execution_proof: bool = True,
    workspace_commit_state: str = "none",
    recovery_strategy: RecoveryStrategy = RecoveryStrategy.READ_ONLY_REPLAY,
    effect_may_have_started: bool | None = None,
) -> dict[str, object]:
    intent = _recovery_intent(recovery_strategy)
    authority = _recovery_authority(
        intent,
        attempt_id=resolution_attempt_id or "try_01",
    )
    source_terminal = source_state in {
        ActionState.DENIED,
        ActionState.COMMITTED,
        ActionState.ABORTED,
        ActionState.PARTIAL,
        ActionState.UNKNOWN,
        ActionState.VERIFIED,
    }
    if effect_may_have_started is None:
        effect_may_have_started = source_terminal or workspace_commit_state in {
            "decided",
            "published",
            "quarantined",
        }
    return {
        "schemaVersion": 1,
        "contextId": "ctx_01",
        "recoveryEpochId": "recovery_epoch_01",
        "recoveryPlanDigest": D0,
        "recoveryOwnerId": "worker_01",
        "partitionId": "workspace_01",
        "expectedPartitionCompareVersion": 8,
        "recoveryFencingToken": 11,
        "taskContractDigest": canonical_task_contract_digest(_task()),
        "actionId": "act_01",
        "expectedActionCompareVersion": 5,
        "sourceAttemptId": "try_01",
        "expectedSourceState": source_state,
        "expectedSourceVersion": source_version,
        "sourceTerminal": source_terminal,
        "resolutionAttemptId": resolution_attempt_id,
        "pendingUserDecision": False,
        "effectMayHaveStarted": effect_may_have_started,
        "replaySafe": recovery_strategy is RecoveryStrategy.READ_ONLY_REPLAY,
        "authorityValid": True,
        "taskVersionCurrent": True,
        "fenceCurrent": True,
        "stateRootCurrent": True,
        "workspaceCommitState": workspace_commit_state,
        "workspaceCommitSnapshotDigest": (
            D4 if workspace_commit_state in {"decided", "published", "quarantined"} else None
        ),
        "projectionStatus": "current",
        "integrityStatus": "clean",
        "integrityScanDigest": D0,
        "recoveryAdapterId": "provider-reconcile",
        "recoveryAdapterVersion": "1.0.0",
        "recoveryAdapterSchemaVersion": "1",
        "recoveryAdapterDigest": intent.declaration.recovery_adapter_digest,
        "evaluatedAt": NOW,
        "actionIntent": intent,
        "actionIntentDigest": canonical_action_intent_digest(intent),
        "recoveryAuthority": authority,
        "recoveryAuthorityDigest": canonical_authority_contract_digest(authority),
        "nonExecutionProof": (
            _non_execution_proof_payload(
                source_state=source_state,
                source_version=source_version,
            )
            if include_non_execution_proof
            else None
        ),
        "actionSnapshotDigest": D1,
        "attemptSnapshotDigest": D2,
        "journalHeadDigest": D3,
        "workspaceViewBindingDigest": intent.workspace_view_binding_digest,
        "providerCapabilitiesDigest": None,
        "currentPolicyDigest": intent.policy_digest,
        "currentSandboxProfileDigest": D6,
    }


def _recovery_decision_payload(
    context: RecoveryContext,
    *,
    disposition: RecoveryDisposition,
    proof_digest: str | None,
) -> dict[str, object]:
    return {
        "schemaId": "magi.recovery_decision.v1",
        "decisionId": (
            f"recovery:{context.recovery_epoch_id}:{context.action_id}:{context.source_attempt_id}"
        ),
        "recoveryEpochId": context.recovery_epoch_id,
        "recoveryPlanDigest": context.recovery_plan_digest,
        "recoveryOwnerId": context.recovery_owner_id,
        "partitionId": context.partition_id,
        "expectedPartitionCompareVersion": context.expected_partition_compare_version,
        "recoveryFencingToken": context.recovery_fencing_token,
        "actionId": context.action_id,
        "expectedActionCompareVersion": context.expected_action_compare_version,
        "taskContractDigest": context.task_contract_digest,
        "sourceAttemptId": context.source_attempt_id,
        "expectedSourceState": context.expected_source_state,
        "expectedSourceVersion": context.expected_source_version,
        "sourceTerminal": context.source_terminal,
        "terminalizeSourceTo": ActionState.ABORTED,
        "resolutionAttemptId": context.resolution_attempt_id,
        "disposition": disposition,
        "contextDigest": context.context_digest,
        "nonExecutionProofDigest": proof_digest,
        "reasonCodes": ("mechanically_not_started",),
    }


def _stored_event(
    event_type: str,
    *,
    event_id: str,
    action_id: str = "act_01",
    attempt_id: str = "try_01",
    authority_contract_id: str | None = None,
    request_digest: str = D3,
    policy_digest: str = D9,
    fencing_token: int = 7,
    actor_id: str = "actor_01",
    causation_id: str = "turn_01",
    payload: dict[str, object] | None = None,
    sequence: int = 1,
    previous_hash: str = D0,
    event_hash: str = D1,
    row_checksum: str = D2,
) -> JournalEvent:
    draft = _draft_lifecycle_journal_event(
        event_id=event_id,
        partition_id="workspace_01",
        event_type=event_type,
        action_id=action_id,
        attempt_id=attempt_id,
        task_contract_id="task_01",
        task_version=1,
        task_contract_digest=canonical_task_contract_digest(_task()),
        completion_epoch_id="epoch_01",
        admission_sequence=1,
        authority_contract_id=authority_contract_id,
        request_digest=request_digest,
        idempotency_key_digest=D8,
        fencing_token=fencing_token,
        actor_id=actor_id,
        policy_digest=policy_digest,
        causation_id=causation_id,
        correlation_id="run_01",
        identity_digest=D8,
        payload={} if payload is None else payload,
    )
    return JournalEvent(
        **draft.model_dump(by_alias=True),
        sequence=sequence,
        previousHash=previous_hash,
        eventHash=event_hash,
        rowChecksum=row_checksum,
        createdAt=NOW,
    )


def _workspace_commit_decision() -> WorkspaceCommitDecision:
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_ROOT_REF,
        authority_partition_id="workspace_01",
        generation=1,
        state_root=D1,
    )
    request = WorkspaceCommitDecisionRequest(
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
        stateRootBefore=D1,
        stateRootAfter=D2,
        decisionFencingToken=7,
        mutationPlanDigest=D3,
        stagingManifestRef=f"authority-manifest://{D4}",
        stagingManifestDigest=D4,
        changedResourceRefsDigest=canonical_resource_refs_digest((WORKSPACE_A_REF,)),
        workspaceViewBindingDigest=view_digest,
        changedResourceRefs=(WORKSPACE_A_REF,),
    )
    snapshot = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=request,
        state="decided",
        activeFencingToken=7,
        activeFenceEventId="event_commit_decided",
        activeFenceEventSequence=1,
        activeFenceEventHash=D1,
        commitCompareVersion=1,
    )
    return WorkspaceCommitDecision(
        schemaVersion=1,
        snapshot=snapshot,
        workspaceCompareVersion=4,
        commitEvent=_stored_event(
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
                "expectedTransactionCompareVersion": (request.expected_transaction_compare_version),
                "expectedWorkspaceCompareVersion": request.expected_workspace_compare_version,
                "mutationPlanDigest": request.mutation_plan_digest,
                "requestDigest": "sha256:"
                + sha256(
                    json.dumps(
                        request.model_dump(by_alias=True, mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest(),
                "stagingManifestDigest": request.staging_manifest_digest,
                "stateRootAfter": request.state_root_after,
                "stateRootBefore": request.state_root_before,
                "targetGeneration": request.target_generation,
                "transactionId": request.transaction_id,
                "workspaceId": request.workspace_id,
                "workspaceViewBindingDigest": request.workspace_view_binding_digest,
            },
        ),
    )


def _claim_manifest(candidate: str) -> ResponseClaimManifest:
    split = 3 if len(candidate) > 3 else len(candidate)
    parts = (candidate[:split], candidate[split:]) if candidate[split:] else (candidate,)
    claims: list[ResponseClaim] = []
    codepoint = 0
    utf8 = 0
    for index, part in enumerate(parts):
        encoded = part.encode("utf-8")
        claims.append(
            ResponseClaim(
                claimId=f"claim_{index}",
                claimClass="result",
                textDigest="sha256:" + sha256(encoded).hexdigest(),
                codepointStart=codepoint,
                codepointEnd=codepoint + len(part),
                utf8Start=utf8,
                utf8End=utf8 + len(encoded),
                evidenceIds=("evidence_01",),
            )
        )
        codepoint += len(part)
        utf8 += len(encoded)
    return ResponseClaimManifest(
        candidateResponseDigest="sha256:" + sha256(candidate.encode()).hexdigest(),
        segments=tuple(claims),
    )


def _finalization(
    candidate: str = "완료 ✅ verified",
    *,
    task: TaskContractSnapshot | None = None,
    dependency_health: tuple[DependencyHealth, ...] = (),
) -> FinalizationRequest:
    task = task or _task()
    digest = canonical_task_contract_digest(task)
    manifest = _claim_manifest(candidate)
    return FinalizationRequest(
        finalizationId="final_01",
        taskContract=task,
        taskContractDigest=digest,
        taskContractSnapshotRef=f"authority-task://{digest}",
        taskPartitionId="task:task_01:1",
        stateRoot=D3,
        evidenceRoot=D4,
        completionEpochId="epoch_01",
        barrierAdmissionSequence=1,
        dependencyHealth=dependency_health,
        candidateResponse=candidate,
        claimManifest=manifest,
    )


def _completion_contracts(
    *,
    request: FinalizationRequest | None = None,
    requirements: tuple[RequirementResult, ...] | None = None,
    included_action_ids: tuple[str, ...] = ("act_01",),
) -> tuple[EpochSeal, CompletionVerdict]:
    request = request or _finalization()
    cursor = ProjectionCursorBinding(
        schemaVersion=1,
        partitionId=request.task_partition_id,
        projectionId="task",
        requiredSequence=1,
        requiredEventHash=D1,
        acknowledgedSequence=1,
        acknowledgedEventHash=D1,
        stateRoot=request.state_root,
        compareVersion=1,
    )
    projections = (
        RequiredProjection(
            schemaVersion=1,
            partitionId=request.task_partition_id,
            projectionId="task",
        ),
    )
    seal = EpochSeal(
        schemaVersion=1,
        completionEpochId=request.completion_epoch_id,
        taskPartitionId=request.task_partition_id,
        taskContractDigest=request.task_contract_digest,
        taskContractSnapshotRef=request.task_contract_snapshot_ref,
        barrierAdmissionSequence=request.barrier_admission_sequence,
        epochCompareVersion=2,
        requiredProjectionDigest=canonical_required_projections_digest(projections),
        requiredProjections=projections,
        sealedAt=NOW,
    )
    research = request.task_contract.requirements[0].proof.research
    assert research is not None
    claim = research.claims[0]
    verdict = CompletionVerdict(
        schemaId="magi.completion_verdict.v1",
        completionId="completion_01",
        finalizationId=request.finalization_id,
        finalizationRequestDigest=request.finalization_request_digest,
        responseClaimManifestDigest=request.response_claim_manifest_digest,
        status=CompletionStatus.COMPLETE,
        taskContractId=request.task_contract.task_contract_id,
        taskVersion=request.task_contract.version,
        taskContractDigest=request.task_contract_digest,
        taskContractSnapshotRef=request.task_contract_snapshot_ref,
        taskPartitionId=request.task_partition_id,
        completionEpochId=request.completion_epoch_id,
        stateRoot=request.state_root,
        evidenceRoot=request.evidence_root,
        barrierAdmissionSequence=request.barrier_admission_sequence,
        requiredProjectionDigest=seal.required_projection_digest,
        projectionCursors=(cursor,),
        requirements=(
            RequirementResult(
                requirementId="req_01",
                state=RequirementState.SATISFIED,
                evidenceIds=("evidence_01",),
                researchClaims=(
                    ResearchClaimResult(
                        schemaVersion=1,
                        claimId=claim.claim_id,
                        propositionDigest=claim.proposition_digest,
                        state="satisfied",
                        evidenceIds=("evidence_01",),
                        reasonCodes=("entailed",),
                    ),
                ),
                reasonCodes=("verified",),
            ),
        )
        if requirements is None
        else requirements,
        includedActionIds=included_action_ids,
        responseDigest=request.claim_manifest.candidate_response_digest,
        reasonCodes=("all_requirements_satisfied",),
    )
    return seal, verdict


def _decision_request() -> UserDecisionRequest:
    return UserDecisionRequest(
        schemaId="magi.user_decision_request.v1",
        decisionRequestId="decision_01",
        principalId="actor_01",
        tenantId="tenant_01",
        sessionId="session_01",
        turnId="turn_01",
        taskContractId="task_01",
        taskVersion=1,
        taskContractDigest=canonical_task_contract_digest(_task()),
        completionEpochId="epoch_01",
        actionId="act_01",
        authorityPartitionId="workspace_01",
        normalizedRequestDigest=D3,
        capabilities=(_capability(),),
        workspaceViewBindingDigest=D2,
        authorityCeilingDigest=D4,
        policyDigest=D5,
        pendingEventId="event_01",
        reasonCodes=("sensitive_write",),
        createdAt=NOW,
        expiresAt=NOW + timedelta(minutes=5),
        compareVersion=0,
    )


def test_envelopes_module_imports_directly_in_a_fresh_interpreter() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import magi_agent.execution_authority.envelopes"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_task_contract_has_one_strict_canonical_unicode_byte_stream() -> None:
    task = _task()
    canonical_json = canonical_task_contract_json(task)
    canonical_bytes = canonical_task_contract_bytes(task)

    assert canonical_bytes == canonical_json.encode("utf-8")
    assert "서울" in canonical_json
    assert "\\uc11c\\uc6b8" not in canonical_json
    assert json.loads(canonical_json)["intent"] == task.intent
    assert canonical_task_contract_digest(task) == ("sha256:" + sha256(canonical_bytes).hexdigest())

    invalid = task.model_dump(by_alias=True, mode="json")
    invalid["requirements"][0]["proof"]["freshness"] = float("nan")
    with pytest.raises((TypeError, ValueError, ValidationError)):
        TaskContractSnapshot.model_validate(invalid)


def test_public_schema_registry_is_exact_and_immutable() -> None:
    assert SUPPORTED_SCHEMA_VERSIONS == (
        "magi.action_intent.v1",
        "magi.action_proposal.v1",
        "magi.action_receipt.v1",
        "magi.action_resolution.v1",
        "magi.completion_verdict.v1",
        "magi.dependency_health.v1",
        "magi.finalization_request.v1",
        "magi.recovery_decision.v1",
        "magi.response_claim_manifest.v1",
        "magi.task_contract.v1",
        "magi.user_decision_receipt.v1",
        "magi.user_decision_request.v1",
        "magi.workspace_commit_decision_request.v1",
        "magi.workspace_commit_recovery_claim_request.v1",
    )
    assert isinstance(SUPPORTED_SCHEMA_VERSIONS, tuple)


def test_action_proposal_binds_derived_declaration_resources_and_workspace_view() -> None:
    proposal = ActionProposal.model_validate(_proposal_payload())
    intent = ActionIntent.model_validate(
        {**_proposal_payload(), "schemaId": "magi.action_intent.v1", "admissionSequence": 1}
    )

    assert proposal.declaration.effect_class is EffectClass.WORKSPACE_WRITE
    assert (
        proposal.workspace_view_binding_digest
        == proposal.capabilities[0].workspace_view_binding_digest
    )
    assert intent.admission_sequence == 1
    assert canonical_action_proposal_digest(proposal) != canonical_action_intent_digest(intent)
    assert ActionAdmission.model_fields["action_intent_digest"].alias == "actionIntentDigest"


def test_same_action_id_rejects_a_changed_canonical_proposal_digest() -> None:
    proposal_payload = _proposal_payload()
    intent = ActionIntent.model_validate(
        {
            **proposal_payload,
            "schemaId": "magi.action_intent.v1",
            "admissionSequence": 1,
        }
    )
    unchanged = ActionProposal.model_validate(proposal_payload)
    assert validate_same_action_identity(intent, unchanged) == unchanged

    changed_payload = dict(proposal_payload)
    changed_payload["normalizedInputDigest"] = D9
    changed_payload["normalizedRequestSnapshotRef"] = f"authority-input://{D9}"
    changed = ActionProposal.model_validate(changed_payload)
    with pytest.raises(ValueError, match="same actionId.*different action digest"):
        validate_same_action_identity(intent, changed)

    payload = _proposal_payload()
    payload["workspaceViewBindingDigest"] = D1
    with pytest.raises(ValidationError, match="workspace"):
        ActionProposal.model_validate(payload)


def test_action_proposal_must_match_persisted_normalized_input_snapshot() -> None:
    proposal = ActionProposal.model_validate(_proposal_payload())
    snapshot = NormalizedInputSnapshot(
        schemaVersion=1,
        effectDeclarationDigest=proposal.declaration.effect_declaration_digest,
        normalizedInputDigest=proposal.normalized_input_digest,
        normalizedPayloadRef=(f"authority-input-payload://{proposal.normalized_input_digest}"),
        readSet=proposal.read_set,
        absenceSet=proposal.absence_set,
        writeSet=proposal.write_set,
        egressSet=proposal.egress_set,
        readSetDigest=proposal.read_set_digest,
        absenceSetDigest=proposal.absence_set_digest,
        writeSetDigest=proposal.write_set_digest,
        egressSetDigest=proposal.egress_set_digest,
        workspaceViewBindingDigest=proposal.workspace_view_binding_digest,
        idempotencyKeyDigest=proposal.idempotency_key_digest,
        snapshotRef=proposal.normalized_request_snapshot_ref,
        normalizerDigest=proposal.declaration.normalizer_digest,
        resourceDeriverDigest=proposal.declaration.resource_deriver_digest,
        storedAt=NOW,
        compareVersion=1,
    )
    assert validate_action_proposal_input_snapshot(proposal, snapshot) == proposal

    drifted_payload = proposal.model_dump(by_alias=True, mode="json")
    drifted_payload["normalizedInputDigest"] = D9
    drifted_payload["normalizedRequestSnapshotRef"] = f"authority-input://{D9}"
    drifted = ActionProposal.model_validate(drifted_payload)
    with pytest.raises(ValueError, match="normalizedInputDigest"):
        validate_action_proposal_input_snapshot(drifted, snapshot)

    payload = _proposal_payload()
    payload["normalizedRequestSnapshotRef"] = f"authority-input://{D4}"
    with pytest.raises(ValidationError, match="normalized"):
        ActionProposal.model_validate(payload)


def test_action_admission_rejects_top_level_or_event_binding_drift() -> None:
    proposal = ActionProposal.model_validate(_proposal_payload())
    intent = ActionIntent.model_validate(
        {
            **_proposal_payload(),
            "schemaId": "magi.action_intent.v1",
            "admissionSequence": 1,
        }
    )
    admission = ActionAdmission(
        schemaVersion=1,
        completionEpochId="epoch_01",
        admissionSequence=1,
        epochCompareVersion=2,
        actionCompareVersion=1,
        attemptCompareVersion=1,
        partitionCompareVersion=2,
        proposal=proposal,
        proposalDigest=canonical_action_proposal_digest(proposal),
        intent=intent,
        actionIntentDigest=canonical_action_intent_digest(intent),
        proposedEvent=_stored_event("action.proposed", event_id="event_proposed"),
        admissionEvent=_stored_event(
            "action.admitted",
            event_id="event_admitted",
            sequence=2,
            previous_hash=D1,
            event_hash=D2,
            row_checksum=D3,
        ),
    )
    assert admission.intent.action_id == "act_01"

    drifted = admission.model_dump(by_alias=True, mode="json")
    drifted["proposal"]["normalizedInputDigest"] = D9
    drifted["proposal"]["normalizedRequestSnapshotRef"] = f"authority-input://{D9}"
    drifted["proposalDigest"] = canonical_action_proposal_digest(
        ActionProposal.model_validate(drifted["proposal"])
    )
    with pytest.raises(ValidationError, match="proposal.*intent|same actionId"):
        ActionAdmission.model_validate(drifted)

    drifted = admission.model_dump(by_alias=True, mode="json")
    drifted["completionEpochId"] = "epoch_other"
    with pytest.raises(ValidationError, match="completionEpochId"):
        ActionAdmission.model_validate(drifted)

    drifted = admission.model_dump(by_alias=True, mode="json")
    drifted["proposedEvent"]["actionId"] = "act_other"
    with pytest.raises(ValidationError, match="proposedEvent"):
        ActionAdmission.model_validate(drifted)

    drifted = admission.model_dump(by_alias=True, mode="json")
    drifted["admissionEvent"]["sequence"] = 1
    with pytest.raises(ValidationError, match="directly follow"):
        ActionAdmission.model_validate(drifted)


def test_action_proposal_rejects_resource_set_digest_drift() -> None:
    payload = _proposal_payload()
    payload["readSet"] = (WORKSPACE_B_REF,)

    with pytest.raises(ValidationError, match="readSetDigest"):
        ActionProposal.model_validate(payload)

    payload = _proposal_payload()
    unordered = (WORKSPACE_B_REF, WORKSPACE_A_REF)
    payload["capabilities"] = (
        AuthorityCapability(
            effectClass="workspace.write",
            resourceRef=WORKSPACE_ROOT_REF,
            networkRefs=(),
            credentialRefs=(),
            workspaceViewBindingDigest=D2,
        ),
    )
    payload["readSet"] = unordered
    payload["readSetDigest"] = canonical_resource_refs_digest(unordered)
    with pytest.raises(ValidationError, match="canonical sorted order"):
        ActionProposal.model_validate(payload)

    payload = _proposal_payload()
    payload["capabilities"] = (
        AuthorityCapability(
            effectClass="workspace.write",
            resourceRef=WORKSPACE_B_REF,
            networkRefs=(),
            credentialRefs=(),
            workspaceViewBindingDigest=D2,
        ),
    )
    with pytest.raises(ValidationError, match="capability resources"):
        ActionProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "digest_name", "resource_ref"),
    (
        ("readSet", "readSetDigest", "workspace://not-a-sha256/a/../b"),
        ("absenceSet", "absenceSetDigest", "workspace://not-a-sha256/a/../b"),
        ("writeSet", "writeSetDigest", "workspace://not-a-sha256/a/../b"),
        (
            "egressSet",
            "egressSetDigest",
            "HTTPS://EXAMPLE.COM:443/a/../b?z=2&a=1",
        ),
    ),
)
def test_normalized_input_rejects_noncanonical_physical_resource_refs(
    field_name: str,
    digest_name: str,
    resource_ref: str,
) -> None:
    proposal = ActionProposal.model_validate(_proposal_payload())
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "effectDeclarationDigest": proposal.declaration.effect_declaration_digest,
        "normalizedInputDigest": proposal.normalized_input_digest,
        "normalizedPayloadRef": (f"authority-input-payload://{proposal.normalized_input_digest}"),
        "readSet": (),
        "absenceSet": (),
        "writeSet": (),
        "egressSet": (),
        "readSetDigest": canonical_resource_refs_digest(()),
        "absenceSetDigest": canonical_resource_refs_digest(()),
        "writeSetDigest": canonical_resource_refs_digest(()),
        "egressSetDigest": canonical_resource_refs_digest(()),
        "workspaceViewBindingDigest": proposal.workspace_view_binding_digest,
        "idempotencyKeyDigest": proposal.idempotency_key_digest,
    }
    payload[field_name] = (resource_ref,)
    payload[digest_name] = canonical_resource_refs_digest((resource_ref,))

    with pytest.raises(ValidationError, match="canonical"):
        NormalizedInputDraft.model_validate(payload)


@pytest.mark.parametrize("field_name", (None, "readSet", "absenceSet", "writeSet"))
def test_action_proposal_rejects_coherent_noncanonical_workspace_refs(
    field_name: str | None,
) -> None:
    resource_ref = "workspace://not-a-sha256/a/../b"
    payload = _proposal_payload()
    payload["capabilities"] = (
        AuthorityCapability(
            effectClass="workspace.write",
            resourceRef=resource_ref,
            networkRefs=(),
            credentialRefs=(),
            workspaceViewBindingDigest=D2,
        ),
    )
    for set_name, digest_name in (
        ("readSet", "readSetDigest"),
        ("absenceSet", "absenceSetDigest"),
        ("writeSet", "writeSetDigest"),
    ):
        refs = (resource_ref,) if set_name == field_name else ()
        payload[set_name] = refs
        payload[digest_name] = canonical_resource_refs_digest(refs)

    with pytest.raises(ValidationError, match="canonical"):
        ActionProposal.model_validate(payload)


def test_action_proposal_rejects_coherent_noncanonical_http_refs() -> None:
    alias = "HTTPS://EXAMPLE.COM:443/a/../b?z=2&a=1"
    guarantees = (ProviderGuarantee.RECONCILABLE,)
    payload = _proposal_payload()
    payload.update(
        {
            "declaration": EffectDeclarationBinding(
                effectName="network.write",
                effectClass=EffectClass.NETWORK_WRITE,
                resourceSemantics=ResourceSemantics.REMOTE_EFFECT,
                handlerDigest=D2,
                normalizerDigest=D3,
                resourceDeriverDigest=D4,
                executorDigest=D5,
                recoveryAdapterDigest=D6,
                providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
                providerGuarantees=guarantees,
                idempotencyCapability=IdempotencyCapability.RECONCILIATION_ONLY,
                recoveryStrategy=RecoveryStrategy.PROVIDER_RECONCILIATION,
            ),
            "capabilities": (
                AuthorityCapability(
                    effectClass=EffectClass.NETWORK_WRITE,
                    resourceRef=alias,
                    networkRefs=(alias,),
                    credentialRefs=(),
                ),
            ),
            "readSet": (),
            "readSetDigest": canonical_resource_refs_digest(()),
            "writeSet": (),
            "writeSetDigest": canonical_resource_refs_digest(()),
            "egressSet": (alias,),
            "egressSetDigest": canonical_resource_refs_digest((alias,)),
            "workspaceViewBindingDigest": None,
        }
    )

    with pytest.raises(ValidationError, match="canonical"):
        ActionProposal.model_validate(payload)


def test_action_proposal_rejects_unknown_evidence_obligation_semantics() -> None:
    payload = _proposal_payload()
    payload["evidenceObligations"] = ("model_says_it_worked",)

    with pytest.raises(ValidationError):
        ActionProposal.model_validate(payload)


def test_action_proposal_requires_a_primary_capability_for_its_declared_effect() -> None:
    payload = _proposal_payload()
    payload["capabilities"] = (
        AuthorityCapability(
            effectClass="network.connect",
            resourceRef="network:provider",
            networkRefs=("https://api.example.test/",),
            credentialRefs=(),
            workspaceViewBindingDigest=D2,
        ),
    )

    with pytest.raises(ValidationError, match="declared effectClass"):
        ActionProposal.model_validate(payload)


def test_process_action_can_bind_separate_workspace_read_and_write_capabilities() -> None:
    guarantees = (ProviderGuarantee.NONE,)
    declaration = EffectDeclarationBinding(
        effectName="process.exec",
        effectClass=EffectClass.PROCESS_EXEC,
        resourceSemantics=ResourceSemantics.PRIVATE_WORKSPACE_PROCESS,
        handlerDigest=D2,
        normalizerDigest=D3,
        resourceDeriverDigest=D4,
        executorDigest=D5,
        recoveryAdapterDigest=D6,
        providerGuaranteesDigest=canonical_provider_guarantees_digest(guarantees),
        providerGuarantees=guarantees,
        idempotencyCapability=IdempotencyCapability.NONE,
        recoveryStrategy=RecoveryStrategy.NO_REPLAY,
    )
    capabilities = (
        AuthorityCapability(
            effectClass="process.exec",
            resourceRef="binary:git",
            networkRefs=(),
            credentialRefs=(),
        ),
        AuthorityCapability(
            effectClass="workspace.read",
            resourceRef=WORKSPACE_ROOT_REF,
            networkRefs=(),
            credentialRefs=(),
            workspaceViewBindingDigest=D2,
        ),
        AuthorityCapability(
            effectClass="workspace.write",
            resourceRef=WORKSPACE_ROOT_REF,
            networkRefs=(),
            credentialRefs=(),
            workspaceViewBindingDigest=D2,
        ),
    )
    payload = _proposal_payload()
    payload["declaration"] = declaration
    payload["capabilities"] = tuple(
        sorted(capabilities, key=lambda item: item.model_dump_json(by_alias=True))
    )

    proposal = ActionProposal.model_validate(payload)
    assert proposal.declaration.effect_class is EffectClass.PROCESS_EXEC


def test_effect_declaration_digest_binds_every_semantic_field() -> None:
    payload = _declaration().model_dump(by_alias=True, mode="json")
    payload["handlerDigest"] = D9

    with pytest.raises(ValidationError, match="effectDeclarationDigest"):
        EffectDeclarationBinding.model_validate(payload)


def test_effect_declaration_rejects_provider_guarantee_digest_drift() -> None:
    payload = _declaration().model_dump(by_alias=True, mode="json")
    payload["providerGuarantees"] = ["reconcilable"]

    with pytest.raises(ValidationError, match="providerGuaranteesDigest"):
        EffectDeclarationBinding.model_validate(payload)


def test_effect_declaration_rejects_unordered_provider_guarantee_inputs() -> None:
    payload = _declaration().model_dump(by_alias=True, mode="json")
    payload["providerGuarantees"] = {"local_atomic"}
    with pytest.raises(ValidationError, match="ordered"):
        EffectDeclarationBinding.model_validate(payload)


def test_workspace_declaration_rejects_incompatible_recovery_semantics() -> None:
    payload = _declaration().model_dump(by_alias=True, mode="json")
    payload["recoveryStrategy"] = "no_replay"

    with pytest.raises(ValidationError, match="workspace_transaction"):
        EffectDeclarationBinding.model_validate(payload)


def test_effect_declaration_rejects_false_provider_semantics() -> None:
    guarantees = (ProviderGuarantee.NONE, ProviderGuarantee.RECONCILABLE)
    payload = _declaration().model_dump(by_alias=True, mode="json")
    payload.update(
        {
            "effectClass": "network.write",
            "resourceSemantics": "remote_effect",
            "providerGuarantees": [item.value for item in guarantees],
            "providerGuaranteesDigest": canonical_provider_guarantees_digest(guarantees),
            "idempotencyCapability": "reconciliation_only",
            "recoveryStrategy": "provider_reconciliation",
        }
    )
    with pytest.raises(ValidationError, match="none|NONE"):
        EffectDeclarationBinding.model_validate(payload)


def test_workspace_capability_requires_a_physical_view_binding() -> None:
    with pytest.raises(ValidationError, match="workspaceViewBindingDigest"):
        AuthorityCapability(
            effectClass="workspace.write",
            resourceRef=WORKSPACE_A_REF,
            networkRefs=(),
            credentialRefs=(),
        )


def test_journal_payload_is_canonical_unicode_and_rejects_sensitive_keys_recursively() -> None:
    draft = draft_journal_event(
        event_id="event_01",
        partition_id="workspace_01",
        event_type="audit.note",
        task_contract_id="task_01",
        task_version=1,
        task_contract_digest=canonical_task_contract_digest(_task()),
        completion_epoch_id="epoch_01",
        admission_sequence=1,
        request_digest=D1,
        idempotency_key_digest=D2,
        fencing_token=7,
        actor_id="actor_01",
        policy_digest=D3,
        causation_id="turn_01",
        correlation_id="run_01",
        identity_digest=D4,
        payload={"메모": "검증", "nested": [{"ok": True}]},
    )
    assert draft.payload_json == '{"nested":[{"ok":true}],"메모":"검증"}'
    assert draft.payload_digest == "sha256:" + sha256(draft.payload_json.encode()).hexdigest()

    secret_key = "pass" + "word"
    with pytest.raises(ValueError, match="sensitive"):
        draft_journal_event(
            event_id="event_02",
            partition_id="workspace_01",
            event_type="audit.note",
            task_contract_id="task_01",
            task_version=1,
            task_contract_digest=canonical_task_contract_digest(_task()),
            completion_epoch_id="epoch_01",
            admission_sequence=1,
            request_digest=D1,
            idempotency_key_digest=D2,
            fencing_token=7,
            actor_id="actor_01",
            policy_digest=D3,
            causation_id="turn_01",
            correlation_id="run_01",
            identity_digest=D4,
            payload={"nested": [{secret_key: "redacted"}]},
        )

    direct = draft.model_dump(by_alias=True, mode="json")
    direct["payloadJson"] = '{"nested":{"authorization":"redacted"}}'
    direct["payloadDigest"] = "sha256:" + sha256(direct["payloadJson"].encode()).hexdigest()
    with pytest.raises(ValidationError, match="sensitive"):
        type(draft).model_validate(direct)

    secret_value = "sk" + "-proj-" + ("a" * 24)
    with pytest.raises(ValueError, match="sensitive"):
        draft_journal_event(
            event_id="event_secret_value",
            partition_id="workspace_01",
            event_type="audit.note",
            task_contract_id="task_01",
            task_version=1,
            task_contract_digest=canonical_task_contract_digest(_task()),
            completion_epoch_id="epoch_01",
            admission_sequence=1,
            request_digest=D1,
            idempotency_key_digest=D2,
            fencing_token=7,
            actor_id="actor_01",
            policy_digest=D3,
            causation_id="turn_01",
            correlation_id="run_01",
            identity_digest=D4,
            payload={"note": secret_value},
        )

    oversized = draft.model_dump(by_alias=True, mode="json")
    oversized["payloadJson"] = json.dumps({"note": "x" * 1_048_576})
    oversized["payloadDigest"] = "sha256:" + sha256(oversized["payloadJson"].encode()).hexdigest()
    with pytest.raises(ValidationError, match="byte limit"):
        type(draft).model_validate(oversized)


@pytest.mark.parametrize(
    "sensitive_key",
    (
        "To" + "KeN",
        "Se" + "CrEt",
        "Pass" + "Word",
        "CoO" + "KiE",
        "Author" + "ization",
        "CreDen" + "Tial",
    ),
)
def test_every_sensitive_key_family_is_rejected_at_any_payload_depth(
    sensitive_key: str,
) -> None:
    with pytest.raises(ValueError, match="sensitive"):
        draft_journal_event(
            event_id="event_sensitive",
            partition_id="workspace_01",
            event_type="audit.note",
            task_contract_id="task_01",
            task_version=1,
            task_contract_digest=canonical_task_contract_digest(_task()),
            completion_epoch_id="epoch_01",
            admission_sequence=1,
            request_digest=D1,
            idempotency_key_digest=D2,
            fencing_token=7,
            actor_id="actor_01",
            policy_digest=D3,
            causation_id="turn_01",
            correlation_id="run_01",
            identity_digest=D4,
            payload={"outer": [{"inner": {sensitive_key: "redacted"}}]},
        )


def test_stored_journal_event_requires_store_assigned_chain_fields() -> None:
    draft = draft_journal_event(
        event_id="event_01",
        partition_id="workspace_01",
        event_type="audit.note",
        task_contract_id="task_01",
        task_version=1,
        task_contract_digest=canonical_task_contract_digest(_task()),
        completion_epoch_id="epoch_01",
        admission_sequence=1,
        request_digest=D1,
        idempotency_key_digest=D2,
        fencing_token=7,
        actor_id="actor_01",
        policy_digest=D3,
        causation_id="turn_01",
        correlation_id="run_01",
        identity_digest=D4,
        payload={},
    )
    stored = JournalEvent(
        **draft.model_dump(by_alias=True),
        sequence=1,
        previousHash=D0,
        eventHash=D5,
        rowChecksum=D6,
        createdAt=NOW,
    )
    assert stored.sequence == 1
    with pytest.raises(ValidationError):
        JournalEvent(
            **draft.model_dump(by_alias=True),
            sequence=0,
            previousHash=D0,
            eventHash=D5,
            rowChecksum=D6,
            createdAt=NOW,
        )


@pytest.mark.parametrize("candidate", ["완료 ✅ verified", "emoji 🧪와 한글"])
def test_finalization_manifest_exactly_partitions_codepoints_and_utf8(candidate: str) -> None:
    request = _finalization(candidate)
    assert request.claim_manifest.segments[-1].utf8_end == len(candidate.encode())


@pytest.mark.parametrize("mutation", ["gap", "overlap", "tail", "digest"])
def test_finalization_rejects_any_unreviewed_or_tampered_response_slice(mutation: str) -> None:
    request = _finalization()
    payload = request.model_dump(by_alias=True, mode="json")
    payload.pop("finalizationRequestDigest")
    payload.pop("responseClaimManifestDigest")
    segments = payload["claimManifest"]["segments"]
    assert isinstance(segments, list)
    if mutation == "gap":
        segments[1]["codepointStart"] += 1
    elif mutation == "overlap":
        segments[1]["utf8Start"] -= 1
    elif mutation == "tail":
        segments[-1]["codepointEnd"] -= 1
    else:
        segments[0]["textDigest"] = D9
    message = {
        "gap": "gap or overlap",
        "overlap": "gap or overlap",
        "tail": "disagree|omit",
        "digest": "textDigest",
    }[mutation]
    with pytest.raises(ValidationError, match=message):
        FinalizationRequest.model_validate(payload)


def test_finalization_requires_health_for_every_declared_dependency() -> None:
    request = _finalization()
    payload = request.model_dump(by_alias=True, mode="json")
    task_payload = payload["taskContract"]
    task_payload["dependencies"] = [
        {
            "dependencyId": "source_fetcher",
            "requiredSchema": "source.v1",
            "unavailableBehavior": "report unavailable",
        }
    ]
    task = TaskContractSnapshot.model_validate(task_payload)
    digest = canonical_task_contract_digest(task)
    payload["taskContract"] = task.model_dump(by_alias=True, mode="json")
    payload["taskContractDigest"] = digest
    payload["taskContractSnapshotRef"] = f"authority-task://{digest}"
    payload.pop("finalizationRequestDigest")

    with pytest.raises(ValidationError, match="dependencyHealth"):
        FinalizationRequest.model_validate(payload)


def test_backend_observation_is_self_digesting_and_preserves_remote_uncertainty() -> None:
    observation = BackendObservation.model_validate(_observation_payload())
    assert observation.observation_digest == canonical_backend_observation_digest(observation)
    assert observation.transmission_state is TransmissionState.PROVEN_NOT_SENT

    tampered = observation.model_dump(by_alias=True, mode="json")
    tampered["reasonCodes"] = ["different"]
    with pytest.raises(ValidationError, match="observationDigest"):
        BackendObservation.model_validate(tampered)

    remote = {
        **_observation_payload(),
        "attemptId": "try_reconcile",
        "providerId": "provider_01",
        "providerVersion": "2026-07",
        "providerCapabilitiesDigest": D1,
        "attemptKind": "reconciliation",
        "sourceAttemptId": "try_01",
        "reconcilesAttemptId": "try_01",
        "observedOutcome": "unknown",
        "transmissionState": "may_have_sent",
        "providerRequestIdDigest": D2,
        "workspacePublicationDigest": None,
    }
    reconciled = BackendObservation.model_validate(remote)
    assert reconciled.reconciles_attempt_id == "try_01"

    invalid = _observation_payload()
    invalid["providerId"] = "provider_01"
    with pytest.raises(ValidationError, match="provider"):
        BackendObservation.model_validate(invalid)

    missing_request_binding = {
        **_observation_payload(),
        "providerId": "provider_01",
        "providerVersion": "2026-07",
        "providerCapabilitiesDigest": D1,
        "observedOutcome": "unknown",
        "transmissionState": "may_have_sent",
        "providerRequestIdDigest": None,
    }
    with pytest.raises(ValidationError, match="providerRequestIdDigest"):
        BackendObservation.model_validate(missing_request_binding)

    unsupported_commit = {
        **remote,
        "attemptKind": "execution",
        "sourceAttemptId": None,
        "reconcilesAttemptId": None,
        "observedOutcome": "committed",
        "transmissionState": "accepted",
        "providerReceiptDigest": None,
    }
    with pytest.raises(ValidationError, match="providerReceiptDigest"):
        BackendObservation.model_validate(unsupported_commit)


def test_execution_start_binds_authority_executor_sandbox_and_only_token_digest() -> None:
    aliases = {field.alias or name for name, field in ExecutionStart.model_fields.items()}
    assert {
        "partitionId",
        "actionIntentDigest",
        "requestDigest",
        "authorityContractId",
        "authorityContractDigest",
        "fencingToken",
        "executorId",
        "executorVersion",
        "sandboxProfileDigest",
        "providerId",
        "providerVersion",
        "providerCapabilitiesDigest",
        "executionTokenDigest",
    }.issubset(aliases)
    assert "executionToken" not in aliases


def test_user_approval_consumption_binds_the_exact_preparation_contract() -> None:
    aliases = {field.alias or name for name, field in UserApprovalConsumption.model_fields.items()}
    assert {
        "approvalReceipt",
        "approvedSnapshot",
        "resumeBinding",
        "authorityContract",
        "authorityContractDigest",
        "expectedActionCompareVersion",
        "expectedAttemptCompareVersion",
        "expectedPartitionCompareVersion",
        "preparation",
        "consumedSnapshot",
        "consumedEvent",
    }.issubset(aliases)


def test_action_receipt_preserves_every_authoritative_observation_binding() -> None:
    observation = BackendObservation.model_validate(_observation_payload())
    receipt = ActionReceipt(
        schemaId="magi.action_receipt.v1",
        observation=observation,
        state=ActionState.COMMITTED,
        reasonCodes=("published",),
        stateRootBefore=D1,
        stateRootAfter=D2,
    )
    assert receipt.observation.executor_version == "1.0.0"
    assert receipt.observation.workspace_publication_digest == D9


def test_action_resolution_cannot_reuse_a_source_attempt_identity() -> None:
    with pytest.raises(ValidationError, match="resolutionAttemptId"):
        ActionResolution(
            schemaId="magi.action_resolution.v1",
            actionId="act_01",
            taskContractDigest=canonical_task_contract_digest(_task()),
            sourceAttemptIds=("try_01",),
            resolutionAttemptId="try_01",
            logicalState=ActionState.VERIFIED,
            reasonCodes=("replayed",),
        )


def test_verified_receipt_preserves_the_committed_physical_observation() -> None:
    observation = BackendObservation.model_validate(_observation_payload())

    receipt = ActionReceipt(
        schemaId="magi.action_receipt.v1",
        observation=observation,
        state=ActionState.VERIFIED,
        reasonCodes=("postcondition_verified",),
        stateRootBefore=D1,
        stateRootAfter=D2,
    )

    assert receipt.state is ActionState.VERIFIED
    assert receipt.observation.observed_outcome.value == "committed"


def test_attempt_recordings_reject_event_or_verification_binding_drift() -> None:
    observation = BackendObservation.model_validate(_observation_payload())
    committed = ActionReceipt(
        schemaId="magi.action_receipt.v1",
        observation=observation,
        state=ActionState.COMMITTED,
        reasonCodes=("published",),
        stateRootBefore=D1,
        stateRootAfter=D2,
    )
    with pytest.raises(ValidationError, match="terminalEvent.actionId"):
        AttemptObservationRecording(
            schemaVersion=1,
            receipt=committed,
            actionCompareVersion=2,
            attemptCompareVersion=4,
            partitionCompareVersion=3,
            observedEvent=_stored_event(
                "action.observed",
                event_id="event_observed",
            ),
            terminalEvent=_stored_event(
                "action.committed",
                event_id="event_committed",
                action_id="act_other",
            ),
        )

    verified = ActionReceipt(
        schemaId="magi.action_receipt.v1",
        observation=observation,
        state=ActionState.VERIFIED,
        reasonCodes=("postcondition_verified",),
        stateRootBefore=D1,
        stateRootAfter=D2,
    )
    binding = VerificationEvidenceBinding(
        schemaVersion=1,
        evidenceId="evidence_01",
        evidenceDigest=D1,
        verificationOutcome="passed",
        sourcePartitionId="workspace_01",
        sourceEventId="event_committed",
        sourceEventSequence=4,
        sourceEventHash=D2,
        sourceHeadSequence=4,
        sourceHeadHash=D2,
        sourceHeadCompareVersion=3,
        projectionCursors=(
            ProjectionCursorBinding(
                schemaVersion=1,
                partitionId="workspace_01",
                projectionId="evidence",
                requiredSequence=4,
                requiredEventHash=D2,
                acknowledgedSequence=4,
                acknowledgedEventHash=D2,
                stateRoot=D2,
                compareVersion=2,
            ),
        ),
        actionId="act_01",
        attemptId="try_01",
        taskContractDigest=canonical_task_contract_digest(_task()),
        requestDigest=D9,
        verifiedStateRoot=D2,
    )
    with pytest.raises(ValidationError, match="requestDigest"):
        AttemptVerificationRecording(
            schemaVersion=1,
            receipt=verified,
            binding=binding,
            actionCompareVersion=3,
            attemptCompareVersion=5,
            partitionCompareVersion=4,
            verificationEvent=_stored_event(
                "action.verified",
                event_id="event_verified",
            ),
        )


def test_unknown_is_an_honest_logical_resolution_but_committed_is_not() -> None:
    resolution = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId="act_01",
        taskContractDigest=canonical_task_contract_digest(_task()),
        sourceAttemptIds=("try_01",),
        resolutionAttemptId=None,
        logicalState=ActionState.UNKNOWN,
        reasonCodes=("irreconcilable_ambiguity",),
    )
    assert resolution.logical_state is ActionState.UNKNOWN

    with pytest.raises(ValidationError, match="logical"):
        ActionResolution(
            schemaId="magi.action_resolution.v1",
            actionId="act_01",
            taskContractDigest=canonical_task_contract_digest(_task()),
            sourceAttemptIds=("try_01",),
            resolutionAttemptId=None,
            logicalState=ActionState.COMMITTED,
            reasonCodes=("published",),
        )


def test_recovery_decision_binds_source_cas_and_has_stable_identity_digest() -> None:
    proof = NonExecutionProof.model_validate(_non_execution_proof_payload())
    context = RecoveryContext.model_validate(
        _recovery_context_payload(
            source_state=ActionState.UNKNOWN,
            source_version=3,
            include_non_execution_proof=False,
            recovery_strategy=RecoveryStrategy.PROVIDER_RECONCILIATION,
            effect_may_have_started=True,
        )
    )
    decision = RecoveryDecision(
        schemaId="magi.recovery_decision.v1",
        decisionId="recovery:recovery_epoch_01:act_01:try_01",
        recoveryEpochId="recovery_epoch_01",
        recoveryPlanDigest=context.recovery_plan_digest,
        recoveryOwnerId="worker_01",
        partitionId="workspace_01",
        expectedPartitionCompareVersion=8,
        recoveryFencingToken=11,
        actionId="act_01",
        expectedActionCompareVersion=5,
        taskContractDigest=canonical_task_contract_digest(_task()),
        sourceAttemptId="try_01",
        expectedSourceState=ActionState.UNKNOWN,
        expectedSourceVersion=3,
        sourceTerminal=True,
        terminalizeSourceTo=None,
        resolutionAttemptId="try_02",
        disposition=RecoveryDisposition.RECONCILE,
        contextDigest=context.context_digest,
        nonExecutionProofDigest=None,
        reasonCodes=("provider_lookup",),
    )
    assert decision.decision_digest == canonical_recovery_decision_digest(decision)
    assert tuple(item.value for item in RecoveryDisposition) == (
        "abort",
        "replay",
        "reconcile",
        "redo_commit",
        "rebuild_projections",
        "quarantine",
    )

    unsafe_replay = decision.model_dump(by_alias=True, mode="json")
    unsafe_replay["disposition"] = "replay"
    unsafe_replay.pop("decisionDigest")
    with pytest.raises(ValidationError, match="terminal|UNKNOWN|unknown"):
        RecoveryDecision.model_validate(unsafe_replay)

    prepared_context = _recovery_context_payload(
        source_state=ActionState.PREPARED,
        source_version=2,
        resolution_attempt_id=None,
    )
    validated = RecoveryContext.model_validate(prepared_context)
    assert validated.non_execution_proof is not None

    abort = RecoveryDecision(
        schemaId="magi.recovery_decision.v1",
        decisionId="recovery:recovery_epoch_01:act_01:try_01",
        recoveryEpochId=validated.recovery_epoch_id,
        recoveryPlanDigest=validated.recovery_plan_digest,
        recoveryOwnerId=validated.recovery_owner_id,
        partitionId=validated.partition_id,
        expectedPartitionCompareVersion=validated.expected_partition_compare_version,
        recoveryFencingToken=validated.recovery_fencing_token,
        actionId=validated.action_id,
        expectedActionCompareVersion=validated.expected_action_compare_version,
        taskContractDigest=validated.task_contract_digest,
        sourceAttemptId=validated.source_attempt_id,
        expectedSourceState=validated.expected_source_state,
        expectedSourceVersion=validated.expected_source_version,
        sourceTerminal=False,
        terminalizeSourceTo=ActionState.ABORTED,
        resolutionAttemptId=None,
        disposition=RecoveryDisposition.ABORT,
        contextDigest=validated.context_digest,
        nonExecutionProofDigest=validated.non_execution_proof.proof_digest,
        reasonCodes=("mechanically_not_started",),
    )
    assert validate_recovery_decision_context(abort, validated) is abort

    mismatched = abort.model_dump(by_alias=True, mode="json")
    mismatched["nonExecutionProofDigest"] = D3
    mismatched.pop("decisionDigest")
    with pytest.raises(ValueError, match="non-execution proof"):
        validate_recovery_decision_context(
            RecoveryDecision.model_validate(mismatched),
            validated,
        )


@pytest.mark.parametrize(
    ("source_state", "disposition", "workspace_commit_state"),
    [
        (ActionState.PREPARED, RecoveryDisposition.REPLAY, "none"),
        (ActionState.EXECUTING, RecoveryDisposition.REPLAY, "none"),
        (ActionState.OBSERVED, RecoveryDisposition.REPLAY, "none"),
    ],
)
def test_replay_of_handed_off_sources_requires_bound_non_execution_proof(
    source_state: ActionState,
    disposition: RecoveryDisposition,
    workspace_commit_state: str,
) -> None:
    context = RecoveryContext.model_validate(
        _recovery_context_payload(
            source_state=source_state,
            workspace_commit_state=workspace_commit_state,
        )
    )
    assert context.non_execution_proof is not None
    decision_payload = _recovery_decision_payload(
        context,
        disposition=disposition,
        proof_digest=context.non_execution_proof.proof_digest,
    )
    decision = RecoveryDecision.model_validate(decision_payload)
    assert validate_recovery_decision_context(decision, context) is decision

    decision_payload["nonExecutionProofDigest"] = None
    with pytest.raises(ValidationError, match="non-execution proof"):
        RecoveryDecision.model_validate(decision_payload)


def test_redo_commit_uses_durable_commit_decision_instead_of_non_execution_proof() -> None:
    payload = _recovery_context_payload(
        source_state=ActionState.EXECUTING,
        include_non_execution_proof=False,
        workspace_commit_state="decided",
        recovery_strategy=RecoveryStrategy.WORKSPACE_TRANSACTION,
    )
    payload["effectMayHaveStarted"] = True
    context = RecoveryContext.model_validate(payload)
    decision_payload = _recovery_decision_payload(
        context,
        disposition=RecoveryDisposition.REDO_COMMIT,
        proof_digest=None,
    )
    decision_payload["terminalizeSourceTo"] = ActionState.UNKNOWN

    decision = RecoveryDecision.model_validate(decision_payload)
    assert validate_recovery_decision_context(decision, context) is decision


def test_durable_commit_state_cannot_coexist_with_non_execution_proof() -> None:
    payload = _recovery_context_payload(workspace_commit_state="decided")

    with pytest.raises(ValidationError, match="durable workspace commit"):
        RecoveryContext.model_validate(payload)


def test_observed_replay_requires_bound_non_execution_proof() -> None:
    context = RecoveryContext.model_validate(
        _recovery_context_payload(
            source_state=ActionState.OBSERVED,
            source_version=4,
            include_non_execution_proof=False,
        )
    )
    decision_payload = _recovery_decision_payload(
        context,
        disposition=RecoveryDisposition.REPLAY,
        proof_digest=None,
    )

    with pytest.raises(ValidationError, match="non-execution proof"):
        RecoveryDecision.model_validate(decision_payload)


def test_quarantine_cannot_disguise_a_proofless_abort() -> None:
    payload = _recovery_context_payload(
        source_state=ActionState.EXECUTING,
        include_non_execution_proof=False,
        resolution_attempt_id=None,
    )
    payload["effectMayHaveStarted"] = True
    context = RecoveryContext.model_validate(payload)
    decision_payload = _recovery_decision_payload(
        context,
        disposition=RecoveryDisposition.QUARANTINE,
        proof_digest=None,
    )

    with pytest.raises(ValidationError, match="QUARANTINE|preserve|non-execution proof"):
        RecoveryDecision.model_validate(decision_payload)


def test_proposed_recovery_cannot_skip_denial_by_terminalizing_aborted() -> None:
    context = RecoveryContext.model_validate(
        _recovery_context_payload(
            source_state=ActionState.PROPOSED,
            source_version=0,
            resolution_attempt_id=None,
        )
    )
    assert context.non_execution_proof is not None
    decision_payload = _recovery_decision_payload(
        context,
        disposition=RecoveryDisposition.ABORT,
        proof_digest=context.non_execution_proof.proof_digest,
    )

    with pytest.raises(ValidationError, match="terminalizeSourceTo"):
        RecoveryDecision.model_validate(decision_payload)


def test_recovery_context_derives_replay_safety_from_bound_action_intent() -> None:
    payload = _recovery_context_payload()
    payload["replaySafe"] = False

    with pytest.raises(ValidationError, match="replaySafe|recoveryStrategy"):
        RecoveryContext.model_validate(payload)

    workspace_payload = _recovery_context_payload(
        recovery_strategy=RecoveryStrategy.WORKSPACE_TRANSACTION,
    )
    context = RecoveryContext.model_validate(workspace_payload)
    assert context.non_execution_proof is not None
    decision = RecoveryDecision.model_validate(
        _recovery_decision_payload(
            context,
            disposition=RecoveryDisposition.REPLAY,
            proof_digest=context.non_execution_proof.proof_digest,
        )
    )
    with pytest.raises(ValueError, match="recoveryStrategy|REPLAY"):
        validate_recovery_decision_context(decision, context)


def test_recovery_context_requires_fresh_cross_bound_recovery_authority() -> None:
    payload = _recovery_context_payload()
    authority = payload["recoveryAuthority"]
    assert isinstance(authority, AuthorityContract)
    expired_payload = authority.model_dump(by_alias=True, mode="python")
    expired_payload["expiresAt"] = NOW - timedelta(seconds=1)
    expired = AuthorityContract.model_validate(expired_payload)
    payload["recoveryAuthority"] = expired
    payload["recoveryAuthorityDigest"] = canonical_authority_contract_digest(expired)

    with pytest.raises(ValidationError, match="expired|fresh"):
        RecoveryContext.model_validate(payload)

    payload = _recovery_context_payload()
    payload["recoveryAuthorityDigest"] = D0
    with pytest.raises(ValidationError, match="recoveryAuthorityDigest"):
        RecoveryContext.model_validate(payload)


@pytest.mark.parametrize(
    ("proof_field", "bad_value"),
    [
        ("partitionId", "workspace_other"),
        ("actionId", "act_other"),
        ("expectedSourceVersion", 3),
        ("actionSnapshotDigest", D4),
        ("attemptSnapshotDigest", D5),
        ("journalHeadDigest", D6),
    ],
)
def test_non_execution_proof_cross_binds_recovery_source_snapshots(
    proof_field: str,
    bad_value: object,
) -> None:
    payload = _recovery_context_payload()
    proof_payload = payload["nonExecutionProof"]
    assert isinstance(proof_payload, dict)
    proof_payload[proof_field] = bad_value
    with pytest.raises(ValidationError, match="non-execution proof"):
        RecoveryContext.model_validate(payload)


def test_executing_non_execution_proof_requires_the_durable_handoff_record() -> None:
    payload = _non_execution_proof_payload(source_state=ActionState.EXECUTING)
    payload["executionHandoffRecorded"] = False
    with pytest.raises(ValidationError, match="durable records"):
        NonExecutionProof.model_validate(payload)


def test_recovery_attempt_and_self_digested_sequences_reject_ambiguous_inputs() -> None:
    context_payload = _recovery_context_payload()
    context_payload["resolutionAttemptId"] = context_payload["sourceAttemptId"]
    with pytest.raises(ValidationError, match="resolutionAttemptId"):
        RecoveryContext.model_validate(context_payload)

    context = RecoveryContext.model_validate(_recovery_context_payload())
    assert context.non_execution_proof is not None
    decision_payload = _recovery_decision_payload(
        context,
        disposition=RecoveryDisposition.REPLAY,
        proof_digest=context.non_execution_proof.proof_digest,
    )
    decision_payload["resolutionAttemptId"] = decision_payload["sourceAttemptId"]
    with pytest.raises(ValidationError, match="resolutionAttemptId"):
        RecoveryDecision.model_validate(decision_payload)

    decision_payload["resolutionAttemptId"] = "try_02"
    decision_payload["reasonCodes"] = {"mechanically_not_started"}
    with pytest.raises(ValidationError, match="ordered"):
        RecoveryDecision.model_validate(decision_payload)


def test_partition_recovery_gate_binds_a_stable_plan_across_takeover() -> None:
    required = (
        RequiredProjection(
            schemaVersion=1,
            partitionId="workspace_01",
            projectionId="lineage",
        ),
    )
    plan = PartitionRecoveryPlan(
        schemaVersion=1,
        recoveryEpochId="recovery_epoch_01",
        partitionId="workspace_01",
        taskContractDigest=canonical_task_contract_digest(_task()),
        selectedSourceAttemptIds=("try_01",),
        requiredProjections=required,
    )
    gate = PartitionGate(
        schemaVersion=1,
        partitionId="workspace_01",
        state="recovering",
        recoveryEpochId=plan.recovery_epoch_id,
        recoveryPlanDigest=plan.recovery_plan_digest,
        recoveryOwnerId="worker_02",
        recoveryFencingToken=12,
        quarantineReasonDigest=None,
        compareVersion=9,
    )
    assert gate.recovery_plan_digest == plan.recovery_plan_digest

    payload = gate.model_dump(by_alias=True, mode="json")
    payload["recoveryFencingToken"] = 0
    with pytest.raises(ValidationError, match="fencing"):
        PartitionGate.model_validate(payload)


def test_durable_snapshots_preserve_lease_and_outbox_high_water_bindings() -> None:
    lease = LeaseSnapshot(
        schemaVersion=1,
        partitionId="workspace_01",
        leaseName="mutation",
        state=LeaseState.RELEASED,
        ownerId=None,
        fencingToken=7,
        highWaterFencingToken=9,
        expiresAt=None,
        compareVersion=4,
    )
    assert lease.high_water_fencing_token >= lease.fencing_token

    with pytest.raises(ValidationError):
        LeaseSnapshot(
            schemaVersion=1,
            partitionId="workspace_01",
            leaseName="mutation",
            state=LeaseState.RELEASED,
            ownerId=None,
            fencingToken=10,
            highWaterFencingToken=9,
            expiresAt=None,
            compareVersion=4,
        )

    item = OutboxItem(
        schemaVersion=1,
        outboxId="outbox_01",
        partitionId="task:task_01:1",
        subjectId="completion_01",
        subjectDigest=D1,
        eventId="event_01",
        eventSequence=7,
        eventHash=D2,
        kind="final_response",
        payloadDigest="sha256:" + sha256(b"{}").hexdigest(),
        payloadJson="{}",
        state=OutboxState.CLAIMED,
        claimOwnerId="delivery_01",
        claimFencingToken=8,
        claimExpiresAt=NOW + timedelta(minutes=1),
        deliveryAttempt=1,
        acknowledgementDigest=None,
        compareVersion=2,
    )
    assert item.claim_owner_id == "delivery_01"


def test_action_and_attempt_snapshots_reject_cross_identity_or_missing_proof() -> None:
    wrong_resolution = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId="act_other",
        taskContractDigest=canonical_task_contract_digest(_task()),
        sourceAttemptIds=("try_01",),
        resolutionAttemptId=None,
        logicalState=ActionState.ABORTED,
        reasonCodes=("not_started",),
    )
    with pytest.raises(ValidationError, match="resolution"):
        ActionSnapshot(
            schemaVersion=1,
            actionId="act_01",
            partitionId="workspace_01",
            taskContractDigest=canonical_task_contract_digest(_task()),
            completionEpochId="epoch_01",
            admissionSequence=1,
            intentDigest=D1,
            resolution=wrong_resolution,
            compareVersion=2,
        )

    observation_payload = _observation_payload()
    observation_payload["actionId"] = "act_other"
    wrong_observation = BackendObservation.model_validate(observation_payload)
    with pytest.raises(ValidationError, match="observation"):
        AttemptSnapshot(
            schemaVersion=1,
            actionId="act_01",
            attemptId="try_01",
            partitionId="workspace_01",
            taskContractDigest=canonical_task_contract_digest(_task()),
            actionIntentDigest=D0,
            requestDigest=D3,
            state=ActionState.COMMITTED,
            authorityDigest=D4,
            fencingToken=7,
            observation=wrong_observation,
            verification=None,
            compareVersion=4,
        )

    with pytest.raises(ValidationError, match="verification"):
        AttemptSnapshot(
            schemaVersion=1,
            actionId="act_01",
            attemptId="try_01",
            partitionId="workspace_01",
            taskContractDigest=canonical_task_contract_digest(_task()),
            actionIntentDigest=D0,
            requestDigest=D3,
            state=ActionState.VERIFIED,
            authorityDigest=D4,
            fencingToken=7,
            observation=BackendObservation.model_validate(_observation_payload()),
            verification=None,
            compareVersion=5,
        )


def test_user_decision_snapshot_preserves_canonical_request_bytes_and_pointers() -> None:
    request = _decision_request()
    request_json = json.dumps(
        request.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    request_digest = "sha256:" + sha256(request_json.encode()).hexdigest()
    snapshot = UserDecisionSnapshot(
        schemaVersion=1,
        request=request,
        requestJson=request_json,
        decisionRequestDigest=request_digest,
        state="pending",
        approvalReceiptDigest=None,
        latestReceiptId=None,
        latestReceiptDigest=None,
        compareVersion=0,
    )
    assert snapshot.request_json == request_json

    with pytest.raises(ValidationError, match="approvalReceiptDigest"):
        UserDecisionSnapshot(
            schemaVersion=1,
            request=request,
            requestJson=request_json,
            decisionRequestDigest=request_digest,
            state="approved",
            approvalReceiptDigest=None,
            latestReceiptId="receipt_01",
            latestReceiptDigest=D1,
            compareVersion=1,
        )


def test_user_decision_recording_cannot_hide_terminal_action_side_effects() -> None:
    request = _decision_request()
    request_json = json.dumps(
        request.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    receipt = UserDecisionReceipt(
        schemaId="magi.user_decision_receipt.v1",
        receiptId="receipt_01",
        decisionRequestId=request.decision_request_id,
        decision="deny",
        authenticatedActorId=request.principal_id,
        authenticationKeyId="key_01",
        authenticationContextDigest=D1,
        authenticationNonceDigest=D2,
        transportReceiptDigest=D3,
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
        expiresAt=NOW + timedelta(minutes=4),
        revokesReceiptDigest=None,
    )
    receipt_json = json.dumps(
        receipt.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    receipt_digest = "sha256:" + sha256(receipt_json.encode()).hexdigest()
    snapshot = UserDecisionSnapshot(
        schemaVersion=1,
        request=request,
        requestJson=request_json,
        decisionRequestDigest="sha256:" + sha256(request_json.encode()).hexdigest(),
        state="denied",
        approvalReceiptDigest=None,
        latestReceiptId=receipt.receipt_id,
        latestReceiptDigest=receipt_digest,
        compareVersion=1,
    )

    with pytest.raises(
        ValidationError,
        match="verifiedReceipt|previousSnapshot|payload|terminal action records",
    ):
        UserDecisionRecording(
            schemaVersion=1,
            receipt=receipt,
            appliedFromState="pending",
            appliedToState="denied",
            recordedEvent=_stored_event(
                "user_decision.recorded",
                event_id="event_decision_denied",
                request_digest=request.normalized_request_digest,
                policy_digest=request.policy_digest,
            ),
            currentSnapshot=snapshot,
            replayed=False,
        )


def test_system_decision_transitions_bind_cas_and_never_accept_caller_time() -> None:
    invalidation = UserDecisionInvalidationRequest(
        schemaVersion=1,
        decisionRequestId="decision_01",
        taskContractDigest=canonical_task_contract_digest(_task()),
        actionId="act_01",
        partitionId="workspace_01",
        expectedDecisionCompareVersion=1,
        expectedActionCompareVersion=2,
        expectedPartitionCompareVersion=3,
        invalidatedBindingKind="policy",
        previousBindingDigest=D1,
        currentBindingDigest=D2,
        reasonCodes=("policy_changed",),
    )
    assert invalidation.previous_binding_digest != invalidation.current_binding_digest

    payload = invalidation.model_dump(by_alias=True, mode="json")
    payload["currentBindingDigest"] = D1
    with pytest.raises(ValidationError, match="changed binding"):
        UserDecisionInvalidationRequest.model_validate(payload)

    expiration_aliases = {
        field.alias or name for name, field in UserDecisionExpirationRequest.model_fields.items()
    }
    assert "partitionId" in expiration_aliases
    assert not any("At" in alias or "now" in alias.lower() for alias in expiration_aliases)

    resolution = ActionResolution(
        schemaId="magi.action_resolution.v1",
        actionId="act_01",
        taskContractDigest=canonical_task_contract_digest(_task()),
        sourceAttemptIds=("try_01",),
        resolutionAttemptId=None,
        logicalState=ActionState.DENIED,
        reasonCodes=("policy_changed",),
    )
    request = _decision_request()
    request_json = json.dumps(
        request.model_dump(by_alias=True, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    request_digest = "sha256:" + sha256(request_json.encode()).hexdigest()
    previous_snapshot = UserDecisionSnapshot(
        schemaVersion=1,
        request=request,
        requestJson=request_json,
        decisionRequestDigest=request_digest,
        state="pending",
        approvalReceiptDigest=None,
        latestReceiptId=None,
        latestReceiptDigest=None,
        compareVersion=1,
    )
    current_snapshot = UserDecisionSnapshot(
        schemaVersion=1,
        request=request,
        requestJson=request_json,
        decisionRequestDigest=request_digest,
        state="invalidated",
        approvalReceiptDigest=None,
        latestReceiptId=None,
        latestReceiptDigest=None,
        compareVersion=2,
    )
    with pytest.raises(ValidationError, match="payload|terminal action events"):
        UserDecisionTransition(
            schemaVersion=1,
            request=invalidation,
            fromState="pending",
            toState="invalidated",
            previousSnapshot=previous_snapshot,
            currentSnapshot=current_snapshot,
            decisionCompareVersion=2,
            actionCompareVersion=3,
            partitionCompareVersion=4,
            transitionEvent=_stored_event(
                "user_decision.invalidated",
                event_id="event_decision_invalidated",
            ),
            actionResolution=resolution,
        )


def test_internal_schema_versions_and_datetimes_reject_coercible_wire_values() -> None:
    declaration = _declaration().model_dump(by_alias=True, mode="json")
    declaration["schemaVersion"] = True
    with pytest.raises(ValidationError, match="schemaVersion"):
        EffectDeclarationBinding.model_validate(declaration)

    for invalid_datetime in (1_735_689_600, "1735689600"):
        with pytest.raises(ValidationError, match="datetime"):
            LeaseSnapshot(
                schemaVersion=1,
                partitionId="workspace_01",
                leaseName="mutation",
                state=LeaseState.HELD,
                ownerId="worker_01",
                fencingToken=10,
                highWaterFencingToken=10,
                expiresAt=invalid_datetime,
                compareVersion=4,
            )


def test_evidence_draft_is_distinct_from_stored_node_and_spans_bind_both_offsets() -> None:
    source = "가나다🧪"
    selected = "나다"
    span = SourceSpan(
        spanId="span_01",
        sourceSnapshotId="snapshot_01",
        sourceSnapshotDigest="sha256:" + sha256(source.encode()).hexdigest(),
        codepointStart=1,
        codepointEnd=3,
        utf8Start=len("가".encode()),
        utf8End=len("가나다".encode()),
        textDigest="sha256:" + sha256(selected.encode()).hexdigest(),
    )
    validate_source_span(span, source)

    for field, value, message in (
        ("sourceSnapshotDigest", D9, "sourceSnapshotDigest"),
        ("codepointStart", 0, "ranges disagree"),
        ("utf8Start", 0, "ranges disagree"),
        ("textDigest", D9, "textDigest"),
    ):
        malformed = span.model_dump(by_alias=True, mode="json")
        malformed[field] = value
        with pytest.raises(ValueError, match=message):
            validate_source_span(SourceSpan.model_validate(malformed), source)

    coverage = CoverageDescriptor(
        coverageKind="journal_window",
        journalWindow=JournalCoverageWindow(
            partitionId="workspace_01",
            startSequence=1,
            endSequence=4,
            startEventHash=D1,
            endEventHash=D2,
        ),
        searchedResourceRefs=(WORKSPACE_A_REF,),
    )
    freshness = FreshnessBinding(
        rule="same_state_root",
        stateRoot=D3,
        observedAt=NOW,
    )
    draft = EvidenceNodeDraft(
        schemaVersion=1,
        evidenceId="evidence_01",
        kind="source_span",
        semanticClass=EvidenceSemanticClass.OBSERVATION,
        sessionId="session_01",
        turnId="turn_01",
        runId="run_01",
        taskContractId="task_01",
        taskVersion=1,
        taskContractDigest=canonical_task_contract_digest(_task()),
        completionEpochId="epoch_01",
        requirementIds=("req_01",),
        claimIds=("claim_weather",),
        actionId="act_01",
        attemptId="try_01",
        requestDigest=D4,
        authorityDigest=D5,
        policyDigest=D6,
        producerId="research-fetch",
        producerVersion="1.0.0",
        producerAlive=True,
        producerStatus=DependencyStatus.CLEAN,
        producerSchemaVersion="1",
        producerInvocationEvidenceId="producer_invocation_01",
        producerInvocationEvidenceDigest=D0,
        partitionId="workspace_01",
        admissionSequence=1,
        workspaceGeneration=2,
        stateRoot=D3,
        sourceSnapshotId="snapshot_01",
        sourceSnapshotDigest=span.source_snapshot_digest,
        sourceSpans=(span,),
        researchSource=ResearchSourceBinding(
            schemaVersion=1,
            sourceSnapshotId="snapshot_01",
            sourceSnapshotDigest=span.source_snapshot_digest,
            sourceClass="primary",
            trustTier="official",
            retrievedAt=NOW,
            sourceVersion="2026-07-15T12:00:00Z",
            truncated=False,
        ),
        contentDigest=D7,
        toolInputDigest=D8,
        toolOutputDigest=D9,
        parentEvidenceIds=(),
        coverage=coverage,
        freshness=freshness,
        publicRedactionClass="public",
        reasonCodes=("retrieved",),
        createdAt=NOW,
        producerPayloadDigest=D0,
    )
    stored = EvidenceNode(
        **draft.model_dump(by_alias=True),
        journalSequence=4,
        journalEventHash=D2,
    )
    assert stored.journal_sequence == 4
    assert "journalSequence" not in draft.model_dump(by_alias=True)

    event_draft = _draft_lifecycle_journal_event(
        event_id="event_evidence",
        partition_id="workspace_01",
        event_type="evidence.recorded",
        action_id="act_01",
        attempt_id="try_01",
        task_contract_id="task_01",
        task_version=1,
        task_contract_digest=stored.task_contract_digest,
        completion_epoch_id="epoch_01",
        admission_sequence=1,
        request_digest=D4,
        idempotency_key_digest=D8,
        fencing_token=7,
        actor_id="actor_01",
        policy_digest=D6,
        causation_id="event_observed",
        correlation_id="run_01",
        identity_digest=D8,
        payload={
            "evidenceId": stored.evidence_id,
            "evidenceNodeDigest": canonical_evidence_node_digest(stored),
            "evidenceEdgesDigest": canonical_evidence_edges_digest(()),
        },
    )
    evidence_event = JournalEvent(
        **event_draft.model_dump(by_alias=True),
        sequence=4,
        previousHash=D1,
        eventHash=D2,
        rowChecksum=D3,
        createdAt=NOW,
    )
    recording = EvidenceRecordRecording(
        schemaVersion=1,
        node=stored,
        edges=(),
        event=evidence_event,
        projectionCompareVersion=3,
    )
    assert recording.node.evidence_id == "evidence_01"

    mutated_payload = stored.model_dump(by_alias=True, mode="json")
    mutated_payload["contentDigest"] = D8
    mutated_node = EvidenceNode.model_validate(mutated_payload)
    with pytest.raises(ValidationError, match="payloadDigest"):
        EvidenceRecordRecording(
            schemaVersion=1,
            node=mutated_node,
            edges=(),
            event=evidence_event,
            projectionCompareVersion=3,
        )

    mismatched_event = evidence_event.model_dump(by_alias=True, mode="json")
    mismatched_event["actionId"] = "act_other"
    with pytest.raises(ValidationError, match="event.actionId"):
        EvidenceRecordRecording(
            schemaVersion=1,
            node=stored,
            edges=(),
            event=JournalEvent.model_validate(mismatched_event),
            projectionCompareVersion=3,
        )


def test_evidence_edges_are_typed_and_cannot_self_reference() -> None:
    edge = EvidenceEdge(
        schemaVersion=1,
        edgeId="edge_01",
        sourceEvidenceId="evidence_source",
        targetEvidenceId="evidence_target",
        kind="supports",
    )
    assert edge.kind == "supports"
    with pytest.raises(ValidationError, match="cannot target"):
        EvidenceEdge(
            schemaVersion=1,
            edgeId="edge_self",
            sourceEvidenceId="evidence_source",
            targetEvidenceId="evidence_source",
            kind="derived_from",
        )


def test_verification_binding_carries_exact_source_head_cursor_and_attempt_vectors() -> None:
    cursor = ProjectionCursorBinding(
        schemaVersion=1,
        partitionId="workspace_01",
        projectionId="evidence",
        requiredSequence=4,
        requiredEventHash=D2,
        acknowledgedSequence=4,
        acknowledgedEventHash=D2,
        stateRoot=D3,
        compareVersion=2,
    )
    binding = VerificationEvidenceBinding(
        schemaVersion=1,
        evidenceId="evidence_01",
        evidenceDigest=D1,
        verificationOutcome="passed",
        sourcePartitionId="workspace_01",
        sourceEventId="event_01",
        sourceEventSequence=4,
        sourceEventHash=D2,
        sourceHeadSequence=4,
        sourceHeadHash=D2,
        sourceHeadCompareVersion=3,
        projectionCursors=(cursor,),
        actionId="act_01",
        attemptId="try_01",
        taskContractDigest=canonical_task_contract_digest(_task()),
        requestDigest=D4,
        verifiedStateRoot=D3,
    )
    assert binding.projection_cursors == (cursor,)

    unrelated = binding.model_dump(by_alias=True, mode="json")
    unrelated["projectionCursors"][0]["partitionId"] = "other_partition"
    with pytest.raises(ValidationError, match="source event cursor"):
        VerificationEvidenceBinding.model_validate(unrelated)

    stale_root = binding.model_dump(by_alias=True, mode="json")
    stale_root["projectionCursors"][0]["stateRoot"] = D4
    with pytest.raises(ValidationError, match="verifiedStateRoot"):
        VerificationEvidenceBinding.model_validate(stale_root)

    forked = binding.model_dump(by_alias=True, mode="json")
    forked["sourceHeadHash"] = D3
    with pytest.raises(ValidationError, match="hashes must match"):
        VerificationEvidenceBinding.model_validate(forked)


def test_projection_cursor_must_cover_not_trail_the_required_sequence() -> None:
    ahead = ProjectionCursorBinding(
        schemaVersion=1,
        partitionId="workspace_01",
        projectionId="evidence",
        requiredSequence=4,
        requiredEventHash=D2,
        acknowledgedSequence=5,
        acknowledgedEventHash=D3,
        acknowledgedAncestry=(
            JournalChainLink(sequence=4, previousHash=D0, eventHash=D2),
            JournalChainLink(sequence=5, previousHash=D2, eventHash=D3),
        ),
        stateRoot=D4,
        compareVersion=2,
    )
    assert ahead.acknowledged_sequence == 5

    forked = ahead.model_dump(by_alias=True, mode="json")
    forked["acknowledgedAncestry"][1]["previousHash"] = D9
    with pytest.raises(ValidationError, match="ancestry"):
        ProjectionCursorBinding.model_validate(forked)

    with pytest.raises(ValidationError, match="cover"):
        ProjectionCursorBinding(
            schemaVersion=1,
            partitionId="workspace_01",
            projectionId="evidence",
            requiredSequence=4,
            requiredEventHash=D2,
            acknowledgedSequence=3,
            acknowledgedEventHash=D1,
            stateRoot=D4,
            compareVersion=2,
        )


def test_workspace_commit_contract_never_contains_a_private_manifest_path() -> None:
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_ROOT_REF,
        authority_partition_id="workspace_01",
        generation=1,
        state_root=D1,
    )
    request = WorkspaceCommitDecisionRequest(
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
        stateRootBefore=D1,
        stateRootAfter=D2,
        decisionFencingToken=7,
        mutationPlanDigest=D3,
        stagingManifestRef="authority-manifest://sha256:" + "4" * 64,
        stagingManifestDigest=D4,
        changedResourceRefsDigest=canonical_resource_refs_digest((WORKSPACE_A_REF,)),
        workspaceViewBindingDigest=view_digest,
        changedResourceRefs=(WORKSPACE_A_REF,),
    )
    alias_payload = request.model_dump(by_alias=True, mode="json")
    assert "schemaVersion" not in alias_payload
    assert "path" not in " ".join(alias_payload).lower()

    commit_snapshot = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=request,
        state="decided",
        activeFencingToken=7,
        activeFenceEventId="event_commit_decided",
        activeFenceEventSequence=1,
        activeFenceEventHash=D1,
        commitCompareVersion=1,
    )
    with pytest.raises(ValidationError, match="commitEvent.actionId"):
        WorkspaceCommitDecision(
            schemaVersion=1,
            snapshot=commit_snapshot,
            workspaceCompareVersion=4,
            commitEvent=_stored_event(
                "workspace.commit_decided",
                event_id="event_commit_decided",
                action_id="act_other",
            ),
        )

    snapshot = WorkspaceSnapshot(
        schemaVersion=1,
        workspaceId="workspace_01",
        workspaceRef=WORKSPACE_ROOT_REF,
        authorityPartitionId="workspace_01",
        currentGeneration=1,
        stateRoot=D1,
        workspaceViewBindingDigest=view_digest,
        publicationState="ready",
        activeCommitId=None,
        pendingGeneration=None,
        pendingStateRoot=None,
        pendingWorkspaceViewBindingDigest=None,
        activeFencingToken=None,
        compareVersion=3,
    )
    assert snapshot.workspace_view_binding_digest == request.workspace_view_binding_digest

    tampered = snapshot.model_dump(by_alias=True, mode="json")
    tampered["workspaceViewBindingDigest"] = D6
    with pytest.raises(ValidationError, match="workspaceViewBindingDigest"):
        WorkspaceSnapshot.model_validate(tampered)

    publishing = WorkspaceSnapshot(
        schemaVersion=1,
        workspaceId="workspace_01",
        workspaceRef=WORKSPACE_ROOT_REF,
        authorityPartitionId="workspace_01",
        currentGeneration=1,
        stateRoot=D1,
        workspaceViewBindingDigest=view_digest,
        publicationState="publishing",
        activeCommitId="commit_01",
        pendingGeneration=2,
        pendingStateRoot=D2,
        pendingWorkspaceViewBindingDigest=canonical_workspace_view_binding_digest(
            workspace_id="workspace_01",
            workspace_ref=WORKSPACE_ROOT_REF,
            authority_partition_id="workspace_01",
            generation=2,
            state_root=D2,
        ),
        activeFencingToken=7,
        compareVersion=4,
    )
    assert publishing.pending_generation == publishing.current_generation + 1

    assert "changed_resource_refs" in WorkspacePublicationReceipt.model_fields
    with pytest.raises(ValidationError, match="publicationEvent.eventType"):
        WorkspacePublicationReceipt(
            schemaVersion=1,
            activeCommitSnapshot=commit_snapshot,
            commitId="commit_01",
            commitDecisionDigest=D6,
            transactionId="txn_01",
            workspaceId="workspace_01",
            workspaceRef=WORKSPACE_ROOT_REF,
            authorityPartitionId="workspace_01",
            actionId="act_01",
            attemptId="try_01",
            expectedWorkspaceCompareVersion=4,
            expectedCommitCompareVersion=1,
            activeFencingToken=7,
            activeFenceEventId="event_commit_decided",
            publishedGeneration=2,
            stateRootBefore=D1,
            stateRootAfter=D2,
            changedResourceRefs=(WORKSPACE_A_REF,),
            changedResourceRefsDigest=canonical_resource_refs_digest((WORKSPACE_A_REF,)),
            workspaceViewBindingDigest=canonical_workspace_view_binding_digest(
                workspace_id="workspace_01",
                workspace_ref=WORKSPACE_ROOT_REF,
                authority_partition_id="workspace_01",
                generation=2,
                state_root=D2,
            ),
            durabilityEvidenceDigest=D5,
            observationRefs=("fsync://commit_01",),
            workspaceCompareVersion=5,
            commitCompareVersion=2,
            publicationEvent=_stored_event(
                "workspace.commit_decided",
                event_id="event_published",
            ),
        )


def test_workspace_commit_decision_event_commits_the_exact_request() -> None:
    decision = _workspace_commit_decision()
    payload = decision.model_dump(by_alias=True, mode="json")
    payload["commitEvent"]["payloadJson"] = "{}"
    payload["commitEvent"]["payloadDigest"] = "sha256:" + sha256(b"{}").hexdigest()

    with pytest.raises(ValidationError, match="commitEvent payload"):
        WorkspaceCommitDecision.model_validate(payload)


def test_initial_workspace_commit_decision_starts_at_commit_version_one() -> None:
    decision = _workspace_commit_decision()
    payload = decision.model_dump(by_alias=True, mode="json")
    payload["snapshot"]["commitCompareVersion"] = 2

    with pytest.raises(ValidationError, match="commitCompareVersion.*1"):
        WorkspaceCommitDecision.model_validate(payload)


def test_workspace_commit_snapshot_persists_active_fence_event_provenance() -> None:
    decision = _workspace_commit_decision()

    assert decision.snapshot.active_fence_event_id == decision.commit_event.event_id
    assert decision.snapshot.active_fence_event_sequence == decision.commit_event.sequence
    assert decision.snapshot.active_fence_event_hash == decision.commit_event.event_hash


def test_workspace_view_digest_has_type_and_version_domain_separation() -> None:
    fields = {
        "workspaceId": "workspace_01",
        "workspaceRef": WORKSPACE_ROOT_REF,
        "authorityPartitionId": "workspace_01",
        "generation": 1,
        "stateRoot": D1,
    }
    legacy = (
        "sha256:"
        + sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )

    assert (
        canonical_workspace_view_binding_digest(
            workspace_id="workspace_01",
            workspace_ref=WORKSPACE_ROOT_REF,
            authority_partition_id="workspace_01",
            generation=1,
            state_root=D1,
        )
        != legacy
    )


def test_workspace_publication_binds_decision_fence_and_durability_event() -> None:
    decision = _workspace_commit_decision()
    decision_request = decision.snapshot.request
    decision_digest = canonical_workspace_commit_decision_digest(decision)
    observation = WorkspacePublicationObservation(
        schemaVersion=1,
        activeCommitSnapshot=decision.snapshot,
        commitId=decision_request.commit_id,
        commitDecisionDigest=decision_digest,
        transactionId=decision_request.transaction_id,
        workspaceId=decision_request.workspace_id,
        workspaceRef=decision_request.workspace_ref,
        authorityPartitionId=decision_request.authority_partition_id,
        actionId=decision_request.action_id,
        attemptId=decision_request.attempt_id,
        expectedWorkspaceCompareVersion=decision.workspace_compare_version,
        expectedCommitCompareVersion=decision.snapshot.commit_compare_version,
        activeFencingToken=decision.snapshot.active_fencing_token,
        activeFenceEventId=decision.commit_event.event_id,
        publishedGeneration=decision_request.target_generation,
        stateRootBefore=decision_request.state_root_before,
        stateRootAfter=decision_request.state_root_after,
        changedResourceRefs=decision_request.changed_resource_refs,
        changedResourceRefsDigest=decision_request.changed_resource_refs_digest,
        workspaceViewBindingDigest=canonical_workspace_view_binding_digest(
            workspace_id=decision_request.workspace_id,
            workspace_ref=decision_request.workspace_ref,
            authority_partition_id=decision_request.authority_partition_id,
            generation=decision_request.target_generation,
            state_root=decision_request.state_root_after,
        ),
        durabilityEvidenceDigest=D5,
        observationRefs=("fsync://commit_01",),
    )
    publication_event = _stored_event(
        "workspace.published",
        event_id="event_published",
        action_id=decision_request.action_id,
        attempt_id=decision_request.attempt_id,
        fencing_token=decision.snapshot.active_fencing_token,
        causation_id=decision.commit_event.event_id,
        payload={
            "commitId": observation.commit_id,
            "transactionId": observation.transaction_id,
            "commitDecisionDigest": observation.commit_decision_digest,
            "publicationObservationDigest": observation.publication_observation_digest,
            "durabilityEvidenceDigest": observation.durability_evidence_digest,
        },
        sequence=2,
        previous_hash=decision.commit_event.event_hash,
        event_hash=D2,
        row_checksum=D3,
    )
    receipt = WorkspacePublicationReceipt(
        **observation.model_dump(by_alias=True),
        workspaceCompareVersion=5,
        commitCompareVersion=2,
        publicationEvent=publication_event,
    )
    assert receipt.active_fencing_token == decision.snapshot.active_fencing_token

    transplanted = receipt.model_dump(by_alias=True, mode="json")
    transplanted.pop("publicationObservationDigest")
    transplanted["commitDecisionDigest"] = D9
    with pytest.raises(ValidationError, match="commitDecisionDigest"):
        WorkspacePublicationReceipt.model_validate(transplanted)

    class DerivedWorkspacePublicationObservation(WorkspacePublicationObservation):
        pass

    derived = DerivedWorkspacePublicationObservation.model_validate(
        observation.model_dump(by_alias=True, mode="json")
    )
    with pytest.raises(TypeError, match="exact WorkspacePublicationObservation"):
        canonical_workspace_publication_observation_digest(derived)


def test_workspace_recovery_claim_preserves_the_decision_and_advances_only_active_fence() -> None:
    decision = _workspace_commit_decision()
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
        expectedActiveFenceEventId="event_commit_decided",
        expectedActiveFenceEventSequence=1,
        expectedActiveFenceEventHash=D1,
        newFencingToken=11,
        workspaceViewBindingDigest=decision.snapshot.request.workspace_view_binding_digest,
    )
    claim_payload = {
        "actionId": "act_01",
        "activeFence": 11,
        "attemptId": "try_01",
        "authorityPartitionId": "workspace_01",
        "claimId": "claim_commit_01_fence_11",
        "commitId": "commit_01",
        "expectedActiveFenceEventHash": D1,
        "expectedActiveFenceEventSequence": 1,
        "expectedCommitCompareVersion": 1,
        "expectedWorkspaceCompareVersion": 4,
        "priorActiveFence": 7,
        "recoveryOwnerId": "recovery_01",
        "stateRootAfter": D2,
        "stateRootBefore": D1,
        "targetGeneration": 2,
        "transactionId": "txn_01",
        "workspaceId": "workspace_01",
        "workspaceViewBindingDigest": decision.snapshot.request.workspace_view_binding_digest,
    }
    draft = _draft_lifecycle_journal_event(
        event_id="event_commit_recovery_claimed",
        partition_id="workspace_01",
        event_type="workspace.commit_recovery_claimed",
        action_id="act_01",
        attempt_id="try_01",
        task_contract_id="task_01",
        task_version=1,
        task_contract_digest=canonical_task_contract_digest(_task()),
        completion_epoch_id="epoch_01",
        admission_sequence=1,
        request_digest=D3,
        idempotency_key_digest=D8,
        fencing_token=11,
        actor_id="recovery_01",
        policy_digest=D9,
        causation_id="event_commit_decided",
        correlation_id="run_01",
        identity_digest=D8,
        payload=claim_payload,
    )
    event = JournalEvent(
        **draft.model_dump(by_alias=True),
        sequence=2,
        previousHash=D1,
        eventHash=D2,
        rowChecksum=D3,
        createdAt=NOW,
    )
    current = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=decision.snapshot.request,
        state="decided",
        activeFencingToken=11,
        activeFenceEventId=event.event_id,
        activeFenceEventSequence=event.sequence,
        activeFenceEventHash=event.event_hash,
        commitCompareVersion=2,
    )
    claim = WorkspaceCommitRecoveryClaim(
        schemaVersion=1,
        request=request,
        originalDecision=decision,
        priorSnapshot=decision.snapshot,
        snapshot=current,
        workspaceCompareVersion=5,
        claimEvent=event,
    )

    assert claim.original_decision.snapshot.active_fencing_token == 7
    assert claim.original_decision.snapshot.request.decision_fencing_token == 7
    assert claim.snapshot.active_fencing_token == 11
    assert claim.snapshot.request == claim.original_decision.snapshot.request

    transplanted = claim.model_dump(by_alias=True, mode="json")
    transplanted["claimEvent"]["attemptId"] = "try_stale"
    with pytest.raises(ValidationError, match="claimEvent.attemptId"):
        WorkspaceCommitRecoveryClaim.model_validate(transplanted)


def test_workspace_recovery_claim_requires_a_strictly_new_fence() -> None:
    decision = _workspace_commit_decision()
    with pytest.raises(ValidationError, match="newFencingToken"):
        WorkspaceCommitRecoveryClaimRequest(
            schemaId="magi.workspace_commit_recovery_claim_request.v1",
            claimId="claim_commit_01_fence_7",
            commitId="commit_01",
            workspaceId="workspace_01",
            authorityPartitionId="workspace_01",
            recoveryOwnerId="recovery_01",
            expectedWorkspaceCompareVersion=4,
            expectedCommitCompareVersion=1,
            expectedActiveFencingToken=7,
            expectedActiveFenceEventId="event_commit_decided",
            expectedActiveFenceEventSequence=1,
            expectedActiveFenceEventHash=D1,
            newFencingToken=7,
            workspaceViewBindingDigest=decision.snapshot.request.workspace_view_binding_digest,
        )


def test_repeated_workspace_recovery_claim_follows_the_exact_prior_fence_event() -> None:
    decision = _workspace_commit_decision()
    first_request = WorkspaceCommitRecoveryClaimRequest(
        schemaId="magi.workspace_commit_recovery_claim_request.v1",
        claimId="claim_commit_01_fence_11",
        commitId="commit_01",
        workspaceId="workspace_01",
        authorityPartitionId="workspace_01",
        recoveryOwnerId="recovery_01",
        expectedWorkspaceCompareVersion=4,
        expectedCommitCompareVersion=1,
        expectedActiveFencingToken=7,
        expectedActiveFenceEventId="event_commit_decided",
        expectedActiveFenceEventSequence=1,
        expectedActiveFenceEventHash=D1,
        newFencingToken=11,
        workspaceViewBindingDigest=decision.snapshot.request.workspace_view_binding_digest,
    )
    first_payload = {
        "actionId": "act_01",
        "activeFence": 11,
        "attemptId": "try_01",
        "authorityPartitionId": "workspace_01",
        "claimId": first_request.claim_id,
        "commitId": "commit_01",
        "expectedActiveFenceEventHash": D1,
        "expectedActiveFenceEventSequence": 1,
        "expectedCommitCompareVersion": 1,
        "expectedWorkspaceCompareVersion": 4,
        "priorActiveFence": 7,
        "recoveryOwnerId": "recovery_01",
        "stateRootAfter": D2,
        "stateRootBefore": D1,
        "targetGeneration": 2,
        "transactionId": "txn_01",
        "workspaceId": "workspace_01",
        "workspaceViewBindingDigest": decision.snapshot.request.workspace_view_binding_digest,
    }
    first_event = _stored_event(
        "workspace.commit_recovery_claimed",
        event_id="event_claim_11",
        fencing_token=11,
        actor_id="recovery_01",
        causation_id=decision.commit_event.event_id,
        payload=first_payload,
        sequence=2,
        previous_hash=D1,
        event_hash=D2,
        row_checksum=D3,
    )
    first_snapshot = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=decision.snapshot.request,
        state="decided",
        activeFencingToken=11,
        activeFenceEventId=first_event.event_id,
        activeFenceEventSequence=first_event.sequence,
        activeFenceEventHash=first_event.event_hash,
        commitCompareVersion=2,
    )
    first_claim = WorkspaceCommitRecoveryClaim(
        schemaVersion=1,
        request=first_request,
        originalDecision=decision,
        priorSnapshot=decision.snapshot,
        snapshot=first_snapshot,
        workspaceCompareVersion=5,
        claimEvent=first_event,
    )

    second_request = WorkspaceCommitRecoveryClaimRequest(
        schemaId="magi.workspace_commit_recovery_claim_request.v1",
        claimId="claim_commit_01_fence_12",
        commitId="commit_01",
        workspaceId="workspace_01",
        authorityPartitionId="workspace_01",
        recoveryOwnerId="recovery_02",
        expectedWorkspaceCompareVersion=5,
        expectedCommitCompareVersion=2,
        expectedActiveFencingToken=11,
        expectedActiveFenceEventId=first_event.event_id,
        expectedActiveFenceEventSequence=first_event.sequence,
        expectedActiveFenceEventHash=first_event.event_hash,
        newFencingToken=12,
        workspaceViewBindingDigest=decision.snapshot.request.workspace_view_binding_digest,
    )
    second_payload = {
        "actionId": "act_01",
        "activeFence": 12,
        "attemptId": "try_01",
        "authorityPartitionId": "workspace_01",
        "claimId": second_request.claim_id,
        "commitId": "commit_01",
        "expectedActiveFenceEventHash": D2,
        "expectedActiveFenceEventSequence": 2,
        "expectedCommitCompareVersion": 2,
        "expectedWorkspaceCompareVersion": 5,
        "priorActiveFence": 11,
        "recoveryOwnerId": "recovery_02",
        "stateRootAfter": D2,
        "stateRootBefore": D1,
        "targetGeneration": 2,
        "transactionId": "txn_01",
        "workspaceId": "workspace_01",
        "workspaceViewBindingDigest": decision.snapshot.request.workspace_view_binding_digest,
    }
    second_event = _stored_event(
        "workspace.commit_recovery_claimed",
        event_id="event_claim_12",
        fencing_token=12,
        actor_id="recovery_02",
        causation_id=first_event.event_id,
        payload=second_payload,
        sequence=3,
        previous_hash=first_event.event_hash,
        event_hash=D3,
        row_checksum=D4,
    )
    second_snapshot = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=decision.snapshot.request,
        state="decided",
        activeFencingToken=12,
        activeFenceEventId=second_event.event_id,
        activeFenceEventSequence=second_event.sequence,
        activeFenceEventHash=second_event.event_hash,
        commitCompareVersion=3,
    )
    second_claim = WorkspaceCommitRecoveryClaim(
        schemaVersion=1,
        request=second_request,
        originalDecision=decision,
        priorSnapshot=first_claim.snapshot,
        snapshot=second_snapshot,
        workspaceCompareVersion=6,
        claimEvent=second_event,
    )
    assert second_claim.snapshot.active_fence_event_id == second_event.event_id

    stale = second_claim.model_dump(by_alias=True, mode="json")
    stale.pop("claimDigest")
    stale["request"]["expectedActiveFenceEventId"] = decision.commit_event.event_id
    stale["claimEvent"]["causationId"] = decision.commit_event.event_id
    with pytest.raises(ValidationError, match="priorSnapshot active fence event"):
        WorkspaceCommitRecoveryClaim.model_validate(stale)


def test_workspace_only_quarantine_receipt_remains_valid_without_commit_bindings() -> None:
    receipt = WorkspaceQuarantineReceipt(
        schemaVersion=1,
        workspaceId="workspace_01",
        commitId=None,
        authorityPartitionId="workspace_01",
        reasonDigest=D6,
        fencingToken=0,
        quarantinedAt=NOW,
        workspaceCompareVersion=4,
    )

    assert receipt.commit_id is None
    assert receipt.fencing_token == 0


def test_commit_quarantine_receipt_binds_prior_snapshot_event_and_cas_versions() -> None:
    view_digest = canonical_workspace_view_binding_digest(
        workspace_id="workspace_01",
        workspace_ref=WORKSPACE_ROOT_REF,
        authority_partition_id="workspace_01",
        generation=1,
        state_root=D1,
    )
    request = WorkspaceCommitDecisionRequest(
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
        stateRootBefore=D1,
        stateRootAfter=D2,
        decisionFencingToken=7,
        mutationPlanDigest=D3,
        stagingManifestRef=f"authority-manifest://{D4}",
        stagingManifestDigest=D4,
        changedResourceRefsDigest=canonical_resource_refs_digest((WORKSPACE_A_REF,)),
        workspaceViewBindingDigest=view_digest,
        changedResourceRefs=(WORKSPACE_A_REF,),
    )
    prior = WorkspaceCommitSnapshot(
        schemaVersion=1,
        request=request,
        state="decided",
        activeFencingToken=7,
        activeFenceEventId="event_commit_decided",
        activeFenceEventSequence=1,
        activeFenceEventHash=D1,
        commitCompareVersion=1,
    )
    expected_workspace_version = 4
    request_digest = (
        "sha256:"
        + sha256(
            json.dumps(
                request.model_dump(by_alias=True, mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    payload = {
        "actionId": request.action_id,
        "activeFence": prior.active_fencing_token,
        "attemptId": request.attempt_id,
        "commitId": request.commit_id,
        "commitCompareVersion": 2,
        "expectedCommitCompareVersion": prior.commit_compare_version,
        "expectedWorkspaceCompareVersion": expected_workspace_version,
        "priorActiveFenceEventHash": prior.active_fence_event_hash,
        "priorActiveFenceEventId": prior.active_fence_event_id,
        "priorActiveFenceEventSequence": prior.active_fence_event_sequence,
        "priorCommitRequestDigest": request_digest,
        "priorCommitState": prior.state,
        "quarantinedAt": NOW.isoformat(),
        "reasonDigest": D6,
        "transactionId": request.transaction_id,
        "workspaceCompareVersion": 5,
        "workspaceId": request.workspace_id,
    }
    stored_payload = _stored_event(
        "diagnostic.recorded",
        event_id="event_workspace_quarantined",
        causation_id=prior.active_fence_event_id,
        payload=payload,
        sequence=prior.active_fence_event_sequence + 1,
        previous_hash=prior.active_fence_event_hash,
        event_hash=D2,
        row_checksum=D3,
    ).model_dump(by_alias=True, mode="json")
    stored_payload["eventType"] = "workspace.quarantined"
    event = JournalEvent.model_validate(stored_payload)
    receipt = WorkspaceQuarantineReceipt(
        schemaVersion=1,
        workspaceId=request.workspace_id,
        commitId=request.commit_id,
        authorityPartitionId=request.authority_partition_id,
        reasonDigest=D6,
        fencingToken=prior.active_fencing_token,
        quarantinedAt=NOW,
        expectedWorkspaceCompareVersion=expected_workspace_version,
        workspaceCompareVersion=5,
        priorCommitSnapshot=prior,
        commitCompareVersion=2,
        quarantineEvent=event,
    )

    assert receipt.commit_compare_version == prior.commit_compare_version + 1

    zero_fence = receipt.model_dump(by_alias=True, mode="json")
    zero_fence["fencingToken"] = 0
    with pytest.raises(ValidationError, match="positive fencingToken"):
        WorkspaceQuarantineReceipt.model_validate(zero_fence)

    stale_snapshot = receipt.model_dump(by_alias=True, mode="json")
    stale_snapshot["priorCommitSnapshot"]["activeFenceEventHash"] = D9
    with pytest.raises(ValidationError, match="previousHash"):
        WorkspaceQuarantineReceipt.model_validate(stale_snapshot)

    stale_commit_version = receipt.model_dump(by_alias=True, mode="json")
    stale_commit_version["commitCompareVersion"] = 3
    with pytest.raises(ValidationError, match="commitCompareVersion must advance"):
        WorkspaceQuarantineReceipt.model_validate(stale_commit_version)

    stale_workspace_version = receipt.model_dump(by_alias=True, mode="json")
    stale_workspace_version["workspaceCompareVersion"] = 6
    with pytest.raises(ValidationError, match="workspaceCompareVersion must advance"):
        WorkspaceQuarantineReceipt.model_validate(stale_workspace_version)

    tampered_payload = receipt.model_dump(by_alias=True, mode="json")
    tampered_payload_json = json.dumps(
        {**payload, "reasonDigest": D7},
        sort_keys=True,
        separators=(",", ":"),
    )
    tampered_payload["quarantineEvent"]["payloadJson"] = tampered_payload_json
    tampered_payload["quarantineEvent"]["payloadDigest"] = (
        "sha256:" + sha256(tampered_payload_json.encode()).hexdigest()
    )
    with pytest.raises(ValidationError, match="exact quarantine receipt"):
        WorkspaceQuarantineReceipt.model_validate(tampered_payload)


def test_completion_verdict_binds_the_exact_finalization_request_and_manifest() -> None:
    request = _finalization()
    cursor = ProjectionCursorBinding(
        schemaVersion=1,
        partitionId=request.task_partition_id,
        projectionId="task",
        requiredSequence=1,
        requiredEventHash=D1,
        acknowledgedSequence=1,
        acknowledgedEventHash=D1,
        stateRoot=request.state_root,
        compareVersion=1,
    )
    verdict = CompletionVerdict(
        schemaId="magi.completion_verdict.v1",
        completionId="completion_01",
        finalizationId=request.finalization_id,
        finalizationRequestDigest=request.finalization_request_digest,
        responseClaimManifestDigest=request.response_claim_manifest_digest,
        status=CompletionStatus.COMPLETE,
        taskContractId=request.task_contract.task_contract_id,
        taskVersion=request.task_contract.version,
        taskContractDigest=request.task_contract_digest,
        taskContractSnapshotRef=request.task_contract_snapshot_ref,
        taskPartitionId=request.task_partition_id,
        completionEpochId=request.completion_epoch_id,
        stateRoot=request.state_root,
        evidenceRoot=request.evidence_root,
        barrierAdmissionSequence=request.barrier_admission_sequence,
        requiredProjectionDigest=canonical_required_projections_digest(
            (
                RequiredProjection(
                    schemaVersion=1,
                    partitionId=request.task_partition_id,
                    projectionId="task",
                ),
            )
        ),
        projectionCursors=(cursor,),
        requirements=(
            RequirementResult(
                requirementId="req_01",
                state=RequirementState.SATISFIED,
                evidenceIds=("evidence_01",),
                researchClaims=(
                    ResearchClaimResult(
                        schemaVersion=1,
                        claimId=request.task_contract.requirements[0]
                        .proof.research.claims[0]
                        .claim_id,
                        propositionDigest=request.task_contract.requirements[0]
                        .proof.research.claims[0]
                        .proposition_digest,
                        state="satisfied",
                        evidenceIds=("evidence_01",),
                        reasonCodes=("entailed",),
                    ),
                ),
                reasonCodes=("verified",),
            ),
        ),
        includedActionIds=("act_01",),
        responseDigest=request.claim_manifest.candidate_response_digest,
        reasonCodes=("all_requirements_satisfied",),
    )
    assert verdict.finalization_request_digest == request.finalization_request_digest
    assert verdict.verdict_digest is not None

    projections = (
        RequiredProjection(
            schemaVersion=1,
            partitionId=request.task_partition_id,
            projectionId="task",
        ),
    )
    seal = EpochSeal(
        schemaVersion=1,
        completionEpochId=request.completion_epoch_id,
        taskPartitionId=request.task_partition_id,
        taskContractDigest=request.task_contract_digest,
        taskContractSnapshotRef=request.task_contract_snapshot_ref,
        barrierAdmissionSequence=request.barrier_admission_sequence,
        epochCompareVersion=2,
        requiredProjectionDigest=canonical_required_projections_digest(projections),
        requiredProjections=projections,
        sealedAt=NOW,
    )
    assert validate_completion_persistence_contract(seal, request, verdict) is verdict

    mismatched = verdict.model_dump(by_alias=True, mode="json")
    mismatched["finalizationId"] = "final_other"
    mismatched.pop("verdictDigest")
    with pytest.raises(ValueError, match="finalizationId"):
        validate_completion_persistence_contract(
            seal,
            request,
            CompletionVerdict.model_validate(mismatched),
        )

    payload = verdict.model_dump(by_alias=True, mode="json")
    payload["requiredProjectionDigest"] = D9
    with pytest.raises(ValidationError, match="requiredProjectionDigest"):
        CompletionVerdict.model_validate(payload)

    payload = verdict.model_dump(by_alias=True, mode="json")
    payload.pop("verdictDigest")
    payload["requiredProjectionRegistryDigest"] = D9
    with pytest.raises(ValidationError, match="requiredProjectionRegistryDigest"):
        CompletionVerdict.model_validate(payload)


def test_epoch_seal_binds_the_exact_sorted_required_projection_set() -> None:
    projections = (
        RequiredProjection(
            schemaVersion=1,
            partitionId="task:task_01:1",
            projectionId="task",
        ),
    )
    digest = canonical_required_projections_digest(projections)
    seal = EpochSeal(
        schemaVersion=1,
        completionEpochId="epoch_01",
        taskPartitionId="task:task_01:1",
        taskContractDigest=canonical_task_contract_digest(_task()),
        taskContractSnapshotRef=("authority-task://" + canonical_task_contract_digest(_task())),
        barrierAdmissionSequence=1,
        epochCompareVersion=2,
        requiredProjectionDigest=digest,
        requiredProjections=projections,
        sealedAt=NOW,
    )
    assert seal.required_projection_digest == digest
    assert seal.required_projection_registry_digest == digest

    payload = seal.model_dump(by_alias=True, mode="json")
    payload["requiredProjectionDigest"] = D9
    with pytest.raises(ValidationError, match="requiredProjectionDigest"):
        EpochSeal.model_validate(payload)

    payload = seal.model_dump(by_alias=True, mode="json")
    payload["requiredProjectionRegistryDigest"] = D9
    with pytest.raises(ValidationError, match="requiredProjectionRegistryDigest"):
        EpochSeal.model_validate(payload)


def test_dependency_health_requires_liveness_and_closed_status() -> None:
    health = DependencyHealth(
        schemaId="magi.dependency_health.v1",
        dependencyId="source_fetcher",
        status=DependencyStatus.CLEAN,
        producerVersion="1.0.0",
        schemaVersion="1",
        producerAlive=True,
        invocationEvidenceId="evidence_01",
        invocationEvidenceDigest=D1,
        taskContractDigest=canonical_task_contract_digest(_task()),
        completionEpochId="epoch_01",
        stateRoot=D3,
        observedAt=NOW,
        reasonCodes=("invoked",),
    )
    assert health.producer_alive is True

    payload = health.model_dump(by_alias=True, mode="json")
    for field, value in (
        ("producerAlive", False),
        ("invocationEvidenceId", None),
        ("producerVersion", None),
        ("schemaVersion", None),
    ):
        invalid = {**payload, field: value}
        if field == "invocationEvidenceId":
            invalid["invocationEvidenceDigest"] = None
        with pytest.raises(ValidationError, match="clean dependency health"):
            DependencyHealth.model_validate(invalid)


def test_finalization_rejects_stale_or_schema_incompatible_dependency_health() -> None:
    dependency = DependencyContract(
        dependencyId="source_fetcher",
        requiredSchema="magi.source_fetcher.v2",
        unavailableBehavior="block",
    )
    task = _task(dependencies=(dependency,))
    task_digest = canonical_task_contract_digest(task)
    incompatible = DependencyHealth(
        schemaId="magi.dependency_health.v1",
        dependencyId=dependency.dependency_id,
        status=DependencyStatus.CLEAN,
        producerVersion="2.0.0",
        schemaVersion="magi.source_fetcher.v1",
        producerAlive=True,
        invocationEvidenceId="evidence_invocation_01",
        invocationEvidenceDigest=D1,
        taskContractDigest=task_digest,
        completionEpochId=task.completion_epoch_id,
        stateRoot=D3,
        observedAt=NOW,
        reasonCodes=("invoked",),
    )
    with pytest.raises(ValidationError, match="schemaVersion is incompatible"):
        _finalization(task=task, dependency_health=(incompatible,))

    compatible_payload = incompatible.model_dump(by_alias=True, mode="json")
    compatible_payload["schemaVersion"] = dependency.required_schema
    compatible_payload["stateRoot"] = D9
    stale = DependencyHealth.model_validate(compatible_payload)
    with pytest.raises(ValidationError, match="current Task Contract, epoch, and state root"):
        _finalization(task=task, dependency_health=(stale,))


def _public_journal_draft(event_type: str) -> GenericJournalEventDraft:
    return draft_journal_event(
        event_id="event_forged",
        partition_id="workspace_01",
        event_type=event_type,
        action_id="act_01",
        attempt_id="try_01",
        task_contract_id="task_01",
        task_version=1,
        task_contract_digest=canonical_task_contract_digest(_task()),
        completion_epoch_id="epoch_01",
        admission_sequence=1,
        request_digest=D3,
        idempotency_key_digest=D8,
        fencing_token=7,
        actor_id="actor_01",
        policy_digest=D9,
        causation_id="turn_01",
        correlation_id="run_01",
        identity_digest=D8,
        payload={},
    )


@pytest.mark.parametrize(
    "reserved_prefix",
    (
        "action.",
        "authority.",
        "completion.",
        "epoch.",
        "evidence.",
        "lease.",
        "outbox.",
        "partition.",
        "projection.",
        "recovery.",
        "task_contract.",
        "user_decision.",
        "workspace.",
    ),
)
def test_public_journal_factory_rejects_every_reserved_lifecycle_prefix(
    reserved_prefix: str,
) -> None:
    with pytest.raises(ValidationError, match="reserved lifecycle"):
        _public_journal_draft(f"{reserved_prefix}forged")


@pytest.mark.parametrize("event_type", ("audit.note", "diagnostic.recorded"))
def test_public_journal_factory_returns_exact_generic_draft(event_type: str) -> None:
    draft = _public_journal_draft(event_type)

    assert type(draft) is GenericJournalEventDraft


@pytest.mark.parametrize(
    ("status", "findings", "quarantined"),
    [
        ("clean", (D1,), False),
        ("clean", (), True),
        ("corrupt", (), True),
        ("unsupported_schema", (D1,), False),
    ],
)
def test_integrity_scan_status_is_consistent_with_findings_and_quarantine(
    status: str,
    findings: tuple[str, ...],
    quarantined: bool,
) -> None:
    with pytest.raises(ValidationError, match="integrity scan"):
        IntegrityScanResult(
            schemaVersion=1,
            partitionId="workspace_01",
            status=status,
            scannedThroughSequence=4,
            scannedHeadHash=D2,
            findingDigests=findings,
            quarantined=quarantined,
            scannedAt=NOW,
        )


def test_projection_cursor_rejects_a_fork_at_the_same_sequence() -> None:
    with pytest.raises(ValidationError, match="same event hash"):
        ProjectionCursorBinding(
            schemaVersion=1,
            partitionId="workspace_01",
            projectionId="evidence",
            requiredSequence=4,
            requiredEventHash=D1,
            acknowledgedSequence=4,
            acknowledgedEventHash=D2,
            stateRoot=D3,
            compareVersion=2,
        )


def test_success_claims_and_satisfied_requirements_require_evidence() -> None:
    with pytest.raises(ValidationError, match="response claims require"):
        ResponseClaim(
            schemaVersion=1,
            claimId="claim_01",
            claimClass="execution",
            textDigest=D1,
            codepointStart=0,
            codepointEnd=1,
            utf8Start=0,
            utf8End=1,
            evidenceIds=(),
        )
    with pytest.raises(ValidationError, match="satisfied requirements require evidence"):
        RequirementResult(
            schemaVersion=1,
            requirementId="req_01",
            state=RequirementState.SATISFIED,
            evidenceIds=(),
            reasonCodes=("claimed",),
        )


def test_completion_cursor_must_share_the_finalized_state_root() -> None:
    _, verdict = _completion_contracts()
    payload = verdict.model_dump(by_alias=True, mode="json")
    payload.pop("verdictDigest")
    payload["projectionCursors"][0]["stateRoot"] = D9

    with pytest.raises(ValidationError, match="cursor stateRoot"):
        CompletionVerdict.model_validate(payload)


def test_completion_claim_evidence_must_be_proven_by_requirements() -> None:
    request = _finalization()
    seal, verdict = _completion_contracts(request=request)
    payload = verdict.model_dump(by_alias=True, mode="json")
    payload.pop("verdictDigest")
    payload["requirements"][0]["evidenceIds"] = ["evidence_other"]
    with pytest.raises(ValidationError, match="research claim evidence"):
        CompletionVerdict.model_validate(payload)


def test_research_completion_requires_exact_atomic_claim_results() -> None:
    request = _finalization()
    seal, verdict = _completion_contracts(request=request)
    assert validate_completion_persistence_contract(seal, request, verdict) is verdict
    payload = verdict.model_dump(by_alias=True, mode="json")
    payload.pop("verdictDigest")
    payload["requirements"][0]["researchClaims"] = []
    missing_claims = CompletionVerdict.model_validate(payload)

    with pytest.raises(ValueError, match="research claim"):
        validate_completion_persistence_contract(seal, request, missing_claims)


def test_execution_claim_requires_an_included_action() -> None:
    request_payload = _finalization().model_dump(by_alias=True, mode="json")
    request_payload.pop("finalizationRequestDigest")
    request_payload.pop("responseClaimManifestDigest")
    request_payload["claimManifest"]["segments"][0]["claimClass"] = "execution"
    request = FinalizationRequest.model_validate(request_payload)
    seal, verdict = _completion_contracts(request=request, included_action_ids=())

    with pytest.raises(ValueError, match="includedActionIds"):
        validate_completion_persistence_contract(seal, request, verdict)


def test_finalization_rejects_same_identity_replacement_against_durable_epoch() -> None:
    original_task = _task()
    binding = bind_task_contract(original_task)
    epoch = EpochSnapshot(
        schemaVersion=1,
        completionEpochId="epoch_01",
        taskPartitionId="task:task_01:1",
        taskContractBinding=binding,
        state="sealing",
        lastAdmissionSequence=1,
        compareVersion=1,
    )
    original_request = _finalization()
    assert validate_finalization_request_epoch(epoch, original_request) == original_request
    seal, verdict = _completion_contracts(request=original_request)
    evaluation = FinalizationEvaluationRequest(
        schemaVersion=1,
        epoch=epoch,
        seal=seal,
        request=original_request,
        projectionCursors=verdict.projection_cursors,
    )
    assert evaluation.evaluation_digest is not None

    evaluation_payload = evaluation.model_dump(by_alias=True, mode="json")
    evaluation_payload.pop("evaluationDigest")
    evaluation_payload["projectionCursors"][0]["stateRoot"] = D9
    with pytest.raises(ValidationError, match="finalization stateRoot"):
        FinalizationEvaluationRequest.model_validate(evaluation_payload)

    open_payload = epoch.model_dump(by_alias=True, mode="json")
    open_payload["state"] = "open"
    with pytest.raises(ValueError, match="SEALING"):
        validate_finalization_request_epoch(
            EpochSnapshot.model_validate(open_payload),
            original_request,
        )

    barrier_payload = epoch.model_dump(by_alias=True, mode="json")
    barrier_payload["lastAdmissionSequence"] = 2
    with pytest.raises(ValueError, match="barrierAdmissionSequence"):
        validate_finalization_request_epoch(
            EpochSnapshot.model_validate(barrier_payload),
            original_request,
        )

    task_payload = original_task.model_dump(by_alias=True, mode="json")
    task_payload["intent"] = "replacement bytes under the same identity"
    replacement_task = TaskContractSnapshot.model_validate(task_payload)
    replacement_digest = canonical_task_contract_digest(replacement_task)
    request_payload = original_request.model_dump(by_alias=True, mode="json")
    request_payload.pop("finalizationRequestDigest")
    request_payload["taskContract"] = replacement_task.model_dump(
        by_alias=True,
        mode="json",
    )
    request_payload["taskContractDigest"] = replacement_digest
    request_payload["taskContractSnapshotRef"] = f"authority-task://{replacement_digest}"
    replacement_request = FinalizationRequest.model_validate(request_payload)

    with pytest.raises(ValueError, match="durable EpochSnapshot"):
        validate_finalization_request_epoch(epoch, replacement_request)


def test_completion_persistence_receipt_binds_verdict_and_terminal_status() -> None:
    _, verdict = _completion_contracts()
    assert verdict.verdict_digest is not None
    outbox_payload_json = json.dumps(
        {
            "completionId": verdict.completion_id,
            "responseDigest": verdict.response_digest,
            "verdictDigest": verdict.verdict_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    outbox_payload_digest = "sha256:" + sha256(outbox_payload_json.encode()).hexdigest()
    completion_draft = _draft_lifecycle_journal_event(
        event_id="event_completion",
        partition_id=verdict.task_partition_id,
        event_type="completion.persisted",
        task_contract_id=verdict.task_contract_id,
        task_version=verdict.task_version,
        task_contract_digest=verdict.task_contract_digest,
        completion_epoch_id=verdict.completion_epoch_id,
        admission_sequence=verdict.barrier_admission_sequence,
        request_digest=verdict.finalization_request_digest,
        idempotency_key_digest=D8,
        fencing_token=0,
        actor_id="completion-evaluator",
        policy_digest=D9,
        causation_id=verdict.finalization_id,
        correlation_id=verdict.completion_id,
        identity_digest=D8,
        payload={
            "completionId": verdict.completion_id,
            "epochCompareVersion": 3,
            "outboxCompareVersion": 1,
            "outboxId": "outbox_completion",
            "outboxPayloadDigest": outbox_payload_digest,
            "responseDigest": verdict.response_digest,
            "status": verdict.status.value,
            "terminalState": verdict.status.value,
            "verdictDigest": verdict.verdict_digest,
        },
    )
    completion_event = JournalEvent(
        **completion_draft.model_dump(by_alias=True),
        sequence=1,
        previousHash=D0,
        eventHash=D1,
        rowChecksum=D2,
        createdAt=NOW,
    )
    outbox_item = OutboxItem(
        schemaVersion=1,
        outboxId="outbox_completion",
        partitionId=verdict.task_partition_id,
        subjectId=verdict.completion_id,
        subjectDigest=verdict.verdict_digest,
        eventId=completion_event.event_id,
        eventSequence=completion_event.sequence,
        eventHash=completion_event.event_hash,
        kind="final_response",
        payloadDigest=outbox_payload_digest,
        payloadJson=outbox_payload_json,
        state="pending",
        claimOwnerId=None,
        claimFencingToken=None,
        claimExpiresAt=None,
        deliveryAttempt=0,
        acknowledgementDigest=None,
        compareVersion=1,
    )
    receipt = CompletionPersistenceReceipt(
        schemaVersion=1,
        completionId=verdict.completion_id,
        completionEpochId=verdict.completion_epoch_id,
        taskContractDigest=verdict.task_contract_digest,
        verdictDigest=verdict.verdict_digest,
        responseDigest=verdict.response_digest,
        completionEventId="event_completion",
        completionEventHash=D1,
        outboxId="outbox_completion",
        outboxPayloadDigest=outbox_payload_digest,
        epochCompareVersion=3,
        outboxCompareVersion=1,
        status="complete",
        terminalState="complete",
        completionEvent=completion_event,
        outboxItem=outbox_item,
    )
    assert validate_completion_persistence_receipt(verdict, receipt) == receipt

    mismatched = receipt.model_dump(by_alias=True, mode="json")
    mismatched["completionId"] = "completion_other"
    with pytest.raises(ValueError, match="completionId"):
        validate_completion_persistence_receipt(
            verdict,
            CompletionPersistenceReceipt.model_validate(mismatched),
        )

    mismatched = receipt.model_dump(by_alias=True, mode="json")
    mismatched["status"] = "blocked"
    with pytest.raises(ValidationError, match="terminal epoch state"):
        CompletionPersistenceReceipt.model_validate(mismatched)

    mismatched = receipt.model_dump(by_alias=True, mode="json")
    mismatched["outboxItem"]["subjectDigest"] = D9
    with pytest.raises(ValidationError, match="outbox item verdictDigest"):
        CompletionPersistenceReceipt.model_validate(mismatched)

    mismatched = receipt.model_dump(by_alias=True, mode="json")
    mismatched["completionEvent"]["correlationId"] = "completion_other"
    with pytest.raises(ValidationError, match="correlationId"):
        CompletionPersistenceReceipt.model_validate(mismatched)
