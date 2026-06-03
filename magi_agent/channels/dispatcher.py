from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.channels.contract import ChannelRef, ChannelType
from magi_agent.channels.runtime_boundary import (
    ChannelRuntimeOperation,
    ChannelRuntimeReceipt,
)
from magi_agent.channels.workflow_routing import WorkflowRouteDecision, decide_workflow_route
from magi_agent.runtime.provider_execution import (
    ProviderExecutionBoundary,
    ProviderExecutionConfig,
    ProviderExecutionRequest,
    ProviderExecutionScope,
)
from magi_agent.runtime.provider_receipts import provider_digest


ChannelDispatchStatus = Literal["disabled", "blocked", "recorded_local_fake", "error"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{6,}|gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{6,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"(?:authorization|cookie|set-cookie|password|token|secret|credential|api[_-]?key)"
    r"\s*[:=]\s*[^,\s}{\n]{3,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "auth",
    "cookie",
    "credential",
    "hidden",
    "key",
    "password",
    "path",
    "private",
    "raw",
    "secret",
    "token",
)


class ChannelDispatchProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def execute(self, request: ChannelDispatchRequest) -> Mapping[str, object]: ...


class ChannelDispatchConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    selected_channel_routes: tuple[ChannelType, ...] = Field(default=(), alias="selectedChannelRoutes")
    provider_allowlist: tuple[str, ...] = Field(default=(), alias="providerAllowlist")
    web_app_canary_route_enabled: bool = Field(default=False, alias="webAppCanaryRouteEnabled")
    production_channel_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelWritesEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(str(key), str(key)): value for key, value in update.items()})
        data["production_channel_writes_enabled"] = False
        data["route_attached"] = False
        _ = deep
        return type(self).model_validate(data)

    @field_validator("selected_channel_routes", mode="before")
    @classmethod
    def _coerce_routes(cls, value: object) -> tuple[ChannelType, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)  # type: ignore[return-value]
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(value)  # type: ignore[return-value]
        return ()

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


class ChannelDispatchAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    production_channel_write: Literal[False] = Field(default=False, alias="productionChannelWrite")
    web_app_canary_attached: Literal[False] = Field(default=False, alias="webAppCanaryAttached")
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
        "production_channel_write",
        "web_app_canary_attached",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ChannelDispatchRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: ChannelRuntimeOperation
    request_id: str = Field(alias="requestId")
    channel: ChannelRef
    provider_name: str = Field(alias="providerName")
    bot_id_digest: str = Field(alias="botIdDigest")
    user_id_digest: str = Field(alias="userIdDigest")
    session_key_digest: str = Field(alias="sessionKeyDigest")
    text: str | None = None
    file_ref: str | None = Field(default=None, alias="fileRef")
    metadata: Mapping[str, object] = Field(default_factory=dict)


class ChannelDispatchDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ChannelDispatchStatus
    request_id: str = Field(alias="requestId")
    request_digest: str = Field(alias="requestDigest")
    receipt: ChannelRuntimeReceipt | None = None
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: ChannelDispatchAuthorityFlags = Field(
        default_factory=ChannelDispatchAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ChannelDispatchAuthorityFlags()
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
        data["authority_flags"] = ChannelDispatchAuthorityFlags()
        _ = deep
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "requestId": _public_ref(self.request_id, "request"),
            "requestDigest": self.request_digest,
            "receipt": None if self.receipt is None else self.receipt.public_projection(),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class ChannelDispatcher:
    """Default-off channel dispatcher with provider-execution receipts."""

    def __init__(self, config: ChannelDispatchConfig) -> None:
        self.config = config
        self._receipt_cache: dict[str, ChannelRuntimeReceipt] = {}

    def dispatch(
        self,
        request: ChannelDispatchRequest,
        *,
        provider: ChannelDispatchProviderPort | None = None,
    ) -> ChannelDispatchDecision:
        request_digest = _request_digest(request)
        diagnostics = _diagnostics(self.config, request)
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                request_digest,
                reason_codes=("channel_dispatch_disabled",),
                diagnostics=diagnostics,
            )
        validation_error = _validation_error(self.config, request, provider)
        if validation_error is not None:
            return _decision(
                request,
                "blocked",
                request_digest,
                reason_codes=(validation_error,),
                diagnostics=diagnostics,
            )
        if request_digest in self._receipt_cache:
            return _decision(
                request,
                "recorded_local_fake",
                request_digest,
                receipt=self._receipt_cache[request_digest],
                reason_codes=("channel_dispatch_idempotent_receipt",),
                diagnostics=diagnostics,
            )

        captured = _CapturingDispatchProvider(provider, request)
        execution_result = _run_provider_execution(
            ProviderExecutionBoundary(
                ProviderExecutionConfig(
                    enabled=True,
                    localFakeProviderEnabled=self.config.local_fake_provider_enabled,
                )
            ).execute(
                ProviderExecutionRequest(
                    providerName=request.provider_name,
                    operation=f"channel.{request.operation}",
                    payload=_provider_payload(request),
                    scope=ProviderExecutionScope(
                        environment="local-test",
                        botIdDigest=request.bot_id_digest,
                        ownerIdDigest=request.user_id_digest,
                        selectedScope=True,
                        sessionIdDigest=request.session_key_digest,
                    ),
                ),
                provider=captured,
            )
        )
        if execution_result.status != "ok" or captured.output is None:
            return _decision(
                request,
                "error",
                request_digest,
                reason_codes=("channel_dispatch_provider_error",),
                diagnostics=diagnostics,
            )
        receipt = _receipt_from_output(request, request_digest, captured.output)
        self._receipt_cache[request_digest] = receipt
        return _decision(
            request,
            "recorded_local_fake",
            request_digest,
            receipt=receipt,
            reason_codes=("channel_dispatch_local_fake_receipt",),
            diagnostics=diagnostics,
        )


