from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openmagi_core_agent.transport.sse import InMemorySseWriter


PublicEventParityStatus = Literal[
    "supported_now",
    "projected_alias",
    "default_off_boundary_only",
    "intentionally_unsupported",
    "blocked_until_gate",
]

_SUPPORTED_STATUSES = {"supported_now", "projected_alias"}
_DEFERRED_STATUSES = {
    "default_off_boundary_only",
    "intentionally_unsupported",
    "blocked_until_gate",
}

_MATRIX_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
)


class PublicEventParityMatrixRow(BaseModel):
    model_config = _MATRIX_CONFIG

    event_type: str = Field(alias="eventType")
    classification: PublicEventParityStatus
    projected_alias: str | None = Field(default=None, alias="projectedAlias")
    frontend_contract: str = Field(default="event: agent", alias="frontendContract")
    source_files: tuple[str, ...] = Field(alias="sourceFiles")
    follow_up_gate_reason: str | None = Field(default=None, alias="followUpGateReason")
    sample_event: dict[str, Any] | None = Field(default=None, alias="sampleEvent")
    expected_public: dict[str, Any] | None = Field(default=None, alias="expectedPublic")

    @model_validator(mode="after")
    def _validate_classification_contract(self) -> PublicEventParityMatrixRow:
        if self.classification in _SUPPORTED_STATUSES:
            if self.sample_event is None or self.expected_public is None:
                msg = f"{self.event_type} supported rows require sample and expected public payload"
                raise ValueError(msg)
            if self.classification == "projected_alias" and self.projected_alias is None:
                msg = f"{self.event_type} projected aliases must name projectedAlias"
                raise ValueError(msg)
        if self.classification in _DEFERRED_STATUSES:
            if not self.follow_up_gate_reason:
                msg = f"{self.event_type} deferred rows require followUpGateReason"
                raise ValueError(msg)
            if self.expected_public is not None:
                msg = f"{self.event_type} deferred rows cannot carry expectedPublic"
                raise ValueError(msg)
        return self


class ActiveSnapshotReconnectContract(BaseModel):
    model_config = _MATRIX_CONFIG

    scenario: str
    expected_order: tuple[str, ...] = Field(alias="expectedOrder")
    production_write_enabled: bool = Field(alias="productionWriteEnabled")
    route_activation_enabled: bool = Field(alias="routeActivationEnabled")
    user_visible_output_enabled: bool = Field(alias="userVisibleOutputEnabled")
    follow_up_gate_reason: str = Field(alias="followUpGateReason")

    @model_validator(mode="after")
    def _validate_default_off(self) -> ActiveSnapshotReconnectContract:
        if (
            self.production_write_enabled
            or self.route_activation_enabled
            or self.user_visible_output_enabled
        ):
            msg = f"{self.scenario} active snapshot contract must remain default-off"
            raise ValueError(msg)
        return self


class RouteCompatibilityContract(BaseModel):
    model_config = _MATRIX_CONFIG

    route: str
    method: str
    event_type: str = Field(alias="eventType")
    selected_bot_authority_required: bool = Field(alias="selectedBotAuthorityRequired")
    ts_fallback_on_python_unavailable: bool = Field(alias="tsFallbackOnPythonUnavailable")
    no_raw_payload_projection: bool = Field(alias="noRawPayloadProjection")
    default_off: bool = Field(alias="defaultOff")

    @model_validator(mode="after")
    def _validate_safe_contract(self) -> RouteCompatibilityContract:
        if not (
            self.selected_bot_authority_required
            and self.ts_fallback_on_python_unavailable
            and self.no_raw_payload_projection
            and self.default_off
        ):
            msg = f"{self.route} compatibility contract must be selected-gated and default-off"
            raise ValueError(msg)
        return self


class ChannelDeliveryReceiptStance(BaseModel):
    model_config = _MATRIX_CONFIG

    classification: PublicEventParityStatus
    follow_up_gate_reason: str = Field(alias="followUpGateReason")
    public_receipt_projection_enabled: bool = Field(alias="publicReceiptProjectionEnabled")

    @model_validator(mode="after")
    def _validate_deferred_receipts(self) -> ChannelDeliveryReceiptStance:
        if self.classification != "blocked_until_gate" or self.public_receipt_projection_enabled:
            msg = "channel delivery receipts must remain blocked until the channel gate"
            raise ValueError(msg)
        return self


class PublicEventParityMatrix(BaseModel):
    model_config = _MATRIX_CONFIG

    schema_version: str = Field(alias="schemaVersion")
    source_files: tuple[str, ...] = Field(alias="sourceFiles")
    rows: tuple[PublicEventParityMatrixRow, ...]
    active_snapshot_contracts: tuple[ActiveSnapshotReconnectContract, ...] = Field(
        alias="activeSnapshotContracts",
    )
    route_compatibility_contracts: tuple[RouteCompatibilityContract, ...] = Field(
        alias="routeCompatibilityContracts",
    )
    channel_delivery_receipt_stance: ChannelDeliveryReceiptStance = Field(
        alias="channelDeliveryReceiptStance",
    )

    @model_validator(mode="after")
    def _validate_matrix(self) -> PublicEventParityMatrix:
        event_types = [row.event_type for row in self.rows]
        if len(event_types) != len(set(event_types)):
            msg = "public event parity matrix contains duplicate eventType rows"
            raise ValueError(msg)
        matrix_sources = set(self.source_files)
        for row in self.rows:
            if not set(row.source_files).issubset(matrix_sources):
                msg = f"{row.event_type} cites a source file outside the matrix sources"
                raise ValueError(msg)
        return self


class PublicEventProjectionAudit(BaseModel):
    model_config = _MATRIX_CONFIG

    event_type: str = Field(alias="eventType")
    classification: PublicEventParityStatus
    projected_alias: str | None = Field(default=None, alias="projectedAlias")
    payload: dict[str, Any] | None = None
    dropped: bool
    drop_reason: str | None = Field(default=None, alias="dropReason")


def load_public_event_parity_matrix(path: str | Path) -> PublicEventParityMatrix:
    return PublicEventParityMatrix.model_validate_json(Path(path).read_text())


def audit_public_event_projection(
    event: Mapping[str, object],
    matrix: PublicEventParityMatrix,
) -> PublicEventProjectionAudit:
    event_type = event.get("type")
    normalized_type = event_type if isinstance(event_type, str) else "<missing>"
    row = next((item for item in matrix.rows if item.event_type == normalized_type), None)
    if row is None:
        return PublicEventProjectionAudit(
            eventType=normalized_type,
            classification="intentionally_unsupported",
            dropped=True,
            dropReason="unclassified_event_type",
        )

    writer = InMemorySseWriter()
    writer.agent(dict(event))
    payload = _first_agent_payload(writer.body)
    if payload is None:
        return PublicEventProjectionAudit(
            eventType=row.event_type,
            classification=row.classification,
            projectedAlias=row.projected_alias,
            dropped=True,
            dropReason=row.follow_up_gate_reason or "sanitizer_dropped_event",
        )
    return PublicEventProjectionAudit(
        eventType=row.event_type,
        classification=row.classification,
        projectedAlias=row.projected_alias,
        payload=payload,
        dropped=False,
    )


def _first_agent_payload(sse_body: str) -> dict[str, Any] | None:
    for line in sse_body.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            payload = json.loads(line.removeprefix("data: "))
            return payload if isinstance(payload, dict) else None
    return None
