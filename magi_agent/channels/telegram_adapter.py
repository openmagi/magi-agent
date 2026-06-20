from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import ipaddress
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.channels.contract import ChannelRef, ChannelType
from magi_agent.channels.runtime_boundary import ChannelRuntimeReceipt
from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.runtime.provider_execution import (
    ProviderExecutionBoundary,
    ProviderExecutionConfig,
    ProviderExecutionRequest,
    ProviderExecutionScope,
)
from magi_agent.runtime.provider_receipts import provider_digest


TelegramAdapterStatus = Literal[
    "disabled",
    "blocked",
    "poll_intent",
    "inbound_projected_local_fake",
    "send_intent",
    "sent_local_fake",
    "typing_recorded_local_fake",
    "download_intent",
    "download_recorded_local_fake",
    "webhook_mitigation_intent",
    "provider_error_swallowed",
]
TelegramSendOperation = Literal["send_message", "send_document", "send_photo", "send_typing"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_TELEGRAM_CHUNK_LIMIT = 3500
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{6,}|gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{6,}|"
    r"(?:\b|bot)\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"(?:authorization|cookie|set-cookie|password|token|secret|credential|api[_-]?key)"
    r"\s*[:=]\s*[^,\s}{\n]{3,})",
    re.IGNORECASE,
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args|child)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_RE = re.compile(
    r"[?&](?:X-Amz-Signature|access[_-]?token|api[_-]?key|auth|authorization|"
    r"cookie|credential|key|password|private[_-]?key|secret|session|sig|"
    r"signature|token)=",
    re.IGNORECASE,
)
_PRIVATE_OBJECT_HOST_RE = re.compile(
    r"(?:"
    r"(?:^|[.])(?:storage\.googleapis\.com|storage\.cloud\.google\.com)$|"
    r"(?:^|[.])supabase\.co$|"
    r"(?:^|[.])r2\.cloudflarestorage\.com$|"
    r"(?:^|[.])blob\.core\.windows\.net$|"
    r"(?:^|[.])s3[.-][A-Za-z0-9-]+\.amazonaws\.com$|"
    r"(?:^|[.])amazonaws\.com$"
    r")",
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
    "production",
    "raw",
    "route",
    "secret",
    "token",
    "attached",
    "enabled",
    "allowed",
    "performed",
    "authority",
    "called",
    "fetched",
    "executed",
    "injected",
    "network",
    "trust",
    "trusted",
    "verified",
    "valid",
)
_ALLOWED_DOWNLOAD_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/json",
        "text/csv",
        "text/markdown",
        "text/plain",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)


class TelegramProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def poll_updates(self, request: TelegramPollRequest) -> Sequence[Mapping[str, Any]]: ...

    def send_message(self, request: TelegramProviderSendRequest) -> Mapping[str, object]: ...

    def send_document(self, request: TelegramProviderSendRequest) -> Mapping[str, object]: ...

    def send_photo(self, request: TelegramProviderSendRequest) -> Mapping[str, object]: ...

    def send_typing(self, request: TelegramProviderSendRequest) -> Mapping[str, object]: ...

    def download_file(self, request: TelegramDownloadRequest) -> Mapping[str, object]: ...


