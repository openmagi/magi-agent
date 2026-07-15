"""Frozen cross-workstream execution-authority envelopes.

This module contains wire contracts only.  It deliberately has no persistence,
executor, policy, or host imports; live behavior arrives through ``ports``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.canonicalization import (
    require_canonical_http_resource_ref,
    require_canonical_workspace_resource_ref,
)
from magi_agent.execution_authority.contracts import (
    AuthorityCapability,
    AuthorityContract,
    AuthorityResumeBinding,
    TaskContractBinding,
    TaskContractSnapshot,
    UserDecisionReceipt,
    UserDecisionRequest,
    _AuthorityContractModel,
    canonical_capabilities_digest,
    canonical_authority_contract_digest,
    canonical_authority_resume_binding_digest,
    canonical_task_contract_digest,
    canonical_user_decision_receipt_digest,
    canonical_user_decision_request_digest,
    validate_user_decision_receipt_binding,
)
from magi_agent.execution_authority.state_machine import (
    ActionState,
    AttemptKind,
    CompletionEpochState,
    CompletionStatus,
    DependencyStatus,
    EffectClass,
    EvidenceKind,
    EvidenceSemanticClass,
    IdempotencyCapability,
    LeaseState,
    ObservationOutcome,
    OutboxState,
    ProviderGuarantee,
    RecoveryDisposition,
    RecoveryStrategy,
    RequirementState,
    ResourceSemantics,
    TransmissionState,
    UserDecisionState,
    WorkspacePublicationState,
)
from magi_agent.ops.safety import (
    is_secret_key,
    redact_secret_tokens,
    require_digest,
)


_SENSITIVE_KEY_RE = re.compile(
    "(?:to" + "ken|se" + "cret|pass" + "word|coo" + "kie|author" + "ization|credential)",
    re.IGNORECASE,
)
_MAX_PAYLOAD_DEPTH = 32
_MAX_PAYLOAD_NODES = 10_000
_MAX_PAYLOAD_BYTES = 1_048_576


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_model_digest(
    model: _AuthorityContractModel,
    *,
    exclude: frozenset[str] = frozenset(),
) -> str:
    payload = model.model_dump(by_alias=True, mode="json", exclude=set(exclude))
    encoded = _canonical_json(payload).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _strict_json_loads(payload_json: str) -> object:
    if type(payload_json) is not str:
        raise ValueError("journal payload JSON must be an exact string")
    if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("journal payload JSON exceeds the byte limit")

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("journal payload JSON contains a duplicate key")
            result[key] = value
        return result

    try:
        return json.loads(
            payload_json,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"journal payload contains non-finite number {value}")
            ),
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("journal payload JSON is invalid") from exc


def _reject_sensitive_keys(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 0)]
    seen = 0
    while pending:
        current, depth = pending.pop()
        seen += 1
        if seen > _MAX_PAYLOAD_NODES:
            raise ValueError("journal payload exceeds the validation node budget")
        if depth > _MAX_PAYLOAD_DEPTH:
            raise ValueError("journal payload exceeds the validation depth budget")
        if isinstance(current, Mapping):
            for key, child in current.items():
                if type(key) is not str:
                    raise ValueError("journal payload object keys must be exact strings")
                if _SENSITIVE_KEY_RE.search(key) or is_secret_key(
                    key, include_public_credential_keys=True
                ):
                    raise ValueError("journal payload contains a sensitive key")
                pending.append((child, depth + 1))
        elif isinstance(current, (list, tuple)):
            pending.extend((child, depth + 1) for child in current)
        elif type(current) is str and redact_secret_tokens(current) != current:
            raise ValueError("journal payload contains a sensitive value")


class EnvelopeModel(_AuthorityContractModel):
    """Authority envelope with shared digest/hash/root and UTC validation."""

    @model_validator(mode="before")
    @classmethod
    def _reject_coercible_schema_versions_and_datetimes(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        for key, raw_value in value.items():
            if type(raw_value) in (bytes, bytearray, memoryview):
                raise ValueError(f"{key} must use an exact JSON string or scalar")
        schema_field = cls.model_fields.get("schema_version")
        if (
            schema_field is not None
            and type(schema_field.default) is int
            and schema_field.default == 1
        ):
            schema_keys = tuple(
                dict.fromkeys((schema_field.alias or "schema_version", "schema_version"))
            )
            for schema_key in schema_keys:
                if schema_key not in value:
                    continue
                schema_value = value[schema_key]
                if type(schema_value) is not int or schema_value != 1:
                    raise ValueError("schemaVersion must be the exact integer 1")
        for field_name, field_info in cls.model_fields.items():
            if not field_name.endswith("_at"):
                continue
            keys = tuple(dict.fromkeys((field_info.alias or field_name, field_name)))
            for key in keys:
                if key not in value or value[key] is None:
                    continue
                datetime_value = value[key]
                if type(datetime_value) not in (datetime, str):
                    raise ValueError(
                        f"{key} must be an exact datetime instance or ISO datetime string"
                    )
                if type(datetime_value) is str:
                    try:
                        float(datetime_value)
                    except ValueError:
                        pass
                    else:
                        raise ValueError(f"{key} datetime must not be a numeric string")
        return value

    @model_validator(mode="after")
    def _validate_digest_hash_root_and_utc_fields(self) -> Self:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if value is None:
                continue
            if field_name.endswith(("_digest", "_hash", "_root")) or field_name in {
                "state_root_before",
                "state_root_after",
            }:
                if type(value) is not str:
                    raise ValueError(f"{field_name} must be an exact digest string")
                require_digest(value)
            if isinstance(value, datetime):
                if type(value) is not datetime:
                    raise ValueError(f"{field_name} must be an exact datetime")
                if value.tzinfo is None or value.utcoffset() != timedelta(0):
                    raise ValueError(f"{field_name} must use UTC")
        return self


def _reject_empty_string_items(
    value: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if any(item == "" for item in value):
        raise ValueError(f"{field_name} must not contain empty strings")
    return value


def _resource_scope_covers(granted_ref: str, resource_ref: str) -> bool:
    if granted_ref == resource_ref:
        return True
    if granted_ref.startswith("workspace://") and resource_ref.startswith("workspace://"):
        granted_root, _, granted_path = granted_ref.removeprefix("workspace://").partition("/")
        resource_root, _, resource_path = resource_ref.removeprefix("workspace://").partition("/")
        if granted_root != resource_root:
            return False
        if not granted_path:
            return True
        return resource_path.startswith(granted_path.rstrip("/") + "/")
    if granted_ref.startswith(("http://", "https://")) and resource_ref.startswith(
        ("http://", "https://")
    ):
        grant = urlsplit(granted_ref)
        resource = urlsplit(resource_ref)
        if (
            grant.scheme != resource.scheme
            or grant.netloc != resource.netloc
            or grant.query
            or grant.fragment
        ):
            return False
        return resource.path.startswith(grant.path.rstrip("/") + "/")
    return False


def canonical_provider_guarantees_digest(
    guarantees: tuple[ProviderGuarantee, ...],
) -> str:
    if type(guarantees) is not tuple:
        raise TypeError("provider guarantees must be an exact tuple")
    if not guarantees:
        raise ValueError("provider guarantees must not be empty")
    if any(type(guarantee) is not ProviderGuarantee for guarantee in guarantees):
        raise TypeError("provider guarantees must use exact ProviderGuarantee values")
    values = tuple(guarantee.value for guarantee in guarantees)
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise ValueError("provider guarantees must be unique and sorted")
    payload = {"providerGuarantees": list(values)}
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class EffectDeclarationBinding(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    effect_name: str = Field(alias="effectName", min_length=1)
    effect_class: EffectClass = Field(alias="effectClass")
    resource_semantics: ResourceSemantics = Field(alias="resourceSemantics")
    effect_declaration_digest: str | None = Field(
        default=None,
        alias="effectDeclarationDigest",
    )
    handler_digest: str = Field(alias="handlerDigest")
    normalizer_digest: str = Field(alias="normalizerDigest")
    resource_deriver_digest: str = Field(alias="resourceDeriverDigest")
    executor_digest: str = Field(alias="executorDigest")
    recovery_adapter_digest: str = Field(alias="recoveryAdapterDigest")
    provider_guarantees_digest: str = Field(alias="providerGuaranteesDigest")
    provider_guarantees: tuple[ProviderGuarantee, ...] = Field(
        alias="providerGuarantees",
        min_length=1,
    )
    idempotency_capability: IdempotencyCapability = Field(alias="idempotencyCapability")
    recovery_strategy: RecoveryStrategy = Field(alias="recoveryStrategy")

    @field_validator("effect_name", mode="before")
    @classmethod
    def _require_exact_effect_name(cls, value: object) -> object:
        if type(value) is not str:
            raise ValueError("effectName must be an exact string")
        return value

    @field_validator("provider_guarantees", mode="before")
    @classmethod
    def _require_ordered_provider_guarantees(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("providerGuarantees must use an ordered list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_provider_guarantees(self) -> Self:
        expected_workspace_semantics = {
            EffectClass.WORKSPACE_READ: ResourceSemantics.READ_ONLY,
            EffectClass.WORKSPACE_WRITE: ResourceSemantics.WORKSPACE_TRANSACTION,
            EffectClass.WORKSPACE_DELETE: ResourceSemantics.WORKSPACE_TRANSACTION,
        }
        expected_semantics = expected_workspace_semantics.get(self.effect_class)
        if expected_semantics is not None and self.resource_semantics is not expected_semantics:
            raise ValueError("workspace effectClass does not match its required resourceSemantics")
        read_only_effects = {
            EffectClass.WORKSPACE_READ,
            EffectClass.NETWORK_READ,
        }
        if (
            self.resource_semantics is ResourceSemantics.READ_ONLY
            and self.effect_class not in read_only_effects
        ):
            raise ValueError("mutating effectClass cannot claim read_only resource semantics")
        if (
            self.resource_semantics is ResourceSemantics.WORKSPACE_TRANSACTION
            and self.effect_class not in {EffectClass.WORKSPACE_WRITE, EffectClass.WORKSPACE_DELETE}
        ):
            raise ValueError("workspace effectClass does not match workspace_transaction semantics")
        if self.provider_guarantees_digest != canonical_provider_guarantees_digest(
            self.provider_guarantees
        ):
            raise ValueError("providerGuaranteesDigest does not match providerGuarantees")
        if (
            ProviderGuarantee.NONE in self.provider_guarantees
            and len(self.provider_guarantees) != 1
        ):
            raise ValueError("provider guarantee none must be the only guarantee")
        if self.resource_semantics is ResourceSemantics.WORKSPACE_TRANSACTION:
            if (
                self.idempotency_capability is not IdempotencyCapability.LOCAL_GENERATION_CAS
                or self.recovery_strategy is not RecoveryStrategy.WORKSPACE_TRANSACTION
                or self.provider_guarantees != (ProviderGuarantee.LOCAL_ATOMIC,)
            ):
                raise ValueError(
                    "workspace_transaction requires local_generation_cas, "
                    "workspace_transaction recovery, and local_atomic guarantee"
                )
        if self.recovery_strategy is RecoveryStrategy.PROVIDER_RECONCILIATION:
            if ProviderGuarantee.RECONCILABLE not in self.provider_guarantees:
                raise ValueError("provider_reconciliation requires a reconcilable guarantee")
            if self.resource_semantics is not ResourceSemantics.REMOTE_EFFECT:
                raise ValueError("provider_reconciliation is valid only for remote_effect")
        if self.idempotency_capability is IdempotencyCapability.PROVIDER_IDEMPOTENCY_KEY and not {
            ProviderGuarantee.IDEMPOTENT_REPLAY,
            ProviderGuarantee.AT_MOST_ONCE,
        }.intersection(self.provider_guarantees):
            raise ValueError(
                "provider_idempotency_key requires an idempotent or at-most-once guarantee"
            )
        if (
            self.idempotency_capability is IdempotencyCapability.RECONCILIATION_ONLY
            and ProviderGuarantee.RECONCILABLE not in self.provider_guarantees
        ):
            raise ValueError("reconciliation_only requires a reconcilable provider guarantee")
        if self.recovery_strategy is RecoveryStrategy.READ_ONLY_REPLAY and (
            self.resource_semantics is not ResourceSemantics.READ_ONLY
        ):
            raise ValueError("read_only_replay requires read_only resource semantics")
        expected_digest = _canonical_model_digest(
            self,
            exclude=frozenset({"effect_declaration_digest"}),
        )
        if (
            self.effect_declaration_digest is not None
            and self.effect_declaration_digest != expected_digest
        ):
            raise ValueError("effectDeclarationDigest does not match the declaration")
        object.__setattr__(self, "effect_declaration_digest", expected_digest)
        return self


def canonical_effect_declaration_digest(declaration: EffectDeclarationBinding) -> str:
    if type(declaration) is not EffectDeclarationBinding:
        raise TypeError("declaration must be an exact EffectDeclarationBinding")
    validated = EffectDeclarationBinding.model_validate(declaration)
    assert validated.effect_declaration_digest is not None
    return validated.effect_declaration_digest


class NormalizedInputDraft(EnvelopeModel):
    """Normalizer output containing no actor, policy, task, or authority fields."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    effect_declaration_digest: str = Field(alias="effectDeclarationDigest")
    normalized_input_digest: str = Field(alias="normalizedInputDigest")
    normalized_payload_ref: str = Field(alias="normalizedPayloadRef", min_length=1)
    read_set: tuple[str, ...] = Field(alias="readSet")
    absence_set: tuple[str, ...] = Field(alias="absenceSet")
    write_set: tuple[str, ...] = Field(alias="writeSet")
    egress_set: tuple[str, ...] = Field(alias="egressSet")
    read_set_digest: str = Field(alias="readSetDigest")
    absence_set_digest: str = Field(alias="absenceSetDigest")
    write_set_digest: str = Field(alias="writeSetDigest")
    egress_set_digest: str = Field(alias="egressSetDigest")
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )
    idempotency_key_digest: str = Field(alias="idempotencyKeyDigest")

    @model_validator(mode="after")
    def _validate_normalized_input_bindings(self) -> Self:
        if self.normalized_payload_ref != (
            f"authority-input-payload://{self.normalized_input_digest}"
        ):
            raise ValueError("normalizedPayloadRef must bind normalizedInputDigest")
        for field_name in ("read_set", "absence_set", "write_set", "egress_set"):
            values = getattr(self, field_name)
            require_ref = (
                require_canonical_http_resource_ref
                if field_name == "egress_set"
                else require_canonical_workspace_resource_ref
            )
            for resource_ref in values:
                require_ref(resource_ref)
            if values != tuple(sorted(values)) or len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be unique and canonically sorted")
            if getattr(self, f"{field_name}_digest") != canonical_resource_refs_digest(values):
                raise ValueError(f"{field_name}_digest does not match resource set")
        if set(self.read_set).intersection(self.absence_set):
            raise ValueError("readSet and absenceSet must be disjoint")
        if not set(self.write_set).issubset({*self.read_set, *self.absence_set}):
            raise ValueError("every writeSet target requires a read or absence precondition")
        return self


class NormalizedInputSnapshot(NormalizedInputDraft):
    snapshot_ref: str = Field(alias="snapshotRef", min_length=1)
    normalizer_digest: str = Field(alias="normalizerDigest")
    resource_deriver_digest: str = Field(alias="resourceDeriverDigest")
    stored_at: datetime = Field(alias="storedAt")
    compare_version: int = Field(alias="compareVersion", ge=1, strict=True)

    @model_validator(mode="after")
    def _validate_snapshot_ref(self) -> Self:
        if self.snapshot_ref != f"authority-input://{self.normalized_input_digest}":
            raise ValueError("snapshotRef must bind normalizedInputDigest")
        return self


