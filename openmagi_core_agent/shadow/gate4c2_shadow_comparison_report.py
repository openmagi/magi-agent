from __future__ import annotations

from collections.abc import Mapping
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
from openmagi_core_agent.shadow.gate4c1_runner_shadow_invoker import (
    Gate4C1RunnerShadowInvocationResult,
)


Gate4C2Status: TypeAlias = Literal["skipped", "match", "diverged", "redaction_violation", "error"]
Gate4C2Reason: TypeAlias = Literal[
    "comparison_disabled",
    "runner_not_completed",
    "unsafe_recorded_output",
    "unsafe_runner_output",
    "normalized_preview_match",
    "normalized_preview_mismatch",
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
    r"\bclawy\.pro\b\S*"
    r")",
    re.IGNORECASE,
)


class Gate4C2AuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
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
        "adk_runner_invoked",
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


class Gate4C2ShadowComparisonConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    handoff: Gate4LocalHandoff
    runner_result: Gate4C1RunnerShadowInvocationResult = Field(alias="runnerResult")
    ts_recorded_output_preview: str = Field(alias="tsRecordedOutputPreview")
    output_dir: Path | None = Field(default=None, alias="outputDir")
    max_preview_chars: int = Field(default=512, ge=1, alias="maxPreviewChars")


class Gate4C2DiffSummary(BaseModel):
    model_config = _MODEL_CONFIG

    changed: bool
    normalized_ts_preview: str = Field(alias="normalizedTsPreview")
    normalized_runner_preview: str = Field(alias="normalizedRunnerPreview")


class Gate4C2ShadowComparisonReport(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate4c2.shadowComparisonReport.v1"] = Field(
        default="gate4c2.shadowComparisonReport.v1",
        alias="schemaVersion",
    )
    comparison_mode: Literal["local_diagnostic_runner_output_comparison"] = Field(
        default="local_diagnostic_runner_output_comparison",
        alias="comparisonMode",
    )
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    status: Gate4C2Status
    reason: Gate4C2Reason
    bundle_id: str = Field(alias="bundleId")
    source_bundle_id: str = Field(alias="sourceBundleId")
    runner_status: str = Field(alias="runnerStatus")
    runner_reason: str = Field(alias="runnerReason")
    ts_recorded_output_preview: str = Field(alias="tsRecordedOutputPreview")
    runner_output_preview: str = Field(alias="runnerOutputPreview")
    diff_summary: Gate4C2DiffSummary = Field(alias="diffSummary")
    artifact_path: Path | None = Field(default=None, alias="artifactPath")
    error_class: str | None = Field(default=None, alias="errorClass")
    error_preview: str | None = Field(default=None, alias="errorPreview")
    attachment_flags: Gate4C2AuthorityFlags = Field(
        default_factory=Gate4C2AuthorityFlags,
        alias="attachmentFlags",
    )

    @field_serializer("attachment_flags")
    def _serialize_attachment_flags(self, _value: object) -> dict[str, bool]:
        return Gate4C2AuthorityFlags().model_dump(by_alias=True, mode="json")


def build_gate4c2_shadow_comparison_report(
    config: Gate4C2ShadowComparisonConfig,
) -> Gate4C2ShadowComparisonReport:
    if not config.enabled:
        return _report(config, "skipped", "comparison_disabled")

    try:
        output_dir = _validated_output_dir(config.output_dir)
    except Exception as exc:
        return _report(
            config,
            "error",
            "artifact_write_error",
            error_class=type(exc).__name__,
            error_preview=_redacted_preview(str(exc), config.max_preview_chars),
        )

    ts_preview = _redacted_preview(config.ts_recorded_output_preview, config.max_preview_chars)
    runner_preview = _redacted_preview(
        config.runner_result.output_preview,
        config.max_preview_chars,
    )
    if _UNSAFE_TEXT_RE.search(config.ts_recorded_output_preview):
        report = _report(
            config,
            "redaction_violation",
            "unsafe_recorded_output",
            ts_preview=ts_preview,
            runner_preview=runner_preview,
        )
        return _finalize(report, output_dir=output_dir, max_preview_chars=config.max_preview_chars)
    if _UNSAFE_TEXT_RE.search(config.runner_result.output_preview):
        report = _report(
            config,
            "redaction_violation",
            "unsafe_runner_output",
            ts_preview=ts_preview,
            runner_preview=runner_preview,
        )
        return _finalize(report, output_dir=output_dir, max_preview_chars=config.max_preview_chars)

    if config.runner_result.status != "completed":
        report = _report(
            config,
            "skipped",
            "runner_not_completed",
            ts_preview=ts_preview,
            runner_preview=runner_preview,
        )
        return _finalize(report, output_dir=output_dir, max_preview_chars=config.max_preview_chars)

    status: Gate4C2Status = (
        "match" if _normalize(ts_preview) == _normalize(runner_preview) else "diverged"
    )
    reason: Gate4C2Reason = (
        "normalized_preview_match" if status == "match" else "normalized_preview_mismatch"
    )
    report = _report(
        config,
        status,
        reason,
        ts_preview=ts_preview,
        runner_preview=runner_preview,
    )
    return _finalize(report, output_dir=output_dir, max_preview_chars=config.max_preview_chars)


