from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_JSON_RECORD_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="allow",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|"
    r"infra[\\/]k8s|infra[\\/]docker[\\/]provisioning-worker|deploy(?:ment)?[\\/]|"
    r"deploy\\.sh|runtime-selector|runtime_selector|telegram|canary|proxy|dashboard|api[\\/]",
    re.IGNORECASE,
)
_SECRET_SHAPED_VALUE_RE = re.compile(
    r"\b(?:Bearer\s+[A-Za-z0-9._~+/=-]+|gh[opusr]_[A-Za-z0-9_]+|"
    r"sk-[A-Za-z0-9._-]+|[rs]k_(?:live|test)_[A-Za-z0-9_]+)\b",
    re.IGNORECASE,
)


class ControlProjectionAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    tool_host_attached: Literal[False] = Field(default=False, alias="toolHostAttached")
    dispatcher_attached: Literal[False] = Field(default=False, alias="dispatcherAttached")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    database_or_proxy_attached: Literal[False] = Field(
        default=False,
        alias="databaseOrProxyAttached",
    )
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    child_runner_invoked: Literal[False] = Field(default=False, alias="childRunnerInvoked")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    artifact_delivery_attached: Literal[False] = Field(
        default=False,
        alias="artifactDeliveryAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)(**{name: False for name in type(self).model_fields})

    @field_serializer(
        "adk_runner_invoked",
        "tool_host_attached",
        "dispatcher_attached",
        "route_or_api_attached",
        "database_or_proxy_attached",
        "canary_traffic_attached",
        "production_storage_written",
        "memory_provider_called",
        "child_runner_invoked",
        "workspace_mutated",
        "scheduler_attached",
        "artifact_delivery_attached",
        "evidence_block_enabled",
        "user_visible_output_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ControlProjectionEventRecord(BaseModel):
    model_config = _JSON_RECORD_CONFIG

    type: str
    seq: int
    ts: int | float

    @model_validator(mode="before")
    @classmethod
    def _validate_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("control projection events must be JSON objects")
        _validate_json_like(value)
        return value

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json", warnings=False)


class ControlProjectionFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["controlProjectionContractFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    explicit_now: int | float = Field(alias="explicitNow")
    attachment_flags: ControlProjectionAttachmentFlags = Field(alias="attachmentFlags")
    control_events: tuple[ControlProjectionEventRecord, ...] = Field(alias="controlEvents")

    @model_validator(mode="before")
    @classmethod
    def _validate_payload(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise ValueError("control projection fixture must be a JSON object")
        _validate_json_like(value)
        return value

    @model_validator(mode="after")
    def _validate_event_sequence(self) -> Self:
        last_seq = 0
        for event in self.control_events:
            if event.seq <= last_seq:
                raise ValueError("control projection events must use monotonic seq values")
            last_seq = event.seq
        return self


class ControlProjectionResult(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: ControlProjectionAttachmentFlags = Field(alias="attachmentFlags")
    last_seq: int = Field(alias="lastSeq")
    pending_request_ids: tuple[str, ...] = Field(alias="pendingRequestIds")
    request_states: dict[str, str] = Field(alias="requestStates")
    requests: dict[str, dict[str, object]]
    active_plan: dict[str, object] | None = Field(alias="activePlan")
    task_board: object | None = Field(alias="taskBoard")
    verification: object | None
    retry_counts: dict[str, int] = Field(alias="retryCounts")
    last_stop_reason_by_turn: dict[str, str] = Field(alias="lastStopReasonByTurn")
    child_agents: dict[str, dict[str, object]] = Field(alias="childAgents")


def load_control_projection_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> ControlProjectionFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return ControlProjectionFixture.model_validate(payload)


def project_control_projection_fixture(
    fixture: ControlProjectionFixture | Mapping[str, Any],
) -> ControlProjectionResult:
    safe_fixture = _validated_fixture_snapshot(fixture)
    raw_projection = _project_control_events(
        [event.as_dict() for event in safe_fixture.control_events],
        now=safe_fixture.explicit_now,
    )
    requests = raw_projection["requests"]
    if not isinstance(requests, dict):
        raise ValueError("control projection requests must be a mapping")
    return ControlProjectionResult(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        lastSeq=raw_projection["lastSeq"],
        pendingRequestIds=tuple(
            str(request["requestId"])
            for request in raw_projection["pendingRequests"]
            if isinstance(request, Mapping)
        ),
        requestStates={
            str(request_id): str(request["state"])
            for request_id, request in requests.items()
            if isinstance(request, Mapping)
        },
        requests=requests,
        activePlan=raw_projection["activePlan"],
        taskBoard=raw_projection["taskBoard"],
        verification=raw_projection["verification"],
        retryCounts=raw_projection["retryCounts"],
        lastStopReasonByTurn=raw_projection["lastStopReasonByTurn"],
        childAgents=raw_projection["childAgents"],
    )


def _validated_fixture_snapshot(
    fixture: ControlProjectionFixture | Mapping[str, Any],
) -> ControlProjectionFixture:
    if isinstance(fixture, ControlProjectionFixture):
        return ControlProjectionFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return ControlProjectionFixture.model_validate(fixture)


def _project_control_events(
    events: list[dict[str, object]],
    *,
    now: int | float,
) -> dict[str, object]:
    projection: dict[str, object] = {
        "lastSeq": 0,
        "pendingRequests": [],
        "requests": {},
        "activePlan": None,
        "taskBoard": None,
        "verification": None,
        "retryCounts": {},
        "lastStopReasonByTurn": {},
        "childAgents": {},
    }
    requests: dict[str, dict[str, object]] = {}
    retry_counts: dict[str, int] = {}
    last_stop_reason_by_turn: dict[str, str] = {}
    child_agents: dict[str, dict[str, object]] = {}

    for event in events:
        seq = _required_int(event, "seq")
        projection["lastSeq"] = max(int(projection["lastSeq"]), seq)
        event_type = event.get("type")
        if event_type == "retry":
            turn_id = _required_str(event, "turnId")
            retry_counts[turn_id] = retry_counts.get(turn_id, 0) + 1
        elif event_type == "control_request_created":
            request = _required_mapping(event, "request")
            request_id = _required_str(request, "requestId")
            requests[request_id] = dict(request)
        elif event_type == "control_request_resolved":
            request_id = _required_str(event, "requestId")
            existing = requests.get(request_id)
            if not existing or existing.get("state") != "pending":
                continue
            decision = _required_str(event, "decision")
            existing = dict(existing)
            existing.update(
                {
                    "state": _state_for_decision(decision),
                    "resolvedAt": event["ts"],
                    "decision": decision,
                    "feedback": event.get("feedback"),
                    "updatedInput": event.get("updatedInput"),
                    "answer": event.get("answer"),
                }
            )
            requests[request_id] = existing
        elif event_type == "control_request_cancelled":
            request_id = _required_str(event, "requestId")
            existing = requests.get(request_id)
            if not existing or existing.get("state") != "pending":
                continue
            existing = dict(existing)
            existing.update(
                {
                    "state": "cancelled",
                    "resolvedAt": event["ts"],
                    "cancelReason": event.get("reason"),
                }
            )
            requests[request_id] = existing
        elif event_type == "control_request_timed_out":
            request_id = _required_str(event, "requestId")
            existing = requests.get(request_id)
            if not existing or existing.get("state") != "pending":
                continue
            existing = dict(existing)
            existing.update({"state": "timed_out", "resolvedAt": event["ts"]})
            requests[request_id] = existing
        elif event_type == "plan_lifecycle":
            projection["activePlan"] = _without_none(
                {
                    "planId": event.get("planId"),
                    "state": event.get("state"),
                    "turnId": event.get("turnId"),
                    "requestId": event.get("requestId"),
                    "plan": event.get("plan"),
                    "feedback": event.get("feedback"),
                }
            )
        elif event_type == "task_board_snapshot":
            projection["taskBoard"] = event.get("taskBoard")
        elif event_type == "verification":
            projection["verification"] = _without_none(
                {
                    "type": event.get("type"),
                    "turnId": event.get("turnId"),
                    "status": event.get("status"),
                    "evidence": event.get("evidence"),
                    "reason": event.get("reason"),
                }
            )
        elif event_type == "stop_reason":
            last_stop_reason_by_turn[_required_str(event, "turnId")] = _required_str(
                event,
                "reason",
            )
        elif event_type == "child_started":
            task_id = _required_str(event, "taskId")
            child_agents[task_id] = _without_none(
                {
                    "taskId": task_id,
                    "state": "running",
                    "parentTurnId": event.get("parentTurnId"),
                    "lastEventSeq": seq,
                }
            )
        elif event_type in {
            "child_progress",
            "child_tool_request",
            "child_permission_decision",
        }:
            task_id = _required_str(event, "taskId")
            existing = child_agents.get(task_id)
            if existing:
                existing["lastEventSeq"] = seq
        elif event_type == "child_cancelled":
            task_id = _required_str(event, "taskId")
            existing = dict(child_agents.get(task_id, {"taskId": task_id, "state": "running"}))
            existing.update(
                {
                    "state": "cancelled",
                    "lastEventSeq": seq,
                    "errorMessage": event.get("reason"),
                }
            )
            child_agents[task_id] = existing
        elif event_type == "child_failed":
            task_id = _required_str(event, "taskId")
            existing = dict(child_agents.get(task_id, {"taskId": task_id, "state": "running"}))
            existing.update(
                {
                    "state": "failed",
                    "lastEventSeq": seq,
                    "errorMessage": event.get("errorMessage"),
                }
            )
            child_agents[task_id] = existing
        elif event_type == "child_completed":
            task_id = _required_str(event, "taskId")
            existing = dict(child_agents.get(task_id, {"taskId": task_id, "state": "running"}))
            existing.update(
                {
                    "state": "completed",
                    "lastEventSeq": seq,
                    "summary": event.get("summary"),
                }
            )
            child_agents[task_id] = existing

    for request_id, request in tuple(requests.items()):
        if request.get("state") == "pending" and _required_number(request, "expiresAt") <= now:
            updated = dict(request)
            updated.update({"state": "timed_out", "resolvedAt": request["expiresAt"]})
            requests[request_id] = updated

    projection["requests"] = requests
    projection["pendingRequests"] = [
        request for request in requests.values() if request.get("state") == "pending"
    ]
    projection["retryCounts"] = retry_counts
    projection["lastStopReasonByTurn"] = last_stop_reason_by_turn
    projection["childAgents"] = child_agents
    return projection


def _state_for_decision(decision: str) -> str:
    if decision == "approved":
        return "approved"
    if decision == "denied":
        return "denied"
    if decision == "answered":
        return "answered"
    raise ValueError(f"unsupported control request decision: {decision}")


def _without_none(values: Mapping[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _required_mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise ValueError(f"control projection event is missing object field {key}")
    return nested


def _required_str(value: Mapping[str, object], key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str) or not nested:
        raise ValueError(f"control projection event is missing string field {key}")
    return nested


def _required_int(value: Mapping[str, object], key: str) -> int:
    nested = value.get(key)
    if not isinstance(nested, int) or isinstance(nested, bool):
        raise ValueError(f"control projection event is missing integer field {key}")
    return nested


def _required_number(value: Mapping[str, object], key: str) -> int | float:
    nested = value.get(key)
    if isinstance(nested, (int, float)) and not isinstance(nested, bool):
        return nested
    raise ValueError(f"control projection event is missing number field {key}")


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("control projection fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("control projection fixtures must be local and non-production")


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            _reject_unsafe_path_text(value)
            if _SECRET_SHAPED_VALUE_RE.search(value):
                raise ValueError("control projection fixture contains unsafe secret-shaped data")
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("control projection payloads must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("control projection mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("control projection payloads must be JSON-compatible")


__all__ = [
    "ControlProjectionAttachmentFlags",
    "ControlProjectionFixture",
    "ControlProjectionResult",
    "load_control_projection_fixture",
    "project_control_projection_fixture",
]
