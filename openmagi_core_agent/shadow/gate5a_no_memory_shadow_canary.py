from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from pathlib import Path
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.shadow.gate3b_local_consumer import (
    validate_gate3b_local_consumer_path,
)
from openmagi_core_agent.shadow.gate4_consumer import Gate4LocalHandoff
from openmagi_core_agent.shadow.gate4c0_shadow_config import (
    Gate4C0DecisionReason,
    Gate4C0ModelSelectionSource,
    Gate4C0ShadowConfig,
    resolve_gate4c0_shadow_config,
)
from openmagi_core_agent.shadow.gate4c1_runner_shadow_invoker import (
    Gate4C1RunnerShadowInvocationConfig,
    Gate4C1RunnerShadowInvocationResult,
    invoke_gate4c1_runner_shadow,
)
from openmagi_core_agent.shadow.gate4c2_shadow_comparison_report import (
    Gate4C2ShadowComparisonConfig,
    Gate4C2ShadowComparisonReport,
    build_gate4c2_shadow_comparison_report,
)
from openmagi_core_agent.shadow.gate4d_local_shadow_diagnostics import (
    Gate4DLocalShadowDiagnosticsConfig,
    Gate4DLocalShadowDiagnosticsSnapshot,
    build_gate4d_local_shadow_diagnostics,
)


Gate5AStatus: TypeAlias = Literal["skipped", "dropped", "completed", "error"]
Gate5AReason: TypeAlias = Literal[
    "shadow_canary_eligible",
    "shadow_canary_completed",
    "canary_disabled",
    "kill_switch_enabled",
    "missing_bot_allowlist",
    "missing_org_allowlist",
    "missing_environment_allowlist",
    "bot_not_allowlisted",
    "org_not_allowlisted",
    "environment_not_allowlisted",
    "daily_limit_exhausted",
    "concurrency_limit_exhausted",
    "cost_limit_exhausted",
    "cost_limit_exceeded",
    "timeout_limit_exceeded",
    "output_limit_exceeded",
    "shadow_config_mismatch",
    "redaction_not_verified",
    "redaction_violation",
    "input_too_large",
    "event_count_too_large",
    "unsafe_input",
    "gate4c0_not_accepted",
    "missing_output_dir",
    "runner_not_completed",
    "shadow_runner_error",
    "artifact_write_error",
]
RunnerInvoker: TypeAlias = Callable[
    [Gate4C1RunnerShadowInvocationConfig],
    Gate4C1RunnerShadowInvocationResult,
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
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
    r"\bclawy\.pro\b\S*"
    r")",
    re.IGNORECASE,
)


class _Gate5AModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate5ANoMemoryShadowCanaryAuthorityFlags(BaseModel):
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


class Gate5ANoMemoryShadowCanaryPolicy(_Gate5AModel):
    tools_mode: Literal["disabled", "stubbed"] = Field(default="disabled", alias="toolsMode")
    memory_mode: Literal["disabled", "read_only", "test_only"] = Field(
        default="disabled",
        alias="memoryMode",
    )
    output_mode: Literal["local_diagnostic_artifacts_only"] = Field(
        default="local_diagnostic_artifacts_only",
        alias="outputMode",
    )
    toolhost_dispatch_attached: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchAttached",
    )
    live_tools_executed: Literal[False] = Field(default=False, alias="liveToolsExecuted")
    memory_provider_calls_enabled: Literal[False] = Field(
        default=False,
        alias="memoryProviderCallsEnabled",
    )
    memory_writes_enabled: Literal[False] = Field(
        default=False,
        alias="memoryWritesEnabled",
    )
    prompt_injection_enabled: Literal[False] = Field(
        default=False,
        alias="promptInjectionEnabled",
    )
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
        data = dict(values)
        for name, field in cls.model_fields.items():
            if name in {"tools_mode", "memory_mode", "output_mode"}:
                continue
            data[field.alias or name] = False
            data.pop(name, None)
        return cls(**data)

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        for name, field in cls.model_fields.items():
            if name in {"tools_mode", "memory_mode", "output_mode"}:
                continue
            data[field.alias or name] = False
            data.pop(name, None)
        return data


class Gate5ANoMemoryShadowCanaryCounters(_Gate5AModel):
    accepted: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    dropped: int = Field(default=0, ge=0)
    redaction_failures: int = Field(default=0, ge=0, alias="redactionFailures")
    runner_invoked: int = Field(default=0, ge=0, alias="runnerInvoked")
    model_call_via_adk_runner_attempted: int = Field(
        default=0,
        ge=0,
        alias="modelCallViaAdkRunnerAttempted",
    )
    comparison_reports: int = Field(default=0, ge=0, alias="comparisonReports")
    diagnostics_snapshots: int = Field(default=0, ge=0, alias="diagnosticsSnapshots")


