from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef


ArtifactOperation = Literal[
    "artifact.create",
    "artifact.read",
    "artifact.list",
    "artifact.update",
    "artifact.delete",
    "artifact.import_child",
    "file.deliver",
    "file.send",
]
ArtifactBoundaryStatus = Literal[
    "disabled",
    "artifact_intent",
    "artifact_recorded_local_fake",
    "delivery_intent",
    "delivery_recorded_local_fake",
    "channel_absent",
    "unsupported_channel",
    "blocked",
]
ArtifactKind = Literal[
    "document",
    "spreadsheet",
    "file",
    "rendered_preview",
    "delivery_receipt",
    "child_handoff",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,160}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test|artifact)?[-_A-Za-z0-9]{8,}|"
    r"\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/home/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+|s3://[^,\s\"']+|"
    r"gs://[^,\s\"']+|supabase://[^,\s\"']+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args|browser|child)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"reasoning[_ -]?trace|model[_ -]?internal|authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_KEY_MARKERS = (
    "raw",
    "secret",
    "token",
    "credential",
    "key",
    "password",
    "cookie",
    "path",
    "hidden",
    "transcript",
    "toollog",
)


class ArtifactServicePort(Protocol):
    openmagi_local_fake_provider: bool

    def handle_artifact_request(
        self,
        request: ArtifactChannelDeliveryRequest,
    ) -> ArtifactServiceResult: ...


class ChannelDeliveryPort(Protocol):
    openmagi_local_fake_provider: bool

    def deliver(self, request: ArtifactChannelDeliveryRequest) -> ChannelDeliveryReceipt: ...


class ArtifactChannelDeliveryConfig(BaseModel):
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
    production_storage_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionStorageWritesEnabled",
    )
    production_channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelDeliveryEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class ArtifactChannelAuthorityFlags(BaseModel):
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

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "adk_artifact_service_attached",
        "artifact_written",
        "channel_delivery_performed",
        "production_storage_written",
        "production_channel_write",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ArtifactRecord(BaseModel):
    model_config = _MODEL_CONFIG

    artifact_id: str = Field(alias="artifactId")
    kind: ArtifactKind
    title: str
    filename: str
    mime_type: str = Field(alias="mimeType")
    content_digest: str = Field(alias="contentDigest")
    artifact_ref: str = Field(alias="artifactRef")
    source_refs: tuple[str, ...] = Field(default=(), alias="sourceRefs")
    provenance_refs: tuple[str, ...] = Field(default=(), alias="provenanceRefs")

    @field_validator("artifact_id", "artifact_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("source_refs", "provenance_refs")
    @classmethod
    def _validate_ref_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("title")
    @classmethod
    def _sanitize_title(cls, value: str) -> str:
        clean = _sanitize_public_text(value)
        if not clean:
            raise ValueError("artifact title must be non-empty")
        return clean[:120]

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        return _safe_filename(value)

    @field_validator("mime_type")
    @classmethod
    def _validate_mime_type(cls, value: str) -> str:
        if "/" not in value or _SECRET_TEXT_RE.search(value):
            raise ValueError("mimeType must be public media type")
        return value[:120]

    @field_validator("content_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "artifactId": _public_ref(self.artifact_id, prefix="artifact"),
            "kind": self.kind,
            "title": _sanitize_public_text(self.title)[:120],
            "filename": _public_filename(self.filename),
            "mimeType": _public_mime_type(self.mime_type),
            "contentDigest": _public_digest(self.content_digest),
            "artifactRef": _public_ref(self.artifact_ref, prefix="artifact"),
            "sourceRefs": [_public_ref(ref, prefix="source") for ref in self.source_refs],
            "provenanceRefs": [
                _public_ref(ref, prefix="provenance") for ref in self.provenance_refs
            ],
        }


class ArtifactServiceResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["ok", "not_found", "blocked", "error"]
    artifact: ArtifactRecord | None = None
    receipt_ref: str = Field(alias="receiptRef")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )

    @field_validator("receipt_ref")
    @classmethod
    def _validate_receipt_ref(cls, value: str) -> str:
        return _safe_ref(value)


class ArtifactChannelDeliveryRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: ArtifactOperation
    request_id: str = Field(alias="requestId")
    session_key: str = Field(alias="sessionKey")
    channel: ChannelRef | None = None
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    file_refs: tuple[str, ...] = Field(default=(), alias="fileRefs")
    filename: str | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")
    content_digest: str | None = Field(default=None, alias="contentDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "session_key")
    @classmethod
    def _validate_ids(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("artifact_refs", "file_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("filename")
    @classmethod
    def _validate_optional_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_filename(value)

    @field_validator("mime_type")
    @classmethod
    def _validate_optional_mime_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "/" not in value or _SECRET_TEXT_RE.search(value):
            raise ValueError("mimeType must be public media type")
        return value[:120]

    @field_validator("content_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_digest(value)


class ArtifactChannelDeliveryDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ArtifactBoundaryStatus
    operation: ArtifactOperation
    request_id: str = Field(alias="requestId")
    artifact: ArtifactRecord | None = None
    delivery_receipt: ChannelDeliveryReceipt | None = Field(
        default=None,
        alias="deliveryReceipt",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    receipt_ref: str = Field(alias="receiptRef")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: ArtifactChannelAuthorityFlags = Field(
        default_factory=ArtifactChannelAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ArtifactChannelAuthorityFlags()
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
        data["authorityFlags"] = ArtifactChannelAuthorityFlags()
        return type(self).model_validate(data)

    @field_validator("request_id", "receipt_ref")
    @classmethod
    def _validate_refs(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "requestId": self.request_id,
            "artifact": None if self.artifact is None else self.artifact.public_projection(),
            "deliveryReceipt": _safe_delivery_receipt(self.delivery_receipt),
            "reasonCodes": list(self.reason_codes),
            "receiptRef": self.receipt_ref,
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class ArtifactChannelDeliveryBoundary:
    """Disabled-by-default artifact and channel delivery boundary.

    The boundary is live-callable with injected fakes for tests, but it does not
    import or attach ADK ArtifactService, channel transports, routes, or storage.
    """

    def __init__(self, config: ArtifactChannelDeliveryConfig) -> None:
        self.config = config

    def execute(
        self,
        request: ArtifactChannelDeliveryRequest,
        *,
        artifact_service: ArtifactServicePort | None = None,
        channel_provider: ChannelDeliveryPort | None = None,
    ) -> ArtifactChannelDeliveryDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeArtifactServiceEnabled": self.config.local_fake_artifact_service_enabled,
            "localFakeChannelDeliveryEnabled": self.config.local_fake_channel_delivery_enabled,
            "productionStorageWritesEnabled": False,
            "productionChannelDeliveryEnabled": False,
            "routeAttached": False,
        }
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                reason_codes=("artifact_channel_delivery_disabled",),
                diagnostics=diagnostics,
            )

        if request.operation.startswith("artifact."):
            return self._artifact_operation(request, artifact_service, diagnostics)
        return self._delivery_operation(request, channel_provider, diagnostics)

    def consume_file_delivery_decision(
        self,
        request: ArtifactChannelDeliveryRequest,
        file_decision: object,
    ) -> ArtifactChannelDeliveryDecision:
        from openmagi_core_agent.artifacts.file_delivery import FileDeliveryDecision

        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeArtifactServiceEnabled": self.config.local_fake_artifact_service_enabled,
            "localFakeChannelDeliveryEnabled": self.config.local_fake_channel_delivery_enabled,
            "productionStorageWritesEnabled": False,
            "productionChannelDeliveryEnabled": False,
            "routeAttached": False,
            "source": "file_delivery_decision",
        }
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                reason_codes=("artifact_channel_delivery_disabled",),
                diagnostics=diagnostics,
            )
        if not isinstance(file_decision, FileDeliveryDecision):
            return _decision(
                request,
                "blocked",
                reason_codes=("file_delivery_decision_invalid",),
                diagnostics=diagnostics,
            )
        if file_decision.status != "delivered_local_fake":
            return _decision(
                request,
                "delivery_intent",
                reason_codes=("file_delivery_receipt_required",),
                diagnostics=diagnostics,
            )
        if (
            not file_decision.boundary_verified
            or file_decision.artifact_receipt is None
            or file_decision.artifact_ref is None
        ):
            return _decision(
                request,
                "blocked",
                reason_codes=("file_delivery_decision_unverified",),
                diagnostics=diagnostics,
            )
        receipt = file_decision.delivery_receipt
        claim_allowed = file_decision.delivery_claim_allowed is True
        if receipt is None or not claim_allowed:
            return _decision(
                request,
                "delivery_intent",
                reason_codes=("file_delivery_receipt_required",),
                diagnostics=diagnostics,
            )
        if not isinstance(receipt, ChannelDeliveryReceipt):
            return _decision(
                request,
                "blocked",
                reason_codes=("file_delivery_receipt_invalid",),
                diagnostics=diagnostics,
            )
        if receipt.status != "sent":
            return _decision(
                request,
                "blocked",
                delivery_receipt=receipt,
                reason_codes=("channel_delivery_failed",),
                receipt_ref=receipt.receipt_id,
                diagnostics=diagnostics,
            )
        if _file_delivery_receipt_mismatch(request, file_decision, receipt):
            return _decision(
                request,
                "blocked",
                delivery_receipt=receipt,
                reason_codes=("file_delivery_receipt_mismatch",),
                receipt_ref=receipt.receipt_id,
                diagnostics=diagnostics,
            )
        if not receipt.provider_message_id:
            return _decision(
                request,
                "blocked",
                delivery_receipt=receipt,
                reason_codes=("channel_delivery_receipt_missing",),
                receipt_ref=receipt.receipt_id,
                diagnostics=diagnostics,
            )
        return _decision(
            request,
            "delivery_recorded_local_fake",
            delivery_receipt=receipt,
            reason_codes=("file_delivery_receipt_consumed",),
            receipt_ref=receipt.receipt_id,
            diagnostics=diagnostics,
        )

    def _artifact_operation(
        self,
        request: ArtifactChannelDeliveryRequest,
        artifact_service: ArtifactServicePort | None,
        diagnostics: Mapping[str, object],
    ) -> ArtifactChannelDeliveryDecision:
        if not self.config.local_fake_artifact_service_enabled or artifact_service is None:
            return _decision(
                request,
                "artifact_intent",
                reason_codes=("local_artifact_service_disabled",),
                diagnostics=diagnostics,
            )
        if not _is_local_fake_provider(artifact_service):
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_artifact_service_untrusted",),
                diagnostics=diagnostics,
            )
        try:
            result = artifact_service.handle_artifact_request(request)
        except Exception as exc:
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_artifact_service_error",),
                diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        status: ArtifactBoundaryStatus = (
            "artifact_recorded_local_fake" if result.status == "ok" else "blocked"
        )
        reason_codes = (
            ("local_fake_artifact_service_receipt_only",)
            if result.status == "ok"
            else ("artifact_service_result_blocked",)
        )
        return _decision(
            request,
            status,
            artifact=result.artifact,
            reason_codes=reason_codes,
            receipt_ref=result.receipt_ref,
            diagnostics={**diagnostics, **_safe_metadata(result.diagnostic_metadata)},
        )

    def _delivery_operation(
        self,
        request: ArtifactChannelDeliveryRequest,
        channel_provider: ChannelDeliveryPort | None,
        diagnostics: Mapping[str, object],
    ) -> ArtifactChannelDeliveryDecision:
        if request.channel is None:
            return _decision(
                request,
                "channel_absent",
                reason_codes=("channel_required_for_delivery",),
                diagnostics=diagnostics,
            )
        if request.operation == "file.send" and request.channel.type in {"web", "app"}:
            return _decision(
                request,
                "unsupported_channel",
                reason_codes=("file_send_channel_unsupported",),
                diagnostics=diagnostics,
            )
        if request.operation in {"file.deliver", "file.send"}:
            return _decision(
                request,
                "delivery_intent",
                reason_codes=("file_delivery_boundary_required",),
                diagnostics=diagnostics,
            )
        if not self.config.local_fake_channel_delivery_enabled or channel_provider is None:
            return _decision(
                request,
                "delivery_intent",
                reason_codes=("local_channel_delivery_disabled",),
                diagnostics=diagnostics,
            )
        if not _is_local_fake_provider(channel_provider):
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_channel_provider_untrusted",),
                diagnostics=diagnostics,
            )
        try:
            receipt = channel_provider.deliver(request)
        except Exception as exc:
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_channel_provider_error",),
                diagnostics={**diagnostics, "providerError": _safe_provider_error(exc)},
            )
        if receipt.status != "sent":
            return _decision(
                request,
                "blocked",
                delivery_receipt=receipt,
                reason_codes=("channel_delivery_failed",),
                receipt_ref=receipt.receipt_id,
                diagnostics=diagnostics,
            )
        if not receipt.provider_message_id:
            return _decision(
                request,
                "blocked",
                delivery_receipt=receipt,
                reason_codes=("channel_delivery_receipt_missing",),
                receipt_ref=receipt.receipt_id,
                diagnostics=diagnostics,
            )
        return _decision(
            request,
            "delivery_recorded_local_fake",
            delivery_receipt=receipt,
            reason_codes=("local_fake_channel_receipt_only",),
            receipt_ref=receipt.receipt_id,
            diagnostics=diagnostics,
        )


