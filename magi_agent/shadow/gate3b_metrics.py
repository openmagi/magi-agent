from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
import hashlib
import math
import re
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.shadow.gate3b_local_consumer import (
    Gate3BLocalConsumerResult,
    Gate3BLocalSkipReason,
)
from magi_agent.shadow.gate3b_local_report import (
    Gate3BLocalComparisonReport,
    Gate3BLocalComparisonStatus,
)


Gate3BMetricName: TypeAlias = Literal[
    "gate3b.capture.enabled",
    "gate3b.capture.accepted",
    "gate3b.capture.skipped",
    "gate3b.capture.dropped",
    "gate3b.capture.redaction_miss",
    "gate3b.capture.skip_reason",
    "gate3b.capture.drop_reason",
    "gate3b.ts.response_impact_ms",
    "gate3b.queue.depth",
    "gate3b.queue.enqueue_timeout",
    "gate3b.python.shadow.success",
    "gate3b.python.shadow.failure",
    "gate3b.parity.event",
    "gate3b.parity.transcript",
    "gate3b.parity.sse",
    "gate3b.tool_policy.violation",
    "gate3b.storage.bytes",
    "gate3b.consumer.files",
    "gate3b.consumer.bytes",
    "gate3b.consumer.schema_failure",
    "gate3b.consumer.duplicate_bundle_id",
    "gate3b.consumer.ordering",
    "gate3b.report.verdict",
    "gate3b.cost.shadow_model_usd",
]
Gate3BMetricSourceSlice: TypeAlias = Literal[
    "3b-1a",
    "3b-2a",
    "3b-2c",
    "3b-3a",
    "3b-3b",
    "3b-3c",
    "3b-4",
]
Gate3BMetricSourceRuntime: TypeAlias = Literal[
    "typescript-core-agent",
    "python-adk",
    "local-diagnostic",
]
Gate3BStopConditionCategory: TypeAlias = Literal[
    "none",
    "sanitizer_miss",
    "tool_policy_bypass",
    "queue_backpressure",
    "typescript_response_impact",
    "python_user_output_path",
    "production_state_mutation",
    "evidence_block",
]
Gate3BRedactionStatus: TypeAlias = Literal["verified"]
Gate3BMetricDimensionValue: TypeAlias = str | int | float | bool

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
    r"-----BEGIN\s+(?:OPENSSH\s+)?PRIVATE\s+KEY-----|"
    r"authorization\s*:\s*(?:bearer\s+)?\S+|"
    r"bearer\s+\S+|"
    r"cookie\s*:\s*\S+|"
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
_PRIVATE_TEXT_RE = re.compile(
    r"(?:hidden_reasoning|chain_of_thought|private_reasoning|reasoning_trace|"
    r"private_tool_preview|private_tool_input|private_tool_output|raw_tool_preview|"
    r"private_connector_token|connector_credentials|tool_arguments?|transcript|"
    r"shell\s+command\s+executed|code\s+runner\s+executed|live\s+tool\s+executed)",
    re.IGNORECASE,
)
_FORBIDDEN_TEXT_RE = re.compile(
    r"(?:"
    r"\b[a-z][a-z0-9+.-]*://\S+|"
    r"\bmagi\.pro\b\S*|"
    r"/(?:Users|data/bots|workspace|var/lib/kubelet|mnt|private|home/ocuser)\S*|"
    r"\S*\.kube\S*|"
    r"\binfra/k8s\b\S*|"
    r"\bkube\s+path\b|"
    r"\bdeploy\.sh\b|"
    r"\bruntime[-_ ]selector\b|"
    r"\btelegram\b|"
    r"\bapi\s+(?:route|proxy|attached)\b|"
    r"\bdashboard\s+(?:proxy|route|attached)\b|"
    r"\bproxy\s+(?:route|attached)\b|"
    r"\b(?:org|bot|user|session)[_-](?:prod|production|stage|staging|dev|test|live)"
    r"[_-][A-Za-z0-9_-]+|"
    r"\bbot-[A-Za-z0-9_-]+|"
    r"\bsession[-_ ]?key[-_A-Za-z0-9]*|"
    r"\braw[-_ ]?org[-_ ]?id\b|"
    r"\braw[-_ ]?bot[-_ ]?id\b|"
    r"\braw[-_ ]?session\b"
    r")",
    re.IGNORECASE,
)
_FORBIDDEN_DIMENSION_KEYS = frozenset(
    {
        "org",
        "org_id",
        "orgid",
        "organization",
        "organization_id",
        "organizationid",
        "raw_organization_id",
        "raworganizationid",
        "bot",
        "bot_id",
        "botid",
        "raw_bot_id",
        "rawbotid",
        "session",
        "session_key",
        "sessionkey",
        "session_id",
        "sessionid",
        "raw_session_id",
        "rawsessionid",
        "user",
        "user_id",
        "userid",
        "raw_user_id",
        "rawuserid",
        "user_text",
        "usertext",
        "tool_arguments",
        "toolarguments",
        "tool_args",
        "toolargs",
        "file_path",
        "filepath",
        "source_path",
        "sourcepath",
        "payload",
        "transcript",
        "bundle_payload",
        "bundlepayload",
    }
)
_FALSE_ATTACHMENT_FIELD_ALIASES = frozenset(
    {
        "adkRunnerInvoked",
        "liveRunnerAttached",
        "liveShadowExecuted",
        "toolsExecuted",
        "shellOrCodeExecuted",
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
    }
)