class Gate5ANoMemoryShadowCanaryDecision(_Gate5AModel):
    status: Gate5AStatus
    reason: Gate5AReason
    gate4c0_reason: Gate4C0DecisionReason | None = Field(
        default=None,
        alias="gate4c0Reason",
    )
    attachment_flags: Gate5ANoMemoryShadowCanaryAuthorityFlags = Field(
        default_factory=Gate5ANoMemoryShadowCanaryAuthorityFlags,
        alias="attachmentFlags",
    )


class Gate5ANoMemoryShadowCanaryConfig(_Gate5AModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    selected_bot_digest: str = Field(alias="selectedBotDigest")
    selected_org_digest: str = Field(alias="selectedOrgDigest")
    environment: str
    bot_allowlist_digests: tuple[str, ...] = Field(default=(), alias="botAllowlistDigests")
    org_allowlist_digests: tuple[str, ...] = Field(default=(), alias="orgAllowlistDigests")
    environment_allowlist: tuple[str, ...] = Field(default=(), alias="environmentAllowlist")
    runner_config: Gate4C1RunnerShadowInvocationConfig = Field(alias="runnerConfig")
    handoff: Gate4LocalHandoff
    ts_recorded_output_preview: str = Field(alias="tsRecordedOutputPreview")
    output_dir: Path | None = Field(default=None, alias="outputDir")
    policy: Gate5ANoMemoryShadowCanaryPolicy = Field(
        default_factory=Gate5ANoMemoryShadowCanaryPolicy,
    )
    current_daily_canary_count: int = Field(default=0, ge=0, alias="currentDailyCanaryCount")
    max_daily_canary_count: int = Field(default=100, ge=0, alias="maxDailyCanaryCount")
    current_pending_shadow_runs: int = Field(default=0, ge=0, alias="currentPendingShadowRuns")
    max_concurrent_shadow_runs: int = Field(default=1, ge=0, alias="maxConcurrentShadowRuns")
    max_input_bytes: int = Field(default=262_144, ge=1, alias="maxInputBytes")
    max_output_chars: int = Field(default=512, ge=1, alias="maxOutputChars")
    timeout_ms: int = Field(default=30_000, ge=1, alias="timeoutMs")
    max_cost_usd: float = Field(default=0, ge=0, alias="maxCostUsd")

    @model_validator(mode="after")
    def _validate_public_safe_metadata(self) -> Self:
        for value in (self.selected_bot_digest, self.selected_org_digest):
            if not _DIGEST_RE.match(value):
                raise ValueError("Gate 5A selected IDs must be sha256 digests")
        for value in (*self.bot_allowlist_digests, *self.org_allowlist_digests):
            if not _DIGEST_RE.match(value):
                raise ValueError("Gate 5A allowlist IDs must be sha256 digests")
        for value in (self.environment, *self.environment_allowlist):
            _reject_unsafe_text(value)
        return self


class Gate5ANoMemoryShadowCanaryRunResult(_Gate5AModel):
    schema_version: Literal["gate5a.noMemoryShadowCanaryRun.v1"] = Field(
        default="gate5a.noMemoryShadowCanaryRun.v1",
        alias="schemaVersion",
    )
    canary_mode: Literal["no_memory_shadow_diagnostic"] = Field(
        default="no_memory_shadow_diagnostic",
        alias="canaryMode",
    )
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    status: Gate5AStatus
    reason: Gate5AReason
    eligibility: Gate5ANoMemoryShadowCanaryDecision
    policy: Gate5ANoMemoryShadowCanaryPolicy
    model_selection_source: Gate4C0ModelSelectionSource = Field(
        default="invalid_or_missing",
        alias="modelSelectionSource",
    )
    selected_provider: str = Field(default="", alias="selectedProvider")
    selected_model: str = Field(default="", alias="selectedModel")
    counters: Gate5ANoMemoryShadowCanaryCounters = Field(
        default_factory=Gate5ANoMemoryShadowCanaryCounters,
    )
    runner_result: Gate4C1RunnerShadowInvocationResult | None = Field(
        default=None,
        alias="runnerResult",
    )
    comparison_report: Gate4C2ShadowComparisonReport | None = Field(
        default=None,
        alias="comparisonReport",
    )
    diagnostics_snapshot: Gate4DLocalShadowDiagnosticsSnapshot | None = Field(
        default=None,
        alias="diagnosticsSnapshot",
    )
    artifact_path: Path | None = Field(default=None, alias="artifactPath")
    error_class: str | None = Field(default=None, alias="errorClass")
    error_preview: str | None = Field(default=None, alias="errorPreview")
    attachment_flags: Gate5ANoMemoryShadowCanaryAuthorityFlags = Field(
        default_factory=Gate5ANoMemoryShadowCanaryAuthorityFlags,
        alias="attachmentFlags",
    )

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, _value: object) -> dict[str, bool]:
        return Gate5ANoMemoryShadowCanaryAuthorityFlags().model_dump(
            by_alias=True,
            mode="json",
        )