def _decision(
    request: ArtifactChannelDeliveryRequest,
    status: ArtifactBoundaryStatus,
    *,
    artifact: ArtifactRecord | None = None,
    delivery_receipt: ChannelDeliveryReceipt | None = None,
    reason_codes: tuple[str, ...],
    receipt_ref: str | None = None,
    diagnostics: Mapping[str, object],
) -> ArtifactChannelDeliveryDecision:
    return ArtifactChannelDeliveryDecision(
        status=status,
        operation=request.operation,
        requestId=request.request_id,
        artifact=artifact,
        deliveryReceipt=delivery_receipt,
        reasonCodes=reason_codes,
        receiptRef=receipt_ref or _receipt_ref(request, status),
        diagnosticMetadata=_safe_metadata({**dict(request.metadata), **dict(diagnostics)}),
        authorityFlags=ArtifactChannelAuthorityFlags(),
    )


def _safe_delivery_receipt(receipt: ChannelDeliveryReceipt | None) -> dict[str, object] | None:
    if receipt is None:
        return None
    return {
        "receiptId": _public_ref(receipt.receipt_id, prefix="receipt"),
        "requestId": _public_ref(receipt.request_id, prefix="request"),
        "channel": {
            "type": receipt.channel.type,
            "channelId": _sanitize_public_text(receipt.channel.channel_id),
        },
        "status": receipt.status,
        "providerMessageId": (
            None
            if receipt.provider_message_id is None
            else _sanitize_public_text(receipt.provider_message_id)
        ),
        "artifactRefs": [_public_ref(item, prefix="artifact") for item in receipt.artifact_refs],
        "fileRefs": [_public_ref(item, prefix="file") for item in receipt.file_refs],
        "transcriptEventId": (
            None
            if receipt.transcript_event_id is None
            else _public_ref(receipt.transcript_event_id, prefix="event")
        ),
    }


