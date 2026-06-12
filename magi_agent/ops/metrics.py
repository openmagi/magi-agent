from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator, model_validator

from .safety import (
    require_digest,
    require_metric_name,
    require_safe_ref,
    safe_dimensions,
    safe_metadata,
    sanitize_validation_error,
    serialize_safe_value,
)


MetricUnit = Literal["count", "ms", "bytes", "ratio"]
MetricSource = Literal["local_in_memory"]

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


class RuntimeOpsAttachmentFlags(BaseModel):
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

    live_tool_execution_attached: Literal[False] = Field(
        default=False,
        alias="liveToolExecutionAttached",
    )
    production_storage_attached: Literal[False] = Field(
        default=False,
        alias="productionStorageAttached",
    )
    production_queue_attached: Literal[False] = Field(
        default=False,
        alias="productionQueueAttached",
    )
    raw_prompt_attached: Literal[False] = Field(default=False, alias="rawPromptAttached")
    hidden_reasoning_attached: Literal[False] = Field(
        default=False,
        alias="hiddenReasoningAttached",
    )
    credential_attached: Literal[False] = Field(default=False, alias="credentialAttached")
    raw_tool_output_attached: Literal[False] = Field(
        default=False,
        alias="rawToolOutputAttached",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        result = dict(value) if isinstance(value, Mapping) else {}
        for alias in (
            "liveToolExecutionAttached",
            "productionStorageAttached",
            "productionQueueAttached",
            "rawPromptAttached",
            "hiddenReasoningAttached",
            "credentialAttached",
            "rawToolOutputAttached",
        ):
            result[alias] = False
        return result

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for runtime ops attachment flags")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for runtime ops attachment flags")
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
            raise ValueError("copy update/include/exclude is disabled for runtime ops attachment flags")
        return self.model_copy(deep=deep)

    def public_projection(self) -> dict[str, object]:
        return {
            "liveToolExecutionAttached": False,
            "productionStorageAttached": False,
            "productionQueueAttached": False,
            "promptPayloadAttached": False,
            "hiddenReasoningAttached": False,
            "credentialAttached": False,
            "toolOutputPayloadAttached": False,
        }


class RuntimeMetricRecord(BaseModel):
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

    schema_version: Literal["openmagi.ops.metric.v1"] = Field(
        default="openmagi.ops.metric.v1",
        alias="schemaVersion",
    )
    metric_name: str = Field(alias="metricName")
    value: float = Field(ge=0)
    unit: MetricUnit
    trace_digest: str = Field(alias="traceDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    dimensions: Mapping[str, object] = Field(default_factory=dict)
    source: MetricSource = "local_in_memory"
    attachment_flags: RuntimeOpsAttachmentFlags = Field(
        default_factory=RuntimeOpsAttachmentFlags,
        alias="attachmentFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for runtime metric records")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for runtime metric records")
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
            raise ValueError("copy update/include/exclude is disabled for runtime metric records")
        return self.model_copy(deep=deep)

    @field_validator("metric_name")
    @classmethod
    def _validate_metric_name(cls, value: str) -> str:
        return require_metric_name(value)

    @field_validator("trace_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("dimensions")
    @classmethod
    def _validate_dimensions(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_dimensions(value)

    @field_serializer("dimensions")
    def _serialize_dimensions(self, value: Mapping[str, object]) -> dict[str, object]:
        return {
            key: serialize_safe_value(item)
            for key, item in safe_dimensions(value).items()
        }

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.ops.metric.public.v1",
            "metricName": self.metric_name,
            "value": self.value,
            "unit": self.unit,
            "traceDigest": self.trace_digest,
            "policySnapshotDigest": self.policy_snapshot_digest,
            "dimensions": {
                key: serialize_safe_value(item)
                for key, item in safe_dimensions(self.dimensions).items()
            },
            "source": self.source,
            "attachmentFlags": self.attachment_flags.public_projection(),
        }


class RuntimeMetricsSnapshot(BaseModel):
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

    schema_version: Literal["openmagi.ops.metrics_snapshot.v1"] = Field(
        default="openmagi.ops.metrics_snapshot.v1",
        alias="schemaVersion",
    )
    source: MetricSource = "local_in_memory"
    runtime_operations_enabled: bool = Field(default=False, alias="runtimeOperationsEnabled")
    counts: Mapping[str, int] = Field(default_factory=dict)
    event_type_counts: Mapping[str, int] = Field(default_factory=dict, alias="eventTypeCounts")
    metric_records: tuple[RuntimeMetricRecord, ...] = Field(
        default=(),
        alias="metricRecords",
    )
    attachment_flags: RuntimeOpsAttachmentFlags = Field(
        default_factory=RuntimeOpsAttachmentFlags,
        alias="attachmentFlags",
    )

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for runtime metrics snapshots")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for runtime metrics snapshots")
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
            raise ValueError("copy update/include/exclude is disabled for runtime metrics snapshots")
        return self.model_copy(deep=deep)

    @field_validator("counts", "event_type_counts")
    @classmethod
    def _validate_counts(cls, value: Mapping[str, int]) -> Mapping[str, int]:
        clean: dict[str, int] = {}
        for key, count in sorted(value.items(), key=lambda item: str(item[0])):
            if count:
                clean[require_safe_ref(str(key), field_name="metric count key")] = int(count)
        return clean

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.ops.metrics_snapshot.public.v1",
            "source": self.source,
            "runtimeOperationsEnabled": self.runtime_operations_enabled,
            "counts": dict(self.counts),
            "eventTypeCounts": dict(self.event_type_counts),
            "metricRecords": [metric.public_projection() for metric in self.metric_records],
            "attachmentFlags": self.attachment_flags.public_projection(),
        }


def build_runtime_metrics_snapshot(
    events: Iterable[RuntimeOperationEvent],
    *,
    extra_metrics: Iterable[RuntimeMetricRecord] = (),
    runtime_operations_enabled: bool = False,
) -> RuntimeMetricsSnapshot:
    materialized = tuple(events)
    status_counts = Counter(event.status for event in materialized)
    event_type_counts = Counter(event.event_type for event in materialized)
    metric_records: list[RuntimeMetricRecord] = []
    if materialized:
        trace_digest = _digest_refs(event.trace_id for event in materialized)
        policy_digest = materialized[-1].policy_snapshot_digest
        for status, count in sorted(status_counts.items()):
            metric_records.append(
                RuntimeMetricRecord(
                    metricName=f"ops.event.{status}",
                    value=count,
                    unit="count",
                    traceDigest=trace_digest,
                    policySnapshotDigest=policy_digest,
                    dimensions={"status": status},
                )
            )
    metric_records.extend(extra_metrics)
    return RuntimeMetricsSnapshot(
        runtimeOperationsEnabled=bool(runtime_operations_enabled),
        counts=dict(status_counts),
        eventTypeCounts=dict(event_type_counts),
        metricRecords=tuple(metric_records),
    )


def _digest_refs(refs: Iterable[str]) -> str:
    encoded = json.dumps(tuple(refs), sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
