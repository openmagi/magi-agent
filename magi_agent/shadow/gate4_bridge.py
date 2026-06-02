from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.shadow.gate3b_local_consumer import (
    Gate3BLocalConsumedBundle,
    Gate3BLocalConsumerConfig,
    Gate3BLocalSkippedFile,
    consume_gate3b_local_files,
    validate_gate3b_local_consumer_path,
)
from magi_agent.shadow.gate3b_local_report import (
    Gate3BLocalComparisonReport,
    build_gate3b_local_comparison_reports,
)
from magi_agent.shadow.gate3b_metrics import (
    Gate3BLocalMetricsSnapshot,
    build_gate3b_local_metrics_snapshot,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DETERMINISTIC_GENERATED_AT = datetime(1970, 1, 1, tzinfo=UTC)


class Gate4LocalBridgeError(ValueError):
    pass


class Gate4LocalBridgeAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_shadow_executed: Literal[False] = Field(default=False, alias="liveShadowExecuted")
    tools_executed: Literal[False] = Field(default=False, alias="toolsExecuted")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    public_output_attached: Literal[False] = Field(
        default=False,
        alias="publicOutputAttached",
    )
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    production_route_attached: Literal[False] = Field(
        default=False,
        alias="productionRouteAttached",
    )
    production_transcript_attached: Literal[False] = Field(
        default=False,
        alias="productionTranscriptAttached",
    )
    production_sse_attached: Literal[False] = Field(
        default=False,
        alias="productionSseAttached",
    )
    production_storage_attached: Literal[False] = Field(
        default=False,
        alias="productionStorageAttached",
    )
    production_queue_attached: Literal[False] = Field(
        default=False,
        alias="productionQueueAttached",
    )
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
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
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump(by_alias=True, mode="python"))

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {field.alias or name: False for name, field in cls.model_fields.items()}

    @field_serializer(
        "adk_runner_invoked",
        "live_shadow_executed",
        "tools_executed",
        "shell_or_code_executed",
        "public_output_attached",
        "user_visible_output_attached",
        "production_route_attached",
        "production_transcript_attached",
        "production_sse_attached",
        "production_storage_attached",
        "production_queue_attached",
        "telegram_attached",
        "canary_attached",
        "memory_provider_called",
        "workspace_mutated",
        "evidence_block_enabled",
        "child_execution_attached",
        "mission_scheduler_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate4LocalBridgeConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    input_dir: Path | None = Field(default=None, alias="inputDir")
    output_dir: Path | None = Field(default=None, alias="outputDir")
    max_files: int = Field(default=100, ge=1, alias="maxFiles")
    max_total_bytes: int = Field(default=10_485_760, ge=1, alias="maxTotalBytes")
    max_bundle_bytes: int = Field(default=262_144, ge=1, alias="maxBundleBytes")
    processed_bundle_ids: tuple[str, ...] = Field(default=(), alias="processedBundleIds")


class Gate4LocalBridgeResult(BaseModel):
    model_config = _MODEL_CONFIG

    bridge_mode: Literal["gate4_local_file_bridge"] = Field(
        default="gate4_local_file_bridge",
        alias="bridgeMode",
    )
    consumed: tuple[Gate3BLocalConsumedBundle, ...] = ()
    skipped: tuple[Gate3BLocalSkippedFile, ...] = ()
    reports: tuple[Gate3BLocalComparisonReport, ...] = ()
    report_paths: tuple[Path, ...] = Field(default=(), alias="reportPaths")
    metrics: Gate3BLocalMetricsSnapshot | None = None
    metrics_path: Path | None = Field(default=None, alias="metricsPath")
    local_diagnostic_artifacts_written: int = Field(
        default=0,
        ge=0,
        alias="localDiagnosticArtifactsWritten",
    )
    attachment_flags: Gate4LocalBridgeAttachmentFlags = Field(
        default_factory=Gate4LocalBridgeAttachmentFlags,
        alias="attachmentFlags",
    )


def run_gate4_local_bridge(config: Gate4LocalBridgeConfig) -> Gate4LocalBridgeResult:
    if not config.enabled:
        return Gate4LocalBridgeResult()
    if config.input_dir is None:
        raise Gate4LocalBridgeError("Gate 4 local bridge input_dir is required")
    if config.output_dir is None:
        raise Gate4LocalBridgeError("Gate 4 local bridge output_dir is required")

    input_dir = _validate_bridge_path(config.input_dir, "input")
    output_dir = _validate_bridge_path(config.output_dir, "output")
    _reject_mixed_input_output(input_dir, output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = output_dir / "reports"
    metrics_dir = output_dir / "metrics"
    reports_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    _clear_bridge_artifacts(reports_dir, "report-*.json")
    _clear_bridge_artifacts(metrics_dir, "metrics-snapshot.json")

    consumer_result = consume_gate3b_local_files(
        Gate3BLocalConsumerConfig(
            enabled=True,
            input_dir=input_dir,
            max_files=config.max_files,
            max_total_bytes=config.max_total_bytes,
            max_bundle_bytes=config.max_bundle_bytes,
            processed_bundle_ids=config.processed_bundle_ids,
        )
    )
    reports = tuple(
        report.model_copy(update={"generated_at": _DETERMINISTIC_GENERATED_AT})
        for report in build_gate3b_local_comparison_reports(consumer_result.consumed)
    )
    metrics = build_gate3b_local_metrics_snapshot(
        consumer_result=consumer_result,
        reports=reports,
    ).model_copy(update={"generated_at": _DETERMINISTIC_GENERATED_AT})

    report_paths = tuple(
        _write_json_artifact(
            reports_dir / f"report-{index:06d}.json",
            report.model_dump(by_alias=True, mode="json", warnings=False),
        )
        for index, report in enumerate(reports, start=1)
    )
    metrics_path = _write_json_artifact(
        metrics_dir / "metrics-snapshot.json",
        metrics.model_dump(by_alias=True, mode="json", warnings=False),
    )

    return Gate4LocalBridgeResult(
        consumed=consumer_result.consumed,
        skipped=consumer_result.skipped,
        reports=reports,
        reportPaths=report_paths,
        metrics=metrics,
        metricsPath=metrics_path,
        localDiagnosticArtifactsWritten=len(report_paths) + 1,
    )


def _validate_bridge_path(path: Path | str, label: str) -> Path:
    try:
        return validate_gate3b_local_consumer_path(path)
    except Exception as exc:
        raise Gate4LocalBridgeError(f"Gate 4 local bridge {label} path is unsafe") from exc


def _reject_mixed_input_output(input_dir: Path, output_dir: Path) -> None:
    if output_dir == input_dir:
        raise Gate4LocalBridgeError("Gate 4 local bridge output_dir must differ from input_dir")
    if output_dir.is_relative_to(input_dir):
        raise Gate4LocalBridgeError("Gate 4 local bridge output_dir must not be inside input_dir")


def _write_json_artifact(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def _clear_bridge_artifacts(directory: Path, pattern: str) -> None:
    for path in directory.glob(pattern):
        if path.is_file() and not path.is_symlink():
            path.unlink()


__all__ = [
    "Gate4LocalBridgeAttachmentFlags",
    "Gate4LocalBridgeConfig",
    "Gate4LocalBridgeError",
    "Gate4LocalBridgeResult",
    "run_gate4_local_bridge",
]
