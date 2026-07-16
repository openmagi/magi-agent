"""Trusted projection registry and finalization-evaluation wire contracts.

The contracts in this module are dormant.  They define the immutable material a
durable store must capture before a completion evaluator can run, but they do
not select a registry, read a projection, or activate any runtime behavior.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.envelopes import (
    EnvelopeModel,
    EpochSeal,
    EpochSnapshot,
    FinalizationRequest,
    JournalChainLink,
    validate_finalization_request_epoch,
)
from magi_agent.execution_authority.state_machine import CompletionEpochState


MAX_PROJECTION_ANCESTRY_LINKS = 64


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _domain_digest(domain: str, value: object) -> str:
    payload = {
        "domain": domain,
        "schemaVersion": 1,
        "value": value,
    }
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _require_ordered_sequence(value: object, *, field_name: str) -> object:
    if type(value) not in (list, tuple):
        raise ValueError(f"{field_name} must use an ordered list or tuple")
    return value


def _require_clean_identifier(value: str, *, field_name: str) -> str:
    if not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a nonblank canonical identifier")
    return value


class ProjectionRegistryEntry(EnvelopeModel):
    """One reducer identity and the exact partitions it governs."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    projection_id: str = Field(alias="projectionId", min_length=1)
    reducer_executable_digest: str = Field(alias="reducerExecutableDigest")
    projection_schema_version: int = Field(
        alias="projectionSchemaVersion",
        ge=1,
        strict=True,
    )
    required: bool = Field(strict=True)
    partition_scope: tuple[str, ...] = Field(
        alias="partitionScope",
        min_length=1,
    )

    @field_validator("partition_scope", mode="before")
    @classmethod
    def _require_ordered_partition_scope(cls, value: object) -> object:
        return _require_ordered_sequence(value, field_name="partitionScope")

    @field_validator("projection_id")
    @classmethod
    def _validate_projection_id(cls, value: str) -> str:
        return _require_clean_identifier(value, field_name="projectionId")

    @field_validator("partition_scope")
    @classmethod
    def _validate_partition_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for partition_id in value:
            _require_clean_identifier(partition_id, field_name="partitionScope")
        if value != tuple(sorted(value)) or len(value) != len(set(value)):
            raise ValueError("partitionScope must be unique and sorted")
        return value


class ProjectionRegistrySnapshot(EnvelopeModel):
    """Versioned first-party registry captured as authoritative seal input."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    registry_id: str = Field(alias="registryId", min_length=1)
    registry_version: int = Field(alias="registryVersion", ge=1, strict=True)
    policy_digest: str = Field(alias="policyDigest")
    entries: tuple[ProjectionRegistryEntry, ...] = Field(min_length=1)
    registry_digest: str | None = Field(default=None, alias="registryDigest")

    @field_validator("entries", mode="before")
    @classmethod
    def _require_ordered_entries(cls, value: object) -> object:
        return _require_ordered_sequence(value, field_name="entries")

    @field_validator("registry_id")
    @classmethod
    def _validate_registry_id(cls, value: str) -> str:
        return _require_clean_identifier(value, field_name="registryId")

    @model_validator(mode="after")
    def _bind_registry_identity(self) -> Self:
        entry_keys = tuple((entry.projection_id, entry.partition_scope) for entry in self.entries)
        if entry_keys != tuple(sorted(entry_keys)) or len(entry_keys) != len(set(entry_keys)):
            raise ValueError("registry entries must be unique and sorted")

        governed_keys = tuple(
            (partition_id, entry.projection_id)
            for entry in self.entries
            for partition_id in entry.partition_scope
        )
        if len(governed_keys) != len(set(governed_keys)):
            raise ValueError("registry entries contain overlapping projection partition scope")

        expected = _domain_digest(
            "magi.projection_registry_snapshot.v1",
            self.model_dump(
                by_alias=True,
                mode="json",
                exclude={"registry_digest"},
            ),
        )
        if self.registry_digest is not None and self.registry_digest != expected:
            raise ValueError("registryDigest does not match ProjectionRegistrySnapshot")
        object.__setattr__(self, "registry_digest", expected)
        return self


def canonical_projection_registry_snapshot_digest(
    registry: ProjectionRegistrySnapshot,
) -> str:
    if type(registry) is not ProjectionRegistrySnapshot:
        raise TypeError("registry must be an exact ProjectionRegistrySnapshot")
    validated = ProjectionRegistrySnapshot.model_validate(registry)
    assert validated.registry_digest is not None
    return validated.registry_digest


class RequiredProjectionCheckpoint(EnvelopeModel):
    """The exact journal head and reducer identity frozen by an epoch seal."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    projection_id: str = Field(alias="projectionId", min_length=1)
    required_sequence: int = Field(alias="requiredSequence", ge=1, strict=True)
    required_event_hash: str = Field(alias="requiredEventHash")
    required_state_root: str = Field(alias="requiredStateRoot")
    reducer_executable_digest: str = Field(alias="reducerExecutableDigest")
    projection_schema_version: int = Field(
        alias="projectionSchemaVersion",
        ge=1,
        strict=True,
    )

    @field_validator("partition_id", "projection_id")
    @classmethod
    def _validate_checkpoint_identifiers(
        cls,
        value: str,
        info: ValidationInfo,
    ) -> str:
        return _require_clean_identifier(
            value,
            field_name=info.field_name or "checkpoint identifier",
        )