def resolve_gate5a_no_memory_shadow_canary(
    config: Gate5ANoMemoryShadowCanaryConfig,
) -> Gate5ANoMemoryShadowCanaryDecision:
    if not config.enabled:
        return _decision("skipped", "canary_disabled")
    if config.kill_switch_enabled:
        return _decision("skipped", "kill_switch_enabled")
    if not config.bot_allowlist_digests:
        return _decision("skipped", "missing_bot_allowlist")
    if not config.org_allowlist_digests:
        return _decision("skipped", "missing_org_allowlist")
    if not config.environment_allowlist:
        return _decision("skipped", "missing_environment_allowlist")
    if config.selected_bot_digest not in config.bot_allowlist_digests:
        return _decision("skipped", "bot_not_allowlisted")
    if config.selected_org_digest not in config.org_allowlist_digests:
        return _decision("skipped", "org_not_allowlisted")
    if config.environment not in config.environment_allowlist:
        return _decision("skipped", "environment_not_allowlisted")
    if config.current_daily_canary_count >= config.max_daily_canary_count:
        return _decision("dropped", "daily_limit_exhausted")
    if config.current_pending_shadow_runs >= config.max_concurrent_shadow_runs:
        return _decision("dropped", "concurrency_limit_exhausted")
    if config.max_cost_usd <= 0:
        return _decision("dropped", "cost_limit_exhausted")
    if _UNSAFE_TEXT_RE.search(config.runner_config.sanitized_input_text):
        return _decision("dropped", "unsafe_input")
    if _UNSAFE_TEXT_RE.search(config.ts_recorded_output_preview):
        return _decision("dropped", "unsafe_input")
    if not config.handoff.redaction_verified:
        return _decision("dropped", "redaction_not_verified")
    if len(config.runner_config.sanitized_input_text.encode("utf-8")) > config.max_input_bytes:
        return _decision("dropped", "input_too_large")
    if config.runner_config.output_dir != config.output_dir:
        return _decision("dropped", "shadow_config_mismatch")
    if config.runner_config.timeout_ms > config.timeout_ms:
        return _decision("dropped", "timeout_limit_exceeded")
    if config.runner_config.max_output_chars > config.max_output_chars:
        return _decision("dropped", "output_limit_exceeded")
    if config.runner_config.gate4c0_config.budget.max_cost_usd > config.max_cost_usd:
        return _decision("dropped", "cost_limit_exceeded")

    runner_allowlist = config.runner_config.gate4c0_config.allowlist
    if (
        runner_allowlist.selected_bot_digest != config.selected_bot_digest
        or runner_allowlist.selected_org_digest != config.selected_org_digest
        or runner_allowlist.environment != config.environment
    ):
        return _decision("dropped", "shadow_config_mismatch")

    gate4c0 = resolve_gate4c0_shadow_config(config.runner_config.gate4c0_config)
    if gate4c0.status != "accepted":
        reason = _map_gate4c0_reason(gate4c0.reason)
        return _decision(gate4c0.status, reason, gate4c0_reason=gate4c0.reason)

    return _decision("completed", "shadow_canary_eligible")


