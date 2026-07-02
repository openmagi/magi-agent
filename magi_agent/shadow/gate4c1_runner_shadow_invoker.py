from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate3b_local_consumer import (
    validate_gate3b_local_consumer_path,
)
from magi_agent.shadow.gate4c0_shadow_config import (
    Gate4C0DecisionReason,
    Gate4C0ModelSelectionSource,
    Gate4C0ShadowConfig,
    resolve_gate4c0_shadow_config,
)
from magi_agent.shared.tool_preview import sanitize_tool_preview


Gate4C1RunnerStatus: TypeAlias = Literal["skipped", "dropped", "completed", "error"]
Gate4C1RunnerReason: TypeAlias = Literal[
    "runner_disabled",
    "gate4c0_not_accepted",
    "missing_output_dir",
    "unsafe_input",
    "input_too_large",
    "cost_budget_exhausted",
    "queue_budget_exhausted",
    "daily_budget_exhausted",
    "runner_completed",
    "runner_timeout",
    "runner_error",
    "diagnostic_artifact_error",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_UNSAFE_TEXT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"(?:Cookie|Set-Cookie):\s*[^;\r\n]+(?:;[^\r\n]*)?|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"xox[a-z]-[A-Za-z0-9-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"[\"']?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|"
    r"client[_-]?secret|private[_-]?key)[\"']?\s*:\s*[\"'][^\"'\r\n]{4,}[\"']|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|"
    r"_API_KEY|_SERVICE_ROLE_KEY))"
    r"\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password|service[_-]?role[_-]?key)"
    r"\s*[:=]\s*\S+|"
    r"hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|"
    r"private_tool_preview|private_tool_input|private_tool_output|raw_tool_preview|"
    r"/(?:data/bots|workspace|var/lib/kubelet|mnt|private|Users)\S*|"
    r"\bmagi\.pro\b\S*"
    r")",
    re.IGNORECASE,
)
_UNSAFE_PROJECTION_KEY_RE = re.compile(
    r"(?:"
    r"authorization|auth|cookie|set[_-]?cookie|"
    r"access[_-]?token|refresh[_-]?token|token|"
    r"api[_-]?key|client[_-]?secret|secret|password|"
    r"private[_-]?key|service[_-]?role[_-]?key|"
    r"raw[_-]?(?:tool|prompt|output|input|args?|results?|log)|"
    r"private[_-]?(?:tool|prompt|output|input|args?|results?|log)"
    r")",
    re.IGNORECASE,
)
_ALLOWED_AGENT_KWARGS = (
    "description",
    "generate_content_config",
    "instruction",
    "model",
    "name",
    "tools",
)
_ALLOWED_RUNNER_KWARGS = ("agent", "app_name", "auto_create_session", "session_service")
_ALLOWED_RUN_ASYNC_KWARGS = ("new_message", "session_id", "user_id")


@dataclass(frozen=True)
class Gate4C1AdkPrimitives:
    Agent: type
    Runner: type
    InMemorySessionService: type
    Content: type
    Part: type
    GenerateContentConfig: type


class Gate4C1RunnerAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    production_transcript_written: Literal[False] = Field(
        default=False,
        alias="productionTranscriptWritten",
    )
    production_sse_written: Literal[False] = Field(
        default=False,
        alias="productionSseWritten",
    )
    db_written: Literal[False] = Field(default=False, alias="dbWritten")
    channel_delivered: Literal[False] = Field(default=False, alias="channelDelivered")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    live_tools_executed: Literal[False] = Field(default=False, alias="liveToolsExecuted")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_queue_enqueued: Literal[False] = Field(
        default=False,
        alias="productionQueueEnqueued",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    evidence_block_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockEnabled",
    )
    child_execution_attached: Literal[False] = Field(
        default=False,
        alias="childExecutionAttached",
    )
    mission_scheduler_attached: Literal[False] = Field(
        default=False,
        alias="missionSchedulerAttached",
    )
    billing_auth_mutated: Literal[False] = Field(default=False, alias="billingAuthMutated")
    model_routing_mutated: Literal[False] = Field(
        default=False,
        alias="modelRoutingMutated",
    )
    canary_routed: Literal[False] = Field(default=False, alias="canaryRouted")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{key: False for key in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(by_alias=True, mode="python"))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {field.alias or name: False for name, field in cls.model_fields.items()}

    @field_serializer(
        "user_visible_output_attached",
        "production_transcript_written",
        "production_sse_written",
        "db_written",
        "channel_delivered",
        "workspace_mutated",
        "memory_written",
        "memory_provider_called",
        "toolhost_dispatched",
        "live_tools_executed",
        "production_storage_written",
        "production_queue_enqueued",
        "telegram_attached",
        "evidence_block_enabled",
        "child_execution_attached",
        "mission_scheduler_attached",
        "billing_auth_mutated",
        "model_routing_mutated",
        "canary_routed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate4C1RunnerShadowInvocationConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    gate4c0_config: Gate4C0ShadowConfig = Field(alias="gate4c0Config")
    sanitized_input_text: str = Field(default="", alias="sanitizedInputText")
    output_dir: Path | None = Field(default=None, alias="outputDir")
    max_input_chars: int = Field(default=8192, ge=1, alias="maxInputChars")
    max_output_chars: int = Field(default=2048, ge=1, alias="maxOutputChars")
    timeout_ms: int = Field(default=30000, ge=1, alias="timeoutMs")
    cost_budget_metadata: str = Field(default="represented_only", alias="costBudgetMetadata")
    fail_open: Literal[True] = Field(default=True, alias="failOpen")


class Gate4C1RunnerShadowInvocationResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate4c1.runnerShadowInvocation.v1"] = Field(
        default="gate4c1.runnerShadowInvocation.v1",
        alias="schemaVersion",
    )
    status: Gate4C1RunnerStatus
    reason: Gate4C1RunnerReason
    gate4c0_reason: Gate4C0DecisionReason | None = Field(
        default=None,
        alias="gate4c0Reason",
    )
    runner_invoked: bool = Field(default=False, alias="runnerInvoked")
    model_call_via_adk_runner_attempted: bool = Field(
        default=False,
        alias="modelCallViaAdkRunnerAttempted",
    )
    fail_open: Literal[True] = Field(default=True, alias="failOpen")
    event_count: int = Field(default=0, ge=0, alias="eventCount")
    output_preview: str = Field(default="", alias="outputPreview")
    output_truncated: bool = Field(default=False, alias="outputTruncated")
    output_redaction_applied: bool = Field(default=False, alias="outputRedactionApplied")
    diagnostic_agent_events: tuple[dict[str, object], ...] = Field(
        default=(),
        alias="diagnosticAgentEvents",
    )
    diagnostic_legacy_deltas: tuple[str, ...] = Field(
        default=(),
        alias="diagnosticLegacyDeltas",
    )
    diagnostic_transcript_entries: tuple[dict[str, object], ...] = Field(
        default=(),
        alias="diagnosticTranscriptEntries",
    )
    latency_ms: int = Field(default=0, ge=0, alias="latencyMs")
    timeout_ms: int = Field(default=0, ge=0, alias="timeoutMs")
    max_output_chars: int = Field(default=0, ge=0, alias="maxOutputChars")
    max_cost_usd: float = Field(default=0, ge=0, alias="maxCostUsd")
    max_queue_depth: int = Field(default=0, ge=0, alias="maxQueueDepth")
    model_selection_source: Gate4C0ModelSelectionSource = Field(
        default="invalid_or_missing",
        alias="modelSelectionSource",
    )
    selected_provider: str = Field(default="", alias="selectedProvider")
    selected_model: str = Field(default="", alias="selectedModel")
    agent_kwargs_keys: tuple[str, ...] = Field(default=(), alias="agentKwargsKeys")
    runner_kwargs_keys: tuple[str, ...] = Field(default=(), alias="runnerKwargsKeys")
    run_async_kwargs_keys: tuple[str, ...] = Field(default=(), alias="runAsyncKwargsKeys")
    error_class: str | None = Field(default=None, alias="errorClass")
    error_preview: str | None = Field(default=None, alias="errorPreview")
    diagnostic_artifact_error_class: str | None = Field(
        default=None,
        alias="diagnosticArtifactErrorClass",
    )
    diagnostic_artifact_error_preview: str | None = Field(
        default=None,
        alias="diagnosticArtifactErrorPreview",
    )
    diagnostic_artifact_path: Path | None = Field(default=None, alias="diagnosticArtifactPath")
    attachment_flags: Gate4C1RunnerAuthorityFlags = Field(
        default_factory=Gate4C1RunnerAuthorityFlags,
        alias="attachmentFlags",
    )

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, _value: object) -> dict[str, bool]:
        return Gate4C1RunnerAuthorityFlags().model_dump(by_alias=True, mode="json")


