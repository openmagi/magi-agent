from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


ProviderReceiptStatus = Literal["ok", "error", "blocked", "disabled", "timeout"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SAFE_EVIDENCE_DIGEST_RE = re.compile(r"^evidence:[a-f0-9]{16}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"\bsk-(?:live|test)?[-_A-Za-z0-9]{6,}|"
    r"gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]{6,}|"
    r"xox[a-z]-[A-Za-z0-9._-]{6,}|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"(?:authorization|cookie|set-cookie|password|token|secret|credential|api[_-]?key)"
    r"\s*[:=]\s*[^,\s}{\n]{3,}"
    r")",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/private/var(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"/tmp/(?:opencode-inspect|openmagi-inspect|openmagi-workspace-[^/\s,;}\"']+|"
    r"[^/\s,;}\"']*(?:workspace|inspect)[^/\s,;}\"']*)(?:/[^\s,;}\"']*)?"
    r")",
    re.IGNORECASE,
)
_RAW_PRIVATE_RE = re.compile(
    r"raw[_ -]?(?:user|tool|child|prompt|transcript|output|result|log|args|body|text)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"approval[_ -]?(?:payload|body)|private[_ -]?memory",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "auth",
    "bearer",
    "cookie",
    "credential",
    "downloadpath",
    "filepath",
    "header",
    "hidden",
    "key",
    "password",
    "path",
    "private",
    "raw",
    "secret",
    "session",
    "token",
)
_MAX_SAFE_TEXT = 512


class ProviderReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    receipt_id: str = Field(alias="receiptId")
    provider_name: str = Field(alias="providerName")
    operation: str
    status: ProviderReceiptStatus
    request_digest: str = Field(alias="requestDigest")
    response_digest: str = Field(alias="responseDigest")
    duration_ms: int = Field(default=0, alias="durationMs", ge=0)
    retry_count: int = Field(default=0, alias="retryCount", ge=0)
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(str(key), str(key)): value for key, value in update.items()})
        _ = deep
        return type(self).model_validate(data)

    @field_validator("receipt_id")
    @classmethod
    def _sanitize_receipt_id(cls, value: str) -> str:
        return _safe_public_ref(value, prefix="provider-receipt")

    @field_validator("provider_name")
    @classmethod
    def _sanitize_provider_name(cls, value: str) -> str:
        return _safe_public_ref(value, prefix="provider")

    @field_validator("operation")
    @classmethod
    def _sanitize_operation(cls, value: str) -> str:
        return _safe_public_ref(value, prefix="operation")

    @field_validator("request_digest", "response_digest")
    @classmethod
    def _sanitize_digest(cls, value: str) -> str:
        if re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            return value
        return provider_digest(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_evidence_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (_safe_evidence_ref(value),)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(_safe_evidence_ref(str(item)) for item in value)
        return ()

    @field_serializer("evidence_refs")
    def _serialize_evidence_refs(self, value: tuple[str, ...]) -> list[str]:
        return list(value)


def build_provider_receipt(
    *,
    provider_name: str,
    operation: str,
    status: ProviderReceiptStatus,
    request_payload: object,
    response_payload: object,
    duration_ms: int,
    retry_count: int = 0,
    evidence_refs: Sequence[str] = (),
) -> ProviderReceipt:
    request_digest = provider_digest(request_payload)
    response_digest = provider_digest(response_payload)
    receipt_seed = {
        "provider": _safe_public_ref(provider_name, prefix="provider"),
        "operation": _safe_public_ref(operation, prefix="operation"),
        "status": status,
        "request": request_digest,
        "response": response_digest,
        "retry": retry_count,
        "evidence": [_safe_evidence_ref(ref) for ref in evidence_refs],
    }
    receipt_id = f"provider-receipt:{_digest(receipt_seed)[:16]}"
    return ProviderReceipt(
        receiptId=receipt_id,
        providerName=provider_name,
        operation=operation,
        status=status,
        requestDigest=request_digest,
        responseDigest=response_digest,
        durationMs=max(0, int(duration_ms)),
        retryCount=max(0, int(retry_count)),
        evidenceRefs=tuple(evidence_refs),
    )


def provider_digest(value: object) -> str:
    return f"sha256:{_digest(sanitize_provider_payload(value))}"


def sanitize_provider_payload(value: object) -> object:
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                continue
            safe_key = _safe_payload_key(key)
            sanitized[safe_key] = sanitize_provider_payload(raw_value)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [sanitize_provider_payload(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _safe_text(repr(value))


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_payload_key(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]", "_", value).strip("._:-")
    if not normalized or _contains_private_text(normalized):
        return f"key:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return normalized[:80]


def _safe_public_ref(value: str, *, prefix: str) -> str:
    text = str(value).strip()
    if text and _SAFE_REF_RE.fullmatch(text) and not _contains_private_text(text):
        return text
    return f"{prefix}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _safe_evidence_ref(value: str) -> str:
    text = str(value).strip()
    if _SAFE_EVIDENCE_DIGEST_RE.fullmatch(text):
        return text
    return f"evidence:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _safe_text(value: str) -> str:
    if _contains_private_text(value):
        return "[redacted]"
    return value[:_MAX_SAFE_TEXT]


def _contains_private_text(value: str) -> bool:
    return bool(
        _SECRET_TEXT_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
        or _RAW_PRIVATE_RE.search(value)
    )


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = [
    "ProviderReceipt",
    "ProviderReceiptStatus",
    "build_provider_receipt",
    "provider_digest",
    "sanitize_provider_payload",
]
