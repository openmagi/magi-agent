from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.browser.provider_boundary import (
    BrowserAction,
    BrowserAttachmentFlags,
    BrowserRequest,
    BrowserSourceRecord,
    browser_provider_action_requires_approval,
    browser_provider_public_preview,
    build_browser_provider_frame,
    build_browser_provider_source_record,
    validate_browser_provider_request,
)
from magi_agent.runtime.provider_execution import (
    ProviderExecutionBoundary,
    ProviderExecutionConfig,
    ProviderExecutionRequest,
    ProviderExecutionScope,
)
from magi_agent.runtime.provider_receipts import ProviderReceipt, provider_digest
from magi_agent.web_acquisition.policy import redact_public_text, safe_metadata


BrowserProviderPackStatus = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "ok",
    "no_answer",
    "repair_required",
]
BrowserProviderContext = Literal["direct", "research", "web_acquisition"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_ACTION_TO_PROVIDER_METHOD: Mapping[BrowserAction, str] = {
    "browser.open": "open",
    "browser.snapshot": "snapshot",
    "browser.scrape": "scrape",
    "browser.click": "click",
    "browser.fill": "fill",
    "browser.scroll": "scroll",
    "browser.screenshot": "screenshot",
}


class BrowserWorkerProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def open(self, request: BrowserProviderPackRequest) -> Mapping[str, object]: ...

    def snapshot(self, request: BrowserProviderPackRequest) -> Mapping[str, object]: ...

    def scrape(self, request: BrowserProviderPackRequest) -> Mapping[str, object]: ...

    def click(self, request: BrowserProviderPackRequest) -> Mapping[str, object]: ...

    def fill(self, request: BrowserProviderPackRequest) -> Mapping[str, object]: ...

    def scroll(self, request: BrowserProviderPackRequest) -> Mapping[str, object]: ...

    def screenshot(self, request: BrowserProviderPackRequest) -> Mapping[str, object]: ...


class BrowserProviderPackConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    provider_id: str = Field(default="openmagi.browser-provider.system", alias="providerId")
    provider_allowlist: tuple[str, ...] = Field(default=(), alias="providerAllowlist")
    timeout_ms: int = Field(default=60_000, alias="timeoutMs", ge=1)
    max_content_bytes: int = Field(default=32_768, alias="maxContentBytes", ge=1)
    browser_fallback_enabled: bool = Field(default=False, alias="browserFallbackEnabled")
    production_browser_enabled: Literal[False] = Field(default=False, alias="productionBrowserEnabled")
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
        data["production_browser_enabled"] = False
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


class BrowserProviderPackAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    browser_worker_attached: Literal[False] = Field(default=False, alias="browserWorkerAttached")
    cdp_session_attached: Literal[False] = Field(default=False, alias="cdpSessionAttached")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    raw_snapshot_injected: Literal[False] = Field(default=False, alias="rawSnapshotInjected")
    raw_tool_log_injected: Literal[False] = Field(default=False, alias="rawToolLogInjected")
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
        "browser_executed",
        "browser_worker_attached",
        "cdp_session_attached",
        "production_writes_enabled",
        "raw_snapshot_injected",
        "raw_tool_log_injected",
        "parent_context_injected",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class BrowserProviderPackRequest(BaseModel):
    model_config = _MODEL_CONFIG

    action: BrowserAction
    request_id: str = Field(alias="requestId")
    provider_name: str = Field(alias="providerName")
    bot_id_digest: str = Field(alias="botIdDigest")
    owner_id_digest: str = Field(alias="ownerIdDigest")
    session_key_digest: str = Field(alias="sessionKeyDigest")
    turn_id: str = Field(default="turn-local", alias="turnId")
    session_id: str | None = Field(default=None, alias="sessionId")
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    screenshot_path: str | None = Field(default=None, alias="screenshotPath")
    approval_granted: bool = Field(default=False, alias="approvalGranted")
    context: BrowserProviderContext = "direct"
    web_acquisition_browser_fallback_selected: bool = Field(
        default=False,
        alias="webAcquisitionBrowserFallbackSelected",
    )
    web_acquisition_policy_allows_browser: bool = Field(
        default=False,
        alias="webAcquisitionPolicyAllowsBrowser",
    )
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "provider_name", "bot_id_digest", "owner_id_digest", "session_key_digest")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("turn_id")
    @classmethod
    def _validate_turn_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("turnId must be non-empty")
        return value

    def browser_request(self, *, approval_granted: bool = False) -> BrowserRequest:
        return BrowserRequest(
            action=self.action,
            turnId=self.turn_id,
            sessionId=self.session_id,
            url=self.url,
            selector=self.selector,
            text=self.text,
            direction=self.direction,
            screenshotPath=self.screenshot_path,
            approvalGranted=approval_granted,
            metadata=self.metadata,
        )


class BrowserProviderPackApprovalReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    receipt_ref: str = Field(alias="receiptRef")
    request_digest: str = Field(alias="requestDigest")
    action: BrowserAction
    approved: Literal[True] = True

    @field_validator("receipt_ref", "request_digest")
    @classmethod
    def _validate_receipt_ref(cls, value: str) -> str:
        return _safe_ref(value)


class BrowserProviderPackResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: BrowserProviderPackStatus
    action: BrowserAction
    request_digest: str = Field(alias="requestDigest")
    source_records: tuple[BrowserSourceRecord, ...] = Field(default=(), alias="sourceRecords")
    provider_receipt: ProviderReceipt | None = Field(default=None, alias="providerReceipt")
    browser_frame: Mapping[str, object] | None = Field(default=None, alias="browserFrame")
    public_preview: str | None = Field(default=None, alias="publicPreview")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: BrowserProviderPackAuthorityFlags = Field(
        default_factory=BrowserProviderPackAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = BrowserProviderPackAuthorityFlags()
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
        data["authority_flags"] = BrowserProviderPackAuthorityFlags()
        _ = deep
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        projected_records = [record.public_projection() for record in self.source_records]
        return {
            "status": self.status,
            "action": self.action,
            "requestDigest": self.request_digest,
            "sourceRecords": projected_records,
            "parentOutputRefs": [
                ref
                for record in projected_records
                for ref in (record.get("sourceRef"), record.get("evidenceRef"), record.get("artifactRef"))
                if ref is not None
            ],
            "providerReceipt": None
            if self.provider_receipt is None
            else self.provider_receipt.model_dump(by_alias=True),
            "browserFrame": _public_browser_frame(self.browser_frame),
            "publicPreview": None if self.public_preview is None else redact_public_text(self.public_preview, max_chars=512),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": safe_metadata(dict(self.diagnostic_metadata)),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class BrowserProviderPack:
    """Default-off browser-worker/agent-browser provider pack boundary."""

    def __init__(self, config: BrowserProviderPackConfig) -> None:
        self.config = config

    def run(
        self,
        request: BrowserProviderPackRequest,
        *,
        provider: object | None = None,
        approval_receipt: BrowserProviderPackApprovalReceipt | Mapping[str, object] | None = None,
    ) -> BrowserProviderPackResult:
        diagnostics = _diagnostics(self.config, request)
        approval_granted = _host_approval_granted(request, approval_receipt)
        browser_request = request.browser_request(approval_granted=approval_granted)
        if not self.config.enabled:
            return _result(
                request,
                "disabled",
                ("browser_provider_pack_disabled",),
                diagnostics,
            )

        research_gate_error = _research_gate_error(self.config, request)
        if research_gate_error is not None:
            return _result(request, "blocked", (research_gate_error,), diagnostics)

        validation_error = validate_browser_provider_request(browser_request)
        if validation_error is not None:
            return _result(request, "blocked", (validation_error,), diagnostics)

        if browser_provider_action_requires_approval(browser_request) and not approval_granted:
            return _result(
                request,
                "approval_required",
                ("browser_action_requires_approval",),
                diagnostics,
            )

        gate_error = _provider_gate_error(self.config, request, provider)
        if gate_error is not None:
            status: BrowserProviderPackStatus = (
                "disabled" if gate_error == "local_fake_browser_provider_disabled" else "blocked"
            )
            return _result(request, status, (gate_error,), diagnostics)

        adapter = _BrowserOperationProvider(provider, request)
        execution_result = _run_provider_execution(
            ProviderExecutionBoundary(
                ProviderExecutionConfig(
                    enabled=True,
                    localFakeProviderEnabled=self.config.local_fake_provider_enabled,
                    providerAllowlist=self.config.provider_allowlist,
                )
            ).execute(
                _provider_execution_request(request),
                provider=adapter,
            )
        )
        diagnostics = {
            **diagnostics,
            "localFakeProviderCalled": execution_result.provider_called,
        }
        if execution_result.status != "ok":
            status = "repair_required" if execution_result.status == "error" else "blocked"
            return _result(
                request,
                status,
                tuple(execution_result.reason_codes) or ("browser_provider_execution_blocked",),
                diagnostics,
                provider_receipt=execution_result.receipt,
            )

        provider_output = adapter.output if isinstance(adapter.output, Mapping) else {}
        output_status = str(provider_output.get("status") or "ok")
        if output_status in {"denied", "blocked", "refused"}:
            return _result(
                request,
                "no_answer",
                ("browser_provider_denied",),
                diagnostics,
                provider_receipt=execution_result.receipt,
            )
        if output_status in {"timeout", "error"}:
            return _result(
                request,
                "repair_required",
                ("browser_provider_timeout_or_error",),
                diagnostics,
                provider_receipt=execution_result.receipt,
            )

        source_record = build_browser_provider_source_record(
            browser_request,
            provider_output,
            provider_id=self.config.provider_id,
        )
        if source_record is not None and not _sanitized_output_has_public_evidence(provider_output):
            return _result(
                request,
                "repair_required",
                ("browser_output_sanitizer_rejected",),
                diagnostics,
                provider_receipt=execution_result.receipt,
            )
        records = () if source_record is None else (source_record,)
        return BrowserProviderPackResult(
            status="ok",
            action=request.action,
            requestDigest=provider_digest(_request_digest_payload(request)),
            sourceRecords=records,
            providerReceipt=execution_result.receipt,
            browserFrame=build_browser_provider_frame(browser_request, source_record),
            publicPreview=browser_provider_public_preview(provider_output),
            diagnosticMetadata=diagnostics,
            authorityFlags=BrowserProviderPackAuthorityFlags(),
        )


class _BrowserOperationProvider:
    openmagi_local_fake_provider = True

    def __init__(self, provider: object, request: BrowserProviderPackRequest) -> None:
        self.provider = provider
        self.request = request
        self.output: object | None = None

    def execute(self, _request: ProviderExecutionRequest) -> Mapping[str, object]:
        method_name = _ACTION_TO_PROVIDER_METHOD[self.request.action]
        method = getattr(self.provider, method_name, None)
        if method is None and self.request.action == "browser.fill":
            method = getattr(self.provider, "type", None)
        if method is None:
            raise RuntimeError("browser provider method missing")
        output = method(self.request)
        self.output = output
        return output if isinstance(output, Mapping) else {"value": repr(output)}


def _run_provider_execution(coro: object) -> Any:
    try:
        return coro.send(None)  # type: ignore[attr-defined]
    except StopIteration as stop:
        return stop.value
    finally:
        close = getattr(coro, "close", None)
        if close is not None:
            close()


def _provider_execution_request(request: BrowserProviderPackRequest) -> ProviderExecutionRequest:
    return ProviderExecutionRequest(
        providerName=request.provider_name,
        operation=request.action,
        payload=_request_digest_payload(request),
        scope=ProviderExecutionScope(
            environment="test",
            botIdDigest=request.bot_id_digest,
            ownerIdDigest=request.owner_id_digest,
            selectedScope=True,
            sessionIdDigest=request.session_key_digest,
        ),
        requestId=request.request_id,
    )


def _request_digest_payload(request: BrowserProviderPackRequest) -> dict[str, object]:
    return {
        "requestId": request.request_id,
        "action": request.action,
        "providerName": request.provider_name,
        "turnId": request.turn_id,
        "sessionId": request.session_id,
        "url": request.url,
        "selector": request.selector,
        "textDigest": None if request.text is None else _raw_text_digest(request.text),
        "direction": request.direction,
        "screenshotPath": request.screenshot_path,
        "context": request.context,
    }


def browser_provider_pack_request_digest(request: BrowserProviderPackRequest) -> str:
    return provider_digest(_request_digest_payload(request))


def _raw_text_digest(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _host_approval_granted(
    request: BrowserProviderPackRequest,
    approval_receipt: BrowserProviderPackApprovalReceipt | Mapping[str, object] | None,
) -> bool:
    if approval_receipt is None:
        return False
    receipt = (
        approval_receipt
        if isinstance(approval_receipt, BrowserProviderPackApprovalReceipt)
        else BrowserProviderPackApprovalReceipt.model_validate(approval_receipt)
    )
    return (
        receipt.approved is True
        and receipt.action == request.action
        and receipt.request_digest == browser_provider_pack_request_digest(request)
    )


def _research_gate_error(
    config: BrowserProviderPackConfig,
    request: BrowserProviderPackRequest,
) -> str | None:
    if request.context in {"research", "web_acquisition"}:
        if not config.browser_fallback_enabled or not request.web_acquisition_browser_fallback_selected:
            return "web_acquisition_browser_fallback_not_selected"
        if not request.web_acquisition_policy_allows_browser:
            return "web_acquisition_browser_policy_blocked"
    return None


def _provider_gate_error(
    config: BrowserProviderPackConfig,
    request: BrowserProviderPackRequest,
    provider: object | None,
) -> str | None:
    if not config.local_fake_provider_enabled or provider is None:
        return "local_fake_browser_provider_disabled"
    if getattr(provider, "openmagi_local_fake_provider", False) is not True:
        return "local_fake_browser_provider_untrusted"
    if config.provider_allowlist and request.provider_name not in config.provider_allowlist:
        return "browser_provider_not_allowlisted"
    return None


def _diagnostics(
    config: BrowserProviderPackConfig,
    request: BrowserProviderPackRequest,
) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionBrowserEnabled": False,
        "productionWritesEnabled": False,
        "routeAttached": False,
        "providerRef": _diagnostic_ref(request.provider_name, "provider"),
        "actionRef": _diagnostic_ref(request.action, "browser-action"),
        "localFakeProviderCalled": False,
    }


def _result(
    request: BrowserProviderPackRequest,
    status: BrowserProviderPackStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    provider_receipt: ProviderReceipt | None = None,
) -> BrowserProviderPackResult:
    return BrowserProviderPackResult(
        status=status,
        action=request.action,
        requestDigest=provider_digest(_request_digest_payload(request)),
        providerReceipt=provider_receipt,
        reasonCodes=reason_codes,
        diagnosticMetadata=diagnostics,
        authorityFlags=BrowserProviderPackAuthorityFlags(),
    )


def _sanitized_output_has_public_evidence(provider_output: Mapping[str, object]) -> bool:
    for key in ("visibleText", "text", "html", "snapshot", "content", "title"):
        value = provider_output.get(key)
        if isinstance(value, str) and redact_public_text(value, max_chars=512).strip():
            return True
    return False


def _public_browser_frame(frame: object) -> dict[str, object] | None:
    if not isinstance(frame, Mapping):
        return None
    return {
        "type": "browser_frame",
        "action": frame.get("action") if frame.get("action") in _ACTION_TO_PROVIDER_METHOD else "browser.snapshot",
        "sourceRef": _safe_public_ref(frame.get("sourceRef"), "source") if frame.get("sourceRef") is not None else None,
        "evidenceRef": (
            _safe_public_ref(frame.get("evidenceRef"), "evidence") if frame.get("evidenceRef") is not None else None
        ),
        "artifactRef": (
            _safe_public_ref(frame.get("artifactRef"), "artifact") if frame.get("artifactRef") is not None else None
        ),
        "imageBase64": None,
        "rawSnapshotInjected": False,
        "rawToolLogInjected": False,
    }


def _safe_ref(value: str) -> str:
    text = str(value).strip()
    if text and _REF_RE.fullmatch(text):
        return text
    return f"browser-ref:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _safe_public_ref(value: object, prefix: str) -> str:
    text = str(value or "").strip()
    clean = redact_public_text(text, max_chars=180).strip()
    if clean == text and clean and _REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


def _diagnostic_ref(value: str, prefix: str) -> str:
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


__all__ = [
    "BrowserProviderPack",
    "BrowserProviderPackApprovalReceipt",
    "BrowserProviderPackAuthorityFlags",
    "BrowserProviderPackConfig",
    "BrowserProviderPackRequest",
    "BrowserProviderPackResult",
    "BrowserProviderPackStatus",
    "BrowserWorkerProviderPort",
    "browser_provider_pack_request_digest",
]
