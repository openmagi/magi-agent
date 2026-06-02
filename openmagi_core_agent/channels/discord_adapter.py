from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.channels.contract import ChannelRef, ChannelType
from openmagi_core_agent.runtime.provider_execution import (
    ProviderExecutionBoundary,
    ProviderExecutionConfig,
    ProviderExecutionRequest,
    ProviderExecutionScope,
)
from openmagi_core_agent.runtime.provider_receipts import provider_digest


DiscordAdapterStatus = Literal[
    "disabled",
    "blocked",
    "inbound_projected_local_fake",
    "send_intent",
    "sent_local_fake",
    "typing_recorded_local_fake",
    "provider_error_swallowed",
]
DiscordSendOperation = Literal["send_message", "send_file", "send_typing"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DISCORD_CHUNK_LIMIT = 1900
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
_DISCORD_MENTION_RE = re.compile(r"<@!?(\d{15,22})>")
_DISCORD_CHANNEL_RE = re.compile(r"<#(\d{15,22})>")
_DISCORD_ROLE_RE = re.compile(r"<@&(\d{15,22})>")
_DISCORD_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]{1,64}):(\d{15,22})>")
_DISCORD_SNOWFLAKE_RE = re.compile(r"\b\d{15,22}\b")
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
    "context",
    "developer",
    "fetched",
    "executed",
    "injected",
    "instruction",
    "network",
    "parent",
    "prompt",
    "trust",
    "trusted",
    "verified",
    "valid",
)


class DiscordProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def read_events(self, request: DiscordEventRequest) -> Sequence[Mapping[str, Any]]: ...

    def send_message(self, request: DiscordProviderSendRequest) -> Mapping[str, object]: ...

    def send_file(self, request: DiscordProviderSendRequest) -> Mapping[str, object]: ...

    def send_typing(self, request: DiscordProviderSendRequest) -> Mapping[str, object]: ...


class DiscordAdapterConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_provider_enabled: bool = Field(default=False, alias="localFakeProviderEnabled")
    selected_channel_routes: tuple[ChannelType, ...] = Field(default=(), alias="selectedChannelRoutes")
    provider_allowlist: tuple[str, ...] = Field(default=(), alias="providerAllowlist")
    production_channel_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelWriteEnabled",
    )
    discord_gateway_attached: Literal[False] = Field(default=False, alias="discordGatewayAttached")
    discord_attached: Literal[False] = Field(default=False, alias="discordAttached")
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
        data["production_channel_write_enabled"] = False
        data["discord_gateway_attached"] = False
        data["discord_attached"] = False
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


class DiscordAdapterAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    provider_called: Literal[False] = Field(default=False, alias="providerCalled")
    gateway_attached: Literal[False] = Field(default=False, alias="gatewayAttached")
    discord_attached: Literal[False] = Field(default=False, alias="discordAttached")
    channel_delivery_performed: Literal[False] = Field(default=False, alias="channelDeliveryPerformed")
    production_channel_write: Literal[False] = Field(default=False, alias="productionChannelWrite")
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
        "gateway_attached",
        "discord_attached",
        "channel_delivery_performed",
        "production_channel_write",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class _DiscordScopedRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    provider_name: str = Field(alias="providerName")
    bot_id_digest: str = Field(alias="botIdDigest")
    owner_id_digest: str = Field(alias="ownerIdDigest")
    session_key_digest: str = Field(alias="sessionKeyDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)


class DiscordEventRequest(_DiscordScopedRequest):
    bot_user_id: str | None = Field(default=None, alias="botUserId")


class DiscordSendRequest(_DiscordScopedRequest):
    operation: DiscordSendOperation
    channel: ChannelRef
    channel_id: str = Field(alias="channelId")
    text: str | None = None
    reply_to_message_id: str | None = Field(default=None, alias="replyToMessageId")
    file_ref: str | None = Field(default=None, alias="fileRef")
    artifact_receipt_ref: str | None = Field(default=None, alias="artifactReceiptRef")


class DiscordProviderSendRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: DiscordSendOperation
    request_id: str = Field(alias="requestId")
    channel_id: str = Field(alias="channelId")
    text: str | None = None
    reply_to_message_id: str | None = Field(default=None, alias="replyToMessageId")
    file_ref: str | None = Field(default=None, alias="fileRef")
    artifact_receipt_ref: str | None = Field(default=None, alias="artifactReceiptRef")
    chunk_index: int = Field(default=1, alias="chunkIndex", ge=1)
    chunk_count: int = Field(default=1, alias="chunkCount", ge=1)


class DiscordReplyRef(BaseModel):
    model_config = _MODEL_CONFIG

    message_id: str = Field(alias="messageId")
    preview: str
    role: Literal["user", "assistant"] = "user"

    def public_projection(self) -> dict[str, object]:
        return {
            "messageId": _public_digest_ref(self.message_id, "discord-message"),
            "preview": _safe_discord_public_text(self.preview)[:160],
            "role": self.role if self.role in {"user", "assistant"} else "user",
        }


class DiscordAttachmentRef(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["file", "image", "audio"] = "file"
    file_ref: str = Field(alias="fileRef")
    filename: str
    mime_type: str | None = Field(default=None, alias="mimeType")
    size_bytes: int | None = Field(default=None, alias="sizeBytes", ge=0)

    def public_projection(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "fileRef": _public_ref(self.file_ref, "discord-file"),
            "filename": _safe_filename(self.filename),
            "mimeType": None if self.mime_type is None else _safe_text(self.mime_type)[:120],
            "sizeBytes": self.size_bytes,
        }


class DiscordInboundEvent(BaseModel):
    model_config = _MODEL_CONFIG

    channel: Literal["discord"] = "discord"
    channel_id: str = Field(alias="channelId")
    user_id: str = Field(alias="userId")
    text: str = ""
    message_id: str = Field(alias="messageId")
    reply_to: DiscordReplyRef | None = Field(default=None, alias="replyTo")
    attachment_refs: tuple[DiscordAttachmentRef, ...] = Field(default=(), alias="attachmentRefs")
    raw_event_ref: str = Field(alias="rawEventRef")

    def public_projection(self) -> dict[str, object]:
        return {
            "channel": "discord",
            "channelId": _public_digest_ref(self.channel_id, "discord-channel"),
            "userId": _public_digest_ref(self.user_id, "discord-user"),
            "text": _safe_discord_public_text(self.text),
            "messageId": _public_digest_ref(self.message_id, "discord-message"),
            "replyTo": None if self.reply_to is None else self.reply_to.public_projection(),
            "attachmentRefs": [ref.public_projection() for ref in self.attachment_refs],
            "rawEventRef": _public_digest_ref(self.raw_event_ref, "discord-event"),
        }


class DiscordDeliveryReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    channel_id: str = Field(alias="channelId")
    provider_message_id: str | None = Field(default=None, alias="providerMessageId")
    chunk_index: int = Field(default=1, alias="chunkIndex", ge=1)
    chunk_count: int = Field(default=1, alias="chunkCount", ge=1)
    file_ref: str | None = Field(default=None, alias="fileRef")

    def public_projection(self) -> dict[str, object]:
        return {
            "channelId": _public_digest_ref(self.channel_id, "discord-channel"),
            "providerMessageId": None
            if self.provider_message_id is None
            else _public_digest_ref(self.provider_message_id, "discord-message"),
            "chunkIndex": self.chunk_index,
            "chunkCount": self.chunk_count,
            "fileRef": None if self.file_ref is None else _public_ref(self.file_ref, "file"),
        }


class DiscordFailureReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    receipt_id: str = Field(alias="receiptId")
    request_digest: str = Field(alias="requestDigest")
    error_code: str = Field(alias="errorCode")
    message: str = "[redacted-discord-failure]"

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


class DiscordAdapterDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: DiscordAdapterStatus
    operation: str
    request_digest: str = Field(alias="requestDigest")
    inbound_events: tuple[DiscordInboundEvent, ...] = Field(default=(), alias="inboundEvents")
    delivery_receipts: tuple[DiscordDeliveryReceipt, ...] = Field(default=(), alias="deliveryReceipts")
    failure_receipt: DiscordFailureReceipt | None = Field(default=None, alias="failureReceipt")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: DiscordAdapterAuthorityFlags = Field(
        default_factory=DiscordAdapterAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = DiscordAdapterAuthorityFlags()
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
        data["authority_flags"] = DiscordAdapterAuthorityFlags()
        _ = deep
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "requestDigest": self.request_digest,
            "inboundEvents": [event.public_projection() for event in self.inbound_events],
            "deliveryReceipts": [receipt.public_projection() for receipt in self.delivery_receipts],
            "failureReceipt": None if self.failure_receipt is None else self.failure_receipt.public_projection(),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": DiscordAdapterAuthorityFlags().model_dump(by_alias=True),
        }


class DiscordAdapterBoundary:
    """Default-off Discord live adapter boundary with injected fake providers only."""

    def __init__(self, config: DiscordAdapterConfig) -> None:
        self.config = config

    def handle_events(
        self,
        request: DiscordEventRequest,
        *,
        provider: DiscordProviderPort | None = None,
    ) -> DiscordAdapterDecision:
        request_digest = provider_digest(_scope_payload(request) | {"operation": "handle_events"})
        diagnostics = _diagnostics(self.config, request, "handle_events")
        gate_error = self._gate_error(request, provider)
        if gate_error is not None:
            return _blocked_or_disabled("handle_events", request_digest, gate_error, diagnostics)
        captured = _CapturingDiscordProvider(provider, "read_events", request)
        execution = _run_provider_execution(self._execute_provider(request, "discord.read_events", captured))
        if execution.status != "ok":
            return _failure_decision(
                "provider_error_swallowed",
                "handle_events",
                request_digest,
                "discord_provider_error",
                diagnostics,
            )
        events = tuple(
            event for raw in captured.output_sequence if (event := _project_event(raw, request.bot_user_id)) is not None
        )
        return _decision(
            "inbound_projected_local_fake",
            "handle_events",
            request_digest,
            ("local_fake_discord_event_projection_only",),
            diagnostics,
            inbound_events=events,
        )

    def send(
        self,
        request: DiscordSendRequest,
        *,
        provider: DiscordProviderPort | None = None,
    ) -> DiscordAdapterDecision:
        request_digest = provider_digest(_scope_payload(request) | _send_payload(request))
        diagnostics = _diagnostics(self.config, request, request.operation)
        if not self.config.enabled:
            return _decision("disabled", request.operation, request_digest, ("discord_adapter_disabled",), diagnostics)
        channel_error = _discord_channel_error(self.config, request)
        if channel_error is not None:
            return _decision("blocked", request.operation, request_digest, (channel_error,), diagnostics)
        gate_error = self._gate_error(request, provider)
        if gate_error is not None:
            return _blocked_or_disabled(request.operation, request_digest, gate_error, diagnostics)
        if request.operation == "send_typing":
            return self._send_typing(request, provider, request_digest, diagnostics)
        if request.operation == "send_file":
            return self._send_file(request, provider, request_digest, diagnostics)
        return self._send_message(request, provider, request_digest, diagnostics)

    def _send_message(
        self,
        request: DiscordSendRequest,
        provider: DiscordProviderPort | None,
        request_digest: str,
        diagnostics: Mapping[str, object],
    ) -> DiscordAdapterDecision:
        if _contains_private_text(request.text or ""):
            return _decision("blocked", "send_message", request_digest, ("private_outbound_text_blocked",), diagnostics)
        receipts: list[DiscordDeliveryReceipt] = []
        chunks = _chunk_text(request.text or "")
        for index, chunk in enumerate(chunks, start=1):
            provider_request = DiscordProviderSendRequest(
                operation="send_message",
                requestId=request.request_id,
                channelId=request.channel_id,
                text=chunk,
                replyToMessageId=request.reply_to_message_id if index == 1 else None,
                chunkIndex=index,
                chunkCount=len(chunks),
            )
            captured = _CapturingDiscordProvider(provider, "send_message", provider_request)
            execution = _run_provider_execution(self._execute_provider(request, "discord.send_message", captured))
            failure_code = _provider_failure_code(execution, captured.output_mapping)
            if failure_code is not None:
                return _failure_decision(
                    "provider_error_swallowed" if failure_code == "discord_provider_error" else "blocked",
                    "send_message",
                    request_digest,
                    failure_code,
                    diagnostics,
                )
            assert captured.output_mapping is not None
            provider_message_id = _safe_provider_message_id(captured.output_mapping)
            if provider_message_id is None:
                return _failure_decision("blocked", "send_message", request_digest, "provider_message_ack_required", diagnostics)
            receipts.append(
                DiscordDeliveryReceipt(
                    channelId=request.channel_id,
                    providerMessageId=provider_message_id,
                    chunkIndex=index,
                    chunkCount=len(chunks),
                )
            )
        return _decision(
            "sent_local_fake",
            "send_message",
            request_digest,
            ("local_fake_discord_send_receipt_only",),
            diagnostics,
            delivery_receipts=tuple(receipts),
        )

    def _send_file(
        self,
        request: DiscordSendRequest,
        provider: DiscordProviderPort | None,
        request_digest: str,
        diagnostics: Mapping[str, object],
    ) -> DiscordAdapterDecision:
        if _contains_private_text(request.text or ""):
            return _decision("blocked", "send_file", request_digest, ("private_outbound_text_blocked",), diagnostics)
        if not request.file_ref or _looks_like_raw_path(request.file_ref):
            return _decision("blocked", "send_file", request_digest, ("raw_path_file_delivery_blocked",), diagnostics)
        if not _is_public_ref(request.file_ref):
            return _decision("blocked", "send_file", request_digest, ("invalid_file_ref_blocked",), diagnostics)
        if not request.artifact_receipt_ref:
            return _decision("blocked", "send_file", request_digest, ("artifact_receipt_required",), diagnostics)
        provider_request = DiscordProviderSendRequest(
            operation="send_file",
            requestId=request.request_id,
            channelId=request.channel_id,
            text=request.text,
            fileRef=_safe_ref(request.file_ref),
            artifactReceiptRef=_public_ref(request.artifact_receipt_ref, "artifact-receipt"),
        )
        captured = _CapturingDiscordProvider(provider, "send_file", provider_request)
        execution = _run_provider_execution(self._execute_provider(request, "discord.send_file", captured))
        failure_code = _provider_failure_code(execution, captured.output_mapping)
        if failure_code is not None:
            return _failure_decision(
                "provider_error_swallowed" if failure_code == "discord_provider_error" else "blocked",
                "send_file",
                request_digest,
                failure_code,
                diagnostics,
            )
        assert captured.output_mapping is not None
        provider_message_id = _safe_provider_message_id(captured.output_mapping)
        if provider_message_id is None:
            return _failure_decision("blocked", "send_file", request_digest, "provider_message_ack_required", diagnostics)
        return _decision(
            "sent_local_fake",
            "send_file",
            request_digest,
            ("local_fake_discord_file_receipt_only",),
            diagnostics,
            delivery_receipts=(
                DiscordDeliveryReceipt(
                    channelId=request.channel_id,
                    providerMessageId=provider_message_id,
                    fileRef=request.file_ref,
                ),
            ),
        )

    def _send_typing(
        self,
        request: DiscordSendRequest,
        provider: DiscordProviderPort | None,
        request_digest: str,
        diagnostics: Mapping[str, object],
    ) -> DiscordAdapterDecision:
        provider_request = DiscordProviderSendRequest(
            operation="send_typing",
            requestId=request.request_id,
            channelId=request.channel_id,
        )
        captured = _CapturingDiscordProvider(provider, "send_typing", provider_request)
        execution = _run_provider_execution(self._execute_provider(request, "discord.send_typing", captured))
        if execution.status != "ok":
            return _failure_decision(
                "provider_error_swallowed",
                "send_typing",
                request_digest,
                "discord_provider_error",
                diagnostics,
            )
        return _decision(
            "typing_recorded_local_fake",
            "send_typing",
            request_digest,
            ("local_fake_discord_typing_receipt_only",),
            diagnostics,
        )

    def _gate_error(self, request: _DiscordScopedRequest, provider: DiscordProviderPort | None) -> str | None:
        if not self.config.enabled:
            return "discord_adapter_disabled"
        if "discord" not in set(self.config.selected_channel_routes):
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
            return "local_fake_discord_provider_disabled"
        if getattr(provider, "openmagi_local_fake_provider", False) is not True:
            return "local_fake_discord_provider_untrusted"
        return None

    def _execute_provider(
        self,
        request: _DiscordScopedRequest,
        operation: str,
        provider: _CapturingDiscordProvider,
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
                payload=_scope_payload(request) | {"discordOperation": operation},
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


class _CapturingDiscordProvider:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        provider: DiscordProviderPort | None,
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
            raise RuntimeError("discord provider missing")
        if self.operation == "read_events":
            value = self.provider.read_events(self.adapter_request)  # type: ignore[arg-type]
            self.output_sequence = tuple(value)
            return {"status": "ok", "eventCount": len(self.output_sequence)}
        if self.operation == "send_message":
            value = self.provider.send_message(self.adapter_request)  # type: ignore[arg-type]
        elif self.operation == "send_file":
            value = self.provider.send_file(self.adapter_request)  # type: ignore[arg-type]
        elif self.operation == "send_typing":
            value = self.provider.send_typing(self.adapter_request)  # type: ignore[arg-type]
        else:
            raise RuntimeError("unknown discord provider operation")
        self.output_mapping = value
        return value


class DiscordChannelDispatchProviderAdapter:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *,
        boundary: DiscordAdapterBoundary,
        discord_provider: DiscordProviderPort,
    ) -> None:
        self.boundary = boundary
        self.discord_provider = discord_provider

    def execute(self, request: object) -> Mapping[str, object]:
        from openmagi_core_agent.channels.dispatcher import ChannelDispatchRequest

        if not isinstance(request, ChannelDispatchRequest) or request.channel.type != "discord":
            return {"status": "failed", "providerMessageId": None}
        if request.operation == "dispatch.message":
            operation: DiscordSendOperation = "send_message"
        elif request.operation == "typing.start":
            operation = "send_typing"
        elif request.operation == "file.send":
            operation = "send_file"
        else:
            return {"status": "skipped", "providerMessageId": None}
        decision = self.boundary.send(
            DiscordSendRequest(
                operation=operation,
                requestId=request.request_id,
                channel=request.channel,
                providerName=request.provider_name,
                botIdDigest=request.bot_id_digest,
                ownerIdDigest=request.user_id_digest,
                sessionKeyDigest=request.session_key_digest,
                channelId=request.channel.channel_id,
                text=request.text,
                fileRef=request.file_ref,
                artifactReceiptRef=_metadata_string(request.metadata, "artifactReceiptRef"),
            ),
            provider=self.discord_provider,
        )
        if decision.status not in {"sent_local_fake", "typing_recorded_local_fake"}:
            return {"status": "failed", "providerMessageId": None}
        provider_message_id = None if not decision.delivery_receipts else decision.delivery_receipts[0].provider_message_id
        return {"status": "sent", "providerMessageId": provider_message_id}


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
) -> DiscordAdapterDecision:
    if gate_error == "discord_adapter_disabled":
        return _decision("disabled", operation, request_digest, ("discord_adapter_disabled",), diagnostics)
    if gate_error == "local_fake_discord_provider_disabled":
        return _decision("send_intent", operation, request_digest, (gate_error,), diagnostics)
    return _decision("blocked", operation, request_digest, (gate_error,), diagnostics)


def _decision(
    status: DiscordAdapterStatus,
    operation: str,
    request_digest: str,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    inbound_events: tuple[DiscordInboundEvent, ...] = (),
    delivery_receipts: tuple[DiscordDeliveryReceipt, ...] = (),
    failure_receipt: DiscordFailureReceipt | None = None,
) -> DiscordAdapterDecision:
    return DiscordAdapterDecision(
        status=status,
        operation=operation,
        requestDigest=request_digest,
        inboundEvents=inbound_events,
        deliveryReceipts=delivery_receipts,
        failureReceipt=failure_receipt,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=DiscordAdapterAuthorityFlags(),
    )


def _failure_decision(
    status: DiscordAdapterStatus,
    operation: str,
    request_digest: str,
    error_code: str,
    diagnostics: Mapping[str, object],
) -> DiscordAdapterDecision:
    return _decision(
        status,
        operation,
        request_digest,
        (error_code,),
        diagnostics,
        failure_receipt=DiscordFailureReceipt(
            receiptId=f"discord-failure:{_short_digest(request_digest + ':' + error_code)}",
            requestDigest=request_digest,
            errorCode=error_code,
        ),
    )


def _provider_failure_code(execution: object, output: Mapping[str, object] | None) -> str | None:
    if getattr(execution, "status", None) != "ok":
        return "discord_provider_error"
    if output is None:
        return "discord_provider_error"
    status = output.get("status")
    if status == "permission_denied":
        return "discord_permission_denied"
    if status == "missing_channel":
        return "discord_channel_missing"
    if status == "rate_limited":
        return "discord_rate_limited"
    if status not in {None, "sent", "queued"}:
        return "discord_provider_error"
    return None


def _discord_channel_error(config: DiscordAdapterConfig, request: DiscordSendRequest) -> str | None:
    if request.channel.type != "discord":
        return "discord_channel_required"
    if request.channel.type not in set(config.selected_channel_routes):
        return "channel_route_not_selected"
    return None


def _project_event(raw: Mapping[str, Any], bot_user_id: str | None) -> DiscordInboundEvent | None:
    if raw.get("type") != "message_create":
        return None
    author = raw.get("author")
    if not isinstance(author, Mapping) or author.get("bot") is True:
        return None
    text = raw.get("content")
    if not isinstance(text, str):
        text = ""
    attachments = _attachments_from_event(raw)
    is_dm = raw.get("is_dm") is True
    mentions = raw.get("mentions")
    mention_values = set(str(value) for value in mentions) if isinstance(mentions, Sequence) and not isinstance(mentions, str | bytes | bytearray) else set()
    if not text and not attachments:
        return None
    if not is_dm and (not bot_user_id or bot_user_id not in mention_values):
        return None
    channel_id = _coerce_id(raw.get("channel_id"))
    user_id = _coerce_id(author.get("id"))
    message_id = _coerce_id(raw.get("id"))
    if not channel_id or not user_id or not message_id:
        return None
    return DiscordInboundEvent(
        channelId=channel_id,
        userId=user_id,
        text=_safe_text(text),
        messageId=message_id,
        replyTo=_reply_from_event(raw.get("reference"), bot_user_id),
        attachmentRefs=attachments,
        rawEventRef=_event_ref(raw),
    )


def _reply_from_event(value: object, bot_user_id: str | None) -> DiscordReplyRef | None:
    if not isinstance(value, Mapping):
        return None
    preview = value.get("content")
    message_id = _coerce_id(value.get("message_id"))
    if not isinstance(preview, str) or not preview.strip() or not message_id:
        return None
    author_id = value.get("author_id")
    role: Literal["user", "assistant"] = "assistant" if bot_user_id is not None and author_id == bot_user_id else "user"
    return DiscordReplyRef(messageId=message_id, preview=preview, role=role)


def _attachments_from_event(raw: Mapping[str, Any]) -> tuple[DiscordAttachmentRef, ...]:
    value = raw.get("attachments")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    refs: list[DiscordAttachmentRef] = []
    for index, attachment in enumerate(value, start=1):
        if not isinstance(attachment, Mapping):
            continue
        attachment_id = attachment.get("id")
        filename = attachment.get("filename")
        mime_type = attachment.get("content_type")
        refs.append(
            DiscordAttachmentRef(
                kind=_attachment_kind(mime_type),
                fileRef=f"discord-file:{_short_digest(str(attachment_id) if attachment_id is not None else repr(attachment))}",
                filename=_safe_filename(str(filename) if isinstance(filename, str) else f"attachment-{index}"),
                mimeType=str(mime_type) if isinstance(mime_type, str) else None,
                sizeBytes=attachment.get("size") if isinstance(attachment.get("size"), int) else None,
            )
        )
    return tuple(refs)


def _attachment_kind(mime_type: object) -> Literal["file", "image", "audio"]:
    if isinstance(mime_type, str) and mime_type.startswith("image/"):
        return "image"
    if isinstance(mime_type, str) and mime_type.startswith("audio/"):
        return "audio"
    return "file"


def _event_ref(raw: Mapping[str, Any]) -> str:
    event_id = raw.get("id")
    if isinstance(event_id, str) and event_id.strip():
        return _public_digest_ref(event_id, "discord-event")
    return _public_digest_ref(repr(raw), "discord-event")


def _chunk_text(text: str) -> tuple[str, ...]:
    if text == "":
        return ("",)
    return tuple(text[index : index + _DISCORD_CHUNK_LIMIT] for index in range(0, len(text), _DISCORD_CHUNK_LIMIT))


def _scope_payload(request: _DiscordScopedRequest) -> dict[str, object]:
    return {
        "requestId": request.request_id,
        "providerName": request.provider_name,
        "botIdDigest": request.bot_id_digest,
        "ownerIdDigest": request.owner_id_digest,
        "sessionKeyDigest": request.session_key_digest,
    }


def _send_payload(request: DiscordSendRequest) -> dict[str, object]:
    return {
        "operation": request.operation,
        "channelType": request.channel.type,
        "channelId": request.channel_id,
        "text": request.text,
        "replyToMessageId": request.reply_to_message_id,
        "fileRef": request.file_ref,
        "artifactReceiptRef": request.artifact_receipt_ref,
    }


def _diagnostics(config: DiscordAdapterConfig, request: _DiscordScopedRequest, operation: str) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeProviderEnabled": config.local_fake_provider_enabled,
        "productionChannelWriteEnabled": False,
        "discordGatewayAttached": False,
        "discordAttached": False,
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


def _metadata_string(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


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
        raise ValueError("Discord adapter refs must be public identifiers")
    return clean


def _is_public_ref(value: str) -> bool:
    clean = _safe_text(value.strip())
    return bool(clean and _REF_RE.fullmatch(clean))


def _public_ref(value: str, prefix: str) -> str:
    clean = _safe_text(str(value)).strip()
    if clean and _REF_RE.fullmatch(clean):
        return clean
    return f"{prefix}:{_short_digest(str(value))}"


def _public_digest_ref(value: str, prefix: str) -> str:
    return f"{prefix}:{_short_digest(str(value))}"


def _safe_filename(value: str) -> str:
    base = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
    clean = _safe_text(base).lstrip(".").replace("/", "-").replace("\\", "-").strip()
    return clean[:160] if clean else "attachment"


def _contains_private_text(value: str) -> bool:
    return bool(_SECRET_TEXT_RE.search(value) or _PRIVATE_TEXT_RE.search(value))


def _safe_text(value: str) -> str:
    if _contains_private_text(value):
        return "[redacted]"
    return value[:4096]


def _safe_discord_public_text(value: str) -> str:
    clean = _safe_text(value)
    if clean == "[redacted]":
        return clean
    clean = _DISCORD_EMOJI_RE.sub(
        lambda match: f"<:{match.group(1)}:discord-emoji:{_short_digest(match.group(2))}>",
        clean,
    )
    clean = _DISCORD_ROLE_RE.sub(
        lambda match: f"<@&discord-role:{_short_digest(match.group(1))}>",
        clean,
    )
    clean = _DISCORD_CHANNEL_RE.sub(
        lambda match: f"<#discord-channel:{_short_digest(match.group(1))}>",
        clean,
    )
    clean = _DISCORD_MENTION_RE.sub(
        lambda match: f"<@discord-user:{_short_digest(match.group(1))}>",
        clean,
    )
    return _DISCORD_SNOWFLAKE_RE.sub(
        lambda match: f"discord-id:{_short_digest(match.group(0))}",
        clean,
    )[:4096]


def _looks_like_raw_path(value: str) -> bool:
    return value.startswith(("/", "~")) or "\\" in value or ".." in value.split("/")


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "DiscordAdapterAuthorityFlags",
    "DiscordAdapterBoundary",
    "DiscordAdapterConfig",
    "DiscordAdapterDecision",
    "DiscordAttachmentRef",
    "DiscordChannelDispatchProviderAdapter",
    "DiscordDeliveryReceipt",
    "DiscordEventRequest",
    "DiscordFailureReceipt",
    "DiscordInboundEvent",
    "DiscordProviderPort",
    "DiscordProviderSendRequest",
    "DiscordReplyRef",
    "DiscordSendRequest",
]
