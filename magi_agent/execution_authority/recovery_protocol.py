"""Replay-complete contracts for fenced recovery execution.

The legacy recovery envelope summarizes store state with booleans and digests.
This module is the structural protocol used at the recovery boundary: reducers
receive the exact immutable snapshots they evaluated, recovery authority binds
that context and its decision, and a new physical attempt has its own intent,
prepare receipt, execution grant, and start receipt.

There is deliberately no persistence or executor implementation here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self, cast

from pydantic import Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.contracts import (
    AuthorityContract,
    canonical_authority_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionIntent,
    ActionSnapshot,
    AttemptSnapshot,
    EnvelopeModel,
    IntegrityScanResult,
    JournalEvent,
    JournalHead,
    LeaseSnapshot,
    NonExecutionProof,
    PartitionGate,
    PartitionRecoveryPlan,
    ProjectionCursorSnapshot,
    WorkspaceCommitSnapshot,
    WorkspaceSnapshot,
    _canonical_model_digest,
    _strict_json_loads,
    canonical_action_intent_digest,
)
from magi_agent.execution_authority.state_machine import (
    ActionState,
    LeaseState,
    RecoveryStrategy,
    UserDecisionState,
    WorkspacePublicationState,
)


class RecoveryDisposition(StrEnum):
    """Closed decisions emitted by the replay-complete recovery reducer."""

    ABORT = "abort"
    REPLAY = "replay"
    RECONCILE = "reconcile"
    REDO_COMMIT = "redo_commit"
    CONFIRM_COMMIT = "confirm_commit"
    REBUILD_PROJECTIONS = "rebuild_projections"
    QUARANTINE = "quarantine"


_ATTEMPT_CREATING_DISPOSITIONS = frozenset(
    {
        RecoveryDisposition.REPLAY,
        RecoveryDisposition.RECONCILE,
        RecoveryDisposition.REDO_COMMIT,
    }
)


def _derive_digest(model: EnvelopeModel, field_name: str) -> str:
    return _canonical_model_digest(model, exclude=frozenset({field_name}))


def _set_or_check_digest(
    model: EnvelopeModel,
    *,
    field_name: str,
    alias: str,
) -> None:
    expected = _derive_digest(model, field_name)
    observed = getattr(model, field_name)
    if observed is not None and observed != expected:
        raise ValueError(f"{alias} does not match the exact recovery envelope")
    object.__setattr__(model, field_name, expected)


def _require_ordered_nonempty_strings(value: object, info: ValidationInfo) -> object:
    if type(value) not in (list, tuple):
        raise ValueError(f"{info.field_name} must use an ordered list or tuple")
    items = cast(list[object] | tuple[object, ...], value)
    if any(type(item) is not str or not cast(str, item).strip() for item in items):
        raise ValueError(f"{info.field_name} must contain non-empty exact strings")
    strings = tuple(cast(str, item) for item in items)
    if len(strings) != len(set(strings)):
        raise ValueError(f"{info.field_name} must not contain duplicates")
    return value


def _effective_actor(authority: AuthorityContract) -> str:
    return authority.child_actor_id or authority.principal_id


def _require_equal_bindings(
    bindings: tuple[tuple[str, object, object], ...],
    *,
    message: str,
) -> None:
    for alias, observed, expected in bindings:
        if observed != expected:
            raise ValueError(message.format(alias=alias))


class OldExecutorFenceAcknowledgement(EnvelopeModel):
    """Evidence that the old executor observed and yielded to a newer fence."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    acknowledgement_id: str = Field(alias="acknowledgementId", min_length=1)
    source_attempt_id: str = Field(alias="sourceAttemptId", min_length=1)
    old_executor_id: str = Field(alias="oldExecutorId", min_length=1)
    old_executor_version: str = Field(alias="oldExecutorVersion", min_length=1)
    old_fencing_token: int = Field(alias="oldFencingToken", ge=1, strict=True)
    superseding_recovery_fencing_token: int = Field(
        alias="supersedingRecoveryFencingToken",
        ge=1,
        strict=True,
    )
    acknowledgement_evidence_digest: str = Field(alias="acknowledgementEvidenceDigest")
    observed_at: datetime = Field(alias="observedAt")
    acknowledgement_digest: str | None = Field(
        default=None,
        alias="acknowledgementDigest",
    )

    @model_validator(mode="after")
    def _bind_superseding_fence(self) -> Self:
        if self.superseding_recovery_fencing_token <= self.old_fencing_token:
            raise ValueError("superseding recovery fence must exceed the old executor fence")
        _set_or_check_digest(
            self,
            field_name="acknowledgement_digest",
            alias="acknowledgementDigest",
        )
        return self


class RecoveryNonExecutionProof(EnvelopeModel):
    """Non-execution proof plus explicit acknowledgement of fence replacement."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    proof: NonExecutionProof
    old_executor_fence_acknowledgement: OldExecutorFenceAcknowledgement = Field(
        alias="oldExecutorFenceAcknowledgement"
    )
    recovery_proof_digest: str | None = Field(default=None, alias="recoveryProofDigest")

    @model_validator(mode="after")
    def _bind_proof_and_acknowledgement(self) -> Self:
        acknowledgement = self.old_executor_fence_acknowledgement
        if acknowledgement.source_attempt_id != self.proof.source_attempt_id:
            raise ValueError("old executor acknowledgement sourceAttemptId does not match proof")
        if acknowledgement.observed_at < self.proof.observed_at:
            raise ValueError("old executor acknowledgement cannot predate its non-execution proof")
        _set_or_check_digest(
            self,
            field_name="recovery_proof_digest",
            alias="recoveryProofDigest",
        )
        return self


class RecoveryUserDecisionSnapshot(EnvelopeModel):
    """Minimal persisted user-decision state consumed by recovery policy."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    decision_request_id: str = Field(alias="decisionRequestId", min_length=1)
    decision_request_digest: str = Field(alias="decisionRequestDigest")
    state: UserDecisionState
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)