def _file_delivery_receipt_mismatch(
    request: ArtifactChannelDeliveryRequest,
    file_decision: object,
    receipt: ChannelDeliveryReceipt,
) -> bool:
    if receipt.request_id != request.request_id:
        return True
    if request.channel is None:
        return True
    if receipt.channel.type != request.channel.type or receipt.channel.channel_id != request.channel.channel_id:
        return True
    artifact_ref = getattr(file_decision, "artifact_ref", None)
    if isinstance(artifact_ref, str) and request.artifact_refs and artifact_ref not in request.artifact_refs:
        return True
    if isinstance(artifact_ref, str) and receipt.artifact_refs and artifact_ref not in receipt.artifact_refs:
        return True
    if request.file_refs and not set(receipt.file_refs).intersection(request.file_refs):
        return True
    return False


def _is_local_fake_provider(provider: object) -> bool:
    return getattr(provider, "openmagi_local_fake_provider", False) is True


def _safe_ref(value: str) -> str:
    clean = _sanitize_public_text(value.strip())
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("reference must be non-empty public identifier")
    return clean[:180]


def _public_ref(value: str, *, prefix: str) -> str:
    try:
        return _safe_ref(str(value))
    except ValueError:
        return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _safe_filename(value: str) -> str:
    clean = _sanitize_public_text(value.strip())
    path = PurePosixPath(clean)
    if not clean or path.name != clean or clean in {".", ".."} or ".." in path.parts:
        raise ValueError("filename must be a safe basename")
    return clean[:160]


