"""Dormant read-before-write and execution-observation wire contracts.

The contracts in this module describe evidence a future store/runtime adapter
must persist.  Importing them does not activate filesystem reads, execution, or
journal writes.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from magi_agent.execution_authority.canonicalization import (
    require_canonical_workspace_resource_ref,
)
from magi_agent.execution_authority.envelopes import (
    ActionReceipt,
    BackendObservation,
    EnvelopeModel,
    ExecutionPreparation,
    ExecutionStart,
    JournalEvent,
    VerificationEvidenceBinding,
    _require_direct_event_successor,
    _strict_json_loads,
    canonical_action_intent_digest,
    canonical_backend_observation_digest,
    canonical_workspace_view_binding_digest,
)
from magi_agent.execution_authority.state_machine import ActionState


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _model_digest(model: EnvelopeModel, *, exclude: frozenset[str] = frozenset()) -> str:
    payload = model.model_dump(by_alias=True, mode="json", exclude=set(exclude))
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _require_exact_cas_increment(
    *,
    name: str,
    expected: int,
    result: int,
) -> None:
    if result != expected + 1:
        raise ValueError(f"{name} must equal its expected version plus one")


def _require_chronological_successor(
    first: JournalEvent,
    second: JournalEvent,
    *,
    first_name: str,
    second_name: str,
) -> None:
    _require_direct_event_successor(
        first,
        second,
        first_name=first_name,
        second_name=second_name,
    )
    if second.created_at < first.created_at:
        raise ValueError(f"{second_name}.createdAt cannot precede {first_name}.createdAt")


class ResourceObservation(EnvelopeModel):
    """One physical path observation in a frozen workspace view."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    resource_ref: str = Field(alias="resourceRef", min_length=1)
    expected_presence: Literal["present", "absent"] = Field(alias="expectedPresence")
    observed_presence: Literal["present", "absent"] = Field(alias="observedPresence")
    content_digest: str | None = Field(default=None, alias="contentDigest")
    resource_state_digest: str = Field(alias="resourceStateDigest")
    workspace_generation: int = Field(alias="workspaceGeneration", ge=0, strict=True)
    workspace_state_root: str = Field(alias="workspaceStateRoot")
    observed_at: datetime = Field(alias="observedAt")
    observation_digest: str | None = Field(default=None, alias="observationDigest")

    @model_validator(mode="after")
    def _validate_resource_observation(self) -> Self:
        require_canonical_workspace_resource_ref(self.resource_ref)
        if self.observed_presence != self.expected_presence:
            raise ValueError("observedPresence must equal the declared precondition")
        if self.observed_presence == "present" and self.content_digest is None:
            raise ValueError("present resource observations require contentDigest")
        if self.observed_presence == "absent" and self.content_digest is not None:
            raise ValueError("absent resource observations cannot carry contentDigest")
        expected = _model_digest(self, exclude=frozenset({"observation_digest"}))
        if self.observation_digest is not None and self.observation_digest != expected:
            raise ValueError("observationDigest does not match ResourceObservation")
        object.__setattr__(self, "observation_digest", expected)
        return self


