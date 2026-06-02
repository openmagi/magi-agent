from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from openmagi_core_agent.channels.contract import ChannelRef, ChannelType


ChannelRuntimeOperation = Literal[
    "dispatch.message",
    "typing.start",
    "typing.stop",
    "file.send",
    "file.download",
    "delivery.ack",
]
ChannelRuntimeStatus = Literal["disabled", "intent", "recorded_local_fake", "blocked", "error"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_MAX_TEXT_BY_CHANNEL: dict[ChannelType, int] = {
    "web": 16_000,
    "app": 16_000,
    "telegram": 3500,
    "discord": 1900,
}
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|/workspace(?:/[^,\s\"']*)?|"
    r"/data/bots(?:/[^,\s\"']*)?|/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args|download)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie",
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


class ChannelRuntimeProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def execute(self, request: ChannelRuntimeRequest) -> Mapping[str, object]: ...


class ChannelRuntimeConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_channel_provider_enabled: bool = Field(
        default=False,
        alias="localFakeChannelProviderEnabled",
    )
    production_channel_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelWritesEnabled",
    )
    polling_attached: Literal[False] = Field(default=False, alias="pollingAttached")
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
        data["polling_attached"] = False
        data["route_attached"] = False
        _ = deep
        return type(self).model_validate(data)


class ChannelRuntimeAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    channel_provider_called: Literal[False] = Field(default=False, alias="channelProviderCalled")
    production_channel_write: Literal[False] = Field(default=False, alias="productionChannelWrite")
    polling_attached: Literal[False] = Field(default=False, alias="pollingAttached")
    file_download_performed: Literal[False] = Field(default=False, alias="fileDownloadPerformed")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "channel_provider_called",
        "production_channel_write",
        "polling_attached",
        "file_download_performed",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ChannelRuntimeRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: ChannelRuntimeOperation
    request_id: str = Field(alias="requestId")
    channel: ChannelRef
    text: str | None = None
    file_ref: str | None = Field(default=None, alias="fileRef")
    provider_message_id: str | None = Field(default=None, alias="providerMessageId")
    retry_attempt: int = Field(default=0, alias="retryAttempt", ge=0, le=10)
    metadata: Mapping[str, object] = Field(default_factory=dict)


class ChannelRuntimeReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    receipt_id: str = Field(alias="receiptId")
    request_id: str = Field(alias="requestId")
    channel_type: ChannelType = Field(alias="channelType")
    status: Literal["sent", "queued", "failed", "skipped"]
    provider_message_id: str | None = Field(default=None, alias="providerMessageId")
    chunks: tuple[str, ...] = ()
    file_ref: str | None = Field(default=None, alias="fileRef")

    def public_projection(self) -> dict[str, object]:
        return {
            "receiptId": _public_ref(self.receipt_id, "receipt"),
            "requestId": _public_ref(self.request_id, "request"),
            "channelType": self.channel_type,
            "status": self.status,
            "providerMessageId": (
                None if self.provider_message_id is None else _safe_text(self.provider_message_id)[:120]
            ),
            "chunks": [_safe_text(chunk) for chunk in self.chunks],
            "fileRef": None if self.file_ref is None else _public_ref(self.file_ref, "file"),
        }


class ChannelRuntimeDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ChannelRuntimeStatus
    operation: ChannelRuntimeOperation
    receipt: ChannelRuntimeReceipt | None = None
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: ChannelRuntimeAuthorityFlags = Field(
        default_factory=ChannelRuntimeAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ChannelRuntimeAuthorityFlags()
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
        data["authority_flags"] = ChannelRuntimeAuthorityFlags()
        _ = deep
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "receipt": None if self.receipt is None else self.receipt.public_projection(),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": ChannelRuntimeAuthorityFlags().model_dump(by_alias=True),
        }


class ChannelRuntimeBoundary:
    """Default-off ChannelDispatcher/TypingTicker/file delivery boundary."""

    def __init__(self, config: ChannelRuntimeConfig) -> None:
        self.config = config

    def consume_dispatch_decision(
        self,
        request: ChannelRuntimeRequest,
        dispatch_decision: object,
    ) -> ChannelRuntimeDecision:
        from openmagi_core_agent.channels.dispatcher import ChannelDispatchDecision

        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeChannelProviderEnabled": self.config.local_fake_channel_provider_enabled,
            "productionChannelWritesEnabled": False,
            "pollingAttached": False,
            "routeAttached": False,
            "source": "channel_dispatch_decision",
        }
        if not self.config.enabled:
            return _decision(request, "disabled", ("channel_runtime_disabled",), diagnostics)
        if not isinstance(dispatch_decision, ChannelDispatchDecision):
            return _decision(
                request,
                "blocked",
                ("channel_dispatch_decision_invalid",),
                diagnostics,
            )
        if dispatch_decision.status != "recorded_local_fake" or dispatch_decision.receipt is None:
            return _decision(
                request,
                "intent",
                ("channel_dispatch_receipt_required",),
                diagnostics,
            )
        receipt = dispatch_decision.receipt
        if (
            receipt.request_id != request.request_id
            or receipt.channel_type != request.channel.type
            or request.operation in {"dispatch.message", "file.send"}
            and not receipt.provider_message_id
        ):
            return _decision(
                request,
                "blocked",
                ("channel_dispatch_receipt_mismatch",),
                diagnostics,
                receipt=receipt,
            )
        return _decision(
            request,
            "recorded_local_fake",
            ("channel_dispatch_receipt_consumed",),
            diagnostics,
            receipt=receipt,
        )

    async def execute(
        self,
        request: ChannelRuntimeRequest,
        *,
        provider: ChannelRuntimeProviderPort | None = None,
    ) -> ChannelRuntimeDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeChannelProviderEnabled": self.config.local_fake_channel_provider_enabled,
            "productionChannelWritesEnabled": False,
            "pollingAttached": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        validation_error = _validate_request(request)
        if not self.config.enabled:
            return _decision(request, "disabled", ("channel_runtime_disabled",), diagnostics)
        if validation_error is not None:
            return _decision(request, "blocked", (validation_error,), diagnostics)
        if not self.config.local_fake_channel_provider_enabled or provider is None:
            return _decision(request, "intent", ("local_channel_provider_disabled",), diagnostics)
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return _decision(request, "blocked", ("local_fake_channel_provider_untrusted",), diagnostics)
        try:
            raw = provider.execute(request)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception as exc:
            return _decision(
                request,
                "error",
                ("local_fake_channel_provider_error",),
                {**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        receipt = _receipt_from_raw(request, raw)
        if receipt.status in {"failed", "skipped"}:
            return _decision(
                request,
                "blocked",
                ("channel_provider_ack_failed",),
                diagnostics,
                receipt=receipt,
            )
        if request.operation in {"dispatch.message", "file.send"} and not receipt.provider_message_id:
            return _decision(
                request,
                "blocked",
                ("channel_provider_ack_missing",),
                diagnostics,
                receipt=receipt,
            )
        return _decision(
            request,
            "recorded_local_fake",
            (f"{request.operation.replace('.', '_')}_local_fake_receipt_only",),
            diagnostics,
            receipt=receipt,
        )


def _decision(
    request: ChannelRuntimeRequest,
    status: ChannelRuntimeStatus,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    receipt: ChannelRuntimeReceipt | None = None,
) -> ChannelRuntimeDecision:
    return ChannelRuntimeDecision(
        status=status,
        operation=request.operation,
        receipt=receipt,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=ChannelRuntimeAuthorityFlags(),
    )


def _validate_request(request: ChannelRuntimeRequest) -> str | None:
    if request.operation == "dispatch.message" and request.text is None:
        return "message_text_required"
    if request.operation in {"file.send", "file.download"}:
        if not request.file_ref:
            return "file_ref_required"
        if _looks_like_raw_path(request.file_ref):
            return "raw_file_ref_blocked"
    if request.operation == "delivery.ack" and not request.provider_message_id:
        return "provider_message_id_required"
    if _contains_private_payload(request.text or ""):
        return "private_channel_payload_blocked"
    return None


def _receipt_from_raw(
    request: ChannelRuntimeRequest,
    raw: Mapping[str, object],
) -> ChannelRuntimeReceipt:
    status = raw.get("status")
    if status not in {"sent", "queued", "failed", "skipped"}:
        status = "sent"
    chunks = _chunks_for_request(request)
    return ChannelRuntimeReceipt(
        receiptId=str(raw.get("receiptId") or _receipt_id(request)),
        requestId=request.request_id,
        channelType=request.channel.type,
        status=status,  # type: ignore[arg-type]
        providerMessageId=(
            str(raw.get("providerMessageId"))
            if raw.get("providerMessageId") is not None
            else request.provider_message_id
        ),
        chunks=chunks,
        fileRef=request.file_ref,
    )


def _chunks_for_request(request: ChannelRuntimeRequest) -> tuple[str, ...]:
    if request.text is None:
        return ()
    text = _safe_text(request.text)
    limit = _MAX_TEXT_BY_CHANNEL[request.channel.type]
    if text == "":
        return ("",)
    return tuple(text[index : index + limit] for index in range(0, len(text), limit))


def _receipt_id(request: ChannelRuntimeRequest) -> str:
    seed = f"{request.operation}:{request.request_id}:{request.channel.type}:{request.channel.channel_id}:{request.retry_attempt}"
    return f"channel-receipt:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        raw_key = str(key)
        normalized_key = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
            continue
        if _contains_private_payload(raw_key) or _looks_like_raw_path(raw_key):
            continue
        clean_key = _safe_text(raw_key)
        if not clean_key or clean_key != raw_key.strip():
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[raw_key] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[raw_key] = value
    return safe


def _public_ref(value: str, prefix: str) -> str:
    clean = _safe_text(str(value))
    if _REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _safe_text(value: str) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None and not _PRIVATE_PATH_RE.search(line)
    ]
    clean = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


def _contains_private_payload(value: str) -> bool:
    return bool(_RAW_PRIVATE_LINE_RE.search(value) or _PRIVATE_PATH_RE.search(value) or _SECRET_TEXT_RE.search(value))


def _looks_like_raw_path(value: str) -> bool:
    return value.startswith(("/", "~")) or "\\" in value or ".." in value.split("/")


def _safe_provider_error(exc: BaseException) -> str:
    return _safe_text(str(exc))[:240] or "[redacted-provider-error]"


__all__ = [
    "ChannelRuntimeBoundary",
    "ChannelRuntimeConfig",
    "ChannelRuntimeDecision",
    "ChannelRuntimeReceipt",
    "ChannelRuntimeRequest",
]
