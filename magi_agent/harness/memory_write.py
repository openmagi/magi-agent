from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.memory.write_boundary import (
    MemoryMutationReceipt,
    MemoryMutationTarget,
    MemoryWriteAuthorityFlags,
    MemoryWriteClaim,
    fake_successful_test_receipt,
    plan_memory_mutation,
    sha256_hex,
    _sanitize_path_ref,
    _sanitize_public_text,
)


MemoryWriteHarnessStatus: TypeAlias = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "success",
]
MemoryWriteHarnessOperation: TypeAlias = Literal[
    "remember",
    "write",
    "redact",
    "delete",
    "erase",
    "retention_expire",
]
MemoryHarnessRedactionStatus: TypeAlias = Literal[
    "verified",
    "not_required",
    "unverified",
    "failed",
]
MemoryHarnessRetentionState: TypeAlias = Literal["active", "expired", "suspended"]
MemoryHarnessEraseState: TypeAlias = Literal["active", "erased", "tombstoned"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_CORE_MUTATION_OPERATIONS = frozenset({"remember", "write", "redact", "delete"})
_PRIVATE_REASON_CODES = frozenset(
    {
        "private_memory_payload_denied",
        "child_memory_scope_isolated",
    }
)
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$", re.IGNORECASE)
_SAFE_RECEIPT_REF_RE = re.compile(
    r"^(?:memory|memory-compaction|evidence|approval|policy|policy-snapshot|source|"
    r"artifact|receipt|sha256):"
    r"[A-Za-z0-9_.:/=-]{1,191}$|^memory/[A-Za-z0-9_.:/=-]{1,191}$"
)
_SAFE_REDACTION_TOKEN_RE = re.compile(r"^\[[a-z0-9_.:-]*redacted[a-z0-9_.:-]*\]$")
_UNSAFE_REF_MARKER_RE = re.compile(
    r"raw[-_:]?(?:source|output|result|text|prompt|transcript|tool|log|args|"
    r"policy|snapshot|config|control|metadata|selector|recipe|authority|instruction)|"
    r"private[-_:]?memory|tool[-_:]?log|child[-_:]?prompt|hidden[-_:]?reasoning|"
    r"authorization|cookie|session|token|secret|credential|private[-_:]?key|api[-_:]?key|"
    r"bearer|connector[-_:]?token|password|"
    r"policy[-_:]?snapshot[-_:]?(?:text|prompt|payload|raw)|"
    r"control[-_:]?metadata|selector[-_:]?payload|recipe[-_:]?prompt|"
    r"authority[-_:]?payload|instruction[-_:]?payload",
    re.IGNORECASE,
)


class MemoryWriteHarnessConfig(BaseModel):
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


class MemoryWritePolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_ref: str = Field(alias="policyRef")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    approval_required: bool = Field(default=False, alias="approvalRequired")
    evidence_required: bool = Field(default=True, alias="evidenceRequired")
    local_fake_success_allowed: bool = Field(default=False, alias="localFakeSuccessAllowed")
    allowed_operations: tuple[str, ...] = Field(
        default=("remember", "write", "redact"),
        alias="allowedOperations",
    )
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
        return _sanitize_public_ref(str(value or "policy:unspecified"))

    @field_validator("allowed_operations", mode="before")
    @classmethod
    def _sanitize_allowed_operations(cls, value: object) -> tuple[str, ...]:
        return tuple(_string_tuple(value))

    @field_serializer("production_write_enabled", "provider_call_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False


class MemoryWriteRequest(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    turn_id: str = Field(alias="turnId")
    operation: MemoryWriteHarnessOperation
    content: str | None = Field(default=None, exclude=True)
    target_text: str | None = Field(default=None, alias="targetText", exclude=True)
    target_sha256: str | None = Field(default=None, alias="targetSha256")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    path_refs: tuple[str, ...] = Field(default=(), alias="pathRefs")
    private_payload: bool = Field(default=False, alias="privatePayload")
    child_memory_isolated: bool = Field(default=False, alias="childMemoryIsolated")
    child_prompt: str | None = Field(default=None, alias="childPrompt", exclude=True)
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True)

    @field_validator("provider_id", "turn_id", mode="before")
    @classmethod
    def _sanitize_public_ids(cls, value: object) -> str:
        return _sanitize_public_id(str(value or "memory-write"), prefix="memory-id")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_public_ref(item) for item in _string_tuple(value))

    @field_validator("approval_ref", mode="before")
    @classmethod
    def _sanitize_approval_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return _sanitize_public_ref(str(value))

    @field_validator("path_refs", mode="before")
    @classmethod
    def _sanitize_path_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_public_ref(item) for item in _string_tuple(value))

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

    def target_hash(self) -> str:
        if self.target_sha256:
            return _sanitize_target_digest(self.target_sha256)
        source = self.target_text if self.target_text is not None else self.content
        if source is None:
            return "sha256:unspecified"
        return sha256_hex(source)


class MemoryWriteHarnessResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: MemoryWriteHarnessStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    receipt: MemoryMutationReceipt | None = None
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    authority_flags: MemoryWriteAuthorityFlags = Field(
        default_factory=MemoryWriteAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_reason_code(item) for item in _string_tuple(value))

    def write_claim(self) -> MemoryWriteClaim:
        if self.receipt is None:
            raise ValueError("memory write claim requires a receipt")
        return MemoryWriteClaim(
            providerId=self.receipt.provider_id,
            turnId=self.receipt.turn_id,
            operation=self.receipt.operation,
            targetSha256=self.receipt.target.target_sha256,
        )

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": tuple(_sanitize_reason_code(item) for item in self.reason_codes),
            "receipt": self.receipt.public_projection() if self.receipt is not None else None,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class MemoryWriteHarness:
    """Default-off policy boundary for future ADK MemoryService writes."""

    def __init__(
        self,
        config: MemoryWriteHarnessConfig | Mapping[str, object] | None = None,
        *,
        adapter: object | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, MemoryWriteHarnessConfig)
            else MemoryWriteHarnessConfig.model_validate(config or {})
        )
        self.adapter = adapter

    async def write(
        self,
        *,
        request: MemoryWriteRequest | Mapping[str, object],
        policy: MemoryWritePolicy | Mapping[str, object] | None,
    ) -> MemoryWriteHarnessResult:
        safe_request = MemoryWriteRequest.model_validate(request)
        safe_policy = (
            policy
            if isinstance(policy, MemoryWritePolicy)
            else MemoryWritePolicy.model_validate(policy)
            if policy is not None
            else None
        )
        policy_digest = _policy_snapshot_digest(safe_policy)
        planned_receipt = _planned_receipt_for_request(safe_request)

        if not self.config.enabled:
            return _result(
                status="disabled",
                reason_codes=("memory_write_boundary_disabled",),
                receipt=planned_receipt,
                policy_snapshot_digest=policy_digest,
            )
        denial_status, denial_reasons = _write_denial_reasons(safe_request, safe_policy)
        if denial_status is not None:
            return _result(
                status=denial_status,
                reason_codes=denial_reasons,
                receipt=planned_receipt if not _contains_private_denial(denial_reasons) else None,
                policy_snapshot_digest=policy_digest,
            )
        assert safe_policy is not None
        if not self.config.local_fake_adapter_enabled or not safe_policy.local_fake_success_allowed:
            return _result(
                status="blocked",
                reason_codes=("local_fake_memory_write_disabled",),
                receipt=planned_receipt,
                policy_snapshot_digest=policy_digest,
            )
        core_operation = safe_request.operation
        if core_operation not in _CORE_MUTATION_OPERATIONS:
            return _result(
                status="blocked",
                reason_codes=("memory_operation_not_supported",),
                receipt=planned_receipt,
                policy_snapshot_digest=policy_digest,
            )

        receipt = fake_successful_test_receipt(
            provider_id=safe_request.provider_id,
            turn_id=safe_request.turn_id,
            operation=core_operation,  # type: ignore[arg-type]
            target_sha256=safe_request.target_hash(),
        )
        return _result(
            status="success",
            reason_codes=("local_fake_memory_write_receipt",),
            receipt=receipt,
            policy_snapshot_digest=policy_digest,
        )


def _planned_receipt_for_request(request: MemoryWriteRequest) -> MemoryMutationReceipt | None:
    if request.operation not in _CORE_MUTATION_OPERATIONS:
        return None
    return plan_memory_mutation(
        {
            "providerId": request.provider_id,
            "turnId": request.turn_id,
            "operation": request.operation,
            "targetSha256": request.target_hash(),
            "pathRefs": request.path_refs,
            "childMemoryIsolated": request.child_memory_isolated,
            "childPrompt": request.child_prompt,
            "toolLogs": request.tool_logs,
        }
    )