AdkPrimitivesLoader: TypeAlias = Callable[[], Gate4C1AdkPrimitives]


class RunnerShadowInvoker:
    def __init__(
        self,
        adk_primitives_loader: AdkPrimitivesLoader | None = None,
    ) -> None:
        self._adk_primitives_loader = adk_primitives_loader or load_gate4c1_adk_primitives

    def invoke(
        self,
        config: Gate4C1RunnerShadowInvocationConfig,
    ) -> Gate4C1RunnerShadowInvocationResult:
        return asyncio.run(self.invoke_async(config))

    async def invoke_async(
        self,
        config: Gate4C1RunnerShadowInvocationConfig,
    ) -> Gate4C1RunnerShadowInvocationResult:
        if not config.enabled:
            return _result(config, "skipped", "runner_disabled")

        try:
            output_dir = _validated_output_dir(config.output_dir)
        except Exception as exc:
            error_preview = _redacted_preview(str(exc), config.max_output_chars)[0]
            return _result(
                config,
                "error",
                "diagnostic_artifact_error",
                error_class=type(exc).__name__,
                error_preview=error_preview,
                diagnostic_artifact_error_class=type(exc).__name__,
                diagnostic_artifact_error_preview=error_preview,
            )

        gate4c0 = resolve_gate4c0_shadow_config(config.gate4c0_config)
        if gate4c0.status != "accepted":
            return _finalize(
                _result(
                    config,
                    "skipped",
                    "gate4c0_not_accepted",
                    gate4c0_reason=gate4c0.reason,
                ),
                output_dir=output_dir,
            )

        if output_dir is None:
            return _result(config, "dropped", "missing_output_dir")

        if _UNSAFE_TEXT_RE.search(config.sanitized_input_text):
            return _finalize(
                _result(config, "dropped", "unsafe_input"),
                output_dir=output_dir,
            )
        if len(config.sanitized_input_text) > config.max_input_chars:
            return _finalize(
                _result(config, "dropped", "input_too_large"),
                output_dir=output_dir,
            )
        if config.gate4c0_config.budget.max_cost_usd <= 0:
            return _finalize(
                _result(config, "dropped", "cost_budget_exhausted"),
                output_dir=output_dir,
            )
        if config.gate4c0_config.budget.max_queue_depth <= 0:
            return _finalize(
                _result(config, "dropped", "queue_budget_exhausted"),
                output_dir=output_dir,
            )
        if config.gate4c0_config.budget.max_daily_shadow_runs <= 0:
            return _finalize(
                _result(config, "dropped", "daily_budget_exhausted"),
                output_dir=output_dir,
            )

        start = time.perf_counter()
        primitives = self._adk_primitives_loader()
        generate_content_config = primitives.GenerateContentConfig(
            maxOutputTokens=config.max_output_chars,
        )
        agent_kwargs = {
            "name": "openmagi_gate4c1_shadow_agent",
            "description": "OpenMagi Gate 4C-1 local diagnostic shadow agent.",
            "model": config.gate4c0_config.model_routing.model,
            "instruction": _build_shadow_instruction(config),
            "tools": [],
            "generate_content_config": generate_content_config,
        }
        session_service = primitives.InMemorySessionService()
        agent = primitives.Agent(**_allowlist_kwargs(agent_kwargs, _ALLOWED_AGENT_KWARGS))
        runner_kwargs = {
            "app_name": "openmagi-gate4c1-shadow",
            "agent": agent,
            "session_service": session_service,
            "auto_create_session": True,
        }
        runner = primitives.Runner(**_allowlist_kwargs(runner_kwargs, _ALLOWED_RUNNER_KWARGS))
        message = primitives.Content(
            parts=[primitives.Part.from_text(text=config.sanitized_input_text)],
            role="user",
        )
        run_kwargs = {
            "user_id": "gate4c1-shadow-user",
            "session_id": _shadow_session_id(config),
            "new_message": message,
        }

        events: list[object] = []
        max_event_count = min(config.gate4c0_config.redaction_policy.max_event_count, 64)
        try:
            async with asyncio.timeout(config.timeout_ms / 1000):
                async for event in runner.run_async(
                    **_allowlist_kwargs(run_kwargs, _ALLOWED_RUN_ASYNC_KWARGS)
                ):
                    events.append(event)
                    if len(events) >= max_event_count:
                        break
        except TimeoutError:
            latency_ms = _elapsed_ms(start)
            return _finalize(
                _result(
                    config,
                    "error",
                    "runner_timeout",
                    runner_invoked=True,
                    model_attempted=True,
                    latency_ms=latency_ms,
                    agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                    runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                    run_async_kwargs_keys=tuple(sorted(run_kwargs)),
                    error_class="TimeoutError",
                    error_preview="ADK Runner shadow invocation exceeded its local timeout budget.",
                ),
                output_dir=output_dir,
            )
        except Exception as exc:
            latency_ms = _elapsed_ms(start)
            return _finalize(
                _result(
                    config,
                    "error",
                    "runner_error",
                    runner_invoked=True,
                    model_attempted=True,
                    latency_ms=latency_ms,
                    agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                    runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                    run_async_kwargs_keys=tuple(sorted(run_kwargs)),
                    error_class=type(exc).__name__,
                    error_preview=_redacted_preview(str(exc), config.max_output_chars)[0],
                ),
                output_dir=output_dir,
            )

        preview, truncated, redacted = _events_preview(events, config.max_output_chars)
        projected_agent_events, projected_legacy_deltas, projected_transcript_entries = (
            _project_diagnostic_events(
                events,
                turn_id=config.gate4c0_config.input_envelope.turn_id,
                max_chars=config.max_output_chars,
            )
        )
        return _finalize(
            _result(
                config,
                "completed",
                "runner_completed",
                runner_invoked=True,
                model_attempted=True,
                event_count=len(events),
                output_preview=preview,
                output_truncated=truncated,
                output_redaction_applied=redacted,
                diagnostic_agent_events=projected_agent_events,
                diagnostic_legacy_deltas=projected_legacy_deltas,
                diagnostic_transcript_entries=projected_transcript_entries,
                latency_ms=_elapsed_ms(start),
                agent_kwargs_keys=tuple(sorted(agent_kwargs)),
                runner_kwargs_keys=tuple(sorted(runner_kwargs)),
                run_async_kwargs_keys=tuple(sorted(run_kwargs)),
            ),
            output_dir=output_dir,
        )