def canonical_required_projection_checkpoints_digest(
    checkpoints: tuple[RequiredProjectionCheckpoint, ...],
) -> str:
    if type(checkpoints) is not tuple:
        raise TypeError("checkpoints must be an exact tuple")
    if not checkpoints:
        raise ValueError("checkpoints must not be empty")
    validated: list[RequiredProjectionCheckpoint] = []
    for checkpoint in checkpoints:
        if type(checkpoint) is not RequiredProjectionCheckpoint:
            raise TypeError("checkpoints must contain exact RequiredProjectionCheckpoint values")
        validated.append(RequiredProjectionCheckpoint.model_validate(checkpoint))
    keys = tuple((item.partition_id, item.projection_id) for item in validated)
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        raise ValueError("checkpoints must be unique and sorted by key")
    return _domain_digest(
        "magi.required_projection_checkpoint_vector.v1",
        [item.model_dump(by_alias=True, mode="json") for item in validated],
    )


class EpochSealBinding(EnvelopeModel):
    """Seal plus the authoritative registry and its exact captured checkpoints."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    registry: ProjectionRegistrySnapshot
    seal: EpochSeal
    post_seal_epoch: EpochSnapshot = Field(alias="postSealEpoch")
    required_checkpoints: tuple[RequiredProjectionCheckpoint, ...] = Field(
        alias="requiredCheckpoints",
        min_length=1,
    )
    checkpoint_vector_digest: str | None = Field(
        default=None,
        alias="checkpointVectorDigest",
    )
    required_projection_domain_digest: str | None = Field(
        default=None,
        alias="requiredProjectionDomainDigest",
    )
    seal_binding_digest: str | None = Field(default=None, alias="sealBindingDigest")

    @field_validator("required_checkpoints", mode="before")
    @classmethod
    def _require_ordered_checkpoints(cls, value: object) -> object:
        return _require_ordered_sequence(value, field_name="requiredCheckpoints")

    @model_validator(mode="after")
    def _bind_authoritative_projection_domain(self) -> Self:
        epoch = self.post_seal_epoch
        seal = self.seal
        if epoch.state is not CompletionEpochState.SEALING:
            raise ValueError("post-seal epoch must be in SEALING state")
        if epoch.compare_version != seal.epoch_compare_version:
            raise ValueError(
                "post-seal epoch compareVersion must equal EpochSeal epochCompareVersion"
            )

        epoch_binding = epoch.task_contract_binding
        seal_epoch_checks = (
            ("completionEpochId", epoch.completion_epoch_id, seal.completion_epoch_id),
            ("taskPartitionId", epoch.task_partition_id, seal.task_partition_id),
            (
                "taskContractDigest",
                epoch_binding.task_contract_digest,
                seal.task_contract_digest,
            ),
            (
                "taskContractSnapshotRef",
                epoch_binding.task_contract_snapshot_ref,
                seal.task_contract_snapshot_ref,
            ),
            (
                "barrierAdmissionSequence",
                epoch.last_admission_sequence,
                seal.barrier_admission_sequence,
            ),
        )
        for alias, observed, expected in seal_epoch_checks:
            if observed != expected:
                raise ValueError(f"post-seal epoch {alias} does not match EpochSeal")

        registry_index = {
            (partition_id, entry.projection_id): entry
            for entry in self.registry.entries
            if entry.required
            for partition_id in entry.partition_scope
        }
        expected_keys = tuple(sorted(registry_index))
        if not expected_keys:
            raise ValueError("authoritative registry has no required projection")

        checkpoint_keys = tuple(
            (item.partition_id, item.projection_id) for item in self.required_checkpoints
        )
        if len(checkpoint_keys) != len(set(checkpoint_keys)):
            raise ValueError("requiredCheckpoints must be unique")
        if len(checkpoint_keys) != len(expected_keys) or set(checkpoint_keys) != set(expected_keys):
            raise ValueError(
                "requiredCheckpoints must exactly cover authoritative required projection keys"
            )
        if checkpoint_keys != expected_keys:
            raise ValueError("requiredCheckpoints must be sorted by key")

        seal_keys = tuple(
            (item.partition_id, item.projection_id) for item in seal.required_projections
        )
        if seal_keys != expected_keys:
            raise ValueError(
                "EpochSeal required projections must exactly cover authoritative registry keys"
            )

        state_roots = {item.required_state_root for item in self.required_checkpoints}
        if len(state_roots) != 1:
            raise ValueError(
                "required checkpoint state roots must bind one finalization state root"
            )
        for checkpoint in self.required_checkpoints:
            entry = registry_index[(checkpoint.partition_id, checkpoint.projection_id)]
            if checkpoint.reducer_executable_digest != entry.reducer_executable_digest:
                raise ValueError("checkpoint reducer identity does not match registry")
            if checkpoint.projection_schema_version != entry.projection_schema_version:
                raise ValueError("checkpoint projection schema does not match registry")

        checkpoint_digest = canonical_required_projection_checkpoints_digest(
            self.required_checkpoints
        )
        if (
            self.checkpoint_vector_digest is not None
            and self.checkpoint_vector_digest != checkpoint_digest
        ):
            raise ValueError("checkpointVectorDigest does not match requiredCheckpoints")
        object.__setattr__(self, "checkpoint_vector_digest", checkpoint_digest)

        registry_digest = canonical_projection_registry_snapshot_digest(self.registry)
        domain_digest = _domain_digest(
            "magi.sealed_required_projection_domain.v1",
            {
                "barrierAdmissionSequence": seal.barrier_admission_sequence,
                "checkpointVectorDigest": checkpoint_digest,
                "completionEpochId": seal.completion_epoch_id,
                "registryDigest": registry_digest,
                "taskContractDigest": seal.task_contract_digest,
                "taskPartitionId": seal.task_partition_id,
            },
        )
        if (
            self.required_projection_domain_digest is not None
            and self.required_projection_domain_digest != domain_digest
        ):
            raise ValueError(
                "requiredProjectionDomainDigest does not match the sealed projection domain"
            )
        object.__setattr__(self, "required_projection_domain_digest", domain_digest)

        binding_digest = _domain_digest(
            "magi.epoch_seal_binding.v1",
            self.model_dump(
                by_alias=True,
                mode="json",
                exclude={"seal_binding_digest"},
            ),
        )
        if self.seal_binding_digest is not None and self.seal_binding_digest != binding_digest:
            raise ValueError("sealBindingDigest does not match EpochSealBinding")
        object.__setattr__(self, "seal_binding_digest", binding_digest)
        return self


def canonical_epoch_seal_binding_digest(binding: EpochSealBinding) -> str:
    if type(binding) is not EpochSealBinding:
        raise TypeError("binding must be an exact EpochSealBinding")
    validated = EpochSealBinding.model_validate(binding)
    assert validated.seal_binding_digest is not None
    return validated.seal_binding_digest


class ProjectionCursorProof(EnvelopeModel):
    """One checkpoint acknowledgement with a bounded successor-only proof."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    checkpoint: RequiredProjectionCheckpoint
    acknowledged_sequence: int = Field(
        alias="acknowledgedSequence",
        ge=1,
        strict=True,
    )
    acknowledged_event_hash: str = Field(alias="acknowledgedEventHash")
    acknowledged_state_root: str = Field(alias="acknowledgedStateRoot")
    reducer_executable_digest: str = Field(alias="reducerExecutableDigest")
    projection_schema_version: int = Field(
        alias="projectionSchemaVersion",
        ge=1,
        strict=True,
    )
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)
    successor_ancestry: tuple[JournalChainLink, ...] = Field(
        default=(),
        alias="successorAncestry",
        max_length=MAX_PROJECTION_ANCESTRY_LINKS,
    )

    @field_validator("successor_ancestry", mode="before")
    @classmethod
    def _require_ordered_successors(cls, value: object) -> object:
        return _require_ordered_sequence(value, field_name="successorAncestry")

    @model_validator(mode="after")
    def _validate_checkpoint_acknowledgement(self) -> Self:
        checkpoint = self.checkpoint
        if self.reducer_executable_digest != checkpoint.reducer_executable_digest:
            raise ValueError("cursor reducer identity does not match sealed checkpoint")
        if self.projection_schema_version != checkpoint.projection_schema_version:
            raise ValueError("cursor projection schema does not match sealed checkpoint")
        if self.acknowledged_state_root != checkpoint.required_state_root:
            raise ValueError("cursor state root does not match sealed checkpoint")
        if self.acknowledged_sequence < checkpoint.required_sequence:
            raise ValueError("acknowledgedSequence cannot precede requiredSequence")

        distance = self.acknowledged_sequence - checkpoint.required_sequence
        if distance > MAX_PROJECTION_ANCESTRY_LINKS:
            raise ValueError("projection ancestry exceeds the bounded proof length")
        if distance == 0:
            if self.acknowledged_event_hash != checkpoint.required_event_hash:
                raise ValueError("equal cursor sequence must preserve the checkpoint event hash")
            if self.successor_ancestry:
                raise ValueError("equal cursor sequence cannot carry successor ancestry")
            return self

        if len(self.successor_ancestry) != distance:
            raise ValueError(
                "successor-only ancestry must contain exactly one link per later sequence"
            )
        first = self.successor_ancestry[0]
        if first.sequence != checkpoint.required_sequence + 1:
            raise ValueError("successor-only ancestry must begin after requiredSequence")
        if first.previous_hash != checkpoint.required_event_hash:
            raise ValueError("first successor previousHash must equal requiredEventHash")

        previous_sequence = checkpoint.required_sequence
        previous_hash = checkpoint.required_event_hash
        for link in self.successor_ancestry:
            if link.sequence != previous_sequence + 1:
                raise ValueError("successor ancestry sequences must be contiguous")
            if link.previous_hash != previous_hash:
                raise ValueError("successor ancestry previousHash must preserve the hash chain")
            previous_sequence = link.sequence
            previous_hash = link.event_hash
        if previous_sequence != self.acknowledged_sequence:
            raise ValueError("successor ancestry does not end at acknowledgedSequence")
        if previous_hash != self.acknowledged_event_hash:
            raise ValueError("successor ancestry does not end at acknowledgedEventHash")
        return self