def run_gate5a_no_memory_shadow_canary(
    config: Gate5ANoMemoryShadowCanaryConfig,
    *,
    runner_invoker: RunnerInvoker = invoke_gate4c1_runner_shadow,
) -> Gate5ANoMemoryShadowCanaryRunResult:
    eligibility = resolve_gate5a_no_memory_shadow_canary(config)
    if eligibility.status != "completed":
        result = _run_result(
            config,
            eligibility.status,
            eligibility.reason,
            eligibility=eligibility,
            counters=_counters_for_decision(eligibility.reason, eligibility.status),
        )
        return _finalize_if_possible(result, output_dir=None)

    if config.output_dir is None:
        return _run_result(
            config,
            "dropped",
            "missing_output_dir",
            eligibility=eligibility,
            counters=Gate5ANoMemoryShadowCanaryCounters(dropped=1),
        )

    try:
        output_dir = _validated_output_dir(config.output_dir)
    except Exception as exc:
        return _run_result(
            config,
            "error",
            "artifact_write_error",
            eligibility=eligibility,
            counters=Gate5ANoMemoryShadowCanaryCounters(dropped=1),
            error_class=type(exc).__name__,
            error_preview=_redacted_preview(str(exc), config.max_output_chars),
        )

    try:
        runner_result = runner_invoker(config.runner_config)
    except Exception as exc:
        result = _run_result(
            config,
            "error",
            "shadow_runner_error",
            eligibility=eligibility,
            counters=Gate5ANoMemoryShadowCanaryCounters(accepted=1),
            error_class=type(exc).__name__,
            error_preview=_redacted_preview(str(exc), config.max_output_chars),
        )
        return _finalize_if_possible(result, output_dir=output_dir)

    comparison_report = build_gate4c2_shadow_comparison_report(
        Gate4C2ShadowComparisonConfig(
            enabled=True,
            handoff=config.handoff,
            runnerResult=runner_result,
            tsRecordedOutputPreview=config.ts_recorded_output_preview,
            outputDir=output_dir,
            maxPreviewChars=config.max_output_chars,
        )
    )
    diagnostics_snapshot = build_gate4d_local_shadow_diagnostics(
        Gate4DLocalShadowDiagnosticsConfig(
            enabled=True,
            runnerResults=(runner_result,),
            comparisonReports=(comparison_report,),
            outputDir=output_dir,
            killSwitchEnabled=False,
            maxLatencyMs=config.timeout_ms,
        )
    )
    counters = Gate5ANoMemoryShadowCanaryCounters(
        accepted=1,
        redactionFailures=int(comparison_report.status == "redaction_violation"),
        runnerInvoked=int(runner_result.runner_invoked),
        modelCallViaAdkRunnerAttempted=int(
            runner_result.model_call_via_adk_runner_attempted
        ),
        comparisonReports=1,
        diagnosticsSnapshots=1,
    )
    status: Gate5AStatus = "completed"
    reason: Gate5AReason = "shadow_canary_completed"
    if runner_result.status == "error":
        status = "error"
        reason = "runner_not_completed"
    elif runner_result.status in {"skipped", "dropped"}:
        status = "dropped"
        reason = "runner_not_completed"
    elif comparison_report.status == "redaction_violation":
        status = "dropped"
        reason = "redaction_violation"
    elif diagnostics_snapshot.status in {"rollback_recommended", "redaction_violation"}:
        status = "dropped"
        reason = "redaction_violation"

    result = _run_result(
        config,
        status,
        reason,
        eligibility=eligibility,
        counters=counters,
        runner_result=runner_result,
        comparison_report=comparison_report,
        diagnostics_snapshot=diagnostics_snapshot,
    )
    return _finalize_if_possible(result, output_dir=output_dir)


def _decision(
    status: Gate5AStatus,
    reason: Gate5AReason,
    *,
    gate4c0_reason: Gate4C0DecisionReason | None = None,
) -> Gate5ANoMemoryShadowCanaryDecision:
    return Gate5ANoMemoryShadowCanaryDecision(
        status=status,
        reason=reason,
        gate4c0Reason=gate4c0_reason,
    )


def _map_gate4c0_reason(reason: Gate4C0DecisionReason) -> Gate5AReason:
    if reason in {
        "redaction_not_verified",
        "input_too_large",
        "event_count_too_large",
        "missing_bot_allowlist",
        "missing_org_allowlist",
        "missing_environment_allowlist",
        "bot_not_allowlisted",
        "org_not_allowlisted",
        "environment_not_allowlisted",
        "kill_switch_enabled",
    }:
        return reason
    return "gate4c0_not_accepted"


def _counters_for_decision(
    reason: Gate5AReason,
    status: Gate5AStatus,
) -> Gate5ANoMemoryShadowCanaryCounters:
    return Gate5ANoMemoryShadowCanaryCounters(
        skipped=int(status == "skipped"),
        dropped=int(status == "dropped"),
        redactionFailures=int(reason in {"redaction_not_verified", "unsafe_input"}),
    )


