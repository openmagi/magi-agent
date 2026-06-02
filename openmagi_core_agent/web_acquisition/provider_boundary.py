from __future__ import annotations

from collections.abc import Mapping
import hashlib
import inspect
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.web_acquisition.policy import (
    content_digest,
    evidence_ref,
    normalize_public_url,
    normalize_query,
    redact_public_text,
    safe_metadata,
    source_ref,
    synthetic_url_ref,
    url_policy_error,
)


WebAcquisitionOperation = Literal[
    "web.search",
    "web.fetch",
    "reader.extract",
    "metadata.jsonld",
    "web.acquire",
]
WebAcquisitionStatus = Literal["ok", "error", "blocked", "disabled", "approval_required"]
WebAcquisitionProofType = Literal["opened", "observed"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")


class WebAcquisitionConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    provider_id: str = Field(default="openmagi.web-acquisition.system", alias="providerId")
    max_results: int = Field(default=5, alias="maxResults", ge=1, le=20)
    max_content_bytes: int = Field(default=32_768, alias="maxContentBytes", ge=1)
    timeout_ms: int = Field(default=30_000, alias="timeoutMs", ge=1)
    adk_function_tool_surface: Literal["future"] = Field(
        default="future",
        alias="adkFunctionToolSurface",
    )
    production_network_enabled: Literal[False] = Field(
        default=False,
        alias="productionNetworkEnabled",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )


class WebAcquisitionAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    network_fetched: Literal[False] = Field(default=False, alias="networkFetched")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    raw_content_injected: Literal[False] = Field(default=False, alias="rawContentInjected")
    parent_context_injected: Literal[False] = Field(default=False, alias="parentContextInjected")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "network_fetched",
        "browser_executed",
        "raw_content_injected",
        "parent_context_injected",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class WebAcquisitionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: WebAcquisitionOperation
    turn_id: str = Field(default="turn-local", alias="turnId")
    query: str | None = None
    url: str | None = None
    content: str | None = None
    title: str | None = None
    allow_browser_fallback: bool = Field(default=False, alias="allowBrowserFallback")
    approval_granted: bool = Field(default=False, alias="approvalGranted")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("turn_id")
    @classmethod
    def _validate_turn_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("turnId must be non-empty")
        return value


class WebAcquisitionSourceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    method: WebAcquisitionOperation
    provider: str
    url: str
    normalized_url: str = Field(alias="normalizedUrl")
    content_digest: str = Field(alias="contentDigest")
    proof_type: WebAcquisitionProofType = Field(alias="proofType")
    title: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": _public_ref(self.source_ref, "source"),
            "evidenceRef": _public_ref(self.evidence_ref, "evidence"),
            "method": self.method,
            "provider": redact_public_text(self.provider, max_chars=120),
            "contentDigest": _public_digest(self.content_digest),
            "proofType": self.proof_type,
            "title": redact_public_text(self.title or "", max_chars=160) or None,
            "url": "[redacted]",
            "metadata": safe_metadata(dict(self.metadata)),
        }


class WebAcquisitionResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: WebAcquisitionStatus
    operation: WebAcquisitionOperation
    records: tuple[WebAcquisitionSourceRecord, ...] = ()
    public_preview: str | None = Field(default=None, alias="publicPreview")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    attachment_flags: WebAcquisitionAttachmentFlags = Field(
        default_factory=WebAcquisitionAttachmentFlags,
        alias="attachmentFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        projected_records = [_record_public_projection(record) for record in self.records]
        return {
            "status": self.status,
            "operation": self.operation,
            "sourceRecords": projected_records,
            "parentOutputRefs": [
                ref
                for record in projected_records
                for ref in (record.get("sourceRef"), record.get("evidenceRef"))
            ],
            "publicPreview": (
                None if self.public_preview is None else redact_public_text(self.public_preview, max_chars=1_024)
            ),
            "errorCode": redact_public_text(self.error_code or "", max_chars=120) or None,
            "diagnosticMetadata": safe_metadata(dict(self.diagnostic_metadata)),
            "attachmentFlags": _attachment_flags_public_projection(self.attachment_flags),
        }


class LocalWebAcquisitionRuntime:
    """Provider-neutral web acquisition boundary.

    The runtime never performs network access itself. Enabled local execution can
    call an injected fake provider for contract tests only.
    """

    def __init__(self, config: WebAcquisitionConfig, *, provider: object | None = None) -> None:
        self.config = config
        self.provider = provider

    async def run(self, request: WebAcquisitionRequest) -> WebAcquisitionResult:
        diagnostics = _diagnostics(self.config)
        if not self.config.enabled:
            return _result(
                request,
                "disabled",
                error_code="web_acquisition_disabled",
                diagnostics=diagnostics,
            )

        validation_error = _validate_request(request)
        if validation_error is not None:
            return _result(
                request,
                "blocked",
                error_code=validation_error,
                error_message=validation_error,
                diagnostics=diagnostics,
            )
        if request.allow_browser_fallback and not request.approval_granted:
            return _result(
                request,
                "approval_required",
                error_code="browser_fallback_requires_approval",
                diagnostics=diagnostics,
            )
        if not self.config.local_fake_provider_enabled or self.provider is None:
            return _result(
                request,
                "disabled",
                error_code="local_fake_provider_disabled",
                diagnostics=diagnostics,
            )
        if getattr(self.provider, "openmagi_local_fake_provider", False) is not True:
            return _result(
                request,
                "blocked",
                error_code="local_fake_provider_untrusted",
                diagnostics=diagnostics,
            )

        try:
            provider_output = await self._call_fake_provider(request)
        except Exception as exc:
            return _result(
                request,
                "blocked",
                error_code="local_fake_provider_error",
                error_message=redact_public_text(str(exc), max_chars=240)
                or "[redacted-provider-error]",
                diagnostics=diagnostics,
            )
        diagnostics["localFakeProviderCalled"] = True
        records = tuple(
            _records_from_provider_output(
                request,
                provider_output,
                provider_id=self.config.provider_id,
                max_results=self.config.max_results,
                max_content_bytes=self.config.max_content_bytes,
            )
        )
        preview = _public_preview_from_output(provider_output, max_bytes=self.config.max_content_bytes)
        return WebAcquisitionResult(
            status="ok",
            operation=request.operation,
            records=records,
            publicPreview=preview,
            diagnosticMetadata=diagnostics,
        )

    async def _call_fake_provider(self, request: WebAcquisitionRequest) -> object:
        method_name = {
            "web.search": "search",
            "web.fetch": "fetch",
            "reader.extract": "extract",
            "metadata.jsonld": "metadata",
            "web.acquire": "acquire",
        }[request.operation]
        method = getattr(self.provider, method_name, None)
        if method is None:
            raise ValueError(f"fake provider does not implement {method_name}")
        value = method(request)
        if inspect.isawaitable(value):
            return await value
        return value


def _diagnostics(config: WebAcquisitionConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionNetworkEnabled": False,
        "productionWritesEnabled": False,
        "adkFunctionToolSurface": config.adk_function_tool_surface,
        "localFakeProviderCalled": False,
    }


def _result(
    request: WebAcquisitionRequest,
    status: WebAcquisitionStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    diagnostics: Mapping[str, object],
) -> WebAcquisitionResult:
    return WebAcquisitionResult(
        status=status,
        operation=request.operation,
        errorCode=error_code,
        errorMessage=error_message,
        diagnosticMetadata=diagnostics,
    )


def _validate_request(request: WebAcquisitionRequest) -> str | None:
    if request.operation == "web.search":
        try:
            normalize_query(request.query or "")
        except ValueError:
            return "query_required"
        return None
    if request.operation in {"web.fetch", "web.acquire"}:
        if not request.url:
            return "url_required"
        return url_policy_error(request.url)
    if request.operation in {"reader.extract", "metadata.jsonld"}:
        if not (request.url or request.content):
            return "source_required"
        if request.url:
            return url_policy_error(request.url)
        return None
    return "unsupported_operation"


def _records_from_provider_output(
    request: WebAcquisitionRequest,
    provider_output: object,
    *,
    provider_id: str,
    max_results: int,
    max_content_bytes: int,
) -> list[WebAcquisitionSourceRecord]:
    raw_records = _raw_source_items(provider_output)
    records: list[WebAcquisitionSourceRecord] = []
    for index, item in enumerate(raw_records[:max_results], start=1):
        url = _source_url(request, item)
        normalized_url = _normalized_source_url(request, url)
        content = _source_content(item, provider_output)[:max_content_bytes]
        kind = "web"
        records.append(
            WebAcquisitionSourceRecord(
                sourceRef=source_ref(kind, index),
                evidenceRef=evidence_ref(kind, index),
                method=request.operation,
                provider=provider_id,
                url=url,
                normalizedUrl=normalized_url,
                contentDigest=content_digest(content or normalized_url),
                proofType="opened" if request.operation != "web.search" else "observed",
                title=_optional_text(item.get("title")),
                metadata=safe_metadata(item.get("metadata")),
            )
        )
    return records


def _raw_source_items(provider_output: object) -> list[Mapping[str, object]]:
    if isinstance(provider_output, Mapping):
        raw_results = provider_output.get("results") or provider_output.get("sources")
        if isinstance(raw_results, list | tuple):
            return [item for item in raw_results if isinstance(item, Mapping)]
        return [provider_output]
    if isinstance(provider_output, list | tuple):
        return [item for item in provider_output if isinstance(item, Mapping)]
    return [{}]


def _source_url(request: WebAcquisitionRequest, item: Mapping[str, object]) -> str:
    raw_url = item.get("url") or item.get("finalUrl") or request.url
    if isinstance(raw_url, str) and raw_url.strip():
        error = url_policy_error(raw_url)
        if error is None:
            return normalize_public_url(raw_url)
        return synthetic_url_ref(raw_url, prefix="blocked-source")
    if request.operation == "web.search":
        return synthetic_url_ref(normalize_query(request.query or ""), prefix="search")
    return synthetic_url_ref(request.operation, prefix="source")


def _normalized_source_url(request: WebAcquisitionRequest, url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return normalize_public_url(url)
    if request.operation == "web.search":
        return synthetic_url_ref(url, prefix="search")
    return synthetic_url_ref(url, prefix="source")


def _source_content(item: Mapping[str, object], provider_output: object) -> str:
    for key in ("content", "body", "text", "snippet", "preview"):
        value = item.get(key)
        if isinstance(value, str):
            return redact_public_text(value)
    if isinstance(provider_output, Mapping):
        value = provider_output.get("content") or provider_output.get("body")
        if isinstance(value, str):
            return redact_public_text(value)
    return ""


def _public_preview_from_output(provider_output: object, *, max_bytes: int) -> str | None:
    if isinstance(provider_output, Mapping):
        for key in ("preview", "snippet", "content", "body", "text"):
            value = provider_output.get(key)
            if isinstance(value, str):
                return redact_public_text(value, max_chars=min(max_bytes, 1_024))
    return None


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return redact_public_text(value, max_chars=256)


def _record_public_projection(record: object) -> dict[str, object]:
    if isinstance(record, WebAcquisitionSourceRecord):
        return record.public_projection()
    if isinstance(record, Mapping):
        return {
            "sourceRef": _public_ref(str(record.get("sourceRef") or ""), "source"),
            "evidenceRef": _public_ref(str(record.get("evidenceRef") or ""), "evidence"),
            "method": (
                record.get("method")
                if record.get("method") in WebAcquisitionOperation.__args__
                else "web.acquire"
            ),
            "provider": redact_public_text(str(record.get("provider") or ""), max_chars=120),
            "contentDigest": _public_digest(str(record.get("contentDigest") or "")),
            "proofType": record.get("proofType") if record.get("proofType") in {"opened", "observed"} else "observed",
            "title": redact_public_text(str(record.get("title") or ""), max_chars=160) or None,
            "url": "[redacted]",
            "metadata": safe_metadata(record.get("metadata")),
        }
    return {
        "sourceRef": "source:redacted",
        "evidenceRef": "evidence:redacted",
        "method": "web.acquire",
        "provider": "redacted",
        "contentDigest": content_digest("redacted"),
        "proofType": "observed",
        "title": None,
        "url": "[redacted]",
        "metadata": {},
    }


def _attachment_flags_public_projection(flags: object) -> dict[str, bool]:
    if isinstance(flags, WebAcquisitionAttachmentFlags):
        return flags.model_dump(by_alias=True)
    return WebAcquisitionAttachmentFlags().model_dump(by_alias=True)


def _public_ref(value: str, prefix: str) -> str:
    text = str(value)
    clean = redact_public_text(text, max_chars=180).strip()
    if clean == text.strip() and _PUBLIC_REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _public_digest(value: str) -> str:
    if re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        return value
    return content_digest(redact_public_text(value, max_chars=512))


__all__ = [
    "LocalWebAcquisitionRuntime",
    "WebAcquisitionAttachmentFlags",
    "WebAcquisitionConfig",
    "WebAcquisitionRequest",
    "WebAcquisitionResult",
    "WebAcquisitionSourceRecord",
]