def canonical_projection_cursor_vector_digest(
    cursors: tuple[ProjectionCursorProof, ...],
) -> str:
    if type(cursors) is not tuple:
        raise TypeError("cursors must be an exact tuple")
    if not cursors:
        raise ValueError("cursors must not be empty")
    validated: list[ProjectionCursorProof] = []
    for cursor in cursors:
        if type(cursor) is not ProjectionCursorProof:
            raise TypeError("cursors must contain exact ProjectionCursorProof values")
        validated.append(ProjectionCursorProof.model_validate(cursor))
    keys = tuple(
        (item.checkpoint.partition_id, item.checkpoint.projection_id) for item in validated
    )
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        raise ValueError("projection cursors must be unique and sorted by checkpoint key")
    return _domain_digest(
        "magi.projection_cursor_vector.v1",
        [item.model_dump(by_alias=True, mode="json") for item in validated],
    )


class FinalizationEvaluationBinding(EnvelopeModel):
    """Replay-complete evaluator input committed by ``evaluationDigest``."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    seal_binding: EpochSealBinding = Field(alias="sealBinding")
    request: FinalizationRequest
    projection_cursors: tuple[ProjectionCursorProof, ...] = Field(
        alias="projectionCursors",
        min_length=1,
    )
    cursor_vector_digest: str | None = Field(default=None, alias="cursorVectorDigest")
    evaluation_digest: str | None = Field(default=None, alias="evaluationDigest")

    @field_validator("projection_cursors", mode="before")
    @classmethod
    def _require_ordered_cursor_vector(cls, value: object) -> object:
        return _require_ordered_sequence(value, field_name="projectionCursors")

    @model_validator(mode="after")
    def _bind_exact_evaluation_input(self) -> Self:
        seal_binding = self.seal_binding
        seal = seal_binding.seal
        validate_finalization_request_epoch(seal_binding.post_seal_epoch, self.request)

        seal_request_checks = (
            (
                "completionEpochId",
                self.request.completion_epoch_id,
                seal.completion_epoch_id,
            ),
            ("taskPartitionId", self.request.task_partition_id, seal.task_partition_id),
            (
                "taskContractDigest",
                self.request.task_contract_digest,
                seal.task_contract_digest,
            ),
            (
                "taskContractSnapshotRef",
                self.request.task_contract_snapshot_ref,
                seal.task_contract_snapshot_ref,
            ),
            (
                "barrierAdmissionSequence",
                self.request.barrier_admission_sequence,
                seal.barrier_admission_sequence,
            ),
        )
        for alias, observed, expected in seal_request_checks:
            if observed != expected:
                raise ValueError(f"FinalizationRequest {alias} does not match EpochSealBinding")

        checkpoints = seal_binding.required_checkpoints
        if any(
            checkpoint.required_state_root != self.request.state_root for checkpoint in checkpoints
        ):
            raise ValueError("finalization state root does not match sealed checkpoints")

        cursor_keys = tuple(
            (cursor.checkpoint.partition_id, cursor.checkpoint.projection_id)
            for cursor in self.projection_cursors
        )
        checkpoint_keys = tuple(
            (checkpoint.partition_id, checkpoint.projection_id) for checkpoint in checkpoints
        )
        if cursor_keys != checkpoint_keys:
            raise ValueError("projectionCursors must exactly cover sealed checkpoint keys")
        for cursor, checkpoint in zip(
            self.projection_cursors,
            checkpoints,
            strict=True,
        ):
            if cursor.checkpoint != checkpoint:
                raise ValueError("projection cursor does not match the exact sealed checkpoint")

        cursor_digest = canonical_projection_cursor_vector_digest(self.projection_cursors)
        if self.cursor_vector_digest is not None and self.cursor_vector_digest != cursor_digest:
            raise ValueError("cursorVectorDigest does not match projectionCursors")
        object.__setattr__(self, "cursor_vector_digest", cursor_digest)

        seal_binding_digest = canonical_epoch_seal_binding_digest(seal_binding)
        evaluation_digest = _domain_digest(
            "magi.finalization_evaluation_binding.v1",
            {
                "cursorVectorDigest": cursor_digest,
                "finalizationRequestDigest": self.request.finalization_request_digest,
                "sealBindingDigest": seal_binding_digest,
            },
        )
        if self.evaluation_digest is not None and self.evaluation_digest != evaluation_digest:
            raise ValueError("evaluationDigest does not match finalization evaluation input")
        object.__setattr__(self, "evaluation_digest", evaluation_digest)
        return self


def canonical_finalization_evaluation_binding_digest(
    evaluation: FinalizationEvaluationBinding,
) -> str:
    if type(evaluation) is not FinalizationEvaluationBinding:
        raise TypeError("evaluation must be an exact FinalizationEvaluationBinding")
    validated = FinalizationEvaluationBinding.model_validate(evaluation)
    assert validated.evaluation_digest is not None
    return validated.evaluation_digest


__all__ = [
    "MAX_PROJECTION_ANCESTRY_LINKS",
    "EpochSealBinding",
    "FinalizationEvaluationBinding",
    "ProjectionCursorProof",
    "ProjectionRegistryEntry",
    "ProjectionRegistrySnapshot",
    "RequiredProjectionCheckpoint",
    "canonical_epoch_seal_binding_digest",
    "canonical_finalization_evaluation_binding_digest",
    "canonical_projection_cursor_vector_digest",
    "canonical_projection_registry_snapshot_digest",
    "canonical_required_projection_checkpoints_digest",
]
