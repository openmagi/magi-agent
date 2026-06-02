from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias
import weakref

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from openmagi_core_agent.missions.receipts import (
    sanitize_public_id,
    sanitize_public_ref,
    sanitize_public_text,
)


MissionRuntimeEventKind: TypeAlias = Literal[
    "mission_event",
    "mission_created",
    "cron_run",
    "goal_created",
    "goal_progress",
    "goal_completed",
    "goal_cancelled",
    "background_task",
    "mission_progress",
]
MissionRuntimeDeferredEventKind: TypeAlias = Literal[
    "mission_raw_payload",
    "goal_raw_payload",
    "cron_mutation_payload",
    "background_task_payload",
    "channel_delivery_receipt",
    "provider_raw_delta",
]
MissionEventProjectionStatus: TypeAlias = Literal[
    "blocked",
    "deferred",
    "projected_local_fake",
]
MissionEventProjectionClassification: TypeAlias = Literal[
    "supported_now",
    "projected_alias",
    "default_off_boundary_only",
    "intentionally_unsupported",
    "blocked_until_gate",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SUPPORTED_DIRECT_EVENT_KINDS = frozenset(
    {
        "mission_created",
        "mission_event",
        "background_task",
    }
)
_RESERVED_MISSION_ALIAS_EVENT_TYPES = frozenset(
    {
        "mission_progress",
        "cron_run",
        "goal_created",
        "goal_progress",
        "goal_completed",
        "goal_cancelled",
    }
)
_PROJECTED_ALIAS_EVENT_KINDS: Mapping[str, str] = {
}
_DEFERRED_EVENT_KINDS: Mapping[str, tuple[MissionEventProjectionClassification, str]] = {
    "mission_progress": (
        "default_off_boundary_only",
        "Mission progress aliases are blocked until a public mission progress gate.",
    ),
    "cron_run": (
        "blocked_until_gate",
        "Cron run projection is blocked until Gate 5 scheduler activation.",
    ),
    "goal_created": (
        "default_off_boundary_only",
        "Goal event projection is blocked until a public goal runtime gate.",
    ),
    "goal_progress": (
        "default_off_boundary_only",
        "Goal event projection is blocked until a public goal runtime gate.",
    ),
    "goal_completed": (
        "default_off_boundary_only",
        "Goal event projection is blocked until a public goal runtime gate.",
    ),
    "goal_cancelled": (
        "default_off_boundary_only",
        "Goal event projection is blocked until a public goal runtime gate.",
    ),
    "mission_raw_payload": (
        "default_off_boundary_only",
        "Raw mission payload projection is blocked until a public mission payload schema gate.",
    ),
    "goal_raw_payload": (
        "default_off_boundary_only",
        "Raw goal payload projection is blocked until a public goal payload schema gate.",
    ),
    "cron_mutation_payload": (
        "blocked_until_gate",
        "Cron mutation payload projection is blocked until Gate 5 scheduler activation.",
    ),
    "background_task_payload": (
        "default_off_boundary_only",
        "Raw background task payload projection is blocked until a public task payload schema gate.",
    ),
    "channel_delivery_receipt": (
        "blocked_until_gate",
        "Channel delivery receipt projection is blocked until Gate 4 channel activation.",
    ),
    "provider_raw_delta": (
        "default_off_boundary_only",
        "Raw provider deltas are blocked until a public provider delta schema gate.",
    ),
}
_DISABLED_GATE_REASON = (
    "Mission public event projection is disabled unless enabled and "
    "localFakeEventProjectionEnabled are explicitly true."
)
_BACKGROUND_TASK_STATUSES = frozenset({"running", "completed", "failed", "aborted"})
_PROJECTED_RESULT_REFS: dict[int, weakref.ReferenceType[MissionPublicEventProjectionResult]] = {}


def _normalize_event_kind_text(value: object) -> str:
    clean = _sanitize_public_event_text(str(value)).strip().lower()
    normalized = clean.replace("-", "_").replace(" ", "_")
    safe = "".join(char for char in normalized if char.isalnum() or char == "_")
    return safe[:80] or "unsupported_event"


class MissionEventProjectionAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    route_activation_enabled: Literal[False] = Field(
        default=False,
        alias="routeActivationEnabled",
    )
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="memoryMutationEnabled",
    )
    cron_mutation_enabled: Literal[False] = Field(default=False, alias="cronMutationEnabled")
    live_background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveBackgroundExecutionEnabled",
    )
    sse_write_enabled: Literal[False] = Field(default=False, alias="sseWriteEnabled")
    transcript_write_enabled: Literal[False] = Field(
        default=False,
        alias="transcriptWriteEnabled",
    )
    database_write_enabled: Literal[False] = Field(
        default=False,
        alias="databaseWriteEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

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
        "production_write_enabled",
        "route_activation_enabled",
        "user_visible_output_enabled",
        "channel_delivery_enabled",
        "workspace_mutation_enabled",
        "memory_mutation_enabled",
        "cron_mutation_enabled",
        "live_background_execution_enabled",
        "sse_write_enabled",
        "transcript_write_enabled",
        "database_write_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MissionEventProjectionConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_event_projection_enabled: bool = Field(
        default=False,
        alias="localFakeEventProjectionEnabled",
    )
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    route_activation_enabled: Literal[False] = Field(
        default=False,
        alias="routeActivationEnabled",
    )
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="memoryMutationEnabled",
    )
    cron_mutation_enabled: Literal[False] = Field(default=False, alias="cronMutationEnabled")
    live_background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="liveBackgroundExecutionEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name in (
            "production_write_enabled",
            "route_activation_enabled",
            "user_visible_output_enabled",
            "channel_delivery_enabled",
            "workspace_mutation_enabled",
            "memory_mutation_enabled",
            "cron_mutation_enabled",
            "live_background_execution_enabled",
        ):
            payload.pop(field_name, None)
        payload["productionWriteEnabled"] = False
        payload["routeActivationEnabled"] = False
        payload["userVisibleOutputEnabled"] = False
        payload["channelDeliveryEnabled"] = False
        payload["workspaceMutationEnabled"] = False
        payload["memoryMutationEnabled"] = False
        payload["cronMutationEnabled"] = False
        payload["liveBackgroundExecutionEnabled"] = False
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_serializer(
        "production_write_enabled",
        "route_activation_enabled",
        "user_visible_output_enabled",
        "channel_delivery_enabled",
        "workspace_mutation_enabled",
        "memory_mutation_enabled",
        "cron_mutation_enabled",
        "live_background_execution_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def authority_flags(self) -> MissionEventProjectionAuthorityFlags:
        return MissionEventProjectionAuthorityFlags()


class MissionRuntimeEventRequest(BaseModel):
    model_config = _MODEL_CONFIG

    event_kind: str = Field(alias="eventKind")
    mission_id: str | None = Field(default=None, alias="missionId")
    run_id: str | None = Field(default=None, alias="runId")
    turn_id: str | None = Field(default=None, alias="turnId")
    cron_id: str | None = Field(default=None, alias="cronId")
    goal_id: str | None = Field(default=None, alias="goalId")
    task_id: str | None = Field(default=None, alias="taskId")
    event_type: str | None = Field(default=None, alias="eventType")
    status: str | None = None
    detail: str | None = None
    title: str | None = None
    kind: str | None = None
    persona: str | None = None
    created_at: int | float | None = Field(default=None, alias="createdAt")
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True, repr=False)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True, repr=False)
    raw_private_path: str | None = Field(
        default=None,
        alias="rawPrivatePath",
        exclude=True,
        repr=False,
    )
    tool_args: Mapping[str, object] | None = Field(
        default=None,
        alias="toolArgs",
        exclude=True,
        repr=False,
    )
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True, repr=False)
    auth_headers: Mapping[str, object] | None = Field(
        default=None,
        alias="authHeaders",
        exclude=True,
        repr=False,
    )
    cookies: Mapping[str, object] | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    secret_material: str | None = Field(
        default=None,
        alias="secretMaterial",
        exclude=True,
        repr=False,
    )
    hidden_reasoning: str | None = Field(
        default=None,
        alias="hiddenReasoning",
        exclude=True,
        repr=False,
    )
    raw_mission_payload: Mapping[str, object] | None = Field(
        default=None,
        alias="rawMissionPayload",
        exclude=True,
        repr=False,
    )
    raw_goal_payload: Mapping[str, object] | None = Field(
        default=None,
        alias="rawGoalPayload",
        exclude=True,
        repr=False,
    )
    raw_task_payload: Mapping[str, object] | None = Field(
        default=None,
        alias="rawTaskPayload",
        exclude=True,
        repr=False,
    )

    @field_validator("event_kind", mode="before")
    @classmethod
    def _sanitize_event_kind(cls, value: object) -> str:
        return _normalize_event_kind_text(value)

    @field_validator("mission_id", mode="before")
    @classmethod
    def _sanitize_optional_mission_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_id(str(value), prefix="mission")

    @field_validator("cron_id", mode="before")
    @classmethod
    def _sanitize_optional_cron_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_id(str(value), prefix="cron")

    @field_validator("goal_id", mode="before")
    @classmethod
    def _sanitize_optional_goal_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_id(str(value), prefix="goal")

    @field_validator("task_id", mode="before")
    @classmethod
    def _sanitize_optional_task_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_id(str(value), prefix="task")

    @field_validator("run_id", "turn_id", mode="before")
    @classmethod
    def _sanitize_optional_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_ref(str(value))

    @field_validator("event_type", "status", "detail", "title", "kind", "persona", mode="before")
    @classmethod
    def _sanitize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        clean = _sanitize_public_event_text(str(value))
        return clean or None

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in dict(update).items():
                field = type(self).model_fields.get(str(key))
                payload[field.alias if field is not None and field.alias else key] = value
        _ = deep
        return type(self).model_validate(payload)


