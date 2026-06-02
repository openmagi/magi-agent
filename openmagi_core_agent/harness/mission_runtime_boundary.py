from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


MissionOperation = Literal[
    "goal.create",
    "goal.progress",
    "goal.complete",
    "goal.pause",
    "goal.resume",
    "goal.cancel",
    "scheduler.tick",
    "task.create",
    "task.get",
    "task.list",
    "task.wait",
    "task.stop",
    "plan.interview",
    "plan.update",
    "plan.auto_trigger",
]
GoalStatus = Literal["pending", "running", "paused", "completed", "cancelled", "blocked"]
BackgroundTaskStatus = Literal["running", "completed", "failed", "aborted", "blocked"]
MissionRuntimeStatus = Literal[
    "disabled",
    "goal_intent",
    "goal_recorded_local_fake",
    "scheduler_intent",
    "scheduler_tick_recorded_local_fake",
    "task_intent",
    "task_recorded_local_fake",
    "blocked",
]
MissionSchedulerDecisionStatus = Literal["planned", "suppressed"]
MissionChildRole = Literal["implementer", "reviewer", "operator"]

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
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users(?:/[^,\s\"']*)?|/home(?:/[^,\s\"']*)?|"
    r"/workspace(?:/[^,\s\"']*)?|/data/bots(?:/[^,\s\"']*)?|"
    r"/var/lib/kubelet(?:/[^,\s\"']*)?|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_RAW_PRIVATE_LINE_RE = re.compile(
    r"raw[_ -]?(?:transcript|tool|prompt|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"authorization|cookie|set-cookie",
    re.IGNORECASE,
)
_SENSITIVE_METADATA_KEY_MARKERS = (
    "raw",
    "token",
    "secret",
    "credential",
    "password",
    "cookie",
    "path",
    "transcript",
    "hidden",
    "reasoning",
    "production",
    "attached",
    "enabled",
    "allowed",
    "performed",
    "authority",
    "route",
    "called",
    "fetched",
    "executed",
    "injected",
    "authoritative",
    "trust",
    "trusted",
    "verified",
    "valid",
    "network",
    "scheduler",
    "background",
)


class MissionStateStorePort(Protocol):
    openmagi_local_fake_provider: bool

    def save_goal(self, goal: GoalRecord) -> GoalRecord: ...

    def get_goal(self, goal_id: str) -> GoalRecord | None: ...

    def save_task(self, task: BackgroundTaskRecord) -> BackgroundTaskRecord: ...

    def get_task(self, task_id: str) -> BackgroundTaskRecord | None: ...

    def list_tasks(self) -> Sequence[BackgroundTaskRecord]: ...


class MissionSchedulerPort(Protocol):
    openmagi_local_fake_provider: bool

    def record_tick(
        self,
        request: MissionRuntimeRequest,
        goal: GoalRecord | None,
    ) -> Mapping[str, object]: ...


class MissionRuntimeConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_state_store_enabled: bool = Field(
        default=False,
        alias="localFakeStateStoreEnabled",
    )
    local_fake_scheduler_enabled: bool = Field(
        default=False,
        alias="localFakeSchedulerEnabled",
    )
    background_scheduler_attached: Literal[False] = Field(
        default=False,
        alias="backgroundSchedulerAttached",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["backgroundSchedulerAttached"] = False
        payload.pop("background_scheduler_attached", None)
        payload["productionWritesEnabled"] = False
        payload.pop("production_writes_enabled", None)
        payload["routeAttached"] = False
        payload.pop("route_attached", None)
        return payload

    @field_serializer(
        "background_scheduler_attached",
        "production_writes_enabled",
        "route_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

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
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, deep
        return self.model_copy(update=update)


class MissionRuntimeAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    background_scheduler_attached: Literal[False] = Field(
        default=False,
        alias="backgroundSchedulerAttached",
    )
    background_task_started: Literal[False] = Field(
        default=False,
        alias="backgroundTaskStarted",
    )
    real_child_runner_invoked: Literal[False] = Field(
        default=False,
        alias="realChildRunnerInvoked",
    )
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    model_call_enabled: Literal[False] = Field(default=False, alias="modelCallEnabled")
    provider_call_enabled: Literal[False] = Field(default=False, alias="providerCallEnabled")
    tool_execution_enabled: Literal[False] = Field(default=False, alias="toolExecutionEnabled")
    child_execution_enabled: Literal[False] = Field(default=False, alias="childExecutionEnabled")
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_write_enabled: Literal[False] = Field(default=False, alias="memoryWriteEnabled")

    @model_validator(mode="before")
    @classmethod
    def _force_false_authority_flags(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for alias, field_name in (
            ("backgroundSchedulerAttached", "background_scheduler_attached"),
            ("backgroundTaskStarted", "background_task_started"),
            ("realChildRunnerInvoked", "real_child_runner_invoked"),
            ("productionWritesEnabled", "production_writes_enabled"),
            ("routeAttached", "route_attached"),
            ("modelCallEnabled", "model_call_enabled"),
            ("providerCallEnabled", "provider_call_enabled"),
            ("toolExecutionEnabled", "tool_execution_enabled"),
            ("childExecutionEnabled", "child_execution_enabled"),
            ("channelDeliveryEnabled", "channel_delivery_enabled"),
            ("schedulerAttached", "scheduler_attached"),
            ("workspaceMutationEnabled", "workspace_mutation_enabled"),
            ("memoryWriteEnabled", "memory_write_enabled"),
        ):
            payload[alias] = False
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
        "background_scheduler_attached",
        "background_task_started",
        "real_child_runner_invoked",
        "production_writes_enabled",
        "route_attached",
        "model_call_enabled",
        "provider_call_enabled",
        "tool_execution_enabled",
        "child_execution_enabled",
        "channel_delivery_enabled",
        "scheduler_attached",
        "workspace_mutation_enabled",
        "memory_write_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class GoalBudget(BaseModel):
    model_config = _MODEL_CONFIG

    max_turns: int = Field(default=30, alias="maxTurns", ge=1, le=50)
    turns_used: int = Field(default=0, alias="turnsUsed", ge=0)
    max_tokens: int | None = Field(default=None, alias="maxTokens", ge=1)
    tokens_used: int = Field(default=0, alias="tokensUsed", ge=0)
    max_seconds: int | None = Field(default=None, alias="maxSeconds", ge=1)
    elapsed_seconds: int = Field(default=0, alias="elapsedSeconds", ge=0)


class GoalProgressState(BaseModel):
    model_config = _MODEL_CONFIG

    current_step: str | None = Field(default=None, alias="currentStep")
    percent_complete: int | None = Field(default=None, alias="percentComplete", ge=0, le=100)

    @field_validator("current_step")
    @classmethod
    def _sanitize_step(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _sanitize_public_text(value)[:240]


class CompletionAudit(BaseModel):
    model_config = _MODEL_CONFIG

    completed_at: int = Field(alias="completedAt", ge=0)
    summary: str
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")

    @field_validator("summary")
    @classmethod
    def _sanitize_summary(cls, value: str) -> str:
        clean = _sanitize_public_text(value)
        if not clean:
            raise ValueError("completion summary must be non-empty")
        return clean[:500]

    @field_validator("evidence_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)


class GoalRecord(BaseModel):
    model_config = _MODEL_CONFIG

    goal_id: str = Field(alias="goalId")
    objective: str
    status: GoalStatus
    budget: GoalBudget = Field(default_factory=GoalBudget)
    created_at: int = Field(default=0, alias="createdAt", ge=0)
    updated_at: int = Field(default=0, alias="updatedAt", ge=0)
    progress: GoalProgressState = Field(default_factory=GoalProgressState)
    completion_audit: CompletionAudit | None = Field(default=None, alias="completionAudit")

    @field_validator("goal_id")
    @classmethod
    def _validate_goal_id(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("objective")
    @classmethod
    def _sanitize_objective(cls, value: str) -> str:
        clean = _sanitize_public_text(value)
        if not clean:
            raise ValueError("goal objective must be non-empty")
        return clean[:500]


class BackgroundTaskRecord(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str = Field(alias="taskId")
    parent_turn_id: str | None = Field(default=None, alias="parentTurnId")
    status: BackgroundTaskStatus
    prompt_preview: str = Field(alias="promptPreview")
    session_key: str | None = Field(default=None, alias="sessionKey")
    mission_id: str | None = Field(default=None, alias="missionId")
    created_at: int = Field(default=0, alias="createdAt", ge=0)
    updated_at: int = Field(default=0, alias="updatedAt", ge=0)

    @field_validator("task_id", "parent_turn_id", "session_key", "mission_id")
    @classmethod
    def _validate_optional_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value)

    @field_validator("prompt_preview")
    @classmethod
    def _sanitize_prompt_preview(cls, value: str) -> str:
        return _sanitize_public_text(value)[:300]


class MissionRuntimeRequest(BaseModel):
    model_config = _MODEL_CONFIG

    operation: MissionOperation
    goal_id: str | None = Field(default=None, alias="goalId")
    objective: str | None = None
    budget: GoalBudget | None = None
    progress_note: str | None = Field(default=None, alias="progressNote")
    completion_summary: str | None = Field(default=None, alias="completionSummary")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    task_id: str | None = Field(default=None, alias="taskId")
    parent_turn_id: str | None = Field(default=None, alias="parentTurnId")
    session_key: str | None = Field(default=None, alias="sessionKey")
    task_status_filter: BackgroundTaskStatus | None = Field(
        default=None,
        alias="taskStatusFilter",
    )
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None
    wait_timeout_ms: int = Field(default=0, alias="waitTimeoutMs", ge=0, le=300_000)
    now: int = Field(default=0, ge=0)
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("goal_id", "task_id", "parent_turn_id", "session_key", "cursor")
    @classmethod
    def _validate_optional_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)


class MissionRuntimeDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: MissionRuntimeStatus
    operation: MissionOperation
    goal: GoalRecord | None = None
    task: BackgroundTaskRecord | None = None
    tasks: tuple[BackgroundTaskRecord, ...] = ()
    scheduler_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="schedulerMetadata",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: MissionRuntimeAuthorityFlags = Field(
        default_factory=MissionRuntimeAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = MissionRuntimeAuthorityFlags()
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data["authorityFlags"] = MissionRuntimeAuthorityFlags()
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "operation": self.operation,
            "goal": None if self.goal is None else _safe_goal_projection(self.goal),
            "task": None if self.task is None else _safe_task_projection(self.task),
            "tasks": [_safe_task_projection(task) for task in self.tasks],
            "schedulerMetadata": _safe_metadata(self.scheduler_metadata),
            "reasonCodes": list(self.reason_codes),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class MissionChildTaskIntent(BaseModel):
    model_config = _MODEL_CONFIG

    task_id: str = Field(alias="taskId")
    goal_ref: str = Field(alias="goalRef")
    role: MissionChildRole
    prompt_preview: str = Field(alias="promptPreview")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    workspace_policy_ref: str | None = Field(default=None, alias="workspacePolicyRef")
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")

    @model_validator(mode="before")
    @classmethod
    def _force_execution_denied(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        payload["executionAllowed"] = False
        payload.pop("execution_allowed", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["executionAllowed"] = False
        values.pop("execution_allowed", None)
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        payload["executionAllowed"] = False
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude
        return self.model_copy(update=update, deep=deep)

    @field_serializer("execution_allowed")
    def _serialize_execution_denied(self, _value: object) -> bool:
        return False

    @field_validator("task_id", "goal_ref", "workspace_policy_ref")
    @classmethod
    def _validate_optional_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_ref(value)

    @field_validator("prompt_preview")
    @classmethod
    def _sanitize_prompt_preview(cls, value: str) -> str:
        return _sanitize_public_text(value)[:300]

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)


class MissionSchedulerDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: MissionSchedulerDecisionStatus
    stop_condition: str = Field(alias="stopCondition")
    child_task_intents: tuple[MissionChildTaskIntent, ...] = Field(
        default=(),
        alias="childTaskIntents",
    )
    next_tick_after: int | None = Field(default=None, alias="nextTickAfter", ge=0)
    lease_ref: str | None = Field(default=None, alias="leaseRef")
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    authority_flags: MissionRuntimeAuthorityFlags = Field(
        default_factory=MissionRuntimeAuthorityFlags,
        alias="authorityFlags",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = MissionRuntimeAuthorityFlags()
        return cls.model_validate(values)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "stopCondition": _sanitize_public_text(self.stop_condition)[:160],
            "childTaskIntents": [
                {
                    "taskId": _public_ref(intent.task_id, prefix="task"),
                    "goalRef": _public_ref(intent.goal_ref, prefix="goal"),
                    "role": intent.role,
                    "promptPreview": _sanitize_public_text(intent.prompt_preview)[:300],
                    "evidenceRefs": [
                        _public_ref(ref, prefix="evidence")
                        for ref in intent.evidence_refs
                    ],
                    "workspacePolicyRef": (
                        None
                        if intent.workspace_policy_ref is None
                        else _public_ref(intent.workspace_policy_ref, prefix="workspace")
                    ),
                    "executionAllowed": False,
                }
                for intent in self.child_task_intents
            ],
            "nextTickAfter": self.next_tick_after,
            "leaseRef": None if self.lease_ref is None else _public_ref(self.lease_ref, prefix="lease"),
            "diagnosticMetadata": _safe_metadata(self.diagnostic_metadata),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


def build_mission_scheduler_decision(
    *,
    goal: GoalRecord | None,
    now: int = 0,
    evidence_refs: tuple[str, ...] = (),
) -> MissionSchedulerDecision:
    if goal is None:
        return _scheduler_decision("suppressed", "goal_absent", (), now)
    if goal.status == "paused":
        return _scheduler_decision("suppressed", "goal_paused", (), now, goal=goal)
    if goal.status in {"completed", "cancelled", "blocked"}:
        return _scheduler_decision("suppressed", "goal_terminal", (), now, goal=goal)
    if goal.budget.turns_used >= goal.budget.max_turns:
        return _scheduler_decision("suppressed", "budget_exhausted", (), now, goal=goal)

    intent = MissionChildTaskIntent(
        taskId=_generated_task_id(f"{goal.goal_id}:{now}:implementer"),
        goalRef=goal.goal_id,
        role="implementer",
        promptPreview=goal.objective,
        evidenceRefs=evidence_refs,
        workspacePolicyRef="workspace-policy:isolated-child-worktree",
        executionAllowed=False,
    )
    return _scheduler_decision(
        "planned",
        "child_intent_planned",
        (intent,),
        now,
        goal=goal,
    )


def build_scheduler_tick_lock_metadata(
    *,
    tickId: str,
    tickLockRef: str,
    overlapDetected: bool = False,
    recursiveDenied: bool = False,
) -> dict[str, object]:
    return {
        "tickId": _safe_tick_ref(tickId, field_name="tickId", prefix="tick:"),
        "tickLockRef": _safe_tick_ref(
            tickLockRef,
            field_name="tickLockRef",
            prefix="tick-lock:",
        ),
        "overlapPolicy": "skip-if-held",
        "overlapDetected": bool(overlapDetected),
        "recursiveDenied": bool(recursiveDenied),
        "recordOnly": True,
    }


class MissionRuntimeBoundary:
    """Local fake mission/goal/scheduler/task runtime boundary.

    This is not a scheduler loop and does not start background work. It records
    disabled-by-default lifecycle state against injected fakes only.
    """

    def __init__(self, config: MissionRuntimeConfig) -> None:
        self.config = MissionRuntimeConfig.model_validate(config.model_dump(by_alias=True))

    def execute(
        self,
        request: MissionRuntimeRequest,
        *,
        state_store: MissionStateStorePort | None = None,
        scheduler: MissionSchedulerPort | None = None,
    ) -> MissionRuntimeDecision:
        diagnostics = {
            "enabled": self.config.enabled,
            "localFakeStateStoreEnabled": self.config.local_fake_state_store_enabled,
            "localFakeSchedulerEnabled": self.config.local_fake_scheduler_enabled,
            "backgroundSchedulerAttached": False,
            "productionWritesEnabled": False,
            "routeAttached": False,
            **dict(request.metadata),
        }
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                reason_codes=("mission_runtime_disabled",),
                diagnostics=diagnostics,
            )
        if request.operation.startswith("goal."):
            return self._goal_operation(request, state_store, diagnostics)
        if request.operation == "scheduler.tick":
            return self._scheduler_tick(request, state_store, scheduler, diagnostics)
        if request.operation.startswith("task."):
            return self._task_operation(request, state_store, diagnostics)
        return _decision(
            request,
            "goal_intent",
            reason_codes=("methodology_plan_metadata_only",),
            diagnostics=diagnostics,
        )

    def _goal_operation(
        self,
        request: MissionRuntimeRequest,
        state_store: MissionStateStorePort | None,
        diagnostics: Mapping[str, object],
    ) -> MissionRuntimeDecision:
        if not self.config.local_fake_state_store_enabled or state_store is None:
            return _decision(
                request,
                "goal_intent",
                reason_codes=("local_mission_state_store_disabled",),
                diagnostics=diagnostics,
            )
        if not _is_local_fake_provider(state_store):
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_state_store_untrusted",),
                diagnostics=diagnostics,
            )
        if request.operation == "goal.create":
            goal = GoalRecord(
                goalId=request.goal_id or _generated_goal_id(request.objective or ""),
                objective=request.objective or "",
                status="running",
                budget=request.budget or GoalBudget(),
                createdAt=request.now,
                updatedAt=request.now,
            )
            try:
                saved_goal = state_store.save_goal(goal)
            except Exception as exc:
                return _local_fake_error_decision(
                    request,
                    reason_code="local_fake_state_store_error",
                    diagnostics={**diagnostics, "providerError": str(exc)},
                )
            return _decision(
                request,
                "goal_recorded_local_fake",
                goal=saved_goal,
                reason_codes=("local_fake_goal_create_receipt_only",),
                diagnostics=diagnostics,
            )
        try:
            goal = _load_goal(request, state_store)
        except Exception as exc:
            return _local_fake_error_decision(
                request,
                reason_code="local_fake_state_store_error",
                diagnostics={**diagnostics, "providerError": str(exc)},
            )
        if goal is None:
            return _decision(
                request,
                "blocked",
                reason_codes=("goal_not_found",),
                diagnostics=diagnostics,
            )
        if request.operation == "goal.progress":
            return self._progress_goal(request, state_store, goal, diagnostics)
        if request.operation == "goal.complete":
            updated = goal.model_copy(
                update={
                    "status": "completed",
                    "updated_at": request.now,
                    "completion_audit": CompletionAudit(
                        completedAt=request.now,
                        summary=request.completion_summary or "Goal completed.",
                        evidenceRefs=request.evidence_refs,
                    ),
                },
            )
        elif request.operation == "goal.pause":
            updated = goal.model_copy(update={"status": "paused", "updated_at": request.now})
        elif request.operation == "goal.resume":
            updated = goal.model_copy(update={"status": "running", "updated_at": request.now})
        elif request.operation == "goal.cancel":
            updated = goal.model_copy(update={"status": "cancelled", "updated_at": request.now})
        else:
            updated = goal
        try:
            saved_goal = state_store.save_goal(updated)
        except Exception as exc:
            return _local_fake_error_decision(
                request,
                reason_code="local_fake_state_store_error",
                diagnostics={**diagnostics, "providerError": str(exc)},
            )
        return _decision(
            request,
            "goal_recorded_local_fake",
            goal=saved_goal,
            reason_codes=(f"{request.operation.replace('.', '_')}_receipt_only",),
            diagnostics=diagnostics,
        )

    def _progress_goal(
        self,
        request: MissionRuntimeRequest,
        state_store: MissionStateStorePort,
        goal: GoalRecord,
        diagnostics: Mapping[str, object],
    ) -> MissionRuntimeDecision:
        new_budget = goal.budget.model_copy(
            update={"turns_used": goal.budget.turns_used + 1},
        )
        exhausted = new_budget.turns_used >= new_budget.max_turns
        updated = goal.model_copy(
            update={
                "status": "blocked" if exhausted else "running",
                "budget": new_budget,
                "updated_at": request.now,
                "progress": GoalProgressState(currentStep=request.progress_note),
            },
        )
        try:
            saved_goal = state_store.save_goal(updated)
        except Exception as exc:
            return _local_fake_error_decision(
                request,
                reason_code="local_fake_state_store_error",
                diagnostics={**diagnostics, "providerError": str(exc)},
            )
        return _decision(
            request,
            "goal_recorded_local_fake",
            goal=saved_goal,
            reason_codes=(
                ("goal_budget_exhausted",)
                if exhausted
                else ("local_fake_goal_progress_receipt_only",)
            ),
            diagnostics=diagnostics,
        )

    def _scheduler_tick(
        self,
        request: MissionRuntimeRequest,
        state_store: MissionStateStorePort | None,
        scheduler: MissionSchedulerPort | None,
        diagnostics: Mapping[str, object],
    ) -> MissionRuntimeDecision:
        try:
            tick_lock_metadata = _request_tick_lock_metadata(request)
        except ValueError:
            return _decision(
                request,
                "blocked",
                scheduler_metadata={"recordOnly": True},
                reason_codes=("scheduler_tick_lock_metadata_invalid",),
                diagnostics=_without_scheduler_tick_inputs(diagnostics),
            )
        if state_store is not None and not _is_local_fake_provider(state_store):
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_state_store_untrusted",),
                diagnostics=diagnostics,
            )
        try:
            goal = _load_goal(request, state_store) if state_store is not None else None
        except Exception as exc:
            return _local_fake_error_decision(
                request,
                reason_code="local_fake_state_store_error",
                diagnostics={**diagnostics, "providerError": str(exc)},
            )
        if tick_lock_metadata.get("recursiveDenied") is True:
            return _decision(
                request,
                "blocked",
                goal=goal,
                scheduler_metadata=tick_lock_metadata,
                reason_codes=("recursive_scheduler_denied",),
                diagnostics=diagnostics,
            )
        if not self.config.local_fake_scheduler_enabled or scheduler is None:
            return _decision(
                request,
                "scheduler_intent",
                goal=goal,
                scheduler_metadata=tick_lock_metadata,
                reason_codes=("local_scheduler_disabled",),
                diagnostics=diagnostics,
            )
        if not _is_local_fake_provider(scheduler):
            return _decision(
                request,
                "blocked",
                goal=goal,
                reason_codes=("local_fake_scheduler_untrusted",),
                diagnostics=diagnostics,
            )
        try:
            metadata = scheduler.record_tick(request, goal)
        except Exception as exc:
            return _local_fake_error_decision(
                request,
                reason_code="local_fake_scheduler_error",
                diagnostics={**diagnostics, "providerError": str(exc)},
            )
        metadata = {**dict(metadata), **tick_lock_metadata}
        return _decision(
            request,
            "scheduler_tick_recorded_local_fake",
            goal=goal,
            scheduler_metadata=metadata,
            reason_codes=("local_fake_scheduler_tick_receipt_only",),
            diagnostics=diagnostics,
        )

    def _task_operation(
        self,
        request: MissionRuntimeRequest,
        state_store: MissionStateStorePort | None,
        diagnostics: Mapping[str, object],
    ) -> MissionRuntimeDecision:
        if not self.config.local_fake_state_store_enabled or state_store is None:
            return _decision(
                request,
                "task_intent",
                reason_codes=("local_task_state_store_disabled",),
                diagnostics=diagnostics,
            )
        if not _is_local_fake_provider(state_store):
            return _decision(
                request,
                "blocked",
                reason_codes=("local_fake_state_store_untrusted",),
                diagnostics=diagnostics,
            )
        if request.operation == "task.create":
            task = BackgroundTaskRecord(
                taskId=request.task_id or _generated_task_id(request.objective or ""),
                parentTurnId=request.parent_turn_id,
                status="running",
                promptPreview=request.objective or "",
                sessionKey=request.session_key,
                missionId=request.goal_id,
                createdAt=request.now,
                updatedAt=request.now,
            )
            try:
                saved_task = state_store.save_task(task)
            except Exception as exc:
                return _local_fake_error_decision(
                    request,
                    reason_code="local_fake_state_store_error",
                    diagnostics={**diagnostics, "providerError": str(exc)},
                )
            return _decision(
                request,
                "task_recorded_local_fake",
                task=saved_task,
                reason_codes=("local_fake_task_create_receipt_only",),
                diagnostics=diagnostics,
            )
        if request.operation == "task.list":
            try:
                tasks = _filter_tasks(request, tuple(state_store.list_tasks()))
            except Exception as exc:
                return _local_fake_error_decision(
                    request,
                    reason_code="local_fake_state_store_error",
                    diagnostics={**diagnostics, "providerError": str(exc)},
                )
            return _decision(
                request,
                "task_recorded_local_fake",
                tasks=tasks,
                reason_codes=("task_list_metadata_only",),
                diagnostics=diagnostics,
            )
        try:
            task = state_store.get_task(request.task_id or "") if request.task_id else None
        except Exception as exc:
            return _local_fake_error_decision(
                request,
                reason_code="local_fake_state_store_error",
                diagnostics={**diagnostics, "providerError": str(exc)},
            )
        if task is None:
            return _decision(
                request,
                "blocked",
                reason_codes=("task_not_found",),
                diagnostics=diagnostics,
            )
        if request.operation == "task.stop":
            task = task.model_copy(update={"status": "aborted", "updated_at": request.now})
            try:
                task = state_store.save_task(task)
            except Exception as exc:
                return _local_fake_error_decision(
                    request,
                    reason_code="local_fake_state_store_error",
                    diagnostics={**diagnostics, "providerError": str(exc)},
                )
            reason_codes = ("task_stop_receipt_only",)
        elif request.operation == "task.wait":
            reason_codes = (
                (
                    "task_wait_terminal_metadata_only"
                    if task.status in {"completed", "failed", "aborted", "blocked"}
                    else "task_wait_pending_metadata_only"
                ),
            )
        elif request.operation == "task.get":
            reason_codes = ("task_get_metadata_only",)
        else:
            reason_codes = (f"{request.operation.replace('.', '_')}_receipt_only",)
        return _decision(
            request,
            "task_recorded_local_fake",
            task=task,
            reason_codes=reason_codes,
            diagnostics=diagnostics,
        )


def _scheduler_decision(
    status: MissionSchedulerDecisionStatus,
    stop_condition: str,
    child_intents: tuple[MissionChildTaskIntent, ...],
    now: int,
    *,
    goal: GoalRecord | None = None,
) -> MissionSchedulerDecision:
    metadata: dict[str, object] = {
        "goalId": None if goal is None else goal.goal_id,
        "planOnly": True,
        "backgroundTaskStarted": False,
        "realChildRunnerInvoked": False,
    }
    return MissionSchedulerDecision(
        status=status,
        stopCondition=stop_condition,
        childTaskIntents=child_intents,
        nextTickAfter=now,
        leaseRef=(
            None
            if goal is None
            else f"mission-lease:{hashlib.sha1(f'{goal.goal_id}:{now}'.encode('utf-8')).hexdigest()[:16]}"
        ),
        diagnosticMetadata=metadata,
        authorityFlags=MissionRuntimeAuthorityFlags(),
    )


def _decision(
    request: MissionRuntimeRequest,
    status: MissionRuntimeStatus,
    *,
    goal: GoalRecord | None = None,
    task: BackgroundTaskRecord | None = None,
    tasks: tuple[BackgroundTaskRecord, ...] = (),
    scheduler_metadata: Mapping[str, object] | None = None,
    reason_codes: tuple[str, ...],
    diagnostics: Mapping[str, object],
) -> MissionRuntimeDecision:
    return MissionRuntimeDecision(
        status=status,
        operation=request.operation,
        goal=goal,
        task=task,
        tasks=tasks,
        schedulerMetadata={} if scheduler_metadata is None else _safe_metadata(scheduler_metadata),
        reasonCodes=reason_codes,
        diagnosticMetadata=_safe_metadata(diagnostics),
        authorityFlags=MissionRuntimeAuthorityFlags(),
    )


def _local_fake_error_decision(
    request: MissionRuntimeRequest,
    *,
    reason_code: str,
    diagnostics: Mapping[str, object],
) -> MissionRuntimeDecision:
    return _decision(
        request,
        "blocked",
        reason_codes=(reason_code,),
        diagnostics=diagnostics,
    )


def _filter_tasks(
    request: MissionRuntimeRequest,
    tasks: tuple[BackgroundTaskRecord, ...],
) -> tuple[BackgroundTaskRecord, ...]:
    filtered = tasks
    if request.task_status_filter is not None:
        filtered = tuple(task for task in filtered if task.status == request.task_status_filter)
    if request.session_key is not None:
        filtered = tuple(task for task in filtered if task.session_key == request.session_key)
    start = 0
    if request.cursor is not None:
        for index, task in enumerate(filtered):
            if task.task_id == request.cursor:
                start = index + 1
                break
    return filtered[start : start + request.limit]


def _load_goal(
    request: MissionRuntimeRequest,
    state_store: MissionStateStorePort | None,
) -> GoalRecord | None:
    if state_store is None or request.goal_id is None:
        return None
    return state_store.get_goal(request.goal_id)


def _request_tick_lock_metadata(request: MissionRuntimeRequest) -> dict[str, object]:
    recursive_requested = _metadata_bool(request.metadata, "recursiveRequested")
    tick_id = _metadata_str(request.metadata, "tickId")
    tick_lock_ref = _metadata_str(request.metadata, "tickLockRef")
    if recursive_requested:
        if tick_id is not None and tick_lock_ref is not None:
            try:
                return build_scheduler_tick_lock_metadata(
                    tickId=tick_id,
                    tickLockRef=tick_lock_ref,
                    overlapDetected=_metadata_bool(request.metadata, "overlapDetected"),
                    recursiveDenied=True,
                )
            except ValueError:
                return {"recursiveDenied": True, "recordOnly": True}
        return {"recursiveDenied": True, "recordOnly": True}
    if tick_id is None or tick_lock_ref is None:
        return {}
    return build_scheduler_tick_lock_metadata(
        tickId=tick_id,
        tickLockRef=tick_lock_ref,
        overlapDetected=_metadata_bool(request.metadata, "overlapDetected"),
        recursiveDenied=False,
    )


def _metadata_str(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a public ref")
    return value


def _metadata_bool(metadata: Mapping[str, object], key: str) -> bool:
    value = metadata.get(key)
    return value is True


def _without_scheduler_tick_inputs(metadata: Mapping[str, object]) -> dict[str, object]:
    omitted = {"tickId", "tickLockRef", "recursiveRequested", "overlapDetected"}
    return {str(key): value for key, value in metadata.items() if str(key) not in omitted}


def _is_local_fake_provider(provider: object) -> bool:
    return getattr(provider, "openmagi_local_fake_provider", False) is True


def _generated_goal_id(seed: str) -> str:
    return f"goal:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _generated_task_id(seed: str) -> str:
    return f"task:{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _safe_goal_projection(goal: GoalRecord) -> dict[str, object]:
    budget = goal.budget if isinstance(getattr(goal, "budget", None), GoalBudget) else GoalBudget()
    progress = (
        goal.progress
        if isinstance(getattr(goal, "progress", None), GoalProgressState)
        else GoalProgressState()
    )
    completion_audit = (
        goal.completion_audit
        if isinstance(getattr(goal, "completion_audit", None), CompletionAudit)
        else None
    )
    return {
        "goalId": _public_ref(goal.goal_id, prefix="goal"),
        "objective": _sanitize_public_text(goal.objective)[:500],
        "status": goal.status if goal.status in {"pending", "running", "paused", "completed", "cancelled", "blocked"} else "blocked",
        "budget": {
            "maxTurns": _safe_nonnegative_int(budget.max_turns, default=30),
            "turnsUsed": _safe_nonnegative_int(budget.turns_used),
            "maxTokens": (
                None
                if budget.max_tokens is None
                else _safe_nonnegative_int(budget.max_tokens)
            ),
            "tokensUsed": _safe_nonnegative_int(budget.tokens_used),
            "maxSeconds": (
                None
                if budget.max_seconds is None
                else _safe_nonnegative_int(budget.max_seconds)
            ),
            "elapsedSeconds": _safe_nonnegative_int(budget.elapsed_seconds),
        },
        "createdAt": _safe_nonnegative_int(goal.created_at),
        "updatedAt": _safe_nonnegative_int(goal.updated_at),
        "progress": {
            "currentStep": (
                None
                if progress.current_step is None
                else _sanitize_public_text(progress.current_step)[:240]
            ),
            "percentComplete": progress.percent_complete,
        },
        "completionAudit": (
            None
            if completion_audit is None
            else {
                "completedAt": _safe_nonnegative_int(completion_audit.completed_at),
                "summary": _sanitize_public_text(completion_audit.summary)[:500],
                "evidenceRefs": [
                    _public_ref(ref, prefix="evidence")
                    for ref in completion_audit.evidence_refs
                ],
            }
        ),
    }


def _safe_task_projection(task: BackgroundTaskRecord) -> dict[str, object]:
    return {
        "taskId": _public_ref(task.task_id, prefix="task"),
        "parentTurnId": (
            None if task.parent_turn_id is None else _public_ref(task.parent_turn_id, prefix="turn")
        ),
        "status": task.status if task.status in {"running", "completed", "failed", "aborted", "blocked"} else "blocked",
        "promptPreview": _sanitize_public_text(task.prompt_preview)[:300],
        "sessionKey": (
            None if task.session_key is None else _public_ref(task.session_key, prefix="session")
        ),
        "missionId": (
            None if task.mission_id is None else _public_ref(task.mission_id, prefix="mission")
        ),
        "createdAt": _safe_nonnegative_int(task.created_at),
        "updatedAt": _safe_nonnegative_int(task.updated_at),
    }


def _public_ref(value: str, *, prefix: str) -> str:
    try:
        return _safe_ref(str(value))
    except ValueError:
        return f"{prefix}:{hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:16]}"


def _safe_nonnegative_int(value: object, *, default: int = 0) -> int:
    return value if isinstance(value, int) and value >= 0 else default


def _safe_ref(value: str) -> str:
    clean = _sanitize_public_text(value.strip())
    if not clean or not _REF_RE.fullmatch(clean):
        raise ValueError("mission refs must be public identifiers")
    return clean[:180]


def _safe_tick_ref(value: str, *, field_name: str, prefix: str) -> str:
    clean = _safe_ref(value)
    if not clean.startswith(prefix) or len(clean) == len(prefix):
        raise ValueError(f"{field_name} must use {prefix} public identifier")
    normalized_suffix = re.sub(
        r"[^a-z0-9]",
        "",
        clean.removeprefix(prefix).casefold(),
    )
    if any(
        normalized_suffix.startswith(marker)
        for marker in (
            "activation",
            "authority",
            "browser",
            "channel",
            "child",
            "db",
            "env",
            "gate2",
            "gate8",
            "k8s",
            "kubernetes",
            "live",
            "memory",
            "missionruntime",
            "model",
            "provider",
            "route",
            "scheduler",
            "tool",
            "traffic",
            "workspace",
        )
    ):
        raise ValueError(f"{field_name} must not imply live authority")
    return clean


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if any(marker in normalized_key for marker in _SENSITIVE_METADATA_KEY_MARKERS):
            continue
        if isinstance(value, str):
            if _is_authority_metadata_value(value):
                continue
            safe[str(key)] = _sanitize_public_text(value)
        elif isinstance(value, bool | int | float) or value is None:
            safe[str(key)] = value
    return safe


def _is_authority_metadata_value(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(
        normalized.startswith(marker)
        for marker in (
            "activation",
            "authority",
            "browser",
            "channel",
            "child",
            "db",
            "env",
            "gate",
            "gate2",
            "gate8",
            "k8s",
            "kubernetes",
            "live",
            "memory",
            "missionruntime",
            "model",
            "provider",
            "route",
            "scheduler",
            "tool",
            "traffic",
            "workspace",
        )
    )


def _sanitize_public_text(value: str) -> str:
    safe_lines = [
        line
        for line in value.splitlines()
        if line.strip() and not _RAW_PRIVATE_LINE_RE.search(line)
    ]
    clean = "\n".join(safe_lines)
    clean = _SECRET_TEXT_RE.sub("[redacted]", clean)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    return clean.strip()


__all__ = [
    "BackgroundTaskRecord",
    "CompletionAudit",
    "GoalBudget",
    "GoalProgressState",
    "GoalRecord",
    "MissionChildTaskIntent",
    "MissionRuntimeAuthorityFlags",
    "MissionRuntimeBoundary",
    "MissionRuntimeConfig",
    "MissionRuntimeDecision",
    "MissionRuntimeRequest",
    "MissionSchedulerDecision",
    "MissionSchedulerPort",
    "MissionStateStorePort",
    "build_mission_scheduler_decision",
    "build_scheduler_tick_lock_metadata",
]
