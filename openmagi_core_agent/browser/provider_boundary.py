from __future__ import annotations

from collections.abc import Mapping
import hashlib
import inspect
import re
from typing import Any, Literal, Self
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.runtime.provider_execution import (
    ProviderExecutionBoundary,
    ProviderExecutionConfig,
    ProviderExecutionRequest,
    ProviderExecutionScope,
)
from openmagi_core_agent.runtime.provider_receipts import provider_digest
from openmagi_core_agent.web_acquisition.policy import (
    content_digest,
    evidence_ref,
    normalize_public_url,
    redact_public_text,
    safe_metadata,
    source_ref,
    url_policy_error,
)


BrowserAction = Literal[
    "browser.open",
    "browser.snapshot",
    "browser.scrape",
    "browser.click",
    "browser.fill",
    "browser.scroll",
    "browser.screenshot",
]
BrowserStatus = Literal["ok", "error", "blocked", "disabled", "approval_required"]
BrowserSessionDecisionStatus = Literal["allowed", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_SELECTOR_RE = re.compile(r"(?:^@e[1-9][0-9]*$|\[ref=e[1-9][0-9]*\])")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SCREENSHOT_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_FORM_ACTIONS = frozenset({"browser.click", "browser.fill"})
_AUTH_SENSITIVE_URL_MARKERS = ("/login", "/signin", "/auth/", "oauth")


class BrowserProviderConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    provider_id: str = Field(default="openmagi.browser-provider.system", alias="providerId")
    session_isolation: Literal["ephemeral_per_turn"] = Field(
        default="ephemeral_per_turn",
        alias="sessionIsolation",
    )
    worker_boundary: Literal["CDP/browser-worker boundary"] = Field(
        default="CDP/browser-worker boundary",
        alias="workerBoundary",
    )
    screenshot_artifact_policy: Literal["sanitized_artifact_ref_only"] = Field(
        default="sanitized_artifact_ref_only",
        alias="screenshotArtifactPolicy",
    )
    timeout_ms: int = Field(default=60_000, alias="timeoutMs", ge=1)
    production_browser_enabled: Literal[False] = Field(
        default=False,
        alias="productionBrowserEnabled",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )


class BrowserSessionLease(BaseModel):
    model_config = _MODEL_CONFIG

    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    observed_frame_refs: tuple[str, ...] = Field(default=(), alias="observedFrameRefs")
    action_count: int = Field(default=0, ge=0, alias="actionCount")
    screenshot_count: int = Field(default=0, ge=0, alias="screenshotCount")
    max_actions: int = Field(default=8, ge=1, alias="maxActions")
    max_screenshots: int = Field(default=2, ge=0, alias="maxScreenshots")
    expired: bool = False

    @field_validator("session_id", "turn_id")
    @classmethod
    def _validate_public_ids(cls, value: str) -> str:
        return _public_ref(value, "browser-session")

    @field_validator("observed_frame_refs")
    @classmethod
    def _validate_observed_frame_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_public_ref(item, "source") for item in value)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class BrowserAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    browser_worker_attached: Literal[False] = Field(default=False, alias="browserWorkerAttached")
    cdp_session_attached: Literal[False] = Field(default=False, alias="cdpSessionAttached")
    raw_snapshot_injected: Literal[False] = Field(default=False, alias="rawSnapshotInjected")
    raw_tool_log_injected: Literal[False] = Field(default=False, alias="rawToolLogInjected")
    parent_context_injected: Literal[False] = Field(default=False, alias="parentContextInjected")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @field_serializer(
        "browser_executed",
        "browser_worker_attached",
        "cdp_session_attached",
        "raw_snapshot_injected",
        "raw_tool_log_injected",
        "parent_context_injected",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class BrowserSessionActionDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: BrowserSessionDecisionStatus
    reason_code: str = Field(alias="reasonCode")
    next_lease: BrowserSessionLease | None = Field(default=None, alias="nextLease")
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")
    attachment_flags: BrowserAttachmentFlags = Field(
        default_factory=BrowserAttachmentFlags,
        alias="attachmentFlags",
    )


class BrowserRequest(BaseModel):
    model_config = _MODEL_CONFIG

    action: BrowserAction
    turn_id: str = Field(default="turn-local", alias="turnId")
    session_id: str | None = Field(default=None, alias="sessionId")
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    screenshot_path: str | None = Field(default=None, alias="screenshotPath")
    approval_granted: bool = Field(default=False, alias="approvalGranted")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("turn_id")
    @classmethod
    def _validate_turn_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("turnId must be non-empty")
        return value


class BrowserSourceRecord(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    artifact_ref: str | None = Field(default=None, alias="artifactRef")
    method: BrowserAction
    provider: str
    url: str
    normalized_url: str = Field(alias="normalizedUrl")
    content_digest: str = Field(alias="contentDigest")
    proof_type: Literal["opened", "observed"] = Field(alias="proofType")
    title: str | None = None
    metadata: Mapping[str, object] = Field(default_factory=dict)

    def public_projection(self) -> dict[str, object]:
        return {
            "sourceRef": _public_ref(self.source_ref, "source"),
            "evidenceRef": _public_ref(self.evidence_ref, "evidence"),
            "artifactRef": (
                None if self.artifact_ref is None else _public_ref(self.artifact_ref, "artifact")
            ),
            "method": self.method,
            "provider": redact_public_text(self.provider, max_chars=120),
            "contentDigest": _public_digest(self.content_digest),
            "proofType": self.proof_type,
            "title": redact_public_text(self.title or "", max_chars=160) or None,
            "url": "[redacted]",
            "metadata": safe_metadata(dict(self.metadata)),
        }


class BrowserProviderResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: BrowserStatus
    action: BrowserAction
    records: tuple[BrowserSourceRecord, ...] = ()
    browser_frame: Mapping[str, object] | None = Field(default=None, alias="browserFrame")
    public_preview: str | None = Field(default=None, alias="publicPreview")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    attachment_flags: BrowserAttachmentFlags = Field(
        default_factory=BrowserAttachmentFlags,
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
            "action": self.action,
            "sourceRecords": projected_records,
            "parentOutputRefs": [
                ref
                for record in projected_records
                for ref in (record.get("sourceRef"), record.get("evidenceRef"), record.get("artifactRef"))
                if ref is not None
            ],
            "browserFrame": _public_browser_frame(self.browser_frame),
            "publicPreview": (
                None if self.public_preview is None else redact_public_text(self.public_preview, max_chars=512)
            ),
            "errorCode": redact_public_text(self.error_code or "", max_chars=120) or None,
            "diagnosticMetadata": safe_metadata(dict(self.diagnostic_metadata)),
            "attachmentFlags": _attachment_flags_public_projection(self.attachment_flags),
        }


class LocalBrowserProviderRuntime:
    """Browser-worker/agent-browser boundary with fake provider execution only."""

    def __init__(self, config: BrowserProviderConfig, *, provider: object | None = None) -> None:
        self.config = config
        self.provider = provider

    async def run(self, request: BrowserRequest) -> BrowserProviderResult:
        diagnostics = _diagnostics(self.config)
        if not self.config.enabled:
            return _result(
                request,
                "disabled",
                error_code="browser_provider_disabled",
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
        if _approval_required(request) and not request.approval_granted:
            return _result(
                request,
                "approval_required",
                error_code="browser_action_requires_approval",
                diagnostics=diagnostics,
            )
        if not self.config.local_fake_provider_enabled or self.provider is None:
            return _result(
                request,
                "disabled",
                error_code="local_fake_browser_provider_disabled",
                diagnostics=diagnostics,
            )
        if getattr(self.provider, "openmagi_local_fake_provider", False) is not True:
            return _result(
                request,
                "blocked",
                error_code="local_fake_browser_provider_untrusted",
                diagnostics=diagnostics,
            )

        adapter = _LocalBrowserProviderExecutionAdapter(self.provider, request)
        try:
            execution_result = await ProviderExecutionBoundary(
                ProviderExecutionConfig(
                    enabled=True,
                    localFakeProviderEnabled=self.config.local_fake_provider_enabled,
                    providerAllowlist=(self.config.provider_id,),
                )
            ).execute(
                _provider_execution_request(request, self.config.provider_id),
                provider=adapter,
            )
        except Exception as exc:
            return _result(
                request,
                "blocked",
                error_code="local_fake_browser_provider_error",
                error_message=redact_public_text(str(exc), max_chars=240)
                or "[redacted-provider-error]",
                diagnostics=diagnostics,
            )
        diagnostics["localFakeProviderCalled"] = execution_result.provider_called
        if execution_result.status != "ok":
            reason = (
                execution_result.reason_codes[0]
                if execution_result.reason_codes
                else "local_fake_browser_provider_error"
            )
            return _result(
                request,
                "blocked",
                error_code=reason,
                diagnostics=diagnostics,
            )
        provider_output = adapter.output if isinstance(adapter.output, Mapping) else {}
        record = _record_from_output(
            request,
            provider_output,
            provider_id=self.config.provider_id,
        )
        if record is not None and not _sanitized_output_has_public_evidence(provider_output):
            return _result(
                request,
                "blocked",
                error_code="browser_output_sanitizer_rejected",
                diagnostics=diagnostics,
            )
        return BrowserProviderResult(
            status="ok",
            action=request.action,
            records=(record,) if record is not None else (),
            browserFrame=_browser_frame(request, record),
            publicPreview=_public_preview(provider_output),
            diagnosticMetadata=diagnostics,
        )

class _LocalBrowserProviderExecutionAdapter:
    openmagi_local_fake_provider = True

    def __init__(self, provider: object, request: BrowserRequest) -> None:
        self.provider = provider
        self.request = request
        self.output: object | None = None

    async def execute(self, _request: ProviderExecutionRequest) -> Mapping[str, object]:
        method = getattr(self.provider, "run", None)
        if method is None:
            method = getattr(self.provider, self.request.action.removeprefix("browser."))
        value = method(self.request)
        if inspect.isawaitable(value):
            value = await value
        self.output = value
        return value if isinstance(value, Mapping) else {"value": repr(value)}


def evaluate_browser_session_action(
    request: BrowserRequest | Mapping[str, object],
    lease: BrowserSessionLease | Mapping[str, object],
) -> BrowserSessionActionDecision:
    safe_request = BrowserRequest.model_validate(request)
    safe_lease = BrowserSessionLease.model_validate(lease)
    validation_error = _validate_request(safe_request)
    if validation_error is not None:
        return _session_decision("blocked", validation_error, None)
    if safe_request.session_id is None:
        return _session_decision("blocked", "session_id_required", None)
    if (
        _public_ref(safe_request.session_id, "browser-session") != safe_lease.session_id
        or _public_ref(safe_request.turn_id, "browser-session") != safe_lease.turn_id
    ):
        return _session_decision("blocked", "session_lease_mismatch", None)
    if _approval_required(safe_request) and not safe_request.approval_granted:
        return _session_decision("blocked", "browser_action_requires_approval", None)
    if safe_lease.expired:
        return _session_decision("blocked", "session_expired", None)
    if safe_request.action in {"browser.click", "browser.fill"} and not safe_lease.observed_frame_refs:
        return _session_decision("blocked", "observed_frame_required", None)
    if safe_lease.action_count >= safe_lease.max_actions:
        return _session_decision("blocked", "action_budget_exceeded", None)
    if (
        safe_request.action == "browser.screenshot"
        and safe_lease.screenshot_count >= safe_lease.max_screenshots
    ):
        return _session_decision("blocked", "screenshot_budget_exceeded", None)

    next_lease = safe_lease.model_copy(
        update={
            "actionCount": safe_lease.action_count + 1,
            "screenshotCount": (
                safe_lease.screenshot_count + 1
                if safe_request.action == "browser.screenshot"
                else safe_lease.screenshot_count
            ),
        }
    )
    if safe_request.action in {"browser.open", "browser.snapshot", "browser.scrape"}:
        next_lease = next_lease.model_copy(
            update={
                "observedFrameRefs": tuple(
                    dict.fromkeys(
                        (
                            *next_lease.observed_frame_refs,
                            _public_ref(f"source:browser:{safe_lease.turn_id}", "source"),
                        )
                    )
                )
            }
        )
    return _session_decision("allowed", "session_action_planned", next_lease)


def _diagnostics(config: BrowserProviderConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionBrowserEnabled": False,
        "productionWritesEnabled": False,
        "sessionIsolation": config.session_isolation,
        "workerBoundary": config.worker_boundary,
        "screenshotArtifactPolicy": config.screenshot_artifact_policy,
        "localFakeProviderCalled": False,
    }


def _provider_execution_request(
    request: BrowserRequest,
    provider_id: str,
) -> ProviderExecutionRequest:
    return ProviderExecutionRequest(
        providerName=provider_id,
        operation=request.action,
        payload={
            "action": request.action,
            "turnId": request.turn_id,
            "sessionId": request.session_id,
            "url": request.url,
            "selector": request.selector,
            "direction": request.direction,
            "screenshotPath": request.screenshot_path,
            "approvalGranted": request.approval_granted,
        },
        scope=ProviderExecutionScope(
            environment="test",
            botIdDigest="browser-boundary",
            ownerIdDigest="browser-boundary",
            selectedScope=True,
            sessionIdDigest=provider_digest(request.session_id or request.turn_id),
        ),
    )


def _session_decision(
    status: BrowserSessionDecisionStatus,
    reason_code: str,
    next_lease: BrowserSessionLease | None,
) -> BrowserSessionActionDecision:
    return BrowserSessionActionDecision(
        status=status,
        reasonCode=reason_code,
        nextLease=next_lease,
        executionAllowed=False,
        attachmentFlags=BrowserAttachmentFlags(),
    )


def _result(
    request: BrowserRequest,
    status: BrowserStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    diagnostics: Mapping[str, object],
) -> BrowserProviderResult:
    return BrowserProviderResult(
        status=status,
        action=request.action,
        errorCode=error_code,
        errorMessage=error_message,
        diagnosticMetadata=diagnostics,
    )


def _validate_request(request: BrowserRequest) -> str | None:
    if request.action == "browser.open":
        if not request.url:
            return "url_required"
        url_error = url_policy_error(request.url)
        return url_error
    if request.action in {"browser.click", "browser.fill"}:
        if not request.selector or _REF_SELECTOR_RE.search(request.selector) is None:
            return "selector_required"
        if request.action == "browser.fill" and request.text is None:
            return "text_required"
    if request.action == "browser.scroll" and request.direction is None:
        return "direction_required"
    if request.action == "browser.screenshot":
        if not request.screenshot_path:
            return "screenshot_path_required"
        if (
            request.screenshot_path.startswith(("/", "~"))
            or ".." in request.screenshot_path.split("/")
            or _SCREENSHOT_PATH_RE.fullmatch(request.screenshot_path) is None
        ):
            return "invalid_screenshot_path"
    if _looks_private_or_captcha(request.text):
        return "private_or_captcha_payload_blocked"
    return None


def _approval_required(request: BrowserRequest) -> bool:
    if request.action in _FORM_ACTIONS:
        return True
    if request.action == "browser.screenshot":
        return True
    if request.url and _auth_sensitive_browser_url(request.url):
        return True
    return False


def _auth_sensitive_browser_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    haystacks = (
        _decode_bounded(parts.path),
        _decode_bounded(parts.query),
        _decode_bounded(parts.fragment),
    )
    return any(
        any(marker in haystack.casefold() for marker in _AUTH_SENSITIVE_URL_MARKERS)
        for haystack in haystacks
    )


def _decode_bounded(value: str, *, rounds: int = 4) -> str:
    current = value
    for _ in range(rounds):
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


def _record_from_output(
    request: BrowserRequest,
    provider_output: object,
    *,
    provider_id: str,
) -> BrowserSourceRecord | None:
    if request.action in {"browser.click", "browser.fill", "browser.scroll"}:
        return None
    output = provider_output if isinstance(provider_output, Mapping) else {}
    raw_url = output.get("url") or request.url or "about:blank"
    if (
        isinstance(raw_url, str)
        and raw_url.startswith(("http://", "https://"))
        and url_policy_error(raw_url) is None
    ):
        url = normalize_public_url(raw_url)
    else:
        url = "browser:session"
    content = _output_text(output)
    artifact_ref = None
    if request.action in {"browser.snapshot", "browser.screenshot"}:
        artifact_ref = _artifact_ref(request, content)
    return BrowserSourceRecord(
        sourceRef=source_ref("browser", 1),
        evidenceRef=evidence_ref("browser", 1),
        artifactRef=artifact_ref,
        method=request.action,
        provider=provider_id,
        url=url,
        normalizedUrl=url,
        contentDigest=content_digest(content or url),
        proofType="opened" if request.action == "browser.open" else "observed",
        title=_optional_text(output.get("title")),
        metadata=safe_metadata(output.get("metadata")),
    )


def _output_text(output: Mapping[str, object]) -> str:
    for key in ("visibleText", "text", "html", "snapshot", "content"):
        value = output.get(key)
        if isinstance(value, str):
            return redact_public_text(value)
    return ""


def _browser_frame(
    request: BrowserRequest,
    record: BrowserSourceRecord | None,
) -> Mapping[str, object] | None:
    if request.action not in {"browser.open", "browser.snapshot", "browser.screenshot"}:
        return None
    return {
        "type": "browser_frame",
        "action": request.action,
        "sourceRef": _public_ref(record.source_ref, "source") if record is not None else None,
        "evidenceRef": _public_ref(record.evidence_ref, "evidence") if record is not None else None,
        "artifactRef": (
            _public_ref(record.artifact_ref, "artifact")
            if record is not None and record.artifact_ref is not None
            else None
        ),
        "imageBase64": None,
        "rawSnapshotInjected": False,
        "rawToolLogInjected": False,
    }


def _public_preview(provider_output: object) -> str | None:
    if not isinstance(provider_output, Mapping):
        return None
    text = _output_text(provider_output)
    return text[:512] if text else None


def _sanitized_output_has_public_evidence(provider_output: Mapping[str, object]) -> bool:
    for key in ("visibleText", "text", "html", "snapshot", "content", "title"):
        value = provider_output.get(key)
        if isinstance(value, str) and redact_public_text(value, max_chars=512).strip():
            return True
    return False


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return redact_public_text(value, max_chars=160)


def _artifact_ref(request: BrowserRequest, content: str) -> str:
    seed = f"{request.turn_id}:{request.action}:{request.screenshot_path or ''}:{content}"
    return "artifact:browser-snapshot:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _looks_private_or_captcha(value: object) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.casefold()
    return any(
        marker in lowered
        for marker in ("captcha", "password", "cookie:", "authorization:", "bearer ")
    )


def _record_public_projection(record: object) -> dict[str, object]:
    if isinstance(record, BrowserSourceRecord):
        return record.public_projection()
    if isinstance(record, Mapping):
        return {
            "sourceRef": _public_ref(str(record.get("sourceRef") or ""), "source"),
            "evidenceRef": _public_ref(str(record.get("evidenceRef") or ""), "evidence"),
            "artifactRef": (
                None
                if record.get("artifactRef") is None
                else _public_ref(str(record.get("artifactRef")), "artifact")
            ),
            "method": record.get("method") if record.get("method") in BrowserAction.__args__ else "browser.snapshot",
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
        "artifactRef": None,
        "method": "browser.snapshot",
        "provider": "redacted",
        "contentDigest": content_digest("redacted"),
        "proofType": "observed",
        "title": None,
        "url": "[redacted]",
        "metadata": {},
    }


def _public_browser_frame(frame: object) -> dict[str, object] | None:
    if not isinstance(frame, Mapping):
        return None
    action = frame.get("action")
    return {
        "type": "browser_frame",
        "action": action if action in BrowserAction.__args__ else "browser.snapshot",
        "sourceRef": (
            None if frame.get("sourceRef") is None else _public_ref(str(frame.get("sourceRef")), "source")
        ),
        "evidenceRef": (
            None
            if frame.get("evidenceRef") is None
            else _public_ref(str(frame.get("evidenceRef")), "evidence")
        ),
        "artifactRef": (
            None
            if frame.get("artifactRef") is None
            else _public_ref(str(frame.get("artifactRef")), "artifact")
        ),
        "imageBase64": None,
        "rawSnapshotInjected": False,
        "rawToolLogInjected": False,
    }


def _attachment_flags_public_projection(flags: object) -> dict[str, bool]:
    if isinstance(flags, BrowserAttachmentFlags):
        return flags.model_dump(by_alias=True)
    return BrowserAttachmentFlags().model_dump(by_alias=True)


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


def validate_browser_provider_request(request: BrowserRequest) -> str | None:
    return _validate_request(request)


def browser_provider_action_requires_approval(request: BrowserRequest) -> bool:
    return _approval_required(request)


def build_browser_provider_source_record(
    request: BrowserRequest,
    provider_output: object,
    *,
    provider_id: str,
) -> BrowserSourceRecord | None:
    return _record_from_output(request, provider_output, provider_id=provider_id)


def build_browser_provider_frame(
    request: BrowserRequest,
    record: BrowserSourceRecord | None,
) -> Mapping[str, object] | None:
    return _browser_frame(request, record)


def browser_provider_public_preview(provider_output: object) -> str | None:
    return _public_preview(provider_output)


__all__ = [
    "BrowserAttachmentFlags",
    "BrowserProviderConfig",
    "BrowserProviderResult",
    "BrowserRequest",
    "BrowserSessionActionDecision",
    "BrowserSessionLease",
    "BrowserSourceRecord",
    "LocalBrowserProviderRuntime",
    "browser_provider_action_requires_approval",
    "browser_provider_public_preview",
    "build_browser_provider_frame",
    "build_browser_provider_source_record",
    "evaluate_browser_session_action",
    "validate_browser_provider_request",
]
