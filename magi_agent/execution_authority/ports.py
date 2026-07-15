"""Dependency-inverted host boundaries for dormant execution authority."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from magi_agent.execution_authority.contracts import (
    ActionAdmission,
    ActionIntent,
    ActionProposal,
    ActionResolution,
    ActionSnapshot,
    AttemptObservationRecording,
    AttemptSnapshot,
    AttemptVerificationRecording,
    AuthorityContract,
    AuthorityDecision,
    AuthorityResumeBinding,
    BackendObservation,
    CompletionPersistenceReceipt,
    CompletionVerdict,
    EffectDeclarationBinding,
    EpochSeal,
    EpochSnapshot,
    EvidenceEdge,
    EvidenceNode,
    EvidenceNodeDraft,
    EvidenceRecordDraft,
    EvidenceRecordRecording,
    ExecutionPreparation,
    ExecutionStart,
    ExecutionStartRequest,
    FinalizationRequest,
    FinalizationEvaluationRequest,
    GenericJournalEventDraft,
    IntegrityScanResult,
    JournalEvent,
    JournalHead,
    LeaseSnapshot,
    NormalizedInputDraft,
    NormalizedInputSnapshot,
    OutboxDraft,
    OutboxItem,
    PartitionGate,
    PartitionRecoveryPlan,
    ProjectionCursorSnapshot,
    RecoveryContext,
    RecoveryDecision,
    RecoverySessionSnapshot,
    RequiredProjection,
    SourceSpan,
    TaskContractSnapshot,
    UserApprovalConsumption,
    UserDecisionExpirationRequest,
    UserDecisionInvalidationRequest,
    UserDecisionReceipt,
    UserDecisionRecording,
    UserDecisionRequest,
    UserDecisionSnapshot,
    UserDecisionTransition,
    VerificationEvidenceBinding,
    WorkspaceCommitDecision,
    WorkspaceCommitDecisionRequest,
    WorkspaceCommitRecoveryClaim,
    WorkspaceCommitRecoveryClaimRequest,
    WorkspaceCommitSnapshot,
    WorkspacePublicationObservation,
    WorkspacePublicationReceipt,
    WorkspaceQuarantineReceipt,
    WorkspaceSnapshot,
)


PARTITION_RECOVERY_LEASE_NAME = "partition-recovery"


@runtime_checkable
class AuthoritativeClockPort(Protocol):
    """Clock read by a store only after its write transaction begins."""

    def now(self) -> datetime: ...


@runtime_checkable
class JournalPort(Protocol):
    """Atomic authority journal and materialization boundary.

    Named lifecycle methods derive their own journal events.  Callers can only
    append non-reserved generic drafts and can never provide chain fields.
    """

    def append(self, draft: GenericJournalEventDraft) -> JournalEvent: ...

    def append_with_outbox(
        self,
        *,
        draft: GenericJournalEventDraft,
        outbox: OutboxDraft,
    ) -> OutboxItem: ...

    def read_partition(
        self,
        partition_id: str,
        after: int = 0,
    ) -> tuple[JournalEvent, ...]: ...

    def ensure_partition(self, partition_id: str) -> PartitionGate: ...

    def get_journal_head(self, partition_id: str) -> JournalHead: ...

    def scan_integrity(self, partition_id: str) -> IntegrityScanResult: ...

    def create_epoch(
        self,
        *,
        task_contract: TaskContractSnapshot,
        task_contract_snapshot_ref: str,
    ) -> EpochSnapshot: ...

    def get_epoch(self, completion_epoch_id: str) -> EpochSnapshot | None: ...

    def get_task_contract(
        self,
        task_contract_id: str,
        task_version: int,
    ) -> TaskContractSnapshot | None: ...

    def admit_action(
        self,
        *,
        proposal: ActionProposal,
        expected_epoch_compare_version: int,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
    ) -> ActionAdmission: ...

    def get_action(self, action_id: str) -> ActionSnapshot | None: ...

    def get_action_intent(self, action_id: str) -> ActionIntent | None: ...

    def get_attempt(
        self,
        action_id: str,
        attempt_id: str,
    ) -> AttemptSnapshot | None: ...

    def list_unfinished_actions(
        self,
        partition_id: str | None = None,
    ) -> tuple[ActionSnapshot, ...]: ...

    def list_unfinished_attempts(
        self,
        partition_id: str | None = None,
    ) -> tuple[AttemptSnapshot, ...]: ...

    def request_user_decision(
        self,
        request: UserDecisionRequest,
    ) -> UserDecisionSnapshot: ...

    def get_user_decision(
        self,
        decision_request_id: str,
    ) -> UserDecisionSnapshot | None: ...

    def record_user_decision(
        self,
        *,
        decision_request_id: str,
        opaque_envelope: object,
        verifier: UserDecisionVerifierPort,
    ) -> UserDecisionRecording: ...

    def invalidate_user_decision(
        self,
        request: UserDecisionInvalidationRequest,
    ) -> UserDecisionTransition: ...

    def expire_user_decision(
        self,
        request: UserDecisionExpirationRequest,
    ) -> UserDecisionTransition: ...

    def consume_user_approval(
        self,
        *,
        decision_request_id: str,
        expected_decision_compare_version: int,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
        approval_receipt_digest: str,
        current_policy_digest: str,
        current_capabilities_digest: str,
        authority_contract: AuthorityContract,
        authority_contract_digest: str,
        fencing_token: int,
        resume_binding: AuthorityResumeBinding,
    ) -> UserApprovalConsumption: ...

    def consume_authority_and_prepare(
        self,
        *,
        authority_contract: AuthorityContract,
        authority_contract_digest: str,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
        fencing_token: int,
    ) -> ExecutionPreparation: ...

    def consume_recovery_authority_and_prepare(
        self,
        *,
        authority_contract: AuthorityContract,
        authority_contract_digest: str,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
        recovery_owner_id: str,
        recovery_fencing_token: int,
    ) -> ExecutionPreparation: ...

    def deny_action(
        self,
        *,
        action_id: str,
        attempt_id: str,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
        reason_codes: tuple[str, ...],
    ) -> ActionResolution: ...

    def mark_executing(
        self,
        *,
        start: ExecutionStartRequest,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
    ) -> ExecutionStart: ...

    def record_attempt_observation(
        self,
        *,
        observation: BackendObservation,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
    ) -> AttemptObservationRecording: ...

    def record_attempt_verification(
        self,
        *,
        binding: VerificationEvidenceBinding,
        expected_action_compare_version: int,
        expected_attempt_compare_version: int,
        expected_partition_compare_version: int,
    ) -> AttemptVerificationRecording: ...

    def resolve_action(
        self,
        *,
        resolution: ActionResolution,
        expected_action_compare_version: int,
        expected_partition_compare_version: int,
    ) -> ActionResolution: ...

    def begin_recovery(
        self,
        *,
        decision: RecoveryDecision,
        context: RecoveryContext,
    ) -> RecoveryDecision: ...

    def begin_partition_recovery(
        self,
        *,
        plan: PartitionRecoveryPlan,
        owner_id: str,
        expected_compare_version: int,
        recovery_fencing_token: int,
    ) -> PartitionGate: ...

    def take_over_partition_recovery(
        self,
        *,
        recovery_epoch_id: str,
        recovery_plan_digest: str,
        new_owner_id: str,
        expected_compare_version: int,
        recovery_fencing_token: int,
    ) -> PartitionGate: ...

    def finish_partition_recovery(
        self,
        *,
        partition_id: str,
        recovery_epoch_id: str,
        recovery_plan_digest: str,
        owner_id: str,
        expected_compare_version: int,
        recovery_fencing_token: int,
    ) -> PartitionGate: ...

    def get_recovery_session(
        self,
        recovery_epoch_id: str,
    ) -> RecoverySessionSnapshot | None: ...

    def quarantine_partition(
        self,
        *,
        partition_id: str,
        expected_compare_version: int,
        recovery_fencing_token: int,
        reason_digest: str,
    ) -> PartitionGate: ...

    def acquire_lease(
        self,
        *,
        partition_id: str,
        lease_name: str,
        owner_id: str,
        ttl: timedelta,
        expected_compare_version: int,
    ) -> LeaseSnapshot: ...

    def renew_lease(
        self,
        *,
        partition_id: str,
        lease_name: str,
        owner_id: str,
        fencing_token: int,
        ttl: timedelta,
        expected_compare_version: int,
    ) -> LeaseSnapshot: ...

    def release_lease(
        self,
        *,
        partition_id: str,
        lease_name: str,
        owner_id: str,
        fencing_token: int,
        expected_compare_version: int,
    ) -> LeaseSnapshot: ...

    def take_over_recovery_lease(
        self,
        *,
        partition_id: str,
        lease_name: str,
        new_owner_id: str,
        ttl: timedelta,
        expected_compare_version: int,
    ) -> LeaseSnapshot: ...

    def get_lease(self, partition_id: str, lease_name: str) -> LeaseSnapshot | None: ...

    def ensure_projection_cursor(
        self,
        *,
        partition_id: str,
        projection_id: str,
        initial_state_root: str,
    ) -> ProjectionCursorSnapshot: ...

    def get_projection_cursor(
        self,
        partition_id: str,
        projection_id: str,
    ) -> ProjectionCursorSnapshot | None: ...

    def advance_projection_cursor(
        self,
        *,
        partition_id: str,
        projection_id: str,
        acknowledged_sequence: int,
        acknowledged_event_hash: str,
        state_root: str,
        expected_compare_version: int,
    ) -> ProjectionCursorSnapshot: ...

    def list_pending_outbox(self, limit: int) -> tuple[OutboxItem, ...]: ...

    def get_outbox_item(self, outbox_id: str) -> OutboxItem | None: ...

    def claim_outbox_item(
        self,
        *,
        outbox_id: str,
        owner_id: str,
        claim_ttl: timedelta,
        expected_compare_version: int,
    ) -> OutboxItem: ...

    def record_outbox_attempt(
        self,
        *,
        outbox_id: str,
        owner_id: str,
        claim_fencing_token: int,
        subject_digest: str,
        payload_digest: str,
        expected_compare_version: int,
    ) -> OutboxItem: ...

    def acknowledge_outbox_item(
        self,
        *,
        outbox_id: str,
        owner_id: str,
        claim_fencing_token: int,
        subject_digest: str,
        payload_digest: str,
        acknowledgement_digest: str,
        expected_compare_version: int,
    ) -> OutboxItem: ...

    def get_workspace(self, workspace_id: str) -> WorkspaceSnapshot | None: ...

    def get_workspace_commit(self, commit_id: str) -> WorkspaceCommitSnapshot | None: ...

    def decide_workspace_commit(
        self,
        request: WorkspaceCommitDecisionRequest,
    ) -> WorkspaceCommitDecision: ...

    def finalize_workspace_publication(
        self,
        *,
        observation: WorkspacePublicationObservation,
        expected_workspace_compare_version: int,
        expected_commit_compare_version: int,
        active_fencing_token: int,
    ) -> WorkspacePublicationReceipt: ...

    def quarantine_workspace(
        self,
        *,
        workspace_id: str,
        commit_id: str | None,
        expected_workspace_compare_version: int,
        active_fencing_token: int,
        reason_digest: str,
    ) -> WorkspaceQuarantineReceipt: ...

    def claim_workspace_commit_for_recovery(
        self,
        request: WorkspaceCommitRecoveryClaimRequest,
    ) -> WorkspaceCommitRecoveryClaim: ...

    def record_evidence(
        self,
        *,
        draft: EvidenceRecordDraft,
        expected_projection_compare_version: int,
    ) -> EvidenceRecordRecording: ...

    def seal_epoch(
        self,
        *,
        completion_epoch_id: str,
        expected_compare_version: int,
        required_projections: tuple[RequiredProjection, ...],
    ) -> EpochSeal: ...

    def persist_completion(
        self,
        seal: EpochSeal,
        request: FinalizationRequest,
        verdict: CompletionVerdict,
    ) -> CompletionPersistenceReceipt: ...


@runtime_checkable
class AuthorityPort(Protocol):
    def decide(self, intent: ActionIntent) -> AuthorityDecision: ...


@runtime_checkable
class CompletionEvaluatorPort(Protocol):
    """Evaluates only requests already bound to a durable sealing epoch."""

    def evaluate(self, evaluation: FinalizationEvaluationRequest) -> CompletionVerdict: ...


@runtime_checkable
class UserDecisionVerifierPort(Protocol):
    def verify(
        self,
        *,
        opaque_envelope: object,
        request: UserDecisionRequest,
    ) -> UserDecisionReceipt: ...


@runtime_checkable
class UserDecisionKeyPort(Protocol):
    def key_for(
        self,
        *,
        key_id: str,
        tenant_id: str,
        principal_id: str,
        authentication_context_digest: str,
    ) -> bytes | None: ...


@runtime_checkable
class ResumeBindingVerifierPort(Protocol):
    def verify_current(
        self,
        binding: AuthorityResumeBinding,
    ) -> AuthorityResumeBinding: ...


@runtime_checkable
class NormalizedInputNormalizerPort(Protocol):
    """Normalizes untrusted input without receiving trusted proposal context."""

    def normalize(
        self,
        *,
        untrusted_request: Mapping[str, object],
        declaration: EffectDeclarationBinding,
    ) -> NormalizedInputDraft: ...


@runtime_checkable
class NormalizedInputSnapshotPort(Protocol):
    """Persists and resolves content-addressed normalizer output."""

    def persist(self, draft: NormalizedInputDraft) -> NormalizedInputSnapshot: ...

    def resolve(
        self,
        *,
        snapshot_ref: str,
        expected_normalized_input_digest: str,
    ) -> NormalizedInputSnapshot: ...


@runtime_checkable
class EffectExecutorPort(Protocol):
    async def execute(
        self,
        *,
        execution_token: str,
        start: ExecutionStart,
    ) -> BackendObservation: ...


@runtime_checkable
class RecoveryAdapterPort(Protocol):
    """Backend-specific reconciliation under a fresh recovery attempt."""

    async def reconcile(
        self,
        *,
        decision: RecoveryDecision,
        context: RecoveryContext,
    ) -> BackendObservation: ...


@runtime_checkable
class EvidencePayloadResolverPort(Protocol):
    def resolve_payload(self, producer_payload_digest: str) -> bytes: ...


@runtime_checkable
class EvidenceQueryPort(Protocol):
    def resolve_node(self, evidence_id: str) -> EvidenceNode | None: ...

    def edges_for_node(
        self,
        *,
        evidence_id: str,
    ) -> tuple[EvidenceEdge, ...]:
        """Return every stored edge incident to the evidence node."""

        ...

    def nodes_for_requirement(
        self,
        *,
        task_contract_id: str,
        requirement_id: str,
        state_root: str,
    ) -> tuple[EvidenceNode, ...]: ...


@runtime_checkable
class SourceSnapshotResolverPort(Protocol):
    def resolve_snapshot(
        self,
        *,
        source_snapshot_id: str,
        source_snapshot_digest: str,
    ) -> str: ...

    def validate_span(self, span: SourceSpan) -> SourceSpan: ...


@runtime_checkable
class WorkspacePublicationPort(Protocol):
    def publish(
        self,
        *,
        decision: WorkspaceCommitDecision,
        recovery_claim: WorkspaceCommitRecoveryClaim | None,
        workspace_view_binding_digest: str,
    ) -> WorkspacePublicationObservation: ...
