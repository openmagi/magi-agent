from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import os
from pathlib import Path
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate3b_local_consumer import (
    validate_gate3b_local_consumer_path,
)
from magi_agent.shadow.gate4c1_runner_shadow_invoker import (
    Gate4C1RunnerShadowInvocationResult,
)
from magi_agent.shadow.gate4c2_shadow_comparison_report import (
    Gate4C2ShadowComparisonReport,
)


Gate4DStatus: TypeAlias = Literal[
    "skipped",
    "healthy",
    "unhealthy",
    "rollback_recommended",
    "redaction_violation",
    "error",
]
Gate4DReason: TypeAlias = Literal[
    "diagnostics_disabled",
    "within_local_shadow_thresholds",
    "kill_switch_enabled",
    "error_rate_exceeded",
    "divergence_rate_exceeded",
    "comparison_redaction_violation",
    "comparison_error",
    "comparison_incomplete",
    "unsafe_diagnostic_input",
    "artifact_write_error",
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


class Gate4DShadowAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    production_metrics_published: Literal[False] = Field(
        default=False,
        alias="productionMetricsPublished",
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
        "production_metrics_published",
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


class Gate4DLocalShadowDiagnosticsConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    runner_results: tuple[Gate4C1RunnerShadowInvocationResult, ...] = Field(
        default=(),
        alias="runnerResults",
    )
    comparison_reports: tuple[Gate4C2ShadowComparisonReport, ...] = Field(
        default=(),
        alias="comparisonReports",
    )
    output_dir: Path | None = Field(default=None, alias="outputDir")
    kill_switch_enabled: bool = Field(default=False, alias="killSwitchEnabled")
    max_latency_ms: int = Field(default=30000, ge=0, alias="maxLatencyMs")
    max_error_rate: float = Field(default=0.25, ge=0, le=1, alias="maxErrorRate")
    max_divergence_rate: float = Field(default=0.25, ge=0, le=1, alias="maxDivergenceRate")


class Gate4DLocalShadowDiagnosticsSnapshot(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate4d.localShadowDiagnostics.v1"] = Field(
        default="gate4d.localShadowDiagnostics.v1",
        alias="schemaVersion",
    )
    metrics_mode: Literal["local_diagnostic_shadow_metrics"] = Field(
        default="local_diagnostic_shadow_metrics",
        alias="metricsMode",
    )
    status: Gate4DStatus
    reason: Gate4DReason
    kill_switch_enabled: bool = Field(alias="killSwitchEnabled")
    runner_result_count: int = Field(default=0, ge=0, alias="runnerResultCount")
    runner_invoked_count: int = Field(default=0, ge=0, alias="runnerInvokedCount")
    runner_completed_count: int = Field(default=0, ge=0, alias="runnerCompletedCount")
    runner_error_count: int = Field(default=0, ge=0, alias="runnerErrorCount")
    runner_skipped_count: int = Field(default=0, ge=0, alias="runnerSkippedCount")
    runner_dropped_count: int = Field(default=0, ge=0, alias="runnerDroppedCount")
    comparison_report_count: int = Field(default=0, ge=0, alias="comparisonReportCount")
    comparison_match_count: int = Field(default=0, ge=0, alias="comparisonMatchCount")
    comparison_divergence_count: int = Field(
        default=0,
        ge=0,
        alias="comparisonDivergenceCount",
    )
    comparison_redaction_violation_count: int = Field(
        default=0,
        ge=0,
        alias="comparisonRedactionViolationCount",
    )
    comparison_error_count: int = Field(default=0, ge=0, alias="comparisonErrorCount")
    comparison_skipped_count: int = Field(default=0, ge=0, alias="comparisonSkippedCount")
    max_latency_ms: int = Field(default=0, ge=0, alias="maxLatencyMs")
    observed_max_latency_ms: int = Field(default=0, ge=0, alias="observedMaxLatencyMs")
    error_rate: float = Field(default=0, ge=0, le=1, alias="errorRate")
    divergence_rate: float = Field(default=0, ge=0, le=1, alias="divergenceRate")
    artifact_path: Path | None = Field(default=None, alias="artifactPath")
    error_class: str | None = Field(default=None, alias="errorClass")
    error_preview: str | None = Field(default=None, alias="errorPreview")
    attachment_flags: Gate4DShadowAuthorityFlags = Field(
        default_factory=Gate4DShadowAuthorityFlags,
        alias="attachmentFlags",
    )

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, _value: object) -> dict[str, bool]:
        return Gate4DShadowAuthorityFlags().model_dump(by_alias=True, mode="json")


def build_gate4d_local_shadow_diagnostics(
    config: Gate4DLocalShadowDiagnosticsConfig,
) -> Gate4DLocalShadowDiagnosticsSnapshot:
    if not config.enabled:
        return _snapshot(config, "skipped", "diagnostics_disabled")

    try:
        output_dir = _validated_output_dir(config.output_dir)
    except Exception as exc:
        return _snapshot(
            config,
            "error",
            "artifact_write_error",
            error_class=type(exc).__name__,
            error_preview=_redacted_preview(str(exc), 512),
        )

    if _contains_unsafe_diagnostic_text(config.runner_results, config.comparison_reports):
        snapshot = _snapshot(config, "redaction_violation", "unsafe_diagnostic_input")
        return _finalize(snapshot, output_dir=output_dir)

    status, reason = _status_for_config(config)
    snapshot = _snapshot(config, status, reason)
    return _finalize(snapshot, output_dir=output_dir)


def _status_for_config(
    config: Gate4DLocalShadowDiagnosticsConfig,
) -> tuple[Gate4DStatus, Gate4DReason]:
    if config.kill_switch_enabled:
        return "rollback_recommended", "kill_switch_enabled"
    if any(report.status == "redaction_violation" for report in config.comparison_reports):
        return "rollback_recommended", "comparison_redaction_violation"
    error_rate = _error_rate(config.runner_results)
    if error_rate > config.max_error_rate:
        return "unhealthy", "error_rate_exceeded"
    if any(report.status == "error" for report in config.comparison_reports):
        return "unhealthy", "comparison_error"
    if any(report.status == "skipped" for report in config.comparison_reports):
        return "unhealthy", "comparison_incomplete"
    divergence_rate = _divergence_rate(config.comparison_reports)
    if divergence_rate > config.max_divergence_rate:
        return "unhealthy", "divergence_rate_exceeded"
    return "healthy", "within_local_shadow_thresholds"


def _snapshot(
    config: Gate4DLocalShadowDiagnosticsConfig,
    status: Gate4DStatus,
    reason: Gate4DReason,
    *,
    error_class: str | None = None,
    error_preview: str | None = None,
) -> Gate4DLocalShadowDiagnosticsSnapshot:
    runner_results = config.runner_results
    reports = config.comparison_reports
    return Gate4DLocalShadowDiagnosticsSnapshot(
        status=status,
        reason=reason,
        killSwitchEnabled=config.kill_switch_enabled,
        runnerResultCount=len(runner_results),
        runnerInvokedCount=sum(1 for item in runner_results if item.runner_invoked),
        runnerCompletedCount=sum(1 for item in runner_results if item.status == "completed"),
        runnerErrorCount=sum(1 for item in runner_results if item.status == "error"),
        runnerSkippedCount=sum(1 for item in runner_results if item.status == "skipped"),
        runnerDroppedCount=sum(1 for item in runner_results if item.status == "dropped"),
        comparisonReportCount=len(reports),
        comparisonMatchCount=sum(1 for item in reports if item.status == "match"),
        comparisonDivergenceCount=sum(1 for item in reports if item.status == "diverged"),
        comparisonRedactionViolationCount=sum(
            1 for item in reports if item.status == "redaction_violation"
        ),
        comparisonErrorCount=sum(1 for item in reports if item.status == "error"),
        comparisonSkippedCount=sum(1 for item in reports if item.status == "skipped"),
        maxLatencyMs=config.max_latency_ms,
        observedMaxLatencyMs=max((item.latency_ms for item in runner_results), default=0),
        errorRate=_error_rate(runner_results),
        divergenceRate=_divergence_rate(reports),
        errorClass=error_class,
        errorPreview=error_preview,
    )


def _validated_output_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    return validate_gate3b_local_consumer_path(path)


def _finalize(
    snapshot: Gate4DLocalShadowDiagnosticsSnapshot,
    *,
    output_dir: Path | None,
) -> Gate4DLocalShadowDiagnosticsSnapshot:
    if output_dir is None:
        return snapshot
    try:
        path = _write_snapshot(output_dir, snapshot)
    except Exception as exc:
        return snapshot.model_copy(
            update={
                "status": "error",
                "reason": "artifact_write_error",
                "error_class": type(exc).__name__,
                "error_preview": _redacted_preview(str(exc), 512),
                "artifact_path": None,
            }
        )
    return snapshot.model_copy(update={"artifact_path": path})


def _write_snapshot(output_dir: Path, snapshot: Gate4DLocalShadowDiagnosticsSnapshot) -> Path:
    metrics_dir = output_dir / "shadow-diagnostics"
    _validate_child_output_dir(output_dir, metrics_dir)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    _validate_child_output_dir(output_dir, metrics_dir)
    path = metrics_dir / "gate4d-local-shadow-diagnostics.json"
    payload = snapshot.model_dump(by_alias=True, mode="json", warnings=False)
    tmp_path = path.with_name(f".{path.name}.tmp")
    _validate_child_file(metrics_dir, tmp_path)
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
        raise ValueError("Gate 4D diagnostics output directory must not be a symlink")
    resolved_parent = parent.resolve(strict=False)
    resolved_child = child.resolve(strict=False)
    if not resolved_child.is_relative_to(resolved_parent):
        raise ValueError("Gate 4D diagnostics output directory escaped isolated output path")


def _validate_child_file(parent: Path, child: Path) -> None:
    if child.exists() or child.is_symlink():
        raise ValueError("Gate 4D diagnostics temp path already exists")
    resolved_parent = parent.resolve(strict=False)
    resolved_child_parent = child.parent.resolve(strict=False)
    if not resolved_child_parent.is_relative_to(resolved_parent):
        raise ValueError("Gate 4D diagnostics temp path escaped isolated output path")


def _error_rate(items: tuple[Gate4C1RunnerShadowInvocationResult, ...]) -> float:
    if not items:
        return 0
    return sum(1 for item in items if item.status == "error") / len(items)


def _divergence_rate(items: tuple[Gate4C2ShadowComparisonReport, ...]) -> float:
    if not items:
        return 0
    return sum(1 for item in items if item.status == "diverged") / len(items)


def _contains_unsafe_diagnostic_text(
    runner_results: Iterable[Gate4C1RunnerShadowInvocationResult],
    reports: Iterable[Gate4C2ShadowComparisonReport],
) -> bool:
    values: list[str] = []
    for result in runner_results:
        values.extend(
            text
            for text in (result.output_preview, result.error_preview)
            if isinstance(text, str)
        )
    for report in reports:
        values.extend(
            (
                report.ts_recorded_output_preview,
                report.runner_output_preview,
                report.error_preview or "",
            )
        )
    return any(_UNSAFE_TEXT_RE.search(value) for value in values)


def _redacted_preview(text: str, max_chars: int) -> str:
    redacted = _UNSAFE_TEXT_RE.sub("[REDACTED]", text)
    if len(redacted) > max_chars:
        return redacted[:max_chars]
    return redacted


__all__ = [
    "Gate4DLocalShadowDiagnosticsConfig",
    "Gate4DLocalShadowDiagnosticsSnapshot",
    "Gate4DReason",
    "Gate4DShadowAuthorityFlags",
    "Gate4DStatus",
    "build_gate4d_local_shadow_diagnostics",
]
