from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.harness.memory_write import (
    MemoryHarnessEraseState,
    MemoryHarnessRedactionStatus,
    MemoryHarnessRetentionState,
    _sanitize_public_id,
    _sanitize_public_ref,
    _sanitize_reason_code,
    _sanitized_digest,
)
from magi_agent.memory.write_boundary import MemoryWriteAuthorityFlags, sha256_hex


MemoryCompactionStatus: TypeAlias = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "success",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class MemoryCompactionHarnessConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_adapter_enabled: bool = Field(default=False, alias="localFakeAdapterEnabled")
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    provider_call_allowed: Literal[False] = Field(default=False, alias="providerCallAllowed")
    filesystem_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAllowed",
    )
    database_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="databaseMutationAllowed",
    )
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    adk_memory_service_write_enabled: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceWriteEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionWriteEnabled"] = False
        payload["providerCallAllowed"] = False
        payload["filesystemMutationAllowed"] = False
        payload["databaseMutationAllowed"] = False
        payload["networkCallAllowed"] = False
        payload["adkMemoryServiceWriteEnabled"] = False
        payload["trafficAttached"] = False
        payload.pop("production_write_enabled", None)
        payload.pop("provider_call_allowed", None)
        payload.pop("filesystem_mutation_allowed", None)
        payload.pop("database_mutation_allowed", None)
        payload.pop("network_call_allowed", None)
        payload.pop("adk_memory_service_write_enabled", None)
        payload.pop("traffic_attached", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_serializer(
        "production_write_enabled",
        "provider_call_allowed",
        "filesystem_mutation_allowed",
        "database_mutation_allowed",
        "network_call_allowed",
        "adk_memory_service_write_enabled",
        "traffic_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MemoryCompactionPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_ref: str = Field(alias="policyRef")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    approval_required: bool = Field(default=False, alias="approvalRequired")
    evidence_required: bool = Field(default=True, alias="evidenceRequired")
    local_fake_compaction_allowed: bool = Field(default=False, alias="localFakeCompactionAllowed")
    redaction_status: MemoryHarnessRedactionStatus = Field(
        default="verified",
        alias="redactionStatus",
    )
    retention_state: MemoryHarnessRetentionState = Field(default="active", alias="retentionState")
    erase_state: MemoryHarnessEraseState = Field(default="active", alias="eraseState")
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    provider_call_allowed: Literal[False] = Field(default=False, alias="providerCallAllowed")

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionWriteEnabled"] = False
        payload["providerCallAllowed"] = False
        payload.pop("production_write_enabled", None)
        payload.pop("provider_call_allowed", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("policy_ref", "policy_snapshot_ref", mode="before")
    @classmethod
    def _sanitize_refs(cls, value: object) -> str:
        return _sanitize_public_ref(str(value or "policy:memory-compaction"))

    @field_serializer("production_write_enabled", "provider_call_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False


class MemoryCompactionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    turn_id: str = Field(alias="turnId")
    source_refs: tuple[str, ...] = Field(alias="sourceRefs")
    excluded_refs: tuple[str, ...] = Field(default=(), alias="excludedRefs")
    source_texts: tuple[str, ...] = Field(default=(), alias="sourceTexts", exclude=True)
    output_text: str | None = Field(default=None, alias="outputText", exclude=True)
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    private_payload: bool = Field(default=False, alias="privatePayload")
    child_memory_isolated: bool = Field(default=False, alias="childMemoryIsolated")

    @field_validator("provider_id", "turn_id", mode="before")
    @classmethod
    def _sanitize_public_ids(cls, value: object) -> str:
        return _sanitize_public_id(str(value or "memory-compaction"), prefix="memory-id")

    @field_validator("source_refs", "excluded_refs", "evidence_refs", mode="before")
    @classmethod
    def _sanitize_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_public_ref(item) for item in _string_tuple(value))

    @field_validator("approval_ref", mode="before")
    @classmethod
    def _sanitize_approval_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return _sanitize_public_ref(str(value))

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)


class MemoryCompactionReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["memoryCompactionReceipt.v1"] = Field(
        default="memoryCompactionReceipt.v1",
        alias="schemaVersion",
    )
    receipt_id: str = Field(alias="receiptId")
    provider_id: str = Field(alias="providerId")
    turn_id: str = Field(alias="turnId")
    status: MemoryCompactionStatus
    executed: bool = False
    source_refs: tuple[str, ...] = Field(alias="sourceRefs")
    excluded_refs: tuple[str, ...] = Field(default=(), alias="excludedRefs")
    redaction_status: MemoryHarnessRedactionStatus = Field(alias="redactionStatus")
    output_digest: str = Field(alias="outputDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    provider_call_attempted: Literal[False] = Field(default=False, alias="providerCallAttempted")
    filesystem_mutation_attempted: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAttempted",
    )
    database_mutation_attempted: Literal[False] = Field(
        default=False,
        alias="databaseMutationAttempted",
    )
    network_call_attempted: Literal[False] = Field(default=False, alias="networkCallAttempted")
    local_test_only: bool = Field(default=False, alias="localTestOnly")
    authority_flags: MemoryWriteAuthorityFlags = Field(
        default_factory=MemoryWriteAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionWriteEnabled"] = False
        payload["providerCallAttempted"] = False
        payload["filesystemMutationAttempted"] = False
        payload["databaseMutationAttempted"] = False
        payload["networkCallAttempted"] = False
        payload.pop("production_write_enabled", None)
        payload.pop("provider_call_attempted", None)
        payload.pop("filesystem_mutation_attempted", None)
        payload.pop("database_mutation_attempted", None)
        payload.pop("network_call_attempted", None)
        return payload

    @field_validator("receipt_id", "policy_snapshot_ref", mode="before")
    @classmethod
    def _sanitize_public_strings(cls, value: object) -> str:
        return _sanitize_public_ref(str(value or "receipt:memory-compaction"))

    @field_validator("provider_id", "turn_id", mode="before")
    @classmethod
    def _sanitize_public_ids(cls, value: object) -> str:
        return _sanitize_public_id(str(value or "memory-compaction"), prefix="memory-id")

    @field_validator("source_refs", "excluded_refs", mode="before")
    @classmethod
    def _sanitize_memory_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_public_ref(item) for item in _string_tuple(value))

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_reason_code(item) for item in _string_tuple(value))

    @field_serializer(
        "production_write_enabled",
        "provider_call_attempted",
        "filesystem_mutation_attempted",
        "database_mutation_attempted",
        "network_call_attempted",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "receiptId": self.receipt_id,
            "providerId": self.provider_id,
            "turnId": self.turn_id,
            "status": self.status,
            "executed": self.executed,
            "sourceRefs": self.source_refs,
            "excludedRefs": self.excluded_refs,
            "redactionStatus": self.redaction_status,
            "outputDigest": self.output_digest,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "policySnapshotRef": self.policy_snapshot_ref,
            "reasonCodes": self.reason_codes,
            "productionWriteEnabled": False,
            "providerCallAttempted": False,
            "filesystemMutationAttempted": False,
            "databaseMutationAttempted": False,
            "networkCallAttempted": False,
            "localTestOnly": self.local_test_only,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class MemoryCompactionResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: MemoryCompactionStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    receipt: MemoryCompactionReceipt
    authority_flags: MemoryWriteAuthorityFlags = Field(
        default_factory=MemoryWriteAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_reason_code(item) for item in _string_tuple(value))

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": self.reason_codes,
            "receipt": self.receipt.public_projection(),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class MemoryCompactionHarness:
    """Digest-only compaction receipt boundary for future ADK MemoryService writes."""

    def __init__(
        self,
        config: MemoryCompactionHarnessConfig | Mapping[str, object] | None = None,
        *,
        adapter: object | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, MemoryCompactionHarnessConfig)
            else MemoryCompactionHarnessConfig.model_validate(config or {})
        )
        self.adapter = adapter

    async def compact(
        self,
        *,
        request: MemoryCompactionRequest | Mapping[str, object],
        policy: MemoryCompactionPolicy | Mapping[str, object] | None,
    ) -> MemoryCompactionResult:
        safe_request = MemoryCompactionRequest.model_validate(request)
        safe_policy = (
            policy
            if isinstance(policy, MemoryCompactionPolicy)
            else MemoryCompactionPolicy.model_validate(policy)
            if policy is not None
            else None
        )
        policy_digest = _policy_snapshot_digest(safe_policy)
        if not self.config.enabled:
            return _result(
                status="disabled",
                reason_codes=("memory_compaction_boundary_disabled",),
                request=safe_request,
                policy=safe_policy,
                policy_snapshot_digest=policy_digest,
                executed=False,
                local_test_only=False,
            )
        denial_status, denial_reasons = _compaction_denial_reasons(safe_request, safe_policy)
        if denial_status is not None:
            return _result(
                status=denial_status,
                reason_codes=denial_reasons,
                request=safe_request,
                policy=safe_policy,
                policy_snapshot_digest=policy_digest,
                executed=False,
                local_test_only=False,
            )
        assert safe_policy is not None
        if (
            not self.config.local_fake_adapter_enabled
            or not safe_policy.local_fake_compaction_allowed
        ):
            return _result(
                status="blocked",
                reason_codes=("local_fake_memory_compaction_disabled",),
                request=safe_request,
                policy=safe_policy,
                policy_snapshot_digest=policy_digest,
                executed=False,
                local_test_only=False,
            )
        return _result(
            status="success",
            reason_codes=("local_fake_memory_compaction_receipt",),
            request=safe_request,
            policy=safe_policy,
            policy_snapshot_digest=policy_digest,
            executed=True,
            local_test_only=True,
        )


def _compaction_denial_reasons(
    request: MemoryCompactionRequest,
    policy: MemoryCompactionPolicy | None,
) -> tuple[MemoryCompactionStatus | None, tuple[str, ...]]:
    if policy is None:
        return "blocked", ("missing_memory_compaction_policy",)
    if policy.evidence_required and not request.evidence_refs:
        return "blocked", ("missing_memory_compaction_evidence",)
    if policy.approval_required and request.approval_ref is None:
        return "approval_required", ("missing_memory_compaction_approval",)
    if not request.source_refs:
        return "blocked", ("missing_memory_compaction_sources",)
    if policy.redaction_status not in {"verified", "not_required"}:
        return "blocked", ("memory_compaction_redaction_not_verified",)
    if policy.retention_state != "active":
        return "blocked", ("memory_compaction_retention_expired_denied",)
    if policy.erase_state != "active":
        return "blocked", ("memory_compaction_erase_state_denied",)
    if request.private_payload:
        return "blocked", ("private_memory_payload_denied",)
    if request.child_memory_isolated:
        return "blocked", ("child_memory_scope_isolated",)
    return None, ()


def _result(
    *,
    status: MemoryCompactionStatus,
    reason_codes: Sequence[str],
    request: MemoryCompactionRequest,
    policy: MemoryCompactionPolicy | None,
    policy_snapshot_digest: str,
    executed: bool,
    local_test_only: bool,
) -> MemoryCompactionResult:
    receipt = MemoryCompactionReceipt(
        receiptId=_receipt_id(request, status),
        providerId=request.provider_id,
        turnId=request.turn_id,
        status=status,
        executed=executed,
        sourceRefs=request.source_refs,
        excludedRefs=request.excluded_refs,
        redactionStatus=policy.redaction_status if policy is not None else "unverified",
        outputDigest=_output_digest(request, executed),
        policySnapshotDigest=policy_snapshot_digest,
        policySnapshotRef=policy.policy_snapshot_ref if policy is not None else "policy:none",
        reasonCodes=tuple(reason_codes),
        localTestOnly=local_test_only,
        authorityFlags=MemoryWriteAuthorityFlags(),
    )
    return MemoryCompactionResult(
        status=status,
        reasonCodes=tuple(reason_codes),
        receipt=receipt,
        authorityFlags=MemoryWriteAuthorityFlags(),
    )


def _receipt_id(request: MemoryCompactionRequest, status: MemoryCompactionStatus) -> str:
    return "memory-compaction:" + sha256_hex(
        "\0".join(
            (
                request.provider_id,
                request.turn_id,
                status,
                *request.source_refs,
                *request.excluded_refs,
            )
        )
    ).removeprefix("sha256:")


def _output_digest(request: MemoryCompactionRequest, executed: bool) -> str:
    if executed and request.output_text is not None:
        return _sanitized_digest(request.output_text)
    source_digest_input = "\0".join((*request.source_refs, *request.excluded_refs))
    return _sanitized_digest(source_digest_input)


def _policy_snapshot_digest(policy: MemoryCompactionPolicy | None) -> str:
    payload = (
        {"policy": None}
        if policy is None
        else policy.model_dump(by_alias=True, mode="json", warnings=False)
    )
    return sha256_hex(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


__all__ = [
    "MemoryCompactionHarness",
    "MemoryCompactionHarnessConfig",
    "MemoryCompactionPolicy",
    "MemoryCompactionReceipt",
    "MemoryCompactionRequest",
    "MemoryCompactionResult",
    "MemoryCompactionStatus",
]
