from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.harness.goal_loop import GoalLoopPolicy
from openmagi_core_agent.recipes.compiler import MissionLifecycleMetadata
from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview


MissionLifecycleCategory = Literal[
    "mission_identity",
    "goal_continuation",
    "operator_control",
    "scheduled_work",
    "pipeline_artifact_handoff",
    "long_running_tool_boundary",
]
MissionLifecycleStatus = Literal[
    "queued",
    "running",
    "completed",
    "blocked",
    "failed",
    "cancelled",
    "abandoned",
]
MissionLifecycleDecision = Literal["metadata_only", "blocked"]
ContinuationDecision = Literal[
    "continue",
    "done",
    "blocked",
    "needs_user",
    "budget_exhausted",
    "cancelled",
]
ScheduledWorkMode = Literal["agent", "script"]
ScheduledWorkStatus = Literal["running", "completed", "blocked", "failed", "timed_out", "cancelled"]
OperatorActionType = Literal["cancel", "retry", "resume", "unblock"]
OperatorActionResult = Literal["recorded_for_current_run", "recorded_for_future_run"]
LongRunningUnitOfWork = Literal["long_tool_job"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SHA256_REF_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:missions?|schedulers?)(?:[\\/]|$)|"
    r"(?:^|[\\/])(?:mission|scheduler)-store(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_missionsecret",
    "sk-mission-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "gateway token",
    "hidden reasoning",
    "pythonResponseAuthority",
)
_FORBIDDEN_PUBLIC_TOKENS_NORMALIZED = tuple(
    token.casefold() for token in _FORBIDDEN_PUBLIC_TOKENS
)
_SECRET_LIKE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_key|authorization|cookie|credentials?|password|passphrase|"
    r"private_key|client_secret|service_role|service_role_key|secret|secret_key|"
    r"token|access_token|auth_token|bearer_token|refresh_token|session_token)(?:_|$)",
    re.IGNORECASE,
)
_SECRET_SHAPED_VALUE_RE = re.compile(
    r"\b(?:Bearer\s+[A-Za-z0-9._~+/=-]+|gh[opusr]_[A-Za-z0-9_]+|"
    r"sk-[A-Za-z0-9._-]+|[rs]k_(?:live|test)_[A-Za-z0-9_]+)\b",
    re.IGNORECASE,
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_invocation_authority",
        "background_resume_attached",
        "background_resume_authority",
        "canary_traffic_attached",
        "channel_delivery_attached",
        "channel_delivery_authority",
        "code_executed",
        "evidence_block_enabled",
        "hosted_mission_writes_authority",
        "live_child_execution_attached",
        "live_continuation_attached",
        "mission_store_written",
        "operator_polling_attached",
        "operator_polling_authority",
        "production_authority",
        "production_storage_written",
        "route_api_dashboard_proxy_deploy_authority",
        "route_or_api_attached",
        "scheduler_ticks_authority",
        "scheduler_tick_attached",
        "shell_executed",
        "telegram_attached",
        "tool_host_live_dispatch_authority",
        "tool_dispatched_live",
        "traffic_attached",
        "workspace_mutated",
    }
)
_REQUIRED_CATEGORIES = set(MissionLifecycleCategory.__args__)  # type: ignore[attr-defined]
_SCRIPT_TIMEOUT_CAP_MS = 300_000
_AGENT_TIMEOUT_CAP_MS = 600_000


class MissionLifecycleAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    tool_dispatched_live: Literal[False] = Field(default=False, alias="toolDispatchedLive")
    mission_store_written: Literal[False] = Field(default=False, alias="missionStoreWritten")
    scheduler_tick_attached: Literal[False] = Field(
        default=False,
        alias="schedulerTickAttached",
    )
    background_resume_attached: Literal[False] = Field(
        default=False,
        alias="backgroundResumeAttached",
    )
    channel_delivery_attached: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAttached",
    )
    operator_polling_attached: Literal[False] = Field(
        default=False,
        alias="operatorPollingAttached",
    )
    live_continuation_attached: Literal[False] = Field(
        default=False,
        alias="liveContinuationAttached",
    )
    live_child_execution_attached: Literal[False] = Field(
        default=False,
        alias="liveChildExecutionAttached",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

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
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "tool_dispatched_live",
        "mission_store_written",
        "scheduler_tick_attached",
        "background_resume_attached",
        "channel_delivery_attached",
        "operator_polling_attached",
        "live_continuation_attached",
        "live_child_execution_attached",
        "workspace_mutated",
        "production_storage_written",
        "production_authority",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MissionTsParityInputs(BaseModel):
    model_config = _MODEL_CONFIG

    core_agent_goal_loop: bool = Field(default=False, alias="CORE_AGENT_GOAL_LOOP")
    core_agent_missions: bool = Field(default=False, alias="CORE_AGENT_MISSIONS")
    core_agent_script_cron: bool = Field(default=False, alias="CORE_AGENT_SCRIPT_CRON")

    def as_env_strings(self) -> dict[str, str]:
        return {
            "CORE_AGENT_GOAL_LOOP": "1" if self.core_agent_goal_loop else "0",
            "CORE_AGENT_MISSIONS": "1" if self.core_agent_missions else "0",
            "CORE_AGENT_SCRIPT_CRON": "1" if self.core_agent_script_cron else "0",
        }


class PersistentGoalLoopActivationDefaults(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: Literal[False] = False
    scheduling_enabled: Literal[False] = Field(default=False, alias="schedulingEnabled")
    background_resume_enabled: Literal[False] = Field(
        default=False,
        alias="backgroundResumeEnabled",
    )
    ts_env_activation_honored: Literal[False] = Field(
        default=False,
        alias="tsEnvActivationHonored",
    )


class ScheduledWorkActivationDefaults(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: Literal[False] = False
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    channel_delivery_attached: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAttached",
    )
    ts_env_activation_honored: Literal[False] = Field(
        default=False,
        alias="tsEnvActivationHonored",
    )


class MissionOperatorControlsActivationDefaults(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: Literal[False] = False
    polling_attached: Literal[False] = Field(default=False, alias="pollingAttached")
    idempotency_required: Literal[True] = Field(default=True, alias="idempotencyRequired")
    ts_env_activation_honored: Literal[False] = Field(
        default=False,
        alias="tsEnvActivationHonored",
    )


class MissionActivationDefaults(BaseModel):
    model_config = _MODEL_CONFIG

    persistent_goal_loop: PersistentGoalLoopActivationDefaults = Field(
        default_factory=PersistentGoalLoopActivationDefaults,
        alias="persistentGoalLoop",
    )
    scheduled_work: ScheduledWorkActivationDefaults = Field(
        default_factory=ScheduledWorkActivationDefaults,
        alias="scheduledWork",
    )
    operator_controls: MissionOperatorControlsActivationDefaults = Field(
        default_factory=MissionOperatorControlsActivationDefaults,
        alias="operatorControls",
    )


class MissionRuntimeAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    hosted_mission_writes_authority: Literal[False] = Field(
        default=False,
        alias="hostedMissionWritesAuthority",
    )
    scheduler_ticks_authority: Literal[False] = Field(
        default=False,
        alias="schedulerTicksAuthority",
    )
    background_resume_authority: Literal[False] = Field(
        default=False,
        alias="backgroundResumeAuthority",
    )
    operator_polling_authority: Literal[False] = Field(
        default=False,
        alias="operatorPollingAuthority",
    )
    channel_delivery_authority: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAuthority",
    )
    adk_runner_invocation_authority: Literal[False] = Field(
        default=False,
        alias="adkRunnerInvocationAuthority",
    )
    tool_host_live_dispatch_authority: Literal[False] = Field(
        default=False,
        alias="toolHostLiveDispatchAuthority",
    )
    route_api_dashboard_proxy_deploy_authority: Literal[False] = Field(
        default=False,
        alias="routeApiDashboardProxyDeployAuthority",
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
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "hosted_mission_writes_authority",
        "scheduler_ticks_authority",
        "background_resume_authority",
        "operator_polling_authority",
        "channel_delivery_authority",
        "adk_runner_invocation_authority",
        "tool_host_live_dispatch_authority",
        "route_api_dashboard_proxy_deploy_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MissionIdentityMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    task_id: str = Field(alias="taskId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    invocation_id: str = Field(alias="invocationId")
    execution_boundary_id: str = Field(alias="executionBoundaryId")
    idempotency_key: str = Field(alias="idempotencyKey")

    @model_validator(mode="after")
    def _validate_ids(self) -> Self:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("mission identity identifiers must be non-empty")
            _validate_public_value(value)
        return self


class MissionRecipeMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    pack_id: Literal["openmagi.missions"] = Field(alias="packId")
    mission_lifecycle: MissionLifecycleMetadata = Field(alias="missionLifecycle")
    native_plugin_boundary: Literal["openmagi.missions"] = Field(
        alias="nativePluginBoundary",
    )
    recipe_pack_refs: tuple[str, ...] = Field(alias="recipePackRefs")

    @model_validator(mode="after")
    def _validate_recipe_boundary(self) -> Self:
        if self.mission_lifecycle.enabled:
            raise ValueError("mission lifecycle fixture must remain disabled for live traffic")
        if self.mission_lifecycle.mission_uses_long_running_function_tool:
            raise ValueError("missions must not be modeled as LongRunningFunctionTool")
        if "openmagi.missions" not in self.recipe_pack_refs:
            raise ValueError("mission recipe metadata must include openmagi.missions")
        return self


class ContinuationStateSnapshot(BaseModel):
    model_config = _MODEL_CONFIG

    snapshot_id: str = Field(alias="snapshotId")
    objective_hash: str = Field(alias="objectiveHash")
    decision: ContinuationDecision
    reason: str
    turns_used: int = Field(alias="turnsUsed", ge=0)
    max_turns: int = Field(alias="maxTurns", ge=1, le=50)
    continuation_allowed: bool = Field(alias="continuationAllowed")
    cancellation_requested: bool = Field(alias="cancellationRequested")
    background_resume_attached: Literal[False] = Field(
        default=False,
        alias="backgroundResumeAttached",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")

    @model_validator(mode="after")
    def _validate_continuation(self) -> Self:
        if not self.snapshot_id.strip() or not self.reason.strip():
            raise ValueError("continuation snapshots require identifiers and reason")
        if not _SHA256_REF_RE.fullmatch(self.objective_hash):
            raise ValueError("objectiveHash must be a sha256:* reference")
        if self.turns_used >= self.max_turns and self.continuation_allowed:
            raise ValueError("continuation cannot remain allowed after budget exhaustion")
        if self.cancellation_requested and self.continuation_allowed:
            raise ValueError("cancelled missions cannot continue")
        if self.decision in {"budget_exhausted", "cancelled", "blocked", "needs_user"}:
            if self.continuation_allowed:
                raise ValueError("blocked continuation decisions cannot continue")
        if self.decision == "continue" and not self.continuation_allowed:
            raise ValueError("continue decision requires continuationAllowed=true")
        _validate_public_value(self.reason)
        return self


class MissionProgressEventMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    event_type: str = Field(alias="eventType")
    status: MissionLifecycleStatus
    message: str
    public_preview: str = Field(alias="publicPreview")
    redaction_profile: Literal["public-mission-progress"] = Field(alias="redactionProfile")
    created_at: int = Field(alias="createdAt", ge=0)

    @model_validator(mode="after")
    def _validate_progress(self) -> Self:
        for value in (
            self.mission_id,
            self.run_id,
            self.event_type,
            self.message,
            self.public_preview,
        ):
            if not value.strip():
                raise ValueError("mission progress fields must be non-empty")
            _validate_public_value(value)
        return self


class MissionOperatorActionMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    action_event_id: str = Field(alias="actionEventId")
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    action_type: OperatorActionType = Field(alias="actionType")
    reason: str
    actor_type: Literal["user", "operator", "system"] = Field(alias="actorType")
    created_at: int = Field(alias="createdAt", ge=0)
    result: OperatorActionResult
    checkpointed: bool
    idempotency_key: str | None = Field(alias="idempotencyKey")
    linked_task_ids: tuple[str, ...] = Field(default=(), alias="linkedTaskIds")
    linked_schedule_ids: tuple[str, ...] = Field(default=(), alias="linkedScheduleIds")
    polling_attached: Literal[False] = Field(default=False, alias="pollingAttached")

    @model_validator(mode="after")
    def _validate_operator_action(self) -> Self:
        if not self.checkpointed:
            raise ValueError("operator action metadata must be checkpointed")
        if self.idempotency_key is None or not self.idempotency_key.strip():
            raise ValueError("operator actions require idempotencyKey")
        for value in (
            self.action_event_id,
            self.mission_id,
            self.run_id,
            self.reason,
            *(self.linked_task_ids),
            *(self.linked_schedule_ids),
        ):
            if not value.strip():
                raise ValueError("operator action identifiers must be non-empty")
            _validate_public_value(value)
        _validate_public_value(self.idempotency_key)
        return self


class ScheduledWorkRecipeMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    schedule_id: str = Field(alias="scheduleId")
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    mode: ScheduledWorkMode
    status: ScheduledWorkStatus
    timeout_ms: int = Field(alias="timeoutMs", gt=0)
    started_at: int = Field(alias="startedAt", ge=0)
    finished_at: int | None = Field(default=None, alias="finishedAt")
    delivery_policy: Literal["quiet_success", "always_notify", "artifact_only"] = Field(
        alias="deliveryPolicy",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    channel_delivery_attached: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAttached",
    )
    script_path_hash: str | None = Field(default=None, alias="scriptPathHash")
    stdout_preview: str | None = Field(default=None, alias="stdoutPreview")
    stderr_preview: str | None = Field(default=None, alias="stderrPreview")

    @model_validator(mode="after")
    def _validate_scheduled_work(self) -> Self:
        if self.mode == "script":
            if self.timeout_ms > _SCRIPT_TIMEOUT_CAP_MS:
                raise ValueError("script scheduled work timeout exceeds cap")
            if self.script_path_hash is None or not _SHA256_REF_RE.fullmatch(
                self.script_path_hash
            ):
                raise ValueError("script scheduled work requires scriptPathHash")
        elif self.timeout_ms > _AGENT_TIMEOUT_CAP_MS:
            raise ValueError("agent scheduled work timeout exceeds cap")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finishedAt must be after startedAt")
        for value in (
            self.schedule_id,
            self.mission_id,
            self.run_id,
            self.delivery_policy,
            self.script_path_hash,
            self.stdout_preview,
            self.stderr_preview,
        ):
            if value is not None:
                _validate_public_value(value)
        return self


class PipelineContextEvidenceMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    pipeline_id: str = Field(alias="pipelineId")
    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    context_from: Literal["previous_output", "artifact", "last_output_context"] = Field(
        alias="contextFrom",
    )
    used_context_artifact_ids: tuple[str, ...] = Field(alias="usedContextArtifactIds")
    artifact_schema: str = Field(alias="artifactSchema")
    redacted_context_preview: str = Field(alias="redactedContextPreview")
    transient_session_memory_replay: Literal[False] = Field(
        default=False,
        alias="transientSessionMemoryReplay",
    )

    @model_validator(mode="after")
    def _validate_pipeline_handoff(self) -> Self:
        if not self.used_context_artifact_ids:
            raise ValueError("pipeline context handoff requires artifact refs")
        for value in (
            self.pipeline_id,
            self.mission_id,
            self.run_id,
            self.artifact_schema,
            self.redacted_context_preview,
            *(self.used_context_artifact_ids),
        ):
            if not value.strip():
                raise ValueError("pipeline context metadata fields must be non-empty")
            _validate_public_value(value)
        return self


class LongRunningToolPolicyMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    tool_name: str = Field(alias="toolName")
    unit_of_work: LongRunningUnitOfWork = Field(alias="unitOfWork")
    tool_step_uses_long_running_function_tool: bool = Field(
        alias="toolStepUsesLongRunningFunctionTool",
    )
    mission_uses_long_running_function_tool: Literal[False] = Field(
        default=False,
        alias="missionUsesLongRunningFunctionTool",
    )
    background_resume_attached: Literal[False] = Field(
        default=False,
        alias="backgroundResumeAttached",
    )
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    operator_polling_attached: Literal[False] = Field(
        default=False,
        alias="operatorPollingAttached",
    )

    @model_validator(mode="after")
    def _validate_long_running_boundary(self) -> Self:
        if not self.tool_name.strip():
            raise ValueError("long-running tool policy requires toolName")
        if not self.tool_step_uses_long_running_function_tool:
            raise ValueError("long-running tool job metadata must identify tool primitive")
        _validate_public_value(self.tool_name)
        return self


class MissionLifecycleCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: MissionLifecycleCategory
    status: MissionLifecycleStatus
    decision: MissionLifecycleDecision
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    identity: MissionIdentityMetadata
    recipe: MissionRecipeMetadata | None = None
    goal_policy: GoalLoopPolicy | None = Field(default=None, alias="goalPolicy")
    continuation: ContinuationStateSnapshot | None = None
    progress_event: MissionProgressEventMetadata = Field(alias="progressEvent")
    operator_action: MissionOperatorActionMetadata | None = Field(
        default=None,
        alias="operatorAction",
    )
    scheduled_work: ScheduledWorkRecipeMetadata | None = Field(
        default=None,
        alias="scheduledWork",
    )
    pipeline_context: PipelineContextEvidenceMetadata | None = Field(
        default=None,
        alias="pipelineContext",
    )
    long_running_tool_policy: LongRunningToolPolicyMetadata | None = Field(
        default=None,
        alias="longRunningToolPolicy",
    )
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    attachment_flags: MissionLifecycleAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        _validate_public_value(self.model_dump(by_alias=True, mode="json", warnings=False))
        if not self.case_id.strip():
            raise ValueError("mission lifecycle caseId must be non-empty")
        if not self.reason_codes or any(not reason.strip() for reason in self.reason_codes):
            raise ValueError("mission lifecycle cases require reasonCodes")
        if self.progress_event.mission_id != self.identity.mission_id:
            raise ValueError("progress event missionId must match identity")
        if self.progress_event.run_id != self.identity.run_id:
            raise ValueError("progress event runId must match identity")
        self._validate_category_contract()
        return self

    def _validate_category_contract(self) -> None:
        if self.category == "mission_identity" and self.recipe is None:
            raise ValueError("mission identity case requires recipe metadata")
        if self.category == "goal_continuation":
            if self.continuation is None:
                raise ValueError("goal continuation case requires continuation snapshot")
            if "budget_exhausted" in self.reason_codes:
                if self.continuation.continuation_allowed:
                    raise ValueError("budget exhausted continuation must be blocked")
        if self.category == "operator_control":
            if self.operator_action is None:
                raise ValueError("operator control requires operatorAction metadata")
            if self.operator_action.mission_id != self.identity.mission_id:
                raise ValueError("operator action missionId must match identity")
            if self.operator_action.run_id != self.identity.run_id:
                raise ValueError("operator action runId must match identity")
            if self.operator_action.action_type == "cancel":
                if self.continuation is None or not self.continuation.cancellation_requested:
                    raise ValueError("cancel operator action must block continuation")
        if self.category == "scheduled_work":
            if self.scheduled_work is None:
                raise ValueError("scheduled work case requires scheduledWork metadata")
            if self.scheduled_work.mission_id != self.identity.mission_id:
                raise ValueError("scheduledWork missionId must match identity")
            if self.scheduled_work.run_id != self.identity.run_id:
                raise ValueError("scheduledWork runId must match identity")
        if self.category == "pipeline_artifact_handoff":
            if self.pipeline_context is None:
                raise ValueError("pipeline handoff requires pipelineContext metadata")
            if self.pipeline_context.mission_id != self.identity.mission_id:
                raise ValueError("pipelineContext missionId must match identity")
            if self.pipeline_context.run_id != self.identity.run_id:
                raise ValueError("pipelineContext runId must match identity")
        if self.category == "long_running_tool_boundary":
            if self.long_running_tool_policy is None:
                raise ValueError("long-running boundary case requires policy metadata")
            if self.long_running_tool_policy.mission_uses_long_running_function_tool:
                raise ValueError("missions must not be modeled as LongRunningFunctionTool")
        elif self.long_running_tool_policy is not None:
            raise ValueError("only long-running tool boundary cases may include policy metadata")


class MissionLifecycleContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["missionLifecycleFixture.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    ts_parity_inputs: MissionTsParityInputs = Field(
        default_factory=MissionTsParityInputs,
        alias="tsParityInputs",
    )
    activation_defaults: MissionActivationDefaults = Field(
        default_factory=MissionActivationDefaults,
        alias="activationDefaults",
    )
    runtime_authority: MissionRuntimeAuthorityFlags = Field(
        default_factory=MissionRuntimeAuthorityFlags,
        alias="runtimeAuthority",
    )
    attachment_flags: MissionLifecycleAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[MissionLifecycleCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("mission lifecycle caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("mission lifecycle fixture is missing required categories")
        return self


class MissionLifecycleProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: MissionLifecycleAttachmentFlags = Field(alias="attachmentFlags")
    runtime_authority: MissionRuntimeAuthorityFlags = Field(alias="runtimeAuthority")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    ts_parity_inputs: dict[str, str] = Field(alias="tsParityInputs")
    activation_defaults: dict[str, object] = Field(alias="activationDefaults")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_category: dict[str, int] = Field(alias="byCategory")
    by_status: dict[str, int] = Field(alias="byStatus")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_mission_lifecycle_contract_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> MissionLifecycleContractFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return MissionLifecycleContractFixture.model_validate(payload)


def project_mission_lifecycle_contract_fixture(
    fixture: MissionLifecycleContractFixture | Mapping[str, Any],
) -> MissionLifecycleProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    snapshots = {case.case_id: _case_snapshot(case) for case in safe_fixture.cases}
    _validate_public_value(snapshots)
    return MissionLifecycleProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        runtimeAuthority=safe_fixture.runtime_authority,
        noLiveExecution=True,
        tsParityInputs=safe_fixture.ts_parity_inputs.as_env_strings(),
        activationDefaults=safe_fixture.activation_defaults.model_dump(
            by_alias=True,
            mode="python",
            warnings=False,
        ),
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byCategory=dict(Counter(case.category for case in safe_fixture.cases)),
        byStatus=dict(Counter(case.status for case in safe_fixture.cases)),
        caseSnapshots=snapshots,
    )


def _validated_fixture_snapshot(
    fixture: MissionLifecycleContractFixture | Mapping[str, Any],
) -> MissionLifecycleContractFixture:
    if isinstance(fixture, MissionLifecycleContractFixture):
        return MissionLifecycleContractFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return MissionLifecycleContractFixture.model_validate(fixture)


def _case_snapshot(case: MissionLifecycleCase) -> dict[str, object]:
    snapshot = {
        "caseId": case.case_id,
        "category": case.category,
        "status": case.status,
        "decision": case.decision,
        "reasonCodes": case.reason_codes,
        "missionId": case.identity.mission_id,
        "runId": case.identity.run_id,
        "taskId": case.identity.task_id,
        "recipePackRefs": case.recipe.recipe_pack_refs if case.recipe is not None else (),
        "missionUsesLongRunningFunctionTool": (
            case.recipe.mission_lifecycle.mission_uses_long_running_function_tool
            if case.recipe is not None
            else (
                case.long_running_tool_policy.mission_uses_long_running_function_tool
                if case.long_running_tool_policy is not None
                else False
            )
        ),
        "continuationAllowed": (
            case.continuation.continuation_allowed if case.continuation is not None else None
        ),
        "cancellationRequested": (
            case.continuation.cancellation_requested if case.continuation is not None else None
        ),
        "operatorActionType": (
            case.operator_action.action_type if case.operator_action is not None else None
        ),
        "scheduledMode": case.scheduled_work.mode if case.scheduled_work is not None else None,
        "schedulerAttached": (
            case.scheduled_work.scheduler_attached if case.scheduled_work is not None else False
        ),
        "channelDeliveryAttached": (
            case.scheduled_work.channel_delivery_attached
            if case.scheduled_work is not None
            else False
        ),
        "usedContextArtifactIds": (
            case.pipeline_context.used_context_artifact_ids
            if case.pipeline_context is not None
            else ()
        ),
        "transientSessionMemoryReplay": (
            case.pipeline_context.transient_session_memory_replay
            if case.pipeline_context is not None
            else False
        ),
        "toolStepUsesLongRunningFunctionTool": (
            case.long_running_tool_policy.tool_step_uses_long_running_function_tool
            if case.long_running_tool_policy is not None
            else False
        ),
        "adkRunnerInvoked": False,
        "missionStoreWritten": False,
        "schedulerTickAttached": False,
        "backgroundResumeAttached": False,
        "channelDeliveryAttachedRuntime": False,
        "operatorPollingAttached": False,
    }
    _validate_public_value(snapshot)
    return snapshot


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
        raise ValueError("mission lifecycle fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("mission lifecycle fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("mission lifecycle fixture contains unsafe path")
        if _has_forbidden_public_token(value) or _has_secret_shaped_value(value):
            raise ValueError("mission lifecycle fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _reject_unsafe_mapping_key(key)
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("mission lifecycle fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _validate_public_value(value: object) -> None:
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("mission lifecycle public snapshot has unsafe path")
        if _has_forbidden_public_token(value) or _has_secret_shaped_value(value):
            raise ValueError("mission lifecycle public snapshot has unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            _reject_unsafe_mapping_key(key)
            _validate_public_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _validate_public_value(item)
        return
    rendered = json.dumps(value, sort_keys=True)
    if _has_forbidden_public_token(rendered) or _has_secret_shaped_value(rendered):
        raise ValueError("mission lifecycle public snapshot has unsafe data")


def _reject_unsafe_mapping_key(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("mission lifecycle mappings must use string keys")
    normalized = _normalize_key(value)
    if _has_forbidden_public_token(value) or _SECRET_LIKE_KEY_RE.search(
        f"_{normalized}_"
    ):
        raise ValueError("mission lifecycle public snapshot has unsafe data")
    if re.search(
        r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
        r"supabase://|s3://|gs://|postgres(?:ql)?://",
        value,
        re.IGNORECASE,
    ):
        raise ValueError("mission lifecycle public snapshot has unsafe path")


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("mission lifecycle values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("mission lifecycle mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("mission lifecycle values must be JSON-compatible")


def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    chars: list[str] = []
    previous_was_separator = False
    for char in value:
        if char.isalnum():
            chars.append(char.lower())
            previous_was_separator = False
        elif not previous_was_separator:
            chars.append("_")
            previous_was_separator = True
    return "".join(chars).strip("_")


def _has_forbidden_public_token(value: str) -> bool:
    normalized = value.casefold()
    return any(token in normalized for token in _FORBIDDEN_PUBLIC_TOKENS_NORMALIZED)


def _has_secret_shaped_value(value: str) -> bool:
    if _SECRET_SHAPED_VALUE_RE.search(value):
        return True
    redacted = sanitize_tool_preview(value)
    return "[redacted]" in redacted and redacted != value


__all__ = [
    "ContinuationStateSnapshot",
    "LongRunningToolPolicyMetadata",
    "MissionActivationDefaults",
    "MissionIdentityMetadata",
    "MissionLifecycleAttachmentFlags",
    "MissionLifecycleCase",
    "MissionLifecycleContractFixture",
    "MissionLifecycleProjection",
    "MissionOperatorActionMetadata",
    "MissionOperatorControlsActivationDefaults",
    "MissionProgressEventMetadata",
    "MissionRecipeMetadata",
    "MissionRuntimeAuthorityFlags",
    "MissionTsParityInputs",
    "PipelineContextEvidenceMetadata",
    "PersistentGoalLoopActivationDefaults",
    "ScheduledWorkActivationDefaults",
    "ScheduledWorkRecipeMetadata",
    "load_mission_lifecycle_contract_fixture",
    "project_mission_lifecycle_contract_fixture",
]
