from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator

from .safety import (
    require_digest,
    require_safe_ref,
    safe_metadata,
    sanitize_validation_error,
    serialize_safe_value,
)


RuntimeOperationEventType = Literal[
    "operation_started",
    "operation_completed",
    "tool_observed",
    "guardrail_observed",
    "policy_decision",
    "metadata_dropped",
    "error_observed",
]
RuntimeOperationStatus = Literal["accepted", "dropped", "rejected"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class RuntimeOperationEvent(BaseModel):
    model_config = _MODEL_CONFIG

    def __init__(self, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=type(self).__name__) from None

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, *args: object, **kwargs: object) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for runtime operation events")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))

    def copy(
        self,
        *,
        include: object = None,
        exclude: object = None,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        if update or include is not None or exclude is not None:
            raise ValueError("copy update/include/exclude is disabled for runtime operation events")
        return self.model_copy(deep=deep)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for runtime operation events")

    schema_version: Literal["openmagi.ops.event.v1"] = Field(
        default="openmagi.ops.event.v1",
        alias="schemaVersion",
    )
    event_id: str = Field(alias="eventId")
    trace_id: str = Field(alias="traceId")
    operation_id: str = Field(alias="operationId")
    sequence: int = Field(ge=0)
    event_type: RuntimeOperationEventType = Field(alias="eventType")
    status: RuntimeOperationStatus
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    ledger_head_digest: str = Field(alias="ledgerHeadDigest")
    context_projection_digest: str = Field(alias="contextProjectionDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        alias="occurredAt",
    )
    activation_enabled: Literal[False] = Field(default=False, alias="activationEnabled")
    live_tool_execution_attached: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionAttached",
    )
    raw_prompt_attached: Literal[False] = Field(default=False, alias="rawPromptAttached")
    raw_tool_output_attached: Literal[False] = Field(
        default=False,
        alias="rawToolOutputAttached",
    )
    hidden_reasoning_attached: Literal[False] = Field(
        default=False,
        alias="hiddenReasoningAttached",
    )
    credential_attached: Literal[False] = Field(
        default=False,
        alias="credentialAttached",
    )

    @field_validator("event_id", "trace_id", "operation_id")
    @classmethod
    def _validate_refs(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator(
        "policy_snapshot_digest",
        "ledger_head_digest",
        "context_projection_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return {key: serialize_safe_value(item) for key, item in safe_metadata(value).items()}

    @property
    def event_digest(self) -> str:
        payload = self.model_dump(
            by_alias=True,
            mode="json",
            exclude={"occurredAt", "occurred_at"},
        )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def project_runtime_operation_event(event: RuntimeOperationEvent) -> dict[str, object]:
    validated = RuntimeOperationEvent.model_validate(event.model_dump(by_alias=True, mode="json"))
    return {
        "schemaVersion": "openmagi.ops.event.public.v1",
        "eventId": validated.event_id,
        "traceId": validated.trace_id,
        "operationId": validated.operation_id,
        "sequence": validated.sequence,
        "eventType": validated.event_type,
        "status": validated.status,
        "eventDigest": validated.event_digest,
        "policySnapshotDigest": validated.policy_snapshot_digest,
        "ledgerHeadDigest": validated.ledger_head_digest,
        "contextProjectionDigest": validated.context_projection_digest,
        "publicMetadata": {
            key: serialize_safe_value(item)
            for key, item in safe_metadata(validated.metadata).items()
        },
        "occurredAt": validated.occurred_at.isoformat(),
        "activationEnabled": False,
        "attachmentFlags": {
            "liveToolExecutionAttached": False,
            "promptPayloadAttached": False,
            "toolOutputPayloadAttached": False,
            "hiddenReasoningAttached": False,
            "credentialAttached": False,
        },
    }
