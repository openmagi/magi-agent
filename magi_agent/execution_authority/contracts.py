from __future__ import annotations

from collections.abc import Mapping, Set as AbstractSet
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.state_machine import RequirementState
from magi_agent.ops.authority import FrozenContractModel
from magi_agent.ops.safety import canonical_digest, require_digest


class _AuthorityContractModel(FrozenContractModel):
    """Frozen authority contract with every deprecated copy bypass closed."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    def copy(  # type: ignore[override]
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if update is not None:
            raise ValueError(f"copy update is disabled for {type(self).__name__}")
        if include is not None or exclude is not None:
            raise ValueError(f"copy include/exclude is disabled for {type(self).__name__}")
        return self.model_copy(deep=deep)


class ProofObligation(_AuthorityContractModel):
    evidence_kinds: tuple[str, ...] = Field(alias="evidenceKinds", min_length=1)
    freshness: str = Field(min_length=1)
    required_producer: str | None = Field(default=None, alias="requiredProducer")

    @field_validator("evidence_kinds", mode="before")
    @classmethod
    def _reject_unordered_evidence_kinds(cls, value: object) -> object:
        return _require_ordered_collection(value, field_name="evidenceKinds")


class Requirement(_AuthorityContractModel):
    requirement_id: str = Field(alias="requirementId", min_length=1)
    text: str = Field(min_length=1)
    state: RequirementState
    proof: ProofObligation


class DependencyContract(_AuthorityContractModel):
    dependency_id: str = Field(alias="dependencyId", min_length=1)
    required_schema: str = Field(alias="requiredSchema", min_length=1)
    unavailable_behavior: str = Field(alias="unavailableBehavior", min_length=1)


class TaskContractSnapshot(_AuthorityContractModel):
    schema_id: Literal["openmagi.task_contract.v1"] = Field(
        default="openmagi.task_contract.v1",
        alias="schemaId",
    )
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    version: int = Field(ge=1, strict=True)
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    source_message_digests: tuple[str, ...] = Field(
        alias="sourceMessageDigests",
        min_length=1,
    )
    intent: str = Field(min_length=1)
    inclusions: tuple[str, ...]
    exclusions: tuple[str, ...]
    constraints: tuple[str, ...]
    assumptions: tuple[str, ...]
    dependencies: tuple[DependencyContract, ...]
    acceptable_blocked_behavior: str = Field(alias="acceptableBlockedBehavior")
    acceptable_unavailable_behavior: str = Field(alias="acceptableUnavailableBehavior")
    requirements: tuple[Requirement, ...] = Field(min_length=1)
    supersedes_task_contract_id: str | None = Field(
        default=None,
        alias="supersedesTaskContractId",
    )
    supersedes_version: int | None = Field(
        default=None,
        alias="supersedesVersion",
        ge=1,
        strict=True,
    )

    @field_validator(
        "source_message_digests",
        "inclusions",
        "exclusions",
        "constraints",
        "assumptions",
        "dependencies",
        "requirements",
        mode="before",
    )
    @classmethod
    def _reject_unordered_tuple_input(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_ordered_collection(
            value,
            field_name=info.field_name or "tuple field",
        )

    @field_validator("source_message_digests")
    @classmethod
    def _validate_source_message_digests(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(require_digest(digest) for digest in value)

    @model_validator(mode="after")
    def _reject_duplicate_requirement_ids(self) -> Self:
        requirement_ids = tuple(requirement.requirement_id for requirement in self.requirements)
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement IDs must be unique")
        return self


class TaskContractBinding(_AuthorityContractModel):
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    task_contract_snapshot_ref: str = Field(alias="taskContractSnapshotRef")

    @field_validator("task_contract_digest")
    @classmethod
    def _validate_task_contract_digest(cls, value: str) -> str:
        return require_digest(value)

    @model_validator(mode="after")
    def _validate_snapshot_ref(self) -> Self:
        expected = f"authority-task://{self.task_contract_digest}"
        if self.task_contract_snapshot_ref != expected:
            raise ValueError(
                "taskContractSnapshotRef must equal authority-task://<taskContractDigest>"
            )
        return self


def _require_ordered_collection(value: object, *, field_name: str) -> object:
    if isinstance(value, AbstractSet):
        raise ValueError(f"{field_name} must use an ordered collection")
    return value


def _validate_snapshot_instance(snapshot: object) -> TaskContractSnapshot:
    if type(snapshot) is not TaskContractSnapshot:
        raise TypeError("task contract snapshot must be an exact TaskContractSnapshot")
    return TaskContractSnapshot.model_validate(snapshot)


def _validate_binding_instance(binding: object) -> TaskContractBinding:
    if type(binding) is not TaskContractBinding:
        raise TypeError("task contract binding must be an exact TaskContractBinding")
    return TaskContractBinding.model_validate(binding)


def _validated_snapshot_digest(snapshot: TaskContractSnapshot) -> str:
    payload = TaskContractSnapshot.model_dump(snapshot, by_alias=True, mode="json")
    return canonical_digest(payload)


def canonical_task_contract_digest(snapshot: TaskContractSnapshot) -> str:
    return _validated_snapshot_digest(_validate_snapshot_instance(snapshot))


def bind_task_contract(snapshot: TaskContractSnapshot) -> TaskContractBinding:
    validated_snapshot = _validate_snapshot_instance(snapshot)
    digest = _validated_snapshot_digest(validated_snapshot)
    return TaskContractBinding.model_validate(
        {
            "task_contract_id": validated_snapshot.task_contract_id,
            "task_version": validated_snapshot.version,
            "task_contract_digest": digest,
            "task_contract_snapshot_ref": f"authority-task://{digest}",
        }
    )


def validate_task_contract_binding(
    snapshot: TaskContractSnapshot,
    binding: TaskContractBinding,
) -> TaskContractBinding:
    validated_snapshot = _validate_snapshot_instance(snapshot)
    validated_binding = _validate_binding_instance(binding)
    digest = _validated_snapshot_digest(validated_snapshot)
    expected_snapshot_ref = f"authority-task://{digest}"

    if validated_binding.task_contract_id != validated_snapshot.task_contract_id:
        raise ValueError("taskContractId does not match the task contract snapshot")
    if validated_binding.task_version != validated_snapshot.version:
        raise ValueError("taskVersion does not match the task contract snapshot")
    if validated_binding.task_contract_digest != digest:
        raise ValueError("taskContractDigest does not match the task contract snapshot")
    if validated_binding.task_contract_snapshot_ref != expected_snapshot_ref:
        raise ValueError("taskContractSnapshotRef does not match the task contract digest")
    return validated_binding