class _CapturingDispatchProvider:
    openmagi_local_fake_provider = True

    def __init__(self, provider: ChannelDispatchProviderPort | None, request: ChannelDispatchRequest) -> None:
        self.provider = provider
        self.request = request
        self.output: Mapping[str, object] | None = None

    def execute(self, _request: object) -> Mapping[str, object]:
        if self.provider is None:
            raise RuntimeError("provider missing")
        value = self.provider.execute(self.request)
        self.output = value
        return value


def _validation_error(
    config: ChannelDispatchConfig,
    request: ChannelDispatchRequest,
    provider: ChannelDispatchProviderPort | None,
) -> str | None:
    if not request.bot_id_digest.strip():
        return "bot_id_digest_required"
    if not request.user_id_digest.strip():
        return "user_id_digest_required"
    if not request.session_key_digest.strip():
        return "session_key_digest_required"
    if request.channel.type not in set(config.selected_channel_routes):
        return "channel_route_not_selected"
    if not config.provider_allowlist or request.provider_name not in config.provider_allowlist:
        return "provider_not_allowlisted"
    if not config.local_fake_provider_enabled or provider is None:
        return "local_fake_channel_provider_disabled"
    if getattr(provider, "openmagi_local_fake_provider", False) is not True:
        return "local_fake_channel_provider_untrusted"
    return None


def _run_provider_execution(coro: object) -> object:
    try:
        return coro.send(None)  # type: ignore[attr-defined]
    except StopIteration as exc:
        return exc.value


def _decision(
    request: ChannelDispatchRequest,
    status: ChannelDispatchStatus,
    request_digest: str,
    *,
    receipt: ChannelRuntimeReceipt | None = None,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> ChannelDispatchDecision:
    return ChannelDispatchDecision(
        status=status,
        requestId=request.request_id,
        requestDigest=request_digest,
        receipt=receipt,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata({**dict(request.metadata), **dict(diagnostics)}),
        authorityFlags=ChannelDispatchAuthorityFlags(),
    )


def _receipt_from_output(
    request: ChannelDispatchRequest,
    request_digest: str,
    output: Mapping[str, object],
) -> ChannelRuntimeReceipt:
    status = output.get("status")
    if status not in {"sent", "queued", "failed", "skipped"}:
        status = "sent"
    provider_message_id = output.get("providerMessageId")
    return ChannelRuntimeReceipt(
        receiptId=f"channel-dispatch:{hashlib.sha1(request_digest.encode('utf-8')).hexdigest()[:16]}",
        requestId=request.request_id,
        channelType=request.channel.type,
        status=status,  # type: ignore[arg-type]
        providerMessageId=str(provider_message_id) if provider_message_id is not None else None,
        chunks=(request.text,) if request.text else (),
        fileRef=request.file_ref,
    )


def _request_digest(request: ChannelDispatchRequest) -> str:
    return provider_digest(_provider_payload(request))


def _provider_payload(request: ChannelDispatchRequest) -> dict[str, object]:
    return {
        "operation": request.operation,
        "requestId": request.request_id,
        "channelType": request.channel.type,
        "channelId": request.channel.channel_id,
        "providerName": request.provider_name,
        "botIdDigest": request.bot_id_digest,
        "userIdDigest": request.user_id_digest,
        "sessionKeyDigest": request.session_key_digest,
        "text": request.text,
        "fileRef": request.file_ref,
    }


def _diagnostics(config: ChannelDispatchConfig, request: ChannelDispatchRequest) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionChannelWritesEnabled": False,
        "routeAttached": False,
        "channelType": request.channel.type,
    }


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS) or _contains_private_text(str(key)):
            continue
        safe_key = _safe_metadata_key(str(key))
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean and clean != "[redacted]":
                safe[safe_key] = clean
        elif isinstance(value, bool | int | float) or value is None:
            safe[safe_key] = value
    return safe


def _safe_metadata_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]", "_", value.strip())[:80]
    if not clean or _contains_private_text(clean):
        return f"metadata:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return clean


def _public_ref(value: str, prefix: str) -> str:
    text = _safe_text(str(value)).strip()
    if text and re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$", text):
        return text
    return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _safe_text(value: str) -> str:
    if _contains_private_text(value):
        return "[redacted]"
    return value[:240]


def _contains_private_text(value: str) -> bool:
    return bool(_SECRET_TEXT_RE.search(value) or _PRIVATE_PATH_RE.search(value))


def maybe_route_to_workflow(
    *,
    eligible: bool,
    confirmed: bool,
    enabled: bool,
) -> WorkflowRouteDecision | None:
    """Pre-execute() seam: return a routing decision when an inbound message
    should become a workflow, else None (normal LLM turn). Default None keeps
    today's behaviour byte-identical."""
    decision = decide_workflow_route(eligible=eligible, confirmed=confirmed, enabled=enabled)
    return decision if decision.routed else None


__all__ = [
    "ChannelDispatchAuthorityFlags",
    "ChannelDispatchConfig",
    "ChannelDispatchDecision",
    "ChannelDispatchProviderPort",
    "ChannelDispatchRequest",
    "ChannelDispatcher",
    "maybe_route_to_workflow",
]