def load_gate4c1_adk_primitives() -> Gate4C1AdkPrimitives:
    from google.adk import agents as adk_agents
    from google.adk import runners as adk_runners
    from google.adk import sessions as adk_sessions

    return Gate4C1AdkPrimitives(
        Agent=adk_agents.Agent,
        Runner=adk_runners.Runner,
        InMemorySessionService=adk_sessions.InMemorySessionService,
        Content=adk_runners.types.Content,
        Part=adk_runners.types.Part,
        GenerateContentConfig=adk_runners.types.GenerateContentConfig,
    )


def invoke_gate4c1_runner_shadow(
    config: Gate4C1RunnerShadowInvocationConfig,
    *,
    adk_primitives_loader: AdkPrimitivesLoader | None = None,
) -> Gate4C1RunnerShadowInvocationResult:
    invoker = RunnerShadowInvoker(adk_primitives_loader or load_gate4c1_adk_primitives)
    return invoker.invoke(config)


async def invoke_gate4c1_runner_shadow_async(
    config: Gate4C1RunnerShadowInvocationConfig,
    *,
    adk_primitives_loader: AdkPrimitivesLoader | None = None,
) -> Gate4C1RunnerShadowInvocationResult:
    invoker = RunnerShadowInvoker(adk_primitives_loader or load_gate4c1_adk_primitives)
    return await invoker.invoke_async(config)