class TelegramAdapterConfig(FalseOnlyAuthorityModel):
    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    selected_channel_routes: tuple[ChannelType, ...] = Field(default=(), alias="selectedChannelRoutes")
    provider_allowlist: tuple[str, ...] = Field(default=(), alias="providerAllowlist")
    download_enabled: bool = Field(default=False, alias="downloadEnabled")
    production_channel_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelWriteEnabled",
    )
    telegram_polling_attached: Literal[False] = Field(default=False, alias="telegramPollingAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    telegram_webhook_mitigation_attached: Literal[False] = Field(
        default=False,
        alias="telegramWebhookMitigationAttached",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

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


class TelegramAdapterAuthorityFlags(FalseOnlyAuthorityModel):
    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    telegram_polling_attached: Literal[False] = Field(default=False, alias="telegramPollingAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    channel_delivery_performed: Literal[False] = Field(default=False, alias="channelDeliveryPerformed")
    production_channel_write: Literal[False] = Field(default=False, alias="productionChannelWrite")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    webhook_deleted: Literal[False] = Field(default=False, alias="webhookDeleted")
    download_performed: Literal[False] = Field(default=False, alias="downloadPerformed")


class _TelegramScopedRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    provider_name: str = Field(alias="providerName")
    bot_id_digest: str = Field(alias="botIdDigest")
    owner_id_digest: str = Field(alias="ownerIdDigest")
    session_key_digest: str = Field(alias="sessionKeyDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)


class TelegramPollRequest(_TelegramScopedRequest):
    offset: int = Field(default=0, ge=0)


class TelegramWebhookMitigationRequest(_TelegramScopedRequest):
    """Scoped request for default-off Telegram webhook mitigation."""


class TelegramSendRequest(_TelegramScopedRequest):
    operation: TelegramSendOperation
    channel: ChannelRef
    chat_id: str = Field(alias="chatId")
    text: str | None = None
    reply_to_message_id: str | None = Field(default=None, alias="replyToMessageId")
    file_ref: str | None = Field(default=None, alias="fileRef")
    artifact_receipt_ref: str | None = Field(default=None, alias="artifactReceiptRef")


class TelegramProviderSendRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: TelegramSendOperation
    request_id: str = Field(alias="requestId")
    chat_id: str = Field(alias="chatId")
    text: str | None = None
    reply_to_message_id: str | None = Field(default=None, alias="replyToMessageId")
    file_ref: str | None = Field(default=None, alias="fileRef")
    artifact_receipt_ref: str | None = Field(default=None, alias="artifactReceiptRef")
    chunk_index: int = Field(default=1, alias="chunkIndex", ge=1)
    chunk_count: int = Field(default=1, alias="chunkCount", ge=1)


class TelegramDownloadRequest(_TelegramScopedRequest):
    file_id: str = Field(alias="fileId")
    file_name: str = Field(alias="fileName")
    mime_type: str = Field(alias="mimeType")
    file_url: str = Field(alias="fileUrl")


class TelegramReplyRef(BaseModel):
    model_config = _MODEL_CONFIG

    message_id: str = Field(alias="messageId")
    preview: str
    role: Literal["user"] = "user"

    def public_projection(self) -> dict[str, object]:
        return {
            "messageId": _safe_text(self.message_id)[:120],
            "preview": _safe_text(self.preview)[:160],
            "role": "user",
        }


class TelegramAttachmentRef(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["file"] = "file"
    file_ref: str = Field(alias="fileRef")
    filename: str
    mime_type: str | None = Field(default=None, alias="mimeType")
    size_bytes: int | None = Field(default=None, alias="sizeBytes", ge=0)

    def public_projection(self) -> dict[str, object]:
        return {
            "kind": "file",
            "fileRef": _public_ref(self.file_ref, "telegram-file"),
            "filename": _safe_filename(self.filename),
            "mimeType": None if self.mime_type is None else _safe_text(self.mime_type)[:120],
            "sizeBytes": self.size_bytes,
        }


class TelegramInboundUpdate(BaseModel):
    model_config = _MODEL_CONFIG

    channel: Literal["telegram"] = "telegram"
    chat_id: str = Field(alias="chatId")
    user_id: str = Field(alias="userId")
    text: str = ""
    message_id: str = Field(alias="messageId")
    reply_to: TelegramReplyRef | None = Field(default=None, alias="replyTo")
    attachment_refs: tuple[TelegramAttachmentRef, ...] = Field(default=(), alias="attachmentRefs")
    raw_update_ref: str = Field(alias="rawUpdateRef")

    def public_projection(self) -> dict[str, object]:
        return {
            "channel": "telegram",
            "chatId": _safe_text(self.chat_id)[:120],
            "userId": _safe_text(self.user_id)[:120],
            "text": _safe_text(self.text),
            "messageId": _safe_text(self.message_id)[:120],
            "replyTo": None if self.reply_to is None else self.reply_to.public_projection(),
            "attachmentRefs": [ref.public_projection() for ref in self.attachment_refs],
            "rawUpdateRef": _public_ref(self.raw_update_ref, "telegram-update"),
        }


class TelegramDeliveryReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    chat_id: str = Field(alias="chatId")
    provider_message_id: str | None = Field(default=None, alias="providerMessageId")
    chunk_index: int = Field(default=1, alias="chunkIndex", ge=1)
    chunk_count: int = Field(default=1, alias="chunkCount", ge=1)
    file_ref: str | None = Field(default=None, alias="fileRef")

    def public_projection(self) -> dict[str, object]:
        return {
            "chatId": _safe_text(self.chat_id)[:120],
            "providerMessageId": None if self.provider_message_id is None else _safe_text(self.provider_message_id)[:120],
            "chunkIndex": self.chunk_index,
            "chunkCount": self.chunk_count,
            "fileRef": None if self.file_ref is None else _public_ref(self.file_ref, "file"),
        }


class TelegramAdapterDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: TelegramAdapterStatus
    operation: str
    request_digest: str = Field(alias="requestDigest")
    inbound_updates: tuple[TelegramInboundUpdate, ...] = Field(default=(), alias="inboundUpdates")
    delivery_receipts: tuple[TelegramDeliveryReceipt, ...] = Field(default=(), alias="deliveryReceipts")
    offset_receipt_ref: str | None = Field(default=None, alias="offsetReceiptRef")
    next_offset: int | None = Field(default=None, alias="nextOffset")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: TelegramAdapterAuthorityFlags = Field(
        default_factory=TelegramAdapterAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = TelegramAdapterAuthorityFlags()
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
        data["authority_flags"] = TelegramAdapterAuthorityFlags()
        _ = deep
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "requestDigest": self.request_digest,
            "inboundUpdates": [update.public_projection() for update in self.inbound_updates],
            "deliveryReceipts": [receipt.public_projection() for receipt in self.delivery_receipts],
            "offsetReceiptRef": None if self.offset_receipt_ref is None else _public_ref(self.offset_receipt_ref, "telegram-offset"),
            "nextOffset": self.next_offset,
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": TelegramAdapterAuthorityFlags().model_dump(by_alias=True),
        }


class TelegramAdapterBoundary:
    """Default-off Telegram live adapter boundary with injected fake providers only."""

    def __init__(self, config: TelegramAdapterConfig) -> None:
        self.config = config

    def poll_updates(
        self,
        request: TelegramPollRequest,
        *,
        provider: TelegramProviderPort | None = None,
    ) -> TelegramAdapterDecision:
        request_digest = provider_digest(_scope_payload(request) | {"operation": "poll_updates", "offset": request.offset})
        diagnostics = _diagnostics(self.config, request, "poll_updates")
        gate_error = self._gate_error(request, provider)
        if gate_error is not None:
            return _blocked_or_disabled(
                "poll_updates",
                request_digest,
                gate_error,
                diagnostics,
                disabled_status="poll_intent",
            )
        captured = _CapturingTelegramProvider(provider, "poll_updates", request)
        execution = _run_provider_execution(self._execute_provider(request, "telegram.poll_updates", captured))
        if execution.status == "error":
            return _decision(
                "provider_error_swallowed",
                "poll_updates",
                request_digest,
                ("telegram_poll_error_swallowed",),
                diagnostics | {"providerError": "telegram_poll_error_swallowed"},
            )
        updates = tuple(_project_update(update) for update in captured.output_sequence)
        projected = tuple(update for update in updates if update is not None)
        next_offset = _next_offset(request.offset, captured.output_sequence)
        offset_receipt_ref = _offset_receipt_ref(request, next_offset)
        return _decision(
            "inbound_projected_local_fake",
            "poll_updates",
            request_digest,
            ("local_fake_poll_projection_only",),
            diagnostics,
            inbound_updates=projected,
            next_offset=next_offset,
            offset_receipt_ref=offset_receipt_ref,
        )

    def send(
        self,
        request: TelegramSendRequest,
        *,
        provider: TelegramProviderPort | None = None,
    ) -> TelegramAdapterDecision:
        request_digest = provider_digest(_scope_payload(request) | _send_payload(request))
        diagnostics = _diagnostics(self.config, request, request.operation)
        if not self.config.enabled:
            return _decision(
                "disabled",
                request.operation,
                request_digest,
                ("telegram_adapter_disabled",),
                diagnostics,
            )
        channel_error = _telegram_channel_error(self.config, request)
        if channel_error is not None:
            return _decision("blocked", request.operation, request_digest, (channel_error,), diagnostics)
        gate_error = self._gate_error(request, provider)
        if gate_error is not None:
            return _blocked_or_disabled(
                request.operation,
                request_digest,
                gate_error,
                diagnostics,
                disabled_status="send_intent",
            )
        if request.operation == "send_typing":
            return self._send_typing(request, provider, request_digest, diagnostics)
        if request.operation == "send_message":
            return self._send_message(request, provider, request_digest, diagnostics)
        return self._send_file(request, provider, request_digest, diagnostics)

    def download_file(
        self,
        request: TelegramDownloadRequest,
        *,
        provider: TelegramProviderPort | None = None,
    ) -> TelegramAdapterDecision:
        request_digest = provider_digest(_scope_payload(request) | {"operation": "download_file", "fileId": request.file_id})
        diagnostics = _diagnostics(self.config, request, "download_file")
        download_error = _download_validation_error(request)
        if download_error is not None:
            return _decision("blocked", "download_file", request_digest, (download_error,), diagnostics)
        gate_error = self._gate_error(request, provider)
        if gate_error is not None:
            return _blocked_or_disabled(
                "download_file",
                request_digest,
                gate_error,
                diagnostics,
                disabled_status="download_intent",
            )
        if not self.config.download_enabled:
            return _decision(
                "download_intent",
                "download_file",
                request_digest,
                ("telegram_download_intent_only",),
                diagnostics,
            )
        captured = _CapturingTelegramProvider(provider, "download_file", request)
        execution = _run_provider_execution(self._execute_provider(request, "telegram.download_file", captured))
        if execution.status != "ok":
            return _decision(
                "provider_error_swallowed",
                "download_file",
                request_digest,
                ("telegram_download_error_swallowed",),
                diagnostics | {"providerError": "telegram_download_error_swallowed"},
            )
        return _decision(
            "download_recorded_local_fake",
            "download_file",
            request_digest,
            ("local_fake_download_receipt_only",),
            diagnostics,
        )

    def mitigate_stale_webhook(
        self,
        request: TelegramWebhookMitigationRequest,
        *,
        provider: TelegramProviderPort | None = None,
    ) -> TelegramAdapterDecision:
        _ = provider
        request_digest = provider_digest(_scope_payload(request) | {"operation": "mitigate_stale_webhook"})
        diagnostics = _diagnostics(self.config, request, "mitigate_stale_webhook")
        if not self.config.enabled:
            return _decision(
                "disabled",
                "mitigate_stale_webhook",
                request_digest,
                ("telegram_adapter_disabled",),
                diagnostics,
            )
        return _decision(
            "webhook_mitigation_intent",
            "mitigate_stale_webhook",
            request_digest,
            ("telegram_stale_webhook_mitigation_intent",),
            diagnostics,
        )

    def _send_message(
        self,
        request: TelegramSendRequest,
        provider: TelegramProviderPort | None,
        request_digest: str,
        diagnostics: Mapping[str, object],
    ) -> TelegramAdapterDecision:
        if _contains_private_text(request.text or ""):
            return _decision(
                "blocked",
                "send_message",
                request_digest,
                ("private_outbound_text_blocked",),
                diagnostics,
            )
        chunks = _chunk_text(request.text or "")
        receipts: list[TelegramDeliveryReceipt] = []
        for index, chunk in enumerate(chunks, start=1):
            provider_request = TelegramProviderSendRequest(
                operation="send_message",
                requestId=request.request_id,
                chatId=request.chat_id,
                text=chunk,
                replyToMessageId=request.reply_to_message_id if index == 1 else None,
                chunkIndex=index,
                chunkCount=len(chunks),
            )
            captured = _CapturingTelegramProvider(provider, "send_message", provider_request)
            execution = _run_provider_execution(self._execute_provider(request, "telegram.send_message", captured))
            if execution.status != "ok" or captured.output_mapping is None:
                return _decision(
                    "provider_error_swallowed",
                    "send_message",
                    request_digest,
                    ("telegram_send_error_swallowed",),
                    diagnostics | {"providerError": "telegram_send_error_swallowed"},
                )
            provider_message_id = _safe_provider_message_id(captured.output_mapping)
            if provider_message_id is None:
                return _decision(
                    "blocked",
                    "send_message",
                    request_digest,
                    ("provider_message_ack_required",),
                    diagnostics,
                )
            receipts.append(
                TelegramDeliveryReceipt(
                    chatId=request.chat_id,
                    providerMessageId=provider_message_id,
                    chunkIndex=index,
                    chunkCount=len(chunks),
                )
            )
        return _decision(
            "sent_local_fake",
            "send_message",
            request_digest,
            ("local_fake_telegram_send_receipt_only",),
            diagnostics,
            delivery_receipts=tuple(receipts),
        )

    def _send_typing(
        self,
        request: TelegramSendRequest,
        provider: TelegramProviderPort | None,
        request_digest: str,
        diagnostics: Mapping[str, object],
    ) -> TelegramAdapterDecision:
        provider_request = TelegramProviderSendRequest(
            operation="send_typing",
            requestId=request.request_id,
            chatId=request.chat_id,
        )
        captured = _CapturingTelegramProvider(provider, "send_typing", provider_request)
        execution = _run_provider_execution(self._execute_provider(request, "telegram.send_typing", captured))
        if execution.status != "ok":
            return _decision(
                "provider_error_swallowed",
                "send_typing",
                request_digest,
                ("telegram_typing_error_swallowed",),
                diagnostics | {"providerError": "telegram_typing_error_swallowed"},
            )
        return _decision(
            "typing_recorded_local_fake",
            "send_typing",
            request_digest,
            ("local_fake_typing_receipt_only",),
            diagnostics,
        )

    def _send_file(
        self,
        request: TelegramSendRequest,
        provider: TelegramProviderPort | None,
        request_digest: str,
        diagnostics: Mapping[str, object],
    ) -> TelegramAdapterDecision:
        if not request.file_ref or _looks_like_raw_path(request.file_ref):
            return _decision(
                "blocked",
                request.operation,
                request_digest,
                ("raw_path_file_delivery_blocked",),
                diagnostics,
            )
        if not request.artifact_receipt_ref:
            return _decision(
                "blocked",
                request.operation,
                request_digest,
                ("artifact_receipt_required",),
                diagnostics,
            )
        if _contains_private_text(request.text or ""):
            return _decision(
                "blocked",
                request.operation,
                request_digest,
                ("private_outbound_text_blocked",),
                diagnostics,
            )
        provider_request = TelegramProviderSendRequest(
            operation=request.operation,
            requestId=request.request_id,
            chatId=request.chat_id,
            text=request.text,
            fileRef=_safe_ref(request.file_ref),
            artifactReceiptRef=_public_ref(request.artifact_receipt_ref, "artifact-receipt"),
        )
        provider_method = "send_document" if request.operation == "send_document" else "send_photo"
        captured = _CapturingTelegramProvider(provider, provider_method, provider_request)
        execution = _run_provider_execution(self._execute_provider(request, f"telegram.{provider_method}", captured))
        if execution.status != "ok" or captured.output_mapping is None:
            return _decision(
                "provider_error_swallowed",
                request.operation,
                request_digest,
                (f"telegram_{request.operation.removeprefix('send_')}_error_swallowed",),
                diagnostics | {"providerError": "telegram_file_send_error_swallowed"},
            )
        provider_message_id = _safe_provider_message_id(captured.output_mapping)
        if provider_message_id is None:
            return _decision(
                "blocked",
                request.operation,
                request_digest,
                ("provider_message_ack_required",),
                diagnostics,
            )
        return _decision(
            "sent_local_fake",
            request.operation,
            request_digest,
            (f"local_fake_telegram_{request.operation.removeprefix('send_')}_receipt_only",),
            diagnostics,
            delivery_receipts=(
                TelegramDeliveryReceipt(
                    chatId=request.chat_id,
                    providerMessageId=provider_message_id,
                    fileRef=request.file_ref,
                ),
            ),
        )

    def _gate_error(self, request: _TelegramScopedRequest, provider: TelegramProviderPort | None) -> str | None:
        if not self.config.enabled:
            return "telegram_adapter_disabled"
        if "telegram" not in set(self.config.selected_channel_routes):
            return "channel_route_not_selected"
        if not request.bot_id_digest.strip():
            return "bot_id_digest_required"
        if not request.owner_id_digest.strip():
            return "owner_id_digest_required"
        if not request.session_key_digest.strip():
            return "session_key_digest_required"
        if not self.config.provider_allowlist or request.provider_name not in self.config.provider_allowlist:
            return "provider_not_allowlisted"
        if not self.config.local_fake_provider_enabled or provider is None:
            return "local_fake_telegram_provider_disabled"
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return "local_fake_telegram_provider_untrusted"
        return None

    def _execute_provider(
        self,
        request: _TelegramScopedRequest,
        operation: str,
        provider: _CapturingTelegramProvider,
    ) -> object:
        return ProviderExecutionBoundary(
            ProviderExecutionConfig(
                enabled=True,
                localFakeProviderEnabled=self.config.local_fake_provider_enabled,
            )
        ).execute(
            ProviderExecutionRequest(
                providerName=request.provider_name,
                operation=operation,
                payload=_scope_payload(request) | {"telegramOperation": operation},
                scope=ProviderExecutionScope(
                    environment="local-test",
                    botIdDigest=request.bot_id_digest,
                    ownerIdDigest=request.owner_id_digest,
                    selectedScope=True,
                    sessionIdDigest=request.session_key_digest,
                ),
            ),
            provider=provider,
        )


class _CapturingTelegramProvider:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        provider: TelegramProviderPort | None,
        operation: str,
        adapter_request: object,
    ) -> None:
        self.provider = provider
        self.operation = operation
        self.adapter_request = adapter_request
        self.output_mapping: Mapping[str, object] | None = None
        self.output_sequence: Sequence[Mapping[str, Any]] = ()

    def execute(self, _request: object) -> Mapping[str, object]:
        if self.provider is None:
            raise RuntimeError("telegram provider missing")
        if self.operation == "poll_updates":
            value = self.provider.poll_updates(self.adapter_request)  # type: ignore[arg-type]
            self.output_sequence = tuple(value)
            return {"status": "ok", "updateCount": len(self.output_sequence)}
        if self.operation == "send_message":
            value = self.provider.send_message(self.adapter_request)  # type: ignore[arg-type]
        elif self.operation == "send_document":
            value = self.provider.send_document(self.adapter_request)  # type: ignore[arg-type]
        elif self.operation == "send_photo":
            value = self.provider.send_photo(self.adapter_request)  # type: ignore[arg-type]
        elif self.operation == "send_typing":
            value = self.provider.send_typing(self.adapter_request)  # type: ignore[arg-type]
        elif self.operation == "download_file":
            value = self.provider.download_file(self.adapter_request)  # type: ignore[arg-type]
        else:
            raise RuntimeError("unknown telegram provider operation")
        self.output_mapping = value
        return value


class TelegramChannelDispatchProviderAdapter:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *,
        boundary: TelegramAdapterBoundary,
        telegram_provider: TelegramProviderPort,
    ) -> None:
        self.boundary = boundary
        self.telegram_provider = telegram_provider

    def execute(self, request: object) -> Mapping[str, object]:
        from magi_agent.channels.dispatcher import ChannelDispatchRequest

        if not isinstance(request, ChannelDispatchRequest) or request.channel.type != "telegram":
            return {"status": "failed", "providerMessageId": None}
        operation: TelegramSendOperation
        artifact_receipt_ref = None
        if request.operation == "dispatch.message":
            operation = "send_message"
        elif request.operation == "typing.start":
            operation = "send_typing"
        elif request.operation == "file.send":
            operation = "send_document"
            artifact_receipt_ref = _metadata_string(request.metadata, "artifactReceiptRef")
        else:
            return {"status": "skipped", "providerMessageId": None}
        decision = self.boundary.send(
            TelegramSendRequest(
                operation=operation,
                requestId=request.request_id,
                channel=request.channel,
                providerName=request.provider_name,
                botIdDigest=request.bot_id_digest,
                ownerIdDigest=request.user_id_digest,
                sessionKeyDigest=request.session_key_digest,
                chatId=request.channel.channel_id,
                text=request.text,
                fileRef=request.file_ref,
                artifactReceiptRef=artifact_receipt_ref,
            ),
            provider=self.telegram_provider,
        )
        if decision.status not in {"sent_local_fake", "typing_recorded_local_fake"}:
            return {"status": "failed", "providerMessageId": None}
        provider_message_id = (
            None
            if not decision.delivery_receipts
            else decision.delivery_receipts[0].provider_message_id
        )
        return {
            "status": "sent",
            "providerMessageId": provider_message_id,
        }


def _run_provider_execution(coro: object) -> object:
    try:
        return coro.send(None)  # type: ignore[attr-defined]
    except StopIteration as exc:
        return exc.value


def _blocked_or_disabled(
    operation: str,
    request_digest: str,
    gate_error: str,
    diagnostics: Mapping[str, object],
    *,
    disabled_status: TelegramAdapterStatus,
) -> TelegramAdapterDecision:
    if gate_error == "telegram_adapter_disabled":
        return _decision("disabled", operation, request_digest, ("telegram_adapter_disabled",), diagnostics)
    if gate_error == "local_fake_telegram_provider_disabled":
        return _decision(disabled_status, operation, request_digest, (gate_error,), diagnostics)
    return _decision("blocked", operation, request_digest, (gate_error,), diagnostics)


def _decision(
    status: TelegramAdapterStatus,
    operation: str,
    request_digest: str,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    inbound_updates: tuple[TelegramInboundUpdate, ...] = (),
    delivery_receipts: tuple[TelegramDeliveryReceipt, ...] = (),
    offset_receipt_ref: str | None = None,
    next_offset: int | None = None,
) -> TelegramAdapterDecision:
    return TelegramAdapterDecision(
        status=status,
        operation=operation,
        requestDigest=request_digest,
        inboundUpdates=inbound_updates,
        deliveryReceipts=delivery_receipts,
        offsetReceiptRef=offset_receipt_ref,
        nextOffset=next_offset,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=TelegramAdapterAuthorityFlags(),
    )


def _telegram_channel_error(config: TelegramAdapterConfig, request: TelegramSendRequest) -> str | None:
    if request.channel.type != "telegram":
        return "telegram_channel_required"
    if request.channel.type not in set(config.selected_channel_routes):
        return "channel_route_not_selected"
    return None


def _download_validation_error(request: TelegramDownloadRequest) -> str | None:
    if _filename_has_traversal(request.file_name):
        return "download_path_traversal_blocked"
    if _url_is_token_bearing(request.file_url):
        return "download_token_url_blocked"
    if _url_is_private(request.file_url):
        return "download_private_url_blocked"
    if request.mime_type not in _ALLOWED_DOWNLOAD_MIME_TYPES:
        return "download_mime_not_allowed"
    return None


def _project_update(update: Mapping[str, Any]) -> TelegramInboundUpdate | None:
    message = update.get("message")
    if not isinstance(message, Mapping):
        return None
    text = message.get("text")
    caption = message.get("caption")
    attachment = _attachment_from_message(message)
    if not isinstance(text, str) and not isinstance(caption, str) and attachment is None:
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, Mapping):
        return None
    chat_id = _coerce_id(chat.get("id"))
    user_id = _coerce_id(sender.get("id")) if isinstance(sender, Mapping) else chat_id
    message_id = _coerce_id(message.get("message_id"))
    if not chat_id or not user_id or not message_id:
        return None
    return TelegramInboundUpdate(
        chatId=chat_id,
        userId=user_id,
        text=_safe_text(text if isinstance(text, str) else caption if isinstance(caption, str) else ""),
        messageId=message_id,
        replyTo=_reply_from_message(message.get("reply_to_message")),
        attachmentRefs=() if attachment is None else (attachment,),
        rawUpdateRef=_update_ref(update),
    )


def _attachment_from_message(message: Mapping[str, Any]) -> TelegramAttachmentRef | None:
    document = message.get("document")
    if isinstance(document, Mapping):
        return _attachment_from_media(document, default_filename="document", default_mime_type="application/octet-stream")
    for field_name, filename, mime_type in (
        ("photo", "photo.jpg", "image/jpeg"),
        ("audio", "audio", "audio/mpeg"),
        ("voice", "voice.ogg", "audio/ogg"),
        ("video", "video.mp4", "video/mp4"),
    ):
        value = message.get(field_name)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            candidates = [item for item in value if isinstance(item, Mapping)]
            if candidates:
                return _attachment_from_media(candidates[-1], default_filename=filename, default_mime_type=mime_type)
        if isinstance(value, Mapping):
            return _attachment_from_media(value, default_filename=filename, default_mime_type=mime_type)
    return None


def _attachment_from_media(
    media: Mapping[str, Any],
    *,
    default_filename: str,
    default_mime_type: str,
) -> TelegramAttachmentRef | None:
    file_id = media.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        return None
    filename = media.get("file_name") if isinstance(media.get("file_name"), str) else default_filename
    mime_type = media.get("mime_type") if isinstance(media.get("mime_type"), str) else default_mime_type
    return TelegramAttachmentRef(
        fileRef=f"telegram-file:{_short_digest(file_id)}",
        filename=str(filename),
        mimeType=mime_type,
        sizeBytes=media.get("file_size") if isinstance(media.get("file_size"), int) else None,
    )


def _reply_from_message(value: object) -> TelegramReplyRef | None:
    if not isinstance(value, Mapping):
        return None
    preview = value.get("text")
    if not isinstance(preview, str):
        preview = value.get("caption")
    if not isinstance(preview, str) or not preview.strip():
        return None
    message_id = _coerce_id(value.get("message_id"))
    if not message_id:
        return None
    return TelegramReplyRef(messageId=message_id, preview=preview)


def _next_offset(current_offset: int, updates: Sequence[Mapping[str, Any]]) -> int:
    max_update_id = current_offset - 1
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)
    return max_update_id + 1


