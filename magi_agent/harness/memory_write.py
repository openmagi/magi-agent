from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

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
from magi_agent.memory.declarative_filter import is_declarative_result


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


class MemoryWriteEvidenceRecord(BaseModel):
    """Lightweight evidence anchor for a memory write outcome (real or simulated).

    This is a simplified local evidence record — NOT the full ``EvidenceRecord``
    from ``magi_agent.evidence.types`` (which requires ADK deps on the import
    path).  The harness emits this for every write attempt so that callers can
    audit the outcome without touching production-write-protected fields.
    """

    model_config = _MODEL_CONFIG

    type: str = "MemoryWriteAttempt"
    status: Literal["ok", "failed", "blocked", "simulated"] = "simulated"
    is_real_write: bool = Field(default=False, alias="isRealWrite")
    is_declarative: bool = Field(default=True, alias="isDeclarative")
    provider_id: str = Field(alias="providerId")
    turn_id: str = Field(alias="turnId")
    operation: str
    observed_at: float = Field(alias="observedAt")
    rejection_reason: str | None = Field(default=None, alias="rejectionReason")


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
    # D2: lightweight evidence record — present for every write attempt
    evidence_record: MemoryWriteEvidenceRecord | None = Field(
        default=None,
        alias="evidenceRecord",
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
    """Default-off policy boundary for future ADK MemoryService writes.

    D2 extension: when MAGI_MEMORY_WRITE_ENABLED is set AND a writable
    LocalFileMemoryProvider is injected as ``adapter``, the harness calls
    ``adapter.remember(...)`` to persist the fact to disk.  When the env gate
    is off (default) or no provider is injected the behaviour is byte-identical
    to D1 (simulated local-fake receipt only — no file written).

    Declarative-only gate (D2): facts matching task-state patterns (PR numbers,
    commit SHAs, "done/merged/in progress") are rejected before any write.
    """

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

        # ------------------------------------------------------------------ #
        # D2: Declarative-only gate — reject task-state facts before writing  #
        # ------------------------------------------------------------------ #
        body = safe_request.content or ""
        if body:
            filter_result = is_declarative_result(body)
            if not filter_result.accepted:
                ev = MemoryWriteEvidenceRecord(
                    status="blocked",
                    isRealWrite=False,
                    isDeclarative=False,
                    providerId=safe_request.provider_id,
                    turnId=safe_request.turn_id,
                    operation=core_operation,
                    observedAt=time.time(),
                    rejectionReason=filter_result.rejection_reason,
                )
                return _result(
                    status="blocked",
                    reason_codes=("non_declarative_fact_rejected",),
                    receipt=planned_receipt,
                    policy_snapshot_digest=policy_digest,
                    evidence_record=ev,
                )

        # ------------------------------------------------------------------ #
        # D2: Gated real-write path — both env gate AND injected provider     #
        # ------------------------------------------------------------------ #
        real_write_attempted = False
        if body and self.adapter is not None:
            try:
                real_write_attempted = await _attempt_real_write(
                    self.adapter, body, core_operation
                )
            except _ProviderWriteRejectedError as exc:
                # Provider explicitly rejected the write (e.g. byte cap exceeded).
                # Surface as FAILED — do NOT fall through to simulated-success.
                planned_receipt = _planned_receipt_for_request(safe_request)
                ev = MemoryWriteEvidenceRecord(
                    status="failed",
                    isRealWrite=False,
                    isDeclarative=True,
                    providerId=safe_request.provider_id,
                    turnId=safe_request.turn_id,
                    operation=core_operation,
                    observedAt=time.time(),
                    rejectionReason=exc.reason,
                )
                return _result(
                    status="blocked",
                    reason_codes=("provider_write_rejected",),
                    receipt=planned_receipt,
                    policy_snapshot_digest=policy_digest,
                    evidence_record=ev,
                )

        receipt = fake_successful_test_receipt(
            provider_id=safe_request.provider_id,
            turn_id=safe_request.turn_id,
            operation=core_operation,  # type: ignore[arg-type]
            target_sha256=safe_request.target_hash(),
        )
        ev = MemoryWriteEvidenceRecord(
            status="ok" if real_write_attempted else "simulated",
            isRealWrite=real_write_attempted,
            isDeclarative=True,
            providerId=safe_request.provider_id,
            turnId=safe_request.turn_id,
            operation=core_operation,
            observedAt=time.time(),
        )
        reason = (
            "memory_write_real_provider_receipt"
            if real_write_attempted
            else "local_fake_memory_write_receipt"
        )
        return _result(
            status="success",
            reason_codes=(reason,),
            receipt=receipt,
            policy_snapshot_digest=policy_digest,
            evidence_record=ev,
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
    evidence_record: MemoryWriteEvidenceRecord | None = None,
) -> MemoryWriteHarnessResult:
    return MemoryWriteHarnessResult(
        status=status,
        reasonCodes=tuple(reason_codes),
        receipt=receipt,
        policySnapshotDigest=policy_snapshot_digest,
        authorityFlags=MemoryWriteAuthorityFlags(),
        evidenceRecord=evidence_record,
    )


class _ProviderWriteRejectedError(Exception):
    """Raised by ``_attempt_real_write`` when the provider rejects the write
    with a ``ValueError`` (e.g. per-write or cumulative byte cap exceeded).

    Distinct from ``UnsupportedMemoryOperationError`` (gate-off) so the
    caller can surface a FAILED outcome rather than masking it as a simulated
    success.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _attempt_real_write(
    adapter: object,
    body: str,
    operation: str,
) -> bool:
    """Attempt a real write via the injected provider.

    Returns True if the write was attempted and succeeded; False if the
    provider is not writable or the gate is off (caller falls back to
    simulated path).

    Raises ``_ProviderWriteRejectedError`` if the provider raises
    ``ValueError`` (byte-cap exceeded or other validation failure) so that
    the caller can surface a FAILED outcome rather than masking it as a
    simulated success.

    The write gate is ultimately enforced by D1's LocalFileMemoryProvider:
    - provider._write_active must be True (set by MAGI_MEMORY_WRITE_ENABLED or
      explicit write_enabled=True in config)
    - If the gate is off the provider raises UnsupportedMemoryOperationError
      and we silently fall back to the simulated path.
    """
    # Import here to avoid circular imports; lazy import also keeps the
    # harness boundary clean at module load time.
    from magi_agent.memory.adapters.local_file_writable import LocalFileMemoryProvider
    from magi_agent.memory.contracts import UnsupportedMemoryOperationError

    if not isinstance(adapter, LocalFileMemoryProvider):
        return False
    if not adapter._write_active:
        return False

    try:
        await adapter.remember({
            "body": body,
            "kind": "fact",
            "target_file": "MEMORY.md",
        })
        return True
    except UnsupportedMemoryOperationError:
        # Gate is off on the provider side — fall back to simulated
        return False
    except ValueError as exc:
        # Provider rejected the write (byte cap, path safety, etc.) —
        # re-raise as a typed error so the harness reports FAILED, not simulated
        raise _ProviderWriteRejectedError(str(exc)) from exc
    except Exception:  # noqa: BLE001
        # Unexpected I/O or other runtime error — fail-open, return False
        # so the caller can still fall back to simulated without crashing.
        return False


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


def build_gated_live_learning_write_harness(
    *,
    readiness: object,
    bot_id: str,
    user_id: str,
) -> "MemoryWriteHarness | None":
    """PR7 — gated REAL learning store-write binding.

    Returns an ENABLED ``MemoryWriteHarness`` (the real proposed-candidate
    store-write path) ONLY when the learning-live readiness gate
    (``gates/learning_live_readiness``) resolves to ``live`` for the given
    scope.  Otherwise returns ``None`` — the caller keeps the disabled / shadow
    path, byte-identical to PR1–PR6 (shadow is observe-only: no store write).

    Even when enabled, every ``Literal[False]`` authority flag on
    ``MemoryWriteHarnessConfig`` stays frozen-False (the model validator coerces
    them), so the live store-write still flows through the local-fake receipt
    boundary — the promotion is recorded in the ``learning/live`` audit, never by
    flipping a flag.  Imports are lazy to keep the default import path unchanged.
    """
    from magi_agent.gates.learning_live_readiness import (  # lazy: keep seam thin
        LearningLiveReadinessConfig,
        resolve_learning_live_execution_mode,
    )

    if not isinstance(readiness, LearningLiveReadinessConfig):
        return None
    mode = resolve_learning_live_execution_mode(
        readiness, bot_id=bot_id, user_id=user_id
    )
    if mode != "live":
        return None
    return MemoryWriteHarness({"enabled": True, "localFakeAdapterEnabled": True})


__all__ = [
    "MemoryWriteHarness",
    "MemoryWriteHarnessConfig",
    "MemoryWriteHarnessResult",
    "MemoryWriteEvidenceRecord",
    "build_gated_live_learning_write_harness",
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
