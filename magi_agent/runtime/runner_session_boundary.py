from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.runtime.active_turn_registry import (
    ActiveTurnRegistry,  # noqa: F401  (compat re-export; see PR2)
)
from magi_agent.runtime.context_packet import ContextContinuityAuthorityFlags
from magi_agent.runtime.error_taxonomy import (
    DecisionAction,
    ErrorCategory,
    classify_adk_runtime_failure,
    decide_retry_fallback,
)
from magi_agent.runtime.projection_write_boundary import (
    ProjectionWriteBoundaryResult,
    ProjectionWriteIntent,
    evaluate_projection_write_intent,
)
from magi_agent.runtime.turn_controller import TurnControllerInput


RunnerSessionBoundaryStatus: TypeAlias = Literal[
    "skipped",
    "completed",
    "error",
    "timeout",
    "cancelled",
    "concurrent_denied",
]
RunnerSessionBoundaryReason: TypeAlias = Literal[
    "disabled",
    "runner_completed",
    "runner_error",
    "runner_timeout",
    "cancelled_before_run",
    "cancelled_during_run",
    "active_session_turn",
]
RunnerContextContinuityStatus: TypeAlias = Literal["skipped", "imported"]
RunnerContextContinuityReason: TypeAlias = Literal[
    "disabled",
    "missing_transcript_store",
    "missing_session_service",
    "committed_history_imported",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_CANCELLATION_DRAIN_GRACE_SECONDS = 0.05


class RunnerContextContinuityConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    max_imported_events: int = Field(default=128, ge=1, le=512, alias="maxImportedEvents")
    model_visible_projection_enabled: bool = Field(
        default=False,
        alias="modelVisibleProjectionEnabled",
    )
    max_rendered_chars: int = Field(
        default=24_000,
        ge=1_000,
        le=96_000,
        alias="maxRenderedChars",
    )


class RunnerSessionBoundaryConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    timeout_ms: int = Field(default=30_000, ge=1, alias="timeoutMs")
    max_event_count: int = Field(default=256, ge=1, alias="maxEventCount")
    context_continuity: RunnerContextContinuityConfig = Field(
        default_factory=RunnerContextContinuityConfig,
        alias="contextContinuity",
    )


class RunnerSessionAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    control_event_write_allowed: Literal[False] = Field(
        default=False,
        alias="controlEventWriteAllowed",
    )
    control_request_write_allowed: Literal[False] = Field(
        default=False,
        alias="controlRequestWriteAllowed",
    )
    production_receipt_allowed: Literal[False] = Field(
        default=False,
        alias="productionReceiptAllowed",
    )
    durable_write_allowed: Literal[False] = Field(
        default=False,
        alias="durableWriteAllowed",
    )
    tool_host_active: Literal[False] = Field(default=False, alias="toolHostActive")
    memory_provider_active: Literal[False] = Field(
        default=False,
        alias="memoryProviderActive",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    mission_runtime_allowed: Literal[False] = Field(
        default=False,
        alias="missionRuntimeAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**_false_authority_payload(cls))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        return type(self)(**_false_authority_payload(type(self)))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        allowed_keys = set(cls.model_fields)
        allowed_keys.update(
            field.alias
            for field in cls.model_fields.values()
            if field.alias is not None
        )
        unsupported = set(value) - allowed_keys
        if unsupported:
            raise ValueError("runner session authority flags contain unsupported fields")
        return _false_authority_payload(cls)

    @field_serializer(
        "user_visible_output_allowed",
        "transcript_write_allowed",
        "sse_write_allowed",
        "control_event_write_allowed",
        "control_request_write_allowed",
        "production_receipt_allowed",
        "durable_write_allowed",
        "tool_host_active",
        "memory_provider_active",
        "workspace_mutation_allowed",
        "child_execution_allowed",
        "mission_runtime_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class RunnerContextContinuityMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    status: RunnerContextContinuityStatus = "skipped"
    reason: RunnerContextContinuityReason = "disabled"
    enabled: bool = False
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    response_authority: Literal["none"] = Field(default="none", alias="responseAuthority")
    imported_event_count: int = Field(default=0, ge=0, alias="importedEventCount")
    rejected_entry_count: int = Field(default=0, ge=0, alias="rejectedEntryCount")
    compaction_applied: bool = Field(default=False, alias="compactionApplied")
    dropped_pre_boundary_count: int = Field(
        default=0,
        ge=0,
        alias="droppedPreBoundaryCount",
    )
    budget_truncated: bool = Field(default=False, alias="budgetTruncated")
    projection_digest: str | None = Field(default=None, alias="projectionDigest")
    model_visible_digest: str | None = Field(default=None, alias="modelVisibleDigest")
    source_transcript_head_digest: str | None = Field(
        default=None,
        alias="sourceTranscriptHeadDigest",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    authority_flags: ContextContinuityAuthorityFlags = Field(
        default_factory=ContextContinuityAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_local_no_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["localOnly"] = True
        data["diagnosticOnly"] = True
        data["responseAuthority"] = "none"
        data["authorityFlags"] = ContextContinuityAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
        )
        return data


class RunnerTerminalMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    status: RunnerSessionBoundaryStatus
    reason: RunnerSessionBoundaryReason
    error_category: ErrorCategory | None = Field(default=None, alias="errorCategory")
    ts_error_code: str | None = Field(default=None, alias="tsErrorCode")
    fallback_action: DecisionAction | None = Field(default=None, alias="fallbackAction")
    retryable: bool | None = None
    fail_closed: bool | None = Field(default=None, alias="failClosed")
    restore_to_typescript: bool | None = Field(default=None, alias="restoreToTypeScript")


class RunnerSessionBoundaryResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["priorityA.runnerSessionBoundary.v1"] = Field(
        default="priorityA.runnerSessionBoundary.v1",
        alias="schemaVersion",
    )
    status: RunnerSessionBoundaryStatus
    reason: RunnerSessionBoundaryReason
    enabled: bool
    session_key: str = Field(alias="sessionKey")
    turn_id: str = Field(alias="turnId")
    invocation_id: str = Field(alias="invocationId")
    response_authority: Literal["none"] = Field(
        default="none",
        alias="responseAuthority",
    )
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    runner_invoked: bool = Field(default=False, alias="runnerInvoked")
    runner_completed: bool = Field(default=False, alias="runnerCompleted")
    model_call_via_adk_runner_attempted: bool = Field(
        default=False,
        alias="modelCallViaAdkRunnerAttempted",
    )
    event_count: int = Field(default=0, ge=0, alias="eventCount")
    local_public_events: list[dict[str, object]] = Field(
        default_factory=list,
        alias="localPublicEvents",
    )
    local_transcript_entry_count: int = Field(
        default=0,
        ge=0,
        alias="localTranscriptEntryCount",
    )
    user_visible_output: None = Field(default=None, alias="userVisibleOutput")
    latency_ms: int = Field(default=0, ge=0, alias="latencyMs")
    timeout_ms: int = Field(alias="timeoutMs")
    terminal_metadata: RunnerTerminalMetadata = Field(alias="terminalMetadata")
    context_continuity: RunnerContextContinuityMetadata = Field(
        default_factory=RunnerContextContinuityMetadata,
        alias="contextContinuity",
    )
    projection_write_denials: list[ProjectionWriteBoundaryResult] = Field(
        alias="projectionWriteDenials",
    )
    authority_flags: RunnerSessionAuthorityFlags = Field(
        default_factory=RunnerSessionAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_non_authoritative(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["responseAuthority"] = "none"
        data["localOnly"] = True
        data["diagnosticOnly"] = True
        data["userVisibleOutput"] = None
        return data

    @field_serializer("authority_flags")
    def _serialize_authority_flags(self, _value: object) -> dict[str, bool]:
        return RunnerSessionAuthorityFlags().model_dump(by_alias=True, mode="json")


class RunnerCancellationToken:
    def __init__(self) -> None:
        self._cancelled = False
        self._waiters: set[asyncio.Future[None]] = set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        for waiter in tuple(self._waiters):
            if not waiter.done():
                waiter.set_result(None)

    async def wait(self) -> None:
        if self._cancelled:
            return
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        self._waiters.add(waiter)
        try:
            await waiter
        finally:
            self._waiters.discard(waiter)


@dataclass(frozen=True)
class _CollectedRunnerEvents:
    local_public_events: list[dict[str, object]]
    local_transcript_entry_count: int
    event_count: int
    context_continuity: RunnerContextContinuityMetadata


@dataclass(frozen=True)
class _AcquiredTurnOutcome:
    result: RunnerSessionBoundaryResult
    release_session_on_return: bool = True


class RunnerSessionBoundary:
    def __init__(
        self,
        *,
        active_turn_registry: ActiveTurnRegistry | None = None,
    ) -> None:
        self._active_turn_registry = active_turn_registry or ActiveTurnRegistry()

    async def run_turn(
        self,
        turn_input: TurnControllerInput,
        *,
        runner: object,
        config: RunnerSessionBoundaryConfig | None = None,
        cancellation_token: RunnerCancellationToken | None = None,
        transcript_store: object | None = None,
    ) -> RunnerSessionBoundaryResult:
        active_config = config or RunnerSessionBoundaryConfig()
        started = time.monotonic()
        session_key = turn_input.session_id
        turn_id = turn_input.turn_id

        if not active_config.enabled:
            return _result(
                turn_input,
                config=active_config,
                status="skipped",
                reason="disabled",
                started=started,
            )

        token = cancellation_token or RunnerCancellationToken()
        if token.cancelled:
            return _result(
                turn_input,
                config=active_config,
                status="cancelled",
                reason="cancelled_before_run",
                started=started,
                classification_code="user_interrupt",
            )

        if not self._active_turn_registry.try_acquire(
            session_key=session_key,
            turn_id=turn_id,
        ):
            return _result(
                turn_input,
                config=active_config,
                status="concurrent_denied",
                reason="active_session_turn",
                started=started,
                classification_code="user_interrupt",
            )

        release_session_on_return = True
        try:
            if token.cancelled:
                return _result(
                    turn_input,
                    config=active_config,
                    status="cancelled",
                    reason="cancelled_before_run",
                    started=started,
                    classification_code="user_interrupt",
                )
            outcome = await self._run_acquired_turn(
                turn_input,
                runner=runner,
                config=active_config,
                cancellation_token=token,
                started=started,
                transcript_store=transcript_store,
            )
            release_session_on_return = outcome.release_session_on_return
            return outcome.result
        finally:
            if release_session_on_return:
                self._active_turn_registry.release(
                    session_key=session_key,
                    turn_id=turn_id,
                )

    async def _run_acquired_turn(
        self,
        turn_input: TurnControllerInput,
        *,
        runner: object,
        config: RunnerSessionBoundaryConfig,
        cancellation_token: RunnerCancellationToken,
        started: float,
        transcript_store: object | None,
    ) -> _AcquiredTurnOutcome:
        runner_task = asyncio.create_task(
            _collect_runner_events(
                turn_input,
                runner=runner,
                max_event_count=config.max_event_count,
                context_config=config.context_continuity,
                transcript_store=transcript_store,
            )
        )
        cancel_task = asyncio.create_task(cancellation_token.wait())
        timeout_seconds = config.timeout_ms / 1000

        done, _pending = await asyncio.wait(
            {runner_task, cancel_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if cancel_task in done or cancellation_token.cancelled:
            runner_stopped = await _cancel_and_drain_task(runner_task)
            await _cancel_and_drain_task(cancel_task)
            if not runner_stopped:
                self._active_turn_registry.release_when_done(
                    session_key=turn_input.session_id,
                    turn_id=turn_input.turn_id,
                    task=runner_task,
                )
            return _AcquiredTurnOutcome(
                result=_result(
                    turn_input,
                    config=config,
                    status="cancelled",
                    reason="cancelled_during_run",
                    started=started,
                    runner_invoked=True,
                    runner_completed=False,
                    model_attempted=True,
                    classification_code="user_interrupt",
                    local_public_events=_terminal_public_events(
                        turn_id=turn_input.turn_id,
                        status="aborted",
                        reason="user_interrupt",
                    ),
                ),
                release_session_on_return=runner_stopped,
            )

        if runner_task in done:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
            try:
                collected = runner_task.result()
            except Exception as exc:
                return _AcquiredTurnOutcome(
                    _result(
                        turn_input,
                        config=config,
                        status="error",
                        reason="runner_error",
                        started=started,
                        runner_invoked=True,
                        runner_completed=False,
                        model_attempted=True,
                        classification_code="runner_exception",
                        exception=exc,
                        local_public_events=_terminal_public_events(
                            turn_id=turn_input.turn_id,
                            status="aborted",
                            reason="runner_exception",
                        ),
                    )
                )
            return _AcquiredTurnOutcome(
                _result(
                    turn_input,
                    config=config,
                    status="completed",
                    reason="runner_completed",
                    started=started,
                    runner_invoked=True,
                    runner_completed=True,
                    model_attempted=True,
                    event_count=collected.event_count,
                    local_public_events=collected.local_public_events,
                    local_transcript_entry_count=collected.local_transcript_entry_count,
                    context_continuity=collected.context_continuity,
                )
            )

        runner_stopped = await _cancel_and_drain_task(runner_task)
        await _cancel_and_drain_task(cancel_task)
        if not runner_stopped:
            self._active_turn_registry.release_when_done(
                session_key=turn_input.session_id,
                turn_id=turn_input.turn_id,
                task=runner_task,
            )
        return _AcquiredTurnOutcome(
            result=_result(
                turn_input,
                config=config,
                status="timeout",
                reason="runner_timeout",
                started=started,
                runner_invoked=True,
                runner_completed=False,
                model_attempted=True,
                classification_code="timeout",
                exception=TimeoutError("ADK Runner turn timed out"),
                local_public_events=_terminal_public_events(
                    turn_id=turn_input.turn_id,
                    status="aborted",
                    reason="timeout",
                ),
            ),
            release_session_on_return=runner_stopped,
        )


async def _cancel_and_drain_task(
    task: asyncio.Task[Any],
    *,
    grace_seconds: float = _CANCELLATION_DRAIN_GRACE_SECONDS,
) -> bool:
    if task.done():
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        return True

    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=grace_seconds)
    if task in done:
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        return True

    task.add_done_callback(_consume_task_result)
    return False


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.result()


async def _collect_runner_events(
    turn_input: TurnControllerInput,
    *,
    runner: object,
    max_event_count: int,
    context_config: RunnerContextContinuityConfig,
    transcript_store: object | None,
) -> _CollectedRunnerEvents:
    from google.genai import types

    from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
    from magi_agent.adk_bridge.runner_adapter import (
        OpenMagiRunnerAdapter,
        RunnerTurnInput,
    )

    adapter = OpenMagiRunnerAdapter(runner=runner)
    bridge = OpenMagiEventBridge(live_compatible=True)
    context_continuity, context_projection = await _prepare_context_continuity(
        turn_input,
        runner=runner,
        config=context_config,
        transcript_store=transcript_store,
    )
    message_text = _context_aware_message_text(
        turn_input.message_text,
        context_projection=context_projection,
    )
    runner_input = RunnerTurnInput(
        userId=turn_input.user_id,
        sessionId=turn_input.session_id,
        turnId=turn_input.turn_id,
        invocationId=turn_input.turn_id,
        newMessage=types.Content(
            role="user",
            parts=[types.Part(text=message_text)],
        ),
        harnessState=turn_input.harness_state,
    )

    local_public_events: list[dict[str, object]] = []
    local_transcript_entry_count = 0
    event_count = 0
    async for event in adapter.run_turn(runner_input):
        event_count += 1
        projection = bridge.project_adk_event(event, turn_id=turn_input.turn_id)
        local_public_events.extend(projection.agent_events)
        local_transcript_entry_count += len(projection.transcript_entries)
        if event_count >= max_event_count:
            break
    return _CollectedRunnerEvents(
        local_public_events=local_public_events,
        local_transcript_entry_count=local_transcript_entry_count,
        event_count=event_count,
        context_continuity=context_continuity,
    )


async def _prepare_context_continuity(
    turn_input: TurnControllerInput,
    *,
    runner: object,
    config: RunnerContextContinuityConfig,
    transcript_store: object | None,
) -> tuple[RunnerContextContinuityMetadata, str | None]:
    if not config.enabled:
        return RunnerContextContinuityMetadata(), None
    if transcript_store is None:
        return (
            RunnerContextContinuityMetadata(
                status="skipped",
                reason="missing_transcript_store",
                enabled=True,
                reasonCodes=("missing_transcript_store",),
            ),
            None,
        )

    session_service = getattr(runner, "session_service", None)
    if session_service is None:
        return (
            RunnerContextContinuityMetadata(
                status="skipped",
                reason="missing_session_service",
                enabled=True,
                reasonCodes=("missing_session_service",),
            ),
            None,
        )

    app_name = str(getattr(runner, "app_name", "") or "openmagi")
    session = await _get_or_create_adk_session(
        session_service,
        app_name=app_name,
        turn_input=turn_input,
    )

    from magi_agent.runtime.context_packet import (
        ContextContinuityConfig,
        build_context_packet_from_session_continuity,
        render_context_packet_for_model,
    )
    from magi_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    continuity_result = await SessionContinuityBoundary().import_committed_transcript(
        session_service,
        session,
        transcript_store=transcript_store,
        config=SessionContinuityConfig(
            enabled=True,
            maxImportedEvents=config.max_imported_events,
        ),
    )
    packet = build_context_packet_from_session_continuity(
        session,
        transcript_store=transcript_store,
        continuity_result=continuity_result,
        config=ContextContinuityConfig(
            enabled=True,
            maxImportedEvents=config.max_imported_events,
            maxRenderedChars=config.max_rendered_chars,
        ),
    )
    context_projection = (
        render_context_packet_for_model(
            packet,
            max_chars=config.max_rendered_chars,
        )
        if config.model_visible_projection_enabled
        else None
    )
    reason_codes = tuple(
        dict.fromkeys(
            [
                *continuity_result.diagnostics.reason_codes,
                *packet.diagnostics.reason_codes,
            ]
        )
    )
    return (
        RunnerContextContinuityMetadata(
            status="imported",
            reason="committed_history_imported",
            enabled=True,
            importedEventCount=continuity_result.imported_event_count,
            rejectedEntryCount=continuity_result.rejected_entry_count,
            compactionApplied=(
                continuity_result.compaction_applied
                or packet.diagnostics.compaction_applied
            ),
            droppedPreBoundaryCount=continuity_result.dropped_pre_boundary_count,
            budgetTruncated=continuity_result.budget_truncated,
            projectionDigest=packet.projection_digest,
            modelVisibleDigest=(
                packet.model_visible_digest
                if config.model_visible_projection_enabled
                else None
            ),
            sourceTranscriptHeadDigest=packet.source_transcript_head_digest,
            reasonCodes=reason_codes,
        ),
        context_projection,
    )


async def _get_or_create_adk_session(
    session_service: object,
    *,
    app_name: str,
    turn_input: TurnControllerInput,
) -> object:
    session = await session_service.get_session(
        app_name=app_name,
        user_id=turn_input.user_id,
        session_id=turn_input.session_id,
    )
    if session is not None:
        return session
    return await session_service.create_session(
        app_name=app_name,
        user_id=turn_input.user_id,
        session_id=turn_input.session_id,
    )


def _context_aware_message_text(
    message_text: str,
    *,
    context_projection: str | None,
) -> str:
    if not context_projection:
        return message_text
    return f"{context_projection}\n\n{message_text}"


def _result(
    turn_input: TurnControllerInput,
    *,
    config: RunnerSessionBoundaryConfig,
    status: RunnerSessionBoundaryStatus,
    reason: RunnerSessionBoundaryReason,
    started: float,
    runner_invoked: bool = False,
    runner_completed: bool = False,
    model_attempted: bool = False,
    event_count: int = 0,
    local_public_events: list[dict[str, object]] | None = None,
    local_transcript_entry_count: int = 0,
    context_continuity: RunnerContextContinuityMetadata | None = None,
    classification_code: object | None = None,
    exception: BaseException | None = None,
) -> RunnerSessionBoundaryResult:
    terminal_metadata = _terminal_metadata(
        status=status,
        reason=reason,
        classification_code=classification_code,
        exception=exception,
    )
    return RunnerSessionBoundaryResult(
        status=status,
        reason=reason,
        enabled=config.enabled,
        sessionKey=turn_input.session_id,
        turnId=turn_input.turn_id,
        invocationId=turn_input.turn_id,
        runnerInvoked=runner_invoked,
        runnerCompleted=runner_completed,
        modelCallViaAdkRunnerAttempted=model_attempted,
        eventCount=event_count,
        localPublicEvents=local_public_events or [],
        localTranscriptEntryCount=local_transcript_entry_count,
        latencyMs=_elapsed_ms(started),
        timeoutMs=config.timeout_ms,
        terminalMetadata=terminal_metadata.model_dump(
            by_alias=True,
            mode="python",
            warnings=False,
        ),
        contextContinuity=(
            context_continuity.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            )
            if context_continuity is not None
            else RunnerContextContinuityMetadata().model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            )
        ),
        projectionWriteDenials=[
            evaluate_projection_write_intent(intent)
            for intent in _projection_write_intents(
                session_key=turn_input.session_id,
                turn_id=turn_input.turn_id,
                status=status,
            )
        ],
    )


def _terminal_metadata(
    *,
    status: RunnerSessionBoundaryStatus,
    reason: RunnerSessionBoundaryReason,
    classification_code: object | None,
    exception: BaseException | None,
) -> RunnerTerminalMetadata:
    if classification_code is None and exception is None:
        return RunnerTerminalMetadata(status=status, reason=reason)

    classification = classify_adk_runtime_failure(
        code=classification_code,
        message=str(exception) if exception is not None else reason,
        exception=exception,
    )
    decision = decide_retry_fallback(classification)
    return RunnerTerminalMetadata(
        status=status,
        reason=reason,
        errorCategory=classification.category,
        tsErrorCode=classification.ts_error_code,
        fallbackAction=decision.action,
        retryable=classification.retryable,
        failClosed=classification.fail_closed,
        restoreToTypeScript=classification.restore_to_typescript,
    )


def _projection_write_intents(
    *,
    session_key: str,
    turn_id: str,
    status: RunnerSessionBoundaryStatus,
) -> list[ProjectionWriteIntent]:
    payload: dict[str, object] = {
        "turnId": turn_id,
        "status": status,
        "localOnly": True,
    }
    return [
        ProjectionWriteIntent(
            target="transcript",
            operation="append",
            sessionKey=session_key,
            idempotencyKey=f"{turn_id}:transcript",
            payload=payload,
        ),
        ProjectionWriteIntent(
            target="sse",
            operation="publish",
            sessionKey=session_key,
            idempotencyKey=f"{turn_id}:sse",
            payload=payload,
        ),
        ProjectionWriteIntent(
            target="control_event",
            operation="append",
            sessionKey=session_key,
            idempotencyKey=f"{turn_id}:control-event",
            payload=payload,
        ),
        ProjectionWriteIntent(
            target="control_request",
            operation="enqueue",
            sessionKey=session_key,
            idempotencyKey=f"{turn_id}:control-request",
            payload=payload,
        ),
    ]


def _terminal_public_events(
    *,
    turn_id: str,
    status: Literal["aborted"],
    reason: str,
) -> list[dict[str, object]]:
    return [
        {
            "type": "error",
            "code": reason,
            "message": reason,
        },
        {
            "type": "turn_end",
            "turnId": turn_id,
            "status": status,
            "reason": reason,
        },
    ]


def _false_authority_payload(
    model_type: type[RunnerSessionAuthorityFlags],
) -> dict[str, bool]:
    return {
        field.alias or name: False
        for name, field in model_type.model_fields.items()
    }


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = [
    "ActiveTurnRegistry",
    "RunnerCancellationToken",
    "RunnerContextContinuityConfig",
    "RunnerContextContinuityMetadata",
    "RunnerContextContinuityReason",
    "RunnerContextContinuityStatus",
    "RunnerSessionAuthorityFlags",
    "RunnerSessionBoundary",
    "RunnerSessionBoundaryConfig",
    "RunnerSessionBoundaryReason",
    "RunnerSessionBoundaryResult",
    "RunnerSessionBoundaryStatus",
    "RunnerTerminalMetadata",
]