class MissionEventKindClassification(BaseModel):
    model_config = _MODEL_CONFIG

    event_kind: str = Field(alias="eventKind")
    classification: MissionEventProjectionClassification
    projected_alias: str | None = Field(default=None, alias="projectedAlias")
    public_event: None = Field(default=None, alias="publicEvent")
    follow_up_gate_reason: str | None = Field(default=None, alias="followUpGateReason")


class MissionPublicEventProjectionResult(BaseModel):
    model_config = _MODEL_CONFIG

    projection_boundary: Literal["mission_public_event_projection.v1"] = Field(
        default="mission_public_event_projection.v1",
        alias="projectionBoundary",
    )
    status: MissionEventProjectionStatus
    event_kind: str = Field(alias="eventKind")
    classification: MissionEventProjectionClassification
    projected_alias: str | None = Field(default=None, alias="projectedAlias")
    public_event: dict[str, object] | None = Field(default=None, alias="publicEvent")
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    follow_up_gate_reason: str | None = Field(default=None, alias="followUpGateReason")
    authority_flags: MissionEventProjectionAuthorityFlags = Field(
        default_factory=MissionEventProjectionAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority_flags(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["authorityFlags"] = MissionEventProjectionAuthorityFlags()
        payload.pop("authority_flags", None)
        return payload

    @model_validator(mode="after")
    def _validate_public_event_contract(self) -> MissionPublicEventProjectionResult:
        if self.status != "projected_local_fake":
            if self.public_event is not None:
                object.__setattr__(self, "public_event", None)
            return self

        if self.classification != "supported_now":
            msg = "projected_local_fake mission events must be supported_now"
            raise ValueError(msg)
        if self.public_event is None:
            msg = "projected_local_fake mission events require publicEvent"
            raise ValueError(msg)
        safe_event = _sanitize_projected_public_event(self.public_event)
        if safe_event is None:
            msg = "projected_local_fake mission events require a safe publicEvent"
            raise ValueError(msg)
        object.__setattr__(self, "public_event", safe_event)
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            for key, value in dict(update).items():
                field = type(self).model_fields.get(str(key))
                payload[field.alias if field is not None and field.alias else key] = value
        payload["authorityFlags"] = MissionEventProjectionAuthorityFlags()
        _ = deep
        return type(self).model_validate(payload)


class MissionPublicEventProjection:
    """Mission-owned local fake event projection contract.

    The projector returns sanitized event dictionaries only. It never writes to
    SSE, transcript, database, channels, workspace, cron, memory, or background
    runtimes; callers must still pass emitted events through the SSE sanitizer.
    """

    def __init__(
        self,
        config: MissionEventProjectionConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, MissionEventProjectionConfig)
            else MissionEventProjectionConfig.model_validate(config or {})
        )

    def classify_event_kind(self, event_kind: object) -> MissionEventKindClassification:
        kind = str(event_kind)
        if kind in _SUPPORTED_DIRECT_EVENT_KINDS:
            return MissionEventKindClassification(
                eventKind=kind,
                classification="supported_now",
            )
        projected_alias = _PROJECTED_ALIAS_EVENT_KINDS.get(kind)
        if projected_alias is not None:
            return MissionEventKindClassification(
                eventKind=kind,
                classification="projected_alias",
                projectedAlias=projected_alias,
            )
        deferred = _DEFERRED_EVENT_KINDS.get(kind)
        if deferred is not None:
            classification, reason = deferred
            return MissionEventKindClassification(
                eventKind=kind,
                classification=classification,
                followUpGateReason=reason,
            )
        return MissionEventKindClassification(
            eventKind=kind,
            classification="intentionally_unsupported",
            followUpGateReason=(
                "Unclassified mission event kinds require a public event parity "
                "matrix row and activation gate before projection."
            ),
        )

    def project(
        self,
        request: MissionRuntimeEventRequest | Mapping[str, object],
    ) -> MissionPublicEventProjectionResult:
        safe_request = (
            MissionRuntimeEventRequest.model_validate(
                request.model_dump(by_alias=True, mode="python", warnings=False),
            )
            if isinstance(request, MissionRuntimeEventRequest)
            else MissionRuntimeEventRequest.model_validate(request)
        )
        classification = self.classify_event_kind(safe_request.event_kind)

        if not (
            self.config.enabled and self.config.local_fake_event_projection_enabled
        ):
            return MissionPublicEventProjectionResult(
                status="blocked",
                eventKind=safe_request.event_kind,
                classification="blocked_until_gate",
                projectedAlias=classification.projected_alias,
                blockedReason="mission_event_projection_disabled",
                followUpGateReason=_DISABLED_GATE_REASON,
            )

        if (
            safe_request.event_kind == "mission_event"
            and _normalize_event_kind_text(safe_request.event_type or "")
            in _RESERVED_MISSION_ALIAS_EVENT_TYPES
        ):
            alias_classification = self.classify_event_kind(safe_request.event_type)
            return MissionPublicEventProjectionResult(
                status="deferred",
                eventKind=safe_request.event_kind,
                classification=alias_classification.classification,
                projectedAlias=alias_classification.projected_alias,
                blockedReason="mission_event_reserved_alias_not_projected",
                followUpGateReason=alias_classification.follow_up_gate_reason,
            )

        if classification.classification not in {"supported_now", "projected_alias"}:
            return MissionPublicEventProjectionResult(
                status="deferred",
                eventKind=safe_request.event_kind,
                classification=classification.classification,
                projectedAlias=classification.projected_alias,
                blockedReason="mission_event_kind_not_projected",
                followUpGateReason=classification.follow_up_gate_reason,
            )

        public_event = _project_public_event(safe_request)
        if public_event is None:
            return MissionPublicEventProjectionResult(
                status="blocked",
                eventKind=safe_request.event_kind,
                classification=classification.classification,
                projectedAlias=classification.projected_alias,
                blockedReason="mission_event_missing_public_refs",
                followUpGateReason=(
                    "Mission event projection requires safe public mission/task refs."
                ),
            )

        result = MissionPublicEventProjectionResult(
            status="projected_local_fake",
            eventKind=safe_request.event_kind,
            classification=classification.classification,
            projectedAlias=classification.projected_alias,
            publicEvent=public_event,
        )
        _register_projection_result(result)
        return result


def _register_projection_result(result: MissionPublicEventProjectionResult) -> None:
    result_id = id(result)
    _PROJECTED_RESULT_REFS[result_id] = weakref.ref(
        result,
        lambda _ref, stale_id=result_id: _PROJECTED_RESULT_REFS.pop(stale_id, None),
    )


def is_projector_issued_result(result: object) -> bool:
    return _PROJECTED_RESULT_REFS.get(id(result), lambda: None)() is result


def sanitize_projected_agent_event(
    projection_result: object,
) -> dict[str, object] | None:
    if type(projection_result) is not MissionPublicEventProjectionResult:
        return None
    if not is_projector_issued_result(projection_result):
        return None
    payload = projection_result.model_dump(by_alias=True, mode="python", warnings=False)
    if payload.get("projectionBoundary") != "mission_public_event_projection.v1":
        return None
    if payload.get("status") != "projected_local_fake":
        return None
    if payload.get("classification") != "supported_now":
        return None
    authority_flags = payload.get("authorityFlags")
    if not isinstance(authority_flags, Mapping):
        return None
    if any(value is not False for value in authority_flags.values()):
        return None
    event = payload.get("publicEvent")
    if not isinstance(event, Mapping):
        return None
    return _sanitize_projected_public_event(event)


def _project_public_event(request: MissionRuntimeEventRequest) -> dict[str, object] | None:
    if request.event_kind == "mission_created":
        if request.mission_id is None:
            return None
        mission: dict[str, object] = {"id": request.mission_id}
        mission["title"] = request.title or "Mission"
        mission["kind"] = request.kind or "manual"
        mission["status"] = request.status or "running"
        return {"type": "mission_created", "mission": mission}

    if request.event_kind == "background_task":
        task_id = request.task_id or request.goal_id or request.mission_id
        if task_id is None:
            return None
        event: dict[str, object] = {
            "type": "background_task",
            "taskId": task_id,
            "persona": request.persona or "agent",
            "status": _background_task_status(request.status),
        }
        if request.detail:
            event["detail"] = request.detail
        return event

    mission_id = request.mission_id
    if mission_id is None:
        return None
    event_type = (
        request.event_type
        if request.event_kind == "mission_event" and request.event_type
        else request.event_kind
    )
    event = {
        "type": "mission_event",
        "missionId": mission_id,
        "eventType": event_type,
    }
    message = request.detail or request.status
    if message:
        event["message"] = message
    return event


def _sanitize_projected_public_event(event: Mapping[str, object]) -> dict[str, object] | None:
    event_type = event.get("type")
    if event_type == "mission_created":
        mission = event.get("mission")
        if not isinstance(mission, Mapping):
            return None
        mission_id = mission.get("id")
        if not isinstance(mission_id, str):
            return None
        safe_mission: dict[str, object] = {
            "id": sanitize_public_id(mission_id, prefix="mission")
        }
        for key in ("title", "kind", "status"):
            value = mission.get(key)
            if isinstance(value, str):
                safe_value = _sanitize_public_event_text(value)
                if safe_value:
                    safe_mission[key] = safe_value
        return {"type": "mission_created", "mission": safe_mission}

    if event_type == "background_task":
        task_id = event.get("taskId")
        if not isinstance(task_id, str):
            return None
        safe_event: dict[str, object] = {
            "type": "background_task",
            "taskId": sanitize_public_id(task_id, prefix="task"),
            "persona": "agent",
            "status": _background_task_status(None),
        }
        persona = event.get("persona")
        if isinstance(persona, str):
            safe_event["persona"] = _sanitize_public_event_text(persona) or "agent"
        status = event.get("status")
        if isinstance(status, str):
            safe_event["status"] = _background_task_status(_sanitize_public_event_text(status))
        detail = event.get("detail")
        if isinstance(detail, str):
            safe_detail = _sanitize_public_event_text(detail)
            if safe_detail:
                safe_event["detail"] = safe_detail
        return safe_event

    if event_type == "mission_event":
        mission_id = event.get("missionId")
        mission_event_type = event.get("eventType")
        if not isinstance(mission_id, str) or not isinstance(mission_event_type, str):
            return None
        if _normalize_event_kind_text(mission_event_type) in _RESERVED_MISSION_ALIAS_EVENT_TYPES:
            return None
        safe_event = {
            "type": "mission_event",
            "missionId": sanitize_public_id(mission_id, prefix="mission"),
            "eventType": _sanitize_public_event_text(mission_event_type) or "event",
        }
        message = event.get("message")
        if isinstance(message, str):
            safe_message = _sanitize_public_event_text(message)
            if safe_message:
                safe_event["message"] = safe_message
        return safe_event

    return None


def _background_task_status(value: str | None) -> str:
    return value if value in _BACKGROUND_TASK_STATUSES else "running"


def _sanitize_public_event_text(value: str) -> str:
    clean = sanitize_public_text(value)
    lines = []
    for line in clean.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {"[redacted]", "[redacted-path]"}:
            continue
        lines.append(stripped)
    return "\n".join(lines)


__all__ = [
    "MissionEventKindClassification",
    "MissionEventProjectionAuthorityFlags",
    "MissionEventProjectionClassification",
    "MissionEventProjectionConfig",
    "MissionEventProjectionStatus",
    "MissionPublicEventProjection",
    "MissionPublicEventProjectionResult",
    "MissionRuntimeEventKind",
    "MissionRuntimeEventRequest",
    "sanitize_projected_agent_event",
]
