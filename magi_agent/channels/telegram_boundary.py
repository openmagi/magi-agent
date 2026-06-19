from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel


TelegramOperation = Literal[
    "poll_once",
    "send_message",
    "send_typing",
    "send_document",
    "send_photo",
    "start",
    "stop",
]
TelegramRuntimeStatus = Literal[
    "disabled",
    "poll_intent",
    "inbound_projected_local_fake",
    "send_intent",
    "sent_local_fake",
    "typing_recorded_local_fake",
    "provider_error_swallowed",
    "blocked",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_TELEGRAM_MAX_TEXT_CHARS = 3500
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
    r"(?:/Users(?:/[^,\s\"']*)?|/workspace(?:/[^,\s\"']*)?|"
    r"/data/bots(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args|browser|child)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"reasoning[_ -]?trace|model[_ -]?internal|authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SENSITIVE_METADATA_KEY_MARKERS = (
    "raw",
    "key",
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


class TelegramProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def poll_once(self, request: TelegramRuntimeRequest) -> Sequence[Mapping[str, Any]]: ...

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> Mapping[str, Any]: ...

    def send_typing(self, *, chat_id: str) -> Mapping[str, Any]: ...

    def send_document(
        self,
        *,
        chat_id: str,
        file_ref: str,
        caption: str | None = None,
    ) -> Mapping[str, Any]: ...

    def send_photo(
        self,
        *,
        chat_id: str,
        file_ref: str,
        caption: str | None = None,
    ) -> Mapping[str, Any]: ...


class TelegramRuntimeConfig(FalseOnlyAuthorityModel):
    enabled: bool = False
    local_fake_telegram_provider_enabled: bool = Field(
        default=False,
        alias="localFakeTelegramProviderEnabled",
    )
    telegram_polling_attached: Literal[False] = Field(
        default=False,
        alias="telegramPollingAttached",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    production_channel_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelWriteEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class TelegramRuntimeAuthorityFlags(FalseOnlyAuthorityModel):
    telegram_polling_attached: Literal[False] = Field(
        default=False,
        alias="telegramPollingAttached",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    production_channel_write: Literal[False] = Field(
        default=False,
        alias="productionChannelWrite",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class TelegramRuntimeRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: TelegramOperation
    chat_id: str | None = Field(default=None, alias="chatId")
    text: str | None = None
    reply_to_message_id: str | None = Field(default=None, alias="replyToMessageId")
    file_ref: str | None = Field(default=None, alias="fileRef")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("chat_id", "reply_to_message_id")
    @classmethod
    def _sanitize_optional_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = _sanitize_public_text(value.strip())
        if not clean:
            raise ValueError("Telegram ids must be non-empty when present")
        return clean[:120]


class TelegramAttachmentRef(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["file"] = "file"
    file_ref: str = Field(alias="fileRef")
    filename: str
    mime_type: str | None = Field(default=None, alias="mimeType")
    size_bytes: int | None = Field(default=None, alias="sizeBytes", ge=0)

    @field_validator("file_ref")
    @classmethod
    def _validate_file_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("filename")
    @classmethod
    def _sanitize_filename(cls, value: str) -> str:
        clean = _sanitize_public_text(value.strip()).replace("/", "-").replace("\\", "-")
        if not clean or clean in {".", ".."}:
            raise ValueError("filename must be public basename")
        return clean[:160]


class TelegramReplyRef(BaseModel):
    model_config = _MODEL_CONFIG

    message_id: str = Field(alias="messageId")
    preview: str
    role: Literal["user", "assistant"] = "user"

    def public_projection(self) -> dict[str, object]:
        return {
            "messageId": _sanitize_public_text(self.message_id)[:120],
            "preview": _sanitize_public_text(self.preview)[:160],
            "role": self.role if self.role in {"user", "assistant"} else "user",
        }


class TelegramInboundMessage(BaseModel):
    model_config = _MODEL_CONFIG

    channel: Literal["telegram"] = "telegram"
    chat_id: str = Field(alias="chatId")
    user_id: str = Field(alias="userId")
    text: str = ""
    message_id: str = Field(alias="messageId")
    reply_to: TelegramReplyRef | None = Field(default=None, alias="replyTo")
    attachment_refs: tuple[TelegramAttachmentRef, ...] = Field(
        default=(),
        alias="attachmentRefs",
    )
    raw_update_ref: str = Field(alias="rawUpdateRef")

    def public_projection(self) -> dict[str, object]:
        return {
            "channel": "telegram",
            "chatId": _sanitize_public_text(self.chat_id)[:120],
            "userId": _sanitize_public_text(self.user_id)[:120],
            "text": _sanitize_public_text(self.text),
            "messageId": _sanitize_public_text(self.message_id)[:120],
            "replyTo": (
                None
                if self.reply_to is None
                else self.reply_to.public_projection()
            ),
            "attachmentRefs": [
                _safe_attachment_projection(ref) for ref in self.attachment_refs
            ],
            "rawUpdateRef": _safe_public_update_ref(self.raw_update_ref),
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
            "chatId": _sanitize_public_text(self.chat_id)[:120],
            "providerMessageId": _optional_public_text(self.provider_message_id),
            "chunkIndex": self.chunk_index,
            "chunkCount": self.chunk_count,
            "fileRef": _optional_safe_ref(self.file_ref),
        }


class TelegramRuntimeDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: TelegramRuntimeStatus
    operation: TelegramOperation
    inbound_messages: tuple[TelegramInboundMessage, ...] = Field(
        default=(),
        alias="inboundMessages",
    )
    delivery_receipts: tuple[TelegramDeliveryReceipt, ...] = Field(
        default=(),
        alias="deliveryReceipts",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: TelegramRuntimeAuthorityFlags = Field(
        default_factory=TelegramRuntimeAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = TelegramRuntimeAuthorityFlags()
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
        data["authorityFlags"] = TelegramRuntimeAuthorityFlags()
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "inboundMessages": [
                message.public_projection() for message in self.inbound_messages
            ],
            "deliveryReceipts": [
                receipt.public_projection() for receipt in self.delivery_receipts
            ],
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class TelegramRuntimeBoundary:
    """Disabled-by-default Telegram provider boundary with local fake support only."""

    def __init__(self, config: TelegramRuntimeConfig) -> None:
        self.config = config

    def execute(
        self,
        request: TelegramRuntimeRequest,
        *,
        provider: TelegramProviderPort | None = None,
    ) -> TelegramRuntimeDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeTelegramProviderEnabled": self.config.local_fake_telegram_provider_enabled,
            "telegramPollingAttached": False,
            "telegramAttached": False,
            "productionChannelWriteEnabled": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                reason_codes=("telegram_runtime_disabled",),
                diagnostics=diagnostics,
            )
        if not self.config.local_fake_telegram_provider_enabled or provider is None:
            return _decision(
                request,
                "poll_intent" if request.operation == "poll_once" else "send_intent",
                reason_codes=("local_telegram_provider_disabled",),
                diagnostics=diagnostics,
            )
        if not _is_local_fake_provider(provider):
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_telegram_provider_untrusted",),
                diagnostics=diagnostics,
            )
        if request.operation == "poll_once":
            try:
                updates = provider.poll_once(request)
            except Exception as exc:
                return _decision(
                    request,
                    "provider_error_swallowed",
                    reason_codes=("telegram_poll_error_swallowed",),
                    diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
                )
            messages = tuple(
                message
                for update in updates
                if (message := _project_update(update)) is not None
            )
            return _decision(
                request,
                "inbound_projected_local_fake",
                inbound_messages=messages,
                reason_codes=("local_fake_poll_projection_only",),
                diagnostics=diagnostics,
            )
        if request.operation == "send_typing":
            return self._send_typing(request, provider, diagnostics)
        if request.operation == "send_document":
            return self._send_document(request, provider, diagnostics)
        if request.operation == "send_photo":
            return self._send_photo(request, provider, diagnostics)
        if request.operation == "send_message":
            return self._send_message(request, provider, diagnostics)
        return _decision(
            request,
            "send_intent",
            reason_codes=("telegram_lifecycle_metadata_only",),
            diagnostics=diagnostics,
        )

    def _send_message(
        self,
        request: TelegramRuntimeRequest,
        provider: TelegramProviderPort,
        diagnostics: Mapping[str, object],
    ) -> TelegramRuntimeDecision:
        if not request.chat_id:
            return _decision(
                request,
                "blocked",
                reason_codes=("telegram_chat_required",),
                diagnostics=diagnostics,
            )
        if _looks_like_private_outbound_text(request.text):
            return _decision(
                request,
                "blocked",
                reason_codes=("private_outbound_text_blocked",),
                diagnostics=diagnostics,
            )
        if not _provider_ack_guaranteed(provider):
            return _decision(
                request,
                "blocked",
                reason_codes=("provider_message_ack_required",),
                diagnostics=diagnostics,
            )
        chunks = _chunk_text(request.text or "")
        receipts: list[TelegramDeliveryReceipt] = []
        try:
            for index, chunk in enumerate(chunks, start=1):
                raw = provider.send_message(
                    chat_id=request.chat_id,
                    text=chunk,
                    reply_to_message_id=(
                        request.reply_to_message_id if index == 1 else None
                    ),
                )
                provider_message_id = _optional_public_text(raw.get("providerMessageId"))
                if not provider_message_id:
                    return _decision(
                        request,
                        "blocked",
                        reason_codes=("provider_message_ack_required",),
                        diagnostics=diagnostics,
                    )
                receipts.append(
                    TelegramDeliveryReceipt(
                        chatId=request.chat_id,
                        providerMessageId=provider_message_id,
                        chunkIndex=index,
                        chunkCount=len(chunks),
                    )
                )
        except Exception as exc:
            return _decision(
                request,
                "provider_error_swallowed",
                reason_codes=("telegram_send_error_swallowed",),
                diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        return _decision(
            request,
            "sent_local_fake",
            delivery_receipts=tuple(receipts),
            reason_codes=("local_fake_telegram_send_receipt_only",),
            diagnostics=diagnostics,
        )

    def _send_typing(
        self,
        request: TelegramRuntimeRequest,
        provider: TelegramProviderPort,
        diagnostics: Mapping[str, object],
    ) -> TelegramRuntimeDecision:
        if not request.chat_id:
            return _decision(
                request,
                "blocked",
                reason_codes=("telegram_chat_required",),
                diagnostics=diagnostics,
            )
        try:
            provider.send_typing(chat_id=request.chat_id)
        except Exception as exc:
            return _decision(
                request,
                "provider_error_swallowed",
                reason_codes=("telegram_typing_error_swallowed",),
                diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        return _decision(
            request,
            "typing_recorded_local_fake",
            reason_codes=("local_fake_typing_receipt_only",),
            diagnostics=diagnostics,
        )

    def _send_document(
        self,
        request: TelegramRuntimeRequest,
        provider: TelegramProviderPort,
        diagnostics: Mapping[str, object],
    ) -> TelegramRuntimeDecision:
        return self._send_file_like(
            request,
            provider,
            diagnostics,
            operation_name="document",
        )

    def _send_photo(
        self,
        request: TelegramRuntimeRequest,
        provider: TelegramProviderPort,
        diagnostics: Mapping[str, object],
    ) -> TelegramRuntimeDecision:
        return self._send_file_like(
            request,
            provider,
            diagnostics,
            operation_name="photo",
        )

    def _send_file_like(
        self,
        request: TelegramRuntimeRequest,
        provider: TelegramProviderPort,
        diagnostics: Mapping[str, object],
        *,
        operation_name: Literal["document", "photo"],
    ) -> TelegramRuntimeDecision:
        if not request.chat_id:
            return _decision(
                request,
                "blocked",
                reason_codes=("telegram_chat_required",),
                diagnostics=diagnostics,
            )
        if not request.file_ref or _looks_like_raw_path(request.file_ref):
            return _decision(
                request,
                "blocked",
                reason_codes=("raw_path_file_delivery_blocked",),
                diagnostics=diagnostics,
            )
        if _looks_like_private_outbound_text(request.text):
            return _decision(
                request,
                "blocked",
                reason_codes=("private_outbound_text_blocked",),
                diagnostics=diagnostics,
            )
        if not _provider_ack_guaranteed(provider):
            return _decision(
                request,
                "blocked",
                reason_codes=("provider_message_ack_required",),
                diagnostics=diagnostics,
            )
        file_ref = _safe_ref(request.file_ref)
        try:
            sender = (
                provider.send_document
                if operation_name == "document"
                else provider.send_photo
            )
            raw = sender(chat_id=request.chat_id, file_ref=file_ref, caption=request.text)
        except Exception as exc:
            return _decision(
                request,
                "provider_error_swallowed",
                reason_codes=(f"telegram_{operation_name}_error_swallowed",),
                diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        provider_message_id = _optional_public_text(raw.get("providerMessageId"))
        if not provider_message_id:
            return _decision(
                request,
                "blocked",
                reason_codes=("provider_message_ack_required",),
                diagnostics=diagnostics,
            )
        return _decision(
            request,
            "sent_local_fake",
            delivery_receipts=(
                TelegramDeliveryReceipt(
                    chatId=request.chat_id,
                    providerMessageId=provider_message_id,
                    fileRef=file_ref,
                ),
            ),
            reason_codes=(f"local_fake_telegram_{operation_name}_receipt_only",),
            diagnostics=diagnostics,
        )


def _provider_ack_guaranteed(provider: TelegramProviderPort) -> bool:
    return getattr(provider, "openmagi_delivery_ack_guaranteed", False) is True


def _decision(
    request: TelegramRuntimeRequest,
    status: TelegramRuntimeStatus,
    *,
    inbound_messages: tuple[TelegramInboundMessage, ...] = (),
    delivery_receipts: tuple[TelegramDeliveryReceipt, ...] = (),
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> TelegramRuntimeDecision:
    return TelegramRuntimeDecision(
        status=status,
        operation=request.operation,
        inboundMessages=inbound_messages,
        deliveryReceipts=delivery_receipts,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=TelegramRuntimeAuthorityFlags(),
    )


def _project_update(update: Mapping[str, Any]) -> TelegramInboundMessage | None:
    message = update.get("message")
    if not isinstance(message, Mapping):
        return None
    text = message.get("text")
    caption = message.get("caption")
    media_attachment = _attachment_from_message(message)
    if not isinstance(text, str) and not isinstance(caption, str) and media_attachment is None:
        return None
    chat = message.get("chat")
    sender = message.get("from")
    if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
        return None
    message_id = _coerce_id(message.get("message_id"))
    chat_id = _coerce_id(chat.get("id"))
    user_id = _coerce_id(sender.get("id"))
    if not message_id or not chat_id or not user_id:
        return None
    attachment_refs = () if media_attachment is None else (media_attachment,)
    reply_to = _project_reply(message.get("reply_to_message"))
    update_id = update.get("update_id")
    raw_update_ref = _safe_update_ref(update_id, update)
    return TelegramInboundMessage(
        chatId=chat_id,
        userId=user_id,
        text=_sanitize_public_text(text if isinstance(text, str) else caption or ""),
        messageId=message_id,
        replyTo=reply_to,
        attachmentRefs=attachment_refs,
        rawUpdateRef=raw_update_ref,
    )


def _attachment_from_message(message: Mapping[str, Any]) -> TelegramAttachmentRef | None:
    document = message.get("document")
    if isinstance(document, Mapping):
        return _attachment_from_media(
            document,
            default_filename="document",
            default_mime_type="application/octet-stream",
        )
    for field_name, filename, mime_type in (
        ("photo", "photo.jpg", "image/jpeg"),
        ("audio", "audio", "audio/mpeg"),
        ("voice", "voice.ogg", "audio/ogg"),
        ("video", "video.mp4", "video/mp4"),
    ):
        value = message.get(field_name)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            candidates = [item for item in value if isinstance(item, Mapping)]
            if not candidates:
                continue
            selected = candidates[-1]
            return _attachment_from_media(
                selected,
                default_filename=filename,
                default_mime_type=mime_type,
            )
        if isinstance(value, Mapping):
            return _attachment_from_media(
                value,
                default_filename=filename,
                default_mime_type=mime_type,
            )
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
    raw_filename = media.get("file_name")
    filename = raw_filename if isinstance(raw_filename, str) else default_filename
    mime_type = media.get("mime_type")
    return TelegramAttachmentRef(
        fileRef=f"telegram-file:{_short_digest(file_id)}",
        filename=filename,
        mimeType=_optional_public_text(mime_type) or default_mime_type,
        sizeBytes=int(media["file_size"]) if isinstance(media.get("file_size"), int) else None,
    )


def _project_reply(value: object) -> TelegramReplyRef | None:
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
    return TelegramReplyRef(
        messageId=message_id,
        preview=_sanitize_public_text(preview)[:160],
        role="user",
    )


def _coerce_id(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return _sanitize_public_text(value.strip())[:120]
    return ""


def _chunk_text(text: str) -> tuple[str, ...]:
    if text == "":
        return ("",)
    return tuple(
        text[index : index + _TELEGRAM_MAX_TEXT_CHARS]
        for index in range(0, len(text), _TELEGRAM_MAX_TEXT_CHARS)
    )


def _looks_like_private_outbound_text(value: str | None) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return bool(
        _RAW_PRIVATE_LINE_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
        or _SECRET_TEXT_RE.search(value)
    )


def _safe_ref(value: str) -> str:
    clean = _sanitize_public_text(value.strip())
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("Telegram refs must be public identifiers")
    return clean


def _optional_safe_ref(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _safe_ref(value)
    except ValueError:
        return f"telegram-ref:{_short_digest(value)}"


def _safe_attachment_projection(ref: TelegramAttachmentRef) -> dict[str, object]:
    return {
        "kind": "file",
        "fileRef": _optional_safe_ref(ref.file_ref) or "telegram-file:invalid",
        "filename": _sanitize_public_text(ref.filename).replace("/", "-").replace("\\", "-")[
            :160
        ],
        "mimeType": _optional_public_text(ref.mime_type),
        "sizeBytes": ref.size_bytes if isinstance(ref.size_bytes, int) and ref.size_bytes >= 0 else None,
    }


def _safe_update_ref(update_id: object, update: Mapping[str, Any]) -> str:
    if isinstance(update_id, int):
        return f"telegram-update:{update_id}"
    return f"telegram-update:{_short_digest(str(update_id) if update_id is not None else repr(update))}"


def _safe_public_update_ref(value: str) -> str:
    if (
        re.fullmatch(r"telegram-update:[A-Za-z0-9._:-]+", value or "")
        and _sanitize_public_text(value) == value
        and not _SECRET_TEXT_RE.search(value)
    ):
        return value
    return f"telegram-update:{_short_digest(value or 'missing')}"


def _is_local_fake_provider(provider: object) -> bool:
    return getattr(provider, "openmagi_local_fake_provider", False) is True


def _optional_public_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = _sanitize_public_text(value)
    return clean or None


def _looks_like_raw_path(value: str) -> bool:
    clean = value.strip()
    return "/" in clean or "\\" in clean or bool(_PRIVATE_PATH_RE.search(clean))


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_METADATA_KEY_MARKERS):
            continue
        if isinstance(value, str):
            safe[str(key)] = _sanitize_public_text(value)
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _sanitize_public_text(value: str) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None
    ]
    value = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", value)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()[:4096]


def _safe_provider_error(exc: BaseException) -> str:
    return _sanitize_public_text(str(exc))[:240] or "[redacted-provider-error]"


def _short_digest(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "TelegramAttachmentRef",
    "TelegramDeliveryReceipt",
    "TelegramInboundMessage",
    "TelegramProviderPort",
    "TelegramRuntimeAuthorityFlags",
    "TelegramRuntimeBoundary",
    "TelegramRuntimeConfig",
    "TelegramRuntimeDecision",
    "TelegramRuntimeRequest",
]
