from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.channels.contract import (
    ChannelDeliveryReceipt,
    ChannelDeliveryRequest,
    ChannelType,
)
from magi_agent.transport.tool_preview import sanitize_tool_preview


ArtifactChannelCategory = Literal[
    "artifact_index_l0_declared",
    "artifact_index_l1_rendered",
    "artifact_index_l2_delivery_receipt",
    "document_render_pending",
    "spreadsheet_output_validation",
    "web_file_delivery_pending",
    "app_delivery_retry_transient",
    "discord_delivery_failed",
    "telegram_delivery_excluded",
    "child_artifact_handoff",
]
ArtifactIndexLevel = Literal["L0", "L1", "L2"]
ArtifactKind = Literal[
    "document",
    "spreadsheet",
    "file",
    "rendered_preview",
    "delivery_receipt",
    "child_handoff",
]
ArtifactOperation = Literal["create", "read", "update", "delete", "render", "deliver", "handoff"]
ArtifactStorageScope = Literal[
    "adk_artifact_service_metadata",
    "openmagi_artifact_index_metadata",
    "channel_delivery_metadata",
]
DeliveryState = Literal["not_requested", "pending", "retrying", "sent", "failed", "skipped"]
TsDeliveryStatus = Literal["pending", "retrying", "sent", "failed"]
ArtifactServiceOwner = Literal["adk-artifact-service"]
OpenMagiArtifactIndexOwner = Literal["openmagi-artifact-index"]
TsMetadataParityCaseType = Literal[
    "output_registry_metadata",
    "file_deliver_attachment_marker",
    "file_send_provider_receipt",
    "file_deliver_kb_write_receipt",
    "file_deliver_path_escape",
    "file_deliver_both_chat_and_kb_receipts",
    "file_send_missing_provider_receipt",
    "channel_identity",
    "web_app_file_send_unsupported",
]
TsOutputKind = Literal["document", "spreadsheet", "file"]
TsOutputFormat = Literal[
    "html",
    "docx",
    "hwpx",
    "pdf",
    "xlsx",
    "csv",
    "tsv",
    "md",
    "txt",
    "json",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "bin",
]
TsPreviewKind = Literal["inline-html", "inline-markdown", "download-only", "none"]
TsDeliveryTarget = Literal["chat", "kb"]
TsRequestedDeliveryTarget = Literal["chat", "kb", "both"]
TsDeliveryAck = Literal["attachment_marker", "provider_message_receipt", "kb_write_receipt"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet)(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram-token|canary",
    re.IGNORECASE,
)
_SECRET_SHAPED_VALUE_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|ghp_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test|artifact)?[-_A-Za-z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY)[A-Z0-9_]*\s*[:=]\s*[^,\s}{]{4,})",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_artifactsecret",
    "sk-artifact-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "private delivery token",
    "raw artifact bytes",
    "hidden reasoning",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_artifact_service_called",
        "adk_runner_invoked",
        "artifact_written",
        "canary_traffic_attached",
        "channel_delivery_performed",
        "evidence_block_enabled",
        "live_tool_dispatched",
        "memory_provider_called",
        "production_authority",
        "production_storage_written",
        "route_or_api_attached",
        "telegram_attached",
        "telegram_polling_attached",
    }
)
_REQUIRED_CATEGORIES = set(ArtifactChannelCategory.__args__)  # type: ignore[attr-defined]
_SHA256_REF_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9._/@+-][A-Za-z0-9._/@+ -]*$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


def _false_flags() -> dict[str, bool]:
    return {name: False for name in ArtifactChannelDeliveryAttachmentFlags.model_fields}


class ArtifactChannelDeliveryAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    adk_artifact_service_called: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceCalled",
    )
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    artifact_written: Literal[False] = Field(default=False, alias="artifactWritten")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    telegram_polling_attached: Literal[False] = Field(
        default=False,
        alias="telegramPollingAttached",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_flags())

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "adk_artifact_service_called",
        "live_tool_dispatched",
        "artifact_written",
        "channel_delivery_performed",
        "telegram_polling_attached",
        "telegram_attached",
        "memory_provider_called",
        "production_storage_written",
        "route_or_api_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class TsOutputArtifactMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    artifact_id: str = Field(alias="artifactId")
    session_key: str = Field(alias="sessionKey")
    turn_id: str = Field(alias="turnId")
    kind: TsOutputKind
    format: TsOutputFormat
    title: str
    filename: str
    mime_type: str = Field(alias="mimeType")
    workspace_path_preview: str = Field(alias="workspacePathPreview")
    preview_kind: TsPreviewKind = Field(alias="previewKind")
    created_by_tool: str = Field(alias="createdByTool")
    source_kind: str = Field(alias="sourceKind")
    created_at: int = Field(alias="createdAt", ge=0)
    updated_at: int = Field(alias="updatedAt", ge=0)

    @model_validator(mode="after")
    def _validate_output_artifact(self) -> Self:
        for value in (
            self.artifact_id,
            self.session_key,
            self.turn_id,
            self.kind,
            self.format,
            self.title,
            self.filename,
            self.mime_type,
            self.workspace_path_preview,
            self.preview_kind,
            self.created_by_tool,
            self.source_kind,
        ):
            _validate_required_public_value(value)
        _validate_safe_relative_path(self.workspace_path_preview)
        if "/" in self.filename or "\\" in self.filename or self.filename in {".", ".."}:
            raise ValueError("filename must be a basename preview")
        if self.updated_at < self.created_at:
            raise ValueError("updatedAt cannot be earlier than createdAt")
        return self


class TsDeliveryRecordMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    target: TsDeliveryTarget
    status: TsDeliveryStatus
    attempt_count: int = Field(alias="attemptCount", ge=1)
    external_id: str | None = Field(default=None, alias="externalId")
    marker: str | None = None
    provider_message_id: str | None = Field(default=None, alias="providerMessageId")
    delivery_ack: TsDeliveryAck | None = Field(default=None, alias="deliveryAck")
    error_message: str | None = Field(default=None, alias="errorMessage")
    delivered_at: int | None = Field(default=None, alias="deliveredAt", ge=0)
    updated_at: int = Field(alias="updatedAt", ge=0)

    @model_validator(mode="after")
    def _validate_delivery_record(self) -> Self:
        for value in (
            self.external_id,
            self.marker,
            self.provider_message_id,
            self.error_message,
        ):
            if value is not None:
                _validate_required_public_value(value)

        if self.status == "not_requested":
            raise ValueError("delivery record cannot use not_requested status")
        if self.status == "failed" and not self.error_message:
            raise ValueError("failed delivery record requires errorMessage metadata")
        if self.status == "sent" and self.delivered_at is None:
            raise ValueError("sent delivery record requires deliveredAt timestamp metadata")
        if self.status != "sent" and self.delivered_at is not None:
            raise ValueError("deliveredAt metadata is only valid for sent deliveries")

        if self.delivery_ack == "attachment_marker":
            if self.target != "chat" or not self.marker:
                raise ValueError("attachment_marker deliveryAck requires chat marker metadata")
        elif self.delivery_ack == "provider_message_receipt":
            if self.target != "chat" or not self.provider_message_id or not self.external_id:
                raise ValueError("provider_message_receipt requires chat provider receipt metadata")
            if not self.external_id.startswith(("telegram:", "discord:")):
                raise ValueError("provider receipt externalId must identify telegram or discord")
        elif self.delivery_ack == "kb_write_receipt":
            if self.target != "kb" or not self.external_id:
                raise ValueError("kb_write_receipt requires KB externalId metadata")

        return self


class TsArtifactProvenanceMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    title: str
    slug: str
    produced_by: str | None = Field(default=None, alias="producedBy")
    sources: tuple[str, ...] = ()
    spawn_task_id: str | None = Field(default=None, alias="spawnTaskId")
    imported_from_artifact_id: str | None = Field(default=None, alias="importedFromArtifactId")

    @model_validator(mode="after")
    def _validate_provenance(self) -> Self:
        _validate_required_public_value(self.title)
        _validate_required_public_value(self.slug)
        if not _SLUG_RE.fullmatch(self.slug):
            raise ValueError("slug must be lowercase kebab metadata")
        for value in (
            self.produced_by,
            self.spawn_task_id,
            self.imported_from_artifact_id,
        ):
            if value is not None:
                _validate_required_public_value(value)
        for source in self.sources:
            _validate_required_public_value(source)
        return self


class TsChannelIdentityMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    type: ChannelType
    channel_id: str = Field(alias="channelId")
    message_id: str | None = Field(default=None, alias="messageId")
    user_id: str | None = Field(default=None, alias="userId")

    @model_validator(mode="after")
    def _validate_channel_identity(self) -> Self:
        _validate_required_public_value(self.channel_id)
        for value in (self.message_id, self.user_id):
            if value is not None:
                _validate_required_public_value(value)
        return self


class ArtifactChannelTsMetadataParityCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    case_type: TsMetadataParityCaseType = Field(alias="caseType")
    tool_name: str = Field(alias="toolName")
    requested_target: TsRequestedDeliveryTarget | None = Field(
        default=None,
        alias="requestedTarget",
    )
    output_artifact: TsOutputArtifactMetadata | None = Field(default=None, alias="outputArtifact")
    delivery_record: TsDeliveryRecordMetadata | None = Field(default=None, alias="deliveryRecord")
    delivery_records: tuple[TsDeliveryRecordMetadata, ...] = Field(
        default=(),
        alias="deliveryRecords",
    )
    artifact_provenance: TsArtifactProvenanceMetadata | None = Field(
        default=None,
        alias="artifactProvenance",
    )
    source_channel: TsChannelIdentityMetadata | None = Field(default=None, alias="sourceChannel")
    delivery_channel: TsChannelIdentityMetadata | None = Field(
        default=None,
        alias="deliveryChannel",
    )
    unsupported_file_send_channels: tuple[Literal["web", "app"], ...] = Field(
        default=(),
        alias="unsupportedFileSendChannels",
    )
    delivery_claim_allowed: bool = Field(default=False, alias="deliveryClaimAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    attachment_flags: ArtifactChannelDeliveryAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_metadata_case(self) -> Self:
        _validate_required_public_value(self.case_id)
        _validate_required_public_value(self.tool_name)
        for reason_code in self.reason_codes:
            _validate_required_public_value(reason_code)

        delivery_records = _ts_delivery_records_for_case(self)
        if self.delivery_record is not None and self.delivery_records:
            raise ValueError("deliveryRecord and deliveryRecords metadata cannot both be present")

        if self.case_type == "output_registry_metadata":
            if self.output_artifact is None or self.artifact_provenance is None:
                raise ValueError("output registry metadata requires output artifact and provenance")
            if self.tool_name != "OutputArtifactRegistry":
                raise ValueError("output registry metadata must identify OutputArtifactRegistry")
            if delivery_records:
                raise ValueError("output registry metadata must not include delivery record")
        elif self.case_type in {
            "file_deliver_attachment_marker",
            "file_send_provider_receipt",
            "file_deliver_kb_write_receipt",
            "file_deliver_path_escape",
            "file_deliver_both_chat_and_kb_receipts",
            "file_send_missing_provider_receipt",
        }:
            if not delivery_records:
                raise ValueError(
                    "FileDeliver/FileSend receipt cases require delivery record metadata"
                )
        elif self.case_type == "channel_identity":
            if self.source_channel is None or self.delivery_channel is None:
                raise ValueError("channel identity metadata requires source and delivery channels")
        elif self.case_type == "web_app_file_send_unsupported":
            if set(self.unsupported_file_send_channels) != {"web", "app"}:
                raise ValueError("web/app unsupported metadata must name both web and app")
            if delivery_records or self.delivery_claim_allowed:
                raise ValueError("unsupported web/app file-send metadata cannot claim delivery")

        if self.case_type == "file_deliver_path_escape":
            if len(delivery_records) != 1 or delivery_records[0].status != "failed":
                raise ValueError("path escape fixture must record failed metadata only")
            if "path_escape_rejected" not in self.reason_codes:
                raise ValueError("path escape fixture requires deterministic reason code")
            if self.delivery_claim_allowed:
                raise ValueError("path escape fixture cannot allow delivery claims")

        if self.case_type == "file_deliver_both_chat_and_kb_receipts":
            if self.tool_name != "FileDeliver":
                raise ValueError("combined chat/KB delivery metadata must identify FileDeliver")
            if self.requested_target != "both":
                raise ValueError("combined chat/KB delivery metadata requires requestedTarget both")
            if self.delivery_record is not None or len(self.delivery_records) != 2:
                raise ValueError("combined chat/KB delivery metadata requires two deliveryRecords")
            if tuple(record.target for record in self.delivery_records) != ("chat", "kb"):
                raise ValueError("combined delivery records must remain separate chat and kb targets")
            if tuple(record.status for record in self.delivery_records) != ("sent", "sent"):
                raise ValueError("combined delivery metadata requires sent chat and KB records")
            if tuple(record.delivery_ack for record in self.delivery_records) != (
                "attachment_marker",
                "kb_write_receipt",
            ):
                raise ValueError("combined delivery metadata requires chat and KB receipt ACKs")

        if self.delivery_claim_allowed:
            if not delivery_records:
                raise ValueError("delivery claims require sent delivery metadata")
            if any(record.status != "sent" for record in delivery_records):
                raise ValueError("delivery claims require sent delivery metadata")
            if any(record.delivery_ack is None for record in delivery_records):
                raise ValueError("delivery claims require explicit deliveryAck metadata")

        return self


class ArtifactChannelTsMetadataParityFixture(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    cases: tuple[ArtifactChannelTsMetadataParityCase, ...]
    attachment_flags: ArtifactChannelDeliveryAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        _validate_required_public_value(self.fixture_id)
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("TS metadata parity caseId values must be unique")
        case_types = {case.case_type for case in self.cases}
        required = set(TsMetadataParityCaseType.__args__)  # type: ignore[attr-defined]
        if case_types != required:
            missing = sorted(required - case_types)
            extra = sorted(case_types - required)
            raise ValueError(
                "TS metadata parity fixture must cover every case type: "
                f"missing={missing}, extra={extra}"
            )
        return self


class ArtifactChannelDeliveryCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: ArtifactChannelCategory
    artifact_ref: str = Field(alias="artifactRef")
    artifact_kind: ArtifactKind = Field(alias="artifactKind")
    artifact_index_level: ArtifactIndexLevel = Field(alias="artifactIndexLevel")
    artifact_service_owner: ArtifactServiceOwner = Field(
        default="adk-artifact-service",
        alias="artifactServiceOwner",
    )
    openmagi_index_owner: OpenMagiArtifactIndexOwner = Field(
        default="openmagi-artifact-index",
        alias="openmagiIndexOwner",
    )
    operation: ArtifactOperation
    storage_scope: ArtifactStorageScope = Field(alias="storageScope")
    content_type: str = Field(alias="contentType")
    byte_size: int = Field(alias="byteSize", ge=0)
    artifact_hash: str = Field(alias="artifactHash")
    output_path_preview: str = Field(alias="outputPathPreview")
    public_preview: str = Field(alias="publicPreview")
    render_verification_required: bool = Field(
        default=False,
        alias="renderVerificationRequired",
    )
    render_verification_passed: bool = Field(default=False, alias="renderVerificationPassed")
    provider_receipt_required: bool = Field(default=False, alias="providerReceiptRequired")
    delivery_claim_allowed: bool = Field(default=False, alias="deliveryClaimAllowed")
    delivery_state: DeliveryState = Field(alias="deliveryState")
    channel_type: ChannelType | Literal["none"] = Field(default="none", alias="channelType")
    delivery_request: ChannelDeliveryRequest | None = Field(
        default=None,
        alias="deliveryRequest",
    )
    receipt: ChannelDeliveryReceipt | None = None
    retry_count: int = Field(default=0, alias="retryCount", ge=0)
    max_retries: int = Field(default=0, alias="maxRetries", ge=0)
    transient_retry_allowed: bool = Field(default=False, alias="transientRetryAllowed")
    telegram_polling_attached: Literal[False] = Field(
        default=False,
        alias="telegramPollingAttached",
    )
    child_execution_id: str | None = Field(default=None, alias="childExecutionId")
    parent_execution_id: str | None = Field(default=None, alias="parentExecutionId")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    artifact_written: Literal[False] = Field(default=False, alias="artifactWritten")
    attachment_flags: ArtifactChannelDeliveryAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        for value in (
            self.case_id,
            self.artifact_ref,
            self.content_type,
            self.artifact_hash,
            self.output_path_preview,
            self.public_preview,
            *(self.evidence_refs),
            *(self.audit_refs),
            *(self.reason_codes),
        ):
            if not value.strip():
                raise ValueError("artifact delivery fields must be non-empty")
            _validate_public_value(value)

        if not _SHA256_REF_RE.fullmatch(self.artifact_hash):
            raise ValueError("artifactHash must be sha256-prefixed lowercase hex")

        if self.delivery_state == "not_requested" and self.channel_type != "none":
            raise ValueError("not_requested delivery state cannot include channelType")
        if self.delivery_state != "not_requested" and self.channel_type == "none":
            raise ValueError("delivery state requires channelType metadata")
        if self.delivery_state in {"pending", "retrying"} and self.delivery_request is None:
            raise ValueError("pending or retrying delivery requires deliveryRequest")
        if self.delivery_state in {"sent", "failed"} and self.receipt is None:
            raise ValueError("sent or failed delivery requires receipt metadata")
        if self.delivery_state == "skipped" and self.category != "telegram_delivery_excluded":
            raise ValueError("skipped delivery is only valid for excluded channel metadata")
        if self.delivery_state == "retrying":
            if self.retry_count <= 0 or not self.transient_retry_allowed:
                raise ValueError("retrying delivery requires retry count and transient retry")
            if self.max_retries < self.retry_count:
                raise ValueError("retry count cannot exceed maxRetries")
        elif self.transient_retry_allowed and self.max_retries <= 0:
            raise ValueError("transient retry metadata requires maxRetries")
        if self.delivery_state == "failed" and not self.reason_codes:
            raise ValueError("failed delivery requires deterministic reason codes")

        if self.delivery_claim_allowed:
            if self.render_verification_required and not self.render_verification_passed:
                raise ValueError("delivery claims require completed render verification")
            if self.provider_receipt_required and self.delivery_state != "sent":
                raise ValueError("provider receipt delivery claims require sent state")
        if self.render_verification_required and not self.render_verification_passed:
            if self.delivery_claim_allowed:
                raise ValueError("render verification must pass before delivery claim")

        if self.receipt is not None:
            if self.receipt.channel.type != self.channel_type:
                raise ValueError("receipt channel must match channelType")
            if self.receipt.artifact_refs and self.artifact_ref not in self.receipt.artifact_refs:
                raise ValueError("receipt artifactRefs must include artifactRef")
        if self.delivery_request is not None:
            if self.delivery_request.channel.type != self.channel_type:
                raise ValueError("deliveryRequest channel must match channelType")
            if self.delivery_request.artifact_refs and (
                self.artifact_ref not in self.delivery_request.artifact_refs
            ):
                raise ValueError("deliveryRequest artifactRefs must include artifactRef")

        if self.category == "telegram_delivery_excluded":
            if self.channel_type != "telegram" or self.delivery_state != "skipped":
                raise ValueError("Telegram fixture must remain skipped metadata")
        elif self.channel_type == "telegram":
            raise ValueError("Telegram delivery metadata requires explicit excluded category")

        if self.category == "child_artifact_handoff":
            if self.child_execution_id is None or self.parent_execution_id is None:
                raise ValueError("child artifact handoff requires child and parent IDs")
            _validate_public_value(self.child_execution_id)
            _validate_public_value(self.parent_execution_id)
        elif self.child_execution_id is not None or self.parent_execution_id is not None:
            raise ValueError("child/parent execution IDs are only valid on child handoff")

        return self


class ArtifactChannelDeliveryContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    cases: tuple[ArtifactChannelDeliveryCase, ...]
    attachment_flags: ArtifactChannelDeliveryAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        categories = {case.category for case in self.cases}
        if categories != _REQUIRED_CATEGORIES:
            missing = sorted(_REQUIRED_CATEGORIES - categories)
            extra = sorted(categories - _REQUIRED_CATEGORIES)
            raise ValueError(
                "artifact/channel fixture must cover every category: "
                f"missing={missing}, extra={extra}"
            )
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("artifact/channel caseId values must be unique")
        if not self.fixture_id.strip():
            raise ValueError("fixtureId must be non-empty")
        _validate_public_value(self.fixture_id)
        return self


class ArtifactChannelDeliveryContractProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    no_live_delivery: Literal[True] = Field(default=True, alias="noLiveDelivery")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_index_level: dict[ArtifactIndexLevel, int] = Field(alias="byIndexLevel")
    by_delivery_state: dict[DeliveryState, int] = Field(alias="byDeliveryState")
    by_channel: dict[ChannelType | Literal["none"], int] = Field(alias="byChannel")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    receipts: dict[str, dict[str, str]] = Field(alias="receipts")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")
    attachment_flags: ArtifactChannelDeliveryAttachmentFlags = Field(alias="attachmentFlags")


class ArtifactChannelTsMetadataParityProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    no_live_delivery: Literal[True] = Field(default=True, alias="noLiveDelivery")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_tool: dict[str, int] = Field(alias="byTool")
    by_delivery_ack: dict[TsDeliveryAck, int] = Field(alias="byDeliveryAck")
    by_delivery_record_target: dict[TsDeliveryTarget, int] = Field(
        alias="byDeliveryRecordTarget",
    )
    unsupported_file_send_channels: dict[Literal["web", "app"], int] = Field(
        alias="unsupportedFileSendChannels",
    )
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")
    attachment_flags: ArtifactChannelDeliveryAttachmentFlags = Field(alias="attachmentFlags")


def load_artifact_channel_delivery_contract_fixture(
    fixture_name: str,
    *,
    fixture_root: Path | None = None,
) -> ArtifactChannelDeliveryContractFixture:
    root = fixture_root or Path(__file__).parents[2] / "tests" / "fixtures" / (
        "artifact_channel_delivery"
    )
    payload = json.loads((root / fixture_name).read_text(encoding="utf-8"))
    return ArtifactChannelDeliveryContractFixture.model_validate(payload)


def load_artifact_channel_ts_metadata_parity_fixture(
    fixture_name: str,
    *,
    fixture_root: Path | None = None,
) -> ArtifactChannelTsMetadataParityFixture:
    root = fixture_root or Path(__file__).parents[2] / "tests" / "fixtures" / (
        "artifact_channel_delivery"
    )
    payload = json.loads((root / fixture_name).read_text(encoding="utf-8"))
    return ArtifactChannelTsMetadataParityFixture.model_validate(payload)


def project_artifact_channel_delivery_contract_fixture(
    fixture: ArtifactChannelDeliveryContractFixture,
) -> ArtifactChannelDeliveryContractProjection:
    level_counts = Counter(case.artifact_index_level for case in fixture.cases)
    state_counts = Counter(case.delivery_state for case in fixture.cases)
    channel_counts = Counter(case.channel_type for case in fixture.cases)
    return ArtifactChannelDeliveryContractProjection(
        fixtureId=fixture.fixture_id,
        version=fixture.version,
        localDiagnostic=True,
        noLiveDelivery=True,
        caseOrder=tuple(case.case_id for case in fixture.cases),
        byIndexLevel=dict(level_counts),
        byDeliveryState=dict(state_counts),
        byChannel=dict(channel_counts),
        publicPreviews={case.case_id: case.public_preview for case in fixture.cases},
        receipts={
            case.case_id: {
                "receiptId": case.receipt.receipt_id,
                "requestId": case.receipt.request_id,
                "status": case.receipt.status,
                "channelType": case.receipt.channel.type,
            }
            for case in fixture.cases
            if case.receipt is not None
        },
        caseSnapshots={
            case.case_id: {
                "artifactRef": case.artifact_ref,
                "artifactKind": case.artifact_kind,
                "artifactIndexLevel": case.artifact_index_level,
                "storageScope": case.storage_scope,
                "deliveryState": case.delivery_state,
                "channelType": case.channel_type,
                "renderVerificationRequired": case.render_verification_required,
                "renderVerificationPassed": case.render_verification_passed,
                "deliveryClaimAllowed": case.delivery_claim_allowed,
                "providerReceiptRequired": case.provider_receipt_required,
                "attachmentFlags": case.attachment_flags.model_dump(by_alias=True),
            }
            for case in fixture.cases
        },
        attachmentFlags=fixture.attachment_flags,
    )


def project_artifact_channel_ts_metadata_parity_fixture(
    fixture: ArtifactChannelTsMetadataParityFixture,
) -> ArtifactChannelTsMetadataParityProjection:
    tool_counts = Counter(case.tool_name for case in fixture.cases)
    records_by_case = {
        case.case_id: _ts_delivery_records_for_case(case) for case in fixture.cases
    }
    ack_counts = Counter(
        record.delivery_ack
        for delivery_records in records_by_case.values()
        for record in delivery_records
        if record.delivery_ack is not None
    )
    target_counts = Counter(
        record.target
        for delivery_records in records_by_case.values()
        for record in delivery_records
    )
    unsupported_counts = Counter(
        channel for case in fixture.cases for channel in case.unsupported_file_send_channels
    )
    return ArtifactChannelTsMetadataParityProjection(
        fixtureId=fixture.fixture_id,
        version=fixture.version,
        localDiagnostic=True,
        noLiveDelivery=True,
        caseOrder=tuple(case.case_id for case in fixture.cases),
        byTool=dict(tool_counts),
        byDeliveryAck=dict(ack_counts),
        byDeliveryRecordTarget=dict(target_counts),
        unsupportedFileSendChannels=dict(unsupported_counts),
        caseSnapshots={
            case.case_id: {
                "caseType": case.case_type,
                "toolName": case.tool_name,
                "requestedTarget": case.requested_target,
                "deliveryClaimAllowed": case.delivery_claim_allowed,
                "outputArtifact": (
                    case.output_artifact.model_dump(by_alias=True)
                    if case.output_artifact is not None
                    else None
                ),
                "deliveryRecord": (
                    case.delivery_record.model_dump(by_alias=True)
                    if case.delivery_record is not None
                    else None
                ),
                "deliveryRecords": [
                    record.model_dump(by_alias=True) for record in case.delivery_records
                ],
                "artifactProvenance": (
                    case.artifact_provenance.model_dump(by_alias=True)
                    if case.artifact_provenance is not None
                    else None
                ),
                "sourceChannel": (
                    case.source_channel.model_dump(by_alias=True)
                    if case.source_channel is not None
                    else None
                ),
                "deliveryChannel": (
                    case.delivery_channel.model_dump(by_alias=True)
                    if case.delivery_channel is not None
                    else None
                ),
                "unsupportedFileSendChannels": case.unsupported_file_send_channels,
                "reasonCodes": case.reason_codes,
                "attachmentFlags": case.attachment_flags.model_dump(by_alias=True),
            }
            for case in fixture.cases
        },
        attachmentFlags=fixture.attachment_flags,
    )


def _ts_delivery_records_for_case(
    case: ArtifactChannelTsMetadataParityCase,
) -> tuple[TsDeliveryRecordMetadata, ...]:
    if case.delivery_record is not None:
        return (case.delivery_record,)
    return case.delivery_records


def _validate_required_public_value(value: str) -> None:
    if not value.strip():
        raise ValueError("artifact/channel metadata fields must be non-empty")
    _validate_public_value(value)


def _validate_safe_relative_path(value: str) -> None:
    _validate_required_public_value(value)
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        raise ValueError("artifact/channel path previews must be relative")
    parts = re.split(r"[\\/]+", value)
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("artifact/channel path previews cannot escape their root")
    if not _SAFE_RELATIVE_PATH_RE.fullmatch(value):
        raise ValueError("artifact/channel path previews contain unsafe characters")


def _validate_public_value(value: str) -> None:
    if _PRODUCTION_PATH_RE.search(value) or _FORBIDDEN_PATH_RE.search(value):
        raise ValueError("artifact/channel metadata cannot expose production paths")
    if _SECRET_SHAPED_VALUE_RE.search(value):
        raise ValueError("artifact/channel metadata cannot expose secret-shaped values")
    sanitized = sanitize_tool_preview(value)
    for token in _FORBIDDEN_PUBLIC_TOKENS:
        if token in sanitized:
            raise ValueError("artifact/channel metadata contains unsafe public token")


def _reject_unsafe_raw_value(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _camel_to_snake(str(key))
            if normalized_key in _FORBIDDEN_RAW_KEY_TOKENS and item is True:
                raise ValueError(f"{key} cannot be true in artifact/channel fixtures")
            _reject_unsafe_raw_value(item)
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe_raw_value(item)
        return

    if isinstance(value, str):
        _validate_public_value(value)
        return

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("artifact/channel metadata must use finite numbers")


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
