from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from openmagi_core_agent.channels.contract import ChannelRef
from openmagi_core_agent.runtime.provider_receipts import provider_digest


BackgroundTaskOperation = Literal["TaskCreate", "TaskList", "TaskGet", "TaskWait", "TaskOutput", "TaskStop"]
BackgroundTaskStatus = Literal["running", "completed", "failed", "aborted", "blocked"]
BackgroundBoundaryStatus = Literal["disabled", "blocked", "task_intent", "recorded_local_fake"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[a-z]-[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|/workspace(?:/[^,\s\"']*)?|"
    r"/data/bots(?:/[^,\s\"']*)?|/var/lib/kubelet(?:/[^,\s\"']*)?|"
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args)|hidden[_ -]?reasoning)",
    re.IGNORECASE,
)
_SENSITIVE_KEY_MARKERS = (
    "token",
    "secret",
    "credential",
    "password",
    "cookie",
    "path",
    "raw",
    "production",
    "route",
    "enabled",
    "attached",
    "authority",
    "authoritative",
    "performed",
)


class BackgroundTaskStorePort(Protocol):
    openmagi_local_fake_provider: bool

    def save_task(self, task: BackgroundTaskRecord) -> BackgroundTaskRecord: ...

    def get_task(self, task_id: str) -> BackgroundTaskRecord | None: ...

    def list_tasks(self) -> Sequence[BackgroundTaskRecord]: ...


class BackgroundTaskConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_task_store_enabled: bool = Field(default=False, alias="localFakeTaskStoreEnabled")
    background_task_runner_attached: Literal[False] = Field(default=False, alias="backgroundTaskRunnerAttached")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = self.model_dump(mode="python", by_alias=False, warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(str(key), str(key)): value for key, value in update.items()})
        data["background_task_runner_attached"] = False
        data["production_writes_enabled"] = False
        data["route_attached"] = False
        _ = deep
        return type(self).model_validate(data)


class BackgroundTaskAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    background_task_started: Literal[False] = Field(default=False, alias="backgroundTaskStarted")
    real_child_runner_invoked: Literal[False] = Field(default=False, alias="realChildRunnerInvoked")
    production_writes_enabled: Literal[False] = Field(default=False, alias="productionWritesEnabled")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    @field_serializer("background_task_started", "real_child_runner_invoked", "production_writes_enabled", "route_attached")
    def _serialize_false(self, _value: object) -> bool:
        return False


class BackgroundTaskRecord(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str = Field(alias="taskId")
    owner_digest: str = Field(alias="ownerDigest")
    status: BackgroundTaskStatus
    prompt_preview: str = Field(alias="promptPreview")
    parent_turn_id: str | None = Field(default=None, alias="parentTurnId")
    session_key_digest: str | None = Field(default=None, alias="sessionKeyDigest")
    channel: ChannelRef | None = None
    progress: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = Field(default=(), alias="outputRefs")
    cancel_token_ref: str = Field(alias="cancelTokenRef")
    idempotency_digest: str = Field(alias="idempotencyDigest")
    created_at: int = Field(default=0, alias="createdAt", ge=0)
    updated_at: int = Field(default=0, alias="updatedAt", ge=0)

    @field_validator("task_id", "owner_digest", "parent_turn_id", "session_key_digest", "cancel_token_ref", "idempotency_digest")
    @classmethod
    def _validate_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value)

    @field_validator("prompt_preview")
    @classmethod
    def _sanitize_prompt(cls, value: str) -> str:
        return _safe_text(value)[:600]

    @field_validator("progress")
    @classmethod
    def _sanitize_progress(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_text(item)[:160] for item in value)

    @field_validator("output_refs")
    @classmethod
    def _validate_output_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    def public_projection(self) -> dict[str, object]:
        return {
            "taskId": _public_ref(self.task_id, "task"),
            "ownerDigest": _public_ref(self.owner_digest, "owner"),
            "status": self.status,
            "promptPreview": _safe_text(self.prompt_preview)[:600],
            "parentTurnId": None if self.parent_turn_id is None else _public_ref(self.parent_turn_id, "turn"),
            "sessionKeyDigest": None if self.session_key_digest is None else _public_ref(self.session_key_digest, "session"),
            "channel": None if self.channel is None else self.channel.model_dump(by_alias=True),
            "progress": [_safe_text(item)[:160] for item in self.progress],
            "outputRefs": [_public_ref(ref, "artifact") for ref in self.output_refs],
            "cancelTokenRef": _public_ref(self.cancel_token_ref, "cancel-token"),
            "idempotencyDigest": self.idempotency_digest,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class BackgroundTaskRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: BackgroundTaskOperation
    request_id: str = Field(alias="requestId")
    owner_digest: str = Field(alias="ownerDigest")
    task_id: str | None = Field(default=None, alias="taskId")
    task_ids: tuple[str, ...] = Field(default=(), alias="taskIds")
    parent_turn_id: str | None = Field(default=None, alias="parentTurnId")
    session_key_digest: str | None = Field(default=None, alias="sessionKeyDigest")
    channel: ChannelRef | None = None
    prompt_preview: str | None = Field(default=None, alias="promptPreview")
    status_filter: BackgroundTaskStatus | None = Field(default=None, alias="status")
    progress: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = Field(default=(), alias="outputRefs")
    wait_timeout_ms: int = Field(default=0, alias="waitTimeoutMs", ge=0)
    stop_reason: str | None = Field(default=None, alias="stopReason")
    now: int = Field(default=0, ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("request_id", "owner_digest", "task_id", "parent_turn_id", "session_key_digest")
    @classmethod
    def _validate_optional_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value)

    @field_validator("task_ids", "output_refs")
    @classmethod
    def _validate_ref_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)


class BackgroundTaskDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: BackgroundBoundaryStatus
    operation: BackgroundTaskOperation
    request_digest: str = Field(alias="requestDigest")
    task: BackgroundTaskRecord | None = None
    tasks: tuple[BackgroundTaskRecord, ...] = ()
    results: tuple[Mapping[str, object], ...] = ()
    output: Mapping[str, object] | None = None
    timed_out: bool = Field(default=False, alias="timedOut")
    stopped: bool = False
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(default_factory=dict, alias="diagnosticMetadata")
    authority_flags: BackgroundTaskAuthorityFlags = Field(default_factory=BackgroundTaskAuthorityFlags, alias="authorityFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "requestDigest": self.request_digest,
            "task": None if self.task is None else self.task.public_projection(),
            "tasks": [task.public_projection() for task in self.tasks],
            "results": [_safe_result(result) for result in self.results],
            "output": None if self.output is None else _safe_result(self.output),
            "timedOut": self.timed_out,
            "stopped": self.stopped,
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class BackgroundTaskBoundary:
    """Default-off TaskList/Get/Wait/Output/Stop metadata boundary."""

    def __init__(self, config: BackgroundTaskConfig) -> None:
        self.config = config

    def execute(
        self,
        request: BackgroundTaskRequest,
        *,
        store: BackgroundTaskStorePort | None = None,
    ) -> BackgroundTaskDecision:
        diagnostics = _diagnostics(self.config, request.metadata)
        digest = provider_digest({"requestId": request.request_id, "operation": request.operation, "taskId": request.task_id, "taskIds": request.task_ids})
        if not self.config.enabled:
            return _decision(request, "disabled", digest, ("background_task_runtime_disabled",), diagnostics)
        if not self.config.local_fake_task_store_enabled or store is None:
            return _decision(request, "task_intent", digest, ("local_fake_task_store_disabled",), diagnostics)
        if getattr(store, "openmagi_local_fake_provider", False) is not True:
            return _decision(request, "blocked", digest, ("local_fake_task_store_untrusted",), diagnostics)
        try:
            if request.operation == "TaskCreate":
                task = BackgroundTaskRecord(
                    taskId=request.task_id or f"task:{_short_digest(request.prompt_preview or request.request_id)}",
                    ownerDigest=request.owner_digest,
                    status="running",
                    promptPreview=request.prompt_preview or "",
                    parentTurnId=request.parent_turn_id,
                    sessionKeyDigest=request.session_key_digest,
                    channel=request.channel,
                    progress=request.progress,
                    outputRefs=request.output_refs,
                    cancelTokenRef=f"cancel-token:{_short_digest(request.request_id)}",
                    idempotencyDigest=f"task:{_short_digest(f'{request.owner_digest}:{request.task_id}:{request.prompt_preview}')}",
                    createdAt=request.now,
                    updatedAt=request.now,
                )
                return _decision(request, "recorded_local_fake", digest, ("local_fake_task_create_receipt_only",), diagnostics, task=store.save_task(task))
            if request.operation == "TaskList":
                tasks = _filter_tasks(tuple(store.list_tasks()), request)
                return _decision(request, "recorded_local_fake", digest, ("task_list_metadata_only",), diagnostics, tasks=tasks)
            task = store.get_task(request.task_id or "") if request.task_id else None
            if request.operation == "TaskWait":
                records = tuple(
                    record
                    for task_id in request.task_ids
                    if (record := store.get_task(task_id)) is not None and _task_owned_by_request(record, request)
                )
                timed_out = any(record.status == "running" for record in records)
                results = tuple(_task_wait_result(record) for record in records)
                return _decision(
                    request,
                    "recorded_local_fake",
                    digest,
                    ("task_wait_pending_metadata_only" if timed_out else "task_wait_terminal_metadata_only",),
                    diagnostics,
                    tasks=records,
                    results=results,
                    timed_out=timed_out,
                )
            if task is None or not _task_owned_by_request(task, request):
                return _decision(request, "blocked", digest, ("task_not_found_or_not_owned",), diagnostics)
            if request.operation == "TaskStop":
                stopped = False
                stop = getattr(store, "stop_task", None)
                if callable(stop):
                    stopped = bool(stop(task.task_id, request.stop_reason))
                    task = store.get_task(task.task_id) or task
                return _decision(request, "recorded_local_fake", digest, ("task_stop_receipt_only",), diagnostics, task=task, stopped=stopped)
            if request.operation == "TaskOutput":
                output = {
                    "taskId": task.task_id,
                    "status": task.status,
                    "outputRefs": task.output_refs,
                    "durationMs": max(0, task.updated_at - task.created_at),
                }
                return _decision(request, "recorded_local_fake", digest, ("task_output_metadata_only",), diagnostics, task=task, output=output)
            return _decision(request, "recorded_local_fake", digest, ("task_get_metadata_only",), diagnostics, task=task)
        except Exception:
            safe_diagnostics = {**diagnostics, "storeErrorCode": "fake_store_error"}
            return _decision(request, "blocked", digest, ("local_fake_task_store_error",), safe_diagnostics)


def _decision(
    request: BackgroundTaskRequest,
    status: BackgroundBoundaryStatus,
    digest: str,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
    *,
    task: BackgroundTaskRecord | None = None,
    tasks: tuple[BackgroundTaskRecord, ...] = (),
    results: tuple[Mapping[str, object], ...] = (),
    output: Mapping[str, object] | None = None,
    timed_out: bool = False,
    stopped: bool = False,
) -> BackgroundTaskDecision:
    return BackgroundTaskDecision(
        status=status,
        operation=request.operation,
        requestDigest=digest,
        task=task,
        tasks=tasks,
        results=results,
        output=output,
        timedOut=timed_out,
        stopped=stopped,
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=BackgroundTaskAuthorityFlags(),
    )


def _filter_tasks(
    tasks: tuple[BackgroundTaskRecord, ...],
    request: BackgroundTaskRequest,
) -> tuple[BackgroundTaskRecord, ...]:
    filtered = tuple(task for task in tasks if _task_owned_by_request(task, request))
    if request.status_filter is not None:
        filtered = tuple(task for task in filtered if task.status == request.status_filter)
    if request.session_key_digest is not None:
        filtered = tuple(task for task in filtered if task.session_key_digest == request.session_key_digest)
    return filtered


def _task_owned_by_request(task: BackgroundTaskRecord, request: BackgroundTaskRequest) -> bool:
    return task.owner_digest == request.owner_digest


def _task_wait_result(task: BackgroundTaskRecord) -> Mapping[str, object]:
    return {
        "taskId": task.task_id,
        "status": task.status,
        "outputRefs": task.output_refs,
        "durationMs": max(0, task.updated_at - task.created_at),
    }


def _safe_result(result: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in result.items():
        if key in {"taskId", "sessionKeyDigest"} and isinstance(value, str):
            safe[key] = _public_ref(value, "task" if key == "taskId" else "session")
        elif key == "outputRefs" and isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            safe[key] = [_public_ref(str(item), "artifact") for item in value]
        elif isinstance(value, str):
            safe[key] = _safe_text(value)
        elif isinstance(value, bool | int | float) or value is None:
            safe[key] = value
    return safe


def _diagnostics(config: BackgroundTaskConfig, metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "localFakeTaskStoreEnabled": config.local_fake_task_store_enabled,
        "backgroundTaskRunnerAttached": False,
        "productionWritesEnabled": False,
        "routeAttached": False,
        **dict(metadata),
    }


def _safe_ref(value: str) -> str:
    clean = _safe_text(value.strip())
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("background task refs must be public identifiers")
    return clean


def _safe_text(value: str) -> str:
    if _SECRET_TEXT_RE.search(value) or _PRIVATE_TEXT_RE.search(value):
        return "[redacted]"
    return value[:4096]


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        raw_key = str(key)
        normalized = re.sub(r"[^a-z0-9]", "", raw_key.casefold())
        if any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS):
            continue
        if isinstance(value, str):
            clean = _safe_text(value)
            if clean != "[redacted]":
                safe[raw_key[:80]] = clean
        elif isinstance(value, bool | int | float) or value is None:
            safe[raw_key[:80]] = value
    return safe


def _public_ref(value: str, prefix: str) -> str:
    return f"{prefix}:{_short_digest(value)}"


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "BackgroundTaskAuthorityFlags",
    "BackgroundTaskBoundary",
    "BackgroundTaskConfig",
    "BackgroundTaskDecision",
    "BackgroundTaskRecord",
    "BackgroundTaskRequest",
    "BackgroundTaskStorePort",
]
