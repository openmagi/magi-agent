from __future__ import annotations

from collections.abc import Mapping, Set as AbstractSet
from datetime import datetime, timedelta
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


class AuthorityCapability(_AuthorityContractModel):
    effect_class: str = Field(alias="effectClass", min_length=1)
    resource_ref: str = Field(alias="resourceRef", min_length=1)
    network_refs: tuple[str, ...] = Field(alias="networkRefs")
    credential_refs: tuple[str, ...] = Field(alias="credentialRefs")

    @field_validator("effect_class", "resource_ref", mode="before")
    @classmethod
    def _require_exact_identity_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string(value, field_name=info.field_name or "capability")

    @field_validator("network_refs", "credential_refs", mode="before")
    @classmethod
    def _validate_ref_sequence(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string_sequence(
            value,
            field_name=info.field_name or "capability refs",
        )


class AuthorityInput(_AuthorityContractModel):
    source: Literal[
        "platform",
        "user",
        "session",
        "turn",
        "tool",
        "resource",
        "sandbox",
        "guardian",
    ]
    decision: Literal["allow", "deny", "review_required", "user_decision_required"]
    capabilities: tuple[AuthorityCapability, ...] = Field(min_length=1)

    @field_validator("source", "decision", mode="before")
    @classmethod
    def _require_exact_status_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string(value, field_name=info.field_name or "authority status")

    @field_validator("capabilities", mode="before")
    @classmethod
    def _reject_unordered_capabilities(cls, value: object) -> object:
        return _require_ordered_collection(value, field_name="capabilities")

    @model_validator(mode="after")
    def _reject_duplicate_capabilities(self) -> Self:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate capabilities are not allowed")
        return self


class AuthorityDecision(_AuthorityContractModel):
    status: Literal["allow", "deny", "review_required", "user_decision_required"]
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    capabilities: tuple[AuthorityCapability, ...] = ()

    @field_validator("status", mode="before")
    @classmethod
    def _require_exact_status_string(cls, value: object) -> object:
        return _require_exact_string(value, field_name="status")

    @field_validator("reason_codes", "capabilities", mode="before")
    @classmethod
    def _validate_tuple_input(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        field_name = info.field_name or "tuple field"
        if info.field_name == "reason_codes":
            return _require_exact_string_sequence(value, field_name=field_name)
        return _require_ordered_collection(value, field_name=field_name)

    @model_validator(mode="after")
    def _validate_decision_invariants(self) -> Self:
        if self.status != "deny" and not self.capabilities:
            raise ValueError("non-deny authority decisions require capabilities")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate capabilities are not allowed")
        return self


class AuthorityContract(_AuthorityContractModel):
    authority_contract_id: str = Field(alias="authorityContractId", min_length=1)
    issuer_id: str = Field(alias="issuerId", min_length=1)
    principal_id: str = Field(alias="principalId", min_length=1)
    tenant_id: str = Field(alias="tenantId", min_length=1)
    session_id: str = Field(alias="sessionId", min_length=1)
    turn_id: str = Field(alias="turnId", min_length=1)
    child_actor_id: str | None = Field(default=None, alias="childActorId", min_length=1)
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    policy_digest: str = Field(alias="policyDigest")
    normalized_request_digest: str = Field(alias="normalizedRequestDigest")
    command_digest: str | None = Field(default=None, alias="commandDigest")
    arguments_digest: str = Field(alias="argumentsDigest")
    working_directory_digest: str = Field(alias="workingDirectoryDigest")
    environment_digest: str = Field(alias="environmentDigest")
    request_body_digest: str | None = Field(default=None, alias="requestBodyDigest")
    credential_scope_digest: str | None = Field(default=None, alias="credentialScopeDigest")
    network_digest: str | None = Field(default=None, alias="networkDigest")
    disclosure_digest: str = Field(alias="disclosureDigest")
    capabilities: tuple[AuthorityCapability, ...] = Field(min_length=1)
    sandbox_profile_digest: str = Field(alias="sandboxProfileDigest")
    guardian_ceiling_digest: str = Field(alias="guardianCeilingDigest")
    expires_at: datetime = Field(alias="expiresAt")
    revoked_at: datetime | None = Field(default=None, alias="revokedAt")
    revocation_digest: str | None = Field(default=None, alias="revocationDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    maximum_uses: Literal[1] = Field(default=1, alias="maximumUses")
    decision_request_id: str | None = Field(
        default=None,
        alias="decisionRequestId",
        min_length=1,
    )
    resume_binding_digest: str | None = Field(default=None, alias="resumeBindingDigest")
    parent_authority_digest: str | None = Field(default=None, alias="parentAuthorityDigest")
    delegation_chain: tuple[str, ...] = Field(alias="delegationChain")
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")

    @field_validator(
        "authority_contract_id",
        "issuer_id",
        "principal_id",
        "tenant_id",
        "session_id",
        "turn_id",
        "child_actor_id",
        "task_contract_id",
        "task_contract_digest",
        "completion_epoch_id",
        "authority_partition_id",
        "action_id",
        "attempt_id",
        "policy_digest",
        "normalized_request_digest",
        "command_digest",
        "arguments_digest",
        "working_directory_digest",
        "environment_digest",
        "request_body_digest",
        "credential_scope_digest",
        "network_digest",
        "disclosure_digest",
        "sandbox_profile_digest",
        "guardian_ceiling_digest",
        "revocation_digest",
        "decision_request_id",
        "resume_binding_digest",
        "parent_authority_digest",
        mode="before",
    )
    @classmethod
    def _require_exact_string_boundary(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if value is None:
            return None
        return _require_exact_string(value, field_name=info.field_name or "contract field")

    @field_validator("maximum_uses", "schema_version", mode="before")
    @classmethod
    def _require_exact_literal_one(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if type(value) is not int or value != 1:
            field_name = info.field_name or "literal field"
            raise ValueError(f"{field_name} must be the exact integer 1")
        return value

    @field_validator("expires_at", "revoked_at", mode="before")
    @classmethod
    def _require_datetime_wire_type(
        cls,
        value: object,
    ) -> object:
        if value is None or type(value) in (datetime, str):
            return value
        raise ValueError(
            "authority contract datetimes must be exact datetime instances or ISO strings"
        )

    @field_validator("capabilities", "delegation_chain", mode="before")
    @classmethod
    def _validate_tuple_input(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        field_name = info.field_name or "tuple field"
        if info.field_name == "delegation_chain":
            return _require_exact_string_sequence(value, field_name=field_name)
        return _require_ordered_collection(value, field_name=field_name)

    @field_validator(
        "task_contract_digest",
        "policy_digest",
        "normalized_request_digest",
        "command_digest",
        "arguments_digest",
        "working_directory_digest",
        "environment_digest",
        "request_body_digest",
        "credential_scope_digest",
        "network_digest",
        "disclosure_digest",
        "sandbox_profile_digest",
        "guardian_ceiling_digest",
        "revocation_digest",
        "resume_binding_digest",
        "parent_authority_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("delegation_chain")
    @classmethod
    def _validate_delegation_chain(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(require_digest(digest) for digest in value)

    @field_validator("expires_at", "revoked_at")
    @classmethod
    def _require_utc_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if type(value) is not datetime:
            raise ValueError("authority contract datetimes must be exact datetime instances")
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("authority contract datetimes must use UTC")
        return value

    @model_validator(mode="after")
    def _validate_envelope_invariants(self) -> Self:
        if (self.revoked_at is None) != (self.revocation_digest is None):
            if self.revoked_at is None:
                raise ValueError("revokedAt is required with revocationDigest")
            raise ValueError("revocationDigest is required with revokedAt")
        if (self.decision_request_id is None) != (self.resume_binding_digest is None):
            if self.decision_request_id is None:
                raise ValueError("decisionRequestId is required with resumeBindingDigest")
            raise ValueError("resumeBindingDigest is required with decisionRequestId")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate capabilities are not allowed")
        if self.parent_authority_digest is None:
            if self.delegation_chain:
                raise ValueError(
                    "root authority contracts may not contain a delegationChain"
                )
        elif not self.delegation_chain:
            raise ValueError(
                "delegated authority contracts require a nonempty delegationChain"
            )
        elif self.delegation_chain[-1] != self.parent_authority_digest:
            raise ValueError(
                "delegationChain must end with parentAuthorityDigest"
            )
        return self


def _require_ordered_collection(value: object, *, field_name: str) -> object:
    if isinstance(value, AbstractSet):
        raise ValueError(f"{field_name} must use an ordered collection")
    return value


def _require_exact_string(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be an exact string")
    return value


def _require_exact_string_sequence(value: object, *, field_name: str) -> object:
    value = _require_ordered_collection(value, field_name=field_name)
    if type(value) not in (list, tuple):
        raise ValueError(f"{field_name} must use an ordered list or tuple")
    assert isinstance(value, (list, tuple))
    for item in value:
        _require_exact_string(item, field_name=f"{field_name} elements")
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


def _validate_authority_input_instance(authority_input: object) -> AuthorityInput:
    if type(authority_input) is not AuthorityInput:
        raise TypeError("authority resolver inputs must be exact AuthorityInput instances")
    return AuthorityInput.model_validate(authority_input)


def _validate_authority_contract_instance(contract: object) -> AuthorityContract:
    if type(contract) is not AuthorityContract:
        raise TypeError("authority contract must be an exact AuthorityContract")
    return AuthorityContract.model_validate(contract)


def _authority_capability_json(capability: AuthorityCapability) -> str:
    return AuthorityCapability.model_dump_json(capability, by_alias=True)


def _validated_authority_contract_digest(contract: AuthorityContract) -> str:
    payload = AuthorityContract.model_dump(contract, by_alias=True, mode="json")
    return canonical_digest(payload)


def canonical_authority_contract_digest(contract: AuthorityContract) -> str:
    return _validated_authority_contract_digest(_validate_authority_contract_instance(contract))


def resolve_authority(
    *,
    inputs: tuple[AuthorityInput, ...],
    action_digest: str,
) -> AuthorityDecision:
    if type(action_digest) is not str:
        raise TypeError("action digest must be an exact string")
    validated_action_digest = require_digest(action_digest)
    if type(inputs) is not tuple:
        raise TypeError("authority resolver inputs must be an exact tuple")
    validated_inputs = tuple(_validate_authority_input_instance(value) for value in inputs)

    allowing_inputs = tuple(value for value in validated_inputs if value.decision != "deny")
    if not allowing_inputs:
        return AuthorityDecision.model_validate(
            {
                "status": "deny",
                "reason_codes": ("no_allowing_authority",),
                "capabilities": (),
            }
        )

    capabilities_by_key = {
        _authority_capability_json(capability): capability
        for capability in allowing_inputs[0].capabilities
    }
    effective_keys = set(capabilities_by_key)
    for authority_input in allowing_inputs[1:]:
        effective_keys.intersection_update(
            _authority_capability_json(capability) for capability in authority_input.capabilities
        )

    denied_keys = {
        _authority_capability_json(capability)
        for authority_input in validated_inputs
        if authority_input.decision == "deny"
        for capability in authority_input.capabilities
    }
    effective_keys.difference_update(denied_keys)
    if not effective_keys:
        return AuthorityDecision.model_validate(
            {
                "status": "deny",
                "reason_codes": ("deny_wins",),
                "capabilities": (),
            }
        )

    if any(value.decision == "user_decision_required" for value in allowing_inputs):
        status = "user_decision_required"
    elif any(value.decision == "review_required" for value in allowing_inputs):
        status = "review_required"
    else:
        status = "allow"

    capabilities = tuple(capabilities_by_key[key] for key in sorted(effective_keys))
    return AuthorityDecision.model_validate(
        {
            "status": status,
            "reason_codes": ("authority_intersection", validated_action_digest),
            "capabilities": capabilities,
        }
    )


def validate_delegated_authority(
    parent: AuthorityContract,
    child: AuthorityContract,
) -> AuthorityContract:
    validated_parent = _validate_authority_contract_instance(parent)
    validated_child = _validate_authority_contract_instance(child)
    parent_digest = _validated_authority_contract_digest(validated_parent)

    if validated_child.parent_authority_digest != parent_digest:
        raise ValueError("parentAuthorityDigest must equal the canonical parent digest")
    expected_chain = (*validated_parent.delegation_chain, parent_digest)
    if validated_child.delegation_chain != expected_chain:
        raise ValueError("delegationChain must append the canonical parent digest")

    parent_capabilities = {
        _authority_capability_json(capability) for capability in validated_parent.capabilities
    }
    child_capabilities = {
        _authority_capability_json(capability) for capability in validated_child.capabilities
    }
    if not child_capabilities or not child_capabilities.issubset(parent_capabilities):
        raise ValueError("delegated capabilities must be a nonempty subset of the parent")
    if validated_child.expires_at > validated_parent.expires_at:
        raise ValueError("delegated expiresAt may not extend the parent expiry")

    attenuated_fields = frozenset(
        {
            "authority_contract_id",
            "issuer_id",
            "child_actor_id",
            "capabilities",
            "expires_at",
            "parent_authority_digest",
            "delegation_chain",
        }
    )
    for field_name, field_info in AuthorityContract.model_fields.items():
        if field_name in attenuated_fields:
            continue
        if getattr(validated_child, field_name) != getattr(validated_parent, field_name):
            alias = field_info.alias or field_name
            raise ValueError(f"{alias} may not differ from the parent authority contract")

    return validated_child
