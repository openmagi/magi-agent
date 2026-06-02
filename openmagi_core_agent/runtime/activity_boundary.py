from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


ActivityKind = Literal[
    "model_call",
    "web_fetch",
    "file_read",
    "file_write",
    "database_access",
    "memory_read",
    "memory_write",
    "browser_action",
    "process_execution",
    "external_api_call",
    "channel_delivery",
    "scheduler_mutation",
]
RetryPolicy = Literal["none", "safe_retry", "idempotent_retry"]
ActivityStatus = Literal["accepted", "deduped_existing_success", "blocked"]

_DIGEST_PREFIX = "sha256:"
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_PROTECTED_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "pro" + "mpt",
    "sess" + "ion",
    "priv" + "ate",
    "bearer",
    "credential",
)
_RAW_MARKERS = (
    "raw:",
    "rawref",
    "rawtoollog",
    "rawchildtranscript",
    "childrawtoollog",
    "rawoutput",
    "rawresult",
    "hiddenreasoning",
    "privatememory",
)
_PROTECTED_COMPACT_MARKERS = tuple(
    "".join(character for character in marker if character.isalnum())
    for marker in _PROTECTED_FRAGMENTS + _RAW_MARKERS
)
_PATHLIKE_COMPACT_MARKERS = (
    "users",
    "home",
    "ssh",
    "idrsa",
    "env",
    "kube",
    "kubeconfig",
    "varlib",
    "databots",
)
_REASON_CODES = (
    "idempotency_key_required",
    "non_reversible_action_requires_approval",
    "compensation_policy_required_for_reversible_action",
    "idempotency_key_conflict",
)


class _FrozenNoUpdateModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for activity boundary contracts")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class ActivityRequest(_FrozenNoUpdateModel):
    activity_id: str = Field(alias="activityId")
    kind: ActivityKind
    target_system_ref: str = Field(alias="targetSystemRef")
    action_digest: str = Field(alias="actionDigest")
    side_effecting: StrictBool = Field(alias="sideEffecting")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    approval_receipt_digest: str | None = Field(default=None, alias="approvalReceiptDigest")
    timeout_ms: int = Field(alias="timeoutMs", ge=1, le=300_000)
    retry_policy: RetryPolicy = Field(alias="retryPolicy")
    compensation_policy_ref: str | None = Field(default=None, alias="compensationPolicyRef")
    reversible: StrictBool

    @field_validator("activity_id")
    @classmethod
    def _validate_activity_id(cls, value: str) -> str:
        return _safe_ref(value, field_name="activityId")

    @field_validator("target_system_ref")
    @classmethod
    def _validate_target_ref(cls, value: str) -> str:
        return _safe_ref(value, field_name="targetSystemRef")

    @field_validator("idempotency_key", "compensation_policy_ref")
    @classmethod
    def _validate_optional_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value, field_name="activity ref")

    @field_validator("action_digest", "approval_receipt_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value)


class ActivityBoundaryResult(_FrozenNoUpdateModel):
    ok: StrictBool
    status: ActivityStatus
    receipt_digest: str | None = Field(default=None, alias="receiptDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("receipt_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for reason_code in value:
            if reason_code not in _REASON_CODES:
                raise ValueError("reasonCodes must be canonical activity boundary reason codes")
        return value


class _StoredActivityReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    request_fingerprint: str = Field(alias="requestFingerprint")
    receipt_digest: str = Field(alias="receiptDigest")

    @field_validator("request_fingerprint", "receipt_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value)


class ActivityStore:
    """In-memory contract store for local idempotency tests; not durable execution state."""

    def __init__(self) -> None:
        self._receipts_by_idempotency_key: dict[str, _StoredActivityReceipt] = {}

    def existing_receipt(self, key: str) -> _StoredActivityReceipt | None:
        return self._receipts_by_idempotency_key.get(_safe_ref(key, field_name="idempotencyKey"))

    def record_receipt(self, key: str, *, request_fingerprint: str, receipt_digest: str) -> None:
        self._receipts_by_idempotency_key[_safe_ref(key, field_name="idempotencyKey")] = _StoredActivityReceipt(
            requestFingerprint=request_fingerprint,
            receiptDigest=receipt_digest,
        )


def evaluate_activity_request(request: ActivityRequest, store: ActivityStore) -> ActivityBoundaryResult:
    reasons: list[str] = []
    if request.side_effecting and not request.idempotency_key:
        reasons.append("idempotency_key_required")
    if request.side_effecting and not request.reversible and request.approval_receipt_digest is None:
        reasons.append("non_reversible_action_requires_approval")
    if request.side_effecting and request.reversible and request.compensation_policy_ref is None:
        reasons.append("compensation_policy_required_for_reversible_action")
    if reasons:
        return ActivityBoundaryResult(ok=False, status="blocked", reasonCodes=tuple(reasons))

    request_fingerprint = _digest_request(request)
    if request.idempotency_key:
        existing = store.existing_receipt(request.idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                return ActivityBoundaryResult(
                    ok=False,
                    status="blocked",
                    reasonCodes=("idempotency_key_conflict",),
                )
            return ActivityBoundaryResult(
                ok=True,
                status="deduped_existing_success",
                receiptDigest=existing.receipt_digest,
            )

    receipt_digest = _digest_json(
        {
            "activityBoundaryVersion": 1,
            "requestFingerprint": request_fingerprint,
            "status": "accepted",
        }
    )
    if request.idempotency_key:
        store.record_receipt(
            request.idempotency_key,
            request_fingerprint=request_fingerprint,
            receipt_digest=receipt_digest,
        )
    return ActivityBoundaryResult(ok=True, status="accepted", receiptDigest=receipt_digest)


def _digest_request(request: ActivityRequest) -> str:
    return _digest_json(request.model_dump(by_alias=True, mode="json"))


def _require_digest(value: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError("activity digest fields must be sha256 digests")
    return value


def _safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not clean or not _SAFE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a safe public reference")
    lowered = clean.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    if (
        any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS)
        or any(marker in compact for marker in _PROTECTED_COMPACT_MARKERS)
        or any(marker in lowered for marker in _RAW_MARKERS)
        or any(marker in compact for marker in _RAW_MARKERS)
        or _looks_path_like(clean, compact)
        or "/" in clean
        or "\\" in clean
        or clean.startswith(("~", "."))
    ):
        raise ValueError(f"{field_name} contains protected runtime data marker")
    return clean


def _looks_path_like(value: str, compact: str) -> bool:
    if not any(sep in value for sep in (":", ".", "-")):
        return False
    if "users" in compact or "home" in compact:
        return True
    return any(marker in compact for marker in ("ssh", "idrsa", "kube", "kubeconfig", "varlib", "databots")) or (
        "env" in compact and any(marker in compact for marker in _PATHLIKE_COMPACT_MARKERS)
    )


def _digest_json(value: object) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
