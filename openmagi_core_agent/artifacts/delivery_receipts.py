from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from openmagi_core_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef
from openmagi_core_agent.ops.safety import (
    require_digest,
    require_safe_ref,
    safe_metadata,
    serialize_safe_value,
)
from openmagi_core_agent.storage.durable_store import DurableRecord


ArtifactDeliveryOperation = Literal["file.deliver", "file.send", "artifact.deliver"]
ArtifactDeliveryReceiptStatus = Literal["recorded_local_fake", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_ZERO_DIGEST = "sha256:" + "0" * 64


class ArtifactDeliveryReceiptConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_receipt_index_enabled: bool = Field(
        default=False,
        alias="localFakeReceiptIndexEnabled",
    )
    production_storage_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionStorageWritesEnabled",
    )
    production_channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="productionChannelDeliveryEnabled",
    )
    user_visible_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleDeliveryEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["productionStorageWritesEnabled"] = False
        values.pop("production_storage_writes_enabled", None)
        values["productionChannelDeliveryEnabled"] = False
        values.pop("production_channel_delivery_enabled", None)
        values["userVisibleDeliveryEnabled"] = False
        values.pop("user_visible_delivery_enabled", None)
        values["routeAttached"] = False
        values.pop("route_attached", None)
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        payload["productionStorageWritesEnabled"] = False
        payload["productionChannelDeliveryEnabled"] = False
        payload["userVisibleDeliveryEnabled"] = False
        payload["routeAttached"] = False
        _ = deep
        return type(self).model_validate(payload)


class ArtifactDeliveryAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
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
    user_visible_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleDeliveryAllowed",
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

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer(
        "adk_artifact_service_attached",
        "channel_delivery_performed",
        "production_storage_written",
        "production_channel_write",
        "user_visible_delivery_allowed",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ArtifactDeliveryReceiptRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    artifact_id: str = Field(alias="artifactId")
    artifact_ref: str = Field(alias="artifactRef")
    content_digest: str = Field(alias="contentDigest")
    operation: ArtifactDeliveryOperation
    channel: ChannelRef
    render_receipt_digest: str | None = Field(default=None, alias="renderReceiptDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "artifact_id", "artifact_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="artifact delivery ref")

    @field_validator("content_digest", "policy_snapshot_digest", "render_receipt_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: ChannelRef) -> ChannelRef:
        require_safe_ref(value.channel_id, field_name="channelId")
        return value


class ArtifactDeliveryReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    artifact_id: str = Field(alias="artifactId")
    artifact_ref: str = Field(alias="artifactRef")
    content_digest: str = Field(alias="contentDigest")
    operation: ArtifactDeliveryOperation
    channel: ChannelRef
    status: ArtifactDeliveryReceiptStatus
    delivery_claim_allowed: bool = Field(default=False, alias="deliveryClaimAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    channel_receipt_digest: str | None = Field(default=None, alias="channelReceiptDigest")
    render_receipt_digest: str | None = Field(default=None, alias="renderReceiptDigest")
    delivered_at: datetime = Field(alias="deliveredAt")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    request_digest: str = Field(default=_ZERO_DIGEST, alias="requestDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    authority_flags: ArtifactDeliveryAuthorityFlags = Field(
        default_factory=ArtifactDeliveryAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = ArtifactDeliveryAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(by_alias=True, mode="python")
        if update:
            payload.update(update)
        payload["authorityFlags"] = ArtifactDeliveryAuthorityFlags()
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("request_id", "artifact_id", "artifact_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return require_safe_ref(value, field_name="artifact delivery receipt ref")

    @field_validator(
        "content_digest",
        "policy_snapshot_digest",
        "request_digest",
        "channel_receipt_digest",
        "render_receipt_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(require_safe_ref(item, field_name="reason code") for item in value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, value: ChannelRef) -> ChannelRef:
        require_safe_ref(value.channel_id, field_name="channelId")
        return value

    @model_validator(mode="after")
    def _validate_claim_state(self) -> Self:
        if self.status == "recorded_local_fake" and self.channel_receipt_digest is None:
            raise ValueError("recorded receipt requires channel receipt")
        if self.status == "recorded_local_fake" and self.request_digest == _ZERO_DIGEST:
            raise ValueError("recorded receipt requires concrete request digest")
        if self.delivery_claim_allowed and (
            self.status != "recorded_local_fake" or self.channel_receipt_digest is None
        ):
            raise ValueError("claim requires recorded receipt")
        return self

    @property
    def delivery_receipt_digest(self) -> str:
        return _digest_json(self._digest_payload())

    def public_projection(self) -> dict[str, object]:
        return {
            "type": "artifact_delivery_receipt",
            "requestId": self.request_id,
            "artifactId": self.artifact_id,
            "artifactRef": self.artifact_ref,
            "contentDigest": self.content_digest,
            "operation": self.operation,
            "channel": _safe_channel_projection(self.channel),
            "status": self.status,
            "deliveryClaimAllowed": self.delivery_claim_allowed,
            "reasonCodes": list(self.reason_codes),
            "channelReceiptDigest": self.channel_receipt_digest,
            "renderReceiptDigest": self.render_receipt_digest,
            "deliveredAt": _iso_z(self.delivered_at),
            "policySnapshotDigest": self.policy_snapshot_digest,
            "requestDigest": self.request_digest,
            "deliveryReceiptDigest": self.delivery_receipt_digest,
            "metadata": {
                key: serialize_safe_value(value)
                for key, value in safe_metadata(self.metadata).items()
            },
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }

    def to_durable_metadata_record(self, *, record_id: str | None = None) -> DurableRecord:
        _ = record_id
        metadata: dict[str, object] = {
            "requestDigest": self.request_digest,
            "deliveryReceiptDigest": self.delivery_receipt_digest,
        }
        if self.channel_receipt_digest:
            metadata["channelReceiptDigest"] = self.channel_receipt_digest
        if self.render_receipt_digest:
            metadata["renderReceiptDigest"] = self.render_receipt_digest
        return DurableRecord(
            collection="delivery_action_receipts",
            recordId="delivery-ref:" + self.delivery_receipt_digest,
            contentDigest=self.delivery_receipt_digest,
            policySnapshotDigest=self.policy_snapshot_digest,
            metadata=metadata,
            createdAt=self.delivered_at,
        )

    def _digest_payload(self) -> dict[str, object]:
        return {
            "requestId": self.request_id,
            "artifactId": self.artifact_id,
            "artifactRef": self.artifact_ref,
            "contentDigest": self.content_digest,
            "operation": self.operation,
            "channel": _safe_channel_projection(self.channel),
            "status": self.status,
            "deliveryClaimAllowed": self.delivery_claim_allowed,
            "reasonCodes": list(self.reason_codes),
            "channelReceiptDigest": self.channel_receipt_digest,
            "renderReceiptDigest": self.render_receipt_digest,
            "deliveredAt": _iso_z(self.delivered_at),
            "policySnapshotDigest": self.policy_snapshot_digest,
            "requestDigest": self.request_digest,
            "metadata": {
                key: serialize_safe_value(value)
                for key, value in safe_metadata(self.metadata).items()
            },
        }


class ArtifactDeliveryReceiptBoundary:
    def __init__(
        self,
        config: ArtifactDeliveryReceiptConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.config = ArtifactDeliveryReceiptConfig.model_validate(config or {})

    def record(
        self,
        request: ArtifactDeliveryReceiptRequest,
        *,
        delivery_decision: object | None = None,
        channel_receipt: ChannelDeliveryReceipt | None = None,
        now: datetime | None = None,
    ) -> ArtifactDeliveryReceipt:
        if not self.config.enabled:
            return self._blocked(
                request,
                reason_codes=("artifact_delivery_receipts_disabled",),
                now=now,
            )
        if not self.config.local_fake_receipt_index_enabled:
            return self._blocked(
                request,
                reason_codes=("local_fake_receipt_index_disabled",),
                now=now,
            )
        if channel_receipt is not None:
            return self._blocked(
                request,
                reason_codes=("trusted_delivery_decision_required",),
                now=now,
            )
        if delivery_decision is None:
            return self._blocked(
                request,
                reason_codes=("channel_delivery_receipt_required",),
                now=now,
            )
        trusted_receipt, block_reason = _trusted_channel_receipt_from_decision(
            request,
            delivery_decision,
        )
        if block_reason is not None:
            return self._blocked(request, reason_codes=(block_reason,), now=now)
        if trusted_receipt is None:
            return self._blocked(
                request,
                reason_codes=("channel_delivery_receipt_required",),
                now=now,
            )

        channel_receipt_digest = _digest_json(
            trusted_receipt.model_dump(by_alias=True, mode="json")
        )
        return ArtifactDeliveryReceipt(
            requestId=request.request_id,
            artifactId=request.artifact_id,
            artifactRef=request.artifact_ref,
            contentDigest=request.content_digest,
            operation=request.operation,
            channel=request.channel,
            status="recorded_local_fake",
            deliveryClaimAllowed=True,
            reasonCodes=("delivery_receipt_recorded_local_fake",),
            channelReceiptDigest=channel_receipt_digest,
            renderReceiptDigest=request.render_receipt_digest,
            deliveredAt=now or datetime.now(UTC),
            policySnapshotDigest=request.policy_snapshot_digest,
            requestDigest=_digest_json(request.model_dump(by_alias=True, mode="json")),
            metadata=request.metadata,
            authorityFlags=ArtifactDeliveryAuthorityFlags(),
        )

    def _blocked(
        self,
        request: ArtifactDeliveryReceiptRequest,
        *,
        reason_codes: tuple[str, ...],
        now: datetime | None,
    ) -> ArtifactDeliveryReceipt:
        return ArtifactDeliveryReceipt(
            requestId=request.request_id,
            artifactId=request.artifact_id,
            artifactRef=request.artifact_ref,
            contentDigest=request.content_digest,
            operation=request.operation,
            channel=request.channel,
            status="blocked",
            deliveryClaimAllowed=False,
            reasonCodes=reason_codes,
            channelReceiptDigest=None,
            renderReceiptDigest=request.render_receipt_digest,
            deliveredAt=now or datetime.now(UTC),
            policySnapshotDigest=request.policy_snapshot_digest,
            requestDigest=_digest_json(request.model_dump(by_alias=True, mode="json")),
            metadata=request.metadata,
            authorityFlags=ArtifactDeliveryAuthorityFlags(),
        )


def _channel_receipt_block_reason(
    request: ArtifactDeliveryReceiptRequest,
    channel_receipt: ChannelDeliveryReceipt,
) -> str | None:
    if channel_receipt.status != "sent":
        return "channel_delivery_failed"
    if not channel_receipt.provider_message_id:
        return "channel_delivery_receipt_missing"
    if channel_receipt.request_id != request.request_id:
        return "channel_delivery_receipt_mismatch"
    if channel_receipt.channel != request.channel:
        return "channel_delivery_receipt_mismatch"
    if request.artifact_ref not in channel_receipt.artifact_refs:
        return "channel_delivery_receipt_mismatch"
    return None


def _trusted_channel_receipt_from_decision(
    request: ArtifactDeliveryReceiptRequest,
    delivery_decision: object,
) -> tuple[ChannelDeliveryReceipt | None, str | None]:
    from openmagi_core_agent.artifacts.file_delivery import FileDeliveryDecision

    if type(delivery_decision) is not FileDeliveryDecision:
        return None, "trusted_delivery_decision_required"
    receipt = delivery_decision.delivery_receipt
    if receipt is None:
        return None, "channel_delivery_receipt_required"
    mismatch_reason = _channel_receipt_block_reason(request, receipt)
    if mismatch_reason is not None:
        return receipt, mismatch_reason
    if (
        not delivery_decision.boundary_verified
        or delivery_decision.status != "delivered_local_fake"
        or delivery_decision.delivery_claim_allowed is not True
        or delivery_decision.artifact_ref != request.artifact_ref
        or delivery_decision.content_digest != request.content_digest
    ):
        return receipt, "trusted_delivery_decision_unverified"
    return receipt, None


def _safe_channel_projection(channel: ChannelRef) -> dict[str, str]:
    return {
        "type": channel.type,
        "channelId": require_safe_ref(channel.channel_id, field_name="channelId"),
    }


def _digest_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _iso_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
