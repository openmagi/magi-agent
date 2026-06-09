from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_serializer, field_validator

from magi_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef
from magi_agent.runtime.provider_receipts import (
    ProviderReceipt,
    build_provider_receipt,
    provider_digest,
    sanitize_provider_payload,
)


FileDeliveryOperation = Literal["file.deliver", "file.send"]
FileDeliveryStatus = Literal[
    "disabled",
    "delivery_intent",
    "delivered_local_fake",
    "delivered_live",
    "blocked",
]
_BOUNDARY_VERIFICATION_TOKEN = object()

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^\s,;}\"']*)?|/home(?:/[^\s,;}\"']*)?|/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|/var/lib/kubelet(?:/[^\s,;}\"']*)?)",
    re.IGNORECASE,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{6,}|gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{6,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"(?:authorization|cookie|set-cookie|password|token|secret|credential)"
    r"\s*[:=]\s*[^,\s}{\n]{3,})",
    re.IGNORECASE,
)
_RAW_PRIVATE_RE = re.compile(
    r"raw[_ -]?(?:content|file|path|transcript|tool|prompt|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?memory",
    re.IGNORECASE,
)
_UNSUPPORTED_MIME_TYPES = {
    "application/x-shellscript",
    "application/x-msdownload",
    "application/x-executable",
}
_SENSITIVE_METADATA_KEY_MARKERS = (
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


class FileDeliveryProviderPort(Protocol):
    openmagi_local_fake_provider: bool

    def write_artifact(self, request: FileDeliveryRequest) -> Mapping[str, object]: ...


class FileChannelDeliveryPort(Protocol):
    openmagi_local_fake_provider: bool

    def deliver(self, request: FileDeliveryRequest) -> ChannelDeliveryReceipt: ...


class LiveFileArtifactProviderPort(Protocol):
    # Type-clarity twin of FileDeliveryProviderPort for live providers. Runtime
    # trust stays getattr/duck-typed (_is_trusted_live_provider); this Protocol
    # exists so upcoming live provider implementations have something to declare.
    openmagi_live_provider: bool

    def write_artifact(self, request: FileDeliveryRequest) -> Mapping[str, object]: ...


class LiveFileChannelDeliveryPort(Protocol):
    # Type-clarity twin of FileChannelDeliveryPort for live providers.
    openmagi_live_provider: bool

    def deliver(self, request: FileDeliveryRequest) -> ChannelDeliveryReceipt: ...


class FileDeliveryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_artifact_service_enabled: bool = Field(
        default=False,
        alias="localFakeArtifactServiceEnabled",
    )
    local_fake_channel_delivery_enabled: bool = Field(
        default=False,
        alias="localFakeChannelDeliveryEnabled",
    )
    # Parallel real-bool live gate (web_acquisition precedent: "default-False IS
    # the seal"). These do NOT unseal the Literal[False] flags below; they admit a
    # trusted live provider only when explicitly enabled by an operator.
    live_artifact_storage_enabled: bool = Field(
        default=False,
        alias="liveArtifactStorageEnabled",
    )
    live_channel_delivery_enabled: bool = Field(
        default=False,
        alias="liveChannelDeliveryEnabled",
    )
    production_storage_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionStorageWritesEnabled",
    )
    production_channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelDeliveryEnabled",
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
        data["production_storage_writes_enabled"] = False
        data["production_channel_delivery_enabled"] = False
        data["route_attached"] = False
        _ = deep
        return type(self).model_validate(data)


class FileDeliveryAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    artifact_written: Literal[False] = Field(default=False, alias="artifactWritten")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_channel_write: Literal[False] = Field(
        default=False,
        alias="productionChannelWrite",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    raw_content_injected: Literal[False] = Field(default=False, alias="rawContentInjected")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "adk_artifact_service_attached",
        "artifact_written",
        "channel_delivery_performed",
        "production_storage_written",
        "production_channel_write",
        "route_attached",
        "raw_content_injected",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class FileDeliveryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: FileDeliveryOperation
    request_id: str = Field(alias="requestId")
    session_key: str = Field(alias="sessionKey")
    channel: ChannelRef | None = None
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    file_refs: tuple[str, ...] = Field(default=(), alias="fileRefs")
    filename: str
    mime_type: str = Field(alias="mimeType")
    content_digest: str = Field(alias="contentDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "session_key")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        clean = value.strip()
        if not clean or _contains_private_text(clean):
            raise ValueError("id must be public")
        return clean[:180]

    @field_validator("artifact_refs", "file_refs", mode="before")
    @classmethod
    def _coerce_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            return tuple(str(item) for item in value)
        return ()

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        clean = value.strip()
        if not clean or "/" in clean or "\\" in clean or clean in {".", ".."} or ".." in clean:
            raise ValueError("filename must be safe basename")
        if _contains_private_text(clean):
            raise ValueError("filename must be public")
        return clean[:160]

    @field_validator("mime_type")
    @classmethod
    def _validate_mime_type(cls, value: str) -> str:
        clean = value.strip().lower()
        if "/" not in clean or _contains_private_text(clean):
            raise ValueError("mimeType must be public media type")
        return clean[:120]

    @field_validator("content_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("contentDigest must use sha256 hex digest")
        return value


class FileDeliveryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: FileDeliveryStatus
    operation: FileDeliveryOperation
    request_id: str = Field(alias="requestId")
    artifact_ref: str | None = Field(default=None, alias="artifactRef")
    content_digest: str | None = Field(default=None, alias="contentDigest")
    artifact_receipt: ProviderReceipt | None = Field(default=None, alias="artifactReceipt")
    delivery_receipt: ChannelDeliveryReceipt | None = Field(
        default=None,
        alias="deliveryReceipt",
    )
    delivery_claim_allowed: bool = Field(default=False, alias="deliveryClaimAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: FileDeliveryAuthorityFlags = Field(
        default_factory=FileDeliveryAuthorityFlags,
        alias="authorityFlags",
    )
    _boundary_verification_token: object | None = PrivateAttr(default=None)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        values["authorityFlags"] = FileDeliveryAuthorityFlags()
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
        data["authority_flags"] = FileDeliveryAuthorityFlags()
        _ = deep
        return type(self).model_validate(data)

    @property
    def boundary_verified(self) -> bool:
        return self._boundary_verification_token is _BOUNDARY_VERIFICATION_TOKEN

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "requestId": _public_ref(self.request_id, "request"),
            "artifactRef": None if self.artifact_ref is None else _public_ref(self.artifact_ref, "artifact"),
            "contentDigest": None if self.content_digest is None else _public_digest(self.content_digest),
            "artifactReceipt": (
                None if self.artifact_receipt is None else self.artifact_receipt.model_dump(by_alias=True)
            ),
            "deliveryReceipt": _safe_delivery_receipt(self.delivery_receipt),
            "deliveryClaimAllowed": self.delivery_claim_allowed,
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class FileDeliveryBoundary:
    """Default-off FileDeliver/FileSend boundary with injected fake providers only."""

    def __init__(self, config: FileDeliveryConfig) -> None:
        self.config = config

    def execute(
        self,
        request: FileDeliveryRequest,
        *,
        artifact_provider: FileDeliveryProviderPort | None = None,
        channel_provider: FileChannelDeliveryPort | None = None,
    ) -> FileDeliveryDecision:
        diagnostics = _diagnostics(self.config)
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                reason_codes=("file_delivery_disabled",),
                diagnostics=diagnostics,
            )

        validation_error = _validation_error(request)
        if validation_error is not None:
            return _decision(
                request,
                "blocked",
                reason_codes=(validation_error,),
                diagnostics=diagnostics,
            )

        artifact_ref = _digest_artifact_ref(request.artifact_refs[0]) if request.artifact_refs else None
        content_digest = request.content_digest
        artifact_receipt: ProviderReceipt | None = None
        # Track which providers ran live so the decision site can require BOTH
        # sides to be live before emitting delivered_live (no mixed-mode).
        artifact_used_live = False
        channel_used_live = False
        artifact_live = (
            self.config.live_artifact_storage_enabled and artifact_provider is not None
        )
        artifact_fake = (
            self.config.local_fake_artifact_service_enabled and artifact_provider is not None
        )
        if (artifact_live or artifact_fake) and artifact_provider is not None:
            if artifact_live:
                if not _is_trusted_live_provider(artifact_provider):
                    return _decision(
                        request,
                        "blocked",
                        reason_codes=("live_artifact_provider_untrusted",),
                        diagnostics=diagnostics,
                    )
                artifact_used_live = True
            else:
                if not _is_local_fake_provider(artifact_provider):
                    return _decision(
                        request,
                        "blocked",
                        reason_codes=("local_fake_artifact_provider_untrusted",),
                        diagnostics=diagnostics,
                    )
            try:
                raw_artifact = artifact_provider.write_artifact(request)
                if inspect.isawaitable(raw_artifact):
                    raise TypeError("async artifact provider is not supported in sync boundary")
            except Exception as exc:
                return _decision(
                    request,
                    "blocked",
                    reason_codes=(
                        "live_artifact_provider_error"
                        if artifact_live
                        else "local_fake_artifact_provider_error",
                    ),
                    diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
                )
            artifact_ref = _artifact_ref_from_raw(raw_artifact) or artifact_ref
            content_digest = _content_digest_from_raw(raw_artifact) or content_digest
            artifact_receipt = build_provider_receipt(
                provider_name="file-artifact-provider",
                operation=request.operation,
                status="ok" if _raw_status(raw_artifact) == "ok" else "blocked",
                request_payload=_request_payload(request),
                response_payload=raw_artifact,
                duration_ms=0,
                evidence_refs=(_provider_receipt_ref(raw_artifact),),
            )
            if _raw_status(raw_artifact) != "ok":
                return _decision(
                    request,
                    "blocked",
                    artifact_ref=artifact_ref,
                    content_digest=content_digest,
                    artifact_receipt=artifact_receipt,
                    reason_codes=("artifact_provider_blocked",),
                    diagnostics=diagnostics,
                )

        if artifact_receipt is None:
            return _decision(
                request,
                "delivery_intent",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                reason_codes=("artifact_provider_receipt_required",),
                diagnostics=diagnostics,
            )

        if request.channel is None:
            return _decision(
                request,
                "delivery_intent",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                reason_codes=("channel_delivery_receipt_required",),
                diagnostics=diagnostics,
            )
        channel_live = self.config.live_channel_delivery_enabled and channel_provider is not None
        channel_fake = (
            self.config.local_fake_channel_delivery_enabled and channel_provider is not None
        )
        if (not channel_live and not channel_fake) or channel_provider is None:
            return _decision(
                request,
                "delivery_intent",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                reason_codes=("channel_delivery_receipt_required",),
                diagnostics=diagnostics,
            )
        if channel_live:
            if not _is_trusted_live_provider(channel_provider):
                return _decision(
                    request,
                    "blocked",
                    artifact_ref=artifact_ref,
                    content_digest=content_digest,
                    artifact_receipt=artifact_receipt,
                    reason_codes=("live_channel_provider_untrusted",),
                    diagnostics=diagnostics,
                )
            channel_used_live = True
        elif not _is_local_fake_provider(channel_provider):
            return _decision(
                request,
                "blocked",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                reason_codes=("local_fake_channel_provider_untrusted",),
                diagnostics=diagnostics,
            )
        # Mixed-mode is unsupported: delivered_live requires BOTH the artifact
        # provider AND the channel provider to be live. A live+fake mix is
        # ambiguous (partial real-world side effect), so block with NO delivery
        # before invoking the channel provider. Pure fake+fake stays
        # delivered_local_fake; pure live+live stays delivered_live.
        if artifact_used_live != channel_used_live:
            return _decision(
                request,
                "blocked",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                reason_codes=("mixed_provider_mode_unsupported",),
                diagnostics=diagnostics,
            )
        live_path = artifact_used_live and channel_used_live
        delivery_request = request.model_copy(
            update={"artifact_refs": (artifact_ref,) if artifact_ref is not None else request.artifact_refs}
        )
        try:
            receipt = channel_provider.deliver(delivery_request)
        except Exception as exc:
            return _decision(
                request,
                "blocked",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                reason_codes=(
                    "live_channel_provider_error"
                    if channel_live
                    else "local_fake_channel_provider_error",
                ),
                diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        if receipt.status != "sent":
            return _decision(
                request,
                "blocked",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                delivery_receipt=receipt,
                reason_codes=("channel_delivery_failed",),
                diagnostics=diagnostics,
            )
        if not receipt.provider_message_id:
            return _decision(
                request,
                "blocked",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                delivery_receipt=receipt,
                reason_codes=("channel_delivery_receipt_missing",),
                diagnostics=diagnostics,
            )
        if _receipt_mismatch(delivery_request, receipt):
            return _decision(
                request,
                "blocked",
                artifact_ref=artifact_ref,
                content_digest=content_digest,
                artifact_receipt=artifact_receipt,
                delivery_receipt=receipt,
                reason_codes=("channel_delivery_receipt_mismatch",),
                diagnostics=diagnostics,
            )
        return _decision(
            request,
            "delivered_live" if live_path else "delivered_local_fake",
            artifact_ref=artifact_ref,
            content_digest=content_digest,
            artifact_receipt=artifact_receipt,
            delivery_receipt=receipt,
            delivery_claim_allowed=True,
            reason_codes=(
                ("live_delivery_receipt_recorded",)
                if live_path
                else ("local_fake_delivery_receipt_recorded",)
            ),
            diagnostics=diagnostics,
        )


def _decision(
    request: FileDeliveryRequest,
    status: FileDeliveryStatus,
    *,
    artifact_ref: str | None = None,
    content_digest: str | None = None,
    artifact_receipt: ProviderReceipt | None = None,
    delivery_receipt: ChannelDeliveryReceipt | None = None,
    delivery_claim_allowed: bool = False,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> FileDeliveryDecision:
    decision = FileDeliveryDecision(
        status=status,
        operation=request.operation,
        requestId=request.request_id,
        artifactRef=artifact_ref,
        contentDigest=content_digest,
        artifactReceipt=artifact_receipt,
        deliveryReceipt=delivery_receipt,
        deliveryClaimAllowed=delivery_claim_allowed,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata({**dict(request.metadata), **dict(diagnostics)}),
        authorityFlags=FileDeliveryAuthorityFlags(),
    )
    object.__setattr__(
        decision,
        "_boundary_verification_token",
        (
            _BOUNDARY_VERIFICATION_TOKEN
            if (
                delivery_claim_allowed
                and status in ("delivered_local_fake", "delivered_live")
                and artifact_receipt is not None
                and delivery_receipt is not None
            )
            else None
        ),
    )
    return decision


def _diagnostics(config: FileDeliveryConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeArtifactServiceEnabled": config.local_fake_artifact_service_enabled,
        "localFakeChannelDeliveryEnabled": config.local_fake_channel_delivery_enabled,
        "liveArtifactStorageEnabled": config.live_artifact_storage_enabled,
        "liveChannelDeliveryEnabled": config.live_channel_delivery_enabled,
        "productionStorageWritesEnabled": False,
        "productionChannelDeliveryEnabled": False,
        "routeAttached": False,
    }


def _validation_error(request: FileDeliveryRequest) -> str | None:
    if not request.artifact_refs and not request.file_refs:
        return "artifact_or_file_ref_required"
    if request.operation == "file.send" and not request.artifact_refs:
        return "artifact_ref_required_for_file_send"
    if request.mime_type in _UNSUPPORTED_MIME_TYPES:
        return "unsupported_mime_type"
    for ref in request.file_refs:
        if _is_sealed_ref(ref):
            return "sealed_file_ref_blocked"
        if _is_raw_path_ref(ref):
            return "raw_file_ref_blocked"
    for ref in request.artifact_refs:
        if _is_raw_path_ref(ref) or _is_sealed_ref(ref):
            return "raw_file_ref_blocked"
    return None


def _request_payload(request: FileDeliveryRequest) -> dict[str, object]:
    return {
        "operation": request.operation,
        "requestId": request.request_id,
        "sessionKey": request.session_key,
        "artifactRefs": request.artifact_refs,
        "fileRefs": request.file_refs,
        "filename": request.filename,
        "mimeType": request.mime_type,
        "contentDigest": request.content_digest,
    }


def _artifact_ref_from_raw(raw: Mapping[str, object]) -> str | None:
    value = raw.get("artifactRef")
    if isinstance(value, str):
        return _digest_artifact_ref(value)
    return None


def _content_digest_from_raw(raw: Mapping[str, object]) -> str | None:
    value = raw.get("contentDigest")
    if isinstance(value, str):
        return _public_digest(value)
    return None


def _raw_status(raw: Mapping[str, object]) -> str:
    value = raw.get("status")
    return value if value in {"ok", "blocked", "error"} else "ok"  # type: ignore[return-value]


def _provider_receipt_ref(raw: Mapping[str, object]) -> str:
    value = raw.get("receiptId")
    if isinstance(value, str):
        return value
    return provider_digest(sanitize_provider_payload(raw))


def _safe_delivery_receipt(receipt: ChannelDeliveryReceipt | None) -> dict[str, object] | None:
    if receipt is None:
        return None
    return {
        "receiptId": _public_ref(receipt.receipt_id, "receipt"),
        "requestId": _public_ref(receipt.request_id, "request"),
        "channel": {
            "type": receipt.channel.type,
            "channelId": _public_ref(receipt.channel.channel_id, "channel"),
        },
        "status": receipt.status,
        "providerMessageId": (
            None if receipt.provider_message_id is None else _safe_text(receipt.provider_message_id)
        ),
        "artifactRefs": [_public_ref(ref, "artifact") for ref in receipt.artifact_refs],
        "fileRefs": [_public_ref(ref, "file") for ref in receipt.file_refs],
    }


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_METADATA_KEY_MARKERS) or _contains_private_text(str(key)):
            continue
        safe_key = _safe_metadata_key(str(key))
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean:
                safe[safe_key] = clean[:240]
        elif isinstance(value, bool | int | float) or value is None:
            safe[safe_key] = value
    return safe


def _safe_metadata_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]", "_", value.strip())[:80]
    if not clean or _contains_private_text(clean):
        return f"metadata:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return clean


def _digest_artifact_ref(value: str) -> str:
    if re.fullmatch(r"artifact:[a-f0-9]{16}", value):
        return value
    return f"artifact:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _receipt_mismatch(request: FileDeliveryRequest, receipt: ChannelDeliveryReceipt) -> bool:
    if receipt.request_id != request.request_id:
        return True
    if request.channel is None:
        return True
    if receipt.channel.type != request.channel.type or receipt.channel.channel_id != request.channel.channel_id:
        return True
    if not set(receipt.artifact_refs).intersection(request.artifact_refs):
        return True
    if request.file_refs and not set(receipt.file_refs).intersection(request.file_refs):
        return True
    return False


def _safe_provider_error(exc: BaseException) -> str:
    _ = exc
    return "[redacted-provider-error]"


def _is_local_fake_provider(provider: object) -> bool:
    return getattr(provider, "openmagi_local_fake_provider", False) is True


def _is_trusted_live_provider(provider: object) -> bool:
    return getattr(provider, "openmagi_live_provider", False) is True


def _is_raw_path_ref(value: str) -> bool:
    text = value.strip()
    return (
        text.startswith(("/", "~"))
        or "\\" in text
        or ".." in text.split("/")
        or _PRIVATE_PATH_RE.search(text) is not None
    )


def _is_sealed_ref(value: str) -> bool:
    return value.startswith("sealed:") or value in {
        "SOUL.md",
        "TOOLS.md",
        "AGENTS.md",
        "CLAUDE.md",
        "HEARTBEAT.md",
    }


def _public_ref(value: str, prefix: str) -> str:
    text = _safe_text(str(value)).strip()
    if text and _REF_RE.fullmatch(text):
        return text[:180]
    return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _public_digest(value: str) -> str:
    if _SHA256_RE.fullmatch(value):
        return value
    return provider_digest(value)


def _safe_text(value: str) -> str:
    if _contains_private_text(value):
        return "[redacted]"
    return value[:240]


def _contains_private_text(value: str) -> bool:
    return bool(
        _PRIVATE_PATH_RE.search(value)
        or _SECRET_TEXT_RE.search(value)
        or _RAW_PRIVATE_RE.search(value)
    )


__all__ = [
    "FileChannelDeliveryPort",
    "FileDeliveryAuthorityFlags",
    "FileDeliveryBoundary",
    "FileDeliveryConfig",
    "FileDeliveryDecision",
    "FileDeliveryProviderPort",
    "FileDeliveryRequest",
    "LiveFileArtifactProviderPort",
    "LiveFileChannelDeliveryPort",
]
