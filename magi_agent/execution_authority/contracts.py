from __future__ import annotations

from collections.abc import Iterable, Mapping, Set as AbstractSet
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import TYPE_CHECKING, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.state_machine import EffectClass, RequirementState
from magi_agent.ops.authority import FrozenContractModel
from magi_agent.ops.safety import canonical_digest, require_digest


_MAX_CONTRACT_INPUT_NODES = 20_000
_MAX_CONTRACT_INPUT_DEPTH = 64
_MAX_CONTRACT_JSON_BYTES = 1_048_576
_MAX_IJSON_INTEGER = (2**53) - 1


def _preflight_json_depth(payload: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in payload:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_CONTRACT_INPUT_DEPTH:
                raise ValueError("authority contract JSON exceeds the depth budget")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("authority contract JSON has unbalanced containers")


def _load_authority_contract_json(payload: str | bytes | bytearray) -> object:
    if type(payload) is str:
        encoded = payload.encode("utf-8")
        text = payload
    elif type(payload) in (bytes, bytearray):
        encoded = bytes(payload)
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("authority contract JSON must use UTF-8") from exc
    else:
        raise TypeError("authority contract JSON must be an exact string or byte sequence")
    if len(encoded) > _MAX_CONTRACT_JSON_BYTES:
        raise ValueError("authority contract JSON exceeds the byte budget")
    _preflight_json_depth(text)

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("authority contract JSON contains a duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=lambda value: (_ for _ in ()).throw(
                ValueError(f"authority contract JSON contains floating-point number {value}")
            ),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"authority contract JSON contains non-finite number {value}")
            ),
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("authority contract JSON is invalid") from exc


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

    @model_validator(mode="before")
    @classmethod
    def _reject_noncanonical_python_inputs(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        """Reject Python-only aliases before they can become canonical wire bytes."""

        if info.mode == "json":
            raise ValueError(
                "authority JSON must use the duplicate-safe model_validate_json decoder"
            )
        pending: list[tuple[object, int]] = [(value, 0)]
        visited = 0
        while pending:
            current, depth = pending.pop()
            visited += 1
            if visited > _MAX_CONTRACT_INPUT_NODES:
                raise ValueError("authority contract input exceeds the canonical JSON node budget")
            if depth > _MAX_CONTRACT_INPUT_DEPTH:
                raise ValueError("authority contract input exceeds the canonical JSON depth budget")
            if isinstance(current, FrozenContractModel):
                continue
            if type(current) in (bytes, bytearray, memoryview):
                raise ValueError(
                    "canonical JSON requires an exact string; exact integer 1 fields "
                    "cannot use binary aliases"
                )
            if type(current) is int and abs(current) > _MAX_IJSON_INTEGER:
                raise ValueError("authority contract integer exceeds the I-JSON safe range")
            if isinstance(current, AbstractSet):
                raise ValueError("authority contract input must use canonical JSON ordered arrays")
            if type(current) is dict:
                for key, child in current.items():
                    if type(key) is not str:
                        raise ValueError(
                            "authority contract input must use canonical JSON object keys"
                        )
                    pending.append((child, depth + 1))
            elif type(current) in (list, tuple):
                pending.extend((child, depth + 1) for child in current)
            elif isinstance(current, Mapping):
                raise ValueError("authority contract input requires an exact dict")
            elif isinstance(current, Iterable) and not isinstance(current, str):
                raise ValueError("authority contract input rejects Python-only iterable containers")
        return value

    @classmethod
    def model_validate_json(  # type: ignore[override]
        cls,
        json_data: str | bytes | bytearray,
        **kwargs: Any,
    ) -> Self:
        parsed = _load_authority_contract_json(json_data)
        return cls.model_validate(parsed, **kwargs)

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


class ResearchClaimRequirement(_AuthorityContractModel):
    claim_id: str = Field(alias="claimId", min_length=1)
    claim_class: Literal[
        "factual",
        "temporal_fact",
        "comparative",
        "causal",
        "recommendation",
    ] = Field(alias="claimClass")
    proposition: str = Field(min_length=1)
    proposition_digest: str | None = Field(
        default=None,
        alias="propositionDigest",
    )
    freshness: Literal[
        "same_retrieval_window",
        "same_state_root",
        "current_release",
        "historical_snapshot",
    ]

    @model_validator(mode="after")
    def _bind_exact_proposition(self) -> Self:
        expected = "sha256:" + sha256(self.proposition.encode("utf-8")).hexdigest()
        if self.proposition_digest is not None and self.proposition_digest != expected:
            raise ValueError("propositionDigest does not match proposition")
        object.__setattr__(self, "proposition_digest", expected)
        return self


class ResearchProofObligation(_AuthorityContractModel):
    """Research semantics frozen into the Task Contract v1 wire shape."""

    claims: tuple[ResearchClaimRequirement, ...] = Field(min_length=1)
    query_classes: tuple[
        Literal[
            "official_primary",
            "peer_reviewed_primary",
            "first_party_data",
            "reputable_secondary",
            "exploratory",
        ],
        ...,
    ] = Field(alias="queryClasses", min_length=1)
    primary_source_rule: Literal[
        "required",
        "required_when_available",
        "preferred",
        "not_required",
    ] = Field(alias="primarySourceRule")
    conflict_handling: Literal[
        "resolve",
        "resolve_or_disclose",
        "disclose",
        "block",
    ] = Field(alias="conflictHandling")
    stopping_rules: tuple[
        Literal[
            "claim_coverage_met",
            "conflicts_resolved_or_disclosed",
            "source_classes_exhausted",
            "explicit_budget_reached",
        ],
        ...,
    ] = Field(alias="stoppingRules", min_length=1)
    limited_snippet_allowance: Literal[
        "forbidden",
        "discovery_only",
        "explicitly_accepted_proof",
    ] = Field(alias="limitedSnippetAllowance")

    @field_validator("claims", "query_classes", "stopping_rules", mode="before")
    @classmethod
    def _require_ordered_research_sequences(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_ordered_collection(
            value,
            field_name=info.field_name or "research sequence",
        )

    @model_validator(mode="after")
    def _require_unique_claims_and_queries(self) -> Self:
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("research claim IDs must be unique")
        if len(self.query_classes) != len(set(self.query_classes)):
            raise ValueError("research query classes must be unique")
        if len(self.stopping_rules) != len(set(self.stopping_rules)):
            raise ValueError("research stopping rules must be unique")
        return self


class ProofObligation(_AuthorityContractModel):
    evidence_kinds: tuple[str, ...] = Field(alias="evidenceKinds", min_length=1)
    freshness: str = Field(min_length=1)
    required_producer: str | None = Field(default=None, alias="requiredProducer")
    research: ResearchProofObligation | None = None

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
    schema_id: Literal["magi.task_contract.v1"] = Field(
        default="magi.task_contract.v1",
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
        if (self.supersedes_task_contract_id is None) != (self.supersedes_version is None):
            raise ValueError("supersedesTaskContractId and supersedesVersion are both-or-neither")
        if (
            self.supersedes_task_contract_id == self.task_contract_id
            and self.supersedes_version is not None
            and self.version != self.supersedes_version + 1
        ):
            raise ValueError(
                "same-task clarification version must advance supersedesVersion exactly once"
            )
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
    effect_class: EffectClass = Field(alias="effectClass")
    resource_ref: str = Field(alias="resourceRef", min_length=1)
    network_refs: tuple[str, ...] = Field(alias="networkRefs")
    credential_refs: tuple[str, ...] = Field(alias="credentialRefs")
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )

    @field_validator("resource_ref", mode="before")
    @classmethod
    def _require_exact_identity_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string(value, field_name=info.field_name or "capability")

    @field_validator("effect_class", mode="before")
    @classmethod
    def _require_effect_class_wire_type(cls, value: object) -> object:
        if type(value) not in (str, EffectClass):
            raise ValueError("effectClass must be an exact string or EffectClass")
        return value

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

    @field_validator("workspace_view_binding_digest")
    @classmethod
    def _validate_workspace_view_binding_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @model_validator(mode="after")
    def _require_workspace_view_for_workspace_effects(self) -> Self:
        if (
            self.resource_ref.startswith("workspace://")
            or self.effect_class
            in {
                EffectClass.WORKSPACE_READ,
                EffectClass.WORKSPACE_WRITE,
                EffectClass.WORKSPACE_DELETE,
            }
        ) and self.workspace_view_binding_digest is None:
            raise ValueError("workspaceViewBindingDigest is required for workspace capabilities")
        return self


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
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )
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
        "workspace_view_binding_digest",
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
        "workspace_view_binding_digest",
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
        workspace_bindings = {
            capability.workspace_view_binding_digest
            for capability in self.capabilities
            if capability.workspace_view_binding_digest is not None
        }
        if workspace_bindings:
            if workspace_bindings != {self.workspace_view_binding_digest}:
                raise ValueError("workspaceViewBindingDigest must match every workspace capability")
        elif self.workspace_view_binding_digest is not None:
            raise ValueError("workspaceViewBindingDigest requires a workspace-bound capability")
        if self.parent_authority_digest is None:
            if self.delegation_chain:
                raise ValueError("root authority contracts may not contain a delegationChain")
        elif not self.delegation_chain:
            raise ValueError("delegated authority contracts require a nonempty delegationChain")
        elif self.delegation_chain[-1] != self.parent_authority_digest:
            raise ValueError("delegationChain must end with parentAuthorityDigest")
        return self


class UserDecisionRequest(_AuthorityContractModel):
    schema_id: Literal["magi.user_decision_request.v1"] = Field(
        default="magi.user_decision_request.v1",
        alias="schemaId",
    )
    decision_request_id: str = Field(alias="decisionRequestId", min_length=1)
    principal_id: str = Field(alias="principalId", min_length=1)
    tenant_id: str = Field(alias="tenantId", min_length=1)
    session_id: str = Field(alias="sessionId", min_length=1)
    turn_id: str = Field(alias="turnId", min_length=1)
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    normalized_request_digest: str = Field(alias="normalizedRequestDigest")
    capabilities: tuple[AuthorityCapability, ...] = Field(min_length=1)
    capabilities_digest: str = Field(default="", alias="capabilitiesDigest")
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )
    authority_ceiling_digest: str = Field(alias="authorityCeilingDigest")
    policy_digest: str = Field(alias="policyDigest")
    pending_event_id: str = Field(alias="pendingEventId", min_length=1)
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes", min_length=1)
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime = Field(alias="expiresAt")
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="before")
    @classmethod
    def _require_exact_schema_id(cls, value: object) -> object:
        if isinstance(value, Mapping):
            for field_name in ("schemaId", "schema_id"):
                if field_name in value:
                    _require_exact_string(value[field_name], field_name="schemaId")
        return value

    @field_validator(
        "decision_request_id",
        "principal_id",
        "tenant_id",
        "session_id",
        "turn_id",
        "task_contract_id",
        "completion_epoch_id",
        "action_id",
        "authority_partition_id",
        "pending_event_id",
        mode="before",
    )
    @classmethod
    def _require_exact_identity_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string(value, field_name=info.field_name or "request identity")

    @field_validator(
        "task_contract_digest",
        "normalized_request_digest",
        "capabilities_digest",
        "workspace_view_binding_digest",
        "authority_ceiling_digest",
        "policy_digest",
        mode="before",
    )
    @classmethod
    def _require_exact_digest_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if value is None:
            return None
        return _require_exact_string(value, field_name=info.field_name or "request digest")

    @field_validator(
        "task_contract_digest",
        "normalized_request_digest",
        "workspace_view_binding_digest",
        "authority_ceiling_digest",
        "policy_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _require_ordered_capabilities(cls, value: object) -> object:
        return _require_ordered_model_collection(value, field_name="capabilities")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _require_ordered_reason_codes(cls, value: object) -> object:
        return _require_exact_string_sequence(value, field_name="reasonCodes")

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def _require_datetime_wire_type(
        cls,
        value: object,
    ) -> object:
        return _require_exact_datetime_wire_type(value, contract_name="user decision request")

    @field_validator("created_at", "expires_at")
    @classmethod
    def _require_utc_datetime(cls, value: datetime) -> datetime:
        return _validate_exact_utc_datetime(value, contract_name="user decision request")

    @model_validator(mode="after")
    def _derive_capabilities_digest_and_validate_window(self) -> Self:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate capabilities are not allowed")
        expected_digest = _validated_capabilities_digest(self.capabilities)
        if self.capabilities_digest:
            try:
                supplied_digest = require_digest(self.capabilities_digest)
            except ValueError as exc:
                raise ValueError("capabilitiesDigest must use a canonical sha256 digest") from exc
            if supplied_digest != expected_digest:
                raise ValueError(
                    "capabilitiesDigest must equal the canonical ordered capabilities digest"
                )
        elif "capabilities_digest" in self.model_fields_set:
            raise ValueError("capabilitiesDigest must use a canonical sha256 digest")
        object.__setattr__(self, "capabilities_digest", expected_digest)
        workspace_bindings = {
            capability.workspace_view_binding_digest
            for capability in self.capabilities
            if capability.workspace_view_binding_digest is not None
        }
        if workspace_bindings and workspace_bindings != {self.workspace_view_binding_digest}:
            raise ValueError("workspaceViewBindingDigest must match every requested capability")
        if not workspace_bindings and self.workspace_view_binding_digest is not None:
            raise ValueError("workspaceViewBindingDigest requires a workspace-bound capability")
        if self.expires_at <= self.created_at:
            raise ValueError("expiresAt must be later than createdAt")
        return self


class UserDecisionReceipt(_AuthorityContractModel):
    schema_id: Literal["magi.user_decision_receipt.v1"] = Field(
        default="magi.user_decision_receipt.v1",
        alias="schemaId",
    )
    receipt_id: str = Field(alias="receiptId", min_length=1)
    decision_request_id: str = Field(alias="decisionRequestId", min_length=1)
    decision: Literal["approve", "deny", "revoke"]
    authenticated_actor_id: str = Field(alias="authenticatedActorId", min_length=1)
    authentication_key_id: str = Field(alias="authenticationKeyId", min_length=1)
    authentication_context_digest: str = Field(alias="authenticationContextDigest")
    authentication_nonce_digest: str = Field(alias="authenticationNonceDigest")
    transport_receipt_digest: str = Field(alias="transportReceiptDigest")
    principal_id: str = Field(alias="principalId", min_length=1)
    tenant_id: str = Field(alias="tenantId", min_length=1)
    session_id: str = Field(alias="sessionId", min_length=1)
    turn_id: str = Field(alias="turnId", min_length=1)
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    normalized_request_digest: str = Field(alias="normalizedRequestDigest")
    authority_ceiling_digest: str = Field(alias="authorityCeilingDigest")
    policy_digest: str = Field(alias="policyDigest")
    capabilities_digest: str = Field(alias="capabilitiesDigest")
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )
    issued_at: datetime = Field(alias="issuedAt")
    expires_at: datetime = Field(alias="expiresAt")
    revokes_receipt_digest: str | None = Field(default=None, alias="revokesReceiptDigest")

    @model_validator(mode="before")
    @classmethod
    def _require_exact_schema_id(cls, value: object) -> object:
        if isinstance(value, Mapping):
            for field_name in ("schemaId", "schema_id"):
                if field_name in value:
                    _require_exact_string(value[field_name], field_name="schemaId")
        return value

    @field_validator(
        "receipt_id",
        "decision_request_id",
        "decision",
        "authenticated_actor_id",
        "authentication_key_id",
        "principal_id",
        "tenant_id",
        "session_id",
        "turn_id",
        "task_contract_id",
        "completion_epoch_id",
        "action_id",
        "authority_partition_id",
        mode="before",
    )
    @classmethod
    def _require_exact_identity_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string(value, field_name=info.field_name or "receipt identity")

    @field_validator(
        "authentication_context_digest",
        "authentication_nonce_digest",
        "transport_receipt_digest",
        "task_contract_digest",
        "normalized_request_digest",
        "authority_ceiling_digest",
        "policy_digest",
        "capabilities_digest",
        "workspace_view_binding_digest",
        "revokes_receipt_digest",
        mode="before",
    )
    @classmethod
    def _require_exact_digest_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if value is None:
            return None
        return _require_exact_string(value, field_name=info.field_name or "receipt digest")

    @field_validator(
        "authentication_context_digest",
        "authentication_nonce_digest",
        "transport_receipt_digest",
        "task_contract_digest",
        "normalized_request_digest",
        "authority_ceiling_digest",
        "policy_digest",
        "capabilities_digest",
        "workspace_view_binding_digest",
        "revokes_receipt_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("issued_at", "expires_at", mode="before")
    @classmethod
    def _require_datetime_wire_type(
        cls,
        value: object,
    ) -> object:
        return _require_exact_datetime_wire_type(value, contract_name="user decision receipt")

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _require_utc_datetime(cls, value: datetime) -> datetime:
        return _validate_exact_utc_datetime(value, contract_name="user decision receipt")

    @model_validator(mode="after")
    def _validate_receipt_invariants(self) -> Self:
        if self.authenticated_actor_id != self.principal_id:
            raise ValueError("authenticatedActorId must equal principalId")
        if self.expires_at <= self.issued_at:
            raise ValueError("expiresAt must be later than issuedAt")
        if self.decision == "revoke":
            if self.revokes_receipt_digest is None:
                raise ValueError("revokesReceiptDigest is required for revoke decisions")
        elif self.revokes_receipt_digest is not None:
            raise ValueError("revokesReceiptDigest is forbidden for approve and deny decisions")
        return self


class AuthorityResumeBinding(_AuthorityContractModel):
    decision_request_id: str = Field(alias="decisionRequestId", min_length=1)
    authenticated_actor_id: str = Field(alias="authenticatedActorId", min_length=1)
    session_id: str = Field(alias="sessionId", min_length=1)
    turn_id: str = Field(alias="turnId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    transcript_digest: str = Field(alias="transcriptDigest")
    checkpoint_digest: str = Field(alias="checkpointDigest")
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    expected_head_sequence: int = Field(alias="expectedHeadSequence", ge=0, strict=True)
    expected_head_hash: str = Field(alias="expectedHeadHash")
    expected_head_compare_version: int = Field(
        alias="expectedHeadCompareVersion",
        ge=0,
        strict=True,
    )
    state_projection_id: str = Field(alias="stateProjectionId", min_length=1)
    expected_state_sequence: int = Field(alias="expectedStateSequence", ge=0, strict=True)
    expected_state_event_hash: str = Field(alias="expectedStateEventHash")
    expected_state_root: str = Field(alias="expectedStateRoot")
    expected_state_compare_version: int = Field(
        alias="expectedStateCompareVersion",
        ge=0,
        strict=True,
    )

    @field_validator(
        "decision_request_id",
        "authenticated_actor_id",
        "session_id",
        "turn_id",
        "run_id",
        "action_id",
        "task_contract_id",
        "completion_epoch_id",
        "authority_partition_id",
        "state_projection_id",
        mode="before",
    )
    @classmethod
    def _require_exact_identity_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string(value, field_name=info.field_name or "resume identity")

    @field_validator(
        "task_contract_digest",
        "transcript_digest",
        "checkpoint_digest",
        "expected_head_hash",
        "expected_state_event_hash",
        "expected_state_root",
        mode="before",
    )
    @classmethod
    def _require_exact_digest_string(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        return _require_exact_string(value, field_name=info.field_name or "resume digest")

    @field_validator(
        "task_contract_digest",
        "transcript_digest",
        "checkpoint_digest",
        "expected_head_hash",
        "expected_state_event_hash",
        "expected_state_root",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)


def _require_ordered_collection(value: object, *, field_name: str) -> object:
    if isinstance(value, AbstractSet):
        raise ValueError(f"{field_name} must use an ordered collection")
    return value


def _require_ordered_model_collection(value: object, *, field_name: str) -> object:
    value = _require_ordered_collection(value, field_name=field_name)
    if type(value) not in (list, tuple):
        raise ValueError(f"{field_name} must use an ordered list or tuple")
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


def _require_exact_datetime_wire_type(value: object, *, contract_name: str) -> object:
    if type(value) is datetime:
        return value
    if type(value) is str:
        try:
            float(value)
        except ValueError:
            return value
        raise ValueError(f"{contract_name} datetimes must not use numeric timestamp strings")
    raise ValueError(f"{contract_name} datetimes must be exact datetime instances or ISO strings")


def _validate_exact_utc_datetime(value: datetime, *, contract_name: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{contract_name} datetimes must be exact datetime instances")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{contract_name} datetimes must use UTC")
    return value


def _validate_snapshot_instance(snapshot: object) -> TaskContractSnapshot:
    if type(snapshot) is not TaskContractSnapshot:
        raise TypeError("task contract snapshot must be an exact TaskContractSnapshot")
    return TaskContractSnapshot.model_validate(snapshot)


def _validate_binding_instance(binding: object) -> TaskContractBinding:
    if type(binding) is not TaskContractBinding:
        raise TypeError("task contract binding must be an exact TaskContractBinding")
    return TaskContractBinding.model_validate(binding)


def canonical_task_contract_json(snapshot: TaskContractSnapshot) -> str:
    """Return the sole canonical Task Contract v1 JSON representation."""

    validated = _validate_snapshot_instance(snapshot)
    payload = TaskContractSnapshot.model_dump(validated, by_alias=True, mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_task_contract_bytes(snapshot: TaskContractSnapshot) -> bytes:
    return canonical_task_contract_json(snapshot).encode("utf-8")


def _validated_snapshot_digest(snapshot: TaskContractSnapshot) -> str:
    return "sha256:" + sha256(canonical_task_contract_bytes(snapshot)).hexdigest()


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


def _scope_contains(broader: str, narrower: str) -> bool:
    """Return whether one canonical or logical scope wholly contains another."""

    if broader == narrower:
        return True
    if broader.startswith(("http://", "https://")) and narrower.startswith(("http://", "https://")):
        broader_url = urlsplit(broader)
        narrower_url = urlsplit(narrower)
        if (
            broader_url.scheme != narrower_url.scheme
            or broader_url.netloc != narrower_url.netloc
            or broader_url.query
            or broader_url.fragment
        ):
            return False
        return narrower_url.path.startswith(broader_url.path.rstrip("/") + "/")
    if broader.startswith("workspace://") and narrower.startswith("workspace://"):
        broader_root, _, broader_path = broader.removeprefix("workspace://").partition("/")
        narrower_root, _, narrower_path = narrower.removeprefix("workspace://").partition("/")
        if broader_root != narrower_root:
            return False
        if not broader_path:
            return True
        return narrower_path.startswith(broader_path.rstrip("/") + "/")
    return narrower.startswith(broader.rstrip("/") + "/")


def _scopes_contain(
    broader: tuple[str, ...],
    narrower: tuple[str, ...],
) -> bool:
    if not narrower:
        return True
    if not broader:
        return False
    return all(any(_scope_contains(parent, child) for parent in broader) for child in narrower)


def _capability_contains(
    broader: AuthorityCapability,
    narrower: AuthorityCapability,
) -> bool:
    return (
        broader.effect_class is narrower.effect_class
        and _scope_contains(broader.resource_ref, narrower.resource_ref)
        and _scopes_contain(broader.network_refs, narrower.network_refs)
        and _scopes_contain(broader.credential_refs, narrower.credential_refs)
        and broader.workspace_view_binding_digest == narrower.workspace_view_binding_digest
    )


def _intersect_scope_sets(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> tuple[str, ...]:
    if not left or not right:
        return ()
    intersections = {
        right_scope if _scope_contains(left_scope, right_scope) else left_scope
        for left_scope in left
        for right_scope in right
        if _scope_contains(left_scope, right_scope) or _scope_contains(right_scope, left_scope)
    }
    return tuple(sorted(intersections))


def _intersect_capabilities(
    left: AuthorityCapability,
    right: AuthorityCapability,
) -> AuthorityCapability | None:
    if left.effect_class is not right.effect_class:
        return None
    if left.workspace_view_binding_digest != right.workspace_view_binding_digest:
        return None
    if _scope_contains(left.resource_ref, right.resource_ref):
        resource_ref = right.resource_ref
    elif _scope_contains(right.resource_ref, left.resource_ref):
        resource_ref = left.resource_ref
    else:
        return None
    return AuthorityCapability(
        effectClass=left.effect_class,
        resourceRef=resource_ref,
        networkRefs=_intersect_scope_sets(left.network_refs, right.network_refs),
        credentialRefs=_intersect_scope_sets(
            left.credential_refs,
            right.credential_refs,
        ),
        workspaceViewBindingDigest=left.workspace_view_binding_digest,
    )


def _capabilities_overlap(
    left: AuthorityCapability,
    right: AuthorityCapability,
) -> bool:
    return (
        left.effect_class is right.effect_class
        and left.workspace_view_binding_digest == right.workspace_view_binding_digest
        and (
            _scope_contains(left.resource_ref, right.resource_ref)
            or _scope_contains(right.resource_ref, left.resource_ref)
        )
    )


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

    originating_inputs = tuple(
        value
        for value in validated_inputs
        if value.source != "guardian" and value.decision != "deny"
    )
    if not originating_inputs:
        return AuthorityDecision.model_validate(
            {
                "status": "deny",
                "reason_codes": ("no_allowing_authority",),
                "capabilities": (),
            }
        )

    allowing_inputs = tuple(value for value in validated_inputs if value.decision != "deny")
    effective_capabilities = originating_inputs[0].capabilities
    skipped_origin = False
    for authority_input in allowing_inputs:
        if authority_input is originating_inputs[0] and not skipped_origin:
            skipped_origin = True
            continue
        intersections = {
            _authority_capability_json(intersection): intersection
            for current in effective_capabilities
            for ceiling in authority_input.capabilities
            if (intersection := _intersect_capabilities(current, ceiling)) is not None
        }
        effective_capabilities = tuple(intersections[key] for key in sorted(intersections))
        if not effective_capabilities:
            break

    denied_capabilities = tuple(
        capability
        for authority_input in validated_inputs
        if authority_input.decision == "deny"
        for capability in authority_input.capabilities
    )
    effective_capabilities = tuple(
        capability
        for capability in effective_capabilities
        if not any(_capabilities_overlap(capability, denied) for denied in denied_capabilities)
    )
    if not effective_capabilities:
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

    capabilities = tuple(sorted(effective_capabilities, key=_authority_capability_json))
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
    if validated_child.authority_contract_id == validated_parent.authority_contract_id:
        raise ValueError("delegated authority requires a distinct authorityContractId")
    expected_chain = (*validated_parent.delegation_chain, parent_digest)
    if validated_child.delegation_chain != expected_chain:
        raise ValueError("delegationChain must append the canonical parent digest")

    if not validated_child.capabilities or any(
        not any(
            _capability_contains(parent_capability, child_capability)
            for parent_capability in validated_parent.capabilities
        )
        for child_capability in validated_child.capabilities
    ):
        raise ValueError("delegated capabilities must be a nonempty subset of the parent")
    if validated_child.expires_at > validated_parent.expires_at:
        raise ValueError("delegated expiresAt may not extend the parent expiry")

    attenuated_fields = frozenset(
        {
            "authority_contract_id",
            "issuer_id",
            "child_actor_id",
            "capabilities",
            "workspace_view_binding_digest",
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


def _validate_capability_instance(capability: object) -> AuthorityCapability:
    if type(capability) is not AuthorityCapability:
        raise TypeError("capabilities must be exact AuthorityCapability instances")
    return AuthorityCapability.model_validate(capability)


def _validate_capabilities_tuple(
    capabilities: object,
) -> tuple[AuthorityCapability, ...]:
    if type(capabilities) is not tuple:
        raise TypeError("capabilities must be an exact tuple")
    validated = tuple(_validate_capability_instance(value) for value in capabilities)
    if not validated:
        raise ValueError("capabilities must not be empty")
    if len(validated) != len(set(validated)):
        raise ValueError("duplicate capabilities are not allowed")
    return validated


def _validated_capabilities_digest(
    capabilities: tuple[AuthorityCapability, ...],
) -> str:
    payload = {
        "capabilities": [
            AuthorityCapability.model_dump(capability, by_alias=True, mode="json")
            for capability in capabilities
        ]
    }
    return canonical_digest(payload)


def canonical_capabilities_digest(
    capabilities: tuple[AuthorityCapability, ...],
) -> str:
    return _validated_capabilities_digest(_validate_capabilities_tuple(capabilities))


def _validate_user_decision_request_instance(
    request: object,
) -> UserDecisionRequest:
    if type(request) is not UserDecisionRequest:
        raise TypeError("request must be an exact UserDecisionRequest")
    return UserDecisionRequest.model_validate(request)


def _validate_user_decision_receipt_instance(
    receipt: object,
) -> UserDecisionReceipt:
    if type(receipt) is not UserDecisionReceipt:
        raise TypeError("receipt must be an exact UserDecisionReceipt")
    return UserDecisionReceipt.model_validate(receipt)


def _validate_authority_resume_binding_instance(
    binding: object,
) -> AuthorityResumeBinding:
    if type(binding) is not AuthorityResumeBinding:
        raise TypeError("binding must be an exact AuthorityResumeBinding")
    return AuthorityResumeBinding.model_validate(binding)


def _validated_user_decision_request_digest(
    request: UserDecisionRequest,
) -> str:
    payload = UserDecisionRequest.model_dump(request, by_alias=True, mode="json")
    request_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return "sha256:" + sha256(request_json.encode("utf-8")).hexdigest()


def canonical_user_decision_request_digest(request: UserDecisionRequest) -> str:
    return _validated_user_decision_request_digest(
        _validate_user_decision_request_instance(request)
    )


def _validated_user_decision_receipt_digest(
    receipt: UserDecisionReceipt,
) -> str:
    payload = UserDecisionReceipt.model_dump(receipt, by_alias=True, mode="json")
    return canonical_digest(payload)


def canonical_user_decision_receipt_digest(receipt: UserDecisionReceipt) -> str:
    return _validated_user_decision_receipt_digest(
        _validate_user_decision_receipt_instance(receipt)
    )


def _validated_authority_resume_binding_digest(
    binding: AuthorityResumeBinding,
) -> str:
    payload = AuthorityResumeBinding.model_dump(binding, by_alias=True, mode="json")
    return canonical_digest(payload)


def canonical_authority_resume_binding_digest(
    binding: AuthorityResumeBinding,
) -> str:
    return _validated_authority_resume_binding_digest(
        _validate_authority_resume_binding_instance(binding)
    )


def validate_user_decision_receipt_binding(
    request: UserDecisionRequest,
    receipt: UserDecisionReceipt,
) -> UserDecisionReceipt:
    validated_request = _validate_user_decision_request_instance(request)
    validated_receipt = _validate_user_decision_receipt_instance(receipt)

    bindings = (
        ("decisionRequestId", "decision_request_id"),
        ("principalId", "principal_id"),
        ("tenantId", "tenant_id"),
        ("sessionId", "session_id"),
        ("turnId", "turn_id"),
        ("taskContractId", "task_contract_id"),
        ("taskVersion", "task_version"),
        ("taskContractDigest", "task_contract_digest"),
        ("completionEpochId", "completion_epoch_id"),
        ("actionId", "action_id"),
        ("authorityPartitionId", "authority_partition_id"),
        ("normalizedRequestDigest", "normalized_request_digest"),
        ("authorityCeilingDigest", "authority_ceiling_digest"),
        ("policyDigest", "policy_digest"),
        ("capabilitiesDigest", "capabilities_digest"),
        ("workspaceViewBindingDigest", "workspace_view_binding_digest"),
    )
    if validated_receipt.authenticated_actor_id != validated_request.principal_id:
        raise ValueError("authenticatedActorId does not match the request principalId")
    for alias, field_name in bindings:
        if getattr(validated_receipt, field_name) != getattr(validated_request, field_name):
            raise ValueError(f"{alias} does not match the user decision request")
    if validated_receipt.issued_at < validated_request.created_at:
        raise ValueError("issuedAt does not match the user decision request time window")
    if validated_receipt.issued_at >= validated_request.expires_at:
        raise ValueError("issuedAt does not match the user decision request time window")
    if validated_receipt.expires_at > validated_request.expires_at:
        raise ValueError("expiresAt does not match the user decision request time window")
    return validated_receipt


# Cross-workstream envelopes live in a separate leaf module to keep the core
# capability and user-decision contracts reviewable.  A lazy compatibility
# export preserves ``execution_authority.contracts`` as the public import path
# without making direct ``execution_authority.envelopes`` imports cyclic.
if TYPE_CHECKING:
    from magi_agent.execution_authority.envelopes import *  # noqa: F401,F403


_ENVELOPE_EXPORTS = frozenset(
    {
        "ActionAdmission",
        "ActionIntent",
        "ActionProposal",
        "ActionReceipt",
        "ActionResolution",
        "ActionSnapshot",
        "AttemptObservationRecording",
        "AttemptSnapshot",
        "AttemptVerificationRecording",
        "BackendObservation",
        "CompletionPersistenceReceipt",
        "CompletionVerdict",
        "CoverageDescriptor",
        "DependencyHealth",
        "EffectDeclarationBinding",
        "EpochSeal",
        "EpochSnapshot",
        "EvidenceNode",
        "EvidenceNodeDraft",
        "EvidenceEdge",
        "EvidenceRecordDraft",
        "EvidenceRecordRecording",
        "ExecutionPreparation",
        "ExecutionStart",
        "ExecutionStartRequest",
        "FinalizationRequest",
        "FinalizationEvaluationRequest",
        "FreshnessBinding",
        "GenericJournalEventDraft",
        "IntegrityScanResult",
        "JournalCoverageWindow",
        "JournalChainLink",
        "JournalEvent",
        "JournalEventDraft",
        "JournalHead",
        "LeaseSnapshot",
        "NonExecutionProof",
        "NormalizedInputDraft",
        "NormalizedInputSnapshot",
        "OutboxDraft",
        "OutboxItem",
        "PartitionGate",
        "PartitionRecoveryPlan",
        "ProjectionCursorBinding",
        "ProjectionCursorSnapshot",
        "RecoveryContext",
        "RecoveryDecision",
        "RecoverySessionSnapshot",
        "RequiredProjection",
        "RequirementResult",
        "ResearchClaimResult",
        "ResearchSourceBinding",
        "ResponseClaim",
        "ResponseClaimManifest",
        "SourceSpan",
        "UserApprovalConsumption",
        "UserDecisionExpirationRequest",
        "UserDecisionInvalidationRequest",
        "UserDecisionRecording",
        "UserDecisionSnapshot",
        "UserDecisionTransition",
        "VerificationEvidenceBinding",
        "WorkspaceCommitDecision",
        "WorkspaceCommitDecisionRequest",
        "WorkspaceCommitRecoveryClaim",
        "WorkspaceCommitRecoveryClaimRequest",
        "WorkspaceCommitSnapshot",
        "WorkspacePublicationObservation",
        "WorkspacePublicationReceipt",
        "WorkspaceQuarantineReceipt",
        "WorkspaceSnapshot",
        "WorkspaceTransactionRequest",
        "canonical_backend_observation_digest",
        "canonical_evidence_node_digest",
        "canonical_evidence_edges_digest",
        "canonical_effect_declaration_digest",
        "canonical_action_intent_digest",
        "canonical_action_identity_digest",
        "canonical_action_proposal_digest",
        "canonical_provider_guarantees_digest",
        "canonical_recovery_decision_digest",
        "canonical_required_projections_digest",
        "canonical_resource_refs_digest",
        "canonical_response_claim_manifest_digest",
        "canonical_workspace_view_binding_digest",
        "canonical_workspace_commit_decision_digest",
        "canonical_workspace_commit_recovery_claim_digest",
        "canonical_workspace_publication_observation_digest",
        "draft_journal_event",
        "validate_source_span",
        "validate_recovery_decision_context",
        "validate_completion_persistence_contract",
        "validate_completion_persistence_receipt",
        "validate_finalization_request_epoch",
        "validate_same_action_identity",
        "validate_action_proposal_input_snapshot",
    }
)


def __getattr__(name: str) -> Any:
    if name not in _ENVELOPE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from magi_agent.execution_authority import envelopes

    value = getattr(envelopes, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_ENVELOPE_EXPORTS})
