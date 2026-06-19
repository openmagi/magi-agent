from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel


MemoryMutationOperation: TypeAlias = Literal[
    "remember",
    "write",
    "redact",
    "delete",
    "compact",
    "decay",
    "export",
]
MemoryMutationStatus: TypeAlias = Literal[
    "blocked",
    "approval_required",
    "unsupported",
    "success",
]
MemoryFailureKind: TypeAlias = Literal[
    "default",
    "redaction_failed",
    "provider_unavailable",
    "stale_conflict",
]
MemoryBackendKind: TypeAlias = Literal[
    "hipocampus_qmd",
    "agent_memory",
    "external_vector",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_LOCAL_TEST_RECEIPT_MARKER = "openmagi-memory-local-test-receipt-v1"
_LOCAL_TEST_RECEIPT_SECRET = "openmagi-write-boundary-local-test-receipt-v1"
_PUBLIC_TEXT_MAX_LENGTH = 400
_SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class MemoryWriteAuthorityFlags(FalseOnlyAuthorityModel):
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    memory_redact_allowed: Literal[False] = Field(default=False, alias="memoryRedactAllowed")
    memory_delete_allowed: Literal[False] = Field(default=False, alias="memoryDeleteAllowed")
    provider_call_allowed: Literal[False] = Field(default=False, alias="providerCallAllowed")
    filesystem_write_allowed: Literal[False] = Field(
        default=False,
        alias="filesystemWriteAllowed",
    )
    database_write_allowed: Literal[False] = Field(default=False, alias="databaseWriteAllowed")
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )


class MemoryMutationTarget(BaseModel):
    model_config = _MODEL_CONFIG

    target_sha256: str = Field(alias="targetSha256")
    target_byte_length: int = Field(default=0, alias="targetByteLength", ge=0)
    path_refs: tuple[str, ...] = Field(default=(), alias="pathRefs")
    raw_target_text: None = Field(default=None, alias="rawTargetText", exclude=True)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_validator("target_sha256", mode="before")
    @classmethod
    def _sanitize_target_sha256(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return _sanitize_public_text(value)

    @field_validator("path_refs", mode="before")
    @classmethod
    def _sanitize_path_refs(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (_sanitize_path_ref(value),)
        if isinstance(value, Sequence) and not isinstance(value, bytes):
            return tuple(_sanitize_path_ref(str(item)) for item in value)
        return value


class MemoryMutationIntent(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    turn_id: str = Field(alias="turnId")
    operation: MemoryMutationOperation
    target_sha256: str | None = Field(default=None, alias="targetSha256")
    target_text: str | None = Field(default=None, alias="targetText")
    path_refs: tuple[str, ...] = Field(default=(), alias="pathRefs")
    content: str | None = None
    matched_count: int = Field(default=0, alias="matchedCount", ge=0)
    target_still_present: bool = Field(default=False, alias="targetStillPresent")
    failure_kind: MemoryFailureKind = Field(default="default", alias="failureKind")
    child_memory_isolated: bool = Field(default=False, alias="childMemoryIsolated")
    child_prompt: str | None = Field(default=None, alias="childPrompt")
    tool_logs: str | None = Field(default=None, alias="toolLogs")

    @model_validator(mode="before")
    @classmethod
    def _accept_snake_case_tool_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        rewrites = {
            "provider_id": "providerId",
            "turn_id": "turnId",
            "target_sha256": "targetSha256",
            "target_text": "targetText",
            "path_refs": "pathRefs",
            "matched_count": "matchedCount",
            "target_still_present": "targetStillPresent",
            "failure_kind": "failureKind",
            "child_memory_isolated": "childMemoryIsolated",
            "child_prompt": "childPrompt",
            "tool_logs": "toolLogs",
        }
        return {
            rewrites.get(str(key), str(key)): item
            for key, item in value.items()
        }

    def target_descriptor(self) -> MemoryMutationTarget:
        source_text = self.target_text if self.target_text is not None else self.content
        target_sha = self.target_sha256 or (
            sha256_hex(source_text) if source_text is not None else "sha256:unspecified"
        )
        return MemoryMutationTarget(
            targetSha256=target_sha,
            targetByteLength=len(source_text.encode("utf-8")) if source_text is not None else 0,
            pathRefs=tuple(_sanitize_path_ref(item) for item in self.path_refs),
        )


class MemoryMutationReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    receipt_id: str = Field(alias="receiptId")
    provider_id: str = Field(alias="providerId")
    turn_id: str = Field(alias="turnId")
    operation: MemoryMutationOperation
    status: MemoryMutationStatus
    executed: bool = False
    memory_write_allowed: bool = Field(default=False, alias="memoryWriteAllowed")
    production_write_enabled: bool = Field(default=False, alias="productionWriteEnabled")
    provider_call_attempted: bool = Field(default=False, alias="providerCallAttempted")
    filesystem_mutation_attempted: bool = Field(
        default=False,
        alias="filesystemMutationAttempted",
    )
    production_receipt: bool = Field(default=False, alias="productionReceipt")
    local_test_only: bool = Field(default=False, alias="localTestOnly")
    target: MemoryMutationTarget
    matched_count: int = Field(default=0, alias="matchedCount", ge=0)
    target_still_present: bool = Field(default=False, alias="targetStillPresent")
    error_code: str = Field(default="memory_receipt_unverified", alias="errorCode")
    message: str = "Memory receipt has not been verified for production writes."
    local_test_receipt_marker: str | None = Field(
        default=None,
        alias="localTestReceiptMarker",
        exclude=True,
    )
    local_test_receipt_signature: str | None = Field(
        default=None,
        alias="localTestReceiptSignature",
        exclude=True,
    )
    authority_flags: MemoryWriteAuthorityFlags = Field(
        default_factory=MemoryWriteAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _accept_snake_case_tool_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        rewrites = {
            "receipt_id": "receiptId",
            "provider_id": "providerId",
            "turn_id": "turnId",
            "memory_write_allowed": "memoryWriteAllowed",
            "production_write_enabled": "productionWriteEnabled",
            "provider_call_attempted": "providerCallAttempted",
            "filesystem_mutation_attempted": "filesystemMutationAttempted",
            "production_receipt": "productionReceipt",
            "local_test_only": "localTestOnly",
            "matched_count": "matchedCount",
            "target_still_present": "targetStillPresent",
            "error_code": "errorCode",
            "local_test_receipt_marker": "localTestReceiptMarker",
            "local_test_receipt_signature": "localTestReceiptSignature",
            "authority_flags": "authorityFlags",
        }
        return {
            rewrites.get(str(key), str(key)): item
            for key, item in value.items()
        }

    @field_validator("message", mode="before")
    @classmethod
    def _sanitize_message(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return _sanitize_public_text(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "receiptId": _sanitize_receipt_id(self.receipt_id),
            "providerId": _sanitize_provider_id(self.provider_id),
            "turnId": _sanitize_turn_id(self.turn_id),
            "operation": self.operation,
            "status": self.status if not self._has_unsafe_execution_claim() else "blocked",
            "executed": False if self._has_unsafe_execution_claim() else self.executed,
            "memoryWriteAllowed": False,
            "productionWriteEnabled": False,
            "providerCallAttempted": False,
            "filesystemMutationAttempted": False,
            "productionReceipt": False,
            "localTestOnly": self.local_test_only,
            "target": self.target.model_dump(by_alias=True),
            "matchedCount": self.matched_count,
            "targetStillPresent": self.target_still_present,
            "errorCode": _sanitize_error_code(self.error_code),
            "message": _sanitize_public_text(self.message),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }

    @property
    def is_successful_local_test_receipt(self) -> bool:
        return (
            self.status == "success"
            and self.executed is True
            and self.local_test_only is True
            and self.production_receipt is False
            and self.production_write_enabled is False
            and self.memory_write_allowed is False
            and self.provider_call_attempted is False
            and self.filesystem_mutation_attempted is False
            and self.local_test_receipt_marker == _LOCAL_TEST_RECEIPT_MARKER
            and self.local_test_receipt_signature == _local_test_receipt_signature(
                receipt_id=self.receipt_id,
                provider_id=self.provider_id,
                turn_id=self.turn_id,
                operation=self.operation,
                target_sha256=self.target.target_sha256,
                matched_count=self.matched_count,
                target_still_present=self.target_still_present,
            )
        )

    def _has_unsafe_execution_claim(self) -> bool:
        return (
            self.production_receipt is True
            or self.production_write_enabled is True
            or self.memory_write_allowed is True
            or self.provider_call_attempted is True
            or self.filesystem_mutation_attempted is True
        )


class MemoryWriteClaim(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    turn_id: str = Field(alias="turnId")
    operation: MemoryMutationOperation
    target_sha256: str = Field(alias="targetSha256")

    @model_validator(mode="before")
    @classmethod
    def _accept_snake_case_tool_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        rewrites = {
            "provider_id": "providerId",
            "turn_id": "turnId",
            "target_sha256": "targetSha256",
        }
        return {
            rewrites.get(str(key), str(key)): item
            for key, item in value.items()
        }


class MemoryWriteClaimDecision(BaseModel):
    model_config = _MODEL_CONFIG

    allowed: bool
    reason_code: Literal[
        "local_test_only_receipt_matched",
        "missing_successful_receipt",
        "production_receipts_disabled",
    ] = Field(alias="reasonCode")
    receipt: MemoryMutationReceipt | None = None
    message: str


class MemoryBackendCapabilities(BaseModel):
    model_config = _MODEL_CONFIG

    supports_search: bool = Field(default=False, alias="supportsSearch")
    supports_remember: bool = Field(default=False, alias="supportsRemember")
    supports_redact: bool = Field(default=False, alias="supportsRedact")
    supports_delete: bool = Field(default=False, alias="supportsDelete")
    supports_compact: bool = Field(default=False, alias="supportsCompact")
    supports_decay: bool = Field(default=False, alias="supportsDecay")
    supports_export: bool = Field(default=False, alias="supportsExport")
    supports_bitemporal: bool = Field(default=False, alias="supportsBitemporal")
    supports_vector: bool = Field(default=False, alias="supportsVector")


class MemoryBackendDescriptor(FalseOnlyAuthorityModel):
    provider_id: str = Field(alias="providerId")
    kind: MemoryBackendKind
    display_name: str = Field(alias="displayName")
    optional_candidate: bool = Field(alias="optionalCandidate")
    enabled: Literal[False] = False
    provider_calls_enabled: Literal[False] = Field(default=False, alias="providerCallsEnabled")
    provider_sdk_import_allowed: Literal[False] = Field(
        default=False,
        alias="providerSdkImportAllowed",
    )
    memory_write_allowed: Literal[False] = Field(default=False, alias="memoryWriteAllowed")
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    capabilities: MemoryBackendCapabilities
    activation_blockers: tuple[str, ...] = Field(alias="activationBlockers")


def sha256_hex(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def plan_memory_mutation(
    intent: MemoryMutationIntent | Mapping[str, Any],
) -> MemoryMutationReceipt:
    safe_intent = MemoryMutationIntent.model_validate(intent)
    target = safe_intent.target_descriptor()
    status, error_code, message = _denial_for_intent(safe_intent)
    return MemoryMutationReceipt(
        receiptId=_receipt_id(safe_intent),
        providerId=safe_intent.provider_id,
        turnId=safe_intent.turn_id,
        operation=safe_intent.operation,
        status=status,
        executed=False,
        memoryWriteAllowed=False,
        productionWriteEnabled=False,
        providerCallAttempted=False,
        filesystemMutationAttempted=False,
        productionReceipt=False,
        localTestOnly=False,
        target=target,
        matchedCount=safe_intent.matched_count,
        targetStillPresent=safe_intent.target_still_present,
        errorCode=error_code,
        message=message,
        authorityFlags=MemoryWriteAuthorityFlags(),
    )


def fake_successful_test_receipt(
    *,
    provider_id: str,
    turn_id: str,
    operation: MemoryMutationOperation,
    target_sha256: str,
    matched_count: int = 0,
    target_still_present: bool = False,
) -> MemoryMutationReceipt:
    receipt_id = f"local-test-only:{provider_id}:{turn_id}:{operation}:{target_sha256}"
    return MemoryMutationReceipt(
        receiptId=receipt_id,
        providerId=provider_id,
        turnId=turn_id,
        operation=operation,
        status="success",
        executed=True,
        memoryWriteAllowed=False,
        productionWriteEnabled=False,
        providerCallAttempted=False,
        filesystemMutationAttempted=False,
        productionReceipt=False,
        localTestOnly=True,
        target=MemoryMutationTarget(
            targetSha256=target_sha256,
            targetByteLength=0,
            pathRefs=(),
        ),
        matchedCount=matched_count,
        targetStillPresent=target_still_present,
        errorCode="memory_local_test_only_success",
        localTestReceiptMarker=_LOCAL_TEST_RECEIPT_MARKER,
        localTestReceiptSignature=_local_test_receipt_signature(
            receipt_id=receipt_id,
            provider_id=provider_id,
            turn_id=turn_id,
            operation=operation,
            target_sha256=target_sha256,
            matched_count=matched_count,
            target_still_present=target_still_present,
        ),
        message=(
            "Local test-only receipt accepted for gating tests; no production "
            "memory write or provider call was performed."
        ),
        authorityFlags=MemoryWriteAuthorityFlags(),
    )


def evaluate_memory_write_claim(
    claim: MemoryWriteClaim | Mapping[str, Any],
    *,
    receipts: Sequence[MemoryMutationReceipt | Mapping[str, Any]],
    allow_local_test_receipts: bool = False,
) -> MemoryWriteClaimDecision:
    safe_claim = MemoryWriteClaim.model_validate(claim)
    matching = tuple(
        MemoryMutationReceipt.model_validate(receipt)
        for receipt in receipts
        if _receipt_matches_claim(MemoryMutationReceipt.model_validate(receipt), safe_claim)
    )
    if any(
        receipt.status == "success"
        and receipt.executed
        and (
            receipt.local_test_only is False
            or receipt.production_receipt
            or receipt.production_write_enabled
        )
        for receipt in matching
    ):
        return MemoryWriteClaimDecision(
            allowed=False,
            reasonCode="production_receipts_disabled",
            receipt=None,
            message="Production memory write receipts are disabled in this boundary.",
        )
    for receipt in matching:
        if allow_local_test_receipts and receipt.is_successful_local_test_receipt:
            return MemoryWriteClaimDecision(
                allowed=True,
                reasonCode="local_test_only_receipt_matched",
                receipt=receipt,
                message="Memory mutation claim is backed by matching local test-only receipt.",
            )
    return MemoryWriteClaimDecision(
        allowed=False,
        reasonCode="missing_successful_receipt",
        receipt=None,
        message=(
            "Memory mutation claims require a successful receipt for the same "
            "turn, provider, operation, and target hash."
        ),
    )


def provider_backend_descriptors() -> tuple[MemoryBackendDescriptor, ...]:
    return (
        MemoryBackendDescriptor(
            providerId="hipocampus-qmd-readonly",
            kind="hipocampus_qmd",
            displayName="Hipocampus QMD Read-Only",
            optionalCandidate=True,
            capabilities=MemoryBackendCapabilities(
                supportsSearch=True,
                supportsExport=True,
                supportsBitemporal=True,
            ),
            activationBlockers=(
                "write operations disabled by current memory contract",
                "provider remains read-only until receipt policy is approved",
            ),
        ),
        MemoryBackendDescriptor(
            providerId="agentmemory",
            kind="agent_memory",
            displayName="AgentMemory",
            optionalCandidate=True,
            capabilities=MemoryBackendCapabilities(
                supportsSearch=True,
                supportsRemember=True,
                supportsRedact=True,
                supportsDelete=True,
                supportsCompact=True,
                supportsDecay=True,
                supportsExport=True,
                supportsBitemporal=True,
            ),
            activationBlockers=(
                "no AgentMemory SDK dependency attached",
                "no provider lifecycle attachment approved",
                "production memory writes disabled",
                "receipt semantics limited to local test-only fakes",
            ),
        ),
        MemoryBackendDescriptor(
            providerId="external-vector",
            kind="external_vector",
            displayName="External Vector Provider",
            optionalCandidate=True,
            capabilities=MemoryBackendCapabilities(
                supportsSearch=True,
                supportsRemember=True,
                supportsRedact=True,
                supportsDelete=True,
                supportsExport=True,
                supportsVector=True,
            ),
            activationBlockers=(
                "no vector provider SDK dependency attached",
                "no network or provider credentials approved",
                "production memory writes disabled",
                "redaction authority contract not connected to a live backend",
            ),
        ),
    )


def _receipt_id(intent: MemoryMutationIntent) -> str:
    return f"planned:{intent.provider_id}:{intent.turn_id}:{intent.operation}"


def _sanitize_receipt_id(value: str) -> str:
    if _contains_sensitive_public_identifier(value):
        return f"memory-receipt:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return _sanitize_public_text(value)


def _sanitize_provider_id(value: str) -> str:
    if _contains_sensitive_public_identifier(value):
        return f"provider:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return _sanitize_public_text(value)


def _sanitize_turn_id(value: str) -> str:
    if _contains_sensitive_public_identifier(value):
        return f"turn:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return _sanitize_public_text(value)


def _sanitize_error_code(value: str) -> str:
    if (
        _SAFE_CODE_RE.fullmatch(value)
        and not _contains_sensitive_public_identifier(value)
    ):
        return value
    return f"memory-error:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _denial_for_intent(
    intent: MemoryMutationIntent,
) -> tuple[MemoryMutationStatus, str, str]:
    if intent.child_memory_isolated:
        return (
            "blocked",
            "memory_child_scope_isolated",
            "Child-agent memory is isolated and cannot write parent or shared memory.",
        )
    if intent.failure_kind == "redaction_failed":
        return (
            "blocked",
            "memory_redaction_failed",
            "Redaction was not completed; target presence remains unresolved.",
        )
    if intent.failure_kind == "provider_unavailable":
        return (
            "unsupported",
            "memory_provider_unavailable",
            "Memory provider is unavailable and no live provider call is allowed.",
        )
    if intent.failure_kind == "stale_conflict":
        return (
            "blocked",
            "memory_stale_conflict",
            "Memory target is stale or conflicting; mutation is blocked.",
        )
    if intent.operation in {"remember", "write", "redact", "delete"}:
        return (
            "approval_required",
            "memory_write_disabled",
            "Memory writes, redactions, and deletions are disabled by default.",
        )
    return (
        "unsupported",
        f"memory_{intent.operation}_unsupported",
        f"Memory {intent.operation} is unsupported while live providers are disabled.",
    )


def _receipt_matches_claim(
    receipt: MemoryMutationReceipt,
    claim: MemoryWriteClaim,
) -> bool:
    return (
        receipt.provider_id == claim.provider_id
        and receipt.turn_id == claim.turn_id
        and receipt.operation == claim.operation
        and receipt.target.target_sha256 == claim.target_sha256
    )


def _local_test_receipt_signature(
    *,
    receipt_id: str,
    provider_id: str,
    turn_id: str,
    operation: MemoryMutationOperation,
    target_sha256: str,
    matched_count: int,
    target_still_present: bool,
) -> str:
    payload = "\0".join(
        (
            _LOCAL_TEST_RECEIPT_SECRET,
            receipt_id,
            provider_id,
            turn_id,
            operation,
            target_sha256,
            str(matched_count),
            "1" if target_still_present else "0",
        )
    )
    return "local-test:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sanitize_path_ref(value: str) -> str:
    normalized = value.replace("\\", "/")
    if _is_sensitive_ref(normalized):
        return "[private-ref-redacted]"
    if normalized.endswith("/MEMORY.md") or normalized == "MEMORY.md":
        return "MEMORY.md"
    memory_match = re.search(r"(?:^|/)(memory/[^?#\s]+)", normalized)
    if memory_match:
        return _trim_secret_path_fragments(memory_match.group(1))
    if normalized.startswith("/") or normalized.startswith("~"):
        return "[private-path-redacted]"
    return _trim_secret_path_fragments(normalized)


def _sanitize_public_text(value: str) -> str:
    clean = value.strip()
    clean = _RAW_PRIVATE_BLOCK_RE.sub("[redacted-private-block]", clean)
    clean = _SENSITIVE_REF_RE.sub("[redacted-ref]", clean)
    clean = "\n".join(_redact_private_marker_lines(clean.splitlines()))
    clean = _COOKIE_HEADER_RE.sub("[redacted-cookie]", clean)
    clean = _PATH_LIKE_RE.sub(lambda match: _sanitize_path_ref(match.group(0)), clean)
    clean = _BEARER_SECRET_RE.sub("[redacted]", clean)
    clean = _SECRET_VALUE_RE.sub("[redacted]", clean)
    if len(clean) > _PUBLIC_TEXT_MAX_LENGTH:
        return clean[:_PUBLIC_TEXT_MAX_LENGTH].rstrip() + "...[truncated]"
    return clean


def _redact_private_marker_lines(lines: list[str]) -> list[str]:
    public_lines: list[str] = []
    for line in lines:
        match = _RAW_PRIVATE_IDENTIFIER_RE.search(line)
        redacted_ref_index = line.find("[redacted-ref]")
        if match is None and redacted_ref_index < 0:
            public_lines += [line]
            continue
        marker_start = (
            redacted_ref_index
            if match is None
            else min(
                match.start(),
                redacted_ref_index if redacted_ref_index >= 0 else match.start(),
            )
        )
        prefix = line[:marker_start].rstrip()
        public_lines += [
            f"{prefix} [redacted-private-segment]"
            if prefix
            else "[redacted-private-segment]"
        ]
        break
    return public_lines


def _contains_sensitive_public_identifier(value: str) -> bool:
    clean = value.strip()
    lowered = clean.casefold()
    return (
        _is_sensitive_ref(clean)
        or _RAW_PRIVATE_IDENTIFIER_RE.search(clean) is not None
        or _PATH_LIKE_RE.search(clean) is not None
        or _BEARER_SECRET_RE.search(clean) is not None
        or _SECRET_VALUE_RE.search(clean) is not None
        or "cookie" in lowered
        or "authorization" in lowered
    )


def _trim_secret_path_fragments(value: str) -> str:
    clean = value.replace("\\", "/").strip()
    if not clean:
        return "[path-redacted]"
    parts = tuple(
        "[redacted]" if _SECRET_PATH_PART_RE.search(part) else part
        for part in clean.split("/")
        if part and part not in {".", ".."}
    )
    return "/".join(parts) if parts else "[path-redacted]"


_SECRET_PATH_PART_RE = re.compile(r"(secret|token|private|credential|key|\.env)", re.IGNORECASE)
_PATH_LIKE_RE = re.compile(r"(?:~|/)[^\s,.;)]+")
_BEARER_SECRET_RE = re.compile(r"(?i)\b(?:authorization:\s*)?bearer\s+[A-Za-z0-9._~+/=-]+")
_COOKIE_HEADER_RE = re.compile(r"\b(?:Cookie|Set-Cookie)\s*:\s*[^\n\r]+", re.IGNORECASE)
_RAW_PRIVATE_IDENTIFIER_RE = re.compile(
    r"raw[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args)"
    r"[A-Za-z0-9_-]*|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"<(?:/?)(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)\b|"
    r"chain[ _-]?of[ _-]?thought|hidden[ _-]?reasoning|"
    r"private[ _-]?reasoning|private[ _-]?memory[A-Za-z0-9_-]*",
    re.IGNORECASE,
)
_RAW_PRIVATE_BLOCK_RE = re.compile(
    r"<(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)\b[^>]*>.*?"
    r"</(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)>",
    re.IGNORECASE | re.DOTALL,
)
_SENSITIVE_REF_RE = re.compile(
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|file|vault|"
    r"secret|secrets)://[^\s\"'<>]+|"
    r"https?://(?:"
    r"(?:storage\.googleapis\.com|storage\.cloud\.google\.com|[^/\s\"'<>]*\.storage\.googleapis\.com)|"
    r"(?:[^/\s\"'<>]*s3[^/\s\"'<>]*\.amazonaws\.com|s3[.-][^/\s\"'<>]*\.amazonaws\.com)|"
    r"(?:[^/\s\"'<>]*\.supabase\.co/storage/)|"
    r"(?:[^/\s\"'<>]*\.r2\.cloudflarestorage\.com)|"
    r"(?:[^/\s\"'<>]*blob\.core\.windows\.net)"
    r")[^\s\"'<>]*|"
    r"https?://api\.telegram\.org/bot[0-9]+:[^/\s\"'<>]+[^\s\"'<>]*|"
    r"https?://[^\s\"'<>]*[?&](?:X-Amz-Signature|access[_-]?token|api[_-]?key|auth|"
    r"authorization|cookie|credential|key|password|private[_-]?key|secret|session|"
    r"sig|signature|token)=[^\s\"'<>]+",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"\bAKIA[A-Z0-9]{8,}\b|"
    r"\b[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|ACCESS[_-]?KEY|"
    r"AWS[_-]?ACCESS[_-]?KEY[_-]?ID|AWS[_-]?SECRET[_-]?ACCESS[_-]?KEY)"
    r"[A-Z0-9_]*\s*[:=]\s*[^,\s}{\n]{4,}|"
    r"\b(?:sk|ghp|token|api[_-]?key|secret|credential)[A-Za-z0-9._~+/=-]*"
    r")"
)


def _is_sensitive_ref(value: str) -> bool:
    lowered = value.casefold()
    return (
        _SENSITIVE_REF_RE.search(value) is not None
        or "cookie" in lowered
        or "authorization" in lowered
        or "telegram" in lowered
        or "x-amz-signature" in lowered
        or ("://" in lowered and not lowered.startswith(("http://", "https://")))
    )
