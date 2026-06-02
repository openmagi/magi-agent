from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    model_validator,
)

from openmagi_core_agent.shadow.gate3b_local_consumer import (
    validate_gate3b_local_consumer_path,
)
from openmagi_core_agent.shadow.gate3b_local_report import (
    Gate3BLocalComparisonReport,
)
from openmagi_core_agent.shadow.gate3b_metrics import Gate3BLocalMetricsSnapshot


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|"
    r"_API_KEY|_SERVICE_ROLE_KEY))"
    r"\s*=\s*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password|service[_-]?role[_-]?key)"
    r"\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)
_AUTHORITY_FLAG_KEYS = frozenset(
    {
        "adkRunnerInvoked",
        "liveRunnerAttached",
        "liveShadowExecuted",
        "modelCalled",
        "toolsExecuted",
        "shellOrCodeExecuted",
        "storageWritten",
        "queueEnqueued",
        "publicOutputAttached",
        "userVisibleOutputAttached",
        "productionRouteAttached",
        "productionTranscriptAttached",
        "productionSseAttached",
        "productionStorageAttached",
        "productionQueueAttached",
        "telegramAttached",
        "routeAttached",
        "apiAttached",
        "dbAttached",
        "deployAttached",
        "canaryAttached",
        "evidenceBlockEnabled",
        "childExecutionAttached",
        "missionSchedulerAttached",
        "memoryProviderCalled",
        "workspaceMutated",
    }
)


class Gate4LocalConsumerError(ValueError):
    pass