def _public_filename(value: str) -> str:
    try:
        return _safe_filename(value)
    except ValueError:
        return "redacted-file"


def _public_mime_type(value: str) -> str:
    clean = _sanitize_public_text(value)
    if "/" not in clean:
        return "application/octet-stream"
    return clean[:120]


def _safe_digest(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("contentDigest must use sha256 hex digest")
    return value


def _public_digest(value: str) -> str:
    if _SHA256_RE.fullmatch(value):
        return value
    return f"sha256:{hashlib.sha256(str(value).encode('utf-8')).hexdigest()}"


def _receipt_ref(
    request: ArtifactChannelDeliveryRequest,
    status: ArtifactBoundaryStatus,
) -> str:
    seed = "|".join(
        (
            request.request_id,
            request.operation,
            status,
            ",".join(request.artifact_refs),
            ",".join(request.file_refs),
        )
    )
    return f"artifact-delivery-receipt:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_METADATA_KEY_MARKERS) or _contains_private_text(str(key)):
            continue
        safe_key = _safe_metadata_key(str(key))
        if isinstance(value, str):
            clean = _sanitize_public_text(value)
            if clean and clean != "[redacted]":
                safe[safe_key] = clean
        elif isinstance(value, bool | int | float) or value is None:
            safe[safe_key] = value
        elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            safe_values = [
                _sanitize_public_text(item)
                for item in value
                if isinstance(item, str) and _sanitize_public_text(item) not in {"", "[redacted]"}
            ]
            if safe_values:
                safe[safe_key] = safe_values[:20]
    return safe


def _safe_metadata_key(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]", "_", value.strip())[:80]
    if not clean or _contains_private_text(clean):
        return f"metadata:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    return clean


def _sanitize_public_text(value: str) -> str:
    lines = [
        line
        for line in value.splitlines()
        if _RAW_PRIVATE_LINE_RE.search(line) is None
    ]
    value = "\n".join(lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", value)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()[:240]


def _contains_private_text(value: str) -> bool:
    return bool(
        _SECRET_TEXT_RE.search(value)
        or _PRIVATE_PATH_RE.search(value)
        or _RAW_PRIVATE_LINE_RE.search(value)
    )


def _safe_provider_error(exc: BaseException) -> str:
    return _sanitize_public_text(str(exc)) or "[redacted-provider-error]"


__all__ = [
    "ArtifactChannelDeliveryBoundary",
    "ArtifactChannelDeliveryConfig",
    "ArtifactChannelDeliveryDecision",
    "ArtifactChannelDeliveryRequest",
    "ArtifactChannelAuthorityFlags",
    "ArtifactRecord",
    "ArtifactServicePort",
    "ArtifactServiceResult",
    "ChannelDeliveryPort",
]
