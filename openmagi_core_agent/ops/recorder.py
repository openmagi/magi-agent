from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .metrics import RuntimeMetricsSnapshot, build_runtime_metrics_snapshot
from .runtime_events import RuntimeOperationEvent
from .safety import require_digest, sanitize_validation_error
from .traces import RuntimeTraceSnapshot, build_runtime_trace_snapshot


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class RuntimeOperationReceipt(BaseModel):
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

    schema_version: Literal["openmagi.ops.receipt.v1"] = Field(
        default="openmagi.ops.receipt.v1",
        alias="schemaVersion",
    )
    status: Literal["stored", "dropped_disabled"]
    event_digest: str = Field(alias="eventDigest")
    source: Literal["local_in_memory"] = "local_in_memory"
    production_write: Literal[False] = Field(default=False, alias="productionWrite")
    live_tool_execution: Literal[False] = Field(default=False, alias="liveToolExecution")
    public_projection_allowed: Literal[False] = Field(default=False, alias="publicProjectionAllowed")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for runtime operation receipts")
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
            raise ValueError("copy update/include/exclude is disabled for runtime operation receipts")
        return self.model_copy(deep=deep)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for runtime operation receipts")

    @field_validator("event_digest")
    @classmethod
    def _validate_event_digest(cls, value: str) -> str:
        return require_digest(value)

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        result = dict(value) if isinstance(value, Mapping) else {}
        result["productionWrite"] = False
        result["liveToolExecution"] = False
        result["publicProjectionAllowed"] = False
        return result


class InMemoryRuntimeOpsRecorder:
    def __init__(self, *, enabled: bool = False, max_recent_events: int = 50) -> None:
        if max_recent_events < 1:
            raise ValueError("max_recent_events must be positive")
        self._enabled = enabled
        self._max_recent_events = max_recent_events
        self._events: list[RuntimeOperationEvent] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record_event(self, event: RuntimeOperationEvent) -> RuntimeOperationReceipt:
        validated = RuntimeOperationEvent.model_validate(event.model_dump(by_alias=True, mode="json"))
        if not self._enabled:
            return RuntimeOperationReceipt(
                status="dropped_disabled",
                eventDigest=validated.event_digest,
            )
        self._events.append(validated)
        return RuntimeOperationReceipt(status="stored", eventDigest=validated.event_digest)

    def events(self) -> tuple[RuntimeOperationEvent, ...]:
        return tuple(self._events)

    def recent_events(self) -> tuple[RuntimeOperationEvent, ...]:
        return tuple(self._events[-self._max_recent_events :])

    def metrics_snapshot(self) -> RuntimeMetricsSnapshot:
        return build_runtime_metrics_snapshot(
            self._events,
            runtime_operations_enabled=self._enabled,
        )

    def snapshot(self) -> RuntimeMetricsSnapshot:
        return self.metrics_snapshot()

    def trace_snapshot(self, trace_id: str) -> RuntimeTraceSnapshot:
        return build_runtime_trace_snapshot(
            (event for event in self._events if event.trace_id == trace_id),
            runtime_operations_enabled=self._enabled,
        )


InMemoryOpsRecorder = InMemoryRuntimeOpsRecorder