class RecoveryWorkspaceState(EnvelopeModel):
    """The source view, current view, and durable commit record as one snapshot."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    source_snapshot: WorkspaceSnapshot = Field(alias="sourceSnapshot")
    current_snapshot: WorkspaceSnapshot = Field(alias="currentSnapshot")
    commit_snapshot: WorkspaceCommitSnapshot | None = Field(
        default=None,
        alias="commitSnapshot",
    )
    workspace_state_digest: str | None = Field(default=None, alias="workspaceStateDigest")

    @model_validator(mode="after")
    def _bind_workspace_history(self) -> Self:
        source = self.source_snapshot
        current = self.current_snapshot
        identities = (
            ("workspaceId", current.workspace_id, source.workspace_id),
            ("workspaceRef", current.workspace_ref, source.workspace_ref),
            (
                "authorityPartitionId",
                current.authority_partition_id,
                source.authority_partition_id,
            ),
        )
        for alias, observed, expected in identities:
            if observed != expected:
                raise ValueError(f"current workspace {alias} does not match source snapshot")

        commit = self.commit_snapshot
        if commit is None:
            if current != source:
                raise ValueError("workspace without a commit must preserve the exact source view")
        else:
            request = commit.request
            request_bindings = (
                ("workspaceId", request.workspace_id, source.workspace_id),
                ("workspaceRef", request.workspace_ref, source.workspace_ref),
                (
                    "authorityPartitionId",
                    request.authority_partition_id,
                    source.authority_partition_id,
                ),
                ("expectedGeneration", request.expected_generation, source.current_generation),
                ("stateRootBefore", request.state_root_before, source.state_root),
                (
                    "workspaceViewBindingDigest",
                    request.workspace_view_binding_digest,
                    source.workspace_view_binding_digest,
                ),
            )
            _require_equal_bindings(
                request_bindings,
                message="workspace commit {alias} does not match source snapshot",
            )

            if commit.state == "decided":
                decided_bindings = (
                    (
                        "publicationState",
                        current.publication_state,
                        WorkspacePublicationState.PUBLISHING,
                    ),
                    ("activeCommitId", current.active_commit_id, request.commit_id),
                    (
                        "pendingGeneration",
                        current.pending_generation,
                        request.target_generation,
                    ),
                    ("pendingStateRoot", current.pending_state_root, request.state_root_after),
                    (
                        "activeFencingToken",
                        current.active_fencing_token,
                        commit.active_fencing_token,
                    ),
                )
                _require_equal_bindings(
                    decided_bindings,
                    message="decided workspace commit {alias} does not match current snapshot",
                )
            elif commit.state == "published":
                published_bindings = (
                    (
                        "publicationState",
                        current.publication_state,
                        WorkspacePublicationState.READY,
                    ),
                    ("currentGeneration", current.current_generation, request.target_generation),
                    ("stateRoot", current.state_root, request.state_root_after),
                )
                _require_equal_bindings(
                    published_bindings,
                    message="published workspace commit {alias} does not match current snapshot",
                )
            elif current.publication_state is not WorkspacePublicationState.QUARANTINED:
                raise ValueError("quarantined commit requires a quarantined workspace snapshot")

        _set_or_check_digest(
            self,
            field_name="workspace_state_digest",
            alias="workspaceStateDigest",
        )
        return self


class ReplayCompleteRecoveryContext(EnvelopeModel):
    """All immutable inputs required to deterministically replay recovery policy."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    context_id: str = Field(alias="contextId", min_length=1)
    evaluated_at: datetime = Field(alias="evaluatedAt")
    plan: PartitionRecoveryPlan
    gate: PartitionGate
    lease: LeaseSnapshot
    source_intent: ActionIntent = Field(alias="sourceIntent")
    action_snapshot: ActionSnapshot = Field(alias="actionSnapshot")
    source_attempt_snapshot: AttemptSnapshot = Field(alias="sourceAttemptSnapshot")
    journal_head: JournalHead = Field(alias="journalHead")
    workspace: RecoveryWorkspaceState | None = None
    integrity_scan: IntegrityScanResult = Field(alias="integrityScan")
    projection_cursors: tuple[ProjectionCursorSnapshot, ...] = Field(alias="projectionCursors")
    user_decision: RecoveryUserDecisionSnapshot | None = Field(
        default=None,
        alias="userDecision",
    )
    non_execution_proof: RecoveryNonExecutionProof | None = Field(
        default=None,
        alias="nonExecutionProof",
    )
    current_policy_digest: str = Field(alias="currentPolicyDigest")
    current_sandbox_profile_digest: str = Field(alias="currentSandboxProfileDigest")
    context_digest: str | None = Field(default=None, alias="contextDigest")

    @field_validator("projection_cursors", mode="before")
    @classmethod
    def _require_ordered_projection_snapshots(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("projectionCursors must use an ordered list or tuple")
        return value

    @property
    def has_current_positive_recovery_fence(self) -> bool:
        return bool(
            self.gate.state == "recovering"
            and self.lease.state is LeaseState.HELD
            and self.gate.recovery_owner_id is not None
            and self.gate.recovery_owner_id == self.lease.owner_id
            and self.gate.recovery_fencing_token > 0
            and self.gate.recovery_fencing_token == self.lease.fencing_token
            and self.lease.high_water_fencing_token == self.lease.fencing_token
            and self.lease.expires_at is not None
            and self.lease.expires_at > self.evaluated_at
        )

    @property
    def has_pending_user_decision(self) -> bool:
        return bool(
            self.user_decision is not None and self.user_decision.state is UserDecisionState.PENDING
        )

    @property
    def workspace_commit_state(self) -> str | None:
        if self.workspace is None or self.workspace.commit_snapshot is None:
            return None
        return self.workspace.commit_snapshot.state

    @property
    def projections_are_lagging(self) -> bool:
        return any(
            cursor.partition_id == self.journal_head.partition_id
            and cursor.acknowledged_sequence < self.journal_head.sequence
            for cursor in self.projection_cursors
        )

    @model_validator(mode="after")
    def _bind_replay_complete_snapshots(self) -> Self:
        intent = self.source_intent
        intent_digest = canonical_action_intent_digest(intent)
        plan_bindings = (
            ("partitionId", self.plan.partition_id, intent.partition_id),
            ("taskContractDigest", self.plan.task_contract_digest, intent.task_contract_digest),
        )
        for alias, observed, expected in plan_bindings:
            if observed != expected:
                raise ValueError(f"recovery plan {alias} does not match source intent")
        if intent.attempt_id not in self.plan.selected_source_attempt_ids:
            raise ValueError("recovery plan does not select the source attempt")

        gate_bindings = (
            ("partitionId", self.gate.partition_id, self.plan.partition_id),
            ("recoveryEpochId", self.gate.recovery_epoch_id, self.plan.recovery_epoch_id),
            (
                "recoveryPlanDigest",
                self.gate.recovery_plan_digest,
                self.plan.recovery_plan_digest,
            ),
        )
        _require_equal_bindings(
            gate_bindings,
            message="partition gate {alias} does not match recovery plan",
        )
        if self.gate.state != "recovering" or self.gate.recovery_owner_id is None:
            raise ValueError("recovery context requires a recovering partition gate")

        lease_bindings = (
            ("partitionId", self.lease.partition_id, self.plan.partition_id),
            ("leaseName", self.lease.lease_name, "partition-recovery"),
            ("ownerId", self.lease.owner_id, self.gate.recovery_owner_id),
            ("fencingToken", self.lease.fencing_token, self.gate.recovery_fencing_token),
            (
                "highWaterFencingToken",
                self.lease.high_water_fencing_token,
                self.gate.recovery_fencing_token,
            ),
        )
        _require_equal_bindings(
            lease_bindings,
            message="recovery lease {alias} does not match partition gate",
        )

        action_bindings = (
            ("actionId", self.action_snapshot.action_id, intent.action_id),
            ("partitionId", self.action_snapshot.partition_id, intent.partition_id),
            (
                "taskContractDigest",
                self.action_snapshot.task_contract_digest,
                intent.task_contract_digest,
            ),
            (
                "completionEpochId",
                self.action_snapshot.completion_epoch_id,
                intent.completion_epoch_id,
            ),
            (
                "admissionSequence",
                self.action_snapshot.admission_sequence,
                intent.admission_sequence,
            ),
            ("intentDigest", self.action_snapshot.intent_digest, intent_digest),
        )
        _require_equal_bindings(
            action_bindings,
            message="action snapshot {alias} does not match source intent",
        )

        attempt_bindings = (
            ("actionId", self.source_attempt_snapshot.action_id, intent.action_id),
            ("attemptId", self.source_attempt_snapshot.attempt_id, intent.attempt_id),
            ("partitionId", self.source_attempt_snapshot.partition_id, intent.partition_id),
            (
                "taskContractDigest",
                self.source_attempt_snapshot.task_contract_digest,
                intent.task_contract_digest,
            ),
            (
                "actionIntentDigest",
                self.source_attempt_snapshot.action_intent_digest,
                intent_digest,
            ),
            (
                "requestDigest",
                self.source_attempt_snapshot.request_digest,
                intent.normalized_input_digest,
            ),
        )
        for alias, observed, expected in attempt_bindings:
            if observed != expected:
                raise ValueError(f"source attempt snapshot {alias} does not match source intent")

        if self.journal_head.partition_id != intent.partition_id:
            raise ValueError("journal head partitionId does not match source intent")
        integrity_bindings = (
            ("partitionId", self.integrity_scan.partition_id, self.journal_head.partition_id),
            (
                "scannedThroughSequence",
                self.integrity_scan.scanned_through_sequence,
                self.journal_head.sequence,
            ),
            (
                "scannedHeadHash",
                self.integrity_scan.scanned_head_hash,
                self.journal_head.event_hash,
            ),
        )
        _require_equal_bindings(
            integrity_bindings,
            message="integrity scan {alias} does not match captured journal head",
        )
        if self.integrity_scan.scanned_at > self.evaluated_at:
            raise ValueError("integrity scan cannot postdate context evaluatedAt")

        projection_keys = tuple(
            (cursor.partition_id, cursor.projection_id) for cursor in self.projection_cursors
        )
        required_keys = tuple(
            (projection.partition_id, projection.projection_id)
            for projection in self.plan.required_projections
        )
        if projection_keys != required_keys:
            raise ValueError("projectionCursors must exactly cover the recovery plan")
        if len(projection_keys) != len(set(projection_keys)):
            raise ValueError("projectionCursors must be unique")
        if any(
            cursor.partition_id == self.journal_head.partition_id
            and cursor.acknowledged_sequence > self.journal_head.sequence
            for cursor in self.projection_cursors
        ):
            raise ValueError("projection cursor cannot advance beyond the captured journal head")

        if intent.workspace_view_binding_digest is None:
            if self.workspace is not None:
                raise ValueError("non-workspace intent cannot carry workspace recovery snapshots")
        else:
            if self.workspace is None:
                raise ValueError("workspace intent requires typed workspace recovery snapshots")
            source_workspace = self.workspace.source_snapshot
            if (
                source_workspace.workspace_view_binding_digest
                != intent.workspace_view_binding_digest
            ):
                raise ValueError("source workspace view does not match source intent")
            if source_workspace.authority_partition_id != intent.partition_id:
                raise ValueError("source workspace partition does not match source intent")
            commit = self.workspace.commit_snapshot
            if commit is not None:
                if (
                    commit.request.action_id != intent.action_id
                    or commit.request.attempt_id != intent.attempt_id
                ):
                    raise ValueError("workspace commit does not belong to the source attempt")
            current_root = self.workspace.current_snapshot.state_root
            if any(cursor.state_root != current_root for cursor in self.projection_cursors):
                raise ValueError("projection cursor stateRoot does not match current workspace")

        proof = self.non_execution_proof
        if proof is not None:
            base = proof.proof
            proof_bindings = (
                ("partitionId", base.partition_id, intent.partition_id),
                ("actionId", base.action_id, intent.action_id),
                ("sourceAttemptId", base.source_attempt_id, intent.attempt_id),
                (
                    "expectedSourceState",
                    base.expected_source_state,
                    self.source_attempt_snapshot.state,
                ),
                (
                    "expectedSourceVersion",
                    base.expected_source_version,
                    self.source_attempt_snapshot.compare_version,
                ),
                (
                    "taskContractDigest",
                    base.task_contract_digest,
                    intent.task_contract_digest,
                ),
                (
                    "actionSnapshotDigest",
                    base.action_snapshot_digest,
                    _canonical_model_digest(self.action_snapshot),
                ),
                (
                    "attemptSnapshotDigest",
                    base.attempt_snapshot_digest,
                    _canonical_model_digest(self.source_attempt_snapshot),
                ),
                (
                    "journalHeadDigest",
                    base.journal_head_digest,
                    _canonical_model_digest(self.journal_head),
                ),
            )
            _require_equal_bindings(
                proof_bindings,
                message="non-execution proof {alias} does not match context",
            )
            acknowledgement = proof.old_executor_fence_acknowledgement
            if acknowledgement.old_fencing_token != self.source_attempt_snapshot.fencing_token:
                raise ValueError("old executor acknowledgement does not bind source attempt fence")
            if acknowledgement.superseding_recovery_fencing_token != self.lease.fencing_token:
                raise ValueError("old executor acknowledgement does not bind recovery fence")
            if (
                base.observed_at > self.evaluated_at
                or acknowledgement.observed_at > self.evaluated_at
            ):
                raise ValueError("non-execution proof observedAt cannot postdate evaluatedAt")

        _set_or_check_digest(self, field_name="context_digest", alias="contextDigest")
        return self


class RecoveryDecision(EnvelopeModel):
    """Pure reducer output for one exact replay-complete context."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    decision_id: str = Field(alias="decisionId", min_length=1)
    context: ReplayCompleteRecoveryContext
    disposition: RecoveryDisposition
    terminalize_source_to: ActionState | None = Field(
        default=None,
        alias="terminalizeSourceTo",
    )
    resolution_attempt_id: str | None = Field(
        default=None,
        alias="resolutionAttemptId",
        min_length=1,
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes", min_length=1)
    decision_digest: str | None = Field(default=None, alias="decisionDigest")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object, info: ValidationInfo) -> object:
        return _require_ordered_nonempty_strings(value, info)

    @model_validator(mode="after")
    def _validate_reducer_output(self) -> Self:
        context = self.context
        intent = context.source_intent
        expected_id = (
            f"recovery:{context.plan.recovery_epoch_id}:{intent.action_id}:{intent.attempt_id}"
        )
        if self.decision_id != expected_id:
            raise ValueError("decisionId does not match the recovery CAS identity")

        # Every disposition in this enum changes durable state.  Check the fence
        # first so no semantic branch can accidentally bypass fencing.
        if not context.has_current_positive_recovery_fence:
            raise ValueError("recovery decision requires a current positive recovery fence")
        if self.disposition is RecoveryDisposition.RECONCILE and context.has_pending_user_decision:
            raise ValueError("RECONCILE cannot bypass a pending user decision")
        if context.integrity_scan.status != "clean":
            if self.disposition is not RecoveryDisposition.QUARANTINE:
                raise ValueError("non-clean integrity state can only be quarantined")

        creates_attempt = self.disposition in _ATTEMPT_CREATING_DISPOSITIONS
        if creates_attempt != (self.resolution_attempt_id is not None):
            raise ValueError(
                "resolutionAttemptId is required exactly for replay, reconcile, and redo"
            )
        if self.resolution_attempt_id == intent.attempt_id:
            raise ValueError("resolutionAttemptId must differ from sourceAttemptId")

        if self.disposition is RecoveryDisposition.ABORT:
            if self.terminalize_source_to is not ActionState.ABORTED:
                raise ValueError("ABORT must terminalize the source attempt as ABORTED")
            if (
                context.source_attempt_snapshot.state
                in {ActionState.PREPARED, ActionState.EXECUTING, ActionState.OBSERVED}
                and context.non_execution_proof is None
            ):
                raise ValueError("ABORT requires mechanical non-execution proof")
        elif self.disposition is RecoveryDisposition.REPLAY:
            if (
                intent.declaration.recovery_strategy is not RecoveryStrategy.READ_ONLY_REPLAY
                or context.non_execution_proof is None
                or context.workspace_commit_state is not None
                or context.has_pending_user_decision
                or context.current_policy_digest != intent.policy_digest
            ):
                raise ValueError("REPLAY is not safe for the exact recovery context")
            if self.terminalize_source_to is not ActionState.ABORTED:
                raise ValueError("REPLAY must terminalize the old physical attempt")
        elif self.disposition is RecoveryDisposition.RECONCILE:
            if (
                intent.declaration.recovery_strategy is not RecoveryStrategy.PROVIDER_RECONCILIATION
                or intent.declaration.recovery_adapter_digest is None
                or context.current_policy_digest != intent.policy_digest
            ):
                raise ValueError("RECONCILE requires provider reconciliation authority")
            if self.terminalize_source_to is not ActionState.UNKNOWN:
                raise ValueError("RECONCILE must preserve uncertainty on the source attempt")
        elif self.disposition is RecoveryDisposition.REDO_COMMIT:
            if context.workspace_commit_state != "decided":
                raise ValueError("REDO_COMMIT requires a durable decided workspace commit")
            if self.terminalize_source_to is not ActionState.UNKNOWN:
                raise ValueError("REDO_COMMIT must preserve uncertainty on the source attempt")
        elif self.disposition is RecoveryDisposition.CONFIRM_COMMIT:
            if context.workspace_commit_state != "published":
                raise ValueError("CONFIRM_COMMIT requires a published workspace commit")
            if self.terminalize_source_to is not ActionState.COMMITTED:
                raise ValueError("CONFIRM_COMMIT must terminalize the source as COMMITTED")
        elif self.disposition is RecoveryDisposition.REBUILD_PROJECTIONS:
            if not context.projections_are_lagging:
                raise ValueError("REBUILD_PROJECTIONS requires typed lagging projection cursors")
            if self.terminalize_source_to is not None:
                raise ValueError("REBUILD_PROJECTIONS cannot rewrite source attempt state")
        elif self.terminalize_source_to is not None:
            raise ValueError("QUARANTINE cannot invent a source terminal state")

        _set_or_check_digest(self, field_name="decision_digest", alias="decisionDigest")
        return self


class RecoveryAttemptIntent(EnvelopeModel):
    """A new physical attempt that preserves the immutable logical action."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    attempt_intent_id: str = Field(alias="attemptIntentId", min_length=1)
    source_intent: ActionIntent = Field(alias="sourceIntent")
    context: ReplayCompleteRecoveryContext
    decision: RecoveryDecision
    action_id: str = Field(alias="actionId", min_length=1)
    source_attempt_id: str = Field(alias="sourceAttemptId", min_length=1)
    resolution_attempt_id: str = Field(alias="resolutionAttemptId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    normalized_request_digest: str = Field(alias="normalizedRequestDigest")
    policy_digest: str = Field(alias="policyDigest")
    identity_digest: str = Field(alias="identityDigest")
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )
    attempt_intent_digest: str | None = Field(default=None, alias="attemptIntentDigest")

    @model_validator(mode="after")
    def _preserve_logical_identity(self) -> Self:
        intent = self.source_intent
        if self.decision.disposition not in _ATTEMPT_CREATING_DISPOSITIONS:
            raise ValueError("recovery attempt intent requires an attempt-creating disposition")
        if self.context.context_digest != self.decision.context.context_digest:
            raise ValueError("decision does not bind the RecoveryAttemptIntent context")
        if canonical_action_intent_digest(intent) != canonical_action_intent_digest(
            self.context.source_intent
        ):
            raise ValueError("sourceIntent does not match the recovery context")
        bindings = (
            ("actionId", self.action_id, intent.action_id),
            ("sourceAttemptId", self.source_attempt_id, intent.attempt_id),
            (
                "resolutionAttemptId",
                self.resolution_attempt_id,
                self.decision.resolution_attempt_id,
            ),
            ("partitionId", self.partition_id, intent.partition_id),
            ("taskContractDigest", self.task_contract_digest, intent.task_contract_digest),
            (
                "normalizedRequestDigest",
                self.normalized_request_digest,
                intent.normalized_input_digest,
            ),
            ("policyDigest", self.policy_digest, intent.policy_digest),
            ("identityDigest", self.identity_digest, intent.identity_digest),
            (
                "workspaceViewBindingDigest",
                self.workspace_view_binding_digest,
                intent.workspace_view_binding_digest,
            ),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"RecoveryAttemptIntent {alias} does not preserve source intent")
        if self.resolution_attempt_id == self.source_attempt_id:
            raise ValueError("resolutionAttemptId must differ from sourceAttemptId")
        _set_or_check_digest(
            self,
            field_name="attempt_intent_digest",
            alias="attemptIntentDigest",
        )
        return self


class RecoveryAuthorityBinding(EnvelopeModel):
    """First-party binding from ordinary authority to one recovery decision."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    binding_id: str = Field(alias="bindingId", min_length=1)
    context: ReplayCompleteRecoveryContext
    decision: RecoveryDecision
    authority_contract: AuthorityContract = Field(alias="authorityContract")
    authority_contract_digest: str = Field(alias="authorityContractDigest")
    recovery_epoch_id: str = Field(alias="recoveryEpochId", min_length=1)
    recovery_plan_digest: str = Field(alias="recoveryPlanDigest")
    recovery_owner_id: str = Field(alias="recoveryOwnerId", min_length=1)
    context_digest: str = Field(alias="contextDigest")
    decision_digest: str = Field(alias="decisionDigest")
    bound_at: datetime = Field(alias="boundAt")
    binding_digest: str | None = Field(default=None, alias="bindingDigest")

    @model_validator(mode="after")
    def _bind_authority_to_recovery(self) -> Self:
        context = self.context
        decision = self.decision
        intent = context.source_intent
        authority = self.authority_contract
        if context.context_digest != decision.context.context_digest:
            raise ValueError("authority decision context does not match binding context")
        bindings = (
            ("recoveryEpochId", self.recovery_epoch_id, context.plan.recovery_epoch_id),
            (
                "recoveryPlanDigest",
                self.recovery_plan_digest,
                context.plan.recovery_plan_digest,
            ),
            ("recoveryOwnerId", self.recovery_owner_id, context.gate.recovery_owner_id),
            ("contextDigest", self.context_digest, context.context_digest),
            ("decisionDigest", self.decision_digest, decision.decision_digest),
            (
                "authorityContractDigest",
                self.authority_contract_digest,
                canonical_authority_contract_digest(authority),
            ),
            ("authority.effectiveActor", _effective_actor(authority), intent.actor_id),
            ("authority.sessionId", authority.session_id, intent.session_id),
            ("authority.turnId", authority.turn_id, intent.turn_id),
            ("authority.actionId", authority.action_id, intent.action_id),
            (
                "authority.attemptId",
                authority.attempt_id,
                decision.resolution_attempt_id or intent.attempt_id,
            ),
            ("authority.partitionId", authority.authority_partition_id, intent.partition_id),
            ("authority.taskContractId", authority.task_contract_id, intent.task_contract_id),
            ("authority.taskVersion", authority.task_version, intent.task_version),
            (
                "authority.taskContractDigest",
                authority.task_contract_digest,
                intent.task_contract_digest,
            ),
            (
                "authority.completionEpochId",
                authority.completion_epoch_id,
                intent.completion_epoch_id,
            ),
            ("authority.policyDigest", authority.policy_digest, context.current_policy_digest),
            (
                "authority.normalizedRequestDigest",
                authority.normalized_request_digest,
                intent.normalized_input_digest,
            ),
            ("authority.capabilities", authority.capabilities, intent.capabilities),
            (
                "authority.workspaceViewBindingDigest",
                authority.workspace_view_binding_digest,
                intent.workspace_view_binding_digest,
            ),
            (
                "authority.sandboxProfileDigest",
                authority.sandbox_profile_digest,
                context.current_sandbox_profile_digest,
            ),
            ("authority.fencingToken", authority.fencing_token, context.lease.fencing_token),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"{alias} does not match recovery authority binding")
        if self.bound_at < context.evaluated_at:
            raise ValueError("boundAt cannot predate recovery context evaluation")
        if context.lease.expires_at is None or self.bound_at >= context.lease.expires_at:
            raise ValueError("recovery authority must be bound while the recovery lease is valid")
        if authority.revoked_at is not None or authority.expires_at <= self.bound_at:
            raise ValueError("recovery authority must be fresh and unrevoked at boundAt")
        _set_or_check_digest(self, field_name="binding_digest", alias="bindingDigest")
        return self


class BeginRecoveryReceipt(EnvelopeModel):
    """Atomic decision/CAS/event receipt returned by begin-recovery storage."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    decision: RecoveryDecision
    authority_binding: RecoveryAuthorityBinding = Field(alias="authorityBinding")
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion", ge=0, strict=True
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    expected_source_attempt_compare_version: int = Field(
        alias="expectedSourceAttemptCompareVersion", ge=0, strict=True
    )
    source_attempt_compare_version: int = Field(
        alias="sourceAttemptCompareVersion", ge=1, strict=True
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion", ge=0, strict=True
    )
    partition_compare_version: int = Field(alias="partitionCompareVersion", ge=1, strict=True)
    resolution_attempt_compare_version: int | None = Field(
        default=None,
        alias="resolutionAttemptCompareVersion",
        ge=1,
        strict=True,
    )
    recovery_event: JournalEvent = Field(alias="recoveryEvent")
    receipt_digest: str | None = Field(default=None, alias="receiptDigest")

    @model_validator(mode="after")
    def _validate_atomic_begin_receipt(self) -> Self:
        decision = self.decision
        context = decision.context
        binding = self.authority_binding
        if binding.decision_digest != decision.decision_digest:
            raise ValueError("authority binding does not match begin-recovery decision")
        expected_versions = (
            (
                "expectedActionCompareVersion",
                self.expected_action_compare_version,
                context.action_snapshot.compare_version,
            ),
            (
                "expectedSourceAttemptCompareVersion",
                self.expected_source_attempt_compare_version,
                context.source_attempt_snapshot.compare_version,
            ),
            (
                "expectedPartitionCompareVersion",
                self.expected_partition_compare_version,
                context.gate.compare_version,
            ),
        )
        _require_equal_bindings(
            expected_versions,
            message="{alias} does not match recovery context CAS snapshot",
        )
        advances = (
            (
                "actionCompareVersion",
                self.action_compare_version,
                self.expected_action_compare_version + 1,
            ),
            (
                "sourceAttemptCompareVersion",
                self.source_attempt_compare_version,
                self.expected_source_attempt_compare_version + 1,
            ),
            (
                "partitionCompareVersion",
                self.partition_compare_version,
                self.expected_partition_compare_version + 1,
            ),
        )
        _require_equal_bindings(
            advances,
            message="{alias} must advance its expected CAS version exactly once",
        )
        if (decision.resolution_attempt_id is not None) != (
            self.resolution_attempt_compare_version is not None
        ):
            raise ValueError(
                "resolutionAttemptCompareVersion is required exactly when a target is created"
            )
        if (
            self.resolution_attempt_compare_version is not None
            and self.resolution_attempt_compare_version != 1
        ):
            raise ValueError("new resolution attempt must begin at compareVersion 1")

        event = self.recovery_event
        intent = context.source_intent
        authority = binding.authority_contract
        event_bindings: tuple[tuple[str, object, object], ...] = (
            ("eventType", event.event_type, "recovery.begun"),
            ("actionId", event.action_id, intent.action_id),
            ("attemptId", event.attempt_id, intent.attempt_id),
            ("partitionId", event.partition_id, intent.partition_id),
            ("taskContractId", event.task_contract_id, intent.task_contract_id),
            ("taskVersion", event.task_version, intent.task_version),
            ("taskContractDigest", event.task_contract_digest, intent.task_contract_digest),
            ("completionEpochId", event.completion_epoch_id, intent.completion_epoch_id),
            ("admissionSequence", event.admission_sequence, intent.admission_sequence),
            ("authorityContractId", event.authority_contract_id, authority.authority_contract_id),
            ("requestDigest", event.request_digest, decision.decision_digest),
            ("idempotencyKeyDigest", event.idempotency_key_digest, intent.idempotency_key_digest),
            ("fencingToken", event.fencing_token, context.lease.fencing_token),
            ("actorId", event.actor_id, _effective_actor(authority)),
            ("policyDigest", event.policy_digest, context.current_policy_digest),
            ("causationId", event.causation_id, decision.decision_id),
            ("correlationId", event.correlation_id, intent.run_id),
            ("identityDigest", event.identity_digest, intent.identity_digest),
        )
        _require_equal_bindings(
            event_bindings,
            message="recoveryEvent.{alias} does not match begin receipt",
        )
        if event.sequence != context.journal_head.sequence + 1:
            raise ValueError("recoveryEvent must directly follow the captured journal head")
        if event.previous_hash != context.journal_head.event_hash:
            raise ValueError("recoveryEvent previousHash does not match captured journal head")
        if context.lease.expires_at is None or event.created_at >= context.lease.expires_at:
            raise ValueError("recoveryEvent must occur while the recovery lease is valid")
        if event.created_at < binding.bound_at or event.created_at >= authority.expires_at:
            raise ValueError("recoveryEvent must occur while bound recovery authority is valid")

        expected_payload: dict[str, object] = {
            "actionCompareVersion": self.action_compare_version,
            "authorityBindingDigest": binding.binding_digest,
            "contextDigest": context.context_digest,
            "decisionDigest": decision.decision_digest,
            "disposition": decision.disposition.value,
            "expectedActionCompareVersion": self.expected_action_compare_version,
            "expectedPartitionCompareVersion": self.expected_partition_compare_version,
            "expectedSourceAttemptCompareVersion": self.expected_source_attempt_compare_version,
            "partitionCompareVersion": self.partition_compare_version,
            "recoveryEpochId": context.plan.recovery_epoch_id,
            "recoveryOwnerId": context.gate.recovery_owner_id,
            "recoveryPlanDigest": context.plan.recovery_plan_digest,
            "resolutionAttemptCompareVersion": self.resolution_attempt_compare_version,
            "resolutionAttemptId": decision.resolution_attempt_id,
            "sourceAttemptCompareVersion": self.source_attempt_compare_version,
            "sourceAttemptId": intent.attempt_id,
        }
        if _strict_json_loads(event.payload_json) != expected_payload:
            raise ValueError("recoveryEvent payload does not exactly bind begin-recovery CAS")
        _set_or_check_digest(self, field_name="receipt_digest", alias="receiptDigest")
        return self


class RecoveryExecutionPreparation(EnvelopeModel):
    """Prepared target receipt; recovery cannot jump directly to an executor."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    attempt_intent: RecoveryAttemptIntent = Field(alias="attemptIntent")
    begin_receipt: BeginRecoveryReceipt = Field(alias="beginReceipt")
    authority_binding: RecoveryAuthorityBinding = Field(alias="authorityBinding")
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion", ge=1, strict=True
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    expected_resolution_attempt_compare_version: int = Field(
        alias="expectedResolutionAttemptCompareVersion", ge=1, strict=True
    )
    resolution_attempt_compare_version: int = Field(
        alias="resolutionAttemptCompareVersion", ge=1, strict=True
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion", ge=1, strict=True
    )
    partition_compare_version: int = Field(alias="partitionCompareVersion", ge=1, strict=True)
    prepared_event: JournalEvent = Field(alias="preparedEvent")
    preparation_digest: str | None = Field(default=None, alias="preparationDigest")

    @model_validator(mode="after")
    def _validate_prepare_receipt(self) -> Self:
        attempt = self.attempt_intent
        receipt = self.begin_receipt
        binding = self.authority_binding
        if receipt.receipt_digest is None or attempt.attempt_intent_digest is None:
            raise ValueError("recovery preparation inputs must have canonical digests")
        input_bindings = (
            (
                "decisionDigest",
                attempt.decision.decision_digest,
                receipt.decision.decision_digest,
            ),
            (
                "authorityBindingDigest",
                binding.binding_digest,
                receipt.authority_binding.binding_digest,
            ),
            (
                "expectedActionCompareVersion",
                self.expected_action_compare_version,
                receipt.action_compare_version,
            ),
            (
                "expectedResolutionAttemptCompareVersion",
                self.expected_resolution_attempt_compare_version,
                receipt.resolution_attempt_compare_version,
            ),
            (
                "expectedPartitionCompareVersion",
                self.expected_partition_compare_version,
                receipt.partition_compare_version,
            ),
        )
        _require_equal_bindings(
            input_bindings,
            message="RecoveryExecutionPreparation {alias} does not match begin receipt",
        )
        advances = (
            (
                "actionCompareVersion",
                self.action_compare_version,
                self.expected_action_compare_version + 1,
            ),
            (
                "resolutionAttemptCompareVersion",
                self.resolution_attempt_compare_version,
                self.expected_resolution_attempt_compare_version + 1,
            ),
            (
                "partitionCompareVersion",
                self.partition_compare_version,
                self.expected_partition_compare_version + 1,
            ),
        )
        _require_equal_bindings(
            advances,
            message="{alias} must advance its expected CAS version exactly once",
        )

        event = self.prepared_event
        intent = attempt.source_intent
        authority = binding.authority_contract
        event_bindings = (
            ("eventType", event.event_type, "recovery.action_prepared"),
            ("actionId", event.action_id, attempt.action_id),
            ("attemptId", event.attempt_id, attempt.resolution_attempt_id),
            ("partitionId", event.partition_id, attempt.partition_id),
            ("taskContractId", event.task_contract_id, intent.task_contract_id),
            ("taskVersion", event.task_version, intent.task_version),
            ("taskContractDigest", event.task_contract_digest, attempt.task_contract_digest),
            ("completionEpochId", event.completion_epoch_id, intent.completion_epoch_id),
            ("admissionSequence", event.admission_sequence, intent.admission_sequence),
            ("authorityContractId", event.authority_contract_id, authority.authority_contract_id),
            ("requestDigest", event.request_digest, attempt.normalized_request_digest),
            ("idempotencyKeyDigest", event.idempotency_key_digest, intent.idempotency_key_digest),
            ("fencingToken", event.fencing_token, authority.fencing_token),
            ("actorId", event.actor_id, _effective_actor(authority)),
            ("policyDigest", event.policy_digest, attempt.policy_digest),
            ("causationId", event.causation_id, receipt.recovery_event.event_id),
            ("correlationId", event.correlation_id, intent.run_id),
            ("identityDigest", event.identity_digest, attempt.identity_digest),
        )
        _require_equal_bindings(
            event_bindings,
            message="preparedEvent.{alias} does not match recovery target",
        )
        if event.sequence != receipt.recovery_event.sequence + 1:
            raise ValueError("preparedEvent must directly follow recoveryEvent")
        if event.previous_hash != receipt.recovery_event.event_hash:
            raise ValueError("preparedEvent previousHash must equal recoveryEvent eventHash")
        if event.created_at < receipt.recovery_event.created_at:
            raise ValueError("preparedEvent cannot predate recoveryEvent")
        if event.created_at >= authority.expires_at:
            raise ValueError("preparedEvent requires unexpired recovery authority")

        expected_payload = {
            "actionCompareVersion": self.action_compare_version,
            "attemptIntentDigest": attempt.attempt_intent_digest,
            "authorityBindingDigest": binding.binding_digest,
            "beginReceiptDigest": receipt.receipt_digest,
            "contextDigest": attempt.context.context_digest,
            "decisionDigest": attempt.decision.decision_digest,
            "expectedActionCompareVersion": self.expected_action_compare_version,
            "expectedPartitionCompareVersion": self.expected_partition_compare_version,
            "expectedResolutionAttemptCompareVersion": (
                self.expected_resolution_attempt_compare_version
            ),
            "partitionCompareVersion": self.partition_compare_version,
            "resolutionAttemptCompareVersion": self.resolution_attempt_compare_version,
        }
        if _strict_json_loads(event.payload_json) != expected_payload:
            raise ValueError("preparedEvent payload does not exactly bind recovery preparation")
        _set_or_check_digest(
            self,
            field_name="preparation_digest",
            alias="preparationDigest",
        )
        return self


class RecoveryExecutionGrant(EnvelopeModel):
    """Short-lived grant binding an attested executor to the prepared target."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    grant_id: str = Field(alias="grantId", min_length=1)
    preparation: RecoveryExecutionPreparation
    attempt_intent_digest: str = Field(alias="attemptIntentDigest")
    preparation_digest: str = Field(alias="preparationDigest")
    authority_binding_digest: str = Field(alias="authorityBindingDigest")
    executor_id: str = Field(alias="executorId", min_length=1)
    executor_version: str = Field(alias="executorVersion", min_length=1)
    executable_artifact_digest: str = Field(alias="executableArtifactDigest")
    sandbox_profile_digest: str = Field(alias="sandboxProfileDigest")
    execution_token_digest: str = Field(alias="executionTokenDigest")
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    grant_digest: str | None = Field(default=None, alias="grantDigest")

    @model_validator(mode="after")
    def _bind_grant_to_prepared_target(self) -> Self:
        preparation = self.preparation
        bindings = (
            (
                "attemptIntentDigest",
                self.attempt_intent_digest,
                preparation.attempt_intent.attempt_intent_digest,
            ),
            (
                "preparationDigest",
                self.preparation_digest,
                preparation.preparation_digest,
            ),
            (
                "authorityBindingDigest",
                self.authority_binding_digest,
                preparation.authority_binding.binding_digest,
            ),
            (
                "sandboxProfileDigest",
                self.sandbox_profile_digest,
                preparation.authority_binding.authority_contract.sandbox_profile_digest,
            ),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"{alias} does not match the prepared recovery target")
        if self.issued_at < preparation.prepared_event.created_at:
            raise ValueError("execution grant cannot predate preparedEvent")
        if self.expires_at <= self.issued_at:
            raise ValueError("execution grant expiresAt must be after issuedAt")
        if self.expires_at > preparation.authority_binding.authority_contract.expires_at:
            raise ValueError("execution grant cannot outlive recovery authority")
        _set_or_check_digest(self, field_name="grant_digest", alias="grantDigest")
        return self


class RecoveryExecutionStart(EnvelopeModel):
    """Atomic start receipt proving preparation and grant were both consumed."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    preparation: RecoveryExecutionPreparation
    grant: RecoveryExecutionGrant
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion", ge=1, strict=True
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    expected_resolution_attempt_compare_version: int = Field(
        alias="expectedResolutionAttemptCompareVersion", ge=1, strict=True
    )
    resolution_attempt_compare_version: int = Field(
        alias="resolutionAttemptCompareVersion", ge=1, strict=True
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion", ge=1, strict=True
    )
    partition_compare_version: int = Field(alias="partitionCompareVersion", ge=1, strict=True)
    executing_event: JournalEvent = Field(alias="executingEvent")
    start_digest: str | None = Field(default=None, alias="startDigest")

    @model_validator(mode="after")
    def _validate_execution_start(self) -> Self:
        preparation = self.preparation
        grant = self.grant
        if grant.preparation_digest != preparation.preparation_digest:
            raise ValueError("grant preparationDigest does not match execution start preparation")
        expected_versions = (
            (
                "expectedActionCompareVersion",
                self.expected_action_compare_version,
                preparation.action_compare_version,
            ),
            (
                "expectedResolutionAttemptCompareVersion",
                self.expected_resolution_attempt_compare_version,
                preparation.resolution_attempt_compare_version,
            ),
            (
                "expectedPartitionCompareVersion",
                self.expected_partition_compare_version,
                preparation.partition_compare_version,
            ),
        )
        _require_equal_bindings(
            expected_versions,
            message="{alias} does not match recovery preparation",
        )
        advances = (
            (
                "actionCompareVersion",
                self.action_compare_version,
                self.expected_action_compare_version + 1,
            ),
            (
                "resolutionAttemptCompareVersion",
                self.resolution_attempt_compare_version,
                self.expected_resolution_attempt_compare_version + 1,
            ),
            (
                "partitionCompareVersion",
                self.partition_compare_version,
                self.expected_partition_compare_version + 1,
            ),
        )
        _require_equal_bindings(
            advances,
            message="{alias} must advance its expected CAS version exactly once",
        )

        attempt = preparation.attempt_intent
        authority = preparation.authority_binding.authority_contract
        event = self.executing_event
        event_bindings = (
            ("eventType", event.event_type, "recovery.action_executing"),
            ("actionId", event.action_id, attempt.action_id),
            ("attemptId", event.attempt_id, attempt.resolution_attempt_id),
            ("partitionId", event.partition_id, attempt.partition_id),
            ("taskContractId", event.task_contract_id, attempt.source_intent.task_contract_id),
            ("taskVersion", event.task_version, attempt.source_intent.task_version),
            ("taskContractDigest", event.task_contract_digest, attempt.task_contract_digest),
            (
                "completionEpochId",
                event.completion_epoch_id,
                attempt.source_intent.completion_epoch_id,
            ),
            (
                "admissionSequence",
                event.admission_sequence,
                attempt.source_intent.admission_sequence,
            ),
            ("authorityContractId", event.authority_contract_id, authority.authority_contract_id),
            ("requestDigest", event.request_digest, attempt.normalized_request_digest),
            (
                "idempotencyKeyDigest",
                event.idempotency_key_digest,
                attempt.source_intent.idempotency_key_digest,
            ),
            ("fencingToken", event.fencing_token, authority.fencing_token),
            ("actorId", event.actor_id, _effective_actor(authority)),
            ("policyDigest", event.policy_digest, attempt.policy_digest),
            ("causationId", event.causation_id, preparation.prepared_event.event_id),
            ("correlationId", event.correlation_id, attempt.source_intent.run_id),
            ("identityDigest", event.identity_digest, attempt.identity_digest),
        )
        _require_equal_bindings(
            event_bindings,
            message="executingEvent.{alias} does not match recovery start",
        )
        if event.sequence != preparation.prepared_event.sequence + 1:
            raise ValueError("executingEvent must directly follow preparedEvent")
        if event.previous_hash != preparation.prepared_event.event_hash:
            raise ValueError("executingEvent previousHash must equal preparedEvent eventHash")
        if event.created_at < grant.issued_at or event.created_at >= grant.expires_at:
            raise ValueError("executingEvent must occur inside the execution grant window")

        expected_payload = {
            "actionCompareVersion": self.action_compare_version,
            "attemptIntentDigest": attempt.attempt_intent_digest,
            "authorityBindingDigest": preparation.authority_binding.binding_digest,
            "executableArtifactDigest": grant.executable_artifact_digest,
            "executionGrantDigest": grant.grant_digest,
            "executorId": grant.executor_id,
            "executorVersion": grant.executor_version,
            "expectedActionCompareVersion": self.expected_action_compare_version,
            "expectedPartitionCompareVersion": self.expected_partition_compare_version,
            "expectedResolutionAttemptCompareVersion": (
                self.expected_resolution_attempt_compare_version
            ),
            "partitionCompareVersion": self.partition_compare_version,
            "preparationDigest": preparation.preparation_digest,
            "resolutionAttemptCompareVersion": self.resolution_attempt_compare_version,
            "sandboxProfileDigest": grant.sandbox_profile_digest,
        }
        if _strict_json_loads(event.payload_json) != expected_payload:
            raise ValueError("executingEvent payload does not exactly bind recovery start")
        _set_or_check_digest(self, field_name="start_digest", alias="startDigest")
        return self


__all__ = [
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
]
