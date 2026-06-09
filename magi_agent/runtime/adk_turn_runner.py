from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.runtime.model_tiers import (
    ModelTier,
    ModelTierRegistry,
    ModelUsagePhase,
)
from magi_agent.runtime.request_shape import RequestShapeLedger


AdkTurnStatus: TypeAlias = Literal[
    "disabled",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
]
LOCAL_ADK_TURN_RUNNER_ATTESTATION = "openmagi.local_adk_turn_runner.v1"
_LOCAL_RUNNER_CAPABILITY = object()
_LIVE_RUNNER_FLAGS = (
    ("openmagi_provider_attached", "runner_provider_attached"),
    ("openmagi_model_provider_attached", "runner_provider_attached"),
    ("openmagi_live_provider_attached", "runner_provider_attached"),
    ("openmagi_tool_execution_attached", "runner_tool_execution_attached"),
    ("openmagi_live_toolhost_attached", "runner_tool_execution_attached"),
    ("openmagi_adk_tools_attached", "runner_tool_execution_attached"),
    ("openmagi_traffic_attached", "runner_traffic_attached"),
    ("openmagi_memory_attached", "runner_memory_attached"),
    ("openmagi_browser_attached", "runner_browser_attached"),
    ("openmagi_workspace_attached", "runner_workspace_attached"),
    ("openmagi_channel_attached", "runner_channel_attached"),
    ("openmagi_child_runner_attached", "runner_child_attached"),
    ("openmagi_mission_attached", "runner_mission_attached"),
)
_LOCAL_RUNNER_TYPES: tuple[type[object], ...]

_MODEL_CONFIG = ConfigDict(
    arbitrary_types_allowed=True,
    extra="forbid",
    frozen=True,
    hide_input_in_errors=True,
    populate_by_name=True,
    revalidate_instances="always",
    validate_default=True,
)


class AdkTurnRunnerConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    timeout_seconds: float = Field(default=30.0, gt=0, alias="timeoutSeconds")
    provider: str = "google"
    model: str = "gemini-3.5-flash"
    model_tier: ModelTier = Field(default="cheap", alias="modelTier")
    phase: ModelUsagePhase = "planning"
    model_capabilities: tuple[str, ...] = Field(default=(), alias="modelCapabilities")
    #: Permit a vetted ``LocalAdkLiveChildRunner`` to actually execute. Default
    #: False keeps the runner replay-only; the child boundary sets this from
    #: ``real_child_execution_pack_enabled``.
    live_child_runner_allowed: bool = Field(
        default=False, alias="liveChildRunnerAllowed"
    )

    @field_validator("enabled", "live_child_runner_allowed", mode="before")
    @classmethod
    def _reject_enabled_coercion(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("flag must be a boolean")
        return value

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _reject_timeout_coercion(cls, value: object) -> object:
        if type(value) not in {int, float}:
            raise ValueError("timeoutSeconds must be a positive finite number")
        if not math.isfinite(float(value)) or value <= 0:
            raise ValueError("timeoutSeconds must be a positive finite number")
        return value

    @model_validator(mode="after")
    def _require_known_local_route(self) -> Self:
        resolved = ModelTierRegistry.with_defaults().resolve(
            provider=self.provider,
            model=self.model,
        )
        if "unknown_model_standard_no_elevated_capabilities" in resolved.reason_codes:
            raise ValueError("local ADK turn runner requires a known server-side model route")
        if resolved.tier != self.model_tier:
            raise ValueError("modelTier does not match registry")
        return self


class LocalAdkReplayRunner:
    __slots__ = (
        "calls",
        "cancelled",
        "error",
        "events",
        "openmagi_adk_tools_attached",
        "openmagi_browser_attached",
        "openmagi_channel_attached",
        "openmagi_child_runner_attached",
        "openmagi_live_provider_attached",
        "openmagi_live_toolhost_attached",
        "openmagi_memory_attached",
        "openmagi_mission_attached",
        "openmagi_model_provider_attached",
        "openmagi_provider_attached",
        "openmagi_tool_execution_attached",
        "openmagi_traffic_attached",
        "openmagi_workspace_attached",
        "wait_until_cancelled",
    )

    def __init__(
        self,
        events: tuple[object, ...] = (),
        *,
        error: BaseException | None = None,
        wait_until_cancelled: bool = False,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.cancelled = False
        self.error = error
        self.events = events
        self.wait_until_cancelled = wait_until_cancelled
        for flag, _category in _LIVE_RUNNER_FLAGS:
            setattr(self, flag, False)

    async def run_async(self, **kwargs: object):
        self.calls.append(kwargs)
        try:
            if self.error is not None:
                raise self.error
            if self.wait_until_cancelled:
                while True:
                    await asyncio.sleep(0.01)
            for event in self.events:
                yield event
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class LocalAdkLiveChildRunner:
    """Vetted, in-module wrapper that drives a REAL model-backed ADK runner for a
    child turn.

    Admitted as a known local runner *type* so it passes candidate validation,
    but the turn runner only *executes* it when
    ``AdkTurnRunnerConfig.live_child_runner_allowed`` is set — which the child
    boundary derives from ``real_child_execution_pack_enabled``. The enforcement
    invariants are unchanged: ``AdkTurnResult`` stays ``local_only`` /
    ``user_visible_output=None`` and every authority flag stays False. The
    child's work product reaches the parent only through runtime-issued
    evidence / artifact adoption, never the raw transcript.
    """

    __slots__ = ("_raw",)

    def __init__(self, *, raw_runner: object) -> None:
        if not callable(getattr(raw_runner, "run_async", None)):
            raise TypeError("live child runner requires a runner with run_async")
        self._raw = raw_runner

    async def run_async(self, **kwargs: object):
        async for event in self._raw.run_async(**kwargs):
            yield event


class LocalAdkTurnRunnerBoundary:
    __slots__ = ("_attestation", "_runner")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        raise TypeError("local ADK runner boundary does not support subclassing")

    def __init__(self, *, runner: object, _attestation: object) -> None:
        if _attestation is not _LOCAL_RUNNER_CAPABILITY:
            raise TypeError("local ADK runner boundary must be factory-created")
        _validate_local_runner_candidate(runner)
        self._attestation = _attestation
        self._runner = runner

    @classmethod
    def from_local_test_runner(cls, runner: object) -> Self:
        return LocalAdkTurnRunnerBoundary(
            runner=runner,
            _attestation=_LOCAL_RUNNER_CAPABILITY,
        )

    @classmethod
    def for_live_child_runner(cls, real_adk_runner: object) -> Self:
        """Wrap a real ADK runner as a vetted live child runner boundary.

        Structural admission only — actual execution is still gated by
        ``AdkTurnRunnerConfig.live_child_runner_allowed`` in ``run_turn``.
        """

        return LocalAdkTurnRunnerBoundary(
            runner=LocalAdkLiveChildRunner(raw_runner=real_adk_runner),
            _attestation=_LOCAL_RUNNER_CAPABILITY,
        )

    @property
    def raw_runner(self) -> object:
        if type(self) is not LocalAdkTurnRunnerBoundary:
            raise ValueError("local ADK runner boundary must be exact type")
        if self._attestation is not _LOCAL_RUNNER_CAPABILITY:
            raise ValueError("local ADK runner boundary attestation is invalid")
        _validate_local_runner_candidate(self._runner)
        return self._runner


_LOCAL_RUNNER_TYPES = (LocalAdkReplayRunner, LocalAdkLiveChildRunner)
_REQUEST_SHAPE_PUBLIC_KEYS = frozenset(
    {
        "contextPlanDigest",
        "costEstimateUsd",
        "escalationReason",
        "evidenceRefs",
        "fallbackReason",
        "inputDigest",
        "inputRefs",
        "model",
        "modelCapabilities",
        "modelTier",
        "outputDigest",
        "phase",
        "provider",
        "recipeSnapshotId",
        "recordId",
        "turnId",
        "validatorRefs",
        "validatorStatuses",
    }
)
_OMIT = object()


class AdkTurnRequest(BaseModel):
    model_config = _MODEL_CONFIG

    turn_id: str = Field(alias="turnId")
    user_id: str = Field(alias="userId")
    session_id: str = Field(alias="sessionId")
    invocation_id: str = Field(alias="invocationId")
    new_message: Any = Field(alias="newMessage")
    input_refs: tuple[str, ...] = Field(default=(), alias="inputRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    recipe_snapshot_id: str | None = Field(default=None, alias="recipeSnapshotId")
    context_plan_digest: str | None = Field(default=None, alias="contextPlanDigest")
    harness_state: object = Field(default_factory=dict, alias="harnessState")
    state_delta: dict[str, object] = Field(default_factory=dict, alias="stateDelta")
    run_config: object | None = Field(default=None, alias="runConfig")


class AdkTurnAuthority(BaseModel):
    model_config = _MODEL_CONFIG

    traffic_allowed: Literal[False] = Field(default=False, alias="trafficAllowed")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")
    tool_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolExecutionAllowed",
    )
    memory_write_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWriteAllowed",
    )
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    mission_execution_allowed: Literal[False] = Field(
        default=False,
        alias="missionExecutionAllowed",
    )
    channel_publish_allowed: Literal[False] = Field(
        default=False,
        alias="channelPublishAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)(**_false_payload(type(self)))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _false_payload(cls)

    @field_serializer(
        "traffic_allowed",
        "user_visible_output_allowed",
        "transcript_write_allowed",
        "sse_write_allowed",
        "db_write_allowed",
        "tool_execution_allowed",
        "memory_write_allowed",
        "browser_execution_allowed",
        "workspace_mutation_allowed",
        "child_execution_allowed",
        "mission_execution_allowed",
        "channel_publish_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class AdkTurnProductionWrites(BaseModel):
    model_config = _MODEL_CONFIG

    traffic_attempted: Literal[False] = Field(default=False, alias="trafficAttempted")
    user_visible_output_emitted: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEmitted",
    )
    transcript_written: Literal[False] = Field(
        default=False,
        alias="transcriptWritten",
    )
    sse_published: Literal[False] = Field(default=False, alias="ssePublished")
    db_written: Literal[False] = Field(default=False, alias="dbWritten")
    tool_executed: Literal[False] = Field(default=False, alias="toolExecuted")
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    browser_used: Literal[False] = Field(default=False, alias="browserUsed")
    workspace_mutated: Literal[False] = Field(
        default=False,
        alias="workspaceMutated",
    )
    child_invoked: Literal[False] = Field(default=False, alias="childInvoked")
    mission_mutated: Literal[False] = Field(default=False, alias="missionMutated")
    channel_published: Literal[False] = Field(
        default=False,
        alias="channelPublished",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)(**_false_payload(type(self)))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _false_payload(cls)

    @field_serializer(
        "traffic_attempted",
        "user_visible_output_emitted",
        "transcript_written",
        "sse_published",
        "db_written",
        "tool_executed",
        "memory_written",
        "browser_used",
        "workspace_mutated",
        "child_invoked",
        "mission_mutated",
        "channel_published",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class AdkTurnResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["e2eHarness.adkTurnRunner.v1"] = Field(
        default="e2eHarness.adkTurnRunner.v1",
        alias="schemaVersion",
    )
    status: AdkTurnStatus
    enabled: bool
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    runner_invoked: bool = Field(default=False, alias="runnerInvoked")
    event_count: int = Field(default=0, ge=0, alias="eventCount")
    events: tuple[object, ...] = Field(default=(), exclude=True, repr=False)
    request_shape: dict[str, object] | None = Field(default=None, alias="requestShape")
    error_category: str | None = Field(default=None, alias="errorCategory")
    error_digest: str | None = Field(default=None, alias="errorDigest")
    authority: AdkTurnAuthority = Field(default_factory=AdkTurnAuthority)
    production_writes: AdkTurnProductionWrites = Field(
        default_factory=AdkTurnProductionWrites,
        alias="productionWrites",
    )
    user_visible_output: None = Field(default=None, alias="userVisibleOutput")
    latency_ms: int = Field(default=0, ge=0, alias="latencyMs")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_safe_result_payload(values))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="json")
        if update:
            payload.update(update)
        return type(self)(**_safe_result_payload(payload))

    @model_validator(mode="before")
    @classmethod
    def _force_local_false_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return _safe_result_payload(value)

    @field_serializer("authority")
    def _serialize_authority(self, _value: object) -> dict[str, bool]:
        return AdkTurnAuthority().model_dump(by_alias=True, mode="json")

    @field_serializer("local_only")
    def _serialize_local_only(self, _value: object) -> bool:
        return True

    @field_serializer("production_writes")
    def _serialize_production_writes(self, _value: object) -> dict[str, bool]:
        return AdkTurnProductionWrites().model_dump(by_alias=True, mode="json")

    @field_serializer("request_shape")
    def _serialize_request_shape(
        self,
        value: dict[str, object] | None,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        return _sanitize_request_shape_projection(value)

    @field_serializer("user_visible_output")
    def _serialize_user_visible_output(self, _value: object) -> None:
        return None

    def public_projection(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "enabled": self.enabled,
            "localOnly": True,
            "runnerInvoked": self.runner_invoked,
            "eventCount": self.event_count,
            "authority": self.authority.model_dump(by_alias=True, mode="json"),
            "productionWrites": self.production_writes.model_dump(
                by_alias=True,
                mode="json",
            ),
            "userVisibleOutput": None,
            "latencyMs": self.latency_ms,
        }
        if self.request_shape is not None:
            payload["requestShape"] = _sanitize_request_shape_projection(
                self.request_shape
            )
        if self.error_category is not None:
            payload["errorCategory"] = self.error_category
        if self.error_digest is not None:
            payload["errorDigest"] = self.error_digest
        return payload

    def __str__(self) -> str:
        return json.dumps(self.public_projection(), sort_keys=True)

    def __repr__(self) -> str:
        return str(self)


class AdkTurnRunner:
    def __init__(self, *, request_shape_ledger: RequestShapeLedger | None = None) -> None:
        self._request_shape_ledger = request_shape_ledger or RequestShapeLedger()

    async def run_turn(
        self,
        request: AdkTurnRequest,
        *,
        runner: object | None = None,
        config: AdkTurnRunnerConfig | None = None,
    ) -> AdkTurnResult:
        started = time.monotonic()
        if type(request) is not AdkTurnRequest:
            return _result(
                status="failed",
                enabled=False,
                started=started,
                runner_invoked=False,
                error_category="request_boundary_rejected",
                error_digest=_digest({"category": "request_boundary_rejected"}),
            )
        try:
            _validate_request_boundary(request)
        except ValueError as exc:
            return _result(
                status="failed",
                enabled=False,
                started=started,
                runner_invoked=False,
                error_category="request_boundary_rejected",
                error_digest=_error_digest("request_boundary_rejected", exc),
            )
        if config is not None and type(config) is not AdkTurnRunnerConfig:
            return _result(
                status="failed",
                enabled=False,
                started=started,
                runner_invoked=False,
                error_category="config_boundary_rejected",
                error_digest=_digest({"category": "config_boundary_rejected"}),
            )
        try:
            active_config = _validate_config_boundary(config)
        except ValueError as exc:
            return _result(
                status="failed",
                enabled=False,
                started=started,
                runner_invoked=False,
                error_category="config_boundary_rejected",
                error_digest=_error_digest("config_boundary_rejected", exc),
            )
        if not active_config.enabled:
            return _result(
                status="disabled",
                enabled=False,
                started=started,
            )

        try:
            _validate_known_local_model_route(active_config)
        except Exception as exc:
            return _result(
                status="failed",
                enabled=True,
                started=started,
                runner_invoked=False,
                error_category="model_route_rejected",
                error_digest=_error_digest("model_route_rejected", exc),
            )

        if runner is None:
            return _result(
                status="failed",
                enabled=True,
                started=started,
                runner_invoked=False,
                error_category="runner_missing",
                error_digest=_digest({"category": "runner_missing"}),
            )

        attestation = _local_runner_attestation(runner)
        if attestation is not None:
            return _result(
                status="failed",
                enabled=True,
                started=started,
                runner_invoked=False,
                error_category=attestation,
                error_digest=_digest({"category": attestation}),
            )

        # A vetted live child runner may be wrapped structurally, but it only
        # executes when the pack flag is set (the boundary derives this from
        # ``real_child_execution_pack_enabled``). Replay runners are unaffected.
        if (
            type(runner.raw_runner) is LocalAdkLiveChildRunner
            and not active_config.live_child_runner_allowed
        ):
            return _result(
                status="failed",
                enabled=True,
                started=started,
                runner_invoked=False,
                error_category="live_child_runner_not_permitted",
                error_digest=_digest({"category": "live_child_runner_not_permitted"}),
            )

        from magi_agent.adk_bridge.runner_adapter import (  # noqa: PLC0415
            OpenMagiRunnerAdapter,
            RunnerTurnInput,
        )

        try:
            turn_input = RunnerTurnInput(
                userId=request.user_id,
                sessionId=request.session_id,
                turnId=request.turn_id,
                invocationId=request.invocation_id,
                newMessage=request.new_message,
                harnessState=request.harness_state,
                stateDelta=request.state_delta,
                runConfig=request.run_config,
            )
        except Exception as exc:
            return _result(
                status="failed",
                enabled=True,
                started=started,
                runner_invoked=False,
                error_category="runner_input_rejected",
                error_digest=_error_digest("runner_input_rejected", exc),
            )

        try:
            request_shape = _sanitize_request_shape_projection(
                self._record_request_shape(
                    request,
                    config=active_config,
                    trusted_new_message=turn_input.new_message,
                ).public_projection()
            )
        except Exception as exc:
            return _result(
                status="failed",
                enabled=True,
                started=started,
                runner_invoked=False,
                error_category="request_shape_rejected",
                error_digest=_error_digest("request_shape_rejected", exc),
            )

        adapter = OpenMagiRunnerAdapter(runner=runner.raw_runner)
        try:
            events = await asyncio.wait_for(
                adapter.collect_events(turn_input),
                timeout=active_config.timeout_seconds,
            )
        except TimeoutError as exc:
            return _result(
                status="timed_out",
                enabled=True,
                started=started,
                runner_invoked=True,
                request_shape=request_shape,
                error_category="timeout",
                error_digest=_error_digest("timeout", exc),
            )
        except asyncio.CancelledError as exc:
            return _result(
                status="cancelled",
                enabled=True,
                started=started,
                runner_invoked=True,
                request_shape=request_shape,
                error_category="cancelled",
                error_digest=_error_digest("cancelled", exc),
            )
        except Exception as exc:
            return _result(
                status="failed",
                enabled=True,
                started=started,
                runner_invoked=True,
                request_shape=request_shape,
                error_category="runner_exception",
                error_digest=_error_digest("runner_exception", exc),
            )

        return _result(
            status="succeeded",
            enabled=True,
            started=started,
            runner_invoked=True,
            events=tuple(events),
            request_shape=request_shape,
        )

    def _record_request_shape(
        self,
        request: AdkTurnRequest,
        *,
        config: AdkTurnRunnerConfig,
        trusted_new_message: object,
    ):
        return self._request_shape_ledger.record_model_phase(
            turnId=request.turn_id,
            phase=config.phase,
            provider=config.provider,
            model=config.model,
            modelTier=config.model_tier,
            modelCapabilities=config.model_capabilities,
            recipeSnapshotId=request.recipe_snapshot_id,
            inputRefs=request.input_refs,
            evidenceRefs=request.evidence_refs,
            contextPlanDigest=request.context_plan_digest,
            rawInput={
                "turnId": request.turn_id,
                "userId": request.user_id,
                "sessionId": request.session_id,
                "invocationId": request.invocation_id,
                "newMessage": trusted_new_message.model_dump(
                    by_alias=True,
                    exclude_none=True,
                    mode="json",
                ),
                "inputRefs": request.input_refs,
                "evidenceRefs": request.evidence_refs,
                "recipeSnapshotId": request.recipe_snapshot_id,
                "contextPlanDigest": request.context_plan_digest,
            },
        )


def _result(
    *,
    status: AdkTurnStatus,
    enabled: bool,
    started: float,
    runner_invoked: bool = False,
    events: tuple[object, ...] = (),
    request_shape: dict[str, object] | None = None,
    error_category: str | None = None,
    error_digest: str | None = None,
) -> AdkTurnResult:
    return AdkTurnResult(
        status=status,
        enabled=enabled,
        runnerInvoked=runner_invoked,
        eventCount=len(events),
        events=events,
        requestShape=request_shape,
        errorCategory=error_category,
        errorDigest=error_digest,
        latencyMs=_elapsed_ms(started),
    )


def _error_digest(category: str, exc: BaseException) -> str:
    return _digest(
        {
            "category": category,
            "exceptionType": type(exc).__name__,
        }
    )


def _digest(value: object) -> str:
    material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _false_payload(model_type: type[BaseModel]) -> dict[str, bool]:
    return {
        field.alias or name: False
        for name, field in model_type.model_fields.items()
    }


def _safe_result_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(values)
    data.pop("local_only", None)
    data.pop("localOnly", None)
    data.pop("user_visible_output", None)
    data.pop("userVisibleOutput", None)
    data["localOnly"] = True
    data["authority"] = AdkTurnAuthority().model_dump(by_alias=True)
    data["productionWrites"] = AdkTurnProductionWrites().model_dump(by_alias=True)
    data["userVisibleOutput"] = None
    return data


def _validate_request_boundary(request: AdkTurnRequest) -> None:
    data = request.__dict__
    for field in ("turn_id", "user_id", "session_id", "invocation_id"):
        if type(data.get(field)) is not str:
            raise ValueError(f"{field} must be a string")
    for field in ("input_refs", "evidence_refs"):
        value = data.get(field)
        if type(value) is not tuple or any(type(item) is not str for item in value):
            raise ValueError(f"{field} must be a tuple of strings")
    for field in ("recipe_snapshot_id", "context_plan_digest"):
        value = data.get(field)
        if value is not None and type(value) is not str:
            raise ValueError(f"{field} must be a string or null")
    state_delta = data.get("state_delta")
    if type(state_delta) is not dict or not _json_safe_mapping(state_delta):
        raise ValueError("state_delta must be a JSON-safe mapping")
    for field in ("harness_state", "run_config"):
        if not _json_safe_value(data.get(field)):
            raise ValueError(f"{field} must be JSON-safe")


def _validate_config_boundary(
    config: AdkTurnRunnerConfig | None,
) -> AdkTurnRunnerConfig:
    if config is None:
        return AdkTurnRunnerConfig()
    data = config.__dict__
    if type(data.get("enabled")) is not bool:
        raise ValueError("enabled must be a boolean")
    timeout = data.get("timeout_seconds")
    if (
        type(timeout) not in {int, float}
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("timeout_seconds must be a positive number")
    for field in ("provider", "model", "model_tier", "phase"):
        if type(data.get(field)) is not str:
            raise ValueError(f"{field} must be a string")
    capabilities = data.get("model_capabilities")
    if (
        type(capabilities) is not tuple
        or any(type(item) is not str for item in capabilities)
    ):
        raise ValueError("model_capabilities must be a tuple of strings")
    return config


def _json_safe_mapping(value: dict[object, object]) -> bool:
    return all(type(key) is str and _json_safe_value(item) for key, item in value.items())


def _json_safe_value(value: object) -> bool:
    if value is None or type(value) in {str, bool, int}:
        return True
    if type(value) is float:
        return True
    if type(value) is list or type(value) is tuple:
        return all(_json_safe_value(item) for item in value)
    if type(value) is dict:
        return _json_safe_mapping(value)
    return False


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _sanitize_request_shape_projection(value: Mapping[str, object]) -> dict[str, object]:
    clean: dict[str, object] = {}
    for key in sorted(_REQUEST_SHAPE_PUBLIC_KEYS):
        if key not in value:
            continue
        item = value[key]
        sanitized = _sanitize_request_shape_value(key, item)
        if sanitized is not _OMIT:
            clean[key] = sanitized
    return clean


def _sanitize_request_shape_value(key: str, value: object) -> object:
    if value is None:
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value if key == "costEstimateUsd" else _OMIT
    if isinstance(value, str):
        return value if _request_shape_string_is_public(value) else _OMIT
    if isinstance(value, list | tuple):
        clean_items = [
            item
            for item in value
            if isinstance(item, str) and _request_shape_string_is_public(item)
        ]
        return clean_items
    if isinstance(value, dict):
        if key != "validatorStatuses":
            return _OMIT
        clean_statuses = {
            nested_key: nested_value
            for nested_key, nested_value in value.items()
            if isinstance(nested_key, str)
            and _request_shape_string_is_public(nested_key)
            and nested_value in {"passed", "failed", "repair_required", "blocked", "skipped"}
        }
        return clean_statuses
    return _OMIT


def _request_shape_string_is_public(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    normalized = text.casefold()
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    if (
        normalized.startswith(("/", "~"))
        or "/home/" in normalized
        or "/private/" in normalized
        or "/.ssh" in normalized
        or "/.aws" in normalized
        or "/.config" in normalized
        or "/.kube" in normalized
    ):
        return False
    forbidden = (
        "auth",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "apikey",
        "password",
        "privatekey",
        "rawprompt",
        "rawtoolargs",
        "rawtoolresult",
        "session",
        "secret",
        "token",
    )
    return "://" not in normalized and not any(item in compact for item in forbidden)


def _validate_known_local_model_route(config: AdkTurnRunnerConfig) -> None:
    resolved = ModelTierRegistry.with_defaults().resolve(
        provider=config.provider,
        model=config.model,
    )
    if "unknown_model_standard_no_elevated_capabilities" in resolved.reason_codes:
        raise ValueError("local ADK turn runner requires a known server-side model route")
    if resolved.tier != config.model_tier:
        raise ValueError("modelTier does not match registry")


def _validate_local_runner_candidate(runner: object) -> None:
    if type(runner) not in _LOCAL_RUNNER_TYPES:
        raise ValueError("local ADK turn runner requires a trusted local runner type")
    runner_module = runner.__class__.__module__
    if runner_module.startswith(("google.adk", "google.genai")):
        raise ValueError("local ADK turn runner cannot wrap a live ADK/provider runner")
    if runner_module == "magi_agent.adk_bridge.local_runner":
        raise ValueError("local ADK turn runner cannot wrap production local_runner")
    if not callable(getattr(runner, "run_async", None)):
        raise ValueError("local ADK turn runner requires run_async")
    for flag, category in _LIVE_RUNNER_FLAGS:
        if getattr(runner, flag, False) is not False:
            raise ValueError(category)


def _local_runner_attestation(runner: object) -> str | None:
    if type(runner) is not LocalAdkTurnRunnerBoundary:
        return "runner_local_attestation_missing"
    if getattr(runner, "_attestation", None) is not _LOCAL_RUNNER_CAPABILITY:
        return "runner_local_attestation_missing"
    try:
        _validate_local_runner_candidate(runner.raw_runner)
    except ValueError as exc:
        message = str(exc)
        if message.startswith("runner_"):
            return message
        return "runner_local_attestation_missing"
    return None


__all__ = [
    "AdkTurnAuthority",
    "AdkTurnProductionWrites",
    "AdkTurnRequest",
    "AdkTurnResult",
    "AdkTurnRunner",
    "AdkTurnRunnerConfig",
    "AdkTurnStatus",
    "LOCAL_ADK_TURN_RUNNER_ATTESTATION",
    "LocalAdkLiveChildRunner",
    "LocalAdkReplayRunner",
    "LocalAdkTurnRunnerBoundary",
]
