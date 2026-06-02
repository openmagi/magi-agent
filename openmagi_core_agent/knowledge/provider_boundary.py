from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


KnowledgeOperation = Literal[
    "knowledge.search",
    "knowledge.write",
    "external_source.read",
    "external_source.cache",
]
KnowledgeStatus = Literal["disabled", "intent", "ok", "blocked", "error"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:source|transcript|tool|prompt|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_PRIVATE_SOURCE_RE = re.compile(
    r"^(?:file|s3|gs|gcs|vault|secret|secrets|postgres|postgresql|mysql|redis|mongodb)://|"
    r"https?://[^\\s\"'<>]*[?&](?:token|secret|api[_-]?key|signature|credential|password)=",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "raw",
    "token",
    "secret",
    "credential",
    "auth",
    "authoritative",
    "trust",
    "trusted",
    "verified",
    "valid",
    "password",
    "cookie",
    "path",
    "transcript",
    "hidden",
    "sourcepayload",
    "production",
    "attached",
    "enabled",
    "allowed",
    "performed",
    "authority",
    "route",
    "called",
    "fetched",
    "executed",
    "injected",
    "network",
)


class KnowledgeProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def execute(self, request: KnowledgeBoundaryRequest) -> Mapping[str, object]: ...


class KnowledgeBoundaryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    provider_id: str = Field(default="openmagi.knowledge.local-fake", alias="providerId")
    max_records: int = Field(default=5, alias="maxRecords", ge=1, le=20)
    max_preview_chars: int = Field(default=1000, alias="maxPreviewChars", ge=0, le=8000)
    production_provider_calls_enabled: Literal[False] = Field(
        default=False,
        alias="productionProviderCallsEnabled",
    )
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class KnowledgeAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    external_source_fetched: Literal[False] = Field(default=False, alias="externalSourceFetched")
    knowledge_write_performed: Literal[False] = Field(default=False, alias="knowledgeWritePerformed")
    raw_private_source_projected: Literal[False] = Field(
        default=False,
        alias="rawPrivateSourceProjected",
    )
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "provider_called",
        "external_source_fetched",
        "knowledge_write_performed",
        "raw_private_source_projected",
        "production_writes_enabled",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class KnowledgeBoundaryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: KnowledgeOperation
    query: str | None = None
    source_ref: str | None = Field(default=None, alias="sourceRef")
    content: str | None = None
    collection: str | None = None
    write_scope_approved: bool = Field(default=False, alias="writeScopeApproved")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("query", "source_ref", "collection")
    @classmethod
    def _sanitize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        return clean or None


class KnowledgeSourceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    operation: KnowledgeOperation
    provider: str
    content_digest: str = Field(alias="contentDigest")
    title: str | None = None
    preview: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("source_ref")
    @classmethod
    def _validate_source_ref(cls, value: str) -> str:
        return _public_ref(value, "source")

    @field_validator("evidence_ref")
    @classmethod
    def _validate_evidence_ref(cls, value: str) -> str:
        return _public_ref(value, "evidence")

    def public_projection(self) -> dict[str, object]:
        public_safe = _is_public_safe_source_metadata(self.metadata)
        preview = self.preview if public_safe else None
        return {
            "sourceRef": _projection_ref(self.source_ref, "source", public_safe),
            "evidenceRef": _projection_ref(self.evidence_ref, "evidence", public_safe),
            "operation": self.operation,
            "provider": _safe_text(self.provider)[:120],
            "contentDigest": self.content_digest,
            "title": None if not public_safe or self.title is None else _safe_text(self.title)[:160],
            "preview": None if preview is None else _safe_text(preview)[:500],
            "metadata": _safe_metadata(self.metadata) if public_safe else _private_record_metadata(self.metadata),
        }


class KnowledgeBoundaryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: KnowledgeStatus
    operation: KnowledgeOperation
    records: tuple[KnowledgeSourceRecord, ...] = ()
    receipt_ref: str | None = Field(default=None, alias="receiptRef")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: KnowledgeAuthorityFlags = Field(
        default_factory=KnowledgeAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = KnowledgeAuthorityFlags()
        return cls.model_validate(values)

    def public_projection(self) -> dict[str, object]:
        projected_records = [record.public_projection() for record in self.records]
        return {
            "status": self.status,
            "operation": self.operation,
            "sourceRecords": projected_records,
            "parentOutputRefs": [
                ref
                for record in projected_records
                for ref in (record.get("sourceRef"), record.get("evidenceRef"))
            ],
            "receiptRef": None if self.receipt_ref is None else _public_ref(self.receipt_ref, "receipt"),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class KnowledgeBoundary:
    """Default-off KB/external-source provider boundary with fake providers only."""

    def __init__(self, config: KnowledgeBoundaryConfig) -> None:
        self.config = config

    async def execute(
        self,
        request: KnowledgeBoundaryRequest,
        *,
        provider: KnowledgeProviderPort | None = None,
    ) -> KnowledgeBoundaryDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeProviderEnabled": self.config.local_fake_provider_enabled,
            "productionProviderCallsEnabled": False,
            "productionWritesEnabled": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        validation_error = _validate_request(request)
        if not self.config.enabled:
            return _decision(request, "disabled", ("knowledge_boundary_disabled",), diagnostics)
        if validation_error is not None:
            return _decision(request, "blocked", (validation_error,), diagnostics)
        if request.operation == "knowledge.write" and not request.write_scope_approved:
            return _decision(request, "blocked", ("knowledge_write_scope_required",), diagnostics)
        if not self.config.local_fake_provider_enabled or provider is None:
            return _decision(request, "intent", ("local_knowledge_provider_disabled",), diagnostics)
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return _decision(request, "blocked", ("local_fake_knowledge_provider_untrusted",), diagnostics)
        try:
            output = provider.execute(request)
            if inspect.isawaitable(output):
                output = await output
        except Exception as exc:
            return _decision(
                request,
                "error",
                ("local_fake_knowledge_provider_error",),
                {**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        records = tuple(
            _records_from_output(
                request,
                output,
                provider_id=self.config.provider_id,
                max_records=self.config.max_records,
                max_preview_chars=self.config.max_preview_chars,
            )
        )
        return _decision(
            request,
            "ok",
            (f"{request.operation.replace('.', '_')}_local_fake_receipt_only",),
            diagnostics,
            records=records,
            receipt_ref=_receipt_ref(request, records),
        )


def _decision(
    request: KnowledgeBoundaryRequest,
    status: KnowledgeStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    records: tuple[KnowledgeSourceRecord, ...] = (),
    receipt_ref: str | None = None,
) -> KnowledgeBoundaryDecision:
    return KnowledgeBoundaryDecision(
        status=status,
        operation=request.operation,
        records=records,
        receiptRef=receipt_ref,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=KnowledgeAuthorityFlags(),
    )


def _validate_request(request: KnowledgeBoundaryRequest) -> str | None:
    if request.operation == "knowledge.search" and not (request.query and request.query.strip()):
        return "knowledge_query_required"
    if request.operation in {"external_source.read", "external_source.cache"}:
        if not request.source_ref:
            return "external_source_ref_required"
        if _is_private_source(request.source_ref):
            return "private_external_source_blocked"
    if request.operation in {"knowledge.write", "external_source.cache"} and _contains_private_payload(
        request.content or ""
    ):
        return "private_source_payload_blocked"
    return None


def _records_from_output(
    request: KnowledgeBoundaryRequest,
    output: object,
    *,
    provider_id: str,
    max_records: int,
    max_preview_chars: int,
) -> list[KnowledgeSourceRecord]:
    data = output if isinstance(output, Mapping) else {}
    raw_items = data.get("records") or data.get("results") or (data,)
    if isinstance(raw_items, Mapping):
        raw_items = (raw_items,)
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, str | bytes | bytearray):
        raw_items = ()
    records: list[KnowledgeSourceRecord] = []
    for index, item in enumerate(raw_items[:max_records], start=1):
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("sourceRef") or item.get("source_ref") or request.source_ref or f"knowledge:{index}")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        public_safe = _is_public_safe_source_metadata(metadata)
        preview = _public_preview_from_item(item)
        title = _safe_text(str(item.get("title") or "")) if public_safe else ""
        records.append(
            KnowledgeSourceRecord(
                sourceRef=_public_source_ref(source, index, public_safe=public_safe),
                evidenceRef=f"evidence:knowledge:{index}",
                operation=request.operation,
                provider=provider_id,
                contentDigest=_digest_text(str(item.get("content") or item.get("snippet") or request.content or "")),
                title=title or None,
                preview=preview[:max_preview_chars] if preview else None,
                metadata=_safe_metadata(metadata),
            )
        )
    if not records and request.operation in {"knowledge.write", "external_source.cache"}:
        content = _safe_text(request.content or "")
        records.append(
            KnowledgeSourceRecord(
                sourceRef=_public_source_ref(request.source_ref or "knowledge:write", 1),
                evidenceRef="evidence:knowledge:1",
                operation=request.operation,
                provider=provider_id,
                contentDigest=_digest_text(content),
                preview=content[:max_preview_chars] if content else None,
            )
        )
    return records


def _public_source_ref(value: str, index: int, *, public_safe: bool = True) -> str:
    if not public_safe or _is_private_source(value):
        return f"source:knowledge:{_digest(value)}"
    return _public_ref(value if value != "knowledge:write" else f"source:knowledge:{index}", "source")


def _projection_ref(value: str, prefix: str, public_safe: bool) -> str:
    if public_safe:
        return _public_ref(value, prefix)
    return f"{prefix}:knowledge:{_digest(value)}"


def _public_preview_from_item(item: Mapping[str, object]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    if not _is_public_safe_source_metadata(metadata):
        return ""
    for key in ("publicPreview", "public_preview"):
        value = item.get(key)
        if isinstance(value, str):
            return _safe_text(value)
    return ""


def _is_public_safe_source_metadata(metadata: Mapping[str, object]) -> bool:
    visibility = metadata.get("visibility")
    if isinstance(visibility, str):
        normalized_visibility = visibility.casefold().replace("_", "-").strip()
        if normalized_visibility in {"public-safe", "public"}:
            return True
    return metadata.get("publicSafe") is True


def _private_record_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {"visibility": "private"}
    public_safe = metadata.get("publicSafe")
    if isinstance(public_safe, bool):
        safe["publicSafe"] = False
    return safe


def _public_ref(value: str, prefix: str) -> str:
    clean = _safe_text(str(value))
    if _REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{_digest(str(value))}"


def _receipt_ref(
    request: KnowledgeBoundaryRequest,
    records: tuple[KnowledgeSourceRecord, ...],
) -> str:
    seed = "|".join((request.operation, request.query or "", request.source_ref or "", ",".join(record.source_ref for record in records)))
    return f"knowledge-receipt:{_digest(seed)}"


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[str(key)] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _safe_text(value: str) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None and not _PRIVATE_PATH_RE.search(line)
    ]
    clean = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    clean = _PRIVATE_SOURCE_RE.sub("[redacted-source]", clean)
    return clean.strip()


def _contains_private_payload(value: str) -> bool:
    return bool(_RAW_PRIVATE_LINE_RE.search(value) or _PRIVATE_PATH_RE.search(value) or _SECRET_TEXT_RE.search(value))


def _is_private_source(value: str) -> bool:
    return bool(
        _PRIVATE_PATH_RE.search(value)
        or value.startswith(("/", "~"))
        or ".." in value.split("/")
        or _PRIVATE_SOURCE_RE.search(value)
    )


def _safe_provider_error(exc: BaseException) -> str:
    return _safe_text(str(exc))[:240] or "[redacted-provider-error]"


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(_safe_text(value).encode('utf-8')).hexdigest()}"


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "KnowledgeBoundary",
    "KnowledgeBoundaryConfig",
    "KnowledgeBoundaryDecision",
    "KnowledgeBoundaryRequest",
    "KnowledgeSourceRecord",
]