class WorkspacePreconditionObservation(EnvelopeModel):
    """Complete read/absence observation for one admitted action intent."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    observation_id: str = Field(alias="observationId", min_length=1)
    action_intent_digest: str = Field(alias="actionIntentDigest")
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    workspace_ref: str = Field(alias="workspaceRef", min_length=1)
    workspace_view_binding_digest: str = Field(alias="workspaceViewBindingDigest")
    workspace_generation: int = Field(alias="workspaceGeneration", ge=0, strict=True)
    workspace_state_root: str = Field(alias="workspaceStateRoot")
    observer_id: str = Field(alias="observerId", min_length=1)
    observer_version: str = Field(alias="observerVersion", min_length=1)
    observer_artifact_digest: str = Field(alias="observerArtifactDigest")
    observed_at: datetime = Field(alias="observedAt")
    resources: tuple[ResourceObservation, ...]
    observation_digest: str | None = Field(default=None, alias="observationDigest")

    @field_validator("resources", mode="before")
    @classmethod
    def _require_ordered_resources(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("resources must use an ordered list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_workspace_observation(self) -> Self:
        require_canonical_workspace_resource_ref(self.workspace_ref)
        if not self.workspace_ref.endswith("/"):
            raise ValueError("workspaceRef must identify a canonical workspace root")
        expected_view_digest = canonical_workspace_view_binding_digest(
            workspace_id=self.workspace_id,
            workspace_ref=self.workspace_ref,
            authority_partition_id=self.authority_partition_id,
            generation=self.workspace_generation,
            state_root=self.workspace_state_root,
        )
        if self.workspace_view_binding_digest != expected_view_digest:
            raise ValueError("workspace view coordinates do not match workspaceViewBindingDigest")

        refs = tuple(resource.resource_ref for resource in self.resources)
        if refs != tuple(sorted(refs)) or len(refs) != len(set(refs)):
            raise ValueError("resources must be unique and canonically sorted by resourceRef")
        for resource in self.resources:
            if not resource.resource_ref.startswith(self.workspace_ref):
                raise ValueError("resourceRef does not belong to workspaceRef")
            bindings = (
                (
                    "workspaceGeneration",
                    resource.workspace_generation,
                    self.workspace_generation,
                ),
                ("workspaceStateRoot", resource.workspace_state_root, self.workspace_state_root),
                ("observedAt", resource.observed_at, self.observed_at),
            )
            for alias, observed, expected in bindings:
                if observed != expected:
                    raise ValueError(f"ResourceObservation {alias} disagrees with workspace view")

        expected_digest = _model_digest(self, exclude=frozenset({"observation_digest"}))
        if self.observation_digest is not None and self.observation_digest != expected_digest:
            raise ValueError("observationDigest does not match WorkspacePreconditionObservation")
        object.__setattr__(self, "observation_digest", expected_digest)
        return self


def validate_workspace_precondition_observation(
    preparation: ExecutionPreparation,
    observation: WorkspacePreconditionObservation,
) -> WorkspacePreconditionObservation:
    """Bind an exact physical precondition observation to a preparation."""

    if type(preparation) is not ExecutionPreparation:
        raise TypeError("preparation must be an exact ExecutionPreparation")
    if type(observation) is not WorkspacePreconditionObservation:
        raise TypeError("observation must be an exact WorkspacePreconditionObservation")
    validated_preparation = ExecutionPreparation.model_validate(preparation)
    validated = WorkspacePreconditionObservation.model_validate(observation)
    intent = validated_preparation.intent
    expected_intent_digest = canonical_action_intent_digest(intent)
    bindings = (
        (
            "actionIntentDigest",
            validated.action_intent_digest,
            expected_intent_digest,
        ),
        (
            "authorityPartitionId",
            validated.authority_partition_id,
            intent.partition_id,
        ),
        (
            "workspaceViewBindingDigest",
            validated.workspace_view_binding_digest,
            intent.workspace_view_binding_digest,
        ),
        (
            "authority.workspaceViewBindingDigest",
            validated.workspace_view_binding_digest,
            validated_preparation.authority_contract.workspace_view_binding_digest,
        ),
    )
    for alias, observed, expected in bindings:
        if observed != expected:
            raise ValueError(f"WorkspacePreconditionObservation {alias} does not match intent")

    expected_resources = {
        **{resource_ref: "present" for resource_ref in intent.read_set},
        **{resource_ref: "absent" for resource_ref in intent.absence_set},
    }
    observed_resources = {
        resource.resource_ref: resource.expected_presence for resource in validated.resources
    }
    if observed_resources != expected_resources:
        raise ValueError(
            "WorkspacePreconditionObservation resources must exactly cover readSet and absenceSet"
        )
    if not (
        validated_preparation.authority_event.created_at
        <= validated.observed_at
        <= validated_preparation.prepared_event.created_at
    ):
        raise ValueError(
            "WorkspacePreconditionObservation must occur between authorization and preparation"
        )
    return validated


def canonical_execution_start_digest(start: ExecutionStart) -> str:
    if type(start) is not ExecutionStart:
        raise TypeError("start must be an exact ExecutionStart")
    return _model_digest(ExecutionStart.model_validate(start))


def canonical_execution_target_digest(start: ExecutionStart) -> str:
    if type(start) is not ExecutionStart:
        raise TypeError("start must be an exact ExecutionStart")
    validated = ExecutionStart.model_validate(start)
    intent = validated.preparation.intent
    payload = {
        "schemaId": "magi.execution_target.v1",
        "actionId": validated.action_id,
        "attemptId": validated.attempt_id,
        "partitionId": validated.partition_id,
        "taskContractDigest": validated.task_contract_digest,
        "actionIntentDigest": validated.action_intent_digest,
        "normalizedInputDigest": validated.request_digest,
        "readSetDigest": intent.read_set_digest,
        "absenceSetDigest": intent.absence_set_digest,
        "writeSetDigest": intent.write_set_digest,
        "egressSetDigest": intent.egress_set_digest,
        "workspaceViewBindingDigest": intent.workspace_view_binding_digest,
    }
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_action_receipt_digest(receipt: ActionReceipt) -> str:
    if type(receipt) is not ActionReceipt:
        raise TypeError("receipt must be an exact ActionReceipt")
    return _model_digest(ActionReceipt.model_validate(receipt))


def canonical_verification_evidence_digest(binding: VerificationEvidenceBinding) -> str:
    if type(binding) is not VerificationEvidenceBinding:
        raise TypeError("binding must be an exact VerificationEvidenceBinding")
    return _model_digest(VerificationEvidenceBinding.model_validate(binding))


def _validate_execution_event_header(
    *,
    event: JournalEvent,
    start: ExecutionStart,
    event_type: str,
    causation_id: str,
    field_name: str,
) -> None:
    intent = start.preparation.intent
    bindings = (
        ("eventType", event.event_type, event_type),
        ("actionId", event.action_id, start.action_id),
        ("attemptId", event.attempt_id, start.attempt_id),
        ("partitionId", event.partition_id, start.partition_id),
        ("taskContractId", event.task_contract_id, intent.task_contract_id),
        ("taskVersion", event.task_version, intent.task_version),
        ("taskContractDigest", event.task_contract_digest, start.task_contract_digest),
        ("completionEpochId", event.completion_epoch_id, intent.completion_epoch_id),
        ("admissionSequence", event.admission_sequence, intent.admission_sequence),
        ("requestDigest", event.request_digest, start.request_digest),
        ("idempotencyKeyDigest", event.idempotency_key_digest, intent.idempotency_key_digest),
        ("authorityContractId", event.authority_contract_id, start.authority_contract_id),
        ("fencingToken", event.fencing_token, start.fencing_token),
        ("actorId", event.actor_id, intent.actor_id),
        ("identityDigest", event.identity_digest, intent.identity_digest),
        ("policyDigest", event.policy_digest, intent.policy_digest),
        ("correlationId", event.correlation_id, intent.run_id),
        ("causationId", event.causation_id, causation_id),
    )
    for alias, observed, expected in bindings:
        if observed != expected:
            raise ValueError(f"{field_name}.{alias} does not match ExecutionStart")


class ExecutionObservationBinding(EnvelopeModel):
    """Atomic provenance and CAS receipt for an execution observation."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    start: ExecutionStart
    receipt: ActionReceipt
    execution_start_digest: str = Field(alias="executionStartDigest")
    execution_target_digest: str = Field(alias="executionTargetDigest")
    backend_observation_digest: str = Field(alias="backendObservationDigest")
    action_receipt_digest: str = Field(alias="actionReceiptDigest")
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion", ge=1, strict=True
    )
    expected_attempt_compare_version: int = Field(
        alias="expectedAttemptCompareVersion", ge=1, strict=True
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion", ge=1, strict=True
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(alias="partitionCompareVersion", ge=1, strict=True)
    observed_event: JournalEvent = Field(alias="observedEvent")
    terminal_event: JournalEvent = Field(alias="terminalEvent")
    binding_digest: str | None = Field(default=None, alias="bindingDigest")

    @model_validator(mode="after")
    def _validate_execution_observation(self) -> Self:
        start = ExecutionStart.model_validate(self.start)
        receipt = ActionReceipt.model_validate(self.receipt)
        observation = receipt.observation
        if receipt.state is ActionState.VERIFIED:
            raise ValueError("execution observation cannot directly produce VERIFIED")
        terminal_event_type = {
            ActionState.COMMITTED: "action.committed",
            ActionState.ABORTED: "action.aborted",
            ActionState.PARTIAL: "action.partial",
            ActionState.UNKNOWN: "action.unknown",
        }.get(receipt.state)
        if terminal_event_type is None:
            raise ValueError("execution observation requires a physical terminal receipt")

        digest_bindings = (
            (
                "executionStartDigest",
                self.execution_start_digest,
                canonical_execution_start_digest(start),
            ),
            (
                "executionTargetDigest",
                self.execution_target_digest,
                canonical_execution_target_digest(start),
            ),
            (
                "backendObservationDigest",
                self.backend_observation_digest,
                canonical_backend_observation_digest(observation),
            ),
            (
                "actionReceiptDigest",
                self.action_receipt_digest,
                canonical_action_receipt_digest(receipt),
            ),
        )
        for alias, observed, expected in digest_bindings:
            if observed != expected:
                raise ValueError(f"{alias} does not match the embedded contract")

        observation_bindings: tuple[tuple[str, object, object], ...] = (
            ("actionId", observation.action_id, start.action_id),
            ("attemptId", observation.attempt_id, start.attempt_id),
            ("partitionId", observation.partition_id, start.partition_id),
            (
                "taskContractDigest",
                observation.task_contract_digest,
                start.task_contract_digest,
            ),
            (
                "actionIntentDigest",
                observation.action_intent_digest,
                start.action_intent_digest,
            ),
            ("requestDigest", observation.request_digest, start.request_digest),
            (
                "authorityDigest",
                observation.authority_digest,
                start.authority_contract_digest,
            ),
            ("fencingToken", observation.fencing_token, start.fencing_token),
            ("executorId", observation.executor_id, start.executor_id),
            ("executorVersion", observation.executor_version, start.executor_version),
            (
                "sandboxProfileDigest",
                observation.sandbox_profile_digest,
                start.sandbox_profile_digest,
            ),
            ("providerId", observation.provider_id, start.provider_id),
            ("providerVersion", observation.provider_version, start.provider_version),
            (
                "providerCapabilitiesDigest",
                observation.provider_capabilities_digest,
                start.provider_capabilities_digest,
            ),
        )
        for observation_alias, observation_value, start_value in observation_bindings:
            if observation_value != start_value:
                raise ValueError(
                    f"BackendObservation {observation_alias} does not match ExecutionStart"
                )

        version_bindings: tuple[tuple[str, int, int], ...] = (
            (
                "expectedActionCompareVersion",
                self.expected_action_compare_version,
                start.action_compare_version,
            ),
            (
                "expectedAttemptCompareVersion",
                self.expected_attempt_compare_version,
                start.attempt_compare_version,
            ),
            (
                "expectedPartitionCompareVersion",
                self.expected_partition_compare_version,
                start.partition_compare_version,
            ),
        )
        for version_alias, requested_version, start_version in version_bindings:
            if requested_version != start_version:
                raise ValueError(f"{version_alias} does not match ExecutionStart result CAS")
        cas_bindings: tuple[tuple[str, int, int], ...] = (
            (
                "actionCompareVersion",
                self.expected_action_compare_version,
                self.action_compare_version,
            ),
            (
                "attemptCompareVersion",
                self.expected_attempt_compare_version,
                self.attempt_compare_version,
            ),
            (
                "partitionCompareVersion",
                self.expected_partition_compare_version,
                self.partition_compare_version,
            ),
        )
        for cas_name, expected_version, result_version in cas_bindings:
            _require_exact_cas_increment(
                name=cas_name,
                expected=expected_version,
                result=result_version,
            )

        _validate_execution_event_header(
            event=self.observed_event,
            start=start,
            event_type="action.observed",
            causation_id=start.executing_event.event_id,
            field_name="observedEvent",
        )
        _validate_execution_event_header(
            event=self.terminal_event,
            start=start,
            event_type=terminal_event_type,
            causation_id=self.observed_event.event_id,
            field_name="terminalEvent",
        )
        expected_observed_payload = {
            "actorId": start.preparation.intent.actor_id,
            "authorityContractDigest": start.authority_contract_digest,
            "authorityContractId": start.authority_contract_id,
            "backendObservationDigest": self.backend_observation_digest,
            "executingEventHash": start.executing_event.event_hash,
            "executingEventId": start.executing_event.event_id,
            "executingEventSequence": start.executing_event.sequence,
            "executionGrantDigest": start.execution_token_digest,
            "executionStartDigest": self.execution_start_digest,
            "executionTargetDigest": self.execution_target_digest,
            "identityDigest": start.preparation.intent.identity_digest,
            "policyDigest": start.preparation.intent.policy_digest,
        }
        if _strict_json_loads(self.observed_event.payload_json) != expected_observed_payload:
            raise ValueError("observedEvent payload does not exactly bind execution provenance")
        expected_terminal_payload = {
            "actionReceiptDigest": self.action_receipt_digest,
            "backendObservationDigest": self.backend_observation_digest,
            "observedEventHash": self.observed_event.event_hash,
            "observedEventId": self.observed_event.event_id,
            "observedEventSequence": self.observed_event.sequence,
            "state": receipt.state.value,
            "stateRootAfter": receipt.state_root_after,
            "stateRootBefore": receipt.state_root_before,
        }
        if _strict_json_loads(self.terminal_event.payload_json) != expected_terminal_payload:
            raise ValueError("terminalEvent payload does not exactly bind ActionReceipt")
        _require_chronological_successor(
            start.executing_event,
            self.observed_event,
            first_name="executingEvent",
            second_name="observedEvent",
        )
        _require_chronological_successor(
            self.observed_event,
            self.terminal_event,
            first_name="observedEvent",
            second_name="terminalEvent",
        )

        expected_digest = _model_digest(self, exclude=frozenset({"binding_digest"}))
        if self.binding_digest is not None and self.binding_digest != expected_digest:
            raise ValueError("bindingDigest does not match ExecutionObservationBinding")
        object.__setattr__(self, "binding_digest", expected_digest)
        return self


class VerificationRecording(EnvelopeModel):
    """Atomic verification evidence, journal lineage, and CAS receipt."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    observation_binding: ExecutionObservationBinding = Field(alias="observationBinding")
    verified_receipt: ActionReceipt = Field(alias="verifiedReceipt")
    verified_receipt_digest: str = Field(alias="verifiedReceiptDigest")
    verification_evidence: VerificationEvidenceBinding = Field(alias="verificationEvidence")
    verification_evidence_digest: str = Field(alias="verificationEvidenceDigest")
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion", ge=1, strict=True
    )
    expected_attempt_compare_version: int = Field(
        alias="expectedAttemptCompareVersion", ge=1, strict=True
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion", ge=1, strict=True
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(alias="partitionCompareVersion", ge=1, strict=True)
    verification_event: JournalEvent = Field(alias="verificationEvent")
    recording_digest: str | None = Field(default=None, alias="recordingDigest")

    @model_validator(mode="after")
    def _validate_verification_recording(self) -> Self:
        observation_binding = ExecutionObservationBinding.model_validate(self.observation_binding)
        verified_receipt = ActionReceipt.model_validate(self.verified_receipt)
        evidence = VerificationEvidenceBinding.model_validate(self.verification_evidence)
        start = observation_binding.start
        terminal = observation_binding.terminal_event
        if verified_receipt.state is not ActionState.VERIFIED:
            raise ValueError("verifiedReceipt must use VERIFIED state")

        if self.verified_receipt_digest != canonical_action_receipt_digest(verified_receipt):
            raise ValueError("verifiedReceiptDigest does not match verifiedReceipt")
        if self.verification_evidence_digest != canonical_verification_evidence_digest(evidence):
            raise ValueError("verificationEvidenceDigest does not match verificationEvidence")
        if canonical_backend_observation_digest(
            verified_receipt.observation
        ) != canonical_backend_observation_digest(observation_binding.receipt.observation):
            raise ValueError("verifiedReceipt substituted the backend observation")
        if (
            verified_receipt.state_root_before != observation_binding.receipt.state_root_before
            or verified_receipt.state_root_after != observation_binding.receipt.state_root_after
        ):
            raise ValueError("verifiedReceipt state roots do not match physical receipt")

        evidence_bindings = (
            ("sourcePartitionId", evidence.source_partition_id, terminal.partition_id),
            ("sourceEventId", evidence.source_event_id, terminal.event_id),
            ("sourceEventSequence", evidence.source_event_sequence, terminal.sequence),
            ("sourceEventHash", evidence.source_event_hash, terminal.event_hash),
            ("actionId", evidence.action_id, start.action_id),
            ("attemptId", evidence.attempt_id, start.attempt_id),
            (
                "taskContractDigest",
                evidence.task_contract_digest,
                start.task_contract_digest,
            ),
            ("requestDigest", evidence.request_digest, start.request_digest),
            (
                "verifiedStateRoot",
                evidence.verified_state_root,
                verified_receipt.state_root_after,
            ),
        )
        for alias, observed, expected in evidence_bindings:
            if observed != expected:
                raise ValueError(f"verificationEvidence.{alias} does not match terminal event")

        expected_versions = (
            (
                "expectedActionCompareVersion",
                self.expected_action_compare_version,
                observation_binding.action_compare_version,
            ),
            (
                "expectedAttemptCompareVersion",
                self.expected_attempt_compare_version,
                observation_binding.attempt_compare_version,
            ),
            (
                "expectedPartitionCompareVersion",
                self.expected_partition_compare_version,
                observation_binding.partition_compare_version,
            ),
        )
        for alias, observed, expected in expected_versions:
            if observed != expected:
                raise ValueError(f"{alias} does not match observation result CAS")
        for name, expected, result in (
            (
                "actionCompareVersion",
                self.expected_action_compare_version,
                self.action_compare_version,
            ),
            (
                "attemptCompareVersion",
                self.expected_attempt_compare_version,
                self.attempt_compare_version,
            ),
            (
                "partitionCompareVersion",
                self.expected_partition_compare_version,
                self.partition_compare_version,
            ),
        ):
            _require_exact_cas_increment(name=name, expected=expected, result=result)

        _validate_execution_event_header(
            event=self.verification_event,
            start=start,
            event_type="action.verified",
            causation_id=terminal.event_id,
            field_name="verificationEvent",
        )
        binding_digest = observation_binding.binding_digest
        assert binding_digest is not None
        expected_payload = {
            "backendObservationDigest": observation_binding.backend_observation_digest,
            "observationBindingDigest": binding_digest,
            "sourceTerminalEventHash": terminal.event_hash,
            "sourceTerminalEventId": terminal.event_id,
            "sourceTerminalEventSequence": terminal.sequence,
            "stateRootAfter": verified_receipt.state_root_after,
            "verificationEvidenceDigest": self.verification_evidence_digest,
            "verifiedReceiptDigest": self.verified_receipt_digest,
        }
        if _strict_json_loads(self.verification_event.payload_json) != expected_payload:
            raise ValueError(
                "verificationEvent payload does not exactly bind terminal verification"
            )
        _require_chronological_successor(
            terminal,
            self.verification_event,
            first_name="terminalEvent",
            second_name="verificationEvent",
        )

        expected_digest = _model_digest(self, exclude=frozenset({"recording_digest"}))
        if self.recording_digest is not None and self.recording_digest != expected_digest:
            raise ValueError("recordingDigest does not match VerificationRecording")
        object.__setattr__(self, "recording_digest", expected_digest)
        return self


__all__ = [
    "ExecutionObservationBinding",
    "ResourceObservation",
    "VerificationRecording",
    "WorkspacePreconditionObservation",
    "canonical_action_receipt_digest",
    "canonical_execution_start_digest",
    "canonical_execution_target_digest",
    "canonical_verification_evidence_digest",
    "validate_workspace_precondition_observation",
]
