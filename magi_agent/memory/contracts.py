from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


MemoryScope = Literal["user", "bot", "org", "project", "session", "task"]
MemoryKind = Literal[
    "event",
    "note",
    "fact",
    "decision",
    "preference",
    "reasoning",
    "artifact",
    "relation",
]
MemoryConfidence = Literal["observed", "inferred", "user_asserted", "system_asserted", "verified"]
MemoryVisibility = Literal["private", "shared", "public-safe"]
MemoryPurpose = Literal["answer_user", "plan_task", "debug", "summarize", "audit", "maintenance"]

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")
_PRIVATE_VISIBILITIES = {"private", "shared"}
_SENSITIVE_BODY_RE = re.compile(
    r"(?:Authorization\s*:|Cookie\s*:|Set-Cookie\s*:|Bearer\s+|"
    r"raw[ _-]?(?:child|subagent|tool|prompt|transcript|output|result|log|args)|"
    r"(?:child|subagent)[ _-]?(?:prompt|output|transcript)|"
    r"tool[ _-]?(?:log|args|result)|"
    r"chain[ _-]?of[ _-]?thought|hidden[ _-]?reasoning|private[ _-]?reasoning|"
    r"private[ _-]?memory[A-Za-z0-9_-]*|"
    r"<(?:/?)(?:tool[_-]?log|child[_-]?prompt|hidden[_-]?reasoning)\b|"
    r"/Users(?:/|\b)|/home(?:/|\b)|/workspace(?:/|\b)|/data/bots(?:/|\b)|"
    r"/var/lib/kubelet(?:/|\b)|"
    r"s3://|gs://|supabase://|X-Amz-Signature|api\.telegram\.org/bot)",
    re.IGNORECASE,
)
_SENSITIVE_URL_RE = re.compile(
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
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|AKIA[A-Z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|ACCESS_KEY)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_AUTHORIZATION_HEADER_RE = re.compile(
    r"\b((?:Proxy-)?Authorization\s*:\s*[A-Za-z][A-Za-z0-9+.-]*\s+)([^\s,;]+)",
    re.IGNORECASE,
)
_COOKIE_HEADER_RE = re.compile(r"\b((?:Set-)?Cookie\s*:\s*)[^\n\r]+", re.IGNORECASE)
_SAFE_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_MAX_MEMORY_PREVIEW = 400


class UnsupportedMemoryOperationError(RuntimeError):
    def __init__(self, operation: str, *, provider_id: str) -> None:
        self.operation = operation
        self.provider_id = provider_id
        super().__init__(
            f"{_safe_provider_id(provider_id)} is read-only in the Python ADK scaffold; "
            f"{operation} is disabled"
        )


class MemoryProviderCapabilities(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    storage_model: Literal["file", "sql", "object", "vector", "graph", "hybrid", "external"] = (
        Field(alias="storageModel")
    )
    supports_write: bool = Field(default=False, alias="supportsWrite")
    supports_search: bool = Field(default=False, alias="supportsSearch")
    supports_bitemporal: bool = Field(default=False, alias="supportsBitemporal")
    supports_graph: bool = Field(default=False, alias="supportsGraph")
    supports_reasoning: bool = Field(default=False, alias="supportsReasoning")
    supports_decay: bool = Field(default=False, alias="supportsDecay")
    supports_delete: Literal["none", "soft", "hard", "tombstone"] = Field(
        default="none",
        alias="supportsDelete",
    )
    supports_export: bool = Field(default=False, alias="supportsExport")
    consistency: Literal["strong", "eventual", "snapshot"] = "snapshot"
    max_result_bytes: int = Field(default=32_768, alias="maxResultBytes", ge=1)
    max_write_bytes: int = Field(default=0, alias="maxWriteBytes", ge=0)
    policy_required: tuple[str, ...] = Field(default=(), alias="policyRequired")
    # Gated write tier — must be explicitly set to "gated_write" to unlock
    # supports_write=True + max_write_bytes>0.  The default "read_only" preserves
    # the original invariant byte-for-byte.
    write_tier: Literal["read_only", "gated_write"] = Field(
        default="read_only",
        alias="writeTier",
    )

    @model_validator(mode="after")
    def _validate_readonly_scaffold(self) -> "MemoryProviderCapabilities":
        if self.supports_delete != "none":
            raise ValueError("Python memory scaffold does not expose delete operations")
        if self.write_tier == "read_only":
            # Original invariant: unchanged for all providers that do not opt in.
            if self.supports_write:
                raise ValueError("Python memory scaffold supports read-only providers only")
            if self.max_write_bytes != 0:
                raise ValueError("read-only memory providers must set maxWriteBytes=0")
        else:
            # Gated write tier: supports_write may be True but max_write_bytes must be >0.
            if self.supports_write and self.max_write_bytes == 0:
                raise ValueError(
                    "gated_write providers must set max_write_bytes > 0 "
                    "to bound the write surface"
                )
        return self


class MemoryRecord(BaseModel):
    model_config = _MODEL_CONFIG

    id: str
    scope: MemoryScope
    kind: MemoryKind
    body: str
    source_ref: str = Field(alias="sourceRef")
    provider_id: str = Field(alias="providerId")
    subject: str | None = None
    confidence: MemoryConfidence
    visibility: MemoryVisibility = "private"
    score: float | None = None
    time_bounds: Mapping[str, object] | None = Field(default=None, alias="timeBounds")
    custom_metadata: Mapping[str, object] = Field(default_factory=dict, alias="customMetadata")

    def public_projection(self) -> dict[str, object]:
        projection: dict[str, object] = {
            "id": _safe_source_ref(self.id),
            "scope": self.scope,
            "kind": self.kind,
            "providerId": _safe_provider_id(self.provider_id),
            "sourceRef": _safe_source_ref(self.source_ref),
            "confidence": self.confidence,
            "visibility": self.visibility,
            "score": self.score,
        }
        if self.visibility not in _PRIVATE_VISIBILITIES:
            projection["snippet"] = _safe_memory_snippet(self.body)
        return projection


class RecallRequest(BaseModel):
    model_config = _MODEL_CONFIG

    scope: Mapping[str, object]
    query: str
    purpose: MemoryPurpose
    subject: str | None = None
    limit: int = Field(default=5, ge=1, le=20)
    max_bytes: int = Field(default=16_384, alias="maxBytes", ge=1)
    min_score: float = Field(default=0.3, alias="minScore", ge=0)


class RecallResult(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: str = Field(alias="providerId")
    records: tuple[MemoryRecord, ...] = ()
    recall_allowed: bool = Field(alias="recallAllowed")
    write_allowed: Literal[False] = Field(alias="writeAllowed")
    prompt_projection_allowed: Literal[False] = Field(alias="promptProjectionAllowed")
    public_projection_allowed: bool = Field(alias="publicProjectionAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["writeAllowed"] = False
        values["promptProjectionAllowed"] = False
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
        data["writeAllowed"] = False
        data["promptProjectionAllowed"] = False
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "providerId": _safe_provider_id(self.provider_id),
            "recallAllowed": self.recall_allowed,
            "writeAllowed": False,
            "promptProjectionAllowed": False,
            "publicProjectionAllowed": self.public_projection_allowed,
            "reasonCodes": [_safe_reason_code(reason_code) for reason_code in self.reason_codes],
            "records": [
                record.public_projection()
                for record in self.records
                if self.public_projection_allowed
            ],
        }


def _safe_memory_snippet(body: str) -> str:
    lines = _drop_private_marker_lines(body.splitlines())
    scrubbed = _SENSITIVE_URL_RE.sub("[redacted-url]", "\n".join(lines))
    scrubbed = _SECRET_VALUE_RE.sub("[redacted]", scrubbed)
    return _sanitize_memory_preview(scrubbed)


def _sanitize_memory_preview(preview: str) -> str:
    redacted = _AUTHORIZATION_HEADER_RE.sub(r"\1[redacted]", preview)
    redacted = _COOKIE_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _SECRET_VALUE_RE.sub("[redacted]", redacted)
    if len(redacted) > _MAX_MEMORY_PREVIEW:
        return f"{redacted[:_MAX_MEMORY_PREVIEW - 3]}..."
    return redacted


def _safe_source_ref(source_ref: str) -> str:
    if (
        _SENSITIVE_BODY_RE.search(source_ref)
        or _SENSITIVE_URL_RE.search(source_ref)
        or _SECRET_VALUE_RE.search(source_ref)
    ):
        digest = hashlib.sha1(source_ref.encode("utf-8")).hexdigest()[:16]
        return f"memory:{digest}"
    return source_ref


def _safe_provider_id(provider_id: str) -> str:
    safe = _safe_source_ref(provider_id)
    if safe != provider_id:
        return f"provider:{safe.removeprefix('memory:')}"
    return provider_id


def _drop_private_marker_lines(lines: list[str]) -> list[str]:
    public_lines: list[str] = []
    for line in lines:
        line_has_marker = bool(
            _SENSITIVE_BODY_RE.search(line)
            or _SENSITIVE_URL_RE.search(line)
            or _SECRET_VALUE_RE.search(line)
        )
        if line_has_marker:
            break
        public_lines.append(line)
    return public_lines


def _safe_reason_code(reason_code: str) -> str:
    if (
        _SAFE_REASON_CODE_RE.fullmatch(reason_code)
        and _SENSITIVE_BODY_RE.search(reason_code) is None
        and _SENSITIVE_URL_RE.search(reason_code) is None
        and _SECRET_VALUE_RE.search(reason_code) is None
    ):
        return reason_code
    digest = hashlib.sha1(reason_code.encode("utf-8")).hexdigest()[:16]
    return f"reason:{digest}"