def _result(
    config: Gate4C1RunnerShadowInvocationConfig,
    status: Gate4C1RunnerStatus,
    reason: Gate4C1RunnerReason,
    *,
    gate4c0_reason: Gate4C0DecisionReason | None = None,
    runner_invoked: bool = False,
    model_attempted: bool = False,
    event_count: int = 0,
    output_preview: str = "",
    output_truncated: bool = False,
    output_redaction_applied: bool = False,
    diagnostic_agent_events: tuple[dict[str, object], ...] = (),
    diagnostic_legacy_deltas: tuple[str, ...] = (),
    diagnostic_transcript_entries: tuple[dict[str, object], ...] = (),
    latency_ms: int = 0,
    agent_kwargs_keys: tuple[str, ...] = (),
    runner_kwargs_keys: tuple[str, ...] = (),
    run_async_kwargs_keys: tuple[str, ...] = (),
    error_class: str | None = None,
    error_preview: str | None = None,
    diagnostic_artifact_error_class: str | None = None,
    diagnostic_artifact_error_preview: str | None = None,
) -> Gate4C1RunnerShadowInvocationResult:
    return Gate4C1RunnerShadowInvocationResult(
        status=status,
        reason=reason,
        gate4c0Reason=gate4c0_reason,
        runnerInvoked=runner_invoked,
        modelCallViaAdkRunnerAttempted=model_attempted,
        eventCount=event_count,
        outputPreview=output_preview,
        outputTruncated=output_truncated,
        outputRedactionApplied=output_redaction_applied,
        diagnosticAgentEvents=diagnostic_agent_events,
        diagnosticLegacyDeltas=diagnostic_legacy_deltas,
        diagnosticTranscriptEntries=diagnostic_transcript_entries,
        latencyMs=latency_ms,
        timeoutMs=config.timeout_ms,
        maxOutputChars=config.max_output_chars,
        maxCostUsd=config.gate4c0_config.budget.max_cost_usd,
        maxQueueDepth=config.gate4c0_config.budget.max_queue_depth,
        modelSelectionSource=config.gate4c0_config.model_routing.model_selection_source,
        selectedProvider=config.gate4c0_config.model_routing.provider,
        selectedModel=config.gate4c0_config.model_routing.model,
        agentKwargsKeys=agent_kwargs_keys,
        runnerKwargsKeys=runner_kwargs_keys,
        runAsyncKwargsKeys=run_async_kwargs_keys,
        errorClass=error_class,
        errorPreview=error_preview,
        diagnosticArtifactErrorClass=diagnostic_artifact_error_class,
        diagnosticArtifactErrorPreview=diagnostic_artifact_error_preview,
    )