def _offset_receipt_ref(request: TelegramPollRequest, next_offset: int) -> str:
    return f"telegram-offset:{_short_digest(provider_digest(_scope_payload(request) | {'nextOffset': next_offset}))}"


def _update_ref(update: Mapping[str, Any]) -> str:
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        return f"telegram-update:{update_id}"
    return f"telegram-update:{_short_digest(repr(update_id))}"


def _chunk_text(text: str) -> tuple[str, ...]:
    if text == "":
        return ("",)
    chunks: list[str] = []
    remaining = text
    while len(remaining) > _TELEGRAM_CHUNK_LIMIT:
        hard_slice = remaining[:_TELEGRAM_CHUNK_LIMIT]
        cut = _readable_cut(hard_slice) or len(hard_slice)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    chunks.append(remaining)
    return tuple(chunks)


def _readable_cut(text: str) -> int | None:
    min_cut = _TELEGRAM_CHUNK_LIMIT // 2
    for separator in ("\n\n", "\n", ". ", " "):
        index = text.rfind(separator)
        if index >= min_cut:
            return index + len(separator)
    return None


def _scope_payload(request: _TelegramScopedRequest) -> dict[str, object]:
    return {
        "requestId": request.request_id,
        "providerName": request.provider_name,
        "botIdDigest": request.bot_id_digest,
        "ownerIdDigest": request.owner_id_digest,
        "sessionKeyDigest": request.session_key_digest,
    }


