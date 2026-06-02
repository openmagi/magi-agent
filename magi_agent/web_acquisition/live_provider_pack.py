from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.runtime.provider_execution import (
    ProviderExecutionBoundary,
    ProviderExecutionConfig,
    ProviderExecutionRequest,
    ProviderExecutionScope,
)
from magi_agent.runtime.provider_receipts import ProviderReceipt, provider_digest
from magi_agent.web_acquisition.policy import (
    content_digest,
    normalize_public_url,
    normalize_query,
    redact_public_text,
    safe_metadata,
    url_policy_error,
)


WebAcquisitionProviderOperation = Literal["search", "fetch", "reader", "browser_fallback"]
WebAcquisitionProviderStatus = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "ok",
    "no_answer",
    "repair_required",
]
WebAcquisitionProofType = Literal["opened", "observed"]
CitationProofStatus = Literal["ok", "repair_required"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_OPERATION_TO_PROVIDER_METHOD: Mapping[WebAcquisitionProviderOperation, str] = {
    "search": "search",
    "fetch": "fetch",
    "reader": "reader",
    "browser_fallback": "browser_fallback",
}
_OPERATION_TO_PROVIDER_NAME: Mapping[WebAcquisitionProviderOperation, str] = {
    "search": "web.search",
    "fetch": "web.fetch",
    "reader": "reader.extract",
    "browser_fallback": "browser.snapshot_fallback",
}


class SearchProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def search(self, request: WebAcquisitionProviderRequest) -> Mapping[str, object]: ...


class FetchProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def fetch(self, request: WebAcquisitionProviderRequest) -> Mapping[str, object]: ...


class ReaderProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def reader(self, request: WebAcquisitionProviderRequest) -> Mapping[str, object]: ...


class BrowserFallbackProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def browser_fallback(self, request: WebAcquisitionProviderRequest) -> Mapping[str, object]: ...


class WebAcquisitionProviderPackConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    provider_allowlist: tuple[str, ...] = Field(default=(), alias="providerAllowlist")
    max_results: int = Field(default=5, alias="maxResults", ge=1, le=20)
    max_content_bytes: int = Field(default=32_768, alias="maxContentBytes", ge=1)
    timeout_ms: int = Field(default=30_000, alias="timeoutMs", ge=1)
    browser_fallback_enabled: bool = Field(default=False, alias="browserFallbackEnabled")
    production_network_enabled: Literal[False] = Field(default=False, alias="productionNetworkEnabled")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(mode="python", by_alias=False, warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(str(key), str(key)): value for key, value in update.items()})
        data["production_network_enabled"] = False
        data["production_writes_enabled"] = False
        data["route_attached"] = False
        _ = deep
        return type(self).model_validate(data)

    @field_validator("provider_allowlist", mode="before")
    @classmethod
    def _coerce_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(str(item) for item in value)
        return ()


class WebAcquisitionProviderAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    network_fetched: Literal[False] = Field(default=False, alias="networkFetched")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    raw_content_injected: Literal[False] = Field(default=False, alias="rawContentInjected")
    parent_context_injected: Literal[False] = Field(default=False, alias="parentContextInjected")
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
        "network_fetched",
        "browser_executed",
        "production_writes_enabled",
        "raw_content_injected",
        "parent_context_injected",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class WebAcquisitionProviderRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: WebAcquisitionProviderOperation
    request_id: str = Field(alias="requestId")
    provider_name: str = Field(alias="providerName")
    bot_id_digest: str = Field(alias="botIdDigest")
    owner_id_digest: str = Field(alias="ownerIdDigest")
    session_key_digest: str = Field(alias="sessionKeyDigest")
    query: str | None = None
    url: str | None = None
    approval_granted: bool = Field(default=False, alias="approvalGranted")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "provider_name", "bot_id_digest", "owner_id_digest", "session_key_digest")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)


class WebAcquisitionLiveSourceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    provider_fetched_ref: str = Field(alias="providerFetchedRef")
    model_seen_ref: str = Field(alias="modelSeenRef")
    method: WebAcquisitionProviderOperation
    provider: str
    url_ref: str = Field(alias="urlRef")
    content_digest: str = Field(alias="contentDigest")
    proof_type: WebAcquisitionProofType = Field(alias="proofType")
    title: str | None = None
    public_preview: str | None = Field(default=None, alias="publicPreview")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("source_ref", "evidence_ref", "provider_fetched_ref", "model_seen_ref", "url_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": self.source_ref,
            "evidenceRef": self.evidence_ref,
            "providerFetchedRef": self.provider_fetched_ref,
            "modelSeenRef": self.model_seen_ref,
            "method": self.method,
            "provider": redact_public_text(self.provider, max_chars=120),
            "url": "[redacted]",
            "urlRef": self.url_ref,
            "contentDigest": self.content_digest,
            "proofType": self.proof_type,
            "title": None if self.title is None else redact_public_text(self.title, max_chars=160),
            "publicPreview": None if self.public_preview is None else redact_public_text(self.public_preview, max_chars=512),
            "metadata": safe_metadata(dict(self.metadata)),
        }


class WebAcquisitionProviderResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: WebAcquisitionProviderStatus
    operation: WebAcquisitionProviderOperation
    request_digest: str = Field(alias="requestDigest")
    source_records: tuple[WebAcquisitionLiveSourceRecord, ...] = Field(default=(), alias="sourceRecords")
    provider_receipt: ProviderReceipt | None = Field(default=None, alias="providerReceipt")
    public_preview: str | None = Field(default=None, alias="publicPreview")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: WebAcquisitionProviderAuthorityFlags = Field(
        default_factory=WebAcquisitionProviderAuthorityFlags,
        alias="authorityFlags",
    )

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "requestDigest": self.request_digest,
            "sourceRecords": [record.public_projection() for record in self.source_records],
            "parentOutputRefs": [
                ref
                for record in self.source_records
                for ref in (record.source_ref, record.evidence_ref, record.model_seen_ref)
            ],
            "providerReceipt": None
            if self.provider_receipt is None
            else self.provider_receipt.model_dump(by_alias=True),
            "publicPreview": None if self.public_preview is None else redact_public_text(self.public_preview, max_chars=1_024),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": safe_metadata(dict(self.diagnostic_metadata)),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class CitationProofDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: CitationProofStatus
    missing_source_refs: tuple[str, ...] = Field(default=(), alias="missingSourceRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class WebAcquisitionProviderPack:
    """Default-off provider-neutral web acquisition live boundary."""

    def __init__(self, config: WebAcquisitionProviderPackConfig) -> None:
        self.config = config

    def run(
        self,
        request: WebAcquisitionProviderRequest,
        *,
        provider: object | None = None,
    ) -> WebAcquisitionProviderResult:
        diagnostics = _diagnostics(self.config, request)
        digest = provider_digest(_request_payload(request))
        if not self.config.enabled:
            return _result(request, "disabled", digest, ("web_acquisition_provider_pack_disabled",), diagnostics)
        gate_error = self._gate_error(request, provider)
        if gate_error is not None:
            return _result(request, "blocked", digest, (gate_error,), diagnostics)
        validation_error = _validate_request(self.config, request)
        if validation_error is not None:
            status: WebAcquisitionProviderStatus = "approval_required" if validation_error == "browser_fallback_requires_approval" else "blocked"
            return _result(request, status, digest, (validation_error,), diagnostics)

        execution = _run_provider_execution(
            ProviderExecutionBoundary(
                ProviderExecutionConfig(
                    enabled=True,
                    localFakeProviderEnabled=self.config.local_fake_provider_enabled,
                    providerAllowlist=self.config.provider_allowlist,
                )
            ).execute(
                ProviderExecutionRequest(
                    providerName=request.provider_name,
                    operation=_OPERATION_TO_PROVIDER_NAME[request.operation],
                    payload=_request_payload(request),
                    scope=ProviderExecutionScope(
                        botIdDigest=request.bot_id_digest,
                        ownerIdDigest=request.owner_id_digest,
                        sessionIdDigest=request.session_key_digest,
                        selectedScope=True,
                    ),
                    requestId=request.request_id,
                ),
                provider=_OperationProvider(provider, request),
            )
        )
        if execution.status != "ok":
            return _result(
                request,
                "repair_required",
                digest,
                tuple(execution.reason_codes or ("provider_execution_failed",)),
                diagnostics,
                receipt=execution.receipt,
            )
        raw_output = _OperationProvider.last_output(execution.receipt.response_digest, provider, request)
        provider_status = _provider_status(raw_output)
        if provider_status == "denied":
            return _result(request, "no_answer", digest, ("provider_denied",), diagnostics, receipt=execution.receipt)
        if provider_status == "timeout":
            return _result(request, "repair_required", digest, ("provider_timeout",), diagnostics, receipt=execution.receipt)

        records = _records_from_output(
            request,
            raw_output,
            provider_name=request.provider_name,
            max_results=self.config.max_results,
            max_content_bytes=self.config.max_content_bytes,
        )
        return _result(
            request,
            "ok",
            digest,
            ("local_fake_web_acquisition_receipt_only",),
            diagnostics,
            records=records,
            receipt=execution.receipt,
            public_preview=_public_preview(raw_output),
        )

    def _gate_error(self, request: WebAcquisitionProviderRequest, provider: object | None) -> str | None:
        if not self.config.local_fake_provider_enabled:
            return "local_fake_provider_disabled"
        if provider is None:
            return "provider_missing"
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return "local_fake_provider_untrusted"
        if self.config.provider_allowlist and request.provider_name not in self.config.provider_allowlist:
            return "provider_not_allowlisted"
        return None


class _OperationProvider:
    openmagi_local_fake_provider = True
    _outputs: dict[str, object] = {}

    def __init__(self, provider: object | None, request: WebAcquisitionProviderRequest) -> None:
        self.provider = provider
        self.request = request

    def execute(self, _execution_request: ProviderExecutionRequest) -> Mapping[str, object]:
        assert self.provider is not None
        method = getattr(self.provider, _OPERATION_TO_PROVIDER_METHOD[self.request.operation], None)
        if method is None:
            raise ValueError("provider operation missing")
        output = method(self.request)
        if inspect.isawaitable(output):
            raise RuntimeError("async web providers are not supported by sync pack wrapper")
        if not isinstance(output, Mapping):
            output = {"value": repr(output)}
        digest = provider_digest(output)
        self._outputs[digest] = output
        return output

    @classmethod
    def last_output(
        cls,
        response_digest: str,
        _provider: object | None,
        _request: WebAcquisitionProviderRequest,
    ) -> object:
        return cls._outputs.get(response_digest, {})


def require_opened_source_proof(
    records: Sequence[WebAcquisitionLiveSourceRecord],
    citation_refs: Sequence[str],
) -> CitationProofDecision:
    opened = {record.source_ref for record in records if record.proof_type == "opened"}
    missing = tuple(ref for ref in citation_refs if ref not in opened)
    if missing:
        return CitationProofDecision(
            status="repair_required",
            missingSourceRefs=missing,
            reasonCodes=("opened_source_proof_required",),
        )
    return CitationProofDecision(status="ok", reasonCodes=("opened_source_proof_satisfied",))


def _result(
    request: WebAcquisitionProviderRequest,
    status: WebAcquisitionProviderStatus,
    digest: str,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    records: tuple[WebAcquisitionLiveSourceRecord, ...] = (),
    receipt: ProviderReceipt | None = None,
    public_preview: str | None = None,
) -> WebAcquisitionProviderResult:
    return WebAcquisitionProviderResult(
        status=status,
        operation=request.operation,
        requestDigest=digest,
        sourceRecords=records,
        providerReceipt=receipt,
        publicPreview=public_preview,
        reasonCodes=reason_codes,
        diagnosticMetadata=safe_metadata(dict(diagnostics)),
        authorityFlags=WebAcquisitionProviderAuthorityFlags(),
    )


def _validate_request(config: WebAcquisitionProviderPackConfig, request: WebAcquisitionProviderRequest) -> str | None:
    if request.operation == "search":
        try:
            normalize_query(request.query or "")
        except ValueError:
            return "query_required"
        return None
    if request.operation == "browser_fallback":
        if not config.browser_fallback_enabled:
            return "browser_fallback_disabled"
        if not request.approval_granted:
            return "browser_fallback_requires_approval"
    if not request.url:
        return "url_required"
    return url_policy_error(request.url)


def _records_from_output(
    request: WebAcquisitionProviderRequest,
    output: object,
    *,
    provider_name: str,
    max_results: int,
    max_content_bytes: int,
) -> tuple[WebAcquisitionLiveSourceRecord, ...]:
    items = _raw_items(output)
    records: list[WebAcquisitionLiveSourceRecord] = []
    for index, item in enumerate(items[:max_results], start=1):
        url = _url_from_item(request, item)
        normalized_url = normalize_public_url(url) if url.startswith(("http://", "https://")) else url
        content = _content_from_item(item, output)[:max_content_bytes]
        digest = content_digest(content or normalized_url)
        source_id = _source_ref(request, index, normalized_url)
        records.append(
            WebAcquisitionLiveSourceRecord(
                sourceRef=source_id,
                evidenceRef=f"evidence:web:{_short_digest(source_id)}",
                providerFetchedRef=f"provider-fetched:{_short_digest(normalized_url)}",
                modelSeenRef=f"model-saw:{_short_digest(f'{source_id}:{digest}')}",
                method=request.operation,
                provider=provider_name,
                urlRef=f"url:{_short_digest(normalized_url)}",
                contentDigest=digest,
                proofType=_proof_type(request.operation),
                title=_optional_text(item.get("title")),
                publicPreview=content,
                metadata=safe_metadata(item.get("metadata")),
            )
        )
    return tuple(records)


def _raw_items(output: object) -> list[Mapping[str, object]]:
    if isinstance(output, Mapping):
        raw_results = output.get("results") or output.get("sources")
        if isinstance(raw_results, Sequence) and not isinstance(raw_results, str | bytes | bytearray):
            return [item for item in raw_results if isinstance(item, Mapping)]
        return [output]
    return [{}]


def _url_from_item(request: WebAcquisitionProviderRequest, item: Mapping[str, object]) -> str:
    raw_url = item.get("url") or request.url
    if isinstance(raw_url, str) and raw_url.strip() and url_policy_error(raw_url) is None:
        return normalize_public_url(raw_url)
    if request.operation == "search":
        return f"search:{_short_digest(normalize_query(request.query or ''))}"
    return f"source:{_short_digest(request.operation)}"


def _content_from_item(item: Mapping[str, object], output: object) -> str:
    for key in ("content", "body", "text", "snippet", "preview"):
        value = item.get(key)
        if isinstance(value, str):
            return redact_public_text(value)
    if isinstance(output, Mapping):
        for key in ("content", "body", "text", "snippet", "preview"):
            value = output.get(key)
            if isinstance(value, str):
                return redact_public_text(value)
    return ""


def _public_preview(output: object) -> str | None:
    if not isinstance(output, Mapping):
        return None
    text = _content_from_item(output, output)
    return text or None


def _provider_status(output: object) -> str | None:
    if isinstance(output, Mapping):
        status = output.get("status")
        if status in {"denied", "timeout"}:
            return str(status)
    return None


def _proof_type(operation: WebAcquisitionProviderOperation) -> WebAcquisitionProofType:
    return "opened" if operation in {"fetch", "reader"} else "observed"


def _source_ref(request: WebAcquisitionProviderRequest, index: int, normalized_url: str) -> str:
    if request.operation == "search":
        return f"source:web:{_short_digest(normalize_query(request.query or ''))}:{index}"
    return f"source:web:{_short_digest(normalized_url)}:{index}"


def _request_payload(request: WebAcquisitionProviderRequest) -> dict[str, object]:
    return {
        "operation": request.operation,
        "requestId": request.request_id,
        "providerName": request.provider_name,
        "query": _normalized_query_or_none(request.query),
        "url": request.url,
        "approvalGranted": request.approval_granted,
    }


def _normalized_query_or_none(query: str | None) -> str | None:
    if query is None:
        return None
    try:
        return normalize_query(query)
    except ValueError:
        return None


def _diagnostics(
    config: WebAcquisitionProviderPackConfig,
    request: WebAcquisitionProviderRequest,
) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "operation": request.operation,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionNetworkEnabled": False,
        "productionWritesEnabled": False,
        "routeAttached": False,
        **dict(request.metadata),
    }


def _run_provider_execution(value: Any) -> Any:
    try:
        value.send(None)
    except StopIteration as exc:
        return exc.value
    raise RuntimeError("web acquisition provider execution unexpectedly awaited")


def _safe_ref(value: str) -> str:
    clean = redact_public_text(value.strip(), max_chars=180)
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("web acquisition refs must be public identifiers")
    return clean


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return redact_public_text(value, max_chars=256)


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "BrowserFallbackProviderPort",
    "CitationProofDecision",
    "FetchProviderPort",
    "ReaderProviderPort",
    "SearchProviderPort",
    "WebAcquisitionLiveSourceRecord",
    "WebAcquisitionProviderAuthorityFlags",
    "WebAcquisitionProviderOperation",
    "WebAcquisitionProviderPack",
    "WebAcquisitionProviderPackConfig",
    "WebAcquisitionProviderRequest",
    "WebAcquisitionProviderResult",
    "require_opened_source_proof",
]