class ActionProposal(EnvelopeModel):
    schema_id: Literal["magi.action_proposal.v1"] = Field(
        default="magi.action_proposal.v1",
        alias="schemaId",
    )
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    actor_id: str = Field(alias="actorId", min_length=1)
    identity_digest: str = Field(alias="identityDigest")
    policy_digest: str = Field(alias="policyDigest")
    session_id: str = Field(alias="sessionId", min_length=1)
    turn_id: str = Field(alias="turnId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    declaration: EffectDeclarationBinding
    capabilities: tuple[AuthorityCapability, ...] = Field(min_length=1)
    normalized_input_digest: str = Field(alias="normalizedInputDigest")
    normalized_request_snapshot_ref: str = Field(
        alias="normalizedRequestSnapshotRef",
        min_length=1,
    )
    read_set: tuple[str, ...] = Field(alias="readSet")
    absence_set: tuple[str, ...] = Field(alias="absenceSet")
    write_set: tuple[str, ...] = Field(alias="writeSet")
    egress_set: tuple[str, ...] = Field(alias="egressSet")
    read_set_digest: str = Field(alias="readSetDigest")
    absence_set_digest: str = Field(alias="absenceSetDigest")
    write_set_digest: str = Field(alias="writeSetDigest")
    egress_set_digest: str = Field(alias="egressSetDigest")
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )
    idempotency_key_digest: str = Field(alias="idempotencyKeyDigest")
    evidence_obligations: tuple[EvidenceKind, ...] = Field(alias="evidenceObligations")
    compensates_action_id: str | None = Field(default=None, alias="compensatesActionId")

    @field_validator(
        "capabilities",
        "read_set",
        "absence_set",
        "write_set",
        "egress_set",
        "evidence_obligations",
        mode="before",
    )
    @classmethod
    def _require_ordered_sequences(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if type(value) not in (list, tuple):
            raise ValueError(f"{info.field_name} must use an ordered list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_derived_bindings(self) -> Self:
        expected_snapshot_ref = f"authority-input://{self.normalized_input_digest}"
        if self.normalized_request_snapshot_ref != expected_snapshot_ref:
            raise ValueError("normalizedRequestSnapshotRef must bind normalizedInputDigest")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate capabilities are not allowed")
        capability_keys = tuple(
            _canonical_json(capability.model_dump(by_alias=True, mode="json"))
            for capability in self.capabilities
        )
        if capability_keys != tuple(sorted(capability_keys)):
            raise ValueError("capabilities must use canonical sorted order")
        if not any(
            capability.effect_class is self.declaration.effect_class
            for capability in self.capabilities
        ):
            raise ValueError("capabilities must include the declared effectClass")
        workspace_bindings = {
            capability.workspace_view_binding_digest
            for capability in self.capabilities
            if capability.workspace_view_binding_digest is not None
        }
        if workspace_bindings and workspace_bindings != {self.workspace_view_binding_digest}:
            raise ValueError("workspaceViewBindingDigest must match every workspace capability")
        if not workspace_bindings and self.workspace_view_binding_digest is not None:
            raise ValueError("workspaceViewBindingDigest requires a workspace-bound capability")
        workspace_effects = {
            EffectClass.WORKSPACE_READ,
            EffectClass.WORKSPACE_WRITE,
            EffectClass.WORKSPACE_DELETE,
        }
        for capability in self.capabilities:
            if (
                capability.effect_class in workspace_effects
                or capability.resource_ref.casefold().startswith("workspace://")
            ):
                require_canonical_workspace_resource_ref(capability.resource_ref)
            elif capability.resource_ref.casefold().startswith(("http://", "https://")):
                require_canonical_http_resource_ref(capability.resource_ref)
            for network_ref in capability.network_refs:
                if network_ref.casefold().startswith(("http://", "https://")):
                    require_canonical_http_resource_ref(network_ref)
        for sequence_name in ("read_set", "absence_set", "write_set", "egress_set"):
            sequence = getattr(self, sequence_name)
            require_ref = (
                require_canonical_http_resource_ref
                if sequence_name == "egress_set"
                else require_canonical_workspace_resource_ref
            )
            for resource_ref in sequence:
                require_ref(resource_ref)
            if len(sequence) != len(set(sequence)):
                raise ValueError(f"{sequence_name} contains duplicate resource refs")
            if sequence != tuple(sorted(sequence)):
                raise ValueError(f"{sequence_name} must use canonical sorted order")
            digest_name = f"{sequence_name}_digest"
            if getattr(self, digest_name) != canonical_resource_refs_digest(sequence):
                alias = type(self).model_fields[digest_name].alias or digest_name
                raise ValueError(f"{alias} does not match its canonical resource set")
        if set(self.read_set).intersection(self.absence_set):
            raise ValueError("readSet and absenceSet must be disjoint")
        if not set(self.write_set).issubset({*self.read_set, *self.absence_set}):
            raise ValueError("every writeSet target requires a read or absence precondition")
        if len(self.evidence_obligations) != len(set(self.evidence_obligations)):
            raise ValueError("evidenceObligations must be unique")
        if tuple(item.value for item in self.evidence_obligations) != tuple(
            sorted(item.value for item in self.evidence_obligations)
        ):
            raise ValueError("evidenceObligations must use canonical sorted order")
        mutating_effects = set(EffectClass) - {
            EffectClass.WORKSPACE_READ,
            EffectClass.NETWORK_READ,
        }
        if (
            self.declaration.effect_class in mutating_effects
            and EvidenceKind.ACTION_RECEIPT not in self.evidence_obligations
        ):
            raise ValueError("mutating actions require an action_receipt evidence obligation")
        if (
            self.declaration.effect_class
            in {
                EffectClass.WORKSPACE_WRITE,
                EffectClass.WORKSPACE_DELETE,
            }
            and EvidenceKind.WORKSPACE_POSTCONDITION not in self.evidence_obligations
        ):
            raise ValueError(
                "workspace mutations require a workspace_postcondition evidence obligation"
            )

        read_capability_refs = tuple(
            capability.resource_ref
            for capability in self.capabilities
            if capability.effect_class in workspace_effects
        )
        write_effects = (
            {self.declaration.effect_class}
            if self.declaration.effect_class
            in {
                EffectClass.WORKSPACE_WRITE,
                EffectClass.WORKSPACE_DELETE,
            }
            else {
                EffectClass.WORKSPACE_WRITE,
                EffectClass.WORKSPACE_DELETE,
            }
        )
        write_capability_refs = tuple(
            capability.resource_ref
            for capability in self.capabilities
            if capability.effect_class in write_effects
        )
        if (*self.read_set, *self.absence_set) and any(
            not any(_resource_scope_covers(grant, resource_ref) for grant in read_capability_refs)
            for resource_ref in (*self.read_set, *self.absence_set)
        ):
            raise ValueError("workspace read/absence sets exceed declared capability resources")
        if self.write_set and any(
            not any(_resource_scope_covers(grant, resource_ref) for grant in write_capability_refs)
            for resource_ref in self.write_set
        ):
            raise ValueError("workspace write set exceeds declared capability resources")
        network_capability_refs = tuple(
            network_ref
            for capability in self.capabilities
            for network_ref in capability.network_refs
        )
        if self.egress_set and any(
            not any(
                _resource_scope_covers(grant, resource_ref) for grant in network_capability_refs
            )
            for resource_ref in self.egress_set
        ):
            raise ValueError("egressSet exceeds declared capability networkRefs")
        return self


class ActionIntent(ActionProposal):
    schema_id: Literal["magi.action_intent.v1"] = Field(  # type: ignore[assignment]
        default="magi.action_intent.v1",
        alias="schemaId",
    )
    admission_sequence: int = Field(alias="admissionSequence", ge=1, strict=True)


def canonical_action_proposal_digest(proposal: ActionProposal) -> str:
    if type(proposal) is not ActionProposal:
        raise TypeError("proposal must be an exact ActionProposal")
    return _canonical_model_digest(ActionProposal.model_validate(proposal))


def canonical_action_intent_digest(intent: ActionIntent) -> str:
    if type(intent) is not ActionIntent:
        raise TypeError("intent must be an exact ActionIntent")
    return _canonical_model_digest(ActionIntent.model_validate(intent))


def canonical_action_identity_digest(action: ActionProposal | ActionIntent) -> str:
    """Digest the immutable proposal portion shared by a proposal and admitted intent."""

    if type(action) not in (ActionProposal, ActionIntent):
        raise TypeError("action must be an exact ActionProposal or ActionIntent")
    validated = type(action).model_validate(action)
    proposal_payload = {
        (field_info.alias or field_name): getattr(validated, field_name)
        for field_name, field_info in ActionProposal.model_fields.items()
    }
    proposal_payload["schemaId"] = "magi.action_proposal.v1"
    proposal = ActionProposal.model_validate(proposal_payload)
    return canonical_action_proposal_digest(proposal)


def validate_same_action_identity(
    existing: ActionIntent,
    candidate: ActionProposal,
) -> ActionProposal:
    """Reject logical-action equivocation before a duplicate admission is evaluated."""

    if type(existing) is not ActionIntent:
        raise TypeError("existing must be an exact ActionIntent")
    if type(candidate) is not ActionProposal:
        raise TypeError("candidate must be an exact ActionProposal")
    validated_existing = ActionIntent.model_validate(existing)
    validated_candidate = ActionProposal.model_validate(candidate)
    if validated_existing.action_id != validated_candidate.action_id:
        raise ValueError("actionId does not identify the existing logical action")
    if canonical_action_identity_digest(validated_existing) != canonical_action_identity_digest(
        validated_candidate
    ):
        raise ValueError("same actionId cannot carry a different action digest")
    return validated_candidate


def validate_action_proposal_input_snapshot(
    proposal: ActionProposal,
    snapshot: NormalizedInputSnapshot,
) -> ActionProposal:
    """Bind trusted proposal context to the exact persisted normalizer output."""

    if type(proposal) is not ActionProposal:
        raise TypeError("proposal must be an exact ActionProposal")
    if type(snapshot) is not NormalizedInputSnapshot:
        raise TypeError("snapshot must be an exact NormalizedInputSnapshot")
    validated_proposal = ActionProposal.model_validate(proposal)
    validated_snapshot = NormalizedInputSnapshot.model_validate(snapshot)
    bindings = (
        (
            "effectDeclarationDigest",
            validated_snapshot.effect_declaration_digest,
            validated_proposal.declaration.effect_declaration_digest,
        ),
        (
            "normalizerDigest",
            validated_snapshot.normalizer_digest,
            validated_proposal.declaration.normalizer_digest,
        ),
        (
            "resourceDeriverDigest",
            validated_snapshot.resource_deriver_digest,
            validated_proposal.declaration.resource_deriver_digest,
        ),
        (
            "normalizedInputDigest",
            validated_snapshot.normalized_input_digest,
            validated_proposal.normalized_input_digest,
        ),
        (
            "normalizedRequestSnapshotRef",
            validated_snapshot.snapshot_ref,
            validated_proposal.normalized_request_snapshot_ref,
        ),
        ("readSet", validated_snapshot.read_set, validated_proposal.read_set),
        ("absenceSet", validated_snapshot.absence_set, validated_proposal.absence_set),
        ("writeSet", validated_snapshot.write_set, validated_proposal.write_set),
        ("egressSet", validated_snapshot.egress_set, validated_proposal.egress_set),
        (
            "workspaceViewBindingDigest",
            validated_snapshot.workspace_view_binding_digest,
            validated_proposal.workspace_view_binding_digest,
        ),
        (
            "idempotencyKeyDigest",
            validated_snapshot.idempotency_key_digest,
            validated_proposal.idempotency_key_digest,
        ),
    )
    for alias, observed, expected in bindings:
        if observed != expected:
            raise ValueError(f"ActionProposal {alias} does not match normalized input snapshot")
    return validated_proposal


class BackendObservation(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_intent_digest: str = Field(alias="actionIntentDigest")
    request_digest: str = Field(alias="requestDigest")
    authority_digest: str = Field(alias="authorityDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    executor_id: str = Field(alias="executorId", min_length=1)
    executor_version: str = Field(alias="executorVersion", min_length=1)
    sandbox_profile_digest: str = Field(alias="sandboxProfileDigest")
    provider_id: str | None = Field(default=None, alias="providerId", min_length=1)
    provider_version: str | None = Field(default=None, alias="providerVersion", min_length=1)
    provider_capabilities_digest: str | None = Field(
        default=None,
        alias="providerCapabilitiesDigest",
    )
    attempt_kind: AttemptKind = Field(alias="attemptKind")
    source_attempt_id: str | None = Field(default=None, alias="sourceAttemptId", min_length=1)
    reconciles_attempt_id: str | None = Field(
        default=None,
        alias="reconcilesAttemptId",
        min_length=1,
    )
    effect_may_have_started: bool = Field(alias="effectMayHaveStarted", strict=True)
    observed_outcome: ObservationOutcome = Field(alias="observedOutcome")
    transmission_state: TransmissionState = Field(alias="transmissionState")
    provider_request_id_digest: str | None = Field(
        default=None,
        alias="providerRequestIdDigest",
    )
    observed_effect_refs: tuple[str, ...] = Field(alias="observedEffectRefs")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    observation_digest: str | None = Field(default=None, alias="observationDigest")
    process_exit_code: int | None = Field(default=None, alias="processExitCode", strict=True)
    stdout_digest: str | None = Field(default=None, alias="stdoutDigest")
    stderr_digest: str | None = Field(default=None, alias="stderrDigest")
    output_truncated: bool = Field(default=False, alias="outputTruncated", strict=True)
    private_workspace_diff_digest: str | None = Field(
        default=None,
        alias="privateWorkspaceDiffDigest",
    )
    workspace_publication_digest: str | None = Field(
        default=None,
        alias="workspacePublicationDigest",
    )
    provider_receipt_digest: str | None = Field(
        default=None,
        alias="providerReceiptDigest",
    )

    @field_validator("observed_effect_refs", "reason_codes", mode="before")
    @classmethod
    def _require_ordered_observation_sequences(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("observation sequences must use an ordered list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_provider_reconciliation_and_digest(self) -> Self:
        provider_fields = (
            self.provider_id,
            self.provider_version,
            self.provider_capabilities_digest,
        )
        if any(value is not None for value in provider_fields) and not all(
            value is not None for value in provider_fields
        ):
            raise ValueError(
                "providerId, providerVersion, and providerCapabilitiesDigest are all-or-none"
            )
        if self.attempt_kind is AttemptKind.RECONCILIATION:
            if self.source_attempt_id is None or self.reconciles_attempt_id is None:
                raise ValueError(
                    "reconciliation attempts require sourceAttemptId and reconcilesAttemptId"
                )
            if self.source_attempt_id != self.reconciles_attempt_id:
                raise ValueError(
                    "sourceAttemptId must equal reconcilesAttemptId for reconciliation"
                )
        elif self.source_attempt_id is not None or self.reconciles_attempt_id is not None:
            raise ValueError("execution attempts cannot claim reconciliation lineage")
        if self.provider_id is None and self.provider_request_id_digest is not None:
            raise ValueError("providerRequestIdDigest requires provider identity")
        if self.provider_id is None:
            if self.transmission_state is not TransmissionState.PROVEN_NOT_SENT:
                raise ValueError("non-provider observations must prove that no request was sent")
            if self.provider_receipt_digest is not None:
                raise ValueError("providerReceiptDigest requires provider identity")
        elif (
            self.transmission_state is not TransmissionState.PROVEN_NOT_SENT
            and self.provider_request_id_digest is None
        ):
            raise ValueError(
                "providerRequestIdDigest is required once remote transmission is possible"
            )
        if self.observed_outcome is ObservationOutcome.ABORTED and (
            self.effect_may_have_started
            or self.observed_effect_refs
            or self.provider_receipt_digest is not None
        ):
            raise ValueError(
                "aborted outcome proves no effect started and carries no effect evidence"
            )
        if self.provider_id is not None:
            if self.observed_outcome is ObservationOutcome.COMMITTED:
                if self.transmission_state is not TransmissionState.ACCEPTED:
                    raise ValueError("committed provider outcome requires accepted transmission")
                if self.provider_receipt_digest is None:
                    raise ValueError("committed provider outcome requires providerReceiptDigest")
            elif self.observed_outcome is ObservationOutcome.ABORTED:
                if self.transmission_state not in {
                    TransmissionState.PROVEN_NOT_SENT,
                    TransmissionState.REJECTED,
                }:
                    raise ValueError(
                        "aborted provider outcome requires proven-not-sent or rejected"
                    )
            elif self.observed_outcome is ObservationOutcome.UNKNOWN:
                if self.transmission_state is not TransmissionState.MAY_HAVE_SENT:
                    raise ValueError("unknown provider outcome requires may-have-sent transmission")
                if self.provider_receipt_digest is not None:
                    raise ValueError("unknown provider outcome cannot claim a provider receipt")
            elif self.transmission_state is not TransmissionState.PARTIAL:
                raise ValueError("partial provider outcome requires partial transmission state")
        if self.observed_outcome in {
            ObservationOutcome.COMMITTED,
            ObservationOutcome.PARTIAL,
        }:
            if not self.effect_may_have_started or not self.observed_effect_refs:
                raise ValueError("committed or partial outcome requires observed effect references")
        if self.observed_outcome is ObservationOutcome.UNKNOWN and not self.effect_may_have_started:
            raise ValueError("unknown outcome requires possible effect start")
        if (
            self.workspace_publication_digest is not None
            and self.observed_outcome is not ObservationOutcome.COMMITTED
        ):
            raise ValueError("workspacePublicationDigest requires a committed publication")
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"observation_digest"}),
        )
        if self.observation_digest is not None and self.observation_digest != expected:
            raise ValueError("observationDigest does not match the observation")
        object.__setattr__(self, "observation_digest", expected)
        return self


def canonical_backend_observation_digest(observation: BackendObservation) -> str:
    if type(observation) is not BackendObservation:
        raise TypeError("observation must be an exact BackendObservation")
    validated = BackendObservation.model_validate(observation)
    return _canonical_model_digest(
        validated,
        exclude=frozenset({"observation_digest"}),
    )


class ActionReceipt(EnvelopeModel):
    schema_id: Literal["magi.action_receipt.v1"] = Field(
        default="magi.action_receipt.v1",
        alias="schemaId",
    )
    observation: BackendObservation
    state: ActionState
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    state_root_before: str | None = Field(default=None, alias="stateRootBefore")
    state_root_after: str | None = Field(default=None, alias="stateRootAfter")

    @model_validator(mode="after")
    def _validate_receipt_state(self) -> Self:
        if self.state not in {
            ActionState.COMMITTED,
            ActionState.ABORTED,
            ActionState.PARTIAL,
            ActionState.UNKNOWN,
            ActionState.VERIFIED,
        }:
            raise ValueError("action receipt requires a physical terminal observation state")
        if self.state is ActionState.VERIFIED:
            if self.observation.observed_outcome is not ObservationOutcome.COMMITTED:
                raise ValueError("verified receipts require a committed physical observation")
        elif self.observation.observed_outcome.value != self.state.value:
            raise ValueError("receipt state must equal the backend observed outcome")
        return self


class ActionResolution(EnvelopeModel):
    schema_id: Literal["magi.action_resolution.v1"] = Field(
        default="magi.action_resolution.v1",
        alias="schemaId",
    )
    action_id: str = Field(alias="actionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    source_attempt_ids: tuple[str, ...] = Field(alias="sourceAttemptIds", min_length=1)
    resolution_attempt_id: str | None = Field(default=None, alias="resolutionAttemptId")
    logical_state: ActionState = Field(alias="logicalState")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @model_validator(mode="after")
    def _logical_state_is_terminal_and_known(self) -> Self:
        if self.logical_state not in {
            ActionState.DENIED,
            ActionState.ABORTED,
            ActionState.PARTIAL,
            ActionState.UNKNOWN,
            ActionState.VERIFIED,
        }:
            raise ValueError("logical resolution requires one known terminal logical action state")
        if len(self.source_attempt_ids) != len(set(self.source_attempt_ids)):
            raise ValueError("sourceAttemptIds must be unique")
        if self.resolution_attempt_id in self.source_attempt_ids:
            raise ValueError("resolutionAttemptId must differ from every sourceAttemptId")
        return self


class JournalEventDraft(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    event_id: str = Field(alias="eventId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    event_type: str = Field(alias="eventType", min_length=1)
    action_id: str | None = Field(default=None, alias="actionId", min_length=1)
    attempt_id: str | None = Field(default=None, alias="attemptId", min_length=1)
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    admission_sequence: int = Field(alias="admissionSequence", ge=0, strict=True)
    authority_contract_id: str | None = Field(
        default=None,
        alias="authorityContractId",
        min_length=1,
    )
    request_digest: str = Field(alias="requestDigest")
    idempotency_key_digest: str = Field(alias="idempotencyKeyDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    actor_id: str = Field(alias="actorId", min_length=1)
    policy_digest: str = Field(alias="policyDigest")
    causation_id: str = Field(alias="causationId", min_length=1)
    correlation_id: str = Field(alias="correlationId", min_length=1)
    identity_digest: str = Field(alias="identityDigest")
    payload_digest: str = Field(alias="payloadDigest")
    payload_json: str = Field(alias="payloadJson")

    @model_validator(mode="after")
    def _validate_payload(self) -> Self:
        parsed = _strict_json_loads(self.payload_json)
        _reject_sensitive_keys(parsed)
        canonical = _canonical_json(parsed)
        if self.payload_json != canonical:
            raise ValueError("journal payload JSON is not canonical")
        expected = "sha256:" + sha256(self.payload_json.encode("utf-8")).hexdigest()
        if self.payload_digest != expected:
            raise ValueError("payloadDigest does not match canonical payload JSON")
        return self


_RESERVED_LIFECYCLE_EVENT_PREFIXES: tuple[str, ...] = (
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
)


class GenericJournalEventDraft(JournalEventDraft):
    """Only draft accepted by the generic append boundary."""

    @model_validator(mode="after")
    def _reject_reserved_lifecycle_event_type(self) -> Self:
        if self.event_type.startswith(_RESERVED_LIFECYCLE_EVENT_PREFIXES):
            raise ValueError(
                "reserved lifecycle event types require a named JournalPort unit of work"
            )
        return self


class JournalEvent(JournalEventDraft):
    sequence: int = Field(ge=1, strict=True)
    previous_hash: str = Field(alias="previousHash")
    event_hash: str = Field(alias="eventHash")
    row_checksum: str = Field(alias="rowChecksum")
    created_at: datetime = Field(alias="createdAt")


def _require_direct_event_successor(
    first: JournalEvent,
    second: JournalEvent,
    *,
    first_name: str,
    second_name: str,
) -> None:
    if second.sequence != first.sequence + 1:
        raise ValueError(f"{second_name} must directly follow {first_name}")
    if second.previous_hash != first.event_hash:
        raise ValueError(f"{second_name}.previousHash must equal {first_name}.eventHash")
    if second.event_id == first.event_id or second.event_hash == first.event_hash:
        raise ValueError(f"{first_name} and {second_name} must be distinct events")
    if second.created_at < first.created_at:
        raise ValueError(f"{second_name}.createdAt cannot precede {first_name}.createdAt")


def _draft_lifecycle_journal_event(
    *,
    event_id: str,
    partition_id: str,
    event_type: str,
    task_contract_id: str,
    task_version: int,
    task_contract_digest: str,
    completion_epoch_id: str,
    admission_sequence: int,
    request_digest: str,
    idempotency_key_digest: str,
    fencing_token: int,
    actor_id: str,
    policy_digest: str,
    causation_id: str,
    correlation_id: str,
    identity_digest: str,
    payload: Mapping[str, object],
    action_id: str | None = None,
    attempt_id: str | None = None,
    authority_contract_id: str | None = None,
) -> JournalEventDraft:
    if not isinstance(payload, Mapping):
        raise TypeError("journal payload must be a mapping")
    _reject_sensitive_keys(payload)
    payload_json = _canonical_json(payload)
    return JournalEventDraft(
        eventId=event_id,
        partitionId=partition_id,
        eventType=event_type,
        actionId=action_id,
        attemptId=attempt_id,
        taskContractId=task_contract_id,
        taskVersion=task_version,
        taskContractDigest=task_contract_digest,
        completionEpochId=completion_epoch_id,
        admissionSequence=admission_sequence,
        authorityContractId=authority_contract_id,
        requestDigest=request_digest,
        idempotencyKeyDigest=idempotency_key_digest,
        fencingToken=fencing_token,
        actorId=actor_id,
        policyDigest=policy_digest,
        causationId=causation_id,
        correlationId=correlation_id,
        identityDigest=identity_digest,
        payloadDigest="sha256:" + sha256(payload_json.encode("utf-8")).hexdigest(),
        payloadJson=payload_json,
    )


def draft_journal_event(
    *,
    event_id: str,
    partition_id: str,
    event_type: str,
    task_contract_id: str,
    task_version: int,
    task_contract_digest: str,
    completion_epoch_id: str,
    admission_sequence: int,
    request_digest: str,
    idempotency_key_digest: str,
    fencing_token: int,
    actor_id: str,
    policy_digest: str,
    causation_id: str,
    correlation_id: str,
    identity_digest: str,
    payload: Mapping[str, object],
    action_id: str | None = None,
    attempt_id: str | None = None,
    authority_contract_id: str | None = None,
) -> GenericJournalEventDraft:
    """Draft one non-reserved event for the generic journal append boundary."""

    lifecycle_draft = _draft_lifecycle_journal_event(
        event_id=event_id,
        partition_id=partition_id,
        event_type=event_type,
        action_id=action_id,
        attempt_id=attempt_id,
        task_contract_id=task_contract_id,
        task_version=task_version,
        task_contract_digest=task_contract_digest,
        completion_epoch_id=completion_epoch_id,
        admission_sequence=admission_sequence,
        authority_contract_id=authority_contract_id,
        request_digest=request_digest,
        idempotency_key_digest=idempotency_key_digest,
        fencing_token=fencing_token,
        actor_id=actor_id,
        policy_digest=policy_digest,
        causation_id=causation_id,
        correlation_id=correlation_id,
        identity_digest=identity_digest,
        payload=payload,
    )
    return GenericJournalEventDraft.model_validate(
        lifecycle_draft.model_dump(by_alias=True, mode="json")
    )


class ResponseClaim(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    claim_id: str = Field(alias="claimId", min_length=1)
    claim_class: Literal[
        "execution",
        "result",
        "artifact",
        "factual",
        "limitation",
    ] = Field(alias="claimClass")
    text_digest: str = Field(alias="textDigest")
    codepoint_start: int = Field(alias="codepointStart", ge=0, strict=True)
    codepoint_end: int = Field(alias="codepointEnd", ge=0, strict=True)
    utf8_start: int = Field(alias="utf8Start", ge=0, strict=True)
    utf8_end: int = Field(alias="utf8End", ge=0, strict=True)
    evidence_ids: tuple[str, ...] = Field(alias="evidenceIds")

    @field_validator("evidence_ids")
    @classmethod
    def _reject_empty_evidence_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _reject_empty_string_items(value, field_name="evidenceIds")

    @model_validator(mode="after")
    def _require_nonempty_slice(self) -> Self:
        if self.codepoint_start >= self.codepoint_end:
            raise ValueError("response claim code-point span must be nonempty")
        if self.utf8_start >= self.utf8_end:
            raise ValueError("response claim UTF-8 span must be nonempty")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("response claim evidence IDs must be unique")
        if self.claim_class != "limitation" and not self.evidence_ids:
            raise ValueError("success-bearing response claims require at least one evidence ID")
        return self


class ResponseClaimManifest(EnvelopeModel):
    schema_id: Literal["magi.response_claim_manifest.v1"] = Field(
        default="magi.response_claim_manifest.v1",
        alias="schemaId",
    )
    candidate_response_digest: str = Field(alias="candidateResponseDigest")
    segments: tuple[ResponseClaim, ...]

    @field_validator("segments", mode="before")
    @classmethod
    def _require_ordered_segments(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("segments must use an ordered list or tuple")
        return value


def canonical_response_claim_manifest_digest(manifest: ResponseClaimManifest) -> str:
    if type(manifest) is not ResponseClaimManifest:
        raise TypeError("manifest must be an exact ResponseClaimManifest")
    return _canonical_model_digest(ResponseClaimManifest.model_validate(manifest))


class DependencyHealth(EnvelopeModel):
    schema_id: Literal["magi.dependency_health.v1"] = Field(
        default="magi.dependency_health.v1",
        alias="schemaId",
    )
    dependency_id: str = Field(alias="dependencyId", min_length=1)
    status: DependencyStatus
    producer_version: str | None = Field(default=None, alias="producerVersion", min_length=1)
    schema_version: str | None = Field(default=None, alias="schemaVersion", min_length=1)
    producer_alive: bool = Field(alias="producerAlive", strict=True)
    invocation_evidence_id: str | None = Field(
        default=None,
        alias="invocationEvidenceId",
        min_length=1,
    )
    invocation_evidence_digest: str | None = Field(
        default=None,
        alias="invocationEvidenceDigest",
    )
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    state_root: str = Field(alias="stateRoot")
    observed_at: datetime = Field(alias="observedAt")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @field_validator("reason_codes")
    @classmethod
    def _reject_empty_reason_codes(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _reject_empty_string_items(value, field_name="reasonCodes")

    @model_validator(mode="after")
    def _validate_liveness_contract(self) -> Self:
        if (self.invocation_evidence_id is None) != (self.invocation_evidence_digest is None):
            raise ValueError(
                "invocationEvidenceId and invocationEvidenceDigest are both-or-neither"
            )
        if self.status is DependencyStatus.CLEAN:
            if not self.producer_alive or self.invocation_evidence_id is None:
                raise ValueError("clean dependency health requires proven invocation liveness")
            if self.producer_version is None or self.schema_version is None:
                raise ValueError("clean dependency health requires producer and schema versions")
        return self


class FinalizationRequest(EnvelopeModel):
    schema_id: Literal["magi.finalization_request.v1"] = Field(
        default="magi.finalization_request.v1",
        alias="schemaId",
    )
    finalization_id: str = Field(alias="finalizationId", min_length=1)
    finalization_request_digest: str | None = Field(
        default=None,
        alias="finalizationRequestDigest",
    )
    task_contract: TaskContractSnapshot = Field(alias="taskContract")
    task_contract_digest: str = Field(alias="taskContractDigest")
    task_contract_snapshot_ref: str = Field(alias="taskContractSnapshotRef")
    task_partition_id: str = Field(alias="taskPartitionId", min_length=1)
    state_root: str = Field(alias="stateRoot")
    evidence_root: str = Field(alias="evidenceRoot")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    barrier_admission_sequence: int = Field(
        alias="barrierAdmissionSequence",
        ge=0,
        strict=True,
    )
    dependency_health: tuple[DependencyHealth, ...] = Field(alias="dependencyHealth")
    candidate_response: str = Field(alias="candidateResponse")
    claim_manifest: ResponseClaimManifest = Field(alias="claimManifest")
    response_claim_manifest_digest: str | None = Field(
        default=None,
        alias="responseClaimManifestDigest",
    )

    @model_validator(mode="after")
    def _validate_task_and_response_bindings(self) -> Self:
        task_digest = canonical_task_contract_digest(self.task_contract)
        if self.task_contract_digest != task_digest:
            raise ValueError("taskContractDigest does not match the embedded Task Contract")
        if self.task_contract_snapshot_ref != f"authority-task://{task_digest}":
            raise ValueError("taskContractSnapshotRef does not match the Task Contract")
        if self.completion_epoch_id != self.task_contract.completion_epoch_id:
            raise ValueError("completionEpochId does not match the Task Contract")
        expected_partition = (
            f"task:{self.task_contract.task_contract_id}:{self.task_contract.version}"
        )
        if self.task_partition_id != expected_partition:
            raise ValueError("taskPartitionId does not match the Task Contract identity")
        expected_dependency_ids = tuple(
            dependency.dependency_id for dependency in self.task_contract.dependencies
        )
        observed_dependency_ids = tuple(health.dependency_id for health in self.dependency_health)
        if observed_dependency_ids != expected_dependency_ids:
            raise ValueError(
                "dependencyHealth must exactly cover Task Contract dependencies in order"
            )
        for dependency, health in zip(
            self.task_contract.dependencies,
            self.dependency_health,
            strict=True,
        ):
            if (
                health.task_contract_digest != self.task_contract_digest
                or health.completion_epoch_id != self.completion_epoch_id
                or health.state_root != self.state_root
            ):
                raise ValueError(
                    "dependencyHealth must bind the current Task Contract, epoch, and state root"
                )
            if (
                health.status is DependencyStatus.CLEAN
                and health.schema_version != dependency.required_schema
            ):
                raise ValueError("clean dependencyHealth schemaVersion is incompatible")

        candidate_bytes = self.candidate_response.encode("utf-8")
        expected_candidate_digest = "sha256:" + sha256(candidate_bytes).hexdigest()
        if self.claim_manifest.candidate_response_digest != expected_candidate_digest:
            raise ValueError("candidateResponseDigest does not match candidateResponse")
        if not self.candidate_response:
            if self.claim_manifest.segments:
                raise ValueError("an empty candidate response requires an empty manifest")
        elif not self.claim_manifest.segments:
            raise ValueError("a nonempty candidate response requires claim segments")

        expected_codepoint = 0
        expected_utf8 = 0
        claim_ids: set[str] = set()
        for segment in self.claim_manifest.segments:
            if segment.claim_id in claim_ids:
                raise ValueError("response claim IDs must be unique")
            claim_ids.add(segment.claim_id)
            if segment.codepoint_start != expected_codepoint:
                raise ValueError("response claim code-point spans contain a gap or overlap")
            if segment.utf8_start != expected_utf8:
                raise ValueError("response claim UTF-8 spans contain a gap or overlap")
            if segment.codepoint_end > len(self.candidate_response):
                raise ValueError("response claim code-point span is out of bounds")
            if segment.utf8_end > len(candidate_bytes):
                raise ValueError("response claim UTF-8 span is out of bounds")
            text = self.candidate_response[segment.codepoint_start : segment.codepoint_end]
            text_bytes = text.encode("utf-8")
            if segment.utf8_end - segment.utf8_start != len(text_bytes):
                raise ValueError("response claim UTF-8 and code-point spans disagree")
            if candidate_bytes[segment.utf8_start : segment.utf8_end] != text_bytes:
                raise ValueError("response claim UTF-8 slice does not match code-point slice")
            if segment.text_digest != "sha256:" + sha256(text_bytes).hexdigest():
                raise ValueError("response claim textDigest does not match its exact slice")
            expected_codepoint = segment.codepoint_end
            expected_utf8 = segment.utf8_end
        if expected_codepoint != len(self.candidate_response):
            raise ValueError("response claim code-point spans omit the candidate tail")
        if expected_utf8 != len(candidate_bytes):
            raise ValueError("response claim UTF-8 spans omit the candidate tail")

        manifest_digest = canonical_response_claim_manifest_digest(self.claim_manifest)
        if (
            self.response_claim_manifest_digest is not None
            and self.response_claim_manifest_digest != manifest_digest
        ):
            raise ValueError("responseClaimManifestDigest does not match claimManifest")
        object.__setattr__(self, "response_claim_manifest_digest", manifest_digest)
        request_digest = _canonical_model_digest(
            self,
            exclude=frozenset({"finalization_request_digest"}),
        )
        if (
            self.finalization_request_digest is not None
            and self.finalization_request_digest != request_digest
        ):
            raise ValueError("finalizationRequestDigest does not match the request")
        object.__setattr__(self, "finalization_request_digest", request_digest)
        return self


class NonExecutionProof(EnvelopeModel):
    """Mechanical proof that an attempt did not cross the effect boundary."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    proof_id: str = Field(alias="proofId", min_length=1)
    proof_digest: str | None = Field(default=None, alias="proofDigest")
    partition_id: str = Field(alias="partitionId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    source_attempt_id: str = Field(alias="sourceAttemptId", min_length=1)
    expected_source_state: ActionState = Field(alias="expectedSourceState")
    expected_source_version: int = Field(
        alias="expectedSourceVersion",
        ge=0,
        strict=True,
    )
    task_contract_digest: str = Field(alias="taskContractDigest")
    authority_use_recorded: bool = Field(alias="authorityUseRecorded", strict=True)
    prepared_record_recorded: bool = Field(alias="preparedRecordRecorded", strict=True)
    execution_handoff_recorded: bool = Field(
        alias="executionHandoffRecorded",
        strict=True,
    )
    provider_transmission_state: Literal[TransmissionState.PROVEN_NOT_SENT] = Field(
        alias="providerTransmissionState",
    )
    visible_effects_absent: bool = Field(alias="visibleEffectsAbsent", strict=True)
    evidence_id: str = Field(alias="evidenceId", min_length=1)
    evidence_digest: str = Field(alias="evidenceDigest")
    coverage_digest: str = Field(alias="coverageDigest")
    action_snapshot_digest: str = Field(alias="actionSnapshotDigest")
    attempt_snapshot_digest: str = Field(alias="attemptSnapshotDigest")
    journal_head_digest: str = Field(alias="journalHeadDigest")
    producer_id: str = Field(alias="producerId", min_length=1)
    producer_version: str = Field(alias="producerVersion", min_length=1)
    producer_schema_version: str = Field(
        alias="producerSchemaVersion",
        min_length=1,
    )
    producer_invocation_evidence_id: str = Field(
        alias="producerInvocationEvidenceId",
        min_length=1,
    )
    producer_invocation_evidence_digest: str = Field(
        alias="producerInvocationEvidenceDigest",
    )
    producer_alive: bool = Field(alias="producerAlive", strict=True)
    observed_at: datetime = Field(alias="observedAt")

    @model_validator(mode="after")
    def _validate_non_execution_proof(self) -> Self:
        if not self.visible_effects_absent:
            raise ValueError("non-execution proof must prove visible effects absent")
        if not self.producer_alive:
            raise ValueError("non-execution proof requires a live producer")
        record_shapes = {
            ActionState.PROPOSED: (False, False, False),
            ActionState.AUTHORIZED: (True, False, False),
            ActionState.PREPARED: (True, True, False),
            ActionState.EXECUTING: (True, True, True),
            ActionState.OBSERVED: (True, True, True),
        }
        expected_records = record_shapes.get(self.expected_source_state)
        observed_records = (
            self.authority_use_recorded,
            self.prepared_record_recorded,
            self.execution_handoff_recorded,
        )
        if expected_records is None or observed_records != expected_records:
            raise ValueError("non-execution proof durable records contradict source state")
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"proof_digest"}),
        )
        if self.proof_digest is not None and self.proof_digest != expected:
            raise ValueError("proofDigest does not match NonExecutionProof")
        object.__setattr__(self, "proof_digest", expected)
        return self


class RecoveryContext(EnvelopeModel):
    """Replay-complete input to the pure recovery reducer.

    Every Boolean is a coordinator-derived fact bound to immutable snapshot
    digests.  In particular, ``effectMayHaveStarted=False`` is not itself
    non-execution proof; an authenticated ``NonExecutionProof`` is required
    before an already prepared/executing source may be aborted.
    """

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    context_id: str = Field(alias="contextId", min_length=1)
    recovery_epoch_id: str = Field(alias="recoveryEpochId", min_length=1)
    recovery_plan_digest: str = Field(alias="recoveryPlanDigest")
    recovery_owner_id: str = Field(alias="recoveryOwnerId", min_length=1)
    recovery_lease_name: Literal["partition-recovery"] = Field(
        default="partition-recovery",
        alias="recoveryLeaseName",
    )
    partition_id: str = Field(alias="partitionId", min_length=1)
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion",
        ge=0,
        strict=True,
    )
    recovery_fencing_token: int = Field(
        alias="recoveryFencingToken",
        ge=1,
        strict=True,
    )
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_id: str = Field(alias="actionId", min_length=1)
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion",
        ge=0,
        strict=True,
    )
    source_attempt_id: str = Field(alias="sourceAttemptId", min_length=1)
    expected_source_state: ActionState = Field(alias="expectedSourceState")
    expected_source_version: int = Field(
        alias="expectedSourceVersion",
        ge=0,
        strict=True,
    )
    source_terminal: bool = Field(alias="sourceTerminal", strict=True)
    resolution_attempt_id: str | None = Field(
        default=None,
        alias="resolutionAttemptId",
        min_length=1,
    )
    pending_user_decision: bool = Field(alias="pendingUserDecision", strict=True)
    effect_may_have_started: bool = Field(alias="effectMayHaveStarted", strict=True)
    replay_safe: bool = Field(alias="replaySafe", strict=True)
    authority_valid: bool = Field(alias="authorityValid", strict=True)
    task_version_current: bool = Field(alias="taskVersionCurrent", strict=True)
    fence_current: bool = Field(alias="fenceCurrent", strict=True)
    state_root_current: bool = Field(alias="stateRootCurrent", strict=True)
    workspace_commit_state: Literal[
        "none",
        "staged",
        "decided",
        "published",
        "quarantined",
    ] = Field(alias="workspaceCommitState")
    workspace_commit_snapshot_digest: str | None = Field(
        default=None,
        alias="workspaceCommitSnapshotDigest",
    )
    projection_status: Literal["current", "lagging", "unknown"] = Field(
        alias="projectionStatus",
    )
    projection_gap_digest: str | None = Field(
        default=None,
        alias="projectionGapDigest",
    )
    integrity_status: Literal["clean", "corrupt", "unsupported_schema"] = Field(
        alias="integrityStatus",
    )
    integrity_scan_digest: str = Field(alias="integrityScanDigest")
    recovery_adapter_id: str | None = Field(
        default=None,
        alias="recoveryAdapterId",
        min_length=1,
    )
    recovery_adapter_version: str | None = Field(
        default=None,
        alias="recoveryAdapterVersion",
        min_length=1,
    )
    recovery_adapter_schema_version: str | None = Field(
        default=None,
        alias="recoveryAdapterSchemaVersion",
        min_length=1,
    )
    recovery_adapter_digest: str | None = Field(
        default=None,
        alias="recoveryAdapterDigest",
    )
    evaluated_at: datetime = Field(alias="evaluatedAt")
    action_intent: ActionIntent = Field(alias="actionIntent")
    action_intent_digest: str = Field(alias="actionIntentDigest")
    recovery_authority: AuthorityContract = Field(alias="recoveryAuthority")
    recovery_authority_digest: str = Field(alias="recoveryAuthorityDigest")
    non_execution_proof: NonExecutionProof | None = Field(
        default=None,
        alias="nonExecutionProof",
    )
    action_snapshot_digest: str = Field(alias="actionSnapshotDigest")
    attempt_snapshot_digest: str = Field(alias="attemptSnapshotDigest")
    journal_head_digest: str = Field(alias="journalHeadDigest")
    workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="workspaceViewBindingDigest",
    )
    provider_capabilities_digest: str | None = Field(
        default=None,
        alias="providerCapabilitiesDigest",
    )
    current_policy_digest: str = Field(alias="currentPolicyDigest")
    current_sandbox_profile_digest: str = Field(alias="currentSandboxProfileDigest")
    context_digest: str | None = Field(default=None, alias="contextDigest")

    @model_validator(mode="after")
    def _validate_replay_complete_context(self) -> Self:
        if any(
            ":" in value
            for value in (
                self.recovery_epoch_id,
                self.action_id,
                self.source_attempt_id,
            )
        ):
            raise ValueError("recovery CAS identity components may not contain ':'")
        if self.resolution_attempt_id == self.source_attempt_id:
            raise ValueError("resolutionAttemptId must differ from sourceAttemptId")
        physical_terminal = self.expected_source_state in {
            ActionState.DENIED,
            ActionState.COMMITTED,
            ActionState.ABORTED,
            ActionState.PARTIAL,
            ActionState.UNKNOWN,
            ActionState.VERIFIED,
        }
        if self.source_terminal is not physical_terminal:
            raise ValueError("sourceTerminal does not match expectedSourceState")
        adapter_fields = (
            self.recovery_adapter_id,
            self.recovery_adapter_version,
            self.recovery_adapter_schema_version,
            self.recovery_adapter_digest,
        )
        if any(value is not None for value in adapter_fields) and not all(
            value is not None for value in adapter_fields
        ):
            raise ValueError("recovery adapter identity fields are all-or-none")
        expected_intent_digest = canonical_action_intent_digest(self.action_intent)
        if self.action_intent_digest != expected_intent_digest:
            raise ValueError("actionIntentDigest does not match actionIntent")
        intent_bindings = (
            ("action", self.action_intent.action_id, self.action_id),
            ("source attempt", self.action_intent.attempt_id, self.source_attempt_id),
            ("partition", self.action_intent.partition_id, self.partition_id),
            ("Task Contract", self.action_intent.task_contract_digest, self.task_contract_digest),
            ("policy", self.action_intent.policy_digest, self.current_policy_digest),
            (
                "workspace view",
                self.action_intent.workspace_view_binding_digest,
                self.workspace_view_binding_digest,
            ),
        )
        for label, observed, expected_value in intent_bindings:
            if observed != expected_value:
                raise ValueError(f"actionIntent {label} does not match RecoveryContext")
        declaration_replay_safe = (
            self.action_intent.declaration.recovery_strategy is RecoveryStrategy.READ_ONLY_REPLAY
        )
        if self.replay_safe is not declaration_replay_safe:
            raise ValueError("replaySafe does not match actionIntent recoveryStrategy")
        if (
            self.recovery_adapter_digest is not None
            and self.recovery_adapter_digest
            != self.action_intent.declaration.recovery_adapter_digest
        ):
            raise ValueError("recovery adapter digest does not match actionIntent declaration")

        expected_authority_digest = canonical_authority_contract_digest(self.recovery_authority)
        if self.recovery_authority_digest != expected_authority_digest:
            raise ValueError("recoveryAuthorityDigest does not match recoveryAuthority")
        expected_authority_attempt = self.resolution_attempt_id or self.source_attempt_id
        authority_bindings: tuple[tuple[str, object, object], ...] = (
            ("principal", self.recovery_authority.principal_id, self.action_intent.actor_id),
            ("session", self.recovery_authority.session_id, self.action_intent.session_id),
            ("turn", self.recovery_authority.turn_id, self.action_intent.turn_id),
            ("action", self.recovery_authority.action_id, self.action_id),
            ("attempt", self.recovery_authority.attempt_id, expected_authority_attempt),
            (
                "partition",
                self.recovery_authority.authority_partition_id,
                self.partition_id,
            ),
            (
                "Task Contract ID",
                self.recovery_authority.task_contract_id,
                self.action_intent.task_contract_id,
            ),
            (
                "Task Contract version",
                self.recovery_authority.task_version,
                self.action_intent.task_version,
            ),
            (
                "Task Contract digest",
                self.recovery_authority.task_contract_digest,
                self.task_contract_digest,
            ),
            (
                "completion epoch",
                self.recovery_authority.completion_epoch_id,
                self.action_intent.completion_epoch_id,
            ),
            ("policy", self.recovery_authority.policy_digest, self.current_policy_digest),
            (
                "normalized request",
                self.recovery_authority.normalized_request_digest,
                self.action_intent.normalized_input_digest,
            ),
            (
                "capabilities",
                self.recovery_authority.capabilities,
                self.action_intent.capabilities,
            ),
            (
                "workspace view",
                self.recovery_authority.workspace_view_binding_digest,
                self.workspace_view_binding_digest,
            ),
            (
                "sandbox profile",
                self.recovery_authority.sandbox_profile_digest,
                self.current_sandbox_profile_digest,
            ),
            (
                "recovery fence",
                self.recovery_authority.fencing_token,
                self.recovery_fencing_token,
            ),
        )
        for binding_label, binding_observed, binding_expected in authority_bindings:
            if binding_observed != binding_expected:
                raise ValueError(
                    f"recoveryAuthority {binding_label} does not match RecoveryContext"
                )
        objectively_valid_authority = (
            self.recovery_authority.revoked_at is None
            and self.recovery_authority.expires_at > self.evaluated_at
        )
        if self.authority_valid is not objectively_valid_authority:
            raise ValueError("authorityValid does not match fresh recoveryAuthority validity")
        if self.workspace_commit_state in {"decided", "published", "quarantined"}:
            if self.workspace_commit_snapshot_digest is None:
                raise ValueError("durable workspace commit state requires its snapshot digest")
            if not self.effect_may_have_started:
                raise ValueError(
                    "durable workspace commit state proves the effect may have started"
                )
            if self.non_execution_proof is not None:
                raise ValueError(
                    "durable workspace commit state cannot coexist with non-execution proof"
                )
            if (
                self.action_intent.declaration.recovery_strategy
                is not RecoveryStrategy.WORKSPACE_TRANSACTION
            ):
                raise ValueError(
                    "durable workspace commit requires workspace_transaction recoveryStrategy"
                )
        elif self.workspace_commit_snapshot_digest is not None:
            raise ValueError("workspaceCommitSnapshotDigest requires a durable workspace commit")
        if (self.projection_status == "lagging") != (self.projection_gap_digest is not None):
            raise ValueError("projectionGapDigest is required exactly for lagging projections")
        if self.non_execution_proof is not None:
            if self.effect_may_have_started:
                raise ValueError("non-execution proof contradicts effectMayHaveStarted")
            proof_bindings = (
                ("partition", "partition_id"),
                ("action", "action_id"),
                ("source attempt", "source_attempt_id"),
                ("source state", "expected_source_state"),
                ("source version", "expected_source_version"),
                ("Task Contract", "task_contract_digest"),
                ("action snapshot", "action_snapshot_digest"),
                ("attempt snapshot", "attempt_snapshot_digest"),
                ("journal head", "journal_head_digest"),
            )
            for label, attribute in proof_bindings:
                if getattr(self.non_execution_proof, attribute) != getattr(self, attribute):
                    raise ValueError(f"non-execution proof {label} does not match")
            if self.source_terminal:
                raise ValueError(
                    "terminal physical attempts cannot acquire retroactive non-execution proof"
                )
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"context_digest"}),
        )
        if self.context_digest is not None and self.context_digest != expected:
            raise ValueError("contextDigest does not match the recovery context")
        object.__setattr__(self, "context_digest", expected)
        return self


class RecoveryDecision(EnvelopeModel):
    schema_id: Literal["magi.recovery_decision.v1"] = Field(
        default="magi.recovery_decision.v1",
        alias="schemaId",
    )
    decision_id: str = Field(alias="decisionId", min_length=1)
    decision_digest: str | None = Field(default=None, alias="decisionDigest")
    recovery_epoch_id: str = Field(alias="recoveryEpochId", min_length=1)
    recovery_plan_digest: str = Field(alias="recoveryPlanDigest")
    recovery_owner_id: str = Field(alias="recoveryOwnerId", min_length=1)
    recovery_lease_name: Literal["partition-recovery"] = Field(
        default="partition-recovery",
        alias="recoveryLeaseName",
    )
    partition_id: str = Field(alias="partitionId", min_length=1)
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion",
        ge=0,
        strict=True,
    )
    recovery_fencing_token: int = Field(alias="recoveryFencingToken", ge=1, strict=True)
    action_id: str = Field(alias="actionId", min_length=1)
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion",
        ge=0,
        strict=True,
    )
    task_contract_digest: str = Field(alias="taskContractDigest")
    source_attempt_id: str = Field(alias="sourceAttemptId", min_length=1)
    expected_source_state: ActionState = Field(alias="expectedSourceState")
    expected_source_version: int = Field(alias="expectedSourceVersion", ge=0, strict=True)
    source_terminal: bool = Field(alias="sourceTerminal", strict=True)
    terminalize_source_to: ActionState | None = Field(
        default=None,
        alias="terminalizeSourceTo",
    )
    resolution_attempt_id: str | None = Field(default=None, alias="resolutionAttemptId")
    disposition: RecoveryDisposition
    context_digest: str = Field(alias="contextDigest")
    non_execution_proof_digest: str | None = Field(
        default=None,
        alias="nonExecutionProofDigest",
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _require_ordered_reason_codes(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("reasonCodes must use an ordered list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_identity_and_digest(self) -> Self:
        if any(
            ":" in value
            for value in (
                self.recovery_epoch_id,
                self.action_id,
                self.source_attempt_id,
            )
        ):
            raise ValueError("recovery CAS identity components may not contain ':'")
        expected_id = f"recovery:{self.recovery_epoch_id}:{self.action_id}:{self.source_attempt_id}"
        if self.decision_id != expected_id:
            raise ValueError("decisionId does not match the recovery CAS identity")
        physical_terminal = self.expected_source_state in {
            ActionState.DENIED,
            ActionState.COMMITTED,
            ActionState.ABORTED,
            ActionState.PARTIAL,
            ActionState.UNKNOWN,
            ActionState.VERIFIED,
        }
        if self.source_terminal is not physical_terminal:
            raise ValueError("sourceTerminal does not match expectedSourceState")
        if self.source_terminal and self.terminalize_source_to is not None:
            raise ValueError("a terminal source attempt cannot be terminalized again")
        if not self.source_terminal:
            if self.disposition is RecoveryDisposition.QUARANTINE:
                if self.terminalize_source_to is not None:
                    raise ValueError("QUARANTINE must preserve the source attempt state")
            else:
                allowed_terminalizations: dict[ActionState, frozenset[ActionState]] = {
                    ActionState.PROPOSED: frozenset({ActionState.DENIED}),
                    ActionState.AUTHORIZED: frozenset({ActionState.ABORTED}),
                    ActionState.PREPARED: frozenset({ActionState.ABORTED, ActionState.UNKNOWN}),
                    ActionState.EXECUTING: frozenset({ActionState.ABORTED, ActionState.UNKNOWN}),
                    ActionState.OBSERVED: frozenset({ActionState.ABORTED, ActionState.UNKNOWN}),
                }
                allowed = allowed_terminalizations.get(self.expected_source_state)
                if allowed is None or self.terminalize_source_to not in allowed:
                    raise ValueError(
                        "terminalizeSourceTo is invalid for the nonterminal source state"
                    )
        creates_attempt = self.disposition in {
            RecoveryDisposition.REPLAY,
            RecoveryDisposition.RECONCILE,
            RecoveryDisposition.REDO_COMMIT,
        }
        if creates_attempt != (self.resolution_attempt_id is not None):
            raise ValueError("resolutionAttemptId must be present exactly for retry/reconcile/redo")
        if self.resolution_attempt_id == self.source_attempt_id:
            raise ValueError("resolutionAttemptId must differ from sourceAttemptId")
        if self.source_terminal and self.disposition is RecoveryDisposition.REPLAY:
            raise ValueError("terminal source attempts cannot be replayed")
        if self.source_terminal and self.disposition is RecoveryDisposition.ABORT:
            raise ValueError("terminal source attempts cannot be aborted again")
        if self.expected_source_state in {ActionState.UNKNOWN, ActionState.PARTIAL} and (
            self.disposition
            not in {
                RecoveryDisposition.RECONCILE,
                RecoveryDisposition.REDO_COMMIT,
                RecoveryDisposition.QUARANTINE,
            }
        ):
            raise ValueError("UNKNOWN and PARTIAL attempts require reconciliation or quarantine")
        proof_required = self.terminalize_source_to is ActionState.ABORTED or (
            self.disposition is RecoveryDisposition.REPLAY
            and self.expected_source_state
            in {ActionState.PREPARED, ActionState.EXECUTING, ActionState.OBSERVED}
        )
        if proof_required:
            if self.non_execution_proof_digest is None:
                raise ValueError("recovery decision requires a mechanical non-execution proof")
        elif self.non_execution_proof_digest is not None:
            raise ValueError("nonExecutionProofDigest is not valid for this recovery decision")
        if self.disposition is RecoveryDisposition.ABORT:
            if self.terminalize_source_to is ActionState.UNKNOWN:
                raise ValueError("ABORT cannot terminalize a source as UNKNOWN")
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"decision_digest"}),
        )
        if self.decision_digest is not None and self.decision_digest != expected:
            raise ValueError("decisionDigest does not match the RecoveryDecision")
        object.__setattr__(self, "decision_digest", expected)
        return self


def canonical_recovery_decision_digest(decision: RecoveryDecision) -> str:
    if type(decision) is not RecoveryDecision:
        raise TypeError("decision must be an exact RecoveryDecision")
    validated = RecoveryDecision.model_validate(decision)
    return _canonical_model_digest(
        validated,
        exclude=frozenset({"decision_digest"}),
    )


def validate_recovery_decision_context(
    decision: RecoveryDecision,
    context: RecoveryContext,
) -> RecoveryDecision:
    """Prove that a frozen decision is an attenuation of its reducer input."""

    if type(decision) is not RecoveryDecision:
        raise TypeError("decision must be an exact RecoveryDecision")
    if type(context) is not RecoveryContext:
        raise TypeError("context must be an exact RecoveryContext")
    validated_decision = RecoveryDecision.model_validate(decision)
    validated_context = RecoveryContext.model_validate(context)
    bindings = (
        ("recoveryEpochId", "recovery_epoch_id"),
        ("recoveryPlanDigest", "recovery_plan_digest"),
        ("recoveryOwnerId", "recovery_owner_id"),
        ("recoveryLeaseName", "recovery_lease_name"),
        ("partitionId", "partition_id"),
        ("expectedPartitionCompareVersion", "expected_partition_compare_version"),
        ("recoveryFencingToken", "recovery_fencing_token"),
        ("actionId", "action_id"),
        ("expectedActionCompareVersion", "expected_action_compare_version"),
        ("taskContractDigest", "task_contract_digest"),
        ("sourceAttemptId", "source_attempt_id"),
        ("expectedSourceState", "expected_source_state"),
        ("expectedSourceVersion", "expected_source_version"),
        ("sourceTerminal", "source_terminal"),
        ("resolutionAttemptId", "resolution_attempt_id"),
        ("contextDigest", "context_digest"),
    )
    for alias, attribute in bindings:
        if getattr(validated_decision, attribute) != getattr(
            validated_context,
            attribute,
        ):
            raise ValueError(f"{alias} does not match the RecoveryContext")

    proof_required = validated_decision.terminalize_source_to is ActionState.ABORTED or (
        validated_decision.disposition is RecoveryDisposition.REPLAY
        and validated_decision.expected_source_state
        in {ActionState.PREPARED, ActionState.EXECUTING, ActionState.OBSERVED}
    )
    if proof_required:
        proof = validated_context.non_execution_proof
        if proof is None or (validated_decision.non_execution_proof_digest != proof.proof_digest):
            raise ValueError("recovery decision does not match the mechanical non-execution proof")
    if validated_context.integrity_status != "clean" or (
        validated_context.workspace_commit_state == "quarantined"
    ):
        if validated_decision.disposition is not RecoveryDisposition.QUARANTINE:
            raise ValueError("corrupt or quarantined recovery context must quarantine")
    elif validated_context.workspace_commit_state in {"decided", "published"} and (
        validated_decision.disposition in {RecoveryDisposition.ABORT, RecoveryDisposition.REPLAY}
    ):
        raise ValueError("durable workspace commit cannot be aborted or replayed")
    elif validated_decision.disposition is RecoveryDisposition.REPLAY:
        if (
            validated_context.effect_may_have_started
            or not validated_context.replay_safe
            or validated_context.action_intent.declaration.recovery_strategy
            is not RecoveryStrategy.READ_ONLY_REPLAY
            or not validated_context.authority_valid
            or not validated_context.task_version_current
            or not validated_context.fence_current
            or not validated_context.state_root_current
            or validated_context.pending_user_decision
            or validated_context.recovery_adapter_digest is None
        ):
            raise ValueError("REPLAY is not safe for the RecoveryContext")
    elif validated_decision.disposition is RecoveryDisposition.RECONCILE:
        if (
            not validated_context.effect_may_have_started
            and validated_context.expected_source_state
            not in {ActionState.UNKNOWN, ActionState.PARTIAL}
        ) or (
            validated_context.recovery_adapter_digest is None
            or validated_context.action_intent.declaration.recovery_strategy
            is not RecoveryStrategy.PROVIDER_RECONCILIATION
            or not validated_context.authority_valid
            or not validated_context.task_version_current
            or not validated_context.fence_current
            or not validated_context.state_root_current
        ):
            raise ValueError("RECONCILE is not supported by the RecoveryContext")
    elif validated_decision.disposition is RecoveryDisposition.REDO_COMMIT:
        if (
            validated_context.workspace_commit_state != "decided"
            or validated_context.action_intent.declaration.recovery_strategy
            is not RecoveryStrategy.WORKSPACE_TRANSACTION
            or not validated_context.authority_valid
            or not validated_context.task_version_current
            or not validated_context.fence_current
            or not validated_context.state_root_current
            or validated_context.pending_user_decision
        ):
            raise ValueError("REDO_COMMIT requires a durable commit decision")
    elif validated_decision.disposition is RecoveryDisposition.REBUILD_PROJECTIONS:
        if (
            validated_context.expected_source_state is not ActionState.COMMITTED
            or validated_context.projection_status != "lagging"
        ):
            raise ValueError("REBUILD_PROJECTIONS requires committed state with projection lag")
    return decision


class SourceSpan(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    span_id: str = Field(alias="spanId", min_length=1)
    source_snapshot_id: str = Field(alias="sourceSnapshotId", min_length=1)
    source_snapshot_digest: str = Field(alias="sourceSnapshotDigest")
    codepoint_start: int = Field(alias="codepointStart", ge=0, strict=True)
    codepoint_end: int = Field(alias="codepointEnd", ge=0, strict=True)
    utf8_start: int = Field(alias="utf8Start", ge=0, strict=True)
    utf8_end: int = Field(alias="utf8End", ge=0, strict=True)
    text_digest: str = Field(alias="textDigest")

    @model_validator(mode="after")
    def _require_nonempty_dual_spans(self) -> Self:
        if self.codepoint_start >= self.codepoint_end:
            raise ValueError("source span code-point range must be nonempty")
        if self.utf8_start >= self.utf8_end:
            raise ValueError("source span UTF-8 range must be nonempty")
        return self


def validate_source_span(span: SourceSpan, source_text: str) -> SourceSpan:
    if type(span) is not SourceSpan:
        raise TypeError("span must be an exact SourceSpan")
    if type(source_text) is not str:
        raise TypeError("source text must be an exact string")
    validated = SourceSpan.model_validate(span)
    source_bytes = source_text.encode("utf-8")
    expected_snapshot_digest = "sha256:" + sha256(source_bytes).hexdigest()
    if validated.source_snapshot_digest != expected_snapshot_digest:
        raise ValueError("sourceSnapshotDigest does not match resolved source text")
    if validated.codepoint_end > len(source_text) or validated.utf8_end > len(source_bytes):
        raise ValueError("source span is out of bounds")
    text = source_text[validated.codepoint_start : validated.codepoint_end]
    text_bytes = text.encode("utf-8")
    if source_bytes[validated.utf8_start : validated.utf8_end] != text_bytes:
        raise ValueError("source span UTF-8 and code-point ranges disagree")
    if validated.text_digest != "sha256:" + sha256(text_bytes).hexdigest():
        raise ValueError("source span textDigest does not match its exact slice")
    return validated


class JournalCoverageWindow(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    start_sequence: int = Field(alias="startSequence", ge=1, strict=True)
    end_sequence: int = Field(alias="endSequence", ge=1, strict=True)
    start_event_hash: str = Field(alias="startEventHash")
    end_event_hash: str = Field(alias="endEventHash")

    @model_validator(mode="after")
    def _ordered_window(self) -> Self:
        if self.end_sequence < self.start_sequence:
            raise ValueError("coverage window endSequence precedes startSequence")
        return self


class CoverageDescriptor(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    coverage_kind: Literal[
        "journal_window",
        "resource_inventory",
        "query_plan",
        "source_set",
    ] = Field(alias="coverageKind")
    journal_window: JournalCoverageWindow | None = Field(
        default=None,
        alias="journalWindow",
    )
    searched_resource_refs: tuple[str, ...] = Field(alias="searchedResourceRefs")
    coverage_digest: str | None = Field(default=None, alias="coverageDigest")

    @model_validator(mode="after")
    def _validate_window_and_digest(self) -> Self:
        if (self.coverage_kind == "journal_window") != (self.journal_window is not None):
            raise ValueError("journalWindow is required exactly for journal_window coverage")
        if len(self.searched_resource_refs) != len(set(self.searched_resource_refs)):
            raise ValueError("searchedResourceRefs must be unique")
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"coverage_digest"}),
        )
        if self.coverage_digest is not None and self.coverage_digest != expected:
            raise ValueError("coverageDigest does not match CoverageDescriptor")
        object.__setattr__(self, "coverage_digest", expected)
        return self


class FreshnessBinding(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    rule: Literal[
        "same_state_root",
        "same_workspace_generation",
        "same_retrieval_window",
        "current_release",
        "historical_snapshot",
    ]
    state_root: str | None = Field(default=None, alias="stateRoot")
    workspace_generation: int | None = Field(
        default=None,
        alias="workspaceGeneration",
        ge=0,
        strict=True,
    )
    observed_at: datetime = Field(alias="observedAt")
    freshness_digest: str | None = Field(default=None, alias="freshnessDigest")

    @model_validator(mode="after")
    def _validate_rule_and_digest(self) -> Self:
        if self.rule == "same_state_root":
            if self.state_root is None or self.workspace_generation is not None:
                raise ValueError("same_state_root freshness requires only stateRoot")
        elif self.rule == "same_workspace_generation":
            if self.workspace_generation is None or self.state_root is not None:
                raise ValueError(
                    "same_workspace_generation freshness requires only workspaceGeneration"
                )
        elif self.state_root is not None or self.workspace_generation is not None:
            raise ValueError("retrieval/release freshness must not carry workspace state bindings")
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"freshness_digest"}),
        )
        if self.freshness_digest is not None and self.freshness_digest != expected:
            raise ValueError("freshnessDigest does not match FreshnessBinding")
        object.__setattr__(self, "freshness_digest", expected)
        return self


class ResearchSourceBinding(EnvelopeModel):
    """Typed provenance for a research source snapshot."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    source_snapshot_id: str = Field(alias="sourceSnapshotId", min_length=1)
    source_snapshot_digest: str = Field(alias="sourceSnapshotDigest")
    source_class: Literal["primary", "secondary", "snippet"] = Field(alias="sourceClass")
    trust_tier: Literal[
        "official",
        "peer_reviewed",
        "first_party",
        "reputable_secondary",
        "exploratory",
        "untrusted_snippet",
    ] = Field(alias="trustTier")
    retrieved_at: datetime = Field(alias="retrievedAt")
    source_version: str = Field(alias="sourceVersion", min_length=1)
    truncated: bool = Field(strict=True)

    @model_validator(mode="after")
    def _validate_source_class(self) -> Self:
        if self.source_class == "snippet":
            if not self.truncated or self.trust_tier != "untrusted_snippet":
                raise ValueError("snippet research sources must be truncated and untrusted_snippet")
        elif self.trust_tier == "untrusted_snippet":
            raise ValueError("untrusted_snippet trust tier requires snippet sourceClass")
        return self


class EvidenceNodeDraft(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    evidence_id: str = Field(alias="evidenceId", min_length=1)
    kind: EvidenceKind
    semantic_class: EvidenceSemanticClass = Field(alias="semanticClass")
    session_id: str = Field(alias="sessionId", min_length=1)
    turn_id: str = Field(alias="turnId", min_length=1)
    run_id: str = Field(alias="runId", min_length=1)
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    requirement_ids: tuple[str, ...] = Field(alias="requirementIds")
    claim_ids: tuple[str, ...] = Field(default=(), alias="claimIds")
    action_id: str | None = Field(default=None, alias="actionId", min_length=1)
    attempt_id: str | None = Field(default=None, alias="attemptId", min_length=1)
    request_digest: str | None = Field(default=None, alias="requestDigest")
    authority_digest: str | None = Field(default=None, alias="authorityDigest")
    policy_digest: str = Field(alias="policyDigest")
    producer_id: str = Field(alias="producerId", min_length=1)
    producer_version: str = Field(alias="producerVersion", min_length=1)
    producer_alive: bool = Field(alias="producerAlive", strict=True)
    producer_status: DependencyStatus = Field(alias="producerStatus")
    producer_schema_version: str = Field(alias="producerSchemaVersion", min_length=1)
    producer_invocation_evidence_id: str | None = Field(
        default=None,
        alias="producerInvocationEvidenceId",
        min_length=1,
    )
    producer_invocation_evidence_digest: str | None = Field(
        default=None,
        alias="producerInvocationEvidenceDigest",
    )
    partition_id: str = Field(alias="partitionId", min_length=1)
    admission_sequence: int = Field(alias="admissionSequence", ge=0, strict=True)
    workspace_generation: int | None = Field(
        default=None,
        alias="workspaceGeneration",
        ge=0,
        strict=True,
    )
    state_root: str | None = Field(default=None, alias="stateRoot")
    source_snapshot_id: str | None = Field(
        default=None,
        alias="sourceSnapshotId",
        min_length=1,
    )
    source_snapshot_digest: str | None = Field(
        default=None,
        alias="sourceSnapshotDigest",
    )
    source_spans: tuple[SourceSpan, ...] = Field(default=(), alias="sourceSpans")
    research_source: ResearchSourceBinding | None = Field(
        default=None,
        alias="researchSource",
    )
    content_digest: str = Field(alias="contentDigest")
    tool_input_digest: str | None = Field(default=None, alias="toolInputDigest")
    tool_output_digest: str | None = Field(default=None, alias="toolOutputDigest")
    parent_evidence_ids: tuple[str, ...] = Field(alias="parentEvidenceIds")
    coverage: CoverageDescriptor
    freshness: FreshnessBinding
    public_redaction_class: Literal[
        "public",
        "public_summary",
        "private_reference_only",
    ] = Field(alias="publicRedactionClass")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    created_at: datetime = Field(alias="createdAt")
    producer_payload_digest: str = Field(alias="producerPayloadDigest")

    @model_validator(mode="after")
    def _validate_evidence_lineage(self) -> Self:
        if not self.producer_alive and self.producer_status is DependencyStatus.CLEAN:
            raise ValueError("a non-live evidence producer cannot report clean")
        if (self.producer_invocation_evidence_id is None) != (
            self.producer_invocation_evidence_digest is None
        ):
            raise ValueError("producer invocation evidence ID and digest are both-or-neither")
        if self.producer_status is DependencyStatus.CLEAN and (
            not self.producer_alive or self.producer_invocation_evidence_id is None
        ):
            raise ValueError("clean evidence requires proven producer invocation")
        if (self.source_snapshot_id is None) != (self.source_snapshot_digest is None):
            raise ValueError("sourceSnapshotId and sourceSnapshotDigest are both-or-neither")
        if self.source_spans and self.source_snapshot_id is None:
            raise ValueError("sourceSpans require a source snapshot binding")
        if self.research_source is not None:
            if (
                self.research_source.source_snapshot_id != self.source_snapshot_id
                or self.research_source.source_snapshot_digest != self.source_snapshot_digest
            ):
                raise ValueError("researchSource does not match source snapshot binding")
        if (
            self.claim_ids
            and self.research_source is None
            and self.kind
            in {
                EvidenceKind.SOURCE_SNAPSHOT,
                EvidenceKind.SOURCE_SPAN,
                EvidenceKind.EXTRACTION,
                EvidenceKind.ENTAILMENT_VERDICT,
            }
        ):
            raise ValueError("research claim evidence requires typed researchSource metadata")
        if self.evidence_id in self.parent_evidence_ids:
            raise ValueError("evidence nodes cannot name themselves as a parent")
        if self.producer_invocation_evidence_id == self.evidence_id:
            raise ValueError("evidence nodes cannot prove their own producer invocation")
        span_ids = tuple(span.span_id for span in self.source_spans)
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("source span IDs must be unique within an evidence node")
        for span in self.source_spans:
            if span.source_snapshot_id != self.source_snapshot_id:
                raise ValueError("source span snapshot ID does not match evidence node")
            if span.source_snapshot_digest != self.source_snapshot_digest:
                raise ValueError("source span snapshot digest does not match evidence node")
        if self.freshness.state_root is not None and self.freshness.state_root != self.state_root:
            raise ValueError("freshness stateRoot does not match the evidence node")
        if (
            self.freshness.workspace_generation is not None
            and self.freshness.workspace_generation != self.workspace_generation
        ):
            raise ValueError("freshness workspaceGeneration does not match the evidence node")
        for sequence in (
            self.requirement_ids,
            self.claim_ids,
            self.source_spans,
            self.parent_evidence_ids,
            self.reason_codes,
        ):
            if len(sequence) != len(set(sequence)):
                raise ValueError("evidence lineage sequences must not contain duplicates")
        if self.action_id is None and self.attempt_id is not None:
            raise ValueError("attemptId requires actionId")
        if self.kind is EvidenceKind.ACTION_RECEIPT and any(
            value is None
            for value in (
                self.action_id,
                self.attempt_id,
                self.request_digest,
                self.authority_digest,
            )
        ):
            raise ValueError("action_receipt requires action, attempt, request, and authority")
        if self.kind is EvidenceKind.SOURCE_SNAPSHOT:
            if self.source_snapshot_id is None or self.source_spans:
                raise ValueError("source_snapshot requires a snapshot and no inline source spans")
        if self.kind is EvidenceKind.SOURCE_SPAN and (
            self.source_snapshot_id is None or not self.source_spans
        ):
            raise ValueError("source_span requires a snapshot and nonempty source spans")
        if self.kind in {EvidenceKind.EXTRACTION, EvidenceKind.ENTAILMENT_VERDICT} and (
            not self.claim_ids or not self.parent_evidence_ids or self.source_snapshot_id is None
        ):
            raise ValueError(
                "extraction and entailment evidence require claims, parents, and a source"
            )
        verdict_kinds = {
            EvidenceKind.ENTAILMENT_VERDICT,
            EvidenceKind.POSTCONDITION_VERDICT,
            EvidenceKind.REQUIREMENT_VERDICT,
            EvidenceKind.COMPLETION_VERDICT,
        }
        if self.kind in verdict_kinds and self.semantic_class is not EvidenceSemanticClass.VERDICT:
            raise ValueError("verdict evidence kinds require verdict semanticClass")
        if self.kind is EvidenceKind.EXTRACTION and (
            self.semantic_class is not EvidenceSemanticClass.INFERENCE
        ):
            raise ValueError("extraction evidence requires inference semanticClass")
        if self.kind is EvidenceKind.WORKSPACE_POSTCONDITION and any(
            value is None
            for value in (
                self.action_id,
                self.attempt_id,
                self.workspace_generation,
                self.state_root,
            )
        ):
            raise ValueError(
                "workspace_postcondition requires action, attempt, generation, and state root"
            )
        if self.kind is EvidenceKind.REQUIREMENT_VERDICT and not self.requirement_ids:
            raise ValueError("requirement_verdict requires requirementIds")
        return self


class EvidenceNode(EvidenceNodeDraft):
    journal_sequence: int = Field(alias="journalSequence", ge=1, strict=True)
    journal_event_hash: str = Field(alias="journalEventHash")


class EvidenceEdge(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    edge_id: str = Field(alias="edgeId", min_length=1)
    source_evidence_id: str = Field(alias="sourceEvidenceId", min_length=1)
    target_evidence_id: str = Field(alias="targetEvidenceId", min_length=1)
    kind: Literal[
        "derived_from",
        "observes",
        "supports",
        "contradicts",
        "qualifies",
        "invalidates",
        "covers",
        "caused_by",
    ]

    @model_validator(mode="after")
    def _reject_self_edge(self) -> Self:
        if self.source_evidence_id == self.target_evidence_id:
            raise ValueError("evidence edges cannot target their source node")
        return self


def canonical_evidence_edges_digest(edges: tuple[EvidenceEdge, ...]) -> str:
    if type(edges) is not tuple or any(type(edge) is not EvidenceEdge for edge in edges):
        raise TypeError("edges must be an exact tuple of exact EvidenceEdge values")
    payload = {
        "schemaId": "magi.evidence_edges.v1",
        "edges": [edge.model_dump(by_alias=True, mode="json") for edge in edges],
    }
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_evidence_node_digest(node: EvidenceNodeDraft | EvidenceNode) -> str:
    """Digest the immutable evidence content, excluding journal storage coordinates."""

    if type(node) not in (EvidenceNodeDraft, EvidenceNode):
        raise TypeError("node must be an exact EvidenceNodeDraft or EvidenceNode")
    field_names = set(EvidenceNodeDraft.model_fields)
    payload = node.model_dump(
        by_alias=True,
        mode="json",
        include=field_names,
    )
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class EvidenceRecordDraft(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    node: EvidenceNodeDraft
    edges: tuple[EvidenceEdge, ...]

    @model_validator(mode="after")
    def _parallel_parent_edges(self) -> Self:
        if tuple(edge.source_evidence_id for edge in self.edges) != (self.node.parent_evidence_ids):
            raise ValueError("one ordered evidence edge is required per parent evidence ID")
        if any(edge.target_evidence_id != self.node.evidence_id for edge in self.edges):
            raise ValueError("draft evidence edges must target the draft node")
        if len({edge.edge_id for edge in self.edges}) != len(self.edges):
            raise ValueError("draft evidence edge IDs must be unique")
        return self


class EvidenceRecordRecording(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    node: EvidenceNode
    edges: tuple[EvidenceEdge, ...]
    event: JournalEvent
    projection_compare_version: int = Field(
        alias="projectionCompareVersion",
        ge=1,
        strict=True,
    )

    @model_validator(mode="after")
    def _validate_node_event_binding(self) -> Self:
        node = self.node
        event = self.event
        if tuple(edge.source_evidence_id for edge in self.edges) != node.parent_evidence_ids:
            raise ValueError("recorded edges do not exactly cover parentEvidenceIds")
        if any(edge.target_evidence_id != node.evidence_id for edge in self.edges):
            raise ValueError("recorded edges must target the recorded evidence node")
        if len({edge.edge_id for edge in self.edges}) != len(self.edges):
            raise ValueError("recorded evidence edge IDs must be unique")
        bindings = (
            ("eventType", event.event_type, "evidence.recorded"),
            ("actionId", event.action_id, node.action_id),
            ("attemptId", event.attempt_id, node.attempt_id),
            ("partitionId", event.partition_id, node.partition_id),
            ("taskContractId", event.task_contract_id, node.task_contract_id),
            ("taskVersion", event.task_version, node.task_version),
            (
                "taskContractDigest",
                event.task_contract_digest,
                node.task_contract_digest,
            ),
            (
                "completionEpochId",
                event.completion_epoch_id,
                node.completion_epoch_id,
            ),
            ("admissionSequence", event.admission_sequence, node.admission_sequence),
            ("policyDigest", event.policy_digest, node.policy_digest),
            ("sequence", event.sequence, node.journal_sequence),
            ("eventHash", event.event_hash, node.journal_event_hash),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"event.{alias} does not match evidence node")
        if node.request_digest is not None and event.request_digest != node.request_digest:
            raise ValueError("event.requestDigest does not match evidence node")
        event_payload = _strict_json_loads(event.payload_json)
        if not isinstance(event_payload, Mapping):
            raise ValueError("evidence.recorded payload must be an object")
        expected_node_digest = canonical_evidence_node_digest(node)
        if event_payload.get("evidenceNodeDigest") != expected_node_digest:
            raise ValueError("event.payloadDigest does not commit the canonical evidence node")
        if event_payload.get("evidenceId") != node.evidence_id:
            raise ValueError("event payload evidenceId does not match evidence node")
        if event_payload.get("evidenceEdgesDigest") != canonical_evidence_edges_digest(self.edges):
            raise ValueError("event payload does not commit the canonical evidence edges")
        return self


class JournalChainLink(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    sequence: int = Field(ge=0, strict=True)
    previous_hash: str = Field(alias="previousHash")
    event_hash: str = Field(alias="eventHash")


class ProjectionCursorBinding(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    projection_id: str = Field(alias="projectionId", min_length=1)
    required_sequence: int = Field(alias="requiredSequence", ge=0, strict=True)
    required_event_hash: str = Field(alias="requiredEventHash")
    acknowledged_sequence: int = Field(
        alias="acknowledgedSequence",
        ge=0,
        strict=True,
    )
    acknowledged_event_hash: str = Field(alias="acknowledgedEventHash")
    acknowledged_ancestry: tuple[JournalChainLink, ...] = Field(
        default=(),
        alias="acknowledgedAncestry",
    )
    state_root: str = Field(alias="stateRoot")
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _require_acknowledgement_coverage(self) -> Self:
        if self.acknowledged_sequence < self.required_sequence:
            raise ValueError("acknowledgedSequence must cover requiredSequence")
        if (
            self.acknowledged_sequence == self.required_sequence
            and self.acknowledged_event_hash != self.required_event_hash
        ):
            raise ValueError("equal projection sequences must carry the same event hash")
        if self.acknowledged_sequence == self.required_sequence:
            if self.acknowledged_ancestry:
                raise ValueError("equal projection sequences do not require ancestry")
            return self
        expected_length = self.acknowledged_sequence - self.required_sequence + 1
        if len(self.acknowledged_ancestry) != expected_length:
            raise ValueError("later projection acknowledgement requires complete ancestry")
        first = self.acknowledged_ancestry[0]
        last = self.acknowledged_ancestry[-1]
        if (
            first.sequence != self.required_sequence
            or first.event_hash != self.required_event_hash
            or last.sequence != self.acknowledged_sequence
            or last.event_hash != self.acknowledged_event_hash
        ):
            raise ValueError("projection ancestry endpoints do not match cursor hashes")
        for previous, current in zip(
            self.acknowledged_ancestry,
            self.acknowledged_ancestry[1:],
        ):
            if (
                current.sequence != previous.sequence + 1
                or current.previous_hash != previous.event_hash
            ):
                raise ValueError("projection ancestry must be a contiguous hash chain")
        return self


class VerificationEvidenceBinding(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    evidence_id: str = Field(alias="evidenceId", min_length=1)
    evidence_digest: str = Field(alias="evidenceDigest")
    verification_outcome: Literal["passed"] = Field(alias="verificationOutcome")
    source_partition_id: str = Field(alias="sourcePartitionId", min_length=1)
    source_event_id: str = Field(alias="sourceEventId", min_length=1)
    source_event_sequence: int = Field(alias="sourceEventSequence", ge=1, strict=True)
    source_event_hash: str = Field(alias="sourceEventHash")
    source_head_sequence: int = Field(alias="sourceHeadSequence", ge=1, strict=True)
    source_head_hash: str = Field(alias="sourceHeadHash")
    source_head_compare_version: int = Field(
        alias="sourceHeadCompareVersion",
        ge=0,
        strict=True,
    )
    projection_cursors: tuple[ProjectionCursorBinding, ...] = Field(
        alias="projectionCursors",
        min_length=1,
    )
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    request_digest: str = Field(alias="requestDigest")
    verified_state_root: str = Field(alias="verifiedStateRoot")

    @model_validator(mode="after")
    def _validate_source_vector(self) -> Self:
        if len(
            {(cursor.partition_id, cursor.projection_id) for cursor in self.projection_cursors}
        ) != len(self.projection_cursors):
            raise ValueError("projection cursor keys must be unique")
        if self.source_event_sequence > self.source_head_sequence:
            raise ValueError("sourceEventSequence cannot exceed sourceHeadSequence")
        if (
            self.source_event_sequence == self.source_head_sequence
            and self.source_event_hash != self.source_head_hash
        ):
            raise ValueError("source event and head hashes must match at equal sequence")
        if any(cursor.state_root != self.verified_state_root for cursor in self.projection_cursors):
            raise ValueError("projection cursor stateRoot must equal verifiedStateRoot")
        source_cursors = tuple(
            cursor
            for cursor in self.projection_cursors
            if cursor.partition_id == self.source_partition_id
            and cursor.required_sequence == self.source_event_sequence
            and cursor.required_event_hash == self.source_event_hash
        )
        if not source_cursors:
            raise ValueError("a projection source event cursor must bind the verified source event")
        if not any(
            cursor.acknowledged_sequence == self.source_head_sequence
            and cursor.acknowledged_event_hash == self.source_head_hash
            for cursor in source_cursors
        ):
            raise ValueError("source event cursor must acknowledge the bound journal head")
        return self


def canonical_resource_refs_digest(resource_refs: tuple[str, ...]) -> str:
    if type(resource_refs) is not tuple or any(type(ref) is not str for ref in resource_refs):
        raise TypeError("resource refs must be an exact tuple of exact strings")
    encoded = _canonical_json({"resourceRefs": list(resource_refs)}).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def canonical_workspace_view_binding_digest(
    *,
    workspace_id: str,
    workspace_ref: str,
    authority_partition_id: str,
    generation: int,
    state_root: str,
) -> str:
    """Bind a logical generation to one canonical physical workspace view."""

    for name, value in (
        ("workspace_id", workspace_id),
        ("workspace_ref", workspace_ref),
        ("authority_partition_id", authority_partition_id),
    ):
        if type(value) is not str or not value:
            raise TypeError(f"{name} must be a non-empty exact string")
    if type(generation) is not int or generation < 0:
        raise TypeError("generation must be a non-negative exact integer")
    require_digest(state_root)
    payload = {
        "schemaId": "magi.workspace_view_binding.v1",
        "authorityPartitionId": authority_partition_id,
        "generation": generation,
        "stateRoot": state_root,
        "workspaceId": workspace_id,
        "workspaceRef": workspace_ref,
    }
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _require_canonical_workspace_root_ref(ref: str) -> str:
    """Require the root identity form used by durable workspace snapshots."""

    try:
        require_canonical_workspace_resource_ref(ref)
    except (TypeError, ValueError) as exc:
        raise ValueError("workspaceRef must be a canonical workspace root ref") from exc
    parsed = urlsplit(ref)
    if parsed.path != "/" or parsed.query or parsed.fragment:
        raise ValueError("workspaceRef must be a canonical workspace root ref")
    return ref


def _workspace_root_identity(ref: str) -> str:
    require_canonical_workspace_resource_ref(ref)
    return urlsplit(ref).netloc


def _require_workspace_resources_share_root(
    workspace_ref: str,
    resource_refs: tuple[str, ...],
) -> None:
    root_identity = _workspace_root_identity(workspace_ref)
    for resource_ref in resource_refs:
        require_canonical_workspace_resource_ref(resource_ref)
        if _workspace_root_identity(resource_ref) != root_identity:
            raise ValueError("workspace resource refs must share the workspaceRef root")


class JournalHead(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    sequence: int = Field(ge=0, strict=True)
    event_hash: str = Field(alias="eventHash")
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)


class PartitionGate(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    state: Literal["ready", "recovering", "quarantined"]
    recovery_epoch_id: str | None = Field(
        default=None,
        alias="recoveryEpochId",
        min_length=1,
    )
    recovery_plan_digest: str | None = Field(
        default=None,
        alias="recoveryPlanDigest",
    )
    recovery_owner_id: str | None = Field(default=None, alias="recoveryOwnerId")
    recovery_fencing_token: int = Field(
        alias="recoveryFencingToken",
        ge=0,
        strict=True,
    )
    quarantine_reason_digest: str | None = Field(
        default=None,
        alias="quarantineReasonDigest",
    )
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_gate_owner(self) -> Self:
        recovery_fields = (
            self.recovery_epoch_id,
            self.recovery_plan_digest,
            self.recovery_owner_id,
        )
        if self.state == "recovering":
            if not all(value is not None for value in recovery_fields):
                raise ValueError("recovering partitions require epoch, plan, and owner bindings")
            if self.recovery_fencing_token < 1:
                raise ValueError("recovering partitions require a positive fencing token")
        elif any(value is not None for value in recovery_fields):
            raise ValueError("recovery epoch, plan, and owner are valid only while recovering")
        if self.state == "quarantined" and self.quarantine_reason_digest is None:
            raise ValueError("quarantined partitions require quarantineReasonDigest")
        if self.state != "quarantined" and self.quarantine_reason_digest is not None:
            raise ValueError("quarantineReasonDigest is valid only while quarantined")
        return self


class LeaseSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    lease_name: str = Field(alias="leaseName", min_length=1)
    state: LeaseState
    owner_id: str | None = Field(default=None, alias="ownerId", min_length=1)
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    high_water_fencing_token: int = Field(
        alias="highWaterFencingToken",
        ge=0,
        strict=True,
    )
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _preserve_high_water_and_release_tombstone(self) -> Self:
        if self.high_water_fencing_token < self.fencing_token:
            raise ValueError("highWaterFencingToken cannot trail fencingToken")
        if self.state is LeaseState.HELD:
            if self.owner_id is None or self.expires_at is None:
                raise ValueError("held leases require ownerId and expiresAt")
            if self.fencing_token < 1:
                raise ValueError("held leases require a positive fencing token")
            if self.high_water_fencing_token != self.fencing_token:
                raise ValueError(
                    "held lease highWaterFencingToken must equal fencingToken"
                )
        elif self.owner_id is not None or self.expires_at is not None:
            raise ValueError("released lease tombstones clear ownerId and expiresAt")
        return self


class ProjectionCursorSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    projection_id: str = Field(alias="projectionId", min_length=1)
    acknowledged_sequence: int = Field(
        alias="acknowledgedSequence",
        ge=0,
        strict=True,
    )
    acknowledged_event_hash: str = Field(alias="acknowledgedEventHash")
    state_root: str = Field(alias="stateRoot")
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)


class OutboxDraft(EnvelopeModel):
    """Generic non-final delivery request coupled to one generic event append."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    outbox_id: str = Field(alias="outboxId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    subject_id: str = Field(alias="subjectId", min_length=1)
    subject_digest: str = Field(alias="subjectDigest")
    kind: Literal["projection_delivery", "diagnostic_delivery"]
    payload_digest: str = Field(alias="payloadDigest")
    payload_json: str = Field(alias="payloadJson")

    @model_validator(mode="after")
    def _validate_generic_payload(self) -> Self:
        parsed = _strict_json_loads(self.payload_json)
        _reject_sensitive_keys(parsed)
        if self.payload_json != _canonical_json(parsed):
            raise ValueError("outbox payload JSON is not canonical")
        expected = "sha256:" + sha256(self.payload_json.encode("utf-8")).hexdigest()
        if self.payload_digest != expected:
            raise ValueError("outbox payloadDigest does not match payloadJson")
        return self


class OutboxItem(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    outbox_id: str = Field(alias="outboxId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    subject_id: str = Field(alias="subjectId", min_length=1)
    subject_digest: str = Field(alias="subjectDigest")
    event_id: str = Field(alias="eventId", min_length=1)
    event_sequence: int = Field(alias="eventSequence", ge=1, strict=True)
    event_hash: str = Field(alias="eventHash")
    kind: Literal[
        "projection_delivery",
        "diagnostic_delivery",
        "final_response",
    ]
    payload_digest: str = Field(alias="payloadDigest")
    payload_json: str = Field(alias="payloadJson")
    state: OutboxState
    claim_owner_id: str | None = Field(default=None, alias="claimOwnerId", min_length=1)
    claim_fencing_token: int | None = Field(
        default=None,
        alias="claimFencingToken",
        ge=1,
        strict=True,
    )
    claim_expires_at: datetime | None = Field(default=None, alias="claimExpiresAt")
    delivery_attempt: int = Field(alias="deliveryAttempt", ge=0, strict=True)
    acknowledgement_digest: str | None = Field(
        default=None,
        alias="acknowledgementDigest",
    )
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_payload_and_claim_state(self) -> Self:
        parsed = _strict_json_loads(self.payload_json)
        _reject_sensitive_keys(parsed)
        if self.payload_json != _canonical_json(parsed):
            raise ValueError("outbox payload JSON is not canonical")
        expected_payload_digest = "sha256:" + sha256(self.payload_json.encode("utf-8")).hexdigest()
        if self.payload_digest != expected_payload_digest:
            raise ValueError("outbox payloadDigest does not match payloadJson")
        claim_fields = (
            self.claim_owner_id,
            self.claim_fencing_token,
            self.claim_expires_at,
        )
        if self.state is OutboxState.CLAIMED:
            if not all(value is not None for value in claim_fields):
                raise ValueError("claimed outbox items require complete claim bindings")
        elif any(value is not None for value in claim_fields):
            raise ValueError("only claimed outbox items may carry claim bindings")
        if self.state is OutboxState.DELIVERED:
            if self.acknowledgement_digest is None:
                raise ValueError("delivered outbox items require acknowledgementDigest")
        elif self.acknowledgement_digest is not None:
            raise ValueError("acknowledgementDigest is valid only after delivery")
        return self


class ActionSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    action_id: str = Field(alias="actionId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    admission_sequence: int = Field(alias="admissionSequence", ge=1, strict=True)
    intent_digest: str = Field(alias="intentDigest")
    resolution: ActionResolution | None = None
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_resolution_binding(self) -> Self:
        if self.resolution is not None:
            if self.resolution.action_id != self.action_id:
                raise ValueError("action resolution does not match the action snapshot")
            if self.resolution.task_contract_digest != self.task_contract_digest:
                raise ValueError("action resolution Task Contract does not match the snapshot")
        return self


class AttemptSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_intent_digest: str = Field(alias="actionIntentDigest")
    request_digest: str = Field(alias="requestDigest")
    state: ActionState
    authority_digest: str | None = Field(default=None, alias="authorityDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    observation: BackendObservation | None = None
    verification: VerificationEvidenceBinding | None = None
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_observation_and_verification_bindings(self) -> Self:
        authority_states = {
            ActionState.AUTHORIZED,
            ActionState.PREPARED,
            ActionState.EXECUTING,
            ActionState.OBSERVED,
            ActionState.COMMITTED,
            ActionState.ABORTED,
            ActionState.PARTIAL,
            ActionState.UNKNOWN,
            ActionState.VERIFIED,
        }
        if self.state in authority_states and self.authority_digest is None:
            raise ValueError("authorized attempt state requires authorityDigest")
        if self.state not in authority_states and self.authority_digest is not None:
            raise ValueError("pre-authority attempt state cannot carry authorityDigest")

        observed_states = {
            ActionState.COMMITTED,
            ActionState.ABORTED,
            ActionState.PARTIAL,
            ActionState.UNKNOWN,
            ActionState.VERIFIED,
        }
        if self.state in observed_states and self.observation is None:
            raise ValueError("physical terminal attempt state requires an observation")
        if self.observation is not None:
            observation = self.observation
            observation_bindings = (
                ("action", observation.action_id, self.action_id),
                ("attempt", observation.attempt_id, self.attempt_id),
                ("partition", observation.partition_id, self.partition_id),
                (
                    "Task Contract",
                    observation.task_contract_digest,
                    self.task_contract_digest,
                ),
                (
                    "action intent",
                    observation.action_intent_digest,
                    self.action_intent_digest,
                ),
                ("request", observation.request_digest, self.request_digest),
                ("authority", observation.authority_digest, self.authority_digest),
                ("fencing", observation.fencing_token, self.fencing_token),
            )
            for name, observed, expected in observation_bindings:
                if observed != expected:
                    raise ValueError(f"attempt observation {name} binding does not match snapshot")
            if self.state is ActionState.VERIFIED:
                if observation.observed_outcome is not ObservationOutcome.COMMITTED:
                    raise ValueError("verified attempt requires a committed physical observation")
            elif self.state in observed_states and (
                observation.observed_outcome.value != self.state.value
            ):
                raise ValueError("attempt state does not match observation outcome")
            elif self.state not in observed_states:
                raise ValueError("nonterminal attempt state cannot carry a terminal observation")

        if self.state is ActionState.VERIFIED:
            if self.verification is None:
                raise ValueError("verified attempt state requires verification evidence")
        elif self.verification is not None:
            raise ValueError("verification evidence is valid only for VERIFIED state")
        if self.verification is not None:
            verification = self.verification
            verification_bindings = (
                ("action", verification.action_id, self.action_id),
                ("attempt", verification.attempt_id, self.attempt_id),
                (
                    "Task Contract",
                    verification.task_contract_digest,
                    self.task_contract_digest,
                ),
                ("request", verification.request_digest, self.request_digest),
            )
            for name, observed, expected in verification_bindings:
                if observed != expected:
                    raise ValueError(f"attempt verification {name} binding does not match snapshot")
        return self


class IntegrityScanResult(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    status: Literal["clean", "corrupt", "unsupported_schema"]
    scanned_through_sequence: int = Field(
        alias="scannedThroughSequence",
        ge=0,
        strict=True,
    )
    scanned_head_hash: str = Field(alias="scannedHeadHash")
    finding_digests: tuple[str, ...] = Field(alias="findingDigests")
    quarantined: bool = Field(strict=True)
    scanned_at: datetime = Field(alias="scannedAt")

    @model_validator(mode="after")
    def _validate_status_findings_and_quarantine(self) -> Self:
        if self.finding_digests != tuple(sorted(self.finding_digests)) or len(
            self.finding_digests
        ) != len(set(self.finding_digests)):
            raise ValueError("findingDigests must be unique and sorted")
        if self.status == "clean":
            if self.finding_digests or self.quarantined:
                raise ValueError("clean integrity scan cannot have findings or quarantine")
        elif not self.finding_digests or not self.quarantined:
            raise ValueError(
                "non-clean integrity scan requires findings and a quarantined partition"
            )
        return self


class WorkspaceSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    workspace_ref: str = Field(alias="workspaceRef", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    current_generation: int = Field(alias="currentGeneration", ge=0, strict=True)
    state_root: str = Field(alias="stateRoot")
    workspace_view_binding_digest: str = Field(alias="workspaceViewBindingDigest")
    publication_state: WorkspacePublicationState = Field(alias="publicationState")
    active_commit_id: str | None = Field(default=None, alias="activeCommitId", min_length=1)
    pending_generation: int | None = Field(
        default=None,
        alias="pendingGeneration",
        ge=1,
        strict=True,
    )
    pending_state_root: str | None = Field(default=None, alias="pendingStateRoot")
    pending_workspace_view_binding_digest: str | None = Field(
        default=None,
        alias="pendingWorkspaceViewBindingDigest",
    )
    active_fencing_token: int | None = Field(
        default=None,
        alias="activeFencingToken",
        ge=1,
        strict=True,
    )
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_active_commit(self) -> Self:
        _require_canonical_workspace_root_ref(self.workspace_ref)
        expected_view_digest = canonical_workspace_view_binding_digest(
            workspace_id=self.workspace_id,
            workspace_ref=self.workspace_ref,
            authority_partition_id=self.authority_partition_id,
            generation=self.current_generation,
            state_root=self.state_root,
        )
        if self.workspace_view_binding_digest != expected_view_digest:
            raise ValueError(
                "workspaceViewBindingDigest does not match the committed workspace view"
            )
        pending_fields = (
            self.active_commit_id,
            self.pending_generation,
            self.pending_state_root,
            self.pending_workspace_view_binding_digest,
            self.active_fencing_token,
        )
        if self.publication_state is WorkspacePublicationState.PUBLISHING:
            if not all(value is not None for value in pending_fields):
                raise ValueError("publishing workspaces require complete pending commit bindings")
            if self.pending_generation != self.current_generation + 1:
                raise ValueError("pendingGeneration must equal currentGeneration + 1")
            if self.pending_state_root == self.state_root:
                raise ValueError("pendingStateRoot must differ from committed stateRoot")
            if self.pending_state_root is None or self.pending_generation is None:
                raise ValueError("validated pending workspace fields are missing")
            expected_pending_view = canonical_workspace_view_binding_digest(
                workspace_id=self.workspace_id,
                workspace_ref=self.workspace_ref,
                authority_partition_id=self.authority_partition_id,
                generation=self.pending_generation,
                state_root=self.pending_state_root,
            )
            if self.pending_workspace_view_binding_digest != expected_pending_view:
                raise ValueError("pendingWorkspaceViewBindingDigest does not match pending view")
        elif any(value is not None for value in pending_fields):
            raise ValueError("pending commit bindings are valid only while publishing")
        return self


class _WorkspaceTransactionFields(EnvelopeModel):
    transaction_id: str = Field(alias="transactionId", min_length=1)
    workspace_ref: str = Field(alias="workspaceRef", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    staging_manifest_ref: str = Field(alias="stagingManifestRef", min_length=1)
    staging_manifest_digest: str = Field(alias="stagingManifestDigest")
    changed_resource_refs_digest: str = Field(alias="changedResourceRefsDigest")
    workspace_view_binding_digest: str = Field(alias="workspaceViewBindingDigest")

    @model_validator(mode="after")
    def _validate_manifest_ref(self) -> Self:
        _require_canonical_workspace_root_ref(self.workspace_ref)
        if self.staging_manifest_ref != (f"authority-manifest://{self.staging_manifest_digest}"):
            raise ValueError("stagingManifestRef must bind stagingManifestDigest")
        return self


class WorkspaceTransactionRequest(_WorkspaceTransactionFields):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")


def canonical_workspace_transaction_request_digest(
    request: WorkspaceTransactionRequest,
) -> str:
    if type(request) is not WorkspaceTransactionRequest:
        raise TypeError("request must be an exact WorkspaceTransactionRequest")
    return _canonical_model_digest(WorkspaceTransactionRequest.model_validate(request))


def _workspace_transaction_staged_event_payload(
    result: WorkspaceTransactionResult,
) -> dict[str, object]:
    request = result.request
    return {
        "actionId": request.action_id,
        "attemptId": request.attempt_id,
        "authorityContractDigest": result.authority_contract_digest,
        "authorityPartitionId": request.authority_partition_id,
        "changedResourceRefsDigest": request.changed_resource_refs_digest,
        "expectedGeneration": result.expected_generation,
        "mutationPlanDigest": result.mutation_plan_digest,
        "stagingManifestDigest": request.staging_manifest_digest,
        "stateRootAfter": result.state_root_after,
        "stateRootBefore": result.state_root_before,
        "targetGeneration": result.target_generation,
        "transactionId": request.transaction_id,
        "transactionRequestDigest": canonical_workspace_transaction_request_digest(request),
        "transactionVersion": result.transaction_compare_version,
        "workspaceId": result.workspace_id,
        "workspaceViewBindingDigest": request.workspace_view_binding_digest,
    }


class WorkspaceTransactionResult(EnvelopeModel):
    """Store-authored result proving which staged transaction a commit consumes."""

    schema_id: Literal["magi.workspace_transaction_result.v1"] = Field(
        default="magi.workspace_transaction_result.v1",
        alias="schemaId",
    )
    request: WorkspaceTransactionRequest
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    expected_generation: int = Field(alias="expectedGeneration", ge=0, strict=True)
    target_generation: int = Field(alias="targetGeneration", ge=1, strict=True)
    state_root_before: str = Field(alias="stateRootBefore")
    state_root_after: str = Field(alias="stateRootAfter")
    mutation_plan_digest: str = Field(alias="mutationPlanDigest")
    changed_resource_refs: tuple[str, ...] = Field(
        alias="changedResourceRefs",
        min_length=1,
    )
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    admission_sequence: int = Field(alias="admissionSequence", ge=1, strict=True)
    authority_contract_id: str = Field(alias="authorityContractId", min_length=1)
    authority_contract_digest: str = Field(alias="authorityContractDigest")
    transaction_compare_version: int = Field(
        alias="transactionCompareVersion",
        ge=1,
        strict=True,
    )
    staged_event: JournalEvent = Field(alias="stagedEvent")
    result_digest: str | None = Field(default=None, alias="resultDigest")

    @field_validator("changed_resource_refs", mode="before")
    @classmethod
    def _require_ordered_changed_resources(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("changedResourceRefs must use an ordered list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_staged_result(self) -> Self:
        request = self.request
        if self.target_generation != self.expected_generation + 1:
            raise ValueError("targetGeneration must equal expectedGeneration + 1")
        if self.state_root_before == self.state_root_after:
            raise ValueError("staged workspace transaction must change the state root")
        _require_workspace_resources_share_root(
            request.workspace_ref,
            self.changed_resource_refs,
        )
        if self.changed_resource_refs != tuple(sorted(self.changed_resource_refs)) or len(
            self.changed_resource_refs
        ) != len(set(self.changed_resource_refs)):
            raise ValueError("changedResourceRefs must be unique and sorted")
        if request.changed_resource_refs_digest != canonical_resource_refs_digest(
            self.changed_resource_refs
        ):
            raise ValueError("changedResourceRefs do not match the transaction request digest")
        expected_view = canonical_workspace_view_binding_digest(
            workspace_id=self.workspace_id,
            workspace_ref=request.workspace_ref,
            authority_partition_id=request.authority_partition_id,
            generation=self.expected_generation,
            state_root=self.state_root_before,
        )
        if request.workspace_view_binding_digest != expected_view:
            raise ValueError("transaction workspaceViewBindingDigest does not match staged input")

        event = self.staged_event
        event_bindings: tuple[tuple[str, object, object], ...] = (
            ("eventType", event.event_type, "workspace.transaction_staged"),
            ("partitionId", event.partition_id, request.authority_partition_id),
            ("actionId", event.action_id, request.action_id),
            ("attemptId", event.attempt_id, request.attempt_id),
            ("taskContractId", event.task_contract_id, self.task_contract_id),
            ("taskVersion", event.task_version, self.task_version),
            ("taskContractDigest", event.task_contract_digest, self.task_contract_digest),
            ("completionEpochId", event.completion_epoch_id, self.completion_epoch_id),
            ("admissionSequence", event.admission_sequence, self.admission_sequence),
            ("authorityContractId", event.authority_contract_id, self.authority_contract_id),
        )
        for alias, observed, expected in event_bindings:
            if observed != expected:
                raise ValueError(f"stagedEvent.{alias} does not match transaction result")
        if _strict_json_loads(event.payload_json) != _workspace_transaction_staged_event_payload(
            self
        ):
            raise ValueError("stagedEvent payload does not bind the exact transaction result")

        expected_digest = _canonical_model_digest(
            self,
            exclude=frozenset({"result_digest"}),
        )
        if self.result_digest is not None and self.result_digest != expected_digest:
            raise ValueError("resultDigest does not match the staged transaction result")
        object.__setattr__(self, "result_digest", expected_digest)
        return self


def canonical_workspace_transaction_result_digest(
    result: WorkspaceTransactionResult,
) -> str:
    if type(result) is not WorkspaceTransactionResult:
        raise TypeError("result must be an exact WorkspaceTransactionResult")
    validated = WorkspaceTransactionResult.model_validate(result)
    return _canonical_model_digest(
        validated,
        exclude=frozenset({"result_digest"}),
    )


class WorkspaceCommitDecisionRequest(_WorkspaceTransactionFields):
    schema_id: Literal["magi.workspace_commit_decision_request.v1"] = Field(
        default="magi.workspace_commit_decision_request.v1",
        alias="schemaId",
    )
    commit_id: str = Field(alias="commitId", min_length=1)
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    expected_generation: int = Field(alias="expectedGeneration", ge=0, strict=True)
    target_generation: int = Field(alias="targetGeneration", ge=1, strict=True)
    expected_workspace_compare_version: int | None = Field(
        default=None,
        alias="expectedWorkspaceCompareVersion",
        ge=0,
        strict=True,
    )
    expected_transaction_compare_version: int = Field(
        alias="expectedTransactionCompareVersion",
        ge=0,
        strict=True,
    )
    # Older persisted commit intents predate the staged-result receipt.  Keep
    # their wire shape readable; newly issued intents bind this digest.
    staged_transaction_digest: str | None = Field(default=None, alias="stagedTransactionDigest")
    state_root_before: str = Field(alias="stateRootBefore")
    state_root_after: str = Field(alias="stateRootAfter")
    decision_fencing_token: int = Field(alias="decisionFencingToken", ge=1, strict=True)
    mutation_plan_digest: str = Field(alias="mutationPlanDigest")
    changed_resource_refs: tuple[str, ...] = Field(
        alias="changedResourceRefs",
        min_length=1,
    )

    @model_validator(mode="after")
    def _validate_next_generation_and_resources(self) -> Self:
        if self.target_generation != self.expected_generation + 1:
            raise ValueError("targetGeneration must equal expectedGeneration + 1")
        if self.state_root_before == self.state_root_after:
            raise ValueError("workspace commit must change the state root")
        _require_workspace_resources_share_root(
            self.workspace_ref,
            self.changed_resource_refs,
        )
        if len(self.changed_resource_refs) != len(set(self.changed_resource_refs)):
            raise ValueError("changedResourceRefs must be unique")
        if self.changed_resource_refs != tuple(sorted(self.changed_resource_refs)):
            raise ValueError("changedResourceRefs must use canonical sorted order")
        if self.changed_resource_refs_digest != canonical_resource_refs_digest(
            self.changed_resource_refs
        ):
            raise ValueError("changedResourceRefsDigest does not match changedResourceRefs")
        expected_view_digest = canonical_workspace_view_binding_digest(
            workspace_id=self.workspace_id,
            workspace_ref=self.workspace_ref,
            authority_partition_id=self.authority_partition_id,
            generation=self.expected_generation,
            state_root=self.state_root_before,
        )
        if self.workspace_view_binding_digest != expected_view_digest:
            raise ValueError(
                "workspaceViewBindingDigest does not match the expected workspace view"
            )
        return self


def canonical_workspace_commit_decision_request_digest(
    request: WorkspaceCommitDecisionRequest,
) -> str:
    if type(request) is not WorkspaceCommitDecisionRequest:
        raise TypeError("request must be an exact WorkspaceCommitDecisionRequest")
    return _canonical_model_digest(WorkspaceCommitDecisionRequest.model_validate(request))


def _workspace_commit_decision_event_payload(
    request: WorkspaceCommitDecisionRequest,
) -> dict[str, object]:
    return {
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
        "requestDigest": canonical_workspace_commit_decision_request_digest(request),
        **(
            {"stagedTransactionDigest": request.staged_transaction_digest}
            if request.staged_transaction_digest is not None
            else {}
        ),
        "stagingManifestDigest": request.staging_manifest_digest,
        "stateRootAfter": request.state_root_after,
        "stateRootBefore": request.state_root_before,
        "targetGeneration": request.target_generation,
        "transactionId": request.transaction_id,
        "workspaceId": request.workspace_id,
        "workspaceViewBindingDigest": request.workspace_view_binding_digest,
    }


class WorkspaceCommitSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: WorkspaceCommitDecisionRequest
    state: Literal["decided", "published", "quarantined"]
    active_fencing_token: int = Field(alias="activeFencingToken", ge=1, strict=True)
    active_fence_event_id: str = Field(alias="activeFenceEventId", min_length=1)
    active_fence_event_sequence: int = Field(
        alias="activeFenceEventSequence",
        ge=1,
        strict=True,
    )
    active_fence_event_hash: str = Field(alias="activeFenceEventHash")
    commit_compare_version: int = Field(alias="commitCompareVersion", ge=1, strict=True)


class WorkspaceCommitDecision(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    snapshot: WorkspaceCommitSnapshot
    staged_transaction: WorkspaceTransactionResult | None = Field(
        default=None,
        alias="stagedTransaction",
    )
    workspace_compare_version: int = Field(alias="workspaceCompareVersion", ge=1, strict=True)
    commit_event: JournalEvent = Field(alias="commitEvent")

    @model_validator(mode="after")
    def _require_decided_snapshot(self) -> Self:
        if self.snapshot.state != "decided":
            raise ValueError("workspace commit decision requires a decided snapshot")
        request = self.snapshot.request
        staged = self.staged_transaction
        if staged is None:
            if self.commit_event.action_id != request.action_id:
                raise ValueError("commitEvent.actionId does not match commit request")
            return self
        staged_request = staged.request
        staged_bindings: tuple[tuple[str, object, object], ...] = (
            ("transactionId", request.transaction_id, staged_request.transaction_id),
            ("workspaceId", request.workspace_id, staged.workspace_id),
            ("workspaceRef", request.workspace_ref, staged_request.workspace_ref),
            (
                "authorityPartitionId",
                request.authority_partition_id,
                staged_request.authority_partition_id,
            ),
            ("actionId", request.action_id, staged_request.action_id),
            ("attemptId", request.attempt_id, staged_request.attempt_id),
            ("expectedGeneration", request.expected_generation, staged.expected_generation),
            ("targetGeneration", request.target_generation, staged.target_generation),
            ("stateRootBefore", request.state_root_before, staged.state_root_before),
            ("stateRootAfter", request.state_root_after, staged.state_root_after),
            ("mutationPlanDigest", request.mutation_plan_digest, staged.mutation_plan_digest),
            (
                "stagingManifestDigest",
                request.staging_manifest_digest,
                staged_request.staging_manifest_digest,
            ),
            (
                "changedResourceRefsDigest",
                request.changed_resource_refs_digest,
                staged_request.changed_resource_refs_digest,
            ),
            ("changedResourceRefs", request.changed_resource_refs, staged.changed_resource_refs),
            (
                "workspaceViewBindingDigest",
                request.workspace_view_binding_digest,
                staged_request.workspace_view_binding_digest,
            ),
            (
                "expectedTransactionCompareVersion",
                request.expected_transaction_compare_version,
                staged.transaction_compare_version,
            ),
            (
                "stagedTransactionDigest",
                request.staged_transaction_digest,
                staged.result_digest,
            ),
        )
        for alias, observed, expected in staged_bindings:
            if observed != expected:
                raise ValueError(f"commit request {alias} does not match stagedTransaction")
        if self.snapshot.active_fencing_token != request.decision_fencing_token:
            raise ValueError("commit snapshot fence does not match decision request")
        if self.snapshot.commit_compare_version != 1:
            raise ValueError("initial commitCompareVersion must equal 1")
        if self.workspace_compare_version != (request.expected_workspace_compare_version + 1):
            raise ValueError("workspaceCompareVersion must advance the expected workspace version")
        event = self.commit_event
        bindings = (
            ("eventType", event.event_type, "workspace.commit_decided"),
            ("actionId", event.action_id, request.action_id),
            ("attemptId", event.attempt_id, request.attempt_id),
            (
                "partitionId",
                event.partition_id,
                request.authority_partition_id,
            ),
            ("fencingToken", event.fencing_token, request.decision_fencing_token),
            ("taskContractId", event.task_contract_id, staged.task_contract_id),
            ("taskVersion", event.task_version, staged.task_version),
            ("taskContractDigest", event.task_contract_digest, staged.task_contract_digest),
            ("completionEpochId", event.completion_epoch_id, staged.completion_epoch_id),
            ("admissionSequence", event.admission_sequence, staged.admission_sequence),
            ("authorityContractId", event.authority_contract_id, staged.authority_contract_id),
            ("requestDigest", event.request_digest, staged.staged_event.request_digest),
            (
                "idempotencyKeyDigest",
                event.idempotency_key_digest,
                staged.staged_event.idempotency_key_digest,
            ),
            ("actorId", event.actor_id, staged.staged_event.actor_id),
            ("policyDigest", event.policy_digest, staged.staged_event.policy_digest),
            ("causationId", event.causation_id, staged.staged_event.event_id),
            ("correlationId", event.correlation_id, staged.staged_event.correlation_id),
            ("identityDigest", event.identity_digest, staged.staged_event.identity_digest),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"commitEvent.{alias} does not match commit request")
        snapshot_event_bindings: tuple[tuple[str, object, object], ...] = (
            ("activeFenceEventId", self.snapshot.active_fence_event_id, event.event_id),
            (
                "activeFenceEventSequence",
                self.snapshot.active_fence_event_sequence,
                event.sequence,
            ),
            (
                "activeFenceEventHash",
                self.snapshot.active_fence_event_hash,
                event.event_hash,
            ),
        )
        for binding_alias, binding_observed, binding_expected in snapshot_event_bindings:
            if binding_observed != binding_expected:
                raise ValueError(
                    f"commit snapshot {binding_alias} does not match commitEvent"
                )
        if _strict_json_loads(event.payload_json) != _workspace_commit_decision_event_payload(
            request
        ):
            raise ValueError("commitEvent payload does not bind the exact commit request")
        _require_direct_event_successor(
            staged.staged_event,
            event,
            first_name="stagedEvent",
            second_name="commitEvent",
        )
        return self


def canonical_workspace_commit_decision_digest(
    decision: WorkspaceCommitDecision,
) -> str:
    if type(decision) is not WorkspaceCommitDecision:
        raise TypeError("decision must be an exact WorkspaceCommitDecision")
    return _canonical_model_digest(WorkspaceCommitDecision.model_validate(decision))


class WorkspaceCommitRecoveryClaimRequest(EnvelopeModel):
    """CAS input for taking over an already-decided workspace commit."""

    schema_id: Literal["magi.workspace_commit_recovery_claim_request.v1"] = Field(
        default="magi.workspace_commit_recovery_claim_request.v1",
        alias="schemaId",
    )
    claim_id: str = Field(alias="claimId", min_length=1)
    commit_id: str = Field(alias="commitId", min_length=1)
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    recovery_owner_id: str = Field(alias="recoveryOwnerId", min_length=1)
    expected_workspace_compare_version: int = Field(
        alias="expectedWorkspaceCompareVersion",
        ge=1,
        strict=True,
    )
    expected_commit_compare_version: int = Field(
        alias="expectedCommitCompareVersion",
        ge=1,
        strict=True,
    )
    expected_active_fencing_token: int = Field(
        alias="expectedActiveFencingToken",
        ge=1,
        strict=True,
    )
    expected_active_fence_event_id: str = Field(
        alias="expectedActiveFenceEventId",
        min_length=1,
    )
    expected_active_fence_event_sequence: int = Field(
        alias="expectedActiveFenceEventSequence",
        ge=1,
        strict=True,
    )
    expected_active_fence_event_hash: str = Field(alias="expectedActiveFenceEventHash")
    new_fencing_token: int = Field(alias="newFencingToken", ge=1, strict=True)
    workspace_view_binding_digest: str = Field(alias="workspaceViewBindingDigest")
    recovery_decision: RecoveryDecision = Field(alias="recoveryDecision")
    recovery_plan: PartitionRecoveryPlan = Field(alias="recoveryPlan")
    recovery_lease: LeaseSnapshot = Field(alias="recoveryLease")

    @model_validator(mode="after")
    def _require_newer_fence(self) -> Self:
        if self.new_fencing_token <= self.expected_active_fencing_token:
            raise ValueError("newFencingToken must exceed expectedActiveFencingToken")
        decision = self.recovery_decision
        plan = self.recovery_plan
        lease = self.recovery_lease
        if decision.disposition is not RecoveryDisposition.REDO_COMMIT:
            raise ValueError("workspace recovery claim requires a REDO_COMMIT decision")
        if decision.resolution_attempt_id is None:
            raise ValueError("REDO_COMMIT decision requires a resolution attempt")
        bindings: tuple[tuple[str, object, object], ...] = (
            ("recoveryPlanDigest", decision.recovery_plan_digest, plan.recovery_plan_digest),
            ("partitionId", decision.partition_id, self.authority_partition_id),
            ("plan.partitionId", plan.partition_id, self.authority_partition_id),
            ("recoveryOwnerId", decision.recovery_owner_id, self.recovery_owner_id),
            ("lease.ownerId", lease.owner_id, self.recovery_owner_id),
            ("lease.partitionId", lease.partition_id, self.authority_partition_id),
            ("lease.leaseName", lease.lease_name, decision.recovery_lease_name),
            ("recoveryFencingToken", decision.recovery_fencing_token, self.new_fencing_token),
            ("lease.fencingToken", lease.fencing_token, self.new_fencing_token),
            ("taskContractDigest", plan.task_contract_digest, decision.task_contract_digest),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"recovery claim {alias} binding does not match")
        if lease.state is not LeaseState.HELD:
            raise ValueError("workspace recovery claim requires a held recovery lease")
        if decision.source_attempt_id not in plan.selected_source_attempt_ids:
            raise ValueError("recovery plan must select the decision source attempt")
        return self


def _workspace_commit_recovery_claim_payload(
    request: WorkspaceCommitRecoveryClaimRequest,
    decision_request: WorkspaceCommitDecisionRequest,
) -> dict[str, object]:
    return {
        "actionId": decision_request.action_id,
        "activeFence": request.new_fencing_token,
        "attemptId": decision_request.attempt_id,
        "authorityPartitionId": decision_request.authority_partition_id,
        "claimId": request.claim_id,
        "commitId": request.commit_id,
        "expectedCommitCompareVersion": request.expected_commit_compare_version,
        "expectedActiveFenceEventHash": request.expected_active_fence_event_hash,
        "expectedActiveFenceEventId": request.expected_active_fence_event_id,
        "expectedActiveFenceEventSequence": request.expected_active_fence_event_sequence,
        "expectedWorkspaceCompareVersion": request.expected_workspace_compare_version,
        "priorActiveFence": request.expected_active_fencing_token,
        "recoveryDecisionDigest": request.recovery_decision.decision_digest,
        "recoveryEpochId": request.recovery_decision.recovery_epoch_id,
        "recoveryLeaseCompareVersion": request.recovery_lease.compare_version,
        "recoveryLeaseExpiresAt": request.recovery_lease.expires_at.isoformat()
        if request.recovery_lease.expires_at is not None
        else None,
        "recoveryOwnerId": request.recovery_owner_id,
        "recoveryPlanDigest": request.recovery_plan.recovery_plan_digest,
        "resolutionAttemptId": request.recovery_decision.resolution_attempt_id,
        "sourceAttemptId": request.recovery_decision.source_attempt_id,
        "stateRootAfter": decision_request.state_root_after,
        "stateRootBefore": decision_request.state_root_before,
        "targetGeneration": decision_request.target_generation,
        "transactionId": decision_request.transaction_id,
        "workspaceId": request.workspace_id,
        "workspaceViewBindingDigest": request.workspace_view_binding_digest,
    }


class WorkspaceCommitRecoveryClaim(EnvelopeModel):
    """Store-derived fence takeover without rewriting the original decision."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: WorkspaceCommitRecoveryClaimRequest
    original_decision: WorkspaceCommitDecision = Field(alias="originalDecision")
    prior_snapshot: WorkspaceCommitSnapshot = Field(alias="priorSnapshot")
    prior_fence_event: JournalEvent = Field(alias="priorFenceEvent")
    snapshot: WorkspaceCommitSnapshot
    workspace_compare_version: int = Field(alias="workspaceCompareVersion", ge=1, strict=True)
    claim_event: JournalEvent = Field(alias="claimEvent")
    claim_digest: str | None = Field(default=None, alias="claimDigest")

    @model_validator(mode="after")
    def _validate_recovery_claim(self) -> Self:
        request = self.request
        original = self.original_decision
        decision_request = original.snapshot.request
        recovery_decision = request.recovery_decision
        recovery_plan = request.recovery_plan

        request_bindings = (
            ("commitId", request.commit_id, decision_request.commit_id),
            ("workspaceId", request.workspace_id, decision_request.workspace_id),
            (
                "authorityPartitionId",
                request.authority_partition_id,
                decision_request.authority_partition_id,
            ),
            (
                "workspaceViewBindingDigest",
                request.workspace_view_binding_digest,
                decision_request.workspace_view_binding_digest,
            ),
        )
        for alias, observed, expected in request_bindings:
            if observed != expected:
                raise ValueError(f"recovery claim {alias} does not match original decision")
        recovery_bindings: tuple[tuple[str, object, object], ...] = (
            ("actionId", recovery_decision.action_id, decision_request.action_id),
            (
                "sourceAttemptId",
                recovery_decision.source_attempt_id,
                decision_request.attempt_id,
            ),
            (
                "taskContractDigest",
                recovery_decision.task_contract_digest,
                original.staged_transaction.task_contract_digest,
            ),
            ("partitionId", recovery_decision.partition_id, request.authority_partition_id),
            ("plan.partitionId", recovery_plan.partition_id, request.authority_partition_id),
        )
        for binding_alias, binding_observed, binding_expected in recovery_bindings:
            if binding_observed != binding_expected:
                raise ValueError(
                    f"recovery claim {binding_alias} does not match original decision"
                )

        if self.prior_snapshot.request != decision_request:
            raise ValueError("priorSnapshot must preserve the immutable commit request")
        if self.prior_snapshot.state != "decided":
            raise ValueError("priorSnapshot must be an active decided commit")
        if self.prior_snapshot.commit_compare_version != request.expected_commit_compare_version:
            raise ValueError("priorSnapshot commit version does not match claim request")
        if self.prior_snapshot.active_fencing_token != request.expected_active_fencing_token:
            raise ValueError("priorSnapshot active fence does not match claim request")
        if self.prior_snapshot.active_fence_event_id != request.expected_active_fence_event_id:
            raise ValueError("priorSnapshot active fence event does not match claim request")
        if (
            self.prior_snapshot.active_fence_event_sequence
            != request.expected_active_fence_event_sequence
            or self.prior_snapshot.active_fence_event_hash
            != request.expected_active_fence_event_hash
        ):
            raise ValueError("priorSnapshot active fence event provenance does not match claim")
        prior_event_bindings: tuple[tuple[str, object, object], ...] = (
            ("eventId", self.prior_fence_event.event_id, self.prior_snapshot.active_fence_event_id),
            (
                "sequence",
                self.prior_fence_event.sequence,
                self.prior_snapshot.active_fence_event_sequence,
            ),
            (
                "eventHash",
                self.prior_fence_event.event_hash,
                self.prior_snapshot.active_fence_event_hash,
            ),
        )
        for binding_alias, binding_observed, binding_expected in prior_event_bindings:
            if binding_observed != binding_expected:
                raise ValueError(
                    f"priorFenceEvent.{binding_alias} does not match priorSnapshot"
                )
        if request.expected_workspace_compare_version < original.workspace_compare_version:
            raise ValueError("claim request cannot precede the original workspace decision")
        if request.expected_active_fencing_token < decision_request.decision_fencing_token:
            raise ValueError("claim request cannot regress the original decision fence")
        if request.expected_active_fencing_token == decision_request.decision_fencing_token:
            if self.prior_snapshot != original.snapshot:
                raise ValueError("first recovery claim priorSnapshot must be the decision snapshot")
            if request.expected_active_fence_event_id != original.commit_event.event_id:
                raise ValueError("first recovery claim must follow the commit decision event")
            if self.prior_fence_event != original.commit_event:
                raise ValueError("first recovery claim priorFenceEvent must be commitEvent")

        if self.snapshot.request != decision_request:
            raise ValueError("recovery snapshot must preserve the immutable commit request")
        if self.snapshot.state != "decided":
            raise ValueError("recovery snapshot must keep the commit decided")
        if self.snapshot.active_fencing_token != request.new_fencing_token:
            raise ValueError("recovery snapshot active fence does not match claim request")
        if self.snapshot.commit_compare_version != request.expected_commit_compare_version + 1:
            raise ValueError("recovery snapshot must advance commitCompareVersion exactly once")
        if self.workspace_compare_version != request.expected_workspace_compare_version + 1:
            raise ValueError("recovery claim must advance workspaceCompareVersion exactly once")

        event = self.claim_event
        event_bindings: tuple[tuple[str, object, object], ...] = (
            ("eventType", event.event_type, "workspace.commit_recovery_claimed"),
            ("partitionId", event.partition_id, decision_request.authority_partition_id),
            ("actionId", event.action_id, decision_request.action_id),
            (
                "attemptId",
                event.attempt_id,
                recovery_decision.resolution_attempt_id,
            ),
            ("fencingToken", event.fencing_token, request.new_fencing_token),
            ("actorId", event.actor_id, request.recovery_owner_id),
            ("causationId", event.causation_id, request.expected_active_fence_event_id),
        )
        for claim_alias, claim_observed, claim_expected in event_bindings:
            if claim_observed != claim_expected:
                raise ValueError(f"claimEvent.{claim_alias} does not match recovery claim")
        snapshot_event_bindings = (
            ("activeFenceEventId", self.snapshot.active_fence_event_id, event.event_id),
            (
                "activeFenceEventSequence",
                self.snapshot.active_fence_event_sequence,
                event.sequence,
            ),
            (
                "activeFenceEventHash",
                self.snapshot.active_fence_event_hash,
                event.event_hash,
            ),
        )
        for binding_alias, binding_observed, binding_expected in snapshot_event_bindings:
            if binding_observed != binding_expected:
                raise ValueError(
                    f"recovery snapshot {binding_alias} does not match claimEvent"
                )

        source_event = original.commit_event
        inherited_event_bindings: tuple[tuple[str, object, object], ...] = (
            ("taskContractId", event.task_contract_id, source_event.task_contract_id),
            ("taskVersion", event.task_version, source_event.task_version),
            (
                "taskContractDigest",
                event.task_contract_digest,
                source_event.task_contract_digest,
            ),
            (
                "completionEpochId",
                event.completion_epoch_id,
                source_event.completion_epoch_id,
            ),
            ("admissionSequence", event.admission_sequence, source_event.admission_sequence),
            (
                "authorityContractId",
                event.authority_contract_id,
                source_event.authority_contract_id,
            ),
            ("requestDigest", event.request_digest, source_event.request_digest),
            (
                "idempotencyKeyDigest",
                event.idempotency_key_digest,
                source_event.idempotency_key_digest,
            ),
            ("policyDigest", event.policy_digest, source_event.policy_digest),
            ("correlationId", event.correlation_id, source_event.correlation_id),
            ("identityDigest", event.identity_digest, source_event.identity_digest),
        )
        for inherited_alias, inherited_observed, inherited_expected in inherited_event_bindings:
            if inherited_observed != inherited_expected:
                raise ValueError(
                    f"claimEvent.{inherited_alias} does not match original decision event"
                )
        _require_direct_event_successor(
            self.prior_fence_event,
            event,
            first_name="priorFenceEvent",
            second_name="claimEvent",
        )
        lease_expiry = request.recovery_lease.expires_at
        if lease_expiry is None or event.created_at >= lease_expiry:
            raise ValueError("claimEvent must be created before the recovery lease expires")
        if _strict_json_loads(event.payload_json) != _workspace_commit_recovery_claim_payload(
            request,
            decision_request,
        ):
            raise ValueError("claimEvent payload does not bind the recovery claim")

        expected_digest = _canonical_model_digest(
            self,
            exclude=frozenset({"claim_digest"}),
        )
        if self.claim_digest is not None and self.claim_digest != expected_digest:
            raise ValueError("claimDigest does not match the recovery claim")
        object.__setattr__(self, "claim_digest", expected_digest)
        return self


def canonical_workspace_commit_recovery_claim_digest(
    claim: WorkspaceCommitRecoveryClaim,
) -> str:
    if type(claim) is not WorkspaceCommitRecoveryClaim:
        raise TypeError("claim must be an exact WorkspaceCommitRecoveryClaim")
    validated = WorkspaceCommitRecoveryClaim.model_validate(claim)
    return _canonical_model_digest(
        validated,
        exclude=frozenset({"claim_digest"}),
    )


class WorkspacePublicationObservation(EnvelopeModel):
    """Filesystem durability result before the Journal advances authority state."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    commit_decision: WorkspaceCommitDecision = Field(alias="commitDecision")
    recovery_claim: WorkspaceCommitRecoveryClaim | None = Field(
        default=None,
        alias="recoveryClaim",
    )
    active_commit_snapshot: WorkspaceCommitSnapshot | None = Field(
        default=None,
        alias="activeCommitSnapshot",
    )
    commit_id: str = Field(alias="commitId", min_length=1)
    commit_decision_digest: str | None = Field(default=None, alias="commitDecisionDigest")
    transaction_id: str = Field(alias="transactionId", min_length=1)
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    workspace_ref: str = Field(alias="workspaceRef", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    expected_workspace_compare_version: int = Field(
        alias="expectedWorkspaceCompareVersion",
        ge=1,
        strict=True,
    )
    expected_commit_compare_version: int = Field(
        alias="expectedCommitCompareVersion",
        ge=1,
        strict=True,
    )
    active_fencing_token: int = Field(alias="activeFencingToken", ge=1, strict=True)
    active_fence_event_id: str = Field(alias="activeFenceEventId", min_length=1)
    published_generation: int = Field(alias="publishedGeneration", ge=1, strict=True)
    state_root_before: str = Field(alias="stateRootBefore")
    state_root_after: str = Field(alias="stateRootAfter")
    changed_resource_refs: tuple[str, ...] = Field(
        alias="changedResourceRefs",
        min_length=1,
    )
    changed_resource_refs_digest: str = Field(alias="changedResourceRefsDigest")
    workspace_view_binding_digest: str = Field(alias="workspaceViewBindingDigest")
    durability_evidence_digest: str = Field(alias="durabilityEvidenceDigest")
    observation_refs: tuple[str, ...] = Field(alias="observationRefs", min_length=1)
    publication_observation_digest: str | None = Field(
        default=None,
        alias="publicationObservationDigest",
    )

    @field_validator("changed_resource_refs", "observation_refs", mode="before")
    @classmethod
    def _require_ordered_publication_sequences(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("workspace publication sequences must use a list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_publication_resources(self) -> Self:
        decision = self.commit_decision
        expected_decision_digest = canonical_workspace_commit_decision_digest(decision)
        if (
            self.commit_decision_digest is not None
            and self.commit_decision_digest != expected_decision_digest
        ):
            raise ValueError("commitDecisionDigest does not match commitDecision")
        object.__setattr__(self, "commit_decision_digest", expected_decision_digest)

        claim = self.recovery_claim
        if claim is None:
            expected_snapshot = decision.snapshot
            expected_workspace_version = decision.workspace_compare_version
            expected_attempt_id = decision.snapshot.request.attempt_id
        else:
            if claim.original_decision != decision:
                raise ValueError("recoveryClaim does not preserve commitDecision")
            expected_snapshot = claim.snapshot
            expected_workspace_version = claim.workspace_compare_version
            recovery_attempt_id = claim.request.recovery_decision.resolution_attempt_id
            if recovery_attempt_id is None:
                raise ValueError("recoveryClaim requires a resolution attempt")
            expected_attempt_id = recovery_attempt_id
        if self.active_commit_snapshot is not None and (
            self.active_commit_snapshot != expected_snapshot
        ):
            raise ValueError("activeCommitSnapshot does not match the active commit authority")
        object.__setattr__(self, "active_commit_snapshot", expected_snapshot)

        snapshot = expected_snapshot
        if snapshot.state != "decided":
            raise ValueError("activeCommitSnapshot must be a decided commit")
        request = snapshot.request
        snapshot_bindings: tuple[tuple[str, object, object], ...] = (
            ("commitId", self.commit_id, request.commit_id),
            ("transactionId", self.transaction_id, request.transaction_id),
            ("workspaceId", self.workspace_id, request.workspace_id),
            ("workspaceRef", self.workspace_ref, request.workspace_ref),
            (
                "authorityPartitionId",
                self.authority_partition_id,
                request.authority_partition_id,
            ),
            ("actionId", self.action_id, request.action_id),
            ("attemptId", self.attempt_id, expected_attempt_id),
            (
                "expectedWorkspaceCompareVersion",
                self.expected_workspace_compare_version,
                expected_workspace_version,
            ),
            (
                "expectedCommitCompareVersion",
                self.expected_commit_compare_version,
                snapshot.commit_compare_version,
            ),
            (
                "activeFencingToken",
                self.active_fencing_token,
                snapshot.active_fencing_token,
            ),
            (
                "activeFenceEventId",
                self.active_fence_event_id,
                snapshot.active_fence_event_id,
            ),
            ("publishedGeneration", self.published_generation, request.target_generation),
            ("stateRootBefore", self.state_root_before, request.state_root_before),
            ("stateRootAfter", self.state_root_after, request.state_root_after),
            (
                "changedResourceRefs",
                self.changed_resource_refs,
                request.changed_resource_refs,
            ),
            (
                "changedResourceRefsDigest",
                self.changed_resource_refs_digest,
                request.changed_resource_refs_digest,
            ),
        )
        for alias, observed, expected in snapshot_bindings:
            if observed != expected:
                raise ValueError(f"publication {alias} does not match activeCommitSnapshot")
        if snapshot.active_fencing_token < decision.snapshot.active_fencing_token:
            raise ValueError("publication active fence cannot regress commitDecision")
        _require_canonical_workspace_root_ref(self.workspace_ref)
        _require_workspace_resources_share_root(
            self.workspace_ref,
            self.changed_resource_refs,
        )
        if len(self.changed_resource_refs) != len(set(self.changed_resource_refs)):
            raise ValueError("changedResourceRefs must be unique")
        if self.changed_resource_refs != tuple(sorted(self.changed_resource_refs)):
            raise ValueError("changedResourceRefs must use canonical sorted order")
        if self.changed_resource_refs_digest != canonical_resource_refs_digest(
            self.changed_resource_refs
        ):
            raise ValueError("changedResourceRefsDigest does not match changedResourceRefs")
        if self.state_root_before == self.state_root_after:
            raise ValueError("workspace publication must change the state root")
        expected_view_digest = canonical_workspace_view_binding_digest(
            workspace_id=self.workspace_id,
            workspace_ref=self.workspace_ref,
            authority_partition_id=self.authority_partition_id,
            generation=self.published_generation,
            state_root=self.state_root_after,
        )
        if self.workspace_view_binding_digest != expected_view_digest:
            raise ValueError("workspaceViewBindingDigest does not match published workspace view")
        if len(self.observation_refs) != len(set(self.observation_refs)):
            raise ValueError("observationRefs must be unique")
        expected_digest = _workspace_publication_observation_digest_from_model(self)
        if (
            self.publication_observation_digest is not None
            and self.publication_observation_digest != expected_digest
        ):
            raise ValueError("publicationObservationDigest does not match publication observation")
        object.__setattr__(self, "publication_observation_digest", expected_digest)
        return self


def _workspace_publication_observation_digest_from_model(
    observation: WorkspacePublicationObservation,
) -> str:
    field_names = set(WorkspacePublicationObservation.model_fields) - {
        "publication_observation_digest"
    }
    payload = WorkspacePublicationObservation.model_dump(
        observation,
        by_alias=True,
        mode="json",
        include=field_names,
    )
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_workspace_publication_observation_digest(
    observation: WorkspacePublicationObservation,
) -> str:
    if type(observation) is not WorkspacePublicationObservation:
        raise TypeError("observation must be an exact WorkspacePublicationObservation")
    validated = WorkspacePublicationObservation.model_validate(observation)
    return _workspace_publication_observation_digest_from_model(validated)


def _workspace_publication_source_event(
    observation: WorkspacePublicationObservation,
) -> JournalEvent:
    if observation.recovery_claim is not None:
        return observation.recovery_claim.claim_event
    return observation.commit_decision.commit_event


def _workspace_publication_event_payload(
    receipt: WorkspacePublicationReceipt,
) -> dict[str, object]:
    recovery_claim_digest = (
        canonical_workspace_commit_recovery_claim_digest(receipt.recovery_claim)
        if receipt.recovery_claim is not None
        else None
    )
    return {
        "actionId": receipt.action_id,
        "activeFence": receipt.active_fencing_token,
        "activeFenceEventId": receipt.active_fence_event_id,
        "attemptId": receipt.attempt_id,
        "authorityPartitionId": receipt.authority_partition_id,
        "changedResourceRefsDigest": receipt.changed_resource_refs_digest,
        "commitCompareVersion": receipt.commit_compare_version,
        "commitDecisionDigest": receipt.commit_decision_digest,
        "commitId": receipt.commit_id,
        "durabilityEvidenceDigest": receipt.durability_evidence_digest,
        "expectedCommitCompareVersion": receipt.expected_commit_compare_version,
        "expectedWorkspaceCompareVersion": receipt.expected_workspace_compare_version,
        "observationRefs": list(receipt.observation_refs),
        "publicationObservationDigest": receipt.publication_observation_digest,
        "publishedGeneration": receipt.published_generation,
        "recoveryClaimDigest": recovery_claim_digest,
        "stateRootAfter": receipt.state_root_after,
        "stateRootBefore": receipt.state_root_before,
        "transactionId": receipt.transaction_id,
        "workspaceCompareVersion": receipt.workspace_compare_version,
        "workspaceId": receipt.workspace_id,
        "workspaceRef": receipt.workspace_ref,
        "workspaceViewBindingDigest": receipt.workspace_view_binding_digest,
    }


class WorkspacePublicationReceipt(WorkspacePublicationObservation):
    workspace_compare_version: int = Field(alias="workspaceCompareVersion", ge=1, strict=True)
    commit_compare_version: int = Field(alias="commitCompareVersion", ge=1, strict=True)
    publication_event: JournalEvent = Field(alias="publicationEvent")
    workspace_snapshot: WorkspaceSnapshot = Field(alias="workspaceSnapshot")
    commit_snapshot: WorkspaceCommitSnapshot = Field(alias="commitSnapshot")

    @model_validator(mode="after")
    def _validate_publication_event(self) -> Self:
        event = self.publication_event
        source_event = _workspace_publication_source_event(self)
        if event.event_type != "workspace.published":
            raise ValueError("publicationEvent.eventType must be workspace.published")
        if event.partition_id != self.authority_partition_id:
            raise ValueError("publicationEvent.partitionId does not match authority partition")
        event_bindings: tuple[tuple[str, object, object], ...] = (
            ("actionId", event.action_id, self.action_id),
            ("attemptId", event.attempt_id, self.attempt_id),
            (
                "fencingToken",
                event.fencing_token,
                self.active_fencing_token,
            ),
            ("taskContractId", event.task_contract_id, source_event.task_contract_id),
            ("taskVersion", event.task_version, source_event.task_version),
            ("taskContractDigest", event.task_contract_digest, source_event.task_contract_digest),
            ("completionEpochId", event.completion_epoch_id, source_event.completion_epoch_id),
            ("admissionSequence", event.admission_sequence, source_event.admission_sequence),
            ("authorityContractId", event.authority_contract_id, source_event.authority_contract_id),
            ("requestDigest", event.request_digest, source_event.request_digest),
            (
                "idempotencyKeyDigest",
                event.idempotency_key_digest,
                source_event.idempotency_key_digest,
            ),
            ("actorId", event.actor_id, source_event.actor_id),
            ("policyDigest", event.policy_digest, source_event.policy_digest),
            ("causationId", event.causation_id, source_event.event_id),
            ("correlationId", event.correlation_id, source_event.correlation_id),
            ("identityDigest", event.identity_digest, source_event.identity_digest),
        )
        for alias, observed, expected in event_bindings:
            if observed != expected:
                raise ValueError(f"publicationEvent.{alias} does not match observation")
        _require_direct_event_successor(
            source_event,
            event,
            first_name="activeFenceEvent",
            second_name="publicationEvent",
        )
        if self.workspace_compare_version != self.expected_workspace_compare_version + 1:
            raise ValueError("workspaceCompareVersion must advance the expected version")
        if self.commit_compare_version != self.expected_commit_compare_version + 1:
            raise ValueError("commitCompareVersion must advance the expected version")
        if _strict_json_loads(event.payload_json) != _workspace_publication_event_payload(self):
            raise ValueError("publicationEvent payload does not bind the exact publication receipt")

        workspace = self.workspace_snapshot
        workspace_bindings: tuple[tuple[str, object, object], ...] = (
            ("workspaceId", workspace.workspace_id, self.workspace_id),
            ("workspaceRef", workspace.workspace_ref, self.workspace_ref),
            (
                "authorityPartitionId",
                workspace.authority_partition_id,
                self.authority_partition_id,
            ),
            ("currentGeneration", workspace.current_generation, self.published_generation),
            ("stateRoot", workspace.state_root, self.state_root_after),
            (
                "workspaceViewBindingDigest",
                workspace.workspace_view_binding_digest,
                self.workspace_view_binding_digest,
            ),
            ("publicationState", workspace.publication_state, WorkspacePublicationState.READY),
            ("compareVersion", workspace.compare_version, self.workspace_compare_version),
        )
        for alias, observed, expected in workspace_bindings:
            if observed != expected:
                raise ValueError(f"workspaceSnapshot.{alias} does not match publication")

        active_snapshot = self.active_commit_snapshot
        assert active_snapshot is not None
        commit = self.commit_snapshot
        commit_bindings: tuple[tuple[str, object, object], ...] = (
            ("request", commit.request, active_snapshot.request),
            ("state", commit.state, "published"),
            ("activeFencingToken", commit.active_fencing_token, self.active_fencing_token),
            ("activeFenceEventId", commit.active_fence_event_id, event.event_id),
            ("activeFenceEventSequence", commit.active_fence_event_sequence, event.sequence),
            ("activeFenceEventHash", commit.active_fence_event_hash, event.event_hash),
            ("commitCompareVersion", commit.commit_compare_version, self.commit_compare_version),
        )
        for alias, observed, expected in commit_bindings:
            if observed != expected:
                raise ValueError(f"commitSnapshot.{alias} does not match publication")
        return self


class WorkspaceQuarantineReceipt(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    workspace_id: str = Field(alias="workspaceId", min_length=1)
    commit_id: str | None = Field(default=None, alias="commitId", min_length=1)
    authority_partition_id: str = Field(alias="authorityPartitionId", min_length=1)
    reason_digest: str = Field(alias="reasonDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    quarantined_at: datetime = Field(alias="quarantinedAt")
    expected_workspace_compare_version: int | None = Field(
        default=None,
        alias="expectedWorkspaceCompareVersion",
        ge=0,
        strict=True,
    )
    prior_workspace_snapshot: WorkspaceSnapshot | None = Field(
        default=None,
        alias="priorWorkspaceSnapshot",
    )
    workspace_compare_version: int = Field(alias="workspaceCompareVersion", ge=1, strict=True)
    prior_commit_snapshot: WorkspaceCommitSnapshot | None = Field(
        default=None,
        alias="priorCommitSnapshot",
    )
    commit_compare_version: int | None = Field(
        default=None,
        alias="commitCompareVersion",
        ge=1,
        strict=True,
    )
    prior_fence_event: JournalEvent | None = Field(
        default=None,
        alias="priorFenceEvent",
    )
    quarantine_event: JournalEvent | None = Field(default=None, alias="quarantineEvent")

    @model_validator(mode="after")
    def _validate_quarantine_commit_binding(self) -> Self:
        workspace = self.prior_workspace_snapshot
        if workspace is None or self.quarantine_event is None:
            if (
                self.commit_id is None
                and self.fencing_token == 0
                and self.expected_workspace_compare_version is None
                and workspace is None
                and self.quarantine_event is None
            ):
                return self
            raise ValueError("quarantine receipt requires workspace snapshot and event evidence")
        assert self.expected_workspace_compare_version is not None
        workspace_bindings: tuple[tuple[str, object, object], ...] = (
            ("workspaceId", workspace.workspace_id, self.workspace_id),
            (
                "authorityPartitionId",
                workspace.authority_partition_id,
                self.authority_partition_id,
            ),
            (
                "compareVersion",
                workspace.compare_version,
                self.expected_workspace_compare_version,
            ),
        )
        for alias, observed, expected in workspace_bindings:
            if observed != expected:
                raise ValueError(f"priorWorkspaceSnapshot.{alias} does not match quarantine")
        if workspace.publication_state is WorkspacePublicationState.QUARANTINED:
            raise ValueError("priorWorkspaceSnapshot must not already be quarantined")
        if self.workspace_compare_version != self.expected_workspace_compare_version + 1:
            raise ValueError("workspaceCompareVersion must advance exactly once")

        commit_fields = (
            self.prior_commit_snapshot,
            self.commit_compare_version,
            self.prior_fence_event,
        )
        if self.commit_id is None:
            if any(value is not None for value in commit_fields):
                raise ValueError(
                    "workspace-only quarantine cannot contain commit-specific bindings"
                )
            if self.fencing_token != 0:
                raise ValueError("workspace-only quarantine requires fencingToken zero")
            event = self.quarantine_event
            workspace_event_bindings: tuple[tuple[str, object, object], ...] = (
                ("eventType", event.event_type, "workspace.quarantined"),
                ("partitionId", event.partition_id, self.authority_partition_id),
                ("actionId", event.action_id, None),
                ("attemptId", event.attempt_id, None),
                ("fencingToken", event.fencing_token, 0),
                (
                    "causationId",
                    event.causation_id,
                    f"workspace:{self.workspace_id}:v{self.expected_workspace_compare_version}",
                ),
                ("createdAt", event.created_at, self.quarantined_at),
            )
            for alias, observed, expected in workspace_event_bindings:
                if observed != expected:
                    raise ValueError(
                        f"quarantineEvent.{alias} does not match workspace quarantine"
                    )
            expected_workspace_payload: dict[str, object] = {
                "expectedWorkspaceCompareVersion": self.expected_workspace_compare_version,
                "priorGeneration": workspace.current_generation,
                "priorPublicationState": workspace.publication_state.value,
                "priorStateRoot": workspace.state_root,
                "priorWorkspaceSnapshotDigest": _canonical_model_digest(workspace),
                "quarantinedAt": self.quarantined_at.isoformat(),
                "reasonDigest": self.reason_digest,
                "workspaceCompareVersion": self.workspace_compare_version,
                "workspaceId": self.workspace_id,
                "workspaceRef": workspace.workspace_ref,
            }
            if _strict_json_loads(event.payload_json) != expected_workspace_payload:
                raise ValueError(
                    "quarantineEvent payload does not bind exact workspace quarantine"
                )
            return self

        if self.fencing_token <= 0:
            raise ValueError("commit quarantine requires a positive fencingToken")
        if any(value is None for value in commit_fields):
            raise ValueError(
                "commit quarantine requires the prior snapshot, event, and CAS versions"
            )

        prior = self.prior_commit_snapshot
        event = self.quarantine_event
        commit_version = self.commit_compare_version
        prior_event = self.prior_fence_event
        assert prior is not None
        assert prior_event is not None
        assert commit_version is not None

        request = prior.request
        snapshot_bindings: tuple[tuple[str, object, object], ...] = (
            ("commitId", self.commit_id, request.commit_id),
            ("workspaceId", self.workspace_id, request.workspace_id),
            (
                "authorityPartitionId",
                self.authority_partition_id,
                request.authority_partition_id,
            ),
            ("fencingToken", self.fencing_token, prior.active_fencing_token),
        )
        for alias, observed, expected in snapshot_bindings:
            if observed != expected:
                raise ValueError(f"quarantine receipt {alias} does not match priorCommitSnapshot")
        publishing_bindings: tuple[tuple[str, object, object], ...] = (
            ("publicationState", workspace.publication_state, WorkspacePublicationState.PUBLISHING),
            ("activeCommitId", workspace.active_commit_id, request.commit_id),
            ("pendingGeneration", workspace.pending_generation, request.target_generation),
            ("pendingStateRoot", workspace.pending_state_root, request.state_root_after),
            ("activeFencingToken", workspace.active_fencing_token, prior.active_fencing_token),
        )
        for alias, observed, expected in publishing_bindings:
            if observed != expected:
                raise ValueError(f"priorWorkspaceSnapshot.{alias} does not match active commit")
        if prior.state == "quarantined":
            raise ValueError("priorCommitSnapshot must be an active commit")
        if commit_version != prior.commit_compare_version + 1:
            raise ValueError("commitCompareVersion must advance exactly once")

        commit_event_bindings: tuple[tuple[str, object, object], ...] = (
            ("eventType", event.event_type, "workspace.quarantined"),
            ("partitionId", event.partition_id, request.authority_partition_id),
            ("actionId", event.action_id, request.action_id),
            ("attemptId", event.attempt_id, request.attempt_id),
            ("fencingToken", event.fencing_token, prior.active_fencing_token),
            ("causationId", event.causation_id, prior.active_fence_event_id),
            ("createdAt", event.created_at, self.quarantined_at),
        )
        for alias, observed, expected in commit_event_bindings:
            if observed != expected:
                raise ValueError(f"quarantineEvent.{alias} does not match the quarantine receipt")
        prior_event_bindings: tuple[tuple[str, object, object], ...] = (
            ("eventId", prior_event.event_id, prior.active_fence_event_id),
            ("sequence", prior_event.sequence, prior.active_fence_event_sequence),
            ("eventHash", prior_event.event_hash, prior.active_fence_event_hash),
        )
        for alias, observed, expected in prior_event_bindings:
            if observed != expected:
                raise ValueError(f"priorFenceEvent.{alias} does not match priorCommitSnapshot")
        _require_direct_event_successor(
            prior_event,
            event,
            first_name="priorFenceEvent",
            second_name="quarantineEvent",
        )

        expected_commit_payload: dict[str, object] = {
            "actionId": request.action_id,
            "activeFence": prior.active_fencing_token,
            "attemptId": request.attempt_id,
            "commitId": request.commit_id,
            "commitCompareVersion": commit_version,
            "expectedCommitCompareVersion": prior.commit_compare_version,
            "expectedWorkspaceCompareVersion": self.expected_workspace_compare_version,
            "priorActiveFenceEventHash": prior.active_fence_event_hash,
            "priorActiveFenceEventId": prior.active_fence_event_id,
            "priorActiveFenceEventSequence": prior.active_fence_event_sequence,
            "priorCommitRequestDigest": (
                canonical_workspace_commit_decision_request_digest(request)
            ),
            "priorCommitState": prior.state,
            "priorWorkspaceSnapshotDigest": _canonical_model_digest(workspace),
            "quarantinedAt": self.quarantined_at.isoformat(),
            "reasonDigest": self.reason_digest,
            "transactionId": request.transaction_id,
            "workspaceCompareVersion": self.workspace_compare_version,
            "workspaceId": request.workspace_id,
        }
        if _strict_json_loads(event.payload_json) != expected_commit_payload:
            raise ValueError("quarantineEvent payload does not bind the exact quarantine receipt")
        return self


class RequiredProjection(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    projection_id: str = Field(alias="projectionId", min_length=1)


def canonical_required_projections_digest(
    projections: tuple[RequiredProjection, ...],
) -> str:
    if type(projections) is not tuple:
        raise TypeError("required projections must be an exact tuple")
    if not projections:
        raise ValueError("required projections must not be empty")
    validated: list[RequiredProjection] = []
    for projection in projections:
        if type(projection) is not RequiredProjection:
            raise TypeError("required projections must use exact RequiredProjection values")
        validated.append(RequiredProjection.model_validate(projection))
    keys = tuple((projection.partition_id, projection.projection_id) for projection in validated)
    if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
        raise ValueError("required projections must be unique and sorted by key")
    payload = {
        "requiredProjections": [
            projection.model_dump(by_alias=True, mode="json") for projection in validated
        ]
    }
    return "sha256:" + sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class PartitionRecoveryPlan(EnvelopeModel):
    """Immutable source/projection scope for one recoverable gate epoch."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    recovery_epoch_id: str = Field(alias="recoveryEpochId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    selected_source_attempt_ids: tuple[str, ...] = Field(
        alias="selectedSourceAttemptIds",
    )
    required_projections: tuple[RequiredProjection, ...] = Field(
        alias="requiredProjections",
    )
    recovery_plan_digest: str | None = Field(
        default=None,
        alias="recoveryPlanDigest",
    )

    @field_validator(
        "selected_source_attempt_ids",
        "required_projections",
        mode="before",
    )
    @classmethod
    def _require_ordered_plan_sequences(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if type(value) not in (list, tuple):
            raise ValueError(f"{info.field_name} must use an ordered list or tuple")
        return value

    @model_validator(mode="after")
    def _validate_stable_plan(self) -> Self:
        if not self.selected_source_attempt_ids and not self.required_projections:
            raise ValueError("a recovery plan must select a source or projection")
        if self.selected_source_attempt_ids != tuple(
            sorted(self.selected_source_attempt_ids)
        ) or len(self.selected_source_attempt_ids) != len(set(self.selected_source_attempt_ids)):
            raise ValueError("selectedSourceAttemptIds must be unique and sorted")
        projection_keys = tuple(
            (item.partition_id, item.projection_id) for item in self.required_projections
        )
        if projection_keys != tuple(sorted(projection_keys)) or len(projection_keys) != len(
            set(projection_keys)
        ):
            raise ValueError("requiredProjections must be unique and sorted by key")
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"recovery_plan_digest"}),
        )
        if self.recovery_plan_digest is not None and self.recovery_plan_digest != expected:
            raise ValueError("recoveryPlanDigest does not match PartitionRecoveryPlan")
        object.__setattr__(self, "recovery_plan_digest", expected)
        return self


# Workspace recovery claim contracts are declared before the recovery-plan
# section so the workspace transaction state machine remains contiguous.
WorkspaceCommitRecoveryClaimRequest.model_rebuild()
WorkspaceCommitRecoveryClaim.model_rebuild()


class RecoverySessionSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    plan: PartitionRecoveryPlan
    state: Literal["recovering", "completed", "quarantined"]
    owner_id: str = Field(alias="ownerId", min_length=1)
    fencing_token: int = Field(alias="fencingToken", ge=1, strict=True)
    resolved_source_attempt_ids: tuple[str, ...] = Field(
        alias="resolvedSourceAttemptIds",
    )
    recovered_projection_digest: str | None = Field(
        default=None,
        alias="recoveredProjectionDigest",
    )
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_recovery_progress(self) -> Self:
        resolved = self.resolved_source_attempt_ids
        if resolved != tuple(sorted(resolved)) or len(resolved) != len(set(resolved)):
            raise ValueError("resolvedSourceAttemptIds must be unique and sorted")
        if not set(resolved).issubset(self.plan.selected_source_attempt_ids):
            raise ValueError("resolved source attempts must belong to the recovery plan")
        if self.state == "completed":
            if resolved != self.plan.selected_source_attempt_ids:
                raise ValueError("completed recovery must resolve every selected source")
            if self.plan.required_projections and self.recovered_projection_digest is None:
                raise ValueError("completed recovery must bind recovered projection cursors")
        return self


class EpochSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    task_partition_id: str = Field(alias="taskPartitionId", min_length=1)
    task_contract_binding: TaskContractBinding = Field(alias="taskContractBinding")
    state: CompletionEpochState
    last_admission_sequence: int = Field(
        alias="lastAdmissionSequence",
        ge=0,
        strict=True,
    )
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_task_partition(self) -> Self:
        expected = (
            f"task:{self.task_contract_binding.task_contract_id}:"
            f"{self.task_contract_binding.task_version}"
        )
        if self.task_partition_id != expected:
            raise ValueError("taskPartitionId does not match taskContractBinding")
        return self


class EpochSeal(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    task_partition_id: str = Field(alias="taskPartitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    task_contract_snapshot_ref: str = Field(alias="taskContractSnapshotRef")
    barrier_admission_sequence: int = Field(
        alias="barrierAdmissionSequence",
        ge=0,
        strict=True,
    )
    epoch_compare_version: int = Field(alias="epochCompareVersion", ge=1, strict=True)
    required_projection_registry_digest: str | None = Field(
        default=None,
        alias="requiredProjectionRegistryDigest",
    )
    required_projection_digest: str = Field(alias="requiredProjectionDigest")
    required_projections: tuple[RequiredProjection, ...] = Field(
        alias="requiredProjections",
        min_length=1,
    )
    sealed_at: datetime = Field(alias="sealedAt")

    @model_validator(mode="after")
    def _require_unique_sorted_projection_keys(self) -> Self:
        keys = tuple(
            (projection.partition_id, projection.projection_id)
            for projection in self.required_projections
        )
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("requiredProjections must be unique and sorted by key")
        expected_digest = canonical_required_projections_digest(self.required_projections)
        if self.required_projection_digest != expected_digest:
            raise ValueError("requiredProjectionDigest does not match requiredProjections")
        if (
            self.required_projection_registry_digest is not None
            and self.required_projection_registry_digest != expected_digest
        ):
            raise ValueError("requiredProjectionRegistryDigest does not match requiredProjections")
        object.__setattr__(self, "required_projection_registry_digest", expected_digest)
        return self


class ResearchClaimResult(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    claim_id: str = Field(alias="claimId", min_length=1)
    proposition_digest: str = Field(alias="propositionDigest")
    state: Literal[
        "satisfied",
        "unsatisfied",
        "conflicted",
        "insufficient_evidence",
        "blocked",
    ]
    evidence_ids: tuple[str, ...] = Field(alias="evidenceIds")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @field_validator("evidence_ids", "reason_codes")
    @classmethod
    def _reject_empty_reference_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _reject_empty_string_items(
            value,
            field_name=info.field_name or "reference values",
        )

    @model_validator(mode="after")
    def _validate_atomic_claim_result(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("research claim evidence IDs must be unique")
        if self.state == "satisfied" and not self.evidence_ids:
            raise ValueError("satisfied research claims require evidence")
        return self


class RequirementResult(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    requirement_id: str = Field(alias="requirementId", min_length=1)
    state: RequirementState
    evidence_ids: tuple[str, ...] = Field(alias="evidenceIds")
    research_claims: tuple[ResearchClaimResult, ...] = Field(
        default=(),
        alias="researchClaims",
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @field_validator("evidence_ids", "reason_codes")
    @classmethod
    def _reject_empty_reference_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _reject_empty_string_items(
            value,
            field_name=info.field_name or "reference values",
        )

    @model_validator(mode="after")
    def _require_evidence_for_satisfaction(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("requirement evidence IDs must be unique")
        if self.state is RequirementState.SATISFIED and not self.evidence_ids:
            raise ValueError("satisfied requirements require evidence")
        claim_ids = tuple(result.claim_id for result in self.research_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("research claim results must be unique")
        claim_evidence = {
            evidence_id for result in self.research_claims for evidence_id in result.evidence_ids
        }
        if not claim_evidence.issubset(self.evidence_ids):
            raise ValueError("research claim evidence must be included in requirement evidence")
        return self


class CompletionVerdict(EnvelopeModel):
    schema_id: Literal["magi.completion_verdict.v1"] = Field(
        default="magi.completion_verdict.v1",
        alias="schemaId",
    )
    completion_id: str = Field(alias="completionId", min_length=1)
    verdict_digest: str | None = Field(default=None, alias="verdictDigest")
    finalization_id: str = Field(alias="finalizationId", min_length=1)
    finalization_request_digest: str = Field(alias="finalizationRequestDigest")
    response_claim_manifest_digest: str = Field(alias="responseClaimManifestDigest")
    status: CompletionStatus
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    task_contract_snapshot_ref: str = Field(alias="taskContractSnapshotRef")
    task_partition_id: str = Field(alias="taskPartitionId", min_length=1)
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    state_root: str = Field(alias="stateRoot")
    evidence_root: str = Field(alias="evidenceRoot")
    barrier_admission_sequence: int = Field(
        alias="barrierAdmissionSequence",
        ge=0,
        strict=True,
    )
    required_projection_registry_digest: str | None = Field(
        default=None,
        alias="requiredProjectionRegistryDigest",
    )
    required_projection_digest: str = Field(alias="requiredProjectionDigest")
    projection_cursors: tuple[ProjectionCursorBinding, ...] = Field(
        alias="projectionCursors",
        min_length=1,
    )
    requirements: tuple[RequirementResult, ...]
    included_action_ids: tuple[str, ...] = Field(alias="includedActionIds")
    response_digest: str = Field(alias="responseDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")

    @field_validator("included_action_ids", "reason_codes")
    @classmethod
    def _reject_empty_reference_values(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _reject_empty_string_items(
            value,
            field_name=info.field_name or "reference values",
        )

    @model_validator(mode="after")
    def _validate_terminal_verdict_vector(self) -> Self:
        expected_partition = f"task:{self.task_contract_id}:{self.task_version}"
        if self.task_partition_id != expected_partition:
            raise ValueError("taskPartitionId does not match task identity")
        if self.task_contract_snapshot_ref != (f"authority-task://{self.task_contract_digest}"):
            raise ValueError("taskContractSnapshotRef does not match taskContractDigest")
        cursor_keys = tuple(
            (cursor.partition_id, cursor.projection_id) for cursor in self.projection_cursors
        )
        if cursor_keys != tuple(sorted(cursor_keys)) or len(cursor_keys) != len(set(cursor_keys)):
            raise ValueError("projectionCursors must be unique and sorted by key")
        required_projections = tuple(
            RequiredProjection(
                partitionId=cursor.partition_id,
                projectionId=cursor.projection_id,
            )
            for cursor in self.projection_cursors
        )
        expected_projection_digest = canonical_required_projections_digest(required_projections)
        if self.required_projection_digest != expected_projection_digest:
            raise ValueError("requiredProjectionDigest does not match projectionCursors")
        if (
            self.required_projection_registry_digest is not None
            and self.required_projection_registry_digest != expected_projection_digest
        ):
            raise ValueError("requiredProjectionRegistryDigest does not match projectionCursors")
        object.__setattr__(
            self,
            "required_projection_registry_digest",
            expected_projection_digest,
        )
        if any(
            result.state in {RequirementState.PENDING, RequirementState.SUPERSEDED}
            for result in self.requirements
        ):
            raise ValueError("completion verdict cannot contain unresolved requirements")
        if self.status is CompletionStatus.COMPLETE and any(
            result.state is not RequirementState.SATISFIED for result in self.requirements
        ):
            raise ValueError("complete verdict requires every requirement satisfied")
        if self.status is CompletionStatus.COMPLETE and not self.requirements:
            raise ValueError("complete verdict requires at least one proven requirement")
        requirement_ids = tuple(result.requirement_id for result in self.requirements)
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("completion requirement results must be unique")
        if self.included_action_ids != tuple(sorted(self.included_action_ids)) or len(
            self.included_action_ids
        ) != len(set(self.included_action_ids)):
            raise ValueError("includedActionIds must be unique and sorted")
        if any(cursor.state_root != self.state_root for cursor in self.projection_cursors):
            raise ValueError("projection cursor stateRoot does not match CompletionVerdict")
        expected_digest = _canonical_model_digest(
            self,
            exclude=frozenset({"verdict_digest"}),
        )
        if self.verdict_digest is not None and self.verdict_digest != expected_digest:
            raise ValueError("verdictDigest does not match CompletionVerdict")
        object.__setattr__(self, "verdict_digest", expected_digest)
        return self


def validate_finalization_request_epoch(
    epoch: EpochSnapshot,
    request: FinalizationRequest,
) -> FinalizationRequest:
    """Bind finalization bytes to the durable epoch snapshot before evaluation."""

    if type(epoch) is not EpochSnapshot:
        raise TypeError("epoch must be an exact EpochSnapshot")
    if type(request) is not FinalizationRequest:
        raise TypeError("request must be an exact FinalizationRequest")
    validated_epoch = EpochSnapshot.model_validate(epoch)
    validated_request = FinalizationRequest.model_validate(request)
    if validated_epoch.state is not CompletionEpochState.SEALING:
        raise ValueError("durable EpochSnapshot must be in SEALING state")
    if validated_request.barrier_admission_sequence != validated_epoch.last_admission_sequence:
        raise ValueError(
            "FinalizationRequest barrierAdmissionSequence does not match durable EpochSnapshot"
        )
    binding = validated_epoch.task_contract_binding
    task = validated_request.task_contract
    checks = (
        (
            "completionEpochId",
            validated_request.completion_epoch_id,
            validated_epoch.completion_epoch_id,
        ),
        (
            "taskPartitionId",
            validated_request.task_partition_id,
            validated_epoch.task_partition_id,
        ),
        ("taskContractId", task.task_contract_id, binding.task_contract_id),
        ("taskVersion", task.version, binding.task_version),
        (
            "taskContractDigest",
            validated_request.task_contract_digest,
            binding.task_contract_digest,
        ),
        (
            "taskContractSnapshotRef",
            validated_request.task_contract_snapshot_ref,
            binding.task_contract_snapshot_ref,
        ),
    )
    for alias, observed, expected in checks:
        if observed != expected:
            raise ValueError(f"FinalizationRequest {alias} does not match durable EpochSnapshot")
    return validated_request


class FinalizationEvaluationRequest(EnvelopeModel):
    """Structurally validated input accepted by completion evaluators."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    epoch: EpochSnapshot
    seal: EpochSeal
    request: FinalizationRequest
    projection_cursors: tuple[ProjectionCursorBinding, ...] = Field(
        alias="projectionCursors",
        min_length=1,
    )
    evaluation_digest: str | None = Field(default=None, alias="evaluationDigest")

    @model_validator(mode="after")
    def _bind_durable_epoch_before_evaluation(self) -> Self:
        validate_finalization_request_epoch(self.epoch, self.request)
        seal_bindings = (
            (
                "completionEpochId",
                self.seal.completion_epoch_id,
                self.epoch.completion_epoch_id,
            ),
            ("taskPartitionId", self.seal.task_partition_id, self.epoch.task_partition_id),
            (
                "taskContractDigest",
                self.seal.task_contract_digest,
                self.request.task_contract_digest,
            ),
            (
                "taskContractSnapshotRef",
                self.seal.task_contract_snapshot_ref,
                self.request.task_contract_snapshot_ref,
            ),
            (
                "barrierAdmissionSequence",
                self.seal.barrier_admission_sequence,
                self.epoch.last_admission_sequence,
            ),
        )
        for alias, observed, expected in seal_bindings:
            if observed != expected:
                raise ValueError(f"EpochSeal {alias} does not match evaluation epoch")
        if self.seal.epoch_compare_version != self.epoch.compare_version + 1:
            raise ValueError("EpochSeal must advance the durable epoch compareVersion")

        cursor_keys = tuple(
            (cursor.partition_id, cursor.projection_id) for cursor in self.projection_cursors
        )
        seal_keys = tuple(
            (projection.partition_id, projection.projection_id)
            for projection in self.seal.required_projections
        )
        if cursor_keys != seal_keys:
            raise ValueError("projectionCursors must exactly cover the sealed projection keys")
        if any(cursor.state_root != self.request.state_root for cursor in self.projection_cursors):
            raise ValueError("projectionCursors must bind the finalization stateRoot")
        expected = _canonical_model_digest(
            self,
            exclude=frozenset({"evaluation_digest"}),
        )
        if self.evaluation_digest is not None and self.evaluation_digest != expected:
            raise ValueError("evaluationDigest does not match finalization evaluation input")
        object.__setattr__(self, "evaluation_digest", expected)
        return self


def validate_completion_persistence_contract(
    seal: EpochSeal,
    request: FinalizationRequest,
    verdict: CompletionVerdict,
) -> CompletionVerdict:
    """Cross-check the three immutable objects consumed by atomic persistence."""

    if type(seal) is not EpochSeal:
        raise TypeError("seal must be an exact EpochSeal")
    if type(request) is not FinalizationRequest:
        raise TypeError("request must be an exact FinalizationRequest")
    if type(verdict) is not CompletionVerdict:
        raise TypeError("verdict must be an exact CompletionVerdict")
    validated_seal = EpochSeal.model_validate(seal)
    validated_request = FinalizationRequest.model_validate(request)
    validated_verdict = CompletionVerdict.model_validate(verdict)

    seal_request_bindings = (
        ("completionEpochId", "completion_epoch_id"),
        ("taskPartitionId", "task_partition_id"),
        ("taskContractDigest", "task_contract_digest"),
        ("taskContractSnapshotRef", "task_contract_snapshot_ref"),
        ("barrierAdmissionSequence", "barrier_admission_sequence"),
    )
    for alias, attribute in seal_request_bindings:
        if getattr(validated_seal, attribute) != getattr(
            validated_request,
            attribute,
        ):
            raise ValueError(f"FinalizationRequest {alias} does not match EpochSeal")

    request_verdict_bindings = (
        ("finalizationId", "finalization_id", "finalization_id"),
        (
            "finalizationRequestDigest",
            "finalization_request_digest",
            "finalization_request_digest",
        ),
        (
            "responseClaimManifestDigest",
            "response_claim_manifest_digest",
            "response_claim_manifest_digest",
        ),
        ("taskContractDigest", "task_contract_digest", "task_contract_digest"),
        (
            "taskContractSnapshotRef",
            "task_contract_snapshot_ref",
            "task_contract_snapshot_ref",
        ),
        ("taskPartitionId", "task_partition_id", "task_partition_id"),
        ("completionEpochId", "completion_epoch_id", "completion_epoch_id"),
        ("stateRoot", "state_root", "state_root"),
        ("evidenceRoot", "evidence_root", "evidence_root"),
        (
            "barrierAdmissionSequence",
            "barrier_admission_sequence",
            "barrier_admission_sequence",
        ),
    )
    for alias, request_attribute, verdict_attribute in request_verdict_bindings:
        if getattr(validated_request, request_attribute) != getattr(
            validated_verdict,
            verdict_attribute,
        ):
            raise ValueError(f"CompletionVerdict {alias} does not match request")
    if (
        validated_verdict.task_contract_id != validated_request.task_contract.task_contract_id
        or validated_verdict.task_version != validated_request.task_contract.version
    ):
        raise ValueError("CompletionVerdict Task Contract identity does not match request")
    if (
        validated_verdict.response_digest
        != validated_request.claim_manifest.candidate_response_digest
    ):
        raise ValueError("CompletionVerdict responseDigest does not match request")
    if (
        validated_verdict.required_projection_registry_digest
        != validated_seal.required_projection_registry_digest
        or validated_verdict.required_projection_digest != validated_seal.required_projection_digest
    ):
        raise ValueError("CompletionVerdict projection registry does not match EpochSeal")
    cursor_keys = tuple(
        (cursor.partition_id, cursor.projection_id)
        for cursor in validated_verdict.projection_cursors
    )
    seal_keys = tuple(
        (projection.partition_id, projection.projection_id)
        for projection in validated_seal.required_projections
    )
    if cursor_keys != seal_keys:
        raise ValueError("CompletionVerdict cursor keys do not match EpochSeal")
    expected_requirement_ids = tuple(
        requirement.requirement_id
        for requirement in validated_request.task_contract.requirements
        if requirement.state is not RequirementState.SUPERSEDED
    )
    observed_requirement_ids = tuple(
        result.requirement_id for result in validated_verdict.requirements
    )
    if observed_requirement_ids != expected_requirement_ids:
        raise ValueError("CompletionVerdict requirements do not exactly cover the Task Contract")
    task_requirements = {
        requirement.requirement_id: requirement
        for requirement in validated_request.task_contract.requirements
        if requirement.state is not RequirementState.SUPERSEDED
    }
    for result in validated_verdict.requirements:
        research = task_requirements[result.requirement_id].proof.research
        if research is None:
            if result.research_claims:
                raise ValueError("non-research requirement cannot contain research claim results")
            continue
        expected_claims = tuple(
            (claim.claim_id, claim.proposition_digest) for claim in research.claims
        )
        observed_claims = tuple(
            (claim.claim_id, claim.proposition_digest) for claim in result.research_claims
        )
        if observed_claims != expected_claims:
            raise ValueError(
                "requirement research claim results do not exactly cover the Task Contract"
            )
        if result.state is RequirementState.SATISFIED and any(
            claim.state != "satisfied" for claim in result.research_claims
        ):
            raise ValueError("satisfied research requirement requires every research claim met")
    satisfied_evidence_ids = {
        evidence_id
        for result in validated_verdict.requirements
        if result.state is RequirementState.SATISFIED
        for evidence_id in result.evidence_ids
    }
    claimed_evidence_ids = {
        evidence_id
        for claim in validated_request.claim_manifest.segments
        if claim.claim_class != "limitation"
        for evidence_id in claim.evidence_ids
    }
    if validated_verdict.status is CompletionStatus.COMPLETE and not claimed_evidence_ids:
        raise ValueError("complete response requires at least one evidence-bearing claim")
    if validated_verdict.status is CompletionStatus.COMPLETE and any(
        health.status is not DependencyStatus.CLEAN
        for health in validated_request.dependency_health
    ):
        raise ValueError("complete verdict requires every dependency health to be clean")
    if not claimed_evidence_ids.issubset(satisfied_evidence_ids):
        raise ValueError("response claim evidence is not covered by satisfied requirements")
    if (
        any(
            claim.claim_class in {"execution", "artifact"}
            for claim in validated_request.claim_manifest.segments
        )
        and not validated_verdict.included_action_ids
    ):
        raise ValueError("execution or artifact claims require includedActionIds")
    return verdict


class CompletionPersistenceReceipt(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    completion_id: str = Field(alias="completionId", min_length=1)
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    verdict_digest: str = Field(alias="verdictDigest")
    response_digest: str = Field(alias="responseDigest")
    completion_event_id: str = Field(alias="completionEventId", min_length=1)
    completion_event_hash: str = Field(alias="completionEventHash")
    outbox_id: str = Field(alias="outboxId", min_length=1)
    outbox_payload_digest: str = Field(alias="outboxPayloadDigest")
    epoch_compare_version: int = Field(alias="epochCompareVersion", ge=1, strict=True)
    outbox_compare_version: int = Field(alias="outboxCompareVersion", ge=0, strict=True)
    status: CompletionStatus
    terminal_state: CompletionEpochState = Field(alias="terminalState")
    completion_event: JournalEvent = Field(alias="completionEvent")
    outbox_item: OutboxItem = Field(alias="outboxItem")

    @model_validator(mode="after")
    def _require_terminal_epoch(self) -> Self:
        if self.terminal_state in {
            CompletionEpochState.OPEN,
            CompletionEpochState.SEALING,
        }:
            raise ValueError("completion persistence receipt requires a terminal epoch")
        if self.terminal_state.value != self.status.value:
            raise ValueError("completion status does not match terminal epoch state")
        event = self.completion_event
        event_bindings = (
            ("completionEventId", self.completion_event_id, event.event_id),
            ("completionEventHash", self.completion_event_hash, event.event_hash),
            ("taskContractDigest", self.task_contract_digest, event.task_contract_digest),
            ("completionEpochId", self.completion_epoch_id, event.completion_epoch_id),
        )
        for alias, expected, observed in event_bindings:
            if observed != expected:
                raise ValueError(f"completion event {alias} does not match receipt")
        if event.event_type != "completion.persisted":
            raise ValueError("completion event must be completion.persisted")
        if event.action_id is not None or event.attempt_id is not None:
            raise ValueError("completion event cannot bind an action or attempt")
        if event.authority_contract_id is not None:
            raise ValueError("completion event cannot bind an authority contract")
        if event.partition_id != self.outbox_item.partition_id:
            raise ValueError("completion event partition does not match outbox item")
        if event.correlation_id != self.completion_id:
            raise ValueError("completion event correlationId does not match completionId")

        expected_event_payload: dict[str, object] = {
            "completionId": self.completion_id,
            "epochCompareVersion": self.epoch_compare_version,
            "outboxCompareVersion": self.outbox_compare_version,
            "outboxId": self.outbox_id,
            "outboxPayloadDigest": self.outbox_payload_digest,
            "responseDigest": self.response_digest,
            "status": self.status.value,
            "terminalState": self.terminal_state.value,
            "verdictDigest": self.verdict_digest,
        }
        event_payload = _strict_json_loads(event.payload_json)
        if type(event_payload) is not dict:
            raise ValueError("completion event payload must be an object")
        if set(event_payload) != set(expected_event_payload):
            raise ValueError("completion event payload shape does not match receipt")
        for key, payload_expected in expected_event_payload.items():
            if event_payload[key] != payload_expected:
                raise ValueError(f"completion event payload {key} does not match receipt")

        outbox = self.outbox_item
        outbox_bindings = (
            ("outboxId", self.outbox_id, outbox.outbox_id),
            ("outboxPayloadDigest", self.outbox_payload_digest, outbox.payload_digest),
            ("outboxCompareVersion", self.outbox_compare_version, outbox.compare_version),
            ("completionId", self.completion_id, outbox.subject_id),
            ("verdictDigest", self.verdict_digest, outbox.subject_digest),
            ("completionEventId", self.completion_event_id, outbox.event_id),
            ("completionEventHash", self.completion_event_hash, outbox.event_hash),
            ("completionEventSequence", event.sequence, outbox.event_sequence),
        )
        for binding_alias, binding_expected, binding_observed in outbox_bindings:
            if binding_observed != binding_expected:
                raise ValueError(f"outbox item {binding_alias} does not match receipt")
        if outbox.kind != "final_response":
            raise ValueError("completion outbox item must use final_response kind")
        if outbox.state is not OutboxState.PENDING:
            raise ValueError("new completion outbox item must be pending")
        if outbox.delivery_attempt != 0:
            raise ValueError("new completion outbox item cannot have delivery attempts")
        if self.outbox_compare_version != 1:
            raise ValueError("new completion outbox item must start at compareVersion 1")

        expected_outbox_payload: dict[str, object] = {
            "completionId": self.completion_id,
            "responseDigest": self.response_digest,
            "verdictDigest": self.verdict_digest,
        }
        outbox_payload = _strict_json_loads(outbox.payload_json)
        if outbox_payload != expected_outbox_payload:
            raise ValueError("completion outbox payload does not exactly bind receipt")
        return self


def validate_completion_persistence_receipt(
    verdict: CompletionVerdict,
    receipt: CompletionPersistenceReceipt,
) -> CompletionPersistenceReceipt:
    if type(verdict) is not CompletionVerdict:
        raise TypeError("verdict must be an exact CompletionVerdict")
    if type(receipt) is not CompletionPersistenceReceipt:
        raise TypeError("receipt must be an exact CompletionPersistenceReceipt")
    validated_verdict = CompletionVerdict.model_validate(verdict)
    validated_receipt = CompletionPersistenceReceipt.model_validate(receipt)
    bindings = (
        ("completionId", validated_receipt.completion_id, validated_verdict.completion_id),
        (
            "completionEpochId",
            validated_receipt.completion_epoch_id,
            validated_verdict.completion_epoch_id,
        ),
        (
            "taskContractDigest",
            validated_receipt.task_contract_digest,
            validated_verdict.task_contract_digest,
        ),
        ("verdictDigest", validated_receipt.verdict_digest, validated_verdict.verdict_digest),
        ("responseDigest", validated_receipt.response_digest, validated_verdict.response_digest),
        ("status", validated_receipt.status, validated_verdict.status),
        (
            "taskContractId",
            validated_receipt.completion_event.task_contract_id,
            validated_verdict.task_contract_id,
        ),
        (
            "taskVersion",
            validated_receipt.completion_event.task_version,
            validated_verdict.task_version,
        ),
        (
            "taskPartitionId",
            validated_receipt.completion_event.partition_id,
            validated_verdict.task_partition_id,
        ),
        (
            "barrierAdmissionSequence",
            validated_receipt.completion_event.admission_sequence,
            validated_verdict.barrier_admission_sequence,
        ),
        (
            "finalizationRequestDigest",
            validated_receipt.completion_event.request_digest,
            validated_verdict.finalization_request_digest,
        ),
        (
            "finalizationId",
            validated_receipt.completion_event.causation_id,
            validated_verdict.finalization_id,
        ),
    )
    for alias, observed, expected in bindings:
        if observed != expected:
            raise ValueError(f"CompletionPersistenceReceipt {alias} does not match verdict")
    return validated_receipt


class ActionAdmission(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    admission_sequence: int = Field(alias="admissionSequence", ge=1, strict=True)
    epoch_compare_version: int = Field(alias="epochCompareVersion", ge=1, strict=True)
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion",
        ge=1,
        strict=True,
    )
    proposal: ActionProposal
    proposal_digest: str = Field(alias="proposalDigest")
    intent: ActionIntent
    action_intent_digest: str = Field(alias="actionIntentDigest")
    proposed_event: JournalEvent = Field(alias="proposedEvent")
    admission_event: JournalEvent = Field(alias="admissionEvent")

    @model_validator(mode="after")
    def _validate_intent_and_event_bindings(self) -> Self:
        if self.proposal_digest != canonical_action_proposal_digest(self.proposal):
            raise ValueError("proposalDigest does not match proposal")
        try:
            validate_same_action_identity(self.intent, self.proposal)
        except ValueError as exc:
            raise ValueError("proposal does not match admitted intent") from exc
        if self.action_intent_digest != canonical_action_intent_digest(self.intent):
            raise ValueError("actionIntentDigest does not match intent")
        if self.completion_epoch_id != self.intent.completion_epoch_id:
            raise ValueError("completionEpochId does not match intent")
        if self.admission_sequence != self.intent.admission_sequence:
            raise ValueError("admissionSequence does not match intent")
        for field_name, event, event_type in (
            ("proposedEvent", self.proposed_event, "action.proposed"),
            ("admissionEvent", self.admission_event, "action.admitted"),
        ):
            expected_bindings = (
                ("eventType", event.event_type, event_type),
                ("actionId", event.action_id, self.intent.action_id),
                ("attemptId", event.attempt_id, self.intent.attempt_id),
                ("partitionId", event.partition_id, self.intent.partition_id),
                (
                    "taskContractId",
                    event.task_contract_id,
                    self.intent.task_contract_id,
                ),
                ("taskVersion", event.task_version, self.intent.task_version),
                (
                    "taskContractDigest",
                    event.task_contract_digest,
                    self.intent.task_contract_digest,
                ),
                (
                    "completionEpochId",
                    event.completion_epoch_id,
                    self.intent.completion_epoch_id,
                ),
                (
                    "admissionSequence",
                    event.admission_sequence,
                    self.intent.admission_sequence,
                ),
                (
                    "requestDigest",
                    event.request_digest,
                    self.intent.normalized_input_digest,
                ),
                (
                    "idempotencyKeyDigest",
                    event.idempotency_key_digest,
                    self.intent.idempotency_key_digest,
                ),
                ("actorId", event.actor_id, self.intent.actor_id),
                ("identityDigest", event.identity_digest, self.intent.identity_digest),
                ("policyDigest", event.policy_digest, self.intent.policy_digest),
            )
            for alias, observed, expected in expected_bindings:
                if observed != expected:
                    raise ValueError(f"{field_name}.{alias} does not match intent")
        _require_direct_event_successor(
            self.proposed_event,
            self.admission_event,
            first_name="proposedEvent",
            second_name="admissionEvent",
        )
        return self


class ExecutionPreparation(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    intent: ActionIntent
    authority_contract: AuthorityContract = Field(alias="authorityContract")
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_intent_digest: str = Field(alias="actionIntentDigest")
    request_digest: str = Field(alias="requestDigest")
    authority_contract_id: str = Field(alias="authorityContractId", min_length=1)
    authority_contract_digest: str = Field(alias="authorityContractDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion",
        ge=1,
        strict=True,
    )
    authority_event: JournalEvent = Field(alias="authorityEvent")
    prepared_event: JournalEvent = Field(alias="preparedEvent")

    @model_validator(mode="after")
    def _validate_preparation_events(self) -> Self:
        expected_intent_digest = canonical_action_intent_digest(self.intent)
        if self.action_intent_digest != expected_intent_digest:
            raise ValueError("actionIntentDigest does not match the admitted intent")
        expected_authority_digest = canonical_authority_contract_digest(self.authority_contract)
        if self.authority_contract_digest != expected_authority_digest:
            raise ValueError("authorityContractDigest does not match authorityContract")
        context_bindings: tuple[tuple[str, object, object], ...] = (
            ("actionId", self.action_id, self.intent.action_id),
            ("attemptId", self.attempt_id, self.intent.attempt_id),
            ("partitionId", self.partition_id, self.intent.partition_id),
            ("taskContractDigest", self.task_contract_digest, self.intent.task_contract_digest),
            ("requestDigest", self.request_digest, self.intent.normalized_input_digest),
            (
                "authorityContractId",
                self.authority_contract_id,
                self.authority_contract.authority_contract_id,
            ),
            ("fencingToken", self.fencing_token, self.authority_contract.fencing_token),
            ("authority.actionId", self.authority_contract.action_id, self.intent.action_id),
            ("authority.attemptId", self.authority_contract.attempt_id, self.intent.attempt_id),
            (
                "authority.partitionId",
                self.authority_contract.authority_partition_id,
                self.intent.partition_id,
            ),
            (
                "authority.taskContractId",
                self.authority_contract.task_contract_id,
                self.intent.task_contract_id,
            ),
            (
                "authority.taskVersion",
                self.authority_contract.task_version,
                self.intent.task_version,
            ),
            (
                "authority.taskContractDigest",
                self.authority_contract.task_contract_digest,
                self.intent.task_contract_digest,
            ),
            (
                "authority.completionEpochId",
                self.authority_contract.completion_epoch_id,
                self.intent.completion_epoch_id,
            ),
            (
                "authority.normalizedRequestDigest",
                self.authority_contract.normalized_request_digest,
                self.intent.normalized_input_digest,
            ),
            (
                "authority.policyDigest",
                self.authority_contract.policy_digest,
                self.intent.policy_digest,
            ),
            ("authority.principalId", self.authority_contract.principal_id, self.intent.actor_id),
            (
                "authority.capabilities",
                self.authority_contract.capabilities,
                self.intent.capabilities,
            ),
            (
                "authority.workspaceViewBindingDigest",
                self.authority_contract.workspace_view_binding_digest,
                self.intent.workspace_view_binding_digest,
            ),
        )
        for alias, observed, expected in context_bindings:
            if observed != expected:
                raise ValueError(f"preparation {alias} does not match intent and authority")

        for field_name, event, event_type in (
            ("authorityEvent", self.authority_event, "action.authorized"),
            ("preparedEvent", self.prepared_event, "action.prepared"),
        ):
            bindings = (
                ("eventType", event.event_type, event_type),
                ("actionId", event.action_id, self.action_id),
                ("attemptId", event.attempt_id, self.attempt_id),
                ("partitionId", event.partition_id, self.partition_id),
                ("taskContractId", event.task_contract_id, self.intent.task_contract_id),
                ("taskVersion", event.task_version, self.intent.task_version),
                (
                    "taskContractDigest",
                    event.task_contract_digest,
                    self.task_contract_digest,
                ),
                (
                    "completionEpochId",
                    event.completion_epoch_id,
                    self.intent.completion_epoch_id,
                ),
                ("admissionSequence", event.admission_sequence, self.intent.admission_sequence),
                ("requestDigest", event.request_digest, self.request_digest),
                (
                    "idempotencyKeyDigest",
                    event.idempotency_key_digest,
                    self.intent.idempotency_key_digest,
                ),
                (
                    "authorityContractId",
                    event.authority_contract_id,
                    self.authority_contract_id,
                ),
                ("fencingToken", event.fencing_token, self.fencing_token),
                ("actorId", event.actor_id, self.intent.actor_id),
                ("identityDigest", event.identity_digest, self.intent.identity_digest),
                ("policyDigest", event.policy_digest, self.intent.policy_digest),
                ("correlationId", event.correlation_id, self.intent.run_id),
            )
            for alias, observed, expected in bindings:
                if observed != expected:
                    raise ValueError(f"{field_name}.{alias} does not match preparation")
        expected_payload: dict[str, object] = {
            "actionIntentDigest": expected_intent_digest,
            "authorityContractDigest": expected_authority_digest,
        }
        if self.authority_contract.decision_request_id is not None:
            resume_binding_digest = self.authority_contract.resume_binding_digest
            if resume_binding_digest is None:
                raise ValueError("user-approved authority requires resumeBindingDigest")
            expected_payload.update(
                {
                    "decisionRequestId": self.authority_contract.decision_request_id,
                    "resumeBindingDigest": resume_binding_digest,
                }
            )
        for field_name, event in (
            ("authorityEvent", self.authority_event),
            ("preparedEvent", self.prepared_event),
        ):
            if _strict_json_loads(event.payload_json) != expected_payload:
                raise ValueError(f"{field_name} payload does not bind preparation digests")
        _require_direct_event_successor(
            self.authority_event,
            self.prepared_event,
            first_name="authorityEvent",
            second_name="preparedEvent",
        )
        return self


class ExecutionStartRequest(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    preparation: ExecutionPreparation
    approval_consumption: UserApprovalConsumption | None = Field(
        default=None,
        alias="approvalConsumption",
    )
    action_id: str = Field(alias="actionId", min_length=1)
    attempt_id: str = Field(alias="attemptId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_intent_digest: str = Field(alias="actionIntentDigest")
    request_digest: str = Field(alias="requestDigest")
    authority_contract_id: str = Field(alias="authorityContractId", min_length=1)
    authority_contract_digest: str = Field(alias="authorityContractDigest")
    fencing_token: int = Field(alias="fencingToken", ge=0, strict=True)
    executor_id: str = Field(alias="executorId", min_length=1)
    executor_version: str = Field(alias="executorVersion", min_length=1)
    sandbox_profile_digest: str = Field(alias="sandboxProfileDigest")
    provider_id: str | None = Field(default=None, alias="providerId", min_length=1)
    provider_version: str | None = Field(default=None, alias="providerVersion", min_length=1)
    provider_capabilities_digest: str | None = Field(
        default=None,
        alias="providerCapabilitiesDigest",
    )
    execution_token_digest: str = Field(alias="executionTokenDigest")

    @model_validator(mode="after")
    def _validate_provider_identity(self) -> Self:
        provider_fields = (
            self.provider_id,
            self.provider_version,
            self.provider_capabilities_digest,
        )
        if any(value is not None for value in provider_fields) and not all(
            value is not None for value in provider_fields
        ):
            raise ValueError("execution-start provider identity is all-or-none")
        preparation_bindings = (
            ("actionId", self.action_id, self.preparation.action_id),
            ("attemptId", self.attempt_id, self.preparation.attempt_id),
            ("partitionId", self.partition_id, self.preparation.partition_id),
            (
                "taskContractDigest",
                self.task_contract_digest,
                self.preparation.task_contract_digest,
            ),
            (
                "actionIntentDigest",
                self.action_intent_digest,
                self.preparation.action_intent_digest,
            ),
            ("requestDigest", self.request_digest, self.preparation.request_digest),
            (
                "authorityContractId",
                self.authority_contract_id,
                self.preparation.authority_contract_id,
            ),
            (
                "authorityContractDigest",
                self.authority_contract_digest,
                self.preparation.authority_contract_digest,
            ),
            ("fencingToken", self.fencing_token, self.preparation.fencing_token),
            (
                "sandboxProfileDigest",
                self.sandbox_profile_digest,
                self.preparation.authority_contract.sandbox_profile_digest,
            ),
        )
        for alias, observed, expected in preparation_bindings:
            if observed != expected:
                raise ValueError(f"ExecutionStartRequest {alias} does not match preparation")
        if self.approval_consumption is not None:
            if self.preparation.authority_contract.decision_request_id is None:
                raise ValueError("approvalConsumption requires user-approved authority")
            if self.approval_consumption.preparation != self.preparation:
                raise ValueError("approvalConsumption does not contain preparation")
            if self.approval_consumption.authority_contract_digest != (
                self.authority_contract_digest
            ):
                raise ValueError("approvalConsumption authority does not match start request")
        return self


class ExecutionStart(ExecutionStartRequest):
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion",
        ge=1,
        strict=True,
    )
    executing_event: JournalEvent = Field(alias="executingEvent")

    @model_validator(mode="after")
    def _validate_executing_event(self) -> Self:
        event = self.executing_event
        predecessor = self.preparation.prepared_event
        predecessor_name = "preparedEvent"
        if self.approval_consumption is not None:
            predecessor = self.approval_consumption.consumed_event
            predecessor_name = "approvalConsumedEvent"
        bindings = (
            ("eventType", event.event_type, "action.executing"),
            ("actionId", event.action_id, self.action_id),
            ("attemptId", event.attempt_id, self.attempt_id),
            ("partitionId", event.partition_id, self.partition_id),
            (
                "taskContractDigest",
                event.task_contract_digest,
                self.task_contract_digest,
            ),
            ("requestDigest", event.request_digest, self.request_digest),
            (
                "authorityContractId",
                event.authority_contract_id,
                self.authority_contract_id,
            ),
            ("fencingToken", event.fencing_token, self.fencing_token),
            (
                "taskContractId",
                event.task_contract_id,
                self.preparation.intent.task_contract_id,
            ),
            ("taskVersion", event.task_version, self.preparation.intent.task_version),
            (
                "completionEpochId",
                event.completion_epoch_id,
                self.preparation.intent.completion_epoch_id,
            ),
            (
                "admissionSequence",
                event.admission_sequence,
                self.preparation.intent.admission_sequence,
            ),
            (
                "idempotencyKeyDigest",
                event.idempotency_key_digest,
                self.preparation.intent.idempotency_key_digest,
            ),
            ("actorId", event.actor_id, self.preparation.intent.actor_id),
            ("identityDigest", event.identity_digest, self.preparation.intent.identity_digest),
            ("policyDigest", event.policy_digest, self.preparation.intent.policy_digest),
            ("correlationId", event.correlation_id, self.preparation.intent.run_id),
            (
                "causationId",
                event.causation_id,
                predecessor.event_id,
            ),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"executingEvent.{alias} does not match start request")
        expected_payload = {
            "actionIntentDigest": self.action_intent_digest,
            "authorityContractDigest": self.authority_contract_digest,
            "preparedEventId": self.preparation.prepared_event.event_id,
            "preparedEventSequence": self.preparation.prepared_event.sequence,
            "preparedEventHash": self.preparation.prepared_event.event_hash,
            "executorId": self.executor_id,
            "executorVersion": self.executor_version,
            "sandboxProfileDigest": self.sandbox_profile_digest,
            "providerId": self.provider_id,
            "providerVersion": self.provider_version,
            "providerCapabilitiesDigest": self.provider_capabilities_digest,
            "executionGrantDigest": self.execution_token_digest,
        }
        if self.approval_consumption is not None:
            expected_payload.update(
                {
                    "approvalConsumedEventId": predecessor.event_id,
                    "approvalConsumedEventSequence": predecessor.sequence,
                    "approvalConsumedEventHash": predecessor.event_hash,
                }
            )
        if _strict_json_loads(event.payload_json) != expected_payload:
            raise ValueError("executingEvent payload does not bind execution start")
        _require_direct_event_successor(
            predecessor,
            event,
            first_name=predecessor_name,
            second_name="executingEvent",
        )
        if event.causation_id != predecessor.event_id:
            raise ValueError(f"executingEvent must be caused by {predecessor_name}")
        return self


class AttemptObservationRecording(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    receipt: ActionReceipt
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion",
        ge=1,
        strict=True,
    )
    observed_event: JournalEvent = Field(alias="observedEvent")
    terminal_event: JournalEvent = Field(alias="terminalEvent")

    @model_validator(mode="after")
    def _validate_observation_events(self) -> Self:
        if self.receipt.state is ActionState.VERIFIED:
            raise ValueError("attempt observation recording cannot directly produce VERIFIED")
        terminal_event_type = {
            ActionState.COMMITTED: "action.committed",
            ActionState.ABORTED: "action.aborted",
            ActionState.PARTIAL: "action.partial",
            ActionState.UNKNOWN: "action.unknown",
        }.get(self.receipt.state)
        if terminal_event_type is None:
            raise ValueError("attempt observation recording requires a terminal receipt")
        observation = self.receipt.observation
        for field_name, event, event_type in (
            ("observedEvent", self.observed_event, "action.observed"),
            ("terminalEvent", self.terminal_event, terminal_event_type),
        ):
            bindings = (
                ("eventType", event.event_type, event_type),
                ("actionId", event.action_id, observation.action_id),
                ("attemptId", event.attempt_id, observation.attempt_id),
                ("partitionId", event.partition_id, observation.partition_id),
                (
                    "taskContractDigest",
                    event.task_contract_digest,
                    observation.task_contract_digest,
                ),
                ("requestDigest", event.request_digest, observation.request_digest),
                ("fencingToken", event.fencing_token, observation.fencing_token),
            )
            for alias, observed, expected in bindings:
                if observed != expected:
                    raise ValueError(f"{field_name}.{alias} does not match backend observation")
        if self.observed_event.authority_contract_id != self.terminal_event.authority_contract_id:
            raise ValueError("attempt observation events disagree on authorityContractId")
        _require_direct_event_successor(
            self.observed_event,
            self.terminal_event,
            first_name="observedEvent",
            second_name="terminalEvent",
        )
        return self


class AttemptVerificationRecording(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    receipt: ActionReceipt
    binding: VerificationEvidenceBinding
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion",
        ge=1,
        strict=True,
    )
    verification_event: JournalEvent = Field(alias="verificationEvent")

    @model_validator(mode="after")
    def _validate_verification_binding_and_event(self) -> Self:
        if self.receipt.state is not ActionState.VERIFIED:
            raise ValueError("attempt verification recording requires a VERIFIED receipt")
        observation = self.receipt.observation
        binding_pairs = (
            ("actionId", self.binding.action_id, observation.action_id),
            ("attemptId", self.binding.attempt_id, observation.attempt_id),
            (
                "taskContractDigest",
                self.binding.task_contract_digest,
                observation.task_contract_digest,
            ),
            ("requestDigest", self.binding.request_digest, observation.request_digest),
            (
                "verifiedStateRoot",
                self.binding.verified_state_root,
                self.receipt.state_root_after,
            ),
        )
        for alias, observed, expected in binding_pairs:
            if observed != expected:
                raise ValueError(f"verification binding {alias} does not match receipt")
        event = self.verification_event
        event_pairs = (
            ("eventType", event.event_type, "action.verified"),
            ("actionId", event.action_id, observation.action_id),
            ("attemptId", event.attempt_id, observation.attempt_id),
            ("partitionId", event.partition_id, observation.partition_id),
            (
                "taskContractDigest",
                event.task_contract_digest,
                observation.task_contract_digest,
            ),
            ("requestDigest", event.request_digest, observation.request_digest),
            ("fencingToken", event.fencing_token, observation.fencing_token),
        )
        for event_alias, event_observed, event_expected in event_pairs:
            if event_observed != event_expected:
                raise ValueError(f"verificationEvent.{event_alias} does not match verified receipt")
        return self


class UserDecisionSnapshot(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: UserDecisionRequest
    request_json: str = Field(alias="requestJson")
    decision_request_digest: str = Field(alias="decisionRequestDigest")
    state: UserDecisionState
    approval_receipt_digest: str | None = Field(
        default=None,
        alias="approvalReceiptDigest",
    )
    latest_receipt_id: str | None = Field(default=None, alias="latestReceiptId")
    latest_receipt_digest: str | None = Field(default=None, alias="latestReceiptDigest")
    compare_version: int = Field(alias="compareVersion", ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_reconstructable_request(self) -> Self:
        request_payload = self.request.model_dump(by_alias=True, mode="json")
        expected_json = _canonical_json(request_payload)
        if self.request_json != expected_json:
            raise ValueError("requestJson does not match the immutable decision request")
        expected_digest = canonical_user_decision_request_digest(self.request)
        if self.decision_request_digest != expected_digest:
            raise ValueError("decisionRequestDigest does not match requestJson")
        if (self.latest_receipt_id is None) != (self.latest_receipt_digest is None):
            raise ValueError("latestReceiptId and latestReceiptDigest are both-or-neither")
        if self.state is UserDecisionState.PENDING:
            if self.approval_receipt_digest is not None or self.latest_receipt_id is not None:
                raise ValueError("pending decisions cannot carry receipt pointers")
        if (
            self.state
            in {
                UserDecisionState.APPROVED,
                UserDecisionState.REVOKED,
                UserDecisionState.CONSUMED,
            }
            and self.approval_receipt_digest is None
        ):
            raise ValueError("approvalReceiptDigest is required after an approval is recorded")
        if (
            self.state
            in {
                UserDecisionState.APPROVED,
                UserDecisionState.DENIED,
                UserDecisionState.REVOKED,
                UserDecisionState.CONSUMED,
            }
            and self.latest_receipt_id is None
        ):
            raise ValueError("resolved decision states require the latest receipt pointer")
        if self.state is UserDecisionState.DENIED and self.approval_receipt_digest is not None:
            raise ValueError("denied decisions cannot carry approvalReceiptDigest")
        if self.approval_receipt_digest is not None and self.latest_receipt_id is None:
            raise ValueError("approvalReceiptDigest requires a durable latest receipt pointer")
        return self


class UserDecisionRequestRecording(EnvelopeModel):
    """Durable result of atomically recording a pending decision request."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: UserDecisionRequest
    snapshot: UserDecisionSnapshot
    decision_compare_version: int = Field(
        alias="decisionCompareVersion",
        ge=0,
        strict=True,
    )
    requested_event: JournalEvent = Field(alias="requestedEvent")

    @model_validator(mode="after")
    def _validate_request_recording(self) -> Self:
        if self.snapshot.request != self.request:
            raise ValueError("request snapshot does not preserve the recorded request")
        if self.snapshot.state is not UserDecisionState.PENDING:
            raise ValueError("request snapshot must be pending")
        if self.decision_compare_version != self.request.compare_version:
            raise ValueError("decisionCompareVersion does not match request compareVersion")
        if self.snapshot.compare_version != self.decision_compare_version:
            raise ValueError("snapshot compareVersion does not match decisionCompareVersion")
        event = self.requested_event
        bindings = (
            ("eventId", event.event_id, self.request.pending_event_id),
            ("eventType", event.event_type, "user_decision.pending"),
            ("actionId", event.action_id, self.request.action_id),
            ("partitionId", event.partition_id, self.request.authority_partition_id),
            ("taskContractId", event.task_contract_id, self.request.task_contract_id),
            ("taskVersion", event.task_version, self.request.task_version),
            ("taskContractDigest", event.task_contract_digest, self.request.task_contract_digest),
            ("completionEpochId", event.completion_epoch_id, self.request.completion_epoch_id),
            ("requestDigest", event.request_digest, self.request.normalized_request_digest),
            ("actorId", event.actor_id, self.request.principal_id),
            ("policyDigest", event.policy_digest, self.request.policy_digest),
        )
        for alias, observed, expected in bindings:
            if observed != expected:
                raise ValueError(f"requestedEvent.{alias} does not match request")
        if not self.request.created_at <= event.created_at < self.request.expires_at:
            raise ValueError("requestedEvent createdAt is outside the request time window")
        expected_payload = {
            "decisionRequestDigest": canonical_user_decision_request_digest(self.request),
            "decisionRequestId": self.request.decision_request_id,
            "decisionCompareVersion": self.decision_compare_version,
        }
        if _strict_json_loads(event.payload_json) != expected_payload:
            raise ValueError("requestedEvent payload does not exactly bind request persistence")
        return self


class ActionDenialRecording(EnvelopeModel):
    """Atomic denied-and-resolved action persistence receipt."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    resolution: ActionResolution
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion", ge=0, strict=True
    )
    expected_attempt_compare_version: int = Field(
        alias="expectedAttemptCompareVersion", ge=0, strict=True
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion", ge=0, strict=True
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    attempt_compare_version: int = Field(alias="attemptCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion", ge=1, strict=True
    )
    denied_event: JournalEvent = Field(alias="deniedEvent")
    resolution_event: JournalEvent = Field(alias="resolutionEvent")

    @model_validator(mode="after")
    def _validate_denial_recording(self) -> Self:
        if self.resolution.logical_state is not ActionState.DENIED:
            raise ValueError("denial recording requires a DENIED resolution")
        versions = (
            (
                "actionCompareVersion",
                self.action_compare_version,
                self.expected_action_compare_version + 1,
            ),
            (
                "attemptCompareVersion",
                self.attempt_compare_version,
                self.expected_attempt_compare_version + 1,
            ),
            (
                "partitionCompareVersion",
                self.partition_compare_version,
                self.expected_partition_compare_version + 1,
            ),
        )
        for alias, observed, expected in versions:
            if observed != expected:
                raise ValueError(f"{alias} does not advance the expected CAS version")
        for field_name, event, event_type in (
            ("deniedEvent", self.denied_event, "action.denied"),
            ("resolutionEvent", self.resolution_event, "action.resolved"),
        ):
            if event.event_type != event_type:
                raise ValueError(f"{field_name}.eventType does not match denial recording")
            if event.action_id != self.resolution.action_id:
                raise ValueError(f"{field_name}.actionId does not match resolution")
            if event.task_contract_digest != self.resolution.task_contract_digest:
                raise ValueError(f"{field_name}.taskContractDigest does not match resolution")
        resolution_digest = _canonical_model_digest(self.resolution)
        expected_denied_payload = {
            "actionResolutionDigest": resolution_digest,
            "reasonCodes": list(self.resolution.reason_codes),
            "sourceAttemptIds": list(self.resolution.source_attempt_ids),
        }
        if _strict_json_loads(self.denied_event.payload_json) != expected_denied_payload:
            raise ValueError("deniedEvent payload does not exactly bind denial")
        expected_resolution_payload = {
            "actionResolutionDigest": resolution_digest,
            "logicalState": self.resolution.logical_state.value,
            "sourceEventId": self.denied_event.event_id,
        }
        if _strict_json_loads(self.resolution_event.payload_json) != expected_resolution_payload:
            raise ValueError("resolutionEvent payload does not exactly bind resolution")
        _require_direct_event_successor(
            self.denied_event,
            self.resolution_event,
            first_name="deniedEvent",
            second_name="resolutionEvent",
        )
        if self.resolution_event.causation_id != self.denied_event.event_id:
            raise ValueError("resolutionEvent must be caused by deniedEvent")
        return self


class ActionResolutionRecording(EnvelopeModel):
    """Durable result of atomically materializing a logical action resolution."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    resolution: ActionResolution
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion", ge=0, strict=True
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion", ge=0, strict=True
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion", ge=1, strict=True
    )
    source_event: JournalEvent = Field(alias="sourceEvent")
    resolution_event: JournalEvent = Field(alias="resolutionEvent")

    @model_validator(mode="after")
    def _validate_resolution_recording(self) -> Self:
        if self.action_compare_version != self.expected_action_compare_version + 1:
            raise ValueError("actionCompareVersion does not advance the expected CAS version")
        if self.partition_compare_version != self.expected_partition_compare_version + 1:
            raise ValueError("partitionCompareVersion does not advance the expected CAS version")
        for field_name, event in (
            ("sourceEvent", self.source_event),
            ("resolutionEvent", self.resolution_event),
        ):
            if event.action_id != self.resolution.action_id:
                raise ValueError(f"{field_name}.actionId does not match resolution")
            if event.task_contract_digest != self.resolution.task_contract_digest:
                raise ValueError(f"{field_name}.taskContractDigest does not match resolution")
        if self.resolution_event.event_type != "action.resolved":
            raise ValueError("resolutionEvent.eventType does not match resolution recording")
        expected_payload = {
            "actionResolutionDigest": _canonical_model_digest(self.resolution),
            "logicalState": self.resolution.logical_state.value,
            "sourceEventId": self.source_event.event_id,
        }
        if _strict_json_loads(self.resolution_event.payload_json) != expected_payload:
            raise ValueError("resolutionEvent payload does not exactly bind resolution")
        _require_direct_event_successor(
            self.source_event,
            self.resolution_event,
            first_name="sourceEvent",
            second_name="resolutionEvent",
        )
        if self.resolution_event.causation_id != self.source_event.event_id:
            raise ValueError("resolutionEvent must be caused by sourceEvent")
        return self


class VerifiedUserDecisionReceipt(EnvelopeModel):
    """Decision receipt accepted by a configured first-party ingress verifier."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    receipt: UserDecisionReceipt
    receipt_digest: str = Field(alias="receiptDigest")
    verifier_id: str = Field(alias="verifierId", min_length=1)
    verifier_artifact_digest: str = Field(alias="verifierArtifactDigest")
    verified_at: datetime = Field(alias="verifiedAt")

    @model_validator(mode="after")
    def _validate_verified_receipt(self) -> Self:
        expected_digest = canonical_user_decision_receipt_digest(self.receipt)
        if self.receipt_digest != expected_digest:
            raise ValueError("receiptDigest does not match the verified receipt")
        if self.verified_at < self.receipt.issued_at:
            raise ValueError("verifiedAt cannot precede receipt issuedAt")
        if self.verified_at > self.receipt.expires_at:
            raise ValueError("verifiedAt cannot follow receipt expiresAt")
        return self


class VerifiedAuthorityResumeBinding(EnvelopeModel):
    """Store-consumable attestation that a resume binding is still current."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    binding: AuthorityResumeBinding
    binding_digest: str = Field(alias="bindingDigest")
    current_policy_digest: str = Field(alias="currentPolicyDigest")
    current_capabilities_digest: str = Field(alias="currentCapabilitiesDigest")
    verifier_id: str = Field(alias="verifierId", min_length=1)
    verifier_artifact_digest: str = Field(alias="verifierArtifactDigest")
    verified_at: datetime = Field(alias="verifiedAt")

    @model_validator(mode="after")
    def _validate_verified_binding(self) -> Self:
        expected_digest = canonical_authority_resume_binding_digest(self.binding)
        if self.binding_digest != expected_digest:
            raise ValueError("bindingDigest does not match the resume binding")
        return self


class UserDecisionRecording(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    verified_receipt: VerifiedUserDecisionReceipt = Field(alias="verifiedReceipt")
    receipt: UserDecisionReceipt
    applied_from_state: UserDecisionState = Field(alias="appliedFromState")
    applied_to_state: UserDecisionState = Field(alias="appliedToState")
    previous_snapshot: UserDecisionSnapshot = Field(alias="previousSnapshot")
    expected_decision_compare_version: int = Field(
        alias="expectedDecisionCompareVersion",
        ge=0,
        strict=True,
    )
    decision_compare_version: int = Field(
        alias="decisionCompareVersion",
        ge=1,
        strict=True,
    )
    recorded_event: JournalEvent = Field(alias="recordedEvent")
    action_resolution: ActionResolution | None = Field(
        default=None,
        alias="actionResolution",
    )
    denied_event: JournalEvent | None = Field(default=None, alias="deniedEvent")
    resolution_event: JournalEvent | None = Field(
        default=None,
        alias="resolutionEvent",
    )
    current_snapshot: UserDecisionSnapshot = Field(alias="currentSnapshot")
    replayed: bool = Field(strict=True)

    @model_validator(mode="after")
    def _validate_recording_and_atomic_terminal_records(self) -> Self:
        if self.verified_receipt.receipt != self.receipt:
            raise ValueError("verifiedReceipt does not contain receipt")
        expected_transition = {
            "approve": (UserDecisionState.PENDING, UserDecisionState.APPROVED),
            "deny": (UserDecisionState.PENDING, UserDecisionState.DENIED),
            "revoke": (UserDecisionState.APPROVED, UserDecisionState.REVOKED),
        }[self.receipt.decision]
        if (self.applied_from_state, self.applied_to_state) != expected_transition:
            raise ValueError("receipt decision does not match applied state transition")

        if self.previous_snapshot.request != self.current_snapshot.request:
            raise ValueError("decision recording snapshots must preserve the request")
        if self.previous_snapshot.state is not self.applied_from_state:
            raise ValueError("previousSnapshot does not match appliedFromState")
        if self.previous_snapshot.compare_version != self.expected_decision_compare_version:
            raise ValueError("previousSnapshot does not match expected decision CAS version")
        if self.decision_compare_version != self.expected_decision_compare_version + 1:
            raise ValueError("decisionCompareVersion does not advance expected decision CAS")
        if self.current_snapshot.compare_version != self.decision_compare_version:
            raise ValueError("currentSnapshot does not match decisionCompareVersion")

        validate_user_decision_receipt_binding(
            self.current_snapshot.request,
            self.receipt,
        )

        if self.current_snapshot.state is not self.applied_to_state:
            replay_label = "replayed" if self.replayed else "new"
            raise ValueError(f"{replay_label} recording snapshot does not reflect applied state")
        expected_receipt_digest = canonical_user_decision_receipt_digest(self.receipt)
        if self.current_snapshot.latest_receipt_id != self.receipt.receipt_id:
            raise ValueError("recording snapshot does not point to receiptId")
        if self.current_snapshot.latest_receipt_digest != expected_receipt_digest:
            raise ValueError("recording snapshot does not point to receipt digest")
        expected_approval_digest = {
            "approve": expected_receipt_digest,
            "deny": None,
            "revoke": self.receipt.revokes_receipt_digest,
        }[self.receipt.decision]
        if self.current_snapshot.approval_receipt_digest != expected_approval_digest:
            raise ValueError(
                "recording snapshot approvalReceiptDigest does not bind the canonical receipt"
            )

        recorded_event_type = (
            "user_decision.revoked"
            if self.receipt.decision == "revoke"
            else "user_decision.recorded"
        )
        recorded_bindings = (
            ("eventType", self.recorded_event.event_type, recorded_event_type),
            ("actionId", self.recorded_event.action_id, self.receipt.action_id),
            (
                "partitionId",
                self.recorded_event.partition_id,
                self.receipt.authority_partition_id,
            ),
            (
                "taskContractId",
                self.recorded_event.task_contract_id,
                self.receipt.task_contract_id,
            ),
            ("taskVersion", self.recorded_event.task_version, self.receipt.task_version),
            (
                "taskContractDigest",
                self.recorded_event.task_contract_digest,
                self.receipt.task_contract_digest,
            ),
            (
                "completionEpochId",
                self.recorded_event.completion_epoch_id,
                self.receipt.completion_epoch_id,
            ),
            (
                "requestDigest",
                self.recorded_event.request_digest,
                self.receipt.normalized_request_digest,
            ),
            ("actorId", self.recorded_event.actor_id, self.receipt.authenticated_actor_id),
            ("policyDigest", self.recorded_event.policy_digest, self.receipt.policy_digest),
        )
        for alias, observed, expected in recorded_bindings:
            if observed != expected:
                raise ValueError(f"recordedEvent.{alias} does not match receipt")
        expected_recorded_payload = {
            "decision": self.receipt.decision,
            "decisionRequestId": self.receipt.decision_request_id,
            "decisionRequestDigest": self.current_snapshot.decision_request_digest,
            "receiptId": self.receipt.receipt_id,
            "receiptDigest": expected_receipt_digest,
            "verifiedReceiptDigest": _canonical_model_digest(self.verified_receipt),
            "authenticationNonceDigest": self.receipt.authentication_nonce_digest,
            "appliedFromState": self.applied_from_state.value,
            "appliedToState": self.applied_to_state.value,
            "previousSnapshotCompareVersion": self.previous_snapshot.compare_version,
            "currentSnapshotCompareVersion": self.current_snapshot.compare_version,
        }
        if _strict_json_loads(self.recorded_event.payload_json) != expected_recorded_payload:
            raise ValueError("recordedEvent payload does not bind the decision request and receipt")
        if self.recorded_event.causation_id != self.current_snapshot.request.pending_event_id:
            raise ValueError("recordedEvent must be caused by the pending decision event")
        if self.recorded_event.created_at < self.receipt.issued_at:
            raise ValueError("recordedEvent createdAt cannot precede receipt issuedAt")
        if self.recorded_event.created_at > self.receipt.expires_at:
            raise ValueError("recordedEvent createdAt cannot follow receipt expiresAt")

        terminal_records = (
            self.action_resolution,
            self.denied_event,
            self.resolution_event,
        )
        if self.receipt.decision == "approve":
            if any(record is not None for record in terminal_records):
                raise ValueError("approval cannot carry terminal action records")
            return self
        if not all(record is not None for record in terminal_records):
            raise ValueError("deny and revoke recordings require all terminal action records")
        resolution = self.action_resolution
        denied_event = self.denied_event
        resolution_event = self.resolution_event
        if resolution is None or denied_event is None or resolution_event is None:
            raise ValueError("validated terminal action records are missing")
        if resolution.logical_state is not ActionState.DENIED:
            raise ValueError("decision terminal action resolution must be DENIED")
        if resolution.action_id != self.receipt.action_id:
            raise ValueError("actionResolution actionId does not match receipt")
        if resolution.task_contract_digest != self.receipt.task_contract_digest:
            raise ValueError("actionResolution Task Contract does not match receipt")
        for field_name, event, event_type in (
            ("deniedEvent", denied_event, "action.denied"),
            ("resolutionEvent", resolution_event, "action.resolved"),
        ):
            event_bindings = (
                ("eventType", event.event_type, event_type),
                ("actionId", event.action_id, self.receipt.action_id),
                ("partitionId", event.partition_id, self.receipt.authority_partition_id),
                (
                    "taskContractDigest",
                    event.task_contract_digest,
                    self.receipt.task_contract_digest,
                ),
                (
                    "completionEpochId",
                    event.completion_epoch_id,
                    self.receipt.completion_epoch_id,
                ),
                (
                    "requestDigest",
                    event.request_digest,
                    self.receipt.normalized_request_digest,
                ),
            )
            for alias, observed, expected in event_bindings:
                if observed != expected:
                    raise ValueError(f"{field_name}.{alias} does not match receipt")
        _require_direct_event_successor(
            self.recorded_event,
            denied_event,
            first_name="recordedEvent",
            second_name="deniedEvent",
        )
        _require_direct_event_successor(
            denied_event,
            resolution_event,
            first_name="deniedEvent",
            second_name="resolutionEvent",
        )
        if denied_event.causation_id != self.recorded_event.event_id:
            raise ValueError("deniedEvent must be caused by recordedEvent")
        if resolution_event.causation_id != denied_event.event_id:
            raise ValueError("resolutionEvent must be caused by deniedEvent")
        resolution_digest = _canonical_model_digest(resolution)
        expected_denied_payload = {
            "actionResolutionDigest": resolution_digest,
            "decisionReceiptDigest": expected_receipt_digest,
            "decisionRequestId": self.receipt.decision_request_id,
            "recordedEventId": self.recorded_event.event_id,
        }
        if _strict_json_loads(denied_event.payload_json) != expected_denied_payload:
            raise ValueError("deniedEvent payload does not exactly bind decision denial")
        expected_resolution_payload = {
            "actionResolutionDigest": resolution_digest,
            "deniedEventId": denied_event.event_id,
            "logicalState": resolution.logical_state.value,
        }
        if _strict_json_loads(resolution_event.payload_json) != expected_resolution_payload:
            raise ValueError("resolutionEvent payload does not exactly bind decision resolution")
        return self


class UserDecisionInvalidationRequest(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    decision_request_id: str = Field(alias="decisionRequestId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_id: str = Field(alias="actionId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    expected_decision_compare_version: int = Field(
        alias="expectedDecisionCompareVersion",
        ge=0,
        strict=True,
    )
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion",
        ge=0,
        strict=True,
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion",
        ge=0,
        strict=True,
    )
    invalidated_binding_kind: Literal[
        "task_contract",
        "policy",
        "completion_epoch",
        "resource_identity",
        "resume_binding",
    ] = Field(alias="invalidatedBindingKind")
    previous_binding_digest: str = Field(alias="previousBindingDigest")
    current_binding_digest: str = Field(alias="currentBindingDigest")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes", min_length=1)

    @model_validator(mode="after")
    def _require_actual_binding_change(self) -> Self:
        if self.previous_binding_digest == self.current_binding_digest:
            raise ValueError("decision invalidation requires a changed binding digest")
        return self


class UserDecisionExpirationRequest(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    decision_request_id: str = Field(alias="decisionRequestId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    action_id: str = Field(alias="actionId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    expected_decision_compare_version: int = Field(
        alias="expectedDecisionCompareVersion",
        ge=0,
        strict=True,
    )
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion",
        ge=0,
        strict=True,
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion",
        ge=0,
        strict=True,
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes", min_length=1)


class UserDecisionTransition(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: UserDecisionInvalidationRequest | UserDecisionExpirationRequest
    from_state: UserDecisionState = Field(alias="fromState")
    to_state: Literal[
        UserDecisionState.INVALIDATED,
        UserDecisionState.EXPIRED,
    ] = Field(alias="toState")
    previous_snapshot: UserDecisionSnapshot = Field(alias="previousSnapshot")
    current_snapshot: UserDecisionSnapshot = Field(alias="currentSnapshot")
    decision_compare_version: int = Field(
        alias="decisionCompareVersion",
        ge=1,
        strict=True,
    )
    action_compare_version: int = Field(alias="actionCompareVersion", ge=1, strict=True)
    partition_compare_version: int = Field(
        alias="partitionCompareVersion",
        ge=1,
        strict=True,
    )
    transition_event: JournalEvent = Field(alias="transitionEvent")
    action_resolution: ActionResolution = Field(alias="actionResolution")
    denied_event: JournalEvent | None = Field(default=None, alias="deniedEvent")
    resolution_event: JournalEvent | None = Field(
        default=None,
        alias="resolutionEvent",
    )

    @model_validator(mode="after")
    def _validate_system_transition(self) -> Self:
        expected_state = (
            UserDecisionState.INVALIDATED
            if isinstance(self.request, UserDecisionInvalidationRequest)
            else UserDecisionState.EXPIRED
        )
        if self.to_state is not expected_state:
            raise ValueError("decision transition state does not match its request")
        if self.from_state not in {
            UserDecisionState.PENDING,
            UserDecisionState.APPROVED,
        }:
            raise ValueError("only pending or approved decisions may transition")
        if self.previous_snapshot.request != self.current_snapshot.request:
            raise ValueError("decision transition snapshots must preserve the request")
        if self.previous_snapshot.state is not self.from_state:
            raise ValueError("previousSnapshot does not match fromState")
        if self.current_snapshot.state is not self.to_state:
            raise ValueError("currentSnapshot does not match toState")
        snapshot_request = self.previous_snapshot.request
        snapshot_bindings = (
            (
                "decisionRequestId",
                snapshot_request.decision_request_id,
                self.request.decision_request_id,
            ),
            (
                "taskContractDigest",
                snapshot_request.task_contract_digest,
                self.request.task_contract_digest,
            ),
            ("actionId", snapshot_request.action_id, self.request.action_id),
            (
                "partitionId",
                snapshot_request.authority_partition_id,
                self.request.partition_id,
            ),
        )
        for alias, observed, expected in snapshot_bindings:
            if observed != expected:
                raise ValueError(f"transition snapshot {alias} does not match request")
        if self.previous_snapshot.compare_version != self.request.expected_decision_compare_version:
            raise ValueError("previousSnapshot does not match expected decision CAS version")
        if self.current_snapshot.compare_version != self.decision_compare_version:
            raise ValueError("currentSnapshot does not match decisionCompareVersion")
        prior_receipt_history = (
            self.previous_snapshot.approval_receipt_digest,
            self.previous_snapshot.latest_receipt_id,
            self.previous_snapshot.latest_receipt_digest,
        )
        current_receipt_history = (
            self.current_snapshot.approval_receipt_digest,
            self.current_snapshot.latest_receipt_id,
            self.current_snapshot.latest_receipt_digest,
        )
        if current_receipt_history != prior_receipt_history:
            raise ValueError("system transition must preserve durable receipt history")
        if self.action_resolution.action_id != self.request.action_id:
            raise ValueError("decision transition action resolution does not match")
        if self.action_resolution.task_contract_digest != self.request.task_contract_digest:
            raise ValueError("decision transition Task Contract does not match")
        if self.action_resolution.logical_state is not ActionState.DENIED:
            raise ValueError("decision terminal transition must resolve action DENIED")
        expected_versions: tuple[tuple[str, int, int], ...] = (
            (
                "decisionCompareVersion",
                self.decision_compare_version,
                self.request.expected_decision_compare_version + 1,
            ),
            (
                "actionCompareVersion",
                self.action_compare_version,
                self.request.expected_action_compare_version + 1,
            ),
            (
                "partitionCompareVersion",
                self.partition_compare_version,
                self.request.expected_partition_compare_version + 1,
            ),
        )
        for version_alias, version_observed, version_expected in expected_versions:
            if version_observed != version_expected:
                raise ValueError(f"{version_alias} does not advance the requested CAS version")
        transition_type = (
            "user_decision.invalidated"
            if expected_state is UserDecisionState.INVALIDATED
            else "user_decision.expired"
        )
        transition_bindings = (
            ("eventType", self.transition_event.event_type, transition_type),
            ("actionId", self.transition_event.action_id, self.request.action_id),
            (
                "partitionId",
                self.transition_event.partition_id,
                self.request.partition_id,
            ),
            (
                "taskContractDigest",
                self.transition_event.task_contract_digest,
                self.request.task_contract_digest,
            ),
        )
        for transition_alias, transition_observed, transition_expected in transition_bindings:
            if transition_observed != transition_expected:
                raise ValueError(f"transitionEvent.{transition_alias} does not match request")
        if isinstance(self.request, UserDecisionExpirationRequest):
            if self.transition_event.created_at < snapshot_request.expires_at:
                raise ValueError("expiration transitionEvent createdAt must reach request expiresAt")
        elif self.transition_event.created_at < snapshot_request.created_at:
            raise ValueError("invalidation transitionEvent createdAt cannot precede request createdAt")
        expected_transition_payload = {
            "actionResolutionDigest": _canonical_model_digest(self.action_resolution),
            "currentSnapshotCompareVersion": self.current_snapshot.compare_version,
            "decisionRequestId": self.request.decision_request_id,
            "fromState": self.from_state.value,
            "previousSnapshotCompareVersion": self.previous_snapshot.compare_version,
            "toState": self.to_state.value,
            "transitionRequestDigest": _canonical_model_digest(self.request),
        }
        if _strict_json_loads(self.transition_event.payload_json) != (expected_transition_payload):
            raise ValueError("transitionEvent payload does not bind the system transition")
        if self.denied_event is None or self.resolution_event is None:
            raise ValueError("decision transition requires both terminal action events")
        for field_name, event, event_type in (
            ("deniedEvent", self.denied_event, "action.denied"),
            ("resolutionEvent", self.resolution_event, "action.resolved"),
        ):
            event_bindings = (
                ("eventType", event.event_type, event_type),
                ("actionId", event.action_id, self.request.action_id),
                ("partitionId", event.partition_id, self.request.partition_id),
                ("taskContractId", event.task_contract_id, snapshot_request.task_contract_id),
                ("taskVersion", event.task_version, snapshot_request.task_version),
                (
                    "taskContractDigest",
                    event.task_contract_digest,
                    self.request.task_contract_digest,
                ),
                (
                    "completionEpochId",
                    event.completion_epoch_id,
                    snapshot_request.completion_epoch_id,
                ),
                (
                    "requestDigest",
                    event.request_digest,
                    snapshot_request.normalized_request_digest,
                ),
                ("actorId", event.actor_id, snapshot_request.principal_id),
                ("policyDigest", event.policy_digest, snapshot_request.policy_digest),
            )
            for event_alias, event_observed, event_expected in event_bindings:
                if event_observed != event_expected:
                    raise ValueError(f"{field_name}.{event_alias} does not match request")
        _require_direct_event_successor(
            self.transition_event,
            self.denied_event,
            first_name="transitionEvent",
            second_name="deniedEvent",
        )
        _require_direct_event_successor(
            self.denied_event,
            self.resolution_event,
            first_name="deniedEvent",
            second_name="resolutionEvent",
        )
        if self.denied_event.causation_id != self.transition_event.event_id:
            raise ValueError("deniedEvent must be caused by transitionEvent")
        if self.resolution_event.causation_id != self.denied_event.event_id:
            raise ValueError("resolutionEvent must be caused by deniedEvent")
        if self.denied_event.created_at < self.transition_event.created_at:
            raise ValueError("deniedEvent createdAt cannot precede transitionEvent")
        if self.resolution_event.created_at < self.denied_event.created_at:
            raise ValueError("resolutionEvent createdAt cannot precede deniedEvent")
        resolution_digest = _canonical_model_digest(self.action_resolution)
        transition_request_digest = _canonical_model_digest(self.request)
        expected_denied_payload = {
            "actionResolutionDigest": resolution_digest,
            "transitionEventId": self.transition_event.event_id,
            "transitionRequestDigest": transition_request_digest,
        }
        if _strict_json_loads(self.denied_event.payload_json) != expected_denied_payload:
            raise ValueError("deniedEvent payload does not exactly bind decision transition")
        expected_resolution_payload = {
            "actionResolutionDigest": resolution_digest,
            "deniedEventId": self.denied_event.event_id,
            "logicalState": self.action_resolution.logical_state.value,
        }
        if _strict_json_loads(self.resolution_event.payload_json) != expected_resolution_payload:
            raise ValueError("resolutionEvent payload does not exactly bind decision transition")
        return self


class UserApprovalConsumption(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    decision_request_id: str = Field(alias="decisionRequestId", min_length=1)
    task_contract_digest: str = Field(alias="taskContractDigest")
    approval_receipt: UserDecisionReceipt = Field(alias="approvalReceipt")
    approval_receipt_digest: str = Field(alias="approvalReceiptDigest")
    approval_recording: UserDecisionRecording = Field(alias="approvalRecording")
    approved_snapshot: UserDecisionSnapshot = Field(alias="approvedSnapshot")
    resume_binding: AuthorityResumeBinding = Field(alias="resumeBinding")
    resume_binding_digest: str = Field(alias="resumeBindingDigest")
    verified_resume_binding: VerifiedAuthorityResumeBinding = Field(
        alias="verifiedResumeBinding"
    )
    current_policy_digest: str = Field(alias="currentPolicyDigest")
    current_capabilities_digest: str = Field(alias="currentCapabilitiesDigest")
    authority_contract: AuthorityContract = Field(alias="authorityContract")
    authority_contract_digest: str = Field(alias="authorityContractDigest")
    expected_action_compare_version: int = Field(
        alias="expectedActionCompareVersion",
        ge=0,
        strict=True,
    )
    expected_attempt_compare_version: int = Field(
        alias="expectedAttemptCompareVersion",
        ge=0,
        strict=True,
    )
    expected_partition_compare_version: int = Field(
        alias="expectedPartitionCompareVersion",
        ge=0,
        strict=True,
    )
    decision_compare_version: int = Field(
        alias="decisionCompareVersion",
        ge=1,
        strict=True,
    )
    preparation: ExecutionPreparation
    consumed_snapshot: UserDecisionSnapshot = Field(alias="consumedSnapshot")
    consumed_event: JournalEvent = Field(alias="consumedEvent")

    @model_validator(mode="after")
    def _validate_approval_resume_and_preparation(self) -> Self:
        request = self.approved_snapshot.request
        if self.approved_snapshot.state is not UserDecisionState.APPROVED:
            raise ValueError("approvedSnapshot must be in APPROVED state")
        if request.decision_request_id != self.decision_request_id:
            raise ValueError("decisionRequestId does not match approvedSnapshot")
        if request.task_contract_digest != self.task_contract_digest:
            raise ValueError("Task Contract does not match approvedSnapshot")

        if self.approval_recording.receipt != self.approval_receipt:
            raise ValueError("approvalRecording does not contain approvalReceipt")
        if self.approval_recording.current_snapshot != self.approved_snapshot:
            raise ValueError("approvalRecording does not produce approvedSnapshot")
        if self.approval_recording.applied_to_state is not UserDecisionState.APPROVED:
            raise ValueError("approvalRecording must durably record approval")

        validate_user_decision_receipt_binding(request, self.approval_receipt)
        if self.approval_receipt.decision != "approve":
            raise ValueError("approvalReceipt must carry an approve decision")
        expected_approval_digest = canonical_user_decision_receipt_digest(self.approval_receipt)
        if self.approval_receipt_digest != expected_approval_digest:
            raise ValueError("approvalReceiptDigest does not match approvalReceipt")
        if self.approved_snapshot.approval_receipt_digest != expected_approval_digest:
            raise ValueError("approvedSnapshot approvalReceiptDigest does not match receipt")
        if self.approved_snapshot.latest_receipt_id != self.approval_receipt.receipt_id:
            raise ValueError("approvedSnapshot does not point to approval receiptId")
        if self.approved_snapshot.latest_receipt_digest != expected_approval_digest:
            raise ValueError("approvedSnapshot does not point to approvalReceiptDigest")

        expected_resume_digest = canonical_authority_resume_binding_digest(self.resume_binding)
        if self.resume_binding_digest != expected_resume_digest:
            raise ValueError("resumeBindingDigest does not match resumeBinding")
        if self.verified_resume_binding.binding != self.resume_binding:
            raise ValueError("verifiedResumeBinding does not contain resumeBinding")
        if self.verified_resume_binding.binding_digest != expected_resume_digest:
            raise ValueError("verifiedResumeBinding bindingDigest does not match resumeBinding")
        if self.current_policy_digest != self.verified_resume_binding.current_policy_digest:
            raise ValueError("currentPolicyDigest does not match verifiedResumeBinding")
        if (
            self.current_capabilities_digest
            != self.verified_resume_binding.current_capabilities_digest
        ):
            raise ValueError("currentCapabilitiesDigest does not match verifiedResumeBinding")
        if self.current_policy_digest != request.policy_digest:
            raise ValueError("currentPolicyDigest no longer matches the approval request")
        if self.current_capabilities_digest != request.capabilities_digest:
            raise ValueError("currentCapabilitiesDigest no longer matches the approval request")
        resume_bindings = (
            (
                "decisionRequestId",
                self.resume_binding.decision_request_id,
                request.decision_request_id,
            ),
            (
                "authenticatedActorId",
                self.resume_binding.authenticated_actor_id,
                self.approval_receipt.authenticated_actor_id,
            ),
            ("sessionId", self.resume_binding.session_id, request.session_id),
            ("turnId", self.resume_binding.turn_id, request.turn_id),
            ("actionId", self.resume_binding.action_id, request.action_id),
            (
                "taskContractId",
                self.resume_binding.task_contract_id,
                request.task_contract_id,
            ),
            ("taskVersion", self.resume_binding.task_version, request.task_version),
            (
                "taskContractDigest",
                self.resume_binding.task_contract_digest,
                request.task_contract_digest,
            ),
            (
                "completionEpochId",
                self.resume_binding.completion_epoch_id,
                request.completion_epoch_id,
            ),
            (
                "authorityPartitionId",
                self.resume_binding.authority_partition_id,
                request.authority_partition_id,
            ),
        )
        for alias, observed, expected in resume_bindings:
            if observed != expected:
                raise ValueError(f"resumeBinding.{alias} does not match approval request")
        if any(
            event.correlation_id != self.resume_binding.run_id
            for event in (
                self.preparation.authority_event,
                self.preparation.prepared_event,
            )
        ):
            raise ValueError("resumeBinding.runId does not match preparation correlationId")

        expected_authority_digest = canonical_authority_contract_digest(self.authority_contract)
        if self.authority_contract_digest != expected_authority_digest:
            raise ValueError("authorityContractDigest does not match authorityContract")
        authority_bindings: tuple[tuple[str, object, object], ...] = (
            (
                "decisionRequestId",
                self.authority_contract.decision_request_id,
                request.decision_request_id,
            ),
            (
                "resumeBindingDigest",
                self.authority_contract.resume_binding_digest,
                expected_resume_digest,
            ),
            ("principalId", self.authority_contract.principal_id, request.principal_id),
            ("tenantId", self.authority_contract.tenant_id, request.tenant_id),
            ("sessionId", self.authority_contract.session_id, request.session_id),
            ("turnId", self.authority_contract.turn_id, request.turn_id),
            (
                "taskContractId",
                self.authority_contract.task_contract_id,
                request.task_contract_id,
            ),
            ("taskVersion", self.authority_contract.task_version, request.task_version),
            (
                "taskContractDigest",
                self.authority_contract.task_contract_digest,
                request.task_contract_digest,
            ),
            (
                "completionEpochId",
                self.authority_contract.completion_epoch_id,
                request.completion_epoch_id,
            ),
            (
                "authorityPartitionId",
                self.authority_contract.authority_partition_id,
                request.authority_partition_id,
            ),
            ("actionId", self.authority_contract.action_id, request.action_id),
            (
                "normalizedRequestDigest",
                self.authority_contract.normalized_request_digest,
                request.normalized_request_digest,
            ),
            ("policyDigest", self.authority_contract.policy_digest, request.policy_digest),
            (
                "guardianCeilingDigest",
                self.authority_contract.guardian_ceiling_digest,
                request.authority_ceiling_digest,
            ),
            ("capabilities", self.authority_contract.capabilities, request.capabilities),
            (
                "workspaceViewBindingDigest",
                self.authority_contract.workspace_view_binding_digest,
                request.workspace_view_binding_digest,
            ),
        )
        for authority_alias, authority_observed, authority_expected in authority_bindings:
            if authority_observed != authority_expected:
                raise ValueError(
                    f"authorityContract.{authority_alias} does not match approval request"
                )
        if self.authority_contract.policy_digest != self.current_policy_digest:
            raise ValueError("authorityContract.policyDigest does not match current policy")
        if canonical_capabilities_digest(self.authority_contract.capabilities) != (
            self.current_capabilities_digest
        ):
            raise ValueError("authorityContract capabilities do not match current capabilities")

        preparation = self.preparation
        preparation_bindings = (
            ("actionId", preparation.action_id, self.authority_contract.action_id),
            ("attemptId", preparation.attempt_id, self.authority_contract.attempt_id),
            (
                "partitionId",
                preparation.partition_id,
                self.authority_contract.authority_partition_id,
            ),
            (
                "taskContractDigest",
                preparation.task_contract_digest,
                self.task_contract_digest,
            ),
            (
                "requestDigest",
                preparation.request_digest,
                request.normalized_request_digest,
            ),
            (
                "authorityContractId",
                preparation.authority_contract_id,
                self.authority_contract.authority_contract_id,
            ),
            (
                "authorityContractDigest",
                preparation.authority_contract_digest,
                expected_authority_digest,
            ),
            (
                "fencingToken",
                preparation.fencing_token,
                self.authority_contract.fencing_token,
            ),
        )
        for alias, observed, expected in preparation_bindings:
            if observed != expected:
                raise ValueError(f"preparation.{alias} does not match approved authority")

        expected_versions = (
            (
                "decisionCompareVersion",
                self.decision_compare_version,
                self.approved_snapshot.compare_version + 1,
            ),
            (
                "actionCompareVersion",
                preparation.action_compare_version,
                self.expected_action_compare_version + 1,
            ),
            (
                "attemptCompareVersion",
                preparation.attempt_compare_version,
                self.expected_attempt_compare_version + 1,
            ),
            (
                "partitionCompareVersion",
                preparation.partition_compare_version,
                self.expected_partition_compare_version + 1,
            ),
        )
        for alias, observed, expected in expected_versions:
            if observed != expected:
                raise ValueError(f"{alias} does not advance the expected CAS version")

        if self.consumed_snapshot.request != self.approved_snapshot.request:
            raise ValueError("consumedSnapshot must preserve the approved decision request")
        if self.consumed_snapshot.state is not UserDecisionState.CONSUMED:
            raise ValueError("consumedSnapshot must be in CONSUMED state")
        if self.consumed_snapshot.compare_version != self.decision_compare_version:
            raise ValueError("consumedSnapshot compareVersion must equal decisionCompareVersion")
        if (
            self.consumed_snapshot.approval_receipt_digest
            != self.approved_snapshot.approval_receipt_digest
            or self.consumed_snapshot.latest_receipt_id != self.approved_snapshot.latest_receipt_id
            or self.consumed_snapshot.latest_receipt_digest
            != self.approved_snapshot.latest_receipt_digest
        ):
            raise ValueError("consumedSnapshot must preserve the approval receipt pointers")

        consumed_event_bindings: tuple[tuple[str, object, object], ...] = (
            ("eventType", self.consumed_event.event_type, "user_decision.consumed"),
            ("actionId", self.consumed_event.action_id, request.action_id),
            (
                "partitionId",
                self.consumed_event.partition_id,
                request.authority_partition_id,
            ),
            ("taskContractId", self.consumed_event.task_contract_id, request.task_contract_id),
            ("taskVersion", self.consumed_event.task_version, request.task_version),
            (
                "taskContractDigest",
                self.consumed_event.task_contract_digest,
                request.task_contract_digest,
            ),
            (
                "completionEpochId",
                self.consumed_event.completion_epoch_id,
                request.completion_epoch_id,
            ),
            (
                "requestDigest",
                self.consumed_event.request_digest,
                request.normalized_request_digest,
            ),
            ("actorId", self.consumed_event.actor_id, self.approval_receipt.authenticated_actor_id),
            ("policyDigest", self.consumed_event.policy_digest, request.policy_digest),
            ("correlationId", self.consumed_event.correlation_id, self.resume_binding.run_id),
        )
        for binding_alias, binding_observed, binding_expected in consumed_event_bindings:
            if binding_observed != binding_expected:
                raise ValueError(
                    f"consumedEvent.{binding_alias} does not match approval consumption"
                )
        expected_consumed_payload = {
            "decisionRequestId": request.decision_request_id,
            "approvalReceiptDigest": expected_approval_digest,
            "resumeBindingDigest": expected_resume_digest,
            "verifiedResumeBindingDigest": _canonical_model_digest(
                self.verified_resume_binding
            ),
            "currentPolicyDigest": self.current_policy_digest,
            "currentCapabilitiesDigest": self.current_capabilities_digest,
            "authorityContractDigest": expected_authority_digest,
            "preparedEventId": preparation.prepared_event.event_id,
        }
        if _strict_json_loads(self.consumed_event.payload_json) != expected_consumed_payload:
            raise ValueError("consumedEvent payload does not bind approval consumption")
        _require_direct_event_successor(
            preparation.prepared_event,
            self.consumed_event,
            first_name="preparedEvent",
            second_name="consumedEvent",
        )
        if self.consumed_event.causation_id != preparation.prepared_event.event_id:
            raise ValueError("consumedEvent must be caused by preparedEvent")
        _require_direct_event_successor(
            self.approval_recording.recorded_event,
            preparation.authority_event,
            first_name="approvalRecordedEvent",
            second_name="authorityEvent",
        )
        if preparation.authority_event.causation_id != (
            self.approval_recording.recorded_event.event_id
        ):
            raise ValueError("authorityEvent must be caused by approval recordedEvent")
        chronology = (
            ("approval recordedEvent", self.approval_recording.recorded_event.created_at),
            ("resume verification", self.verified_resume_binding.verified_at),
            ("authorityEvent", preparation.authority_event.created_at),
            ("preparedEvent", preparation.prepared_event.created_at),
            ("consumedEvent", self.consumed_event.created_at),
        )
        previous_name, previous_at = chronology[0]
        if previous_at < self.approval_receipt.issued_at:
            raise ValueError("approval recordedEvent cannot precede receipt issuedAt")
        for current_name, current_at in chronology[1:]:
            if current_at < previous_at:
                raise ValueError(f"{current_name} cannot precede {previous_name}")
            previous_name, previous_at = current_name, current_at
        if self.consumed_event.created_at > self.approval_receipt.expires_at:
            raise ValueError("consumedEvent cannot follow approval receipt expiresAt")

        required_event_payload = {
            "decisionRequestId": request.decision_request_id,
            "resumeBindingDigest": expected_resume_digest,
            "authorityContractDigest": expected_authority_digest,
        }
        for field_name, event in (
            ("authorityEvent", preparation.authority_event),
            ("preparedEvent", preparation.prepared_event),
        ):
            payload = _strict_json_loads(event.payload_json)
            if not isinstance(payload, Mapping):
                raise ValueError(f"{field_name} payload must be an object")
            for key, expected in required_event_payload.items():
                if payload.get(key) != expected:
                    raise ValueError(f"{field_name} payload {key} does not match approval")
        return self


ExecutionStartRequest.model_rebuild()
ExecutionStart.model_rebuild()