def _run_result(
    config: Gate5ANoMemoryShadowCanaryConfig,
    status: Gate5AStatus,
    reason: Gate5AReason,
    *,
    eligibility: Gate5ANoMemoryShadowCanaryDecision,
    counters: Gate5ANoMemoryShadowCanaryCounters,
    runner_result: Gate4C1RunnerShadowInvocationResult | None = None,
    comparison_report: Gate4C2ShadowComparisonReport | None = None,
    diagnostics_snapshot: Gate4DLocalShadowDiagnosticsSnapshot | None = None,
    error_class: str | None = None,
    error_preview: str | None = None,
) -> Gate5ANoMemoryShadowCanaryRunResult:
    return Gate5ANoMemoryShadowCanaryRunResult(
        status=status,
        reason=reason,
        eligibility=eligibility,
        policy=_policy_from_config(config),
        modelSelectionSource=config.runner_config.gate4c0_config.model_routing.model_selection_source,
        selectedProvider=config.runner_config.gate4c0_config.model_routing.provider,
        selectedModel=config.runner_config.gate4c0_config.model_routing.model,
        counters=counters,
        runnerResult=runner_result,
        comparisonReport=comparison_report,
        diagnosticsSnapshot=diagnostics_snapshot,
        errorClass=error_class,
        errorPreview=error_preview,
    )


def _policy_from_config(
    config: Gate5ANoMemoryShadowCanaryConfig,
) -> Gate5ANoMemoryShadowCanaryPolicy:
    gate4c0: Gate4C0ShadowConfig = config.runner_config.gate4c0_config
    memory_mode: Literal["disabled", "read_only", "test_only"] = gate4c0.memory_policy.mode
    return Gate5ANoMemoryShadowCanaryPolicy(
        toolsMode=gate4c0.tool_policy.mode,
        memoryMode=memory_mode,
    )


def _validated_output_dir(path: Path) -> Path:
    return validate_gate3b_local_consumer_path(path)


def _finalize_if_possible(
    result: Gate5ANoMemoryShadowCanaryRunResult,
    *,
    output_dir: Path | None,
) -> Gate5ANoMemoryShadowCanaryRunResult:
    if output_dir is None:
        return result
    try:
        path = _write_result(output_dir, result)
    except Exception as exc:
        return result.model_copy(
            update={
                "status": "error",
                "reason": "artifact_write_error",
                "artifact_path": None,
                "error_class": type(exc).__name__,
                "error_preview": _redacted_preview(str(exc), 512),
            }
        )
    return result.model_copy(update={"artifact_path": path})


def _write_result(
    output_dir: Path,
    result: Gate5ANoMemoryShadowCanaryRunResult,
) -> Path:
    canary_dir = output_dir / "gate5a-shadow-canary"
    _validate_child_output_dir(output_dir, canary_dir)
    canary_dir.mkdir(parents=True, exist_ok=True)
    _validate_child_output_dir(output_dir, canary_dir)
    path = canary_dir / "gate5a-no-memory-shadow-canary.json"
    tmp_path = path.with_name(f".{path.name}.tmp")
    _validate_child_file(canary_dir, tmp_path)
    payload = result.model_dump(by_alias=True, mode="json", warnings=False)
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
        raise ValueError("Gate 5A output directory must not be a symlink")
    resolved_parent = parent.resolve(strict=False)
    resolved_child = child.resolve(strict=False)
    if not resolved_child.is_relative_to(resolved_parent):
        raise ValueError("Gate 5A output directory escaped isolated output path")


def _validate_child_file(parent: Path, child: Path) -> None:
    if child.exists() or child.is_symlink():
        raise ValueError("Gate 5A temp output path already exists")
    resolved_parent = parent.resolve(strict=False)
    resolved_child_parent = child.parent.resolve(strict=False)
    if not resolved_child_parent.is_relative_to(resolved_parent):
        raise ValueError("Gate 5A temp output path escaped isolated output path")


def _reject_unsafe_text(value: str) -> None:
    if _UNSAFE_TEXT_RE.search(value):
        raise ValueError("Gate 5A metadata must be sanitized and public-safe")


def _redacted_preview(text: str, max_chars: int) -> str:
    redacted = _UNSAFE_TEXT_RE.sub("[REDACTED]", text)
    if len(redacted) > max_chars:
        return redacted[:max_chars]
    return redacted


__all__ = [
    "Gate5ANoMemoryShadowCanaryAuthorityFlags",
    "Gate5ANoMemoryShadowCanaryConfig",
    "Gate5ANoMemoryShadowCanaryCounters",
    "Gate5ANoMemoryShadowCanaryDecision",
    "Gate5ANoMemoryShadowCanaryPolicy",
    "Gate5ANoMemoryShadowCanaryRunResult",
    "Gate5AReason",
    "Gate5AStatus",
    "resolve_gate5a_no_memory_shadow_canary",
    "run_gate5a_no_memory_shadow_canary",
]