class _Gate3BLocalMetricsModel(BaseModel):
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


class Gate3BLocalMetricAttachmentFlags(_Gate3BLocalMetricsModel):
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_runner_attached: Literal[False] = Field(default=False, alias="liveRunnerAttached")
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
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    api_attached: Literal[False] = Field(default=False, alias="apiAttached")
    db_attached: Literal[False] = Field(default=False, alias="dbAttached")
    deploy_attached: Literal[False] = Field(default=False, alias="deployAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")
    evidence_block_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockEnabled",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{key: False for key in cls.model_fields})

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {field.alias or name: False for name, field in cls.model_fields.items()}

    @field_serializer(
        "adk_runner_invoked",
        "live_runner_attached",
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
        "route_attached",
        "api_attached",
        "db_attached",
        "deploy_attached",
        "canary_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate3BLocalMetricRecord(_Gate3BLocalMetricsModel):
    schema_version: Literal["gate3b.localMetricRecord.v1"] = Field(
        default="gate3b.localMetricRecord.v1",
        alias="schemaVersion",
    )
    metric_name: Gate3BMetricName = Field(alias="metricName")
    source_slice: Gate3BMetricSourceSlice = Field(alias="sourceSlice")
    source_runtime: Gate3BMetricSourceRuntime = Field(alias="sourceRuntime")
    value: float = Field(ge=0)
    capture_surface: str | None = Field(default=None, alias="captureSurface")
    bundle_id_digest: str | None = Field(default=None, alias="bundleIdDigest")
    recipe_snapshot_digest: str | None = Field(default=None, alias="recipeSnapshotDigest")
    redaction_status: Gate3BRedactionStatus = Field(alias="redactionStatus")
    categorical_status: str | None = Field(default=None, alias="categoricalStatus")
    stop_condition_category: Gate3BStopConditionCategory = Field(
        default="none",
        alias="stopConditionCategory",
    )
    dimension_values: dict[str, Gate3BMetricDimensionValue] = Field(
        default_factory=dict,
        alias="dimensionValues",
    )
    attachment_flags: Gate3BLocalMetricAttachmentFlags = Field(
        default_factory=Gate3BLocalMetricAttachmentFlags,
        alias="attachmentFlags",
    )

    @field_validator("value")
    @classmethod
    def _validate_finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Gate 3B metric value must be finite")
        return value

    @field_validator(
        "capture_surface",
        "categorical_status",
    )
    @classmethod
    def _validate_optional_safe_text(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_unsafe_metric_text(value)
        return value

    @field_validator("bundle_id_digest", "recipe_snapshot_digest")
    @classmethod
    def _validate_digest_fields(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_unsafe_metric_text(value)
            if not _is_metric_digest(value):
                raise ValueError("Gate 3B metric digest fields must contain digests")
        return value

    @field_validator("dimension_values")
    @classmethod
    def _validate_dimensions(
        cls,
        value: dict[str, Gate3BMetricDimensionValue],
    ) -> dict[str, Gate3BMetricDimensionValue]:
        sanitized: dict[str, Gate3BMetricDimensionValue] = {}
        for key, item in value.items():
            normalized_key = _normalize_key(key)
            if normalized_key in _FORBIDDEN_DIMENSION_KEYS:
                raise ValueError("Gate 3B metric dimension key is not public-safe")
            _reject_unsafe_metric_text(key)
            if isinstance(item, str):
                _reject_unsafe_metric_text(item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Gate 3B metric dimension number must be finite")
            sanitized[key] = item
        return sanitized

    @field_serializer("attachment_flags")
    def _serialize_false_attachment_flags(
        self,
        _value: Gate3BLocalMetricAttachmentFlags,
    ) -> dict[str, bool]:
        return Gate3BLocalMetricAttachmentFlags().model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )


class Gate3BLocalMetricsCounts(_Gate3BLocalMetricsModel):
    accepted: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    dropped: int = Field(default=0, ge=0)
    redaction_failures: int = Field(default=0, ge=0, alias="redactionFailures")
    schema_failures: int = Field(default=0, ge=0, alias="schemaFailures")
    duplicate_bundle_ids: int = Field(default=0, ge=0, alias="duplicateBundleIds")
    file_count: int = Field(default=0, ge=0, alias="fileCount")
    byte_count: int = Field(default=0, ge=0, alias="byteCount")
    report_count: int = Field(default=0, ge=0, alias="reportCount")


class Gate3BLocalSkipReasonCounts(_Gate3BLocalMetricsModel):
    file_limit_exceeded: int = Field(default=0, ge=0, alias="fileLimitExceeded")
    file_too_large: int = Field(default=0, ge=0, alias="fileTooLarge")
    total_bytes_exceeded: int = Field(default=0, ge=0, alias="totalBytesExceeded")
    invalid_json: int = Field(default=0, ge=0, alias="invalidJson")
    validation_failed: int = Field(default=0, ge=0, alias="validationFailed")
    duplicate_bundle_id: int = Field(default=0, ge=0, alias="duplicateBundleId")
    symlink_not_allowed: int = Field(default=0, ge=0, alias="symlinkNotAllowed")
    not_a_file: int = Field(default=0, ge=0, alias="notAFile")


class Gate3BLocalDropReasonCounts(_Gate3BLocalMetricsModel):
    redaction_failed: int = Field(default=0, ge=0, alias="redactionFailed")
    bundle_too_large: int = Field(default=0, ge=0, alias="bundleTooLarge")
    queue_full: int = Field(default=0, ge=0, alias="queueFull")
    sink_error: int = Field(default=0, ge=0, alias="sinkError")
    sink_timeout: int = Field(default=0, ge=0, alias="sinkTimeout")
    write_failed: int = Field(default=0, ge=0, alias="writeFailed")


class Gate3BLocalReportVerdictCounts(_Gate3BLocalMetricsModel):
    not_run: int = Field(default=0, ge=0, alias="notRun")
    schema_pass: int = Field(default=0, ge=0, alias="schemaPass")
    schema_mismatch: int = Field(default=0, ge=0, alias="schemaMismatch")
    redaction_violation: int = Field(default=0, ge=0, alias="redactionViolation")
    invalid_handoff: int = Field(default=0, ge=0, alias="invalidHandoff")
    not_applicable: int = Field(default=0, ge=0, alias="notApplicable")


class Gate3BLocalOrderingStats(_Gate3BLocalMetricsModel):
    observed_bundle_count: int = Field(default=0, ge=0, alias="observedBundleCount")
    deterministic_ordering: bool = Field(default=True, alias="deterministicOrdering")
    first_bundle_digest: str | None = Field(default=None, alias="firstBundleDigest")
    last_bundle_digest: str | None = Field(default=None, alias="lastBundleDigest")

    @field_validator("first_bundle_digest", "last_bundle_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_unsafe_metric_text(value)
            if not _is_metric_digest(value):
                raise ValueError("Gate 3B ordering digest fields must contain digests")
        return value


class Gate3BLocalMetricsSnapshot(_Gate3BLocalMetricsModel):
    schema_version: Literal["gate3b.localMetricsSnapshot.v1"] = Field(
        default="gate3b.localMetricsSnapshot.v1",
        alias="schemaVersion",
    )
    metrics_mode: Literal["local_diagnostic_metadata_only"] = Field(
        default="local_diagnostic_metadata_only",
        alias="metricsMode",
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
    source_runtime: Literal["local-diagnostic"] = Field(
        default="local-diagnostic",
        alias="sourceRuntime",
    )
    redaction_status: Gate3BRedactionStatus = Field(
        default="verified",
        alias="redactionStatus",
    )
    counts: Gate3BLocalMetricsCounts
    skip_reason_counts: Gate3BLocalSkipReasonCounts = Field(alias="skipReasonCounts")
    drop_reason_counts: Gate3BLocalDropReasonCounts = Field(alias="dropReasonCounts")
    report_verdict_counts: Gate3BLocalReportVerdictCounts = Field(alias="reportVerdictCounts")
    ordering: Gate3BLocalOrderingStats
    duplicate_bundle_id_digests: tuple[str, ...] = Field(
        default=(),
        alias="duplicateBundleIdDigests",
    )
    metric_records: tuple[Gate3BLocalMetricRecord, ...] = Field(
        default=(),
        alias="metricRecords",
    )
    attachment_flags: Gate3BLocalMetricAttachmentFlags = Field(
        default_factory=Gate3BLocalMetricAttachmentFlags,
        alias="attachmentFlags",
    )
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_runner_attached: Literal[False] = Field(default=False, alias="liveRunnerAttached")
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
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    api_attached: Literal[False] = Field(default=False, alias="apiAttached")
    db_attached: Literal[False] = Field(default=False, alias="dbAttached")
    deploy_attached: Literal[False] = Field(default=False, alias="deployAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

    @model_validator(mode="before")
    @classmethod
    def _force_false_attachment_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        alias_to_name = {
            field.alias: name
            for name, field in cls.model_fields.items()
            if field.alias is not None
        }
        for alias in _FALSE_ATTACHMENT_FIELD_ALIASES:
            data.pop(alias_to_name.get(alias, alias), None)
            data[alias] = False
        data["attachmentFlags"] = Gate3BLocalMetricAttachmentFlags()
        data.pop("attachment_flags", None)
        return data

    @field_validator("duplicate_bundle_id_digests")
    @classmethod
    def _validate_duplicate_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _reject_unsafe_metric_text(item)
            if not _is_metric_digest(item):
                raise ValueError("Gate 3B duplicate bundle IDs must be digests")
        return value

    @field_serializer(
        "adk_runner_invoked",
        "live_runner_attached",
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
        "route_attached",
        "api_attached",
        "db_attached",
        "deploy_attached",
        "canary_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @field_serializer("attachment_flags")
    def _serialize_false_attachment_flags(
        self,
        _value: Gate3BLocalMetricAttachmentFlags,
    ) -> dict[str, bool]:
        return Gate3BLocalMetricAttachmentFlags().model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )


def build_gate3b_local_metrics_snapshot(
    *,
    consumer_result: Gate3BLocalConsumerResult | Mapping[str, object],
    reports: Iterable[Gate3BLocalComparisonReport | Mapping[str, object]],
) -> Gate3BLocalMetricsSnapshot:
    consumer = (
        consumer_result
        if isinstance(consumer_result, Gate3BLocalConsumerResult)
        else Gate3BLocalConsumerResult.model_validate(consumer_result)
    )
    report_items = tuple(
        item
        if isinstance(item, Gate3BLocalComparisonReport)
        else Gate3BLocalComparisonReport.model_validate(item)
        for item in reports
    )

    skip_counter: Counter[str] = Counter(item.reason for item in consumer.skipped)
    verdict_counter: Counter[str] = Counter(item.public_summary.status for item in report_items)
    consumed_ids = tuple(item.bundle_id for item in consumer.consumed)
    file_count = len(consumer.consumed) + len(consumer.skipped)
    byte_count = sum(item.file_size_bytes for item in consumer.consumed)
    schema_failures = (
        skip_counter.get("invalid_json", 0)
        + skip_counter.get("validation_failed", 0)
        + verdict_counter.get("invalid_handoff", 0)
        + verdict_counter.get("schema_mismatch", 0)
    )
    redaction_failures = (
        skip_counter.get("validation_failed", 0)
        + verdict_counter.get("redaction_violation", 0)
    )
    ordered_ids = tuple(
        item.bundle_id
        for item in sorted(consumer.consumed, key=lambda item: (item.consumed_at, item.bundle_id))
    )
    observed_ids = tuple(item.bundle_id for item in consumer.consumed)

    counts = Gate3BLocalMetricsCounts(
        accepted=len(consumer.consumed),
        skipped=len(consumer.skipped),
        dropped=0,
        redactionFailures=redaction_failures,
        schemaFailures=schema_failures,
        duplicateBundleIds=skip_counter.get("duplicate_bundle_id", 0),
        fileCount=file_count,
        byteCount=byte_count,
        reportCount=len(report_items),
    )
    skip_reason_counts = Gate3BLocalSkipReasonCounts(
        **{
            _snake_to_camel(name): skip_counter.get(name, 0)
            for name in Gate3BLocalSkipReason.__args__
        }
    )
    report_verdict_counts = Gate3BLocalReportVerdictCounts(
        **{
            _snake_to_camel(name): verdict_counter.get(name, 0)
            for name in Gate3BLocalComparisonStatus.__args__
        }
    )
    ordering = Gate3BLocalOrderingStats(
        observedBundleCount=len(consumed_ids),
        deterministicOrdering=observed_ids == ordered_ids,
        firstBundleDigest=_digest_text(ordered_ids[0]) if ordered_ids else None,
        lastBundleDigest=_digest_text(ordered_ids[-1]) if ordered_ids else None,
    )
    records = _metric_records_for_snapshot(
        counts=counts,
        skip_reason_counts=skip_reason_counts,
        report_verdict_counts=report_verdict_counts,
        ordering=ordering,
    )
    return Gate3BLocalMetricsSnapshot(
        counts=counts,
        skipReasonCounts=skip_reason_counts,
        dropReasonCounts=Gate3BLocalDropReasonCounts(),
        reportVerdictCounts=report_verdict_counts,
        ordering=ordering,
        duplicateBundleIdDigests=(),
        metricRecords=records,
    )


def _metric_records_for_snapshot(
    *,
    counts: Gate3BLocalMetricsCounts,
    skip_reason_counts: Gate3BLocalSkipReasonCounts,
    report_verdict_counts: Gate3BLocalReportVerdictCounts,
    ordering: Gate3BLocalOrderingStats,
) -> tuple[Gate3BLocalMetricRecord, ...]:
    records = [
        _record("gate3b.capture.accepted", counts.accepted),
        _record("gate3b.capture.skipped", counts.skipped),
        _record("gate3b.capture.dropped", counts.dropped),
        _record("gate3b.consumer.files", counts.file_count),
        _record("gate3b.consumer.bytes", counts.byte_count),
        _record("gate3b.consumer.schema_failure", counts.schema_failures),
        _record("gate3b.consumer.duplicate_bundle_id", counts.duplicate_bundle_ids),
        _record(
            "gate3b.consumer.ordering",
            1 if ordering.deterministic_ordering else 0,
            categorical_status="deterministic"
            if ordering.deterministic_ordering
            else "non_deterministic",
        ),
    ]
    for reason, count in skip_reason_counts.model_dump(mode="python").items():
        if count:
            records.append(
                _record(
                    "gate3b.capture.skip_reason",
                    count,
                    categorical_status=reason,
                    dimension_values={"reason": reason},
                )
            )
    for verdict, count in report_verdict_counts.model_dump(mode="python").items():
        if count:
            records.append(
                _record(
                    "gate3b.report.verdict",
                    count,
                    categorical_status=verdict,
                    dimension_values={"verdict": verdict},
                )
            )
    return tuple(records)


def _record(
    metric_name: Gate3BMetricName,
    value: float,
    *,
    categorical_status: str | None = None,
    dimension_values: Mapping[str, Gate3BMetricDimensionValue] | None = None,
) -> Gate3BLocalMetricRecord:
    return Gate3BLocalMetricRecord(
        metricName=metric_name,
        sourceSlice="3b-4",
        sourceRuntime="local-diagnostic",
        value=value,
        redactionStatus="verified",
        categoricalStatus=categorical_status,
        dimensionValues=dict(dimension_values or {}),
    )


def _reject_unsafe_metric_text(value: str) -> None:
    if (
        _SECRET_TEXT_RE.search(value)
        or _PRIVATE_TEXT_RE.search(value)
        or _FORBIDDEN_TEXT_RE.search(value)
    ):
        raise ValueError("Gate 3B local metrics text must be public-safe and redacted")


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _is_metric_digest(value: str) -> bool:
    return re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


__all__ = [
    "Gate3BLocalDropReasonCounts",
    "Gate3BLocalMetricAttachmentFlags",
    "Gate3BLocalMetricRecord",
    "Gate3BLocalMetricsCounts",
    "Gate3BLocalMetricsSnapshot",
    "Gate3BLocalOrderingStats",
    "Gate3BLocalReportVerdictCounts",
    "Gate3BLocalSkipReasonCounts",
    "build_gate3b_local_metrics_snapshot",
]