def _write_denial_reasons(
    request: MemoryWriteRequest,
    policy: MemoryWritePolicy | None,
) -> tuple[MemoryWriteHarnessStatus | None, tuple[str, ...]]:
    if policy is None:
        return "blocked", ("missing_memory_write_policy",)
    if request.operation == "erase":
        return "blocked", ("memory_erase_denied",)
    if request.operation == "delete":
        return "blocked", ("memory_delete_denied",)
    if request.operation == "retention_expire":
        return "blocked", ("memory_retention_expiration_mutation_denied",)
    if request.operation not in policy.allowed_operations:
        return "blocked", ("memory_operation_not_allowed_by_policy",)
    if policy.evidence_required and not request.evidence_refs:
        return "blocked", ("missing_memory_write_evidence",)
    if policy.approval_required and request.approval_ref is None:
        return "approval_required", ("missing_memory_write_approval",)
    if policy.retention_state != "active":
        return "blocked", ("memory_retention_expired_denied",)
    if policy.erase_state != "active":
        return "blocked", ("memory_erase_state_denied",)
    if policy.redaction_status not in {"verified", "not_required"}:
        return "blocked", ("memory_redaction_not_verified",)
    if request.private_payload:
        return "blocked", ("private_memory_payload_denied",)
    if request.child_memory_isolated:
        return "blocked", ("child_memory_scope_isolated",)
    return None, ()


def _result(
    *,
    status: MemoryWriteHarnessStatus,
    reason_codes: Sequence[str],
    receipt: MemoryMutationReceipt | None,
    policy_snapshot_digest: str,
) -> MemoryWriteHarnessResult:
    return MemoryWriteHarnessResult(
        status=status,
        reasonCodes=tuple(reason_codes),
        receipt=receipt,
        policySnapshotDigest=policy_snapshot_digest,
        authorityFlags=MemoryWriteAuthorityFlags(),
    )


def _policy_snapshot_digest(policy: MemoryWritePolicy | None) -> str:
    payload = (
        {"policy": None}
        if policy is None
        else policy.model_dump(by_alias=True, mode="json", warnings=False)
    )
    return sha256_hex(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _contains_private_denial(reason_codes: Sequence[str]) -> bool:
    return any(reason in _PRIVATE_REASON_CODES for reason in reason_codes)


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


def _sanitize_public_ref(value: str) -> str:
    if value.startswith("[private-") and _SAFE_REDACTION_TOKEN_RE.fullmatch(value):
        return value
    path_sanitized = _sanitize_path_ref(value)
    if path_sanitized.startswith("[private-"):
        return path_sanitized
    clean = _sanitize_public_text(path_sanitized)
    if _SAFE_REDACTION_TOKEN_RE.fullmatch(clean):
        return clean
    if _SAFE_RECEIPT_REF_RE.fullmatch(clean) and not _has_unsafe_ref_marker(clean):
        return clean
    return "ref:" + sha256_hex(value).removeprefix("sha256:")


def _sanitize_public_id(value: str, *, prefix: str) -> str:
    path_sanitized = _sanitize_path_ref(value)
    clean = _sanitize_public_text(path_sanitized)
    if (
        clean != value.strip()
        or clean.startswith("[")
        or not _SAFE_ID_RE.fullmatch(clean)
        or _has_unsafe_ref_marker(clean)
    ):
        return f"{prefix}:" + sha256_hex(value).removeprefix("sha256:")
    return clean


def _sanitize_target_digest(value: str) -> str:
    clean = str(value).strip().lower()
    if _SHA256_REF_RE.fullmatch(clean):
        return clean
    return sha256_hex(str(value))


def _sanitize_reason_code(value: str) -> str:
    raw = str(value).strip().lower().replace(" ", "_")
    if raw and all(char.isalnum() or char in "_:-." for char in raw):
        return raw[:160]
    clean = _sanitize_public_text(value).strip().lower().replace(" ", "_")
    if not clean:
        return "memory_boundary_reason"
    return clean


def _sanitized_digest(value: str) -> str:
    return sha256_hex(value)


def _has_unsafe_ref_marker(value: str) -> bool:
    return _UNSAFE_REF_MARKER_RE.search(value) is not None


__all__ = [
    "MemoryWriteHarness",
    "MemoryWriteHarnessConfig",
    "MemoryWriteHarnessResult",
    "MemoryWritePolicy",
    "MemoryWriteRequest",
    "MemoryWriteHarnessStatus",
    "MemoryWriteHarnessOperation",
    "MemoryHarnessRedactionStatus",
    "MemoryHarnessRetentionState",
    "MemoryHarnessEraseState",
    "MemoryMutationReceipt",
    "MemoryMutationTarget",
    "_sanitize_public_ref",
    "_sanitize_public_id",
    "_sanitize_reason_code",
    "_sanitized_digest",
]