def _report(
    config: Gate4C2ShadowComparisonConfig,
    status: Gate4C2Status,
    reason: Gate4C2Reason,
    *,
    ts_preview: str | None = None,
    runner_preview: str | None = None,
    error_class: str | None = None,
    error_preview: str | None = None,
) -> Gate4C2ShadowComparisonReport:
    safe_ts_preview = ts_preview or _redacted_preview(
        config.ts_recorded_output_preview,
        config.max_preview_chars,
    )
    safe_runner_preview = runner_preview or _redacted_preview(
        config.runner_result.output_preview,
        config.max_preview_chars,
    )
    return Gate4C2ShadowComparisonReport(
        status=status,
        reason=reason,
        bundleId=config.handoff.bundle_id,
        sourceBundleId=config.handoff.source_bundle_id,
        runnerStatus=config.runner_result.status,
        runnerReason=config.runner_result.reason,
        tsRecordedOutputPreview=safe_ts_preview,
        runnerOutputPreview=safe_runner_preview,
        diffSummary=Gate4C2DiffSummary(
            changed=_normalize(safe_ts_preview) != _normalize(safe_runner_preview),
            normalizedTsPreview=_normalize(safe_ts_preview),
            normalizedRunnerPreview=_normalize(safe_runner_preview),
        ),
        errorClass=error_class,
        errorPreview=error_preview,
    )


def _validated_output_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    return validate_gate3b_local_consumer_path(path)


def _finalize(
    report: Gate4C2ShadowComparisonReport,
    *,
    output_dir: Path | None,
    max_preview_chars: int,
) -> Gate4C2ShadowComparisonReport:
    if output_dir is None:
        return report
    try:
        path = _write_report(output_dir, report)
    except Exception as exc:
        return report.model_copy(
            update={
                "status": "error",
                "reason": "artifact_write_error",
                "error_class": type(exc).__name__,
                "error_preview": _redacted_preview(str(exc), max_preview_chars),
                "artifact_path": None,
            }
        )
    return report.model_copy(update={"artifact_path": path})


def _write_report(output_dir: Path, report: Gate4C2ShadowComparisonReport) -> Path:
    reports_dir = output_dir / "shadow-comparison"
    _validate_child_output_dir(output_dir, reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    _validate_child_output_dir(output_dir, reports_dir)
    path = reports_dir / "gate4c2-shadow-comparison.json"
    payload = report.model_dump(by_alias=True, mode="json", warnings=False)
    tmp_path = path.with_name(f".{path.name}.tmp")
    _validate_child_file(reports_dir, tmp_path)
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
        raise ValueError("Gate 4C-2 report output directory must not be a symlink")
    resolved_parent = parent.resolve(strict=False)
    resolved_child = child.resolve(strict=False)
    if not resolved_child.is_relative_to(resolved_parent):
        raise ValueError("Gate 4C-2 report output directory escaped isolated output path")


def _validate_child_file(parent: Path, child: Path) -> None:
    if child.exists() or child.is_symlink():
        raise ValueError("Gate 4C-2 report temp path already exists")
    resolved_parent = parent.resolve(strict=False)
    resolved_child_parent = child.parent.resolve(strict=False)
    if not resolved_child_parent.is_relative_to(resolved_parent):
        raise ValueError("Gate 4C-2 report temp path escaped isolated output path")


def _redacted_preview(text: str, max_chars: int) -> str:
    redacted = _UNSAFE_TEXT_RE.sub("[REDACTED]", text)
    if len(redacted) > max_chars:
        return redacted[:max_chars]
    return redacted


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


__all__ = [
    "Gate4C2AuthorityFlags",
    "Gate4C2DiffSummary",
    "Gate4C2Reason",
    "Gate4C2ShadowComparisonConfig",
    "Gate4C2ShadowComparisonReport",
    "Gate4C2Status",
    "build_gate4c2_shadow_comparison_report",
]