class Gate4LocalConsumerAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    live_shadow_executed: Literal[False] = Field(default=False, alias="liveShadowExecuted")
    tools_executed: Literal[False] = Field(default=False, alias="toolsExecuted")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    public_output_attached: Literal[False] = Field(
        default=False,
        alias="publicOutputAttached",
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
        "model_called",
        "live_shadow_executed",
        "tools_executed",
        "shell_or_code_executed",
        "user_visible_output_attached",
        "public_output_attached",
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


class Gate4LocalConsumerConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    input_dir: Path | None = Field(default=None, alias="inputDir")
    processed_bundle_ids: tuple[str, ...] = Field(default=(), alias="processedBundleIds")


Gate4LocalConsumerSkipReason = Literal[
    "invalid_json",
    "validation_failed",
    "duplicate_bundle_id",
    "not_a_file",
]


class Gate4LocalSkippedArtifact(BaseModel):
    model_config = _MODEL_CONFIG

    path: Path
    reason: Gate4LocalConsumerSkipReason
    message: str = ""


class Gate4LocalHandoff(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["gate4.localShadowHandoff.v1"] = Field(
        default="gate4.localShadowHandoff.v1",
        alias="schemaVersion",
    )
    bundle_id: str = Field(alias="bundleId")
    source_bundle_id: str = Field(alias="sourceBundleId")
    source_path: str = Field(alias="sourcePath")
    handoff_mode: Literal["gate4_runner_free_local_shadow_consumer"] = Field(
        default="gate4_runner_free_local_shadow_consumer",
        alias="handoffMode",
    )
    report_mode: Literal["local_diagnostic_metadata_only"] = Field(
        default="local_diagnostic_metadata_only",
        alias="reportMode",
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
    parity_status: str = Field(alias="parityStatus")
    redaction_verified: bool = Field(alias="redactionVerified")
    report_path: Path = Field(alias="reportPath")


class Gate4LocalConsumerResult(BaseModel):
    model_config = _MODEL_CONFIG

    consumer_mode: Literal["gate4_runner_free_local_shadow_consumer"] = Field(
        default="gate4_runner_free_local_shadow_consumer",
        alias="consumerMode",
    )
    handoffs: tuple[Gate4LocalHandoff, ...] = ()
    skipped: tuple[Gate4LocalSkippedArtifact, ...] = ()
    metrics: Gate3BLocalMetricsSnapshot | None = None
    local_diagnostic_artifact_count: int = Field(
        default=0,
        ge=0,
        alias="localDiagnosticArtifactCount",
    )
    attachment_flags: Gate4LocalConsumerAttachmentFlags = Field(
        default_factory=Gate4LocalConsumerAttachmentFlags,
        alias="attachmentFlags",
    )


def consume_gate4_local_bridge_outputs(
    config: Gate4LocalConsumerConfig,
) -> Gate4LocalConsumerResult:
    if not config.enabled:
        return Gate4LocalConsumerResult()
    if config.input_dir is None:
        raise Gate4LocalConsumerError("Gate 4 local consumer input_dir is required")

    input_dir = _validate_consumer_path(config.input_dir)
    reports_dir = input_dir / "reports"
    metrics_path = input_dir / "metrics" / "metrics-snapshot.json"
    if not reports_dir.is_dir():
        raise Gate4LocalConsumerError("Gate 4 local consumer reports directory is required")
    if not metrics_path.is_file():
        raise Gate4LocalConsumerError("Gate 4 local consumer metrics snapshot is required")

    metrics_payload = _read_json(metrics_path)
    _reject_truthy_authority_flags(
        metrics_payload,
        validation_title="Gate3BLocalMetricsSnapshot",
    )
    _reject_secret_text(metrics_payload)
    metrics = Gate3BLocalMetricsSnapshot.model_validate(metrics_payload)

    handoffs: list[Gate4LocalHandoff] = []
    skipped: list[Gate4LocalSkippedArtifact] = []
    seen_bundle_ids = set(config.processed_bundle_ids)

    for report_path in sorted(reports_dir.glob("report-*.json"), key=lambda item: item.name):
        if report_path.is_symlink() or not report_path.is_file():
            skipped.append(
                Gate4LocalSkippedArtifact(
                    path=report_path,
                    reason="not_a_file",
                    message="Gate 4 local consumer accepts bridge report JSON files only",
                )
            )
            continue
        try:
            payload = _read_json(report_path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            skipped.append(
                Gate4LocalSkippedArtifact(
                    path=report_path,
                    reason="invalid_json",
                    message="Gate 4 local bridge report JSON is malformed or partial",
                )
            )
            continue
        try:
            _reject_truthy_authority_flags(payload)
            _reject_secret_text(payload)
            report = Gate3BLocalComparisonReport.model_validate(payload)
        except Exception:
            skipped.append(
                Gate4LocalSkippedArtifact(
                    path=report_path,
                    reason="validation_failed",
                    message="Gate 4 local bridge report failed local diagnostic validation",
                )
            )
            continue
        if _report_has_redaction_failure(report):
            skipped.append(
                Gate4LocalSkippedArtifact(
                    path=report_path,
                    reason="validation_failed",
                    message="Gate 4 local bridge report failed redaction verification",
                )
            )
            continue
        if report.bundle_id in seen_bundle_ids:
            skipped.append(
                Gate4LocalSkippedArtifact(
                    path=report_path,
                    reason="duplicate_bundle_id",
                    message="Gate 4 local bridge report bundle ID was already consumed",
                )
            )
            continue
        seen_bundle_ids.add(report.bundle_id)
        handoffs.append(
            Gate4LocalHandoff(
                bundleId=report.bundle_id,
                sourceBundleId=report.source_bundle_id,
                sourcePath=report.source_path,
                generatedAt=report.generated_at,
                parityStatus=report.public_summary.status,
                redactionVerified=report.redaction.input_verified
                and report.redaction.output_verified,
                reportPath=report_path,
            )
        )

    return Gate4LocalConsumerResult(
        handoffs=tuple(handoffs),
        skipped=tuple(skipped),
        metrics=metrics,
        localDiagnosticArtifactCount=len(handoffs) + (1 if metrics is not None else 0),
        )


def _report_has_redaction_failure(report: Gate3BLocalComparisonReport) -> bool:
    return (
        report.public_summary.status == "redaction_violation"
        or not report.redaction.input_verified
        or not report.redaction.output_verified
        or bool(report.redaction.violations)
    )


def _validate_consumer_path(path: Path | str) -> Path:
    try:
        return validate_gate3b_local_consumer_path(path)
    except Exception as exc:
        raise Gate4LocalConsumerError("Gate 4 local consumer path is unsafe") from exc


def _read_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _reject_truthy_authority_flags(
    value: object,
    *,
    validation_title: str | None = None,
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _AUTHORITY_FLAG_KEYS and item is not False and item is not None:
                if validation_title is not None:
                    raise ValidationError.from_exception_data(
                        validation_title,
                        [
                            {
                                "type": "value_error",
                                "loc": (key,),
                                "msg": "Value error, Gate 4 local consumer received live authority flag",
                                "input": item,
                                "ctx": {
                                    "error": ValueError(
                                        "Gate 4 local consumer received live authority flag"
                                    )
                                },
                            }
                        ],
                    )
                raise Gate4LocalConsumerError("Gate 4 local consumer received live authority flag")
            _reject_truthy_authority_flags(item, validation_title=validation_title)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_truthy_authority_flags(item, validation_title=validation_title)


def _reject_secret_text(value: object) -> None:
    if isinstance(value, str):
        if _SECRET_TEXT_RE.search(value):
            raise Gate4LocalConsumerError("Gate 4 local consumer received unredacted text")
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_secret_text(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_secret_text(item)


__all__ = [
    "Gate4LocalConsumerAttachmentFlags",
    "Gate4LocalConsumerConfig",
    "Gate4LocalConsumerError",
    "Gate4LocalConsumerResult",
    "Gate4LocalHandoff",
    "Gate4LocalSkippedArtifact",
    "consume_gate4_local_bridge_outputs",
]