def _validated_output_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    return validate_gate3b_local_consumer_path(path)


def _finalize(
    result: Gate4C1RunnerShadowInvocationResult,
    *,
    output_dir: Path | None,
) -> Gate4C1RunnerShadowInvocationResult:
    if output_dir is None:
        return result
    try:
        artifact_path = _write_diagnostic_artifact(output_dir, result)
    except Exception as exc:
        error_preview = _redacted_preview(str(exc), max(result.max_output_chars, 1))[0]
        return result.model_copy(
            update={
                "status": "error",
                "reason": "diagnostic_artifact_error",
                "error_class": type(exc).__name__,
                "error_preview": error_preview,
                "diagnostic_artifact_error_class": type(exc).__name__,
                "diagnostic_artifact_error_preview": error_preview,
                "diagnostic_artifact_path": None,
            }
        )
    return result.model_copy(update={"diagnostic_artifact_path": artifact_path})


def _write_diagnostic_artifact(
    output_dir: Path,
    result: Gate4C1RunnerShadowInvocationResult,
) -> Path:
    diagnostics_dir = output_dir / "runner-shadow"
    _validate_child_output_dir(output_dir, diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    _validate_child_output_dir(output_dir, diagnostics_dir)
    path = diagnostics_dir / "gate4c1-runner-shadow-invocation.json"
    payload = result.model_dump(by_alias=True, mode="json", warnings=False)
    tmp_path = path.with_name(f".{path.name}.tmp")
    _validate_child_file(diagnostics_dir, tmp_path)
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    tmp_path.replace(path)
    return path


def _validate_child_output_dir(parent: Path, child: Path) -> None:
    if child.is_symlink():
        raise ValueError("Gate 4C-1 artifact output directory must not be a symlink")
    resolved_parent = parent.resolve(strict=False)
    resolved_child = child.resolve(strict=False)
    if not resolved_child.is_relative_to(resolved_parent):
        raise ValueError("Gate 4C-1 artifact output directory escaped isolated output path")


def _validate_child_file(parent: Path, child: Path) -> None:
    if child.exists() or child.is_symlink():
        raise ValueError("Gate 4C-1 artifact temp path already exists")
    resolved_parent = parent.resolve(strict=False)
    resolved_child_parent = child.parent.resolve(strict=False)
    if not resolved_child_parent.is_relative_to(resolved_parent):
        raise ValueError("Gate 4C-1 artifact temp path escaped isolated output path")


def _allowlist_kwargs(payload: Mapping[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in allowed if key in payload}


def _build_shadow_instruction(config: Gate4C1RunnerShadowInvocationConfig) -> str:
    pack_list = ", ".join(config.gate4c0_config.recipe_profile.selected_pack_ids)
    return (
        "You are running an OpenMagi Gate 4C-1 local diagnostic shadow pass. "
        "Do not claim production authority, do not request tools, do not write "
        "memory, and return a bounded diagnostic comparison candidate only. "
        f"Recipe snapshot: {config.gate4c0_config.recipe_profile.recipe_snapshot_id}. "
        f"Selected packs: {pack_list or 'none'}."
    )


def _shadow_session_id(config: Gate4C1RunnerShadowInvocationConfig) -> str:
    digest = config.gate4c0_config.input_envelope.session_id_digest.removeprefix("sha256:")
    return f"gate4c1-shadow-{digest[:24]}"


def _events_preview(events: list[object], max_chars: int) -> tuple[str, bool, bool]:
    raw = "\n".join(text for event in events if (text := _event_text(event)))
    return _redacted_preview(raw, max_chars)


def _project_diagnostic_events(
    events: list[object],
    *,
    turn_id: str,
    max_chars: int,
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...], tuple[dict[str, object], ...]]:
    if not any(_is_adk_event_like(event) for event in events):
        return (), (), ()

    from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge

    bridge = OpenMagiEventBridge()
    agent_events: list[dict[str, object]] = []
    legacy_deltas: list[str] = []
    transcript_entries: list[dict[str, object]] = []
    for event in events:
        if not _is_adk_event_like(event):
            continue
        projection = bridge.project_adk_event(event, turn_id=turn_id)
        agent_events.extend(
            _sanitize_projection_mapping(agent_event, max_chars=max_chars)
            for agent_event in projection.agent_events
        )
        legacy_deltas.extend(
            _sanitize_projection_text(delta, max_chars=max_chars)
            for delta in projection.legacy_deltas
        )
        transcript_entries.extend(
            _sanitize_projection_mapping(
                entry.model_dump(by_alias=True, exclude_none=True),
                max_chars=max_chars,
            )
            for entry in projection.transcript_entries
        )
    return tuple(agent_events), tuple(legacy_deltas), tuple(transcript_entries)


def _is_adk_event_like(event: object) -> bool:
    return (
        hasattr(event, "invocation_id")
        and hasattr(event, "author")
        and hasattr(event, "content")
    )


def _sanitize_projection_mapping(
    value: dict[str, object],
    *,
    max_chars: int,
) -> dict[str, object]:
    return {
        key: _sanitize_projection_value(
            child,
            max_chars=max_chars,
            parent_key=key,
        )
        for key, child in value.items()
    }


def _sanitize_projection_value(
    value: object,
    *,
    max_chars: int,
    parent_key: str | None = None,
) -> object:
    if parent_key is not None and _UNSAFE_PROJECTION_KEY_RE.search(parent_key):
        return "[REDACTED]"
    if isinstance(value, str):
        return _sanitize_projection_text(value, max_chars=max_chars)
    if isinstance(value, dict):
        return _sanitize_projection_mapping(value, max_chars=max_chars)
    if isinstance(value, list | tuple):
        return tuple(_sanitize_projection_value(item, max_chars=max_chars) for item in value)
    return value


def _sanitize_projection_text(value: str, *, max_chars: int) -> str:
    redacted = sanitize_tool_preview(_UNSAFE_TEXT_RE.sub("[REDACTED]", value))
    if len(redacted) > max_chars:
        return redacted[:max_chars]
    return redacted


def _event_text(event: object) -> str:
    if isinstance(event, str):
        return event
    if isinstance(event, Mapping):
        for key in ("text", "message", "error_message", "error"):
            value = event.get(key)
            if isinstance(value, str):
                return value
        content = event.get("content")
        if content is not None:
            return _content_text(content)
        return ""
    content = getattr(event, "content", None)
    if content is not None:
        return _content_text(content)
    for attr in ("text", "message", "error_message"):
        value = getattr(event, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        parts = content.get("parts")
        if isinstance(parts, list | tuple):
            return "\n".join(_part_text(part) for part in parts if _part_text(part))
        value = content.get("text")
        return value if isinstance(value, str) else ""
    parts = getattr(content, "parts", None)
    if isinstance(parts, list | tuple):
        return "\n".join(_part_text(part) for part in parts if _part_text(part))
    value = getattr(content, "text", None)
    return value if isinstance(value, str) else ""


def _part_text(part: object) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, Mapping):
        value = part.get("text")
        return value if isinstance(value, str) else ""
    value = getattr(part, "text", None)
    return value if isinstance(value, str) else ""


def _redacted_preview(text: str, max_chars: int) -> tuple[str, bool, bool]:
    redacted = _UNSAFE_TEXT_RE.sub("[REDACTED]", text)
    truncated = len(redacted) > max_chars
    if truncated:
        redacted = redacted[:max_chars]
    return redacted, truncated, redacted != text


def _elapsed_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


__all__ = [
    "Gate4C1AdkPrimitives",
    "Gate4C1RunnerAuthorityFlags",
    "Gate4C1RunnerReason",
    "Gate4C1RunnerShadowInvocationConfig",
    "Gate4C1RunnerShadowInvocationResult",
    "Gate4C1RunnerStatus",
    "RunnerShadowInvoker",
    "invoke_gate4c1_runner_shadow",
    "invoke_gate4c1_runner_shadow_async",
    "load_gate4c1_adk_primitives",
]