def _send_payload(request: TelegramSendRequest) -> dict[str, object]:
    return {
        "operation": request.operation,
        "channelType": request.channel.type,
        "channelId": request.channel.channel_id,
        "chatId": request.chat_id,
        "text": request.text,
        "replyToMessageId": request.reply_to_message_id,
        "fileRef": request.file_ref,
        "artifactReceiptRef": request.artifact_receipt_ref,
    }


def _diagnostics(
    config: TelegramAdapterConfig,
    request: _TelegramScopedRequest,
    operation: str,
) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionChannelWriteEnabled": False,
        "telegramPollingAttached": False,
        "telegramAttached": False,
        "telegramWebhookMitigationAttached": False,
        "routeAttached": False,
        "operation": operation,
        **dict(request.metadata),
    }


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        raw_key = str(key)
        normalized_key = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS) or _contains_private_text(raw_key):
            continue
        safe_key = _safe_metadata_key(raw_key)
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
        return f"metadata:{_short_digest(value)}"
    return clean


def _coerce_id(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return _safe_text(value.strip())[:120]
    return ""


def _safe_provider_message_id(output: Mapping[str, object]) -> str | None:
    value = output.get("providerMessageId")
    if value is None:
        return None
    clean = _safe_text(str(value))
    return clean[:120] if clean else None


def _safe_ref(value: str) -> str:
    clean = _safe_text(value.strip())
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("Telegram adapter refs must be public identifiers")
    return clean


def _public_ref(value: str, prefix: str) -> str:
    clean = _safe_text(str(value)).strip()
    if clean and _REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{_short_digest(str(value))}"


def _safe_filename(value: str) -> str:
    clean = _safe_text(value).replace("/", "-").replace("\\", "-").strip()
    if not clean or clean in {".", ".."}:
        return "telegram-file"
    return clean[:160]


def _metadata_string(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def _contains_private_text(value: str) -> bool:
    return bool(_SECRET_TEXT_RE.search(value) or _PRIVATE_TEXT_RE.search(value))


def _safe_text(value: str) -> str:
    if _contains_private_text(value):
        return "[redacted]"
    return value[:4096]


def _looks_like_raw_path(value: str) -> bool:
    return value.startswith(("/", "~")) or "\\" in value or ".." in value.split("/")


def _filename_has_traversal(value: str) -> bool:
    clean = value.strip()
    return "/" in clean or "\\" in clean or clean in {".", ".."} or ".." in clean.split(".")


def _url_is_token_bearing(value: str) -> bool:
    return bool(_SECRET_TEXT_RE.search(value) or _SENSITIVE_QUERY_RE.search(value))


def _url_is_private(value: str) -> bool:
    """Detect whether a Telegram payload URL points at a private/internal target.

    C-6 consolidation: the private/metadata/legacy-IPv4 classification now
    flows through :func:`magi_agent.security.ssrf.classify_host`. The
    telegram-adapter-specific extras (Supabase / R2 / object-host regex /
    ``.local`` mDNS TLD) stay here because they are NOT SSRF concerns — they
    are "don't leak signed storage URLs into telegram outbound" concerns
    specific to this transport.
    """
    from magi_agent.security.ssrf import classify_host

    parsed = _split_url_host(value)
    if parsed is None:
        return True
    scheme, host = parsed
    if scheme not in {"http", "https"}:
        return True
    host = host.casefold().strip()
    if not host:
        return True
    if _PRIVATE_OBJECT_HOST_RE.search(host):
        return True
    if host.endswith(".supabase.co") or host.endswith(".r2.cloudflarestorage.com"):
        return True
    if host.endswith(".local"):
        # mDNS-style local TLD — not in the SSRF leaf because the SSRF leaf
        # uses ``.localhost`` not ``.local``. Telegram-adapter keeps this
        # extra rule because shipping mDNS hostnames to telegram is a
        # transport-specific concern (the SSRF leaf is a strict superset of
        # what blocks egress; the adapter then adds extras on top).
        return True
    # Shared SSRF kernel handles localhost, metadata hosts, legacy IPv4 forms,
    # NAT64-embedded private/metadata IPv4, and standard IPv6 link-local /
    # loopback / private / reserved / multicast.
    if classify_host(host):
        return True
    # ``host.isdigit()`` legacy fork: the integer-host form (e.g. ``"123"`` →
    # ``0.0.0.123``) was handled in the legacy copy. ``classify_host`` routes
    # that through ``coerce_ip`` which already handles the legacy IPv4 forms;
    # the cases that reach here are non-IP DNS names that legitimately serve
    # telegram payloads — return False (not private).
    return False


def _split_url_host(value: str) -> tuple[str, str] | None:
    clean = value.strip()
    if "://" not in clean:
        return None
    scheme, rest = clean.split("://", 1)
    authority = re.split(r"[/#?]", rest, maxsplit=1)[0]
    if not authority:
        return None
    host_port = authority.rsplit("@", 1)[-1]
    if host_port.startswith("["):
        end = host_port.find("]")
        if end < 0:
            return None
        host = host_port[1:end]
    else:
        host = host_port.split(":", 1)[0]
    return scheme.casefold(), host


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "TelegramAdapterAuthorityFlags",
    "TelegramAdapterBoundary",
    "TelegramAdapterConfig",
    "TelegramAdapterDecision",
    "TelegramAttachmentRef",
    "TelegramChannelDispatchProviderAdapter",
    "TelegramDeliveryReceipt",
    "TelegramDownloadRequest",
    "TelegramInboundUpdate",
    "TelegramPollRequest",
    "TelegramProviderPort",
    "TelegramProviderSendRequest",
    "TelegramReplyRef",
    "TelegramSendRequest",
    "TelegramWebhookMitigationRequest",
]
