from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping
import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .runtime_events import RuntimeOperationEvent, project_runtime_operation_event
from .safety import require_digest, require_safe_ref, sanitize_validation_error


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class RuntimeTraceSnapshot(BaseModel):
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

    schema_version: Literal["openmagi.ops.trace_snapshot.v1"] = Field(
        default="openmagi.ops.trace_snapshot.v1",
        alias="schemaVersion",
    )
    trace_id: str = Field(alias="traceId")
    trace_digest: str = Field(alias="traceDigest")
    event_digests: tuple[str, ...] = Field(alias="eventDigests")
    events: tuple[RuntimeOperationEvent, ...]
    source: Literal["local_in_memory"] = "local_in_memory"
    runtime_operations_enabled: bool = Field(default=False, alias="runtimeOperationsEnabled")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError("model_construct is disabled for runtime trace snapshots")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for runtime trace snapshots")
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
            raise ValueError("copy update/include/exclude is disabled for runtime trace snapshots")
        return self.model_copy(deep=deep)

    @field_validator("trace_id")
    @classmethod
    def _validate_trace_id(cls, value: str) -> str:
        return require_safe_ref(value, field_name="traceId")

    @field_validator("trace_digest")
    @classmethod
    def _validate_trace_digest(cls, value: str) -> str:
        return require_digest(value)

    @field_validator("event_digests")
    @classmethod
    def _validate_event_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(require_digest(item) for item in value)

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.ops.trace.public.v1",
            "traceId": self.trace_id,
            "traceDigest": self.trace_digest,
            "eventDigests": list(self.event_digests),
            "source": self.source,
            "runtimeOperationsEnabled": self.runtime_operations_enabled,
            "events": [project_runtime_operation_event(event) for event in self.events],
        }


def build_runtime_trace_snapshot(
    events: Iterable[RuntimeOperationEvent],
    *,
    runtime_operations_enabled: bool = False,
) -> RuntimeTraceSnapshot:
    ordered_events = tuple(sorted(events, key=lambda event: (event.sequence, event.event_id)))
    trace_id = ordered_events[0].trace_id if ordered_events else "trace:empty"
    if any(event.trace_id != trace_id for event in ordered_events):
        raise ValueError("trace snapshots require one traceId")
    event_digests = tuple(sorted(event.event_digest for event in ordered_events))
    encoded = json.dumps(event_digests, sort_keys=True, separators=(",", ":")).encode()
    return RuntimeTraceSnapshot(
        traceId=trace_id,
        traceDigest="sha256:" + hashlib.sha256(encoded).hexdigest(),
        eventDigests=event_digests,
        events=ordered_events,
        runtimeOperationsEnabled=runtime_operations_enabled,
    )
