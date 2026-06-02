from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


ChannelType = Literal["web", "app", "telegram", "discord"]
DeliveryStatus = Literal["queued", "sent", "failed", "skipped"]
_CHANNEL_CONTRACT_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)


class _ChannelContractModel(BaseModel):
    model_config = _CHANNEL_CONTRACT_MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class _FrozenMetadata(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = {key: _freeze_metadata_value(nested) for key, nested in value.items()}

    def __getitem__(self, key: str) -> object:
        return self._value[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return _thaw_metadata_value(self) == _thaw_metadata_value(other)
        return False

    def __repr__(self) -> str:
        return repr(_thaw_metadata_value(self))


def _freeze_metadata_value(value: object) -> object:
    if isinstance(value, _FrozenMetadata):
        return value
    if isinstance(value, Mapping):
        return _FrozenMetadata(value)
    if isinstance(value, list):
        return tuple(_freeze_metadata_value(item) for item in value)
    if isinstance(value, tuple | set | frozenset | bytes | bytearray):
        raise ValueError("metadata must contain only JSON-like values")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata must contain only finite JSON-like float values")
        return value
    if isinstance(value, str | bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    raise ValueError("metadata must contain only JSON-like values")


def _thaw_metadata_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_metadata_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_metadata_value(item) for item in value]
    if isinstance(value, list):
        return [_thaw_metadata_value(item) for item in value]
    return value


class ChannelRef(_ChannelContractModel):
    type: ChannelType
    channel_id: str = Field(alias="channelId")

    @field_validator("channel_id")
    @classmethod
    def _reject_empty_channel_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("channelId must be non-empty")
        return value


class ChannelAdapterManifest(_ChannelContractModel):
    channel_type: ChannelType = Field(alias="channelType")
    display_name: str = Field(alias="displayName")
    supports_sse: bool = Field(default=False, alias="supportsSse")
    supports_polling: bool = Field(default=False, alias="supportsPolling")
    supports_stale_webhook_mitigation: bool = Field(
        default=False,
        alias="supportsStaleWebhookMitigation",
    )
    supports_artifact_delivery: bool = Field(default=True, alias="supportsArtifactDelivery")
    supports_file_delivery: bool = Field(default=True, alias="supportsFileDelivery")
    supports_cron_delivery: bool = Field(default=False, alias="supportsCronDelivery")
    max_text_chars: int = Field(alias="maxTextChars")
    default_enabled: bool = Field(default=False, alias="defaultEnabled")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @field_validator("display_name")
    @classmethod
    def _reject_empty_display_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("displayName must be non-empty")
        return value

    @field_validator("max_text_chars")
    @classmethod
    def _validate_max_text_chars(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("maxTextChars must be positive")
        return value

    @model_validator(mode="after")
    def _validate_traffic_free_manifest(self) -> ChannelAdapterManifest:
        if self.default_enabled or self.traffic_attached or self.execution_attached:
            raise ValueError("Phase 6 channel manifests must be disabled and traffic-free by default")
        return self


class ChannelDeliveryRequest(_ChannelContractModel):
    request_id: str = Field(alias="requestId")
    channel: ChannelRef
    session_key: str = Field(alias="sessionKey")
    text: str | None = None
    content: str | None = None
    locale: str | None = None
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    file_refs: tuple[str, ...] = Field(default=(), alias="fileRefs")
    metadata: Mapping[str, object] = Field(default_factory=dict, validate_default=True)

    @field_validator("request_id", "session_key")
    @classmethod
    def _reject_empty_required_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("requestId and sessionKey must be non-empty")
        return value

    @field_validator("metadata", mode="after")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _FrozenMetadata(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        thawed = _thaw_metadata_value(value)
        if isinstance(thawed, dict):
            return thawed
        return {}


class ChannelDeliveryReceipt(_ChannelContractModel):
    receipt_id: str = Field(alias="receiptId")
    request_id: str = Field(alias="requestId")
    channel: ChannelRef
    status: DeliveryStatus
    provider_message_id: str | None = Field(default=None, alias="providerMessageId")
    delivered_at: str | None = Field(default=None, alias="deliveredAt")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    file_refs: tuple[str, ...] = Field(default=(), alias="fileRefs")
    transcript_event_id: str | None = Field(default=None, alias="transcriptEventId")

    @field_validator("receipt_id", "request_id")
    @classmethod
    def _reject_empty_required_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("receiptId and requestId must be non-empty")
        return value


_DEFAULT_CHANNEL_ADAPTER_MANIFESTS: tuple[ChannelAdapterManifest, ...] = (
    ChannelAdapterManifest(
        channelType="web",
        displayName="Web Chat",
        supportsSse=True,
        supportsArtifactDelivery=True,
        supportsFileDelivery=True,
        maxTextChars=16_000,
    ),
    ChannelAdapterManifest(
        channelType="app",
        displayName="Mobile App",
        supportsSse=True,
        supportsArtifactDelivery=True,
        supportsFileDelivery=True,
        supportsCronDelivery=True,
        maxTextChars=16_000,
    ),
    ChannelAdapterManifest(
        channelType="telegram",
        displayName="Telegram",
        supportsPolling=True,
        supportsStaleWebhookMitigation=True,
        supportsArtifactDelivery=True,
        supportsFileDelivery=True,
        maxTextChars=4_096,
    ),
    ChannelAdapterManifest(
        channelType="discord",
        displayName="Discord",
        supportsArtifactDelivery=True,
        supportsFileDelivery=True,
        maxTextChars=2_000,
    ),
)


def channel_adapter_manifests() -> tuple[ChannelAdapterManifest, ...]:
    return tuple(manifest.model_copy(deep=True) for manifest in _DEFAULT_CHANNEL_ADAPTER_MANIFESTS)
