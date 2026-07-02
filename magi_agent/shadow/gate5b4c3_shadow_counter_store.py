from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
from functools import wraps
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


Gate5B4C3ShadowCounterReservationStatus = Literal[
    "reserved",
    "duplicate_replay",
    "blocked",
]
Gate5B4C3ShadowCounterBlockReason = Literal[
    "none",
    "daily_cap_exhausted",
    "daily_cost_cap_exhausted",
    "concurrency_cap_exhausted",
    "pending_cap_exhausted",
    "duplicate_in_flight",
]
Gate5B4C3ShadowCounterFinishStatus = Literal[
    "completed",
    "runner_completed",
    "served_to_client",
    "completed_after_client_timeout",
    "client_aborted",
    "fallback_served",
    "error",
    "dropped",
    "skipped",
]
Gate5B4C3ShadowDeliveryStatus = Literal[
    "served_to_client",
    "fallback_served",
    "client_aborted",
    "completed_after_client_timeout",
    "python_error",
    "timeout",
    "blocked",
    "harness_failed",
]
Gate5B4C3ShadowDeliveryReceiptStatus = Literal[
    "recorded",
    "duplicate",
    "conflict",
    "not_found",
]
Gate5B4C3ShadowDeliveryEvidenceStatus = Literal["passed", "failed"]
Gate5B4C3ShadowAttemptEvidenceSource = Literal[
    "python_counter_record",
    "chat_proxy_fallback_receipt",
    "missing_attempt_evidence",
]
Gate5B4C3ShadowEgressDisciplineMode = Literal[
    "strict_single_tunnel",
    "bounded_provider_tunnels",
]
Gate5B4C3ShadowEgressEvidenceStatus = Literal[
    "observed_egress_evidence_present",
    "missing_observed_egress_evidence",
]
Gate1ASelectedAttemptPreflightStatus = Literal["ready", "blocked"]
Gate1ASelectedAttemptPreflightReason = Literal[
    "fresh_attempt_ready",
    "budget_exhausted",
    "idempotency_collision",
    "counter_store_unwritable",
    "pending_inflight_inconsistent",
    "fallback_receipt_path_unavailable",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_STORE_SCHEMA_VERSION = "gate5b4c3.shadowCounterStore.v1"
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_EGRESS_HOST_CLASS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_RUNNER_ERROR_DIAGNOSTIC_SCHEMA_VERSION = "gate5b4c3.runnerErrorDiagnostic.v1"
_RUNNER_ERROR_DIAGNOSTIC_STRING_FIELDS = frozenset(
    {
        "schemaVersion",
        "stage",
        "reasonCode",
        "exceptionClass",
        "exceptionCategory",
        "routeMode",
        "gateMode",
        "toolsPolicy",
        "routingSource",
        "correlationMode",
        "runtimeVersion",
        "buildSha",
    }
)
_RUNNER_ERROR_DIAGNOSTIC_DIGEST_FIELDS = frozenset(
    {
        "requestDigest",
        "traceIdDigest",
        "modelAttemptDigest",
        "correlationDigest",
    }
)
_RUNNER_ERROR_DIAGNOSTIC_BOOL_FIELDS = frozenset(
    {
        "adkInvoked",
        "runnerAttempted",
        "modelCallAttempted",
        "toolsEnabled",
        "toolHostDispatchAllowed",
        "adkPrimitivesLoaderConfigured",
        "gate1aEgressCorrelationContextPresent",
        "gate1aProxyUrlConfigured",
        "egressCorrelationHeadersConfigured",
        "observedEgressEvidenceAvailable",
        "gate1aEgressEvidenceReady",
    }
)
_RUNNER_ERROR_DIAGNOSTIC_LIST_FIELDS = frozenset({"activeToolNames"})
_RUNNER_ERROR_DIAGNOSTIC_TRACEBACK_MARKER_FIELDS = frozenset({"tracebackMarkers"})
_RUNNER_ERROR_DIAGNOSTIC_PREVIEW_FIELDS = frozenset({"errorPreview"})
_CONTEXT_CONTINUITY_SCHEMA_VERSION = "pregate8.contextContinuityChatDiagnostic.v1"
_CONTEXT_CONTINUITY_LABEL_FIELDS = frozenset(
    {"schemaVersion", "source", "phase", "mode", "canaryStatus", "fallbackStatus"}
)
_CONTEXT_CONTINUITY_BOOL_FIELDS = frozenset(
    {
        "localOnly",
        "diagnosticOnly",
        "continuityEnabled",
        "continuityCanaryReady",
        "compactionApplied",
        "projectionDigestPresent",
        "modelVisibleDigestPresent",
        "sourceTranscriptHeadDigestPresent",
        "canaryEvidenceVerified",
        "productionAuthorityAllowed",
        "transcriptWriteAllowed",
        "sseWriteAllowed",
        "dbWriteAllowed",
        "clientMessagesTrustedForContinuity",
    }
)
_CONTEXT_CONTINUITY_INT_FIELDS = frozenset(
    {"importedEventCount", "rejectedEntryCount"}
)
_CONTEXT_CONTINUITY_LIST_FIELDS = frozenset({"reasonCodes"})
_RUNNER_ERROR_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE = re.compile(
    r"(?:"
    r"Authorization:|Bearer\s+\S+|(?:Cookie|Set-Cookie):|"
    r"sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|"
    r"\b(?:api[_-]?key|token|secret|password|session[_-]?key)\b|"
    r"\b(?:prompt|output|request[_-]?body|response[_-]?body)\s*[:=]\s*\S+|"
    r"/(?:Users|private|workspace|data/bots|var/lib/kubelet|mnt)\S*|"
    r"https?://\S+"
    r")",
    re.IGNORECASE,
)
_CONTEXT_REASON_CODE_FORBIDDEN_RE = re.compile(
    r"(?:"
    r"Authorization|Bearer|Cookie|Set-Cookie|"
    r"\b(?:api[_-]?key|token|secret|password|session[_-]?key|credential)\b|"
    r"private|"
    r"sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|"
    r"/(?:Users|private|workspace|data/bots|var/lib/kubelet|mnt)\S*|"
    r"https?://\S+|"
    r"^[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}$|"
    r"^[A-Za-z0-9_-]{32,}$"
    r")",
    re.IGNORECASE,
)
_DEFAULT_STALE_AFTER_MS = 120_000
#: Scopes whose counter_date is older than this many days (vs the mutating
#: call's now_ms) are pruned wholesale. Must stay comfortably larger than the
#: longest legitimate late-delivery-receipt / evidence-lookup lag (minutes in
#: practice); 7 days gives audit margin while bounding the file to
#: O(retention_days x daily records).
_DEFAULT_SCOPE_RETENTION_DAYS = 7
#: Per-scope safety net: terminal request records beyond this cap are evicted
#: oldest-first. The daily-cap ladder (max 1000 runs/day) plus fallback
#: receipts bounds organic growth well below this; the cap only bites on
#: pathological duplicates.
_DEFAULT_MAX_TERMINAL_RECORDS_PER_SCOPE = 4000
_DIRECTORY_STATE_FILENAME = "state.json"
_DIRECTORY_LOCK_FILENAME = ".lock"
_EGRESS_FAILURE_STATUSES = frozenset(
    {
        "egress_count_anomaly",
        "egress_policy_violation",
        "egress_without_model_attempt",
        "missing_observed_egress_evidence",
        "missing_egress_host_class",
        "missing_egress_policy",
        "missing_egress_tunnel_count",
    }
)
_TERMINAL_REQUEST_STATUSES = frozenset(
    {
        "completed",
        "runner_completed",
        "served_to_client",
        "completed_after_client_timeout",
        "client_aborted",
        "fallback_served",
        "error",
        "blocked",
        "dropped",
        "skipped",
        "stale_released",
    }
)
_DELIVERY_STATUS_RESERVED_RELEASE_STATUSES = frozenset(
    {
        "served_to_client",
        "fallback_served",
        "client_aborted",
        "completed_after_client_timeout",
        "python_error",
        "timeout",
        "blocked",
        "harness_failed",
    }
)
_DELIVERY_STATUS_TO_TERMINAL_REQUEST_STATUS = {
    "served_to_client": "served_to_client",
    "fallback_served": "fallback_served",
    "client_aborted": "client_aborted",
    "completed_after_client_timeout": "completed_after_client_timeout",
    "python_error": "error",
    "timeout": "error",
    "blocked": "blocked",
    "harness_failed": "error",
}
_RETRYABLE_TERMINAL_ERROR_REASONS = frozenset(
    {
        "runner_error",
        "runner_output_missing",
        "runner_timeout",
    }
)
_FALLBACK_RECEIPT_GATES = frozenset(
    {
        "gate1a_readonly_tools",
        "gate7_5_context_continuity",
    }
)
_F = TypeVar("_F", bound=Callable[..., object])


def _with_exclusive_lock(method: _F) -> _F:
    @wraps(method)
    def wrapper(self: "Gate5B4C3ShadowCounterStore", *args: object, **kwargs: object) -> object:
        with self._exclusive_lock():
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class _Gate5B4C3CounterModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)


class Gate5B4C3ShadowCounterState(_Gate5B4C3CounterModel):
    schema_version: Literal["gate5b4c3.shadowCounterState.v1"] = Field(
        default="gate5b4c3.shadowCounterState.v1",
        alias="schemaVersion",
    )
    selected_bot_digest: str = Field(alias="selectedBotDigest")
    trusted_owner_user_id_digest: str = Field(alias="trustedOwnerUserIdDigest")
    environment: str
    counter_date: str = Field(alias="counterDate")
    daily_generation_runs_used: int = Field(default=0, ge=0, alias="dailyGenerationRunsUsed")
    daily_generation_cost_usd_used: float = Field(
        default=0,
        ge=0,
        alias="dailyGenerationCostUsdUsed",
    )
    in_flight_generation_runs: int = Field(default=0, ge=0, alias="inFlightGenerationRuns")
    pending_generation_runs: int = Field(default=0, ge=0, alias="pendingGenerationRuns")
    stale_in_flight_released: int = Field(default=0, ge=0, alias="staleInFlightReleased")
    max_daily_generation_runs: int = Field(default=0, ge=0, alias="maxDailyGenerationRuns")
    max_daily_generation_cost_usd: float = Field(
        default=0,
        ge=0,
        alias="maxDailyGenerationCostUsd",
    )
    max_concurrent_generation_runs: int = Field(
        default=0,
        ge=0,
        alias="maxConcurrentGenerationRuns",
    )
    max_pending_generation_runs: int = Field(default=0, ge=0, alias="maxPendingGenerationRuns")

    @model_validator(mode="after")
    def _validate_counter_state(self) -> Self:
        _validate_digest(self.selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            self.trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(self.environment, "counter environment must be public-safe")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", self.counter_date):
            raise ValueError("counterDate must be a UTC date")
        return self


class Gate5B4C3ShadowCounterReservation(_Gate5B4C3CounterModel):
    schema_version: Literal["gate5b4c3.shadowCounterReservation.v1"] = Field(
        default="gate5b4c3.shadowCounterReservation.v1",
        alias="schemaVersion",
    )
    status: Gate5B4C3ShadowCounterReservationStatus
    reason: Gate5B4C3ShadowCounterBlockReason = "none"
    should_invoke_runner: bool = Field(alias="shouldInvokeRunner")
    request_digest: str = Field(alias="requestDigest")
    shadow_generation_id: str = Field(alias="shadowGenerationId")
    counter_state: Gate5B4C3ShadowCounterState = Field(alias="counterState")
    reserved_cost_usd: float = Field(default=0, ge=0, alias="reservedCostUsd")
    previous_report_digest: str | None = Field(default=None, alias="previousReportDigest")
    previous_comparison_artifact_digest: str | None = Field(
        default=None,
        alias="previousComparisonArtifactDigest",
    )

    @model_validator(mode="after")
    def _validate_reservation(self) -> Self:
        _validate_digest(self.request_digest, "request idempotency key must be a digest")
        _validate_safe_label(
            self.shadow_generation_id,
            "shadow generation id must be public-safe",
        )
        for digest in (self.previous_report_digest, self.previous_comparison_artifact_digest):
            if digest is not None:
                _validate_digest(digest, "previous report metadata must be a digest")
        if self.status == "reserved" and not self.should_invoke_runner:
            raise ValueError("reserved counters must invoke the runner")
        if self.status != "reserved" and self.should_invoke_runner:
            raise ValueError("non-reserved counters must not invoke the runner")
        return self


class Gate5B4C3ShadowDeliveryReceipt(_Gate5B4C3CounterModel):
    schema_version: Literal["gate5b4c3.shadowDeliveryReceipt.v1"] = Field(
        default="gate5b4c3.shadowDeliveryReceipt.v1",
        alias="schemaVersion",
    )
    status: Gate5B4C3ShadowDeliveryReceiptStatus
    request_digest: str = Field(alias="requestDigest")
    delivery_status: Gate5B4C3ShadowDeliveryStatus = Field(alias="deliveryStatus")
    delivery_receipt_count: int = Field(default=0, ge=0, alias="deliveryReceiptCount")
    delivery_duplicate_count: int = Field(default=0, ge=0, alias="deliveryDuplicateCount")
    delivery_conflict_count: int = Field(default=0, ge=0, alias="deliveryConflictCount")

    @model_validator(mode="after")
    def _validate_receipt(self) -> Self:
        _validate_digest(self.request_digest, "delivery receipt request digest must be a digest")
        return self


class Gate5B4C3ShadowDeliveryEvidence(_Gate5B4C3CounterModel):
    schema_version: Literal["gate5b4c3.shadowDeliveryEvidence.v1"] = Field(
        default="gate5b4c3.shadowDeliveryEvidence.v1",
        alias="schemaVersion",
    )
    status: Gate5B4C3ShadowDeliveryEvidenceStatus
    reason: str
    request_digest: str = Field(alias="requestDigest")
    attempt_evidence_source: Gate5B4C3ShadowAttemptEvidenceSource = Field(
        alias="attemptEvidenceSource",
    )
    delivery_status: str | None = Field(default=None, alias="deliveryStatus")
    delivery_evidence_status: str | None = Field(
        default=None,
        alias="deliveryEvidenceStatus",
    )
    tool_evidence_status: str | None = Field(default=None, alias="toolEvidenceStatus")
    delivery_receipt_count: int = Field(default=0, ge=0, alias="deliveryReceiptCount")
    delivery_duplicate_count: int = Field(default=0, ge=0, alias="deliveryDuplicateCount")
    delivery_conflict_count: int = Field(default=0, ge=0, alias="deliveryConflictCount")
    sse_frame_count: int = Field(default=0, ge=0, alias="sseFrameCount")
    tool_receipt_count: int = Field(default=0, ge=0, alias="toolReceiptCount")
    model_attempt_count: int = Field(default=0, ge=0, alias="modelAttemptCount")
    provider_request_count: int = Field(default=0, ge=0, alias="providerRequestCount")
    egress_connect_count: int | None = Field(default=None, ge=0, alias="egressConnectCount")
    egress_tunnel_count: int | None = Field(default=None, ge=0, alias="egressTunnelCount")
    egress_discipline_mode: Gate5B4C3ShadowEgressDisciplineMode | None = Field(
        default=None,
        alias="egressDisciplineMode",
    )
    egress_evidence_status: Gate5B4C3ShadowEgressEvidenceStatus | None = Field(
        default=None,
        alias="egressEvidenceStatus",
    )
    egress_evidence_source: str | None = Field(default=None, alias="egressEvidenceSource")
    egress_evidence_redaction_status: str | None = Field(
        default=None,
        alias="egressEvidenceRedactionStatus",
    )
    egress_evidence_decision_reason: str | None = Field(
        default=None,
        alias="egressEvidenceDecisionReason",
    )
    egress_discipline_reason: str | None = Field(
        default=None,
        alias="egressDisciplineReason",
    )
    model_attempt_digest: str | None = Field(default=None, alias="modelAttemptDigest")
    source_ledger_digest: str | None = Field(default=None, alias="sourceLedgerDigest")
    final_projection_digest: str | None = Field(
        default=None,
        alias="finalProjectionDigest",
    )
    research_evidence_status: str | None = Field(
        default=None,
        alias="researchEvidenceStatus",
    )
    citation_evidence_status: str | None = Field(
        default=None,
        alias="citationEvidenceStatus",
    )
    verifier_evidence_status: str | None = Field(
        default=None,
        alias="verifierEvidenceStatus",
    )
    final_projection_evidence_status: str | None = Field(
        default=None,
        alias="finalProjectionEvidenceStatus",
    )
    source_inspected_event_count: int = Field(
        default=0,
        ge=0,
        alias="sourceInspectedEventCount",
    )
    rule_check_event_count: int = Field(default=0, ge=0, alias="ruleCheckEventCount")
    unsupported_claim_omitted_count: int = Field(
        default=0,
        ge=0,
        alias="unsupportedClaimOmittedCount",
    )
    max_provider_tunnels_per_model_attempt: int | None = Field(
        default=None,
        ge=0,
        alias="maxProviderTunnelsPerModelAttempt",
    )
    runner_error_diagnostic: dict[str, object] | None = Field(
        default=None,
        alias="runnerErrorDiagnostic",
    )

    @model_validator(mode="before")
    @classmethod
    def _sanitize_runner_error_diagnostic_input(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "runnerErrorDiagnostic" in data:
            data["runnerErrorDiagnostic"] = _sanitize_runner_error_diagnostic(
                data.get("runnerErrorDiagnostic")
            )
        return data

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        _validate_digest(self.request_digest, "delivery evidence request digest must be a digest")
        _validate_safe_label(self.reason, "delivery evidence reason must be public-safe")
        if self.egress_discipline_reason is not None:
            _validate_safe_label(
                self.egress_discipline_reason,
                "egress discipline reason must be public-safe",
            )
        if self.model_attempt_digest is not None:
            _validate_digest(self.model_attempt_digest, "model attempt digest must be a digest")
        if self.source_ledger_digest is not None:
            _validate_digest(self.source_ledger_digest, "source ledger must be a digest")
        if self.final_projection_digest is not None:
            _validate_digest(
                self.final_projection_digest,
                "final projection must be a digest",
            )
        for label, message in (
            (self.research_evidence_status, "research evidence status must be public-safe"),
            (self.citation_evidence_status, "citation evidence status must be public-safe"),
            (self.verifier_evidence_status, "verifier evidence status must be public-safe"),
            (
                self.final_projection_evidence_status,
                "final projection evidence status must be public-safe",
            ),
        ):
            if label is not None:
                _validate_safe_label(label, message)
        if self.runner_error_diagnostic is not None:
            _sanitize_runner_error_diagnostic(self.runner_error_diagnostic)
        return self


class Gate1ASelectedAttemptPreflight(_Gate5B4C3CounterModel):
    schema_version: Literal["gate1a.selectedAttemptPreflight.v1"] = Field(
        default="gate1a.selectedAttemptPreflight.v1",
        alias="schemaVersion",
    )
    status: Gate1ASelectedAttemptPreflightStatus
    reason: Gate1ASelectedAttemptPreflightReason
    request_digest: str = Field(alias="requestDigest")
    counter_store_writable: bool = Field(alias="counterStoreWritable")
    fallback_receipt_path_available: bool = Field(alias="fallbackReceiptPathAvailable")
    selected_scope_budget_available: bool = Field(alias="selectedScopeBudgetAvailable")
    idempotency_collision: bool = Field(alias="idempotencyCollision")
    pending_inflight_consistent: bool = Field(alias="pendingInFlightConsistent")

    @model_validator(mode="after")
    def _validate_preflight(self) -> Self:
        _validate_digest(
            self.request_digest,
            "Gate 1A selected attempt preflight request digest must be a digest",
        )
        return self


class Gate5B4C3ShadowCounterStore:
    def __init__(
        self,
        path: str | Path,
        *,
        stale_after_ms: int = _DEFAULT_STALE_AFTER_MS,
        retention_days: int = _DEFAULT_SCOPE_RETENTION_DAYS,
        max_terminal_records_per_scope: int = _DEFAULT_MAX_TERMINAL_RECORDS_PER_SCOPE,
    ) -> None:
        configured_path = Path(path)
        self._directory_state_path = (
            configured_path.exists() and configured_path.is_dir()
        ) or not configured_path.suffix
        if self._directory_state_path:
            self.path = configured_path
        else:
            self.path = configured_path
        self.stale_after_ms = max(0, stale_after_ms)
        self.retention_days = int(retention_days)
        self.max_terminal_records_per_scope = int(max_terminal_records_per_scope)

    @_with_exclusive_lock
    def reserve(
        self,
        *,
        request_digest: str,
        shadow_generation_id: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        max_daily_generation_runs: int,
        max_daily_generation_cost_usd: float,
        max_concurrent_generation_runs: int,
        max_pending_generation_runs: int,
        cost_cap_usd: float,
        cost_owner_waiver: bool = False,
        now_ms: int | None = None,
    ) -> Gate5B4C3ShadowCounterReservation:
        now = _coerce_now_ms(now_ms)
        _validate_digest(request_digest, "request idempotency key must be a digest")
        _validate_digest(selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(environment, "counter environment must be public-safe")
        _validate_safe_label(shadow_generation_id, "shadow generation id must be public-safe")

        data = self._load()
        _prune_expired_scopes(data, now_ms=now, retention_days=self.retention_days)
        scopes = data.setdefault("scopes", {})
        scope_key = _scope_key(
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
            counter_date=_counter_date(now),
        )
        scope = scopes.setdefault(
            scope_key,
            {
                "state": _initial_state(
                    selected_bot_digest=selected_bot_digest,
                    trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                    environment=environment,
                    now_ms=now,
                    max_daily_generation_runs=max_daily_generation_runs,
                    max_daily_generation_cost_usd=max_daily_generation_cost_usd,
                    max_concurrent_generation_runs=max_concurrent_generation_runs,
                    max_pending_generation_runs=max_pending_generation_runs,
                ),
                "requests": {},
            },
        )
        state = _update_scope_limits(
            scope["state"],
            max_daily_generation_runs=max_daily_generation_runs,
            max_daily_generation_cost_usd=max_daily_generation_cost_usd,
            max_concurrent_generation_runs=max_concurrent_generation_runs,
            max_pending_generation_runs=max_pending_generation_runs,
        )
        requests = scope.setdefault("requests", {})
        _release_stale_in_flight(state, requests, now, self.stale_after_ms)
        _prune_scope_terminal_records(
            scope, cap=self.max_terminal_records_per_scope
        )

        existing = requests.get(request_digest)
        if isinstance(existing, dict):
            if (
                existing.get("status") in _TERMINAL_REQUEST_STATUSES
                and not _is_retryable_terminal_request_record(existing)
            ):
                reservation = Gate5B4C3ShadowCounterReservation(
                    status="duplicate_replay",
                    reason="none",
                    shouldInvokeRunner=False,
                    requestDigest=request_digest,
                    shadowGenerationId=shadow_generation_id,
                    counterState=Gate5B4C3ShadowCounterState.model_validate(state),
                    reservedCostUsd=0,
                    previousReportDigest=existing.get("reportDigest"),
                    previousComparisonArtifactDigest=existing.get(
                        "comparisonArtifactDigest"
                    ),
                )
                self._save(data)
                return reservation
            if existing.get("status") == "reserved":
                reservation = Gate5B4C3ShadowCounterReservation(
                    status="blocked",
                    reason="duplicate_in_flight",
                    shouldInvokeRunner=False,
                    requestDigest=request_digest,
                    shadowGenerationId=shadow_generation_id,
                    counterState=Gate5B4C3ShadowCounterState.model_validate(state),
                    reservedCostUsd=0,
                )
                self._save(data)
                return reservation

        reason = _cap_block_reason(
            state,
            max_daily_generation_runs=max_daily_generation_runs,
            max_daily_generation_cost_usd=max_daily_generation_cost_usd,
            max_concurrent_generation_runs=max_concurrent_generation_runs,
            max_pending_generation_runs=max_pending_generation_runs,
            reserved_cost_usd=cost_cap_usd,
            cost_owner_waiver=cost_owner_waiver,
        )
        if reason != "none":
            reservation = Gate5B4C3ShadowCounterReservation(
                status="blocked",
                reason=reason,
                shouldInvokeRunner=False,
                requestDigest=request_digest,
                shadowGenerationId=shadow_generation_id,
                counterState=Gate5B4C3ShadowCounterState.model_validate(state),
                reservedCostUsd=0,
            )
            self._save(data)
            return reservation

        state["dailyGenerationRunsUsed"] += 1
        state["dailyGenerationCostUsdUsed"] = round(
            float(state["dailyGenerationCostUsdUsed"]) + cost_cap_usd,
            8,
        )
        state["pendingGenerationRuns"] += 1
        state["inFlightGenerationRuns"] += 1
        requests[request_digest] = {
            "status": "reserved",
            "shadowGenerationId": shadow_generation_id,
            "reservedAtMs": now,
            "reservedCostUsd": cost_cap_usd,
        }
        reservation = Gate5B4C3ShadowCounterReservation(
            status="reserved",
            reason="none",
            shouldInvokeRunner=True,
            requestDigest=request_digest,
            shadowGenerationId=shadow_generation_id,
            counterState=Gate5B4C3ShadowCounterState.model_validate(state),
            reservedCostUsd=cost_cap_usd,
        )
        self._save(data)
        return reservation

    @_with_exclusive_lock
    def finish(
        self,
        reservation: Gate5B4C3ShadowCounterReservation,
        *,
        status: Gate5B4C3ShadowCounterFinishStatus,
        reason: str,
        report_digest: str | None = None,
        comparison_artifact_digest: str | None = None,
        runner_error_diagnostic: Mapping[str, object] | None = None,
        now_ms: int | None = None,
    ) -> Gate5B4C3ShadowCounterState:
        now = _coerce_now_ms(now_ms)
        if report_digest is not None:
            _validate_digest(report_digest, "report digest must be a digest")
        if comparison_artifact_digest is not None:
            _validate_digest(
                comparison_artifact_digest,
                "comparison artifact digest must be a digest",
            )
        safe_runner_error_diagnostic = _sanitize_runner_error_diagnostic(
            runner_error_diagnostic
        )
        data = self._load()
        _prune_expired_scopes(data, now_ms=now, retention_days=self.retention_days)
        scopes = data.setdefault("scopes", {})
        scope_key = _scope_key(
            selected_bot_digest=reservation.counter_state.selected_bot_digest,
            trusted_owner_user_id_digest=(
                reservation.counter_state.trusted_owner_user_id_digest
            ),
            environment=reservation.counter_state.environment,
            counter_date=reservation.counter_state.counter_date,
        )
        scope = scopes.setdefault(
            scope_key,
            {
                "state": reservation.counter_state.model_dump(
                    by_alias=True,
                    mode="json",
                ),
                "requests": {},
            },
        )
        state = scope["state"]
        requests = scope.setdefault("requests", {})
        record = requests.setdefault(
            reservation.request_digest,
            {
                "status": "reserved",
                "shadowGenerationId": reservation.shadow_generation_id,
                "reservedAtMs": now,
                "reservedCostUsd": reservation.reserved_cost_usd,
            },
        )
        if record.get("status") == "reserved" and state.get("inFlightGenerationRuns", 0) > 0:
            state["inFlightGenerationRuns"] -= 1
        if record.get("status") == "reserved" and state.get("pendingGenerationRuns", 0) > 0:
            state["pendingGenerationRuns"] -= 1
        record.update(
            {
                "status": status,
                "reason": _safe_reason(reason),
                "finishedAtMs": now,
                "reportDigest": report_digest,
                "comparisonArtifactDigest": comparison_artifact_digest,
            }
        )
        if safe_runner_error_diagnostic is not None:
            record["runnerErrorDiagnostic"] = safe_runner_error_diagnostic
        self._save(data)
        return Gate5B4C3ShadowCounterState.model_validate(state)

    @_with_exclusive_lock
    def record_gate2_sandbox_canary_evidence(
        self,
        *,
        request_digest: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        status: str,
        reason: str,
        workspace_mutation_receipt_digest: str,
        rollback_receipt_digest: str | None,
        sandbox_path_digest: str,
        now_ms: int | None = None,
    ) -> None:
        now = _coerce_now_ms(now_ms)
        _validate_digest(request_digest, "Gate 2 request digest must be a digest")
        _validate_digest(selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(environment, "counter environment must be public-safe")
        _validate_safe_label(status, "Gate 2 canary status must be public-safe")
        _validate_digest(
            workspace_mutation_receipt_digest,
            "Gate 2 mutation receipt must be a digest",
        )
        if rollback_receipt_digest is not None:
            _validate_digest(rollback_receipt_digest, "Gate 2 rollback must be a digest")
        _validate_digest(sandbox_path_digest, "Gate 2 sandbox path must be a digest")

        data = self._load()
        scopes = data.setdefault("scopes", {})
        scope_key = _scope_key(
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
            counter_date=_counter_date(now),
        )
        scope = scopes.setdefault(
            scope_key,
            {
                "state": _initial_state(
                    selected_bot_digest=selected_bot_digest,
                    trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                    environment=environment,
                    now_ms=now,
                    max_daily_generation_runs=0,
                    max_daily_generation_cost_usd=0,
                    max_concurrent_generation_runs=0,
                    max_pending_generation_runs=0,
                ),
                "requests": {},
            },
        )
        scope.setdefault("requests", {})[request_digest] = {
            "status": status,
            "reason": _safe_reason(reason),
            "attemptEvidenceSource": "python_counter_record",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": True,
            "gate": "gate2_sandbox_workspace_canary",
            "createdAtMs": now,
            "finishedAtMs": now,
            "reservedCostUsd": 0,
            "workspaceMutationReceiptDigest": workspace_mutation_receipt_digest,
            "rollbackReceiptDigest": rollback_receipt_digest,
            "sandboxPathDigest": sandbox_path_digest,
        }
        self._save(data)

    @_with_exclusive_lock
    def record_gate8_research_first_canary_evidence(
        self,
        *,
        request_digest: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        source_ledger_digest: str,
        output_digest: str,
        now_ms: int | None = None,
    ) -> Gate5B4C3ShadowCounterState:
        now = _coerce_now_ms(now_ms)
        _validate_digest(request_digest, "Gate 8 request digest must be a digest")
        _validate_digest(selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(environment, "counter environment must be public-safe")
        _validate_digest(source_ledger_digest, "Gate 8 source ledger must be a digest")
        _validate_digest(output_digest, "Gate 8 output must be a digest")

        data = self._load()
        scopes = data.setdefault("scopes", {})
        scope_key = _scope_key(
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
            counter_date=_counter_date(now),
        )
        scope = scopes.setdefault(
            scope_key,
            {
                "state": _initial_state(
                    selected_bot_digest=selected_bot_digest,
                    trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                    environment=environment,
                    now_ms=now,
                    max_daily_generation_runs=0,
                    max_daily_generation_cost_usd=0,
                    max_concurrent_generation_runs=0,
                    max_pending_generation_runs=0,
                ),
                "requests": {},
            },
        )
        state = scope.setdefault(
            "state",
            _initial_state(
                selected_bot_digest=selected_bot_digest,
                trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                environment=environment,
                now_ms=now,
                max_daily_generation_runs=0,
                max_daily_generation_cost_usd=0,
                max_concurrent_generation_runs=0,
                max_pending_generation_runs=0,
            ),
        )
        scope.setdefault("requests", {})[request_digest] = {
            "status": "research_first_selected_readonly_completed",
            "reason": "research_first_projection_passed",
            "attemptEvidenceSource": "python_counter_record",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": True,
            "gate": "gate8_selected_python_authority",
            "createdAtMs": now,
            "finishedAtMs": now,
            "reservedCostUsd": 0,
            "sourceLedgerDigest": source_ledger_digest,
            "outputDigest": output_digest,
            "toolReceiptCount": 0,
            "modelAttemptCount": 0,
            "providerRequestCount": 0,
        }
        self._save(data)
        return Gate5B4C3ShadowCounterState.model_validate(state)

    @_with_exclusive_lock
    def gate8_research_first_delivery_receipt_error(
        self,
        *,
        request_digest: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        delivery_status: str,
        gate: str | None,
        route_decision: str | None,
        response_authority: str | None,
        output_digest: str | None,
        source_ledger_digest: str | None,
        final_projection_digest: str | None,
        research_evidence_status: str | None,
        citation_evidence_status: str | None,
        verifier_evidence_status: str | None,
        final_projection_evidence_status: str | None,
        source_inspected_event_count: int,
        rule_check_event_count: int,
    ) -> str | None:
        _validate_digest(request_digest, "Gate 8 request digest must be a digest")
        _validate_digest(selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(environment, "counter environment must be public-safe")
        data = self._load()
        scope = _find_request_scope(
            data.setdefault("scopes", {}),
            request_digest=request_digest,
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
        )
        if scope is None:
            return None
        record = scope.setdefault("requests", {}).get(request_digest)
        if not isinstance(record, dict):
            return None
        if record.get("status") != "research_first_selected_readonly_completed":
            return None
        if delivery_status != "served_to_client":
            return None
        if gate != "gate8_selected_python_authority":
            return "research_first_evidence_mismatch"
        if route_decision != "python_selected" or response_authority != "python":
            return "research_first_evidence_mismatch"
        if source_ledger_digest is None or final_projection_digest is None:
            return "research_first_evidence_missing"
        _validate_digest(source_ledger_digest, "source ledger must be a digest")
        _validate_digest(final_projection_digest, "final projection must be a digest")
        if output_digest is None:
            return "research_first_evidence_missing"
        _validate_digest(output_digest, "output must be a digest")
        if source_ledger_digest != record.get("sourceLedgerDigest"):
            return "research_first_evidence_mismatch"
        if final_projection_digest != record.get("outputDigest"):
            return "research_first_evidence_mismatch"
        if output_digest != record.get("outputDigest"):
            return "research_first_evidence_mismatch"
        expected_statuses = (
            research_evidence_status,
            citation_evidence_status,
            verifier_evidence_status,
            final_projection_evidence_status,
        )
        if any(status != "passed" for status in expected_statuses):
            return "research_first_evidence_missing"
        if _nonnegative_int(source_inspected_event_count) < 1:
            return "research_first_evidence_missing"
        if _nonnegative_int(rule_check_event_count) < 3:
            return "research_first_evidence_missing"
        return None

    @_with_exclusive_lock
    def gate2_sandbox_canary_evidence_error(
        self,
        *,
        request_digest: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        workspace_mutation_receipt_digest: str,
        rollback_receipt_digest: str,
        sandbox_path_digest: str,
    ) -> str | None:
        for digest, message in (
            (request_digest, "Gate 2 request digest must be a digest"),
            (selected_bot_digest, "selected bot counter scope must be a digest"),
            (trusted_owner_user_id_digest, "owner counter scope must be a digest"),
            (workspace_mutation_receipt_digest, "Gate 2 mutation receipt must be a digest"),
            (rollback_receipt_digest, "Gate 2 rollback must be a digest"),
            (sandbox_path_digest, "Gate 2 sandbox path must be a digest"),
        ):
            _validate_digest(digest, message)
        _validate_safe_label(environment, "counter environment must be public-safe")

        data = self._load()
        scope = _find_request_scope(
            data.setdefault("scopes", {}),
            request_digest=request_digest,
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
        )
        if scope is None:
            return "python_counter_record_required"
        record = scope.setdefault("requests", {}).get(request_digest)
        if not isinstance(record, dict):
            return "python_counter_record_required"
        if record.get("attemptEvidenceSource") != "python_counter_record":
            return "python_counter_record_required"
        if record.get("gate") != "gate2_sandbox_workspace_canary":
            return "gate2_evidence_mismatch"
        if record.get("status") not in {
            "gate2_sandbox_workspace_canary_completed",
            "gate2_selected_sandbox_canary_completed",
        }:
            return "gate2_evidence_not_completed"
        if record.get("workspaceMutationReceiptDigest") != workspace_mutation_receipt_digest:
            return "gate2_evidence_mismatch"
        if record.get("rollbackReceiptDigest") != rollback_receipt_digest:
            return "gate2_evidence_mismatch"
        if record.get("sandboxPathDigest") != sandbox_path_digest:
            return "gate2_evidence_mismatch"
        return None

    @_with_exclusive_lock
    def record_delivery_receipt(
        self,
        *,
        request_digest: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        delivery_status: Gate5B4C3ShadowDeliveryStatus,
        reason: str,
        body_digest: str | None = None,
        route_decision: str | None = None,
        response_authority: str | None = None,
        gate: str | None = None,
        served_at: str | None = None,
        completed_at: str | None = None,
        fallback_reason: str | None = None,
        sse_frame_count: int = 0,
        tool_receipt_count: int = 0,
        model_attempt_count: int = 0,
        provider_request_count: int = 0,
        expected_model_attempt_count: int | None = None,
        egress_connect_count: int | None = None,
        egress_tunnel_count: int | None = None,
        egress_discipline_mode: Gate5B4C3ShadowEgressDisciplineMode | None = None,
        egress_evidence_status: Gate5B4C3ShadowEgressEvidenceStatus | None = None,
        egress_evidence_source: str | None = None,
        egress_evidence_redaction_status: str | None = None,
        egress_evidence_decision_reason: str | None = None,
        model_attempt_digest: str | None = None,
        max_provider_tunnels_per_model_attempt: int | None = None,
        egress_host_classes: tuple[str, ...] = (),
        egress_correlation_digest: str | None = None,
        egress_window_started_at: str | None = None,
        egress_window_ended_at: str | None = None,
        egress_outside_gate_window: bool = False,
        output_digest: str | None = None,
        workspace_mutation_receipt_digest: str | None = None,
        rollback_receipt_digest: str | None = None,
        sandbox_path_digest: str | None = None,
        source_ledger_digest: str | None = None,
        final_projection_digest: str | None = None,
        research_evidence_status: str | None = None,
        citation_evidence_status: str | None = None,
        verifier_evidence_status: str | None = None,
        final_projection_evidence_status: str | None = None,
        source_inspected_event_count: int = 0,
        rule_check_event_count: int = 0,
        unsupported_claim_omitted_count: int = 0,
        python_attempted: bool = False,
        python_counter_record_present: bool = False,
        context_continuity: Mapping[str, object] | None = None,
        now_ms: int | None = None,
    ) -> Gate5B4C3ShadowDeliveryReceipt:
        now = _coerce_now_ms(now_ms)
        _validate_digest(request_digest, "delivery receipt request digest must be a digest")
        _validate_digest(selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(environment, "counter environment must be public-safe")
        if delivery_status not in {
            "served_to_client",
            "fallback_served",
            "client_aborted",
            "completed_after_client_timeout",
            "python_error",
            "timeout",
            "blocked",
            "harness_failed",
        }:
            raise ValueError("delivery status is not approved")
        if body_digest is not None:
            _validate_digest(body_digest, "delivery body digest must be a digest")
        if output_digest is not None:
            _validate_digest(output_digest, "delivery output digest must be a digest")
        for digest, message in (
            (
                workspace_mutation_receipt_digest,
                "workspace mutation receipt digest must be a digest",
            ),
            (rollback_receipt_digest, "rollback receipt digest must be a digest"),
            (sandbox_path_digest, "sandbox path digest must be a digest"),
            (source_ledger_digest, "source ledger digest must be a digest"),
            (final_projection_digest, "final projection digest must be a digest"),
        ):
            if digest is not None:
                _validate_digest(digest, message)
        for label, message in (
            (route_decision, "delivery route decision must be public-safe"),
            (response_authority, "delivery response authority must be public-safe"),
            (gate, "delivery gate must be public-safe"),
            (fallback_reason, "delivery fallback reason must be public-safe"),
            (research_evidence_status, "research evidence status must be public-safe"),
            (citation_evidence_status, "citation evidence status must be public-safe"),
            (verifier_evidence_status, "verifier evidence status must be public-safe"),
            (
                final_projection_evidence_status,
                "final projection evidence status must be public-safe",
            ),
        ):
            if label is not None:
                _validate_safe_label(label, message)
        for timestamp in (served_at, completed_at):
            if timestamp is not None and not _safe_timestamp(timestamp):
                raise ValueError("delivery timestamp must be public-safe")
        sse_frame_count = _nonnegative_int(sse_frame_count)
        tool_receipt_count = _nonnegative_int(tool_receipt_count)
        model_attempt_count = _nonnegative_int(model_attempt_count)
        provider_request_count = _nonnegative_int(provider_request_count)
        source_inspected_event_count = _nonnegative_int(source_inspected_event_count)
        rule_check_event_count = _nonnegative_int(rule_check_event_count)
        unsupported_claim_omitted_count = _nonnegative_int(unsupported_claim_omitted_count)
        if expected_model_attempt_count is None:
            expected_model_attempt_count = 1 if model_attempt_count > 0 else 0
        expected_model_attempt_count = _nonnegative_int(expected_model_attempt_count)
        if egress_connect_count is not None:
            egress_connect_count = _nonnegative_int(egress_connect_count)
        if egress_tunnel_count is None:
            egress_tunnel_count = egress_connect_count
        elif egress_connect_count is None:
            egress_connect_count = egress_tunnel_count
        if egress_tunnel_count is not None:
            egress_tunnel_count = _nonnegative_int(egress_tunnel_count)
        if egress_connect_count is not None:
            egress_connect_count = _nonnegative_int(egress_connect_count)
        if max_provider_tunnels_per_model_attempt is not None:
            max_provider_tunnels_per_model_attempt = _nonnegative_int(
                max_provider_tunnels_per_model_attempt
            )
        if egress_discipline_mode is not None and egress_discipline_mode not in {
            "strict_single_tunnel",
            "bounded_provider_tunnels",
        }:
            raise ValueError("egress discipline mode is not approved")
        if egress_evidence_status is not None and egress_evidence_status not in {
            "observed_egress_evidence_present",
            "missing_observed_egress_evidence",
        }:
            raise ValueError("egress evidence status is not approved")
        for label, message in (
            (egress_evidence_source, "egress evidence source must be public-safe"),
            (
                egress_evidence_redaction_status,
                "egress evidence redaction status must be public-safe",
            ),
            (
                egress_evidence_decision_reason,
                "egress evidence decision reason must be public-safe",
            ),
        ):
            if label is not None:
                _validate_safe_label(label, message)
        if model_attempt_digest is not None:
            _validate_digest(model_attempt_digest, "model attempt digest must be a digest")
        if egress_correlation_digest is not None:
            _validate_digest(egress_correlation_digest, "egress correlation must be a digest")
        for timestamp in (egress_window_started_at, egress_window_ended_at):
            if timestamp is not None and not _safe_timestamp(timestamp):
                raise ValueError("egress window timestamp must be public-safe")
        egress_host_classes = tuple(egress_host_classes)
        for host_class in egress_host_classes:
            _validate_egress_host_class(host_class)

        data = self._load()
        target_scope = _find_request_scope(
            data.setdefault("scopes", {}),
            request_digest=request_digest,
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
        )
        if target_scope is None:
            target_scope = _fallback_receipt_scope(
                data.setdefault("scopes", {}),
                request_digest=request_digest,
                selected_bot_digest=selected_bot_digest,
                trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                environment=environment,
                delivery_status=delivery_status,
                gate=gate,
                reason=fallback_reason or reason,
                python_attempted=python_attempted,
                python_counter_record_present=python_counter_record_present,
                now_ms=now,
            )
            if target_scope is None:
                return Gate5B4C3ShadowDeliveryReceipt(
                    status="not_found",
                    requestDigest=request_digest,
                    deliveryStatus=delivery_status,
                )

        scope = target_scope
        state = scope.get("state")
        if not isinstance(state, dict):
            state = _initial_state(
                selected_bot_digest=selected_bot_digest,
                trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                environment=environment,
                now_ms=now,
                max_daily_generation_runs=0,
                max_daily_generation_cost_usd=0,
                max_concurrent_generation_runs=0,
                max_pending_generation_runs=0,
            )
            scope["state"] = state
        requests = scope.setdefault("requests", {})
        record = requests[request_digest]
        if "attemptEvidenceSource" not in record:
            record["attemptEvidenceSource"] = "python_counter_record"
        if (
            record.get("status") == "reserved"
            and delivery_status in _DELIVERY_STATUS_RESERVED_RELEASE_STATUSES
        ):
            if int(state.get("inFlightGenerationRuns") or 0) > 0:
                state["inFlightGenerationRuns"] = int(state["inFlightGenerationRuns"]) - 1
            if int(state.get("pendingGenerationRuns") or 0) > 0:
                state["pendingGenerationRuns"] = int(state["pendingGenerationRuns"]) - 1
            state.pop("reservedCostUsd", None)
            terminal_status = _DELIVERY_STATUS_TO_TERMINAL_REQUEST_STATUS[delivery_status]
            if (
                delivery_status == "blocked"
                and _safe_reason(reason) in _RETRYABLE_TERMINAL_ERROR_REASONS
            ):
                terminal_status = "error"
            record["status"] = terminal_status
            record["reason"] = _safe_reason(reason)
        existing_delivery_status = record.get("deliveryStatus")
        receipt_count = int(record.get("deliveryReceiptCount") or 0) + 1
        duplicate_count = int(record.get("deliveryDuplicateCount") or 0)
        conflict_count = int(record.get("deliveryConflictCount") or 0)
        receipt_status: Gate5B4C3ShadowDeliveryReceiptStatus = "recorded"
        if existing_delivery_status == delivery_status:
            duplicate_count += 1
            receipt_status = "duplicate"
        elif existing_delivery_status:
            conflict_count += 1
            receipt_status = "conflict"
            record["deliveryLatestStatus"] = delivery_status
            record["deliveryConflict"] = True
        else:
            record["deliveryStatus"] = delivery_status

        egress_decision = _egress_discipline_decision(
            model_attempt_count=model_attempt_count,
            provider_request_count=provider_request_count,
            expected_model_attempt_count=expected_model_attempt_count,
            egress_tunnel_count=egress_tunnel_count,
            egress_discipline_mode=egress_discipline_mode,
            egress_evidence_status=egress_evidence_status,
            max_provider_tunnels_per_model_attempt=max_provider_tunnels_per_model_attempt,
            egress_host_classes=egress_host_classes,
            egress_outside_gate_window=egress_outside_gate_window,
        )
        delivery_evidence_status = _delivery_evidence_status(
            delivery_status=delivery_status,
            gate=gate,
            tool_receipt_count=tool_receipt_count,
            model_attempt_count=model_attempt_count,
            expected_model_attempt_count=expected_model_attempt_count,
            egress_decision=egress_decision,
        )
        tool_evidence_status = _tool_evidence_status(
            delivery_status=delivery_status,
            gate=gate,
            tool_receipt_count=tool_receipt_count,
        )
        record.update(
            {
                "deliveryReason": _safe_reason(reason),
                "deliveryRecordedAtMs": now,
                "deliveryReceiptCount": receipt_count,
                "deliveryDuplicateCount": duplicate_count,
                "deliveryConflictCount": conflict_count,
                "sseFrameCount": sse_frame_count,
                "toolReceiptCount": tool_receipt_count,
                "modelAttemptCount": model_attempt_count,
                "providerRequestCount": provider_request_count,
                "expectedModelAttemptCount": expected_model_attempt_count,
                "deliveryEvidenceStatus": delivery_evidence_status,
                "toolEvidenceStatus": tool_evidence_status,
                "egressDisciplineReason": egress_decision["reason"],
                "pythonAttempted": bool(python_attempted),
                "pythonCounterRecordPresent": bool(python_counter_record_present),
            }
        )
        if body_digest is not None:
            record["bodyDigest"] = body_digest
        if output_digest is not None:
            record["outputDigest"] = output_digest
        if workspace_mutation_receipt_digest is not None:
            record["workspaceMutationReceiptDigest"] = workspace_mutation_receipt_digest
        if rollback_receipt_digest is not None:
            record["rollbackReceiptDigest"] = rollback_receipt_digest
        if sandbox_path_digest is not None:
            record["sandboxPathDigest"] = sandbox_path_digest
        if source_ledger_digest is not None:
            record["sourceLedgerDigest"] = source_ledger_digest
        if final_projection_digest is not None:
            record["finalProjectionDigest"] = final_projection_digest
        if research_evidence_status is not None:
            record["researchEvidenceStatus"] = research_evidence_status
        if citation_evidence_status is not None:
            record["citationEvidenceStatus"] = citation_evidence_status
        if verifier_evidence_status is not None:
            record["verifierEvidenceStatus"] = verifier_evidence_status
        if final_projection_evidence_status is not None:
            record["finalProjectionEvidenceStatus"] = final_projection_evidence_status
        if source_inspected_event_count:
            record["sourceInspectedEventCount"] = source_inspected_event_count
        if rule_check_event_count:
            record["ruleCheckEventCount"] = rule_check_event_count
        if unsupported_claim_omitted_count:
            record["unsupportedClaimOmittedCount"] = unsupported_claim_omitted_count
        if route_decision is not None:
            record["routeDecision"] = route_decision
        if response_authority is not None:
            record["responseAuthority"] = response_authority
        if gate is not None:
            record["gate"] = gate
        if served_at is not None:
            record["servedAt"] = served_at
        if completed_at is not None:
            record["completedAt"] = completed_at
        if fallback_reason is not None:
            record["fallbackReason"] = fallback_reason
        if egress_connect_count is not None:
            record["egressConnectCount"] = egress_connect_count
        if egress_tunnel_count is not None:
            record["egressTunnelCount"] = egress_tunnel_count
        if egress_discipline_mode is not None:
            record["egressDisciplineMode"] = egress_discipline_mode
        if egress_evidence_status is not None:
            record["egressEvidenceStatus"] = egress_evidence_status
        if egress_evidence_source is not None:
            record["egressEvidenceSource"] = egress_evidence_source
        if egress_evidence_redaction_status is not None:
            record["egressEvidenceRedactionStatus"] = egress_evidence_redaction_status
        if egress_evidence_decision_reason is not None:
            record["egressEvidenceDecisionReason"] = egress_evidence_decision_reason
        if model_attempt_digest is not None:
            record["modelAttemptDigest"] = model_attempt_digest
        if max_provider_tunnels_per_model_attempt is not None:
            record["maxProviderTunnelsPerModelAttempt"] = (
                max_provider_tunnels_per_model_attempt
            )
        if egress_discipline_mode is not None or egress_tunnel_count is not None:
            record["expectedEgressTunnelRange"] = {
                "min": egress_decision["expected_min"],
                "max": egress_decision["expected_max"],
            }
        if egress_host_classes:
            record["egressHostClasses"] = list(egress_host_classes)
        if egress_correlation_digest is not None:
            record["egressCorrelationDigest"] = egress_correlation_digest
        if egress_window_started_at is not None:
            record["egressWindowStartedAt"] = egress_window_started_at
        if egress_window_ended_at is not None:
            record["egressWindowEndedAt"] = egress_window_ended_at
        if egress_outside_gate_window:
            record["egressOutsideGateWindow"] = True
        safe_context_continuity = _sanitize_context_continuity_diagnostic(
            context_continuity
        )
        if safe_context_continuity is not None:
            record["contextContinuity"] = safe_context_continuity
        self._save(data)
        return Gate5B4C3ShadowDeliveryReceipt(
            status=receipt_status,
            requestDigest=request_digest,
            deliveryStatus=delivery_status,
            deliveryReceiptCount=receipt_count,
            deliveryDuplicateCount=duplicate_count,
            deliveryConflictCount=conflict_count,
        )

    @_with_exclusive_lock
    def preflight_gate1a_selected_attempt(
        self,
        *,
        request_digest: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        max_daily_generation_runs: int,
        max_daily_generation_cost_usd: float,
        max_concurrent_generation_runs: int,
        max_pending_generation_runs: int,
        cost_cap_usd: float,
        fallback_receipt_path_available: bool,
        now_ms: int | None = None,
    ) -> Gate1ASelectedAttemptPreflight:
        now = _coerce_now_ms(now_ms)
        _validate_digest(request_digest, "request idempotency key must be a digest")
        _validate_digest(selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(environment, "counter environment must be public-safe")

        if not fallback_receipt_path_available:
            return _gate1a_selected_attempt_preflight(
                status="blocked",
                reason="fallback_receipt_path_unavailable",
                request_digest=request_digest,
                counter_store_writable=True,
                fallback_receipt_path_available=False,
                selected_scope_budget_available=False,
                idempotency_collision=False,
                pending_inflight_consistent=True,
            )

        try:
            self._assert_counter_store_writable()
            data = self._load()
        except Exception:
            return _gate1a_selected_attempt_preflight(
                status="blocked",
                reason="counter_store_unwritable",
                request_digest=request_digest,
                counter_store_writable=False,
                fallback_receipt_path_available=True,
                selected_scope_budget_available=False,
                idempotency_collision=False,
                pending_inflight_consistent=True,
            )

        scopes = data.setdefault("scopes", {})
        scope_key = _scope_key(
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
            counter_date=_counter_date(now),
        )
        scope = scopes.get(scope_key)
        if isinstance(scope, dict):
            state = _update_scope_limits(
                scope.setdefault(
                    "state",
                    _initial_state(
                        selected_bot_digest=selected_bot_digest,
                        trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                        environment=environment,
                        now_ms=now,
                        max_daily_generation_runs=max_daily_generation_runs,
                        max_daily_generation_cost_usd=max_daily_generation_cost_usd,
                        max_concurrent_generation_runs=max_concurrent_generation_runs,
                        max_pending_generation_runs=max_pending_generation_runs,
                    ),
                ),
                max_daily_generation_runs=max_daily_generation_runs,
                max_daily_generation_cost_usd=max_daily_generation_cost_usd,
                max_concurrent_generation_runs=max_concurrent_generation_runs,
                max_pending_generation_runs=max_pending_generation_runs,
            )
            requests = scope.setdefault("requests", {})
        else:
            state = _initial_state(
                selected_bot_digest=selected_bot_digest,
                trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                environment=environment,
                now_ms=now,
                max_daily_generation_runs=max_daily_generation_runs,
                max_daily_generation_cost_usd=max_daily_generation_cost_usd,
                max_concurrent_generation_runs=max_concurrent_generation_runs,
                max_pending_generation_runs=max_pending_generation_runs,
            )
            requests = {}

        _release_stale_in_flight(state, requests, now, self.stale_after_ms)
        existing = requests.get(request_digest)
        if isinstance(existing, dict):
            return _gate1a_selected_attempt_preflight(
                status="blocked",
                reason="idempotency_collision",
                request_digest=request_digest,
                counter_store_writable=True,
                fallback_receipt_path_available=True,
                selected_scope_budget_available=True,
                idempotency_collision=True,
                pending_inflight_consistent=_pending_inflight_consistent(state, requests),
            )

        if not _pending_inflight_consistent(state, requests):
            return _gate1a_selected_attempt_preflight(
                status="blocked",
                reason="pending_inflight_inconsistent",
                request_digest=request_digest,
                counter_store_writable=True,
                fallback_receipt_path_available=True,
                selected_scope_budget_available=True,
                idempotency_collision=False,
                pending_inflight_consistent=False,
            )

        cap_reason = _cap_block_reason(
            state,
            max_daily_generation_runs=max_daily_generation_runs,
            max_daily_generation_cost_usd=max_daily_generation_cost_usd,
            max_concurrent_generation_runs=max_concurrent_generation_runs,
            max_pending_generation_runs=max_pending_generation_runs,
            reserved_cost_usd=cost_cap_usd,
        )
        if cap_reason != "none":
            return _gate1a_selected_attempt_preflight(
                status="blocked",
                reason="budget_exhausted",
                request_digest=request_digest,
                counter_store_writable=True,
                fallback_receipt_path_available=True,
                selected_scope_budget_available=False,
                idempotency_collision=False,
                pending_inflight_consistent=True,
            )

        return _gate1a_selected_attempt_preflight(
            status="ready",
            reason="fresh_attempt_ready",
            request_digest=request_digest,
            counter_store_writable=True,
            fallback_receipt_path_available=True,
            selected_scope_budget_available=True,
            idempotency_collision=False,
            pending_inflight_consistent=True,
        )

    @_with_exclusive_lock
    def validate_delivery_evidence(
        self,
        *,
        request_digest: str,
        selected_bot_digest: str,
        trusted_owner_user_id_digest: str,
        environment: str,
        gate: str,
    ) -> Gate5B4C3ShadowDeliveryEvidence:
        _validate_digest(request_digest, "delivery evidence request digest must be a digest")
        _validate_digest(selected_bot_digest, "selected bot counter scope must be a digest")
        _validate_digest(
            trusted_owner_user_id_digest,
            "owner counter scope must be a digest",
        )
        _validate_safe_label(environment, "counter environment must be public-safe")
        _validate_safe_label(gate, "delivery evidence gate must be public-safe")
        data = self._load()
        scope = _find_request_scope(
            data.setdefault("scopes", {}),
            request_digest=request_digest,
            selected_bot_digest=selected_bot_digest,
            trusted_owner_user_id_digest=trusted_owner_user_id_digest,
            environment=environment,
        )
        if scope is None:
            return _delivery_evidence(
                status="failed",
                reason=(
                    "missing_attempt_evidence"
                    if gate == "gate1a_readonly_tools"
                    else "request_not_found"
                ),
                request_digest=request_digest,
            )
        record = scope.setdefault("requests", {}).get(request_digest)
        if not isinstance(record, dict):
            return _delivery_evidence(
                status="failed",
                reason=(
                    "missing_attempt_evidence"
                    if gate == "gate1a_readonly_tools"
                    else "request_not_found"
                ),
                request_digest=request_digest,
            )
        if record.get("status") == "runner_completed" and not record.get("deliveryStatus"):
            return _delivery_evidence(
                status="failed",
                reason="missing_delivery_receipt",
                request_digest=request_digest,
                record=record,
            )
        if int(record.get("deliveryConflictCount") or 0) > 0:
            return _delivery_evidence(
                status="failed",
                reason="delivery_conflict",
                request_digest=request_digest,
                record=record,
            )
        if record.get("deliveryEvidenceStatus") in _EGRESS_FAILURE_STATUSES:
            return _delivery_evidence(
                status="failed",
                reason=str(record.get("deliveryEvidenceStatus")),
                request_digest=request_digest,
                record=record,
            )
        if (
            gate == "gate1a_readonly_tools"
            and record.get("deliveryStatus") == "served_to_client"
            and int(record.get("toolReceiptCount") or 0) <= 0
        ):
            return _delivery_evidence(
                status="failed",
                reason="no_tool_invocation",
                request_digest=request_digest,
                record=record,
            )
        if record.get("status") == "research_first_selected_readonly_completed":
            if (
                record.get("deliveryStatus") != "served_to_client"
                or record.get("responseAuthority") != "python"
                or record.get("routeDecision") != "python_selected"
                or int(record.get("deliveryReceiptCount") or 0) <= 0
            ):
                return _delivery_evidence(
                    status="failed",
                    reason="research_first_delivery_not_served",
                    request_digest=request_digest,
                    record=record,
                )
            for field in (
                "sourceLedgerDigest",
                "finalProjectionDigest",
                "outputDigest",
            ):
                if not isinstance(record.get(field), str) or not _DIGEST_RE.match(
                    str(record.get(field))
                ):
                    return _delivery_evidence(
                        status="failed",
                        reason="research_first_evidence_missing",
                        request_digest=request_digest,
                        record=record,
                    )
            expected_source_ledger = record.get("sourceLedgerDigest")
            expected_final_projection = record.get("outputDigest")
            if (
                record.get("finalProjectionDigest") != expected_final_projection
                or record.get("sourceLedgerDigest") != expected_source_ledger
            ):
                return _delivery_evidence(
                    status="failed",
                    reason="research_first_evidence_mismatch",
                    request_digest=request_digest,
                    record=record,
                )
            for field in (
                "researchEvidenceStatus",
                "citationEvidenceStatus",
                "verifierEvidenceStatus",
                "finalProjectionEvidenceStatus",
            ):
                if record.get(field) != "passed":
                    return _delivery_evidence(
                        status="failed",
                        reason="research_first_evidence_missing",
                        request_digest=request_digest,
                        record=record,
                    )
            if int(record.get("sourceInspectedEventCount") or 0) < 1:
                return _delivery_evidence(
                    status="failed",
                    reason="research_first_evidence_missing",
                    request_digest=request_digest,
                    record=record,
                )
            if int(record.get("ruleCheckEventCount") or 0) < 3:
                return _delivery_evidence(
                    status="failed",
                    reason="research_first_evidence_missing",
                    request_digest=request_digest,
                    record=record,
                )
        return _delivery_evidence(
            status="passed",
            reason="delivery_evidence_ok",
            request_digest=request_digest,
            record=record,
        )

    def _load(self) -> dict[str, Any]:
        state_path = self._state_path()
        if not state_path.exists():
            return {"schemaVersion": _STORE_SCHEMA_VERSION, "scopes": {}}
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schemaVersion") != _STORE_SCHEMA_VERSION:
            raise ValueError("Gate 5B shadow counter store has an unsupported schema")
        raw.setdefault("scopes", {})
        return raw

    def _save(self, data: dict[str, Any]) -> None:
        state_path = self._state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_name(f".{state_path.name}.tmp")
        tmp.write_text(
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, state_path)

    def _assert_counter_store_writable(self) -> None:
        state_path = self._state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        probe = state_path.with_name(f".{state_path.name}.preflight-{os.getpid()}")
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _state_path(self) -> Path:
        if self._uses_directory_state_path():
            return self.path / _DIRECTORY_STATE_FILENAME
        return self.path

    def _lock_path(self) -> Path:
        if self._uses_directory_state_path():
            return self.path / _DIRECTORY_LOCK_FILENAME
        return self.path.with_name(f".{self.path.name}.lock")

    def _uses_directory_state_path(self) -> bool:
        return self._directory_state_path


def _initial_state(
    *,
    selected_bot_digest: str,
    trusted_owner_user_id_digest: str,
    environment: str,
    now_ms: int,
    max_daily_generation_runs: int,
    max_daily_generation_cost_usd: float,
    max_concurrent_generation_runs: int,
    max_pending_generation_runs: int,
) -> dict[str, object]:
    return {
        "schemaVersion": "gate5b4c3.shadowCounterState.v1",
        "selectedBotDigest": selected_bot_digest,
        "trustedOwnerUserIdDigest": trusted_owner_user_id_digest,
        "environment": environment,
        "counterDate": _counter_date(now_ms),
        "dailyGenerationRunsUsed": 0,
        "dailyGenerationCostUsdUsed": 0,
        "inFlightGenerationRuns": 0,
        "pendingGenerationRuns": 0,
        "staleInFlightReleased": 0,
        "maxDailyGenerationRuns": max_daily_generation_runs,
        "maxDailyGenerationCostUsd": max_daily_generation_cost_usd,
        "maxConcurrentGenerationRuns": max_concurrent_generation_runs,
        "maxPendingGenerationRuns": max_pending_generation_runs,
    }


def _fallback_receipt_scope(
    scopes: dict[str, Any],
    *,
    request_digest: str,
    selected_bot_digest: str,
    trusted_owner_user_id_digest: str,
    environment: str,
    delivery_status: str,
    gate: str | None,
    reason: str,
    python_attempted: bool,
    python_counter_record_present: bool,
    now_ms: int,
) -> dict[str, Any] | None:
    if (
        gate not in _FALLBACK_RECEIPT_GATES
        or delivery_status != "fallback_served"
        or not python_attempted
        or python_counter_record_present
    ):
        return None
    scope_key = _scope_key(
        selected_bot_digest=selected_bot_digest,
        trusted_owner_user_id_digest=trusted_owner_user_id_digest,
        environment=environment,
        counter_date=_counter_date(now_ms),
    )
    scope = scopes.setdefault(
        scope_key,
        {
            "state": _initial_state(
                selected_bot_digest=selected_bot_digest,
                trusted_owner_user_id_digest=trusted_owner_user_id_digest,
                environment=environment,
                now_ms=now_ms,
                max_daily_generation_runs=0,
                max_daily_generation_cost_usd=0,
                max_concurrent_generation_runs=0,
                max_pending_generation_runs=0,
            ),
            "requests": {},
        },
    )
    requests = scope.setdefault("requests", {})
    requests[request_digest] = {
        "status": "fallback_served",
        "reason": _safe_reason(reason),
        "attemptEvidenceSource": "chat_proxy_fallback_receipt",
        "pythonAttempted": True,
        "pythonCounterRecordPresent": False,
        "createdAtMs": now_ms,
        "finishedAtMs": now_ms,
        "reservedCostUsd": 0,
    }
    return scope


def _update_scope_limits(
    state: dict[str, Any],
    *,
    max_daily_generation_runs: int,
    max_daily_generation_cost_usd: float,
    max_concurrent_generation_runs: int,
    max_pending_generation_runs: int,
) -> dict[str, Any]:
    state.update(
        {
            "maxDailyGenerationRuns": max_daily_generation_runs,
            "maxDailyGenerationCostUsd": max_daily_generation_cost_usd,
            "maxConcurrentGenerationRuns": max_concurrent_generation_runs,
            "maxPendingGenerationRuns": max_pending_generation_runs,
        }
    )
    return state


def _pending_inflight_consistent(
    state: Mapping[str, Any],
    requests: Mapping[str, Any],
) -> bool:
    reserved_count = 0
    for record in requests.values():
        if isinstance(record, Mapping) and record.get("status") == "reserved":
            reserved_count += 1
    return (
        int(state.get("pendingGenerationRuns") or 0) == reserved_count
        and int(state.get("inFlightGenerationRuns") or 0) == reserved_count
    )


def _release_stale_in_flight(
    state: dict[str, Any],
    requests: dict[str, Any],
    now_ms: int,
    stale_after_ms: int,
) -> None:
    if stale_after_ms <= 0:
        return
    released = 0
    for record in requests.values():
        if not isinstance(record, dict) or record.get("status") != "reserved":
            continue
        reserved_at = int(record.get("reservedAtMs") or 0)
        if now_ms - reserved_at < stale_after_ms:
            continue
        record["status"] = "stale_released"
        record["reason"] = "stale_in_flight_released"
        record["finishedAtMs"] = now_ms
        released += 1
    if released:
        state["inFlightGenerationRuns"] = max(
            0,
            int(state.get("inFlightGenerationRuns") or 0) - released,
        )
        state["pendingGenerationRuns"] = max(
            0,
            int(state.get("pendingGenerationRuns") or 0) - released,
        )
        state["staleInFlightReleased"] = (
            int(state.get("staleInFlightReleased") or 0) + released
        )


def _prune_expired_scopes(
    data: dict[str, Any], *, now_ms: int, retention_days: int
) -> int:
    """Delete scopes whose counter_date is older than the retention window.

    Deleting an expired scope wholesale is safe because:
      1. reserve()'s duplicate-replay lookup is scoped to TODAY's scope only, so
         removing a scope more than retention_days old cannot change admission
         semantics. If that lookup ever becomes cross-day, retention_days would
         become the duplicate-replay defense window - keep this coupling in mind.
      2. any "reserved" record inside an expired scope has already blown past
         stale_after_ms (2 minutes) by orders of magnitude; it is an orphan and
         is unrelated to today's live counters.
      3. late delivery receipts / evidence resolved through _find_request_scope
         still behave exactly as before WITHIN the retention window; only
         receipts arriving later than retention_days degrade to not_found.
    """
    if retention_days <= 0:
        return 0
    scopes = data.get("scopes")
    if not isinstance(scopes, dict):
        return 0
    cutoff_date = _counter_date(now_ms - retention_days * 86_400_000)
    removed = 0
    for scope_key in list(scopes):
        if not isinstance(scope_key, str):
            continue
        parts = scope_key.split("|")
        if len(parts) != 4:
            continue  # fail-safe: never delete an unparseable scope
        if parts[0] < cutoff_date:  # ISO dates compare lexicographically
            del scopes[scope_key]
            removed += 1
    return removed


def _prune_scope_terminal_records(scope: dict[str, Any], *, cap: int) -> int:
    """Evict oldest terminal request records beyond ``cap`` from one scope.

    Never evicts an in-flight ("reserved") record - only records that already
    reached a terminal status are eligible, oldest finishedAtMs first.
    """
    if cap <= 0:
        return 0
    requests = scope.get("requests")
    if not isinstance(requests, dict) or len(requests) <= cap:
        return 0
    # Never evict in-flight ("reserved") records; evict oldest terminal first.
    terminal = [
        (digest, record)
        for digest, record in requests.items()
        if isinstance(record, dict) and record.get("status") != "reserved"
    ]
    excess = len(requests) - cap
    terminal.sort(
        key=lambda item: int(
            item[1].get("finishedAtMs") or item[1].get("reservedAtMs") or 0
        )
    )
    removed = 0
    for digest, _record in terminal[:excess]:
        del requests[digest]
        removed += 1
    return removed


def _cap_block_reason(
    state: dict[str, Any],
    *,
    max_daily_generation_runs: int,
    max_daily_generation_cost_usd: float,
    max_concurrent_generation_runs: int,
    max_pending_generation_runs: int,
    reserved_cost_usd: float,
    cost_owner_waiver: bool = False,
) -> Gate5B4C3ShadowCounterBlockReason:
    if int(state.get("dailyGenerationRunsUsed") or 0) >= max_daily_generation_runs:
        return "daily_cap_exhausted"
    if (
        not cost_owner_waiver
        and
        float(state.get("dailyGenerationCostUsdUsed") or 0) + reserved_cost_usd
        > max_daily_generation_cost_usd
    ):
        return "daily_cost_cap_exhausted"
    if int(state.get("inFlightGenerationRuns") or 0) >= max_concurrent_generation_runs:
        return "concurrency_cap_exhausted"
    if int(state.get("pendingGenerationRuns") or 0) >= max_pending_generation_runs:
        return "pending_cap_exhausted"
    return "none"


def _delivery_evidence_status(
    *,
    delivery_status: str,
    gate: str | None,
    tool_receipt_count: int,
    model_attempt_count: int,
    expected_model_attempt_count: int,
    egress_decision: dict[str, object],
) -> str:
    egress_status = str(egress_decision["status"])
    if egress_status != "delivery_evidence_ok":
        return egress_status
    if (
        gate == "gate1a_readonly_tools"
        and delivery_status == "served_to_client"
        and tool_receipt_count <= 0
    ):
        return "no_tool_invocation"
    return "delivery_evidence_ok"


def _egress_discipline_decision(
    *,
    model_attempt_count: int,
    provider_request_count: int,
    expected_model_attempt_count: int,
    egress_tunnel_count: int | None,
    egress_discipline_mode: Gate5B4C3ShadowEgressDisciplineMode | None,
    egress_evidence_status: Gate5B4C3ShadowEgressEvidenceStatus | None,
    max_provider_tunnels_per_model_attempt: int | None,
    egress_host_classes: tuple[str, ...],
    egress_outside_gate_window: bool,
) -> dict[str, object]:
    """CONNECT tunnels are transport events, not logical model attempts.

    Gemini clients may open more than one HTTPS proxy tunnel for a single logical
    generation. Keep model attempts and provider egress tunnels accounted
    separately, then enforce the configured tunnel policy.
    """

    expected_attempts = max(model_attempt_count, expected_model_attempt_count)
    if egress_tunnel_count is not None and egress_tunnel_count > 0 and expected_attempts <= 0:
        return _egress_decision(
            "egress_without_model_attempt",
            "egress_without_model_attempt",
            expected_min=0,
            expected_max=0,
        )
    if (
        expected_attempts > 0
        and egress_evidence_status != "observed_egress_evidence_present"
    ):
        return _egress_decision(
            "missing_observed_egress_evidence",
            "missing_observed_egress_evidence",
            expected_min=0,
            expected_max=0,
        )
    if expected_attempts > 0 and egress_discipline_mode is None:
        return _egress_decision(
            "missing_egress_policy",
            "missing_egress_policy",
            expected_min=0,
            expected_max=0,
        )
    if expected_attempts > 0 and egress_tunnel_count is None:
        return _egress_decision(
            "missing_observed_egress_evidence",
            "missing_observed_egress_evidence",
            expected_min=0,
            expected_max=0,
        )
    if egress_outside_gate_window:
        return _egress_decision(
            "egress_policy_violation",
            "egress_outside_gate_window",
            expected_min=0,
            expected_max=0,
        )
    if egress_tunnel_count is not None and egress_tunnel_count > 0 and not egress_host_classes:
        return _egress_decision(
            "missing_observed_egress_evidence",
            "missing_observed_egress_evidence",
            expected_min=0,
            expected_max=0,
        )
    if any(host_class != "gemini_proxy" for host_class in egress_host_classes):
        return _egress_decision(
            "egress_policy_violation",
            "unexpected_egress_host_class",
            expected_min=0,
            expected_max=0,
        )
    if egress_discipline_mode is None:
        return _egress_decision(
            "delivery_evidence_ok",
            "egress_not_required",
            expected_min=0,
            expected_max=0,
        )

    effective_attempts = max(expected_attempts, provider_request_count, 1)
    if egress_discipline_mode == "strict_single_tunnel":
        expected_max = expected_attempts
        if (egress_tunnel_count or 0) > expected_max:
            return _egress_decision(
                "egress_count_anomaly",
                "strict_single_tunnel_exceeded",
                expected_min=0,
                expected_max=expected_max,
            )
        return _egress_decision(
            "delivery_evidence_ok",
            "strict_single_tunnel_ok",
            expected_min=0,
            expected_max=expected_max,
        )

    per_attempt_max = max(1, int(max_provider_tunnels_per_model_attempt or 0))
    expected_max = per_attempt_max * effective_attempts
    if (egress_tunnel_count or 0) > expected_max:
        return _egress_decision(
            "egress_count_anomaly",
            "egress_tunnel_count_exceeded",
            expected_min=0,
            expected_max=expected_max,
        )
    return _egress_decision(
        "delivery_evidence_ok",
        "bounded_provider_tunnels_ok",
        expected_min=0,
        expected_max=expected_max,
    )


def _egress_decision(
    status: str,
    reason: str,
    *,
    expected_min: int,
    expected_max: int,
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "expected_min": max(0, expected_min),
        "expected_max": max(0, expected_max),
    }


def _tool_evidence_status(
    *,
    delivery_status: str,
    gate: str | None,
    tool_receipt_count: int,
) -> str:
    if gate != "gate1a_readonly_tools" or delivery_status != "served_to_client":
        return "not_required"
    if tool_receipt_count <= 0:
        return "no_tool_invocation"
    return "tool_receipts_present"


def _delivery_evidence(
    *,
    status: Gate5B4C3ShadowDeliveryEvidenceStatus,
    reason: str,
    request_digest: str,
    record: dict[str, Any] | None = None,
) -> Gate5B4C3ShadowDeliveryEvidence:
    safe_record = record or {}
    attempt_evidence_source = safe_record.get("attemptEvidenceSource")
    if attempt_evidence_source not in {
        "python_counter_record",
        "chat_proxy_fallback_receipt",
        "missing_attempt_evidence",
    }:
        attempt_evidence_source = (
            "python_counter_record" if safe_record else "missing_attempt_evidence"
        )
    return Gate5B4C3ShadowDeliveryEvidence(
        status=status,
        reason=_safe_reason(reason),
        requestDigest=request_digest,
        attemptEvidenceSource=attempt_evidence_source,
        deliveryStatus=safe_record.get("deliveryStatus"),
        deliveryEvidenceStatus=safe_record.get("deliveryEvidenceStatus"),
        toolEvidenceStatus=safe_record.get("toolEvidenceStatus"),
        deliveryReceiptCount=int(safe_record.get("deliveryReceiptCount") or 0),
        deliveryDuplicateCount=int(safe_record.get("deliveryDuplicateCount") or 0),
        deliveryConflictCount=int(safe_record.get("deliveryConflictCount") or 0),
        sseFrameCount=int(safe_record.get("sseFrameCount") or 0),
        toolReceiptCount=int(safe_record.get("toolReceiptCount") or 0),
        modelAttemptCount=int(safe_record.get("modelAttemptCount") or 0),
        providerRequestCount=int(safe_record.get("providerRequestCount") or 0),
        egressConnectCount=(
            int(safe_record["egressConnectCount"])
            if safe_record.get("egressConnectCount") is not None
            else None
        ),
        egressTunnelCount=(
            int(safe_record["egressTunnelCount"])
            if safe_record.get("egressTunnelCount") is not None
            else None
        ),
        egressDisciplineMode=safe_record.get("egressDisciplineMode"),
        egressEvidenceStatus=safe_record.get("egressEvidenceStatus"),
        egressEvidenceSource=safe_record.get("egressEvidenceSource"),
        egressEvidenceRedactionStatus=safe_record.get("egressEvidenceRedactionStatus"),
        egressEvidenceDecisionReason=safe_record.get("egressEvidenceDecisionReason"),
        egressDisciplineReason=safe_record.get("egressDisciplineReason"),
        modelAttemptDigest=safe_record.get("modelAttemptDigest"),
        sourceLedgerDigest=safe_record.get("sourceLedgerDigest"),
        finalProjectionDigest=safe_record.get("finalProjectionDigest"),
        researchEvidenceStatus=safe_record.get("researchEvidenceStatus"),
        citationEvidenceStatus=safe_record.get("citationEvidenceStatus"),
        verifierEvidenceStatus=safe_record.get("verifierEvidenceStatus"),
        finalProjectionEvidenceStatus=safe_record.get("finalProjectionEvidenceStatus"),
        sourceInspectedEventCount=int(safe_record.get("sourceInspectedEventCount") or 0),
        ruleCheckEventCount=int(safe_record.get("ruleCheckEventCount") or 0),
        unsupportedClaimOmittedCount=int(
            safe_record.get("unsupportedClaimOmittedCount") or 0
        ),
        maxProviderTunnelsPerModelAttempt=(
            int(safe_record["maxProviderTunnelsPerModelAttempt"])
            if safe_record.get("maxProviderTunnelsPerModelAttempt") is not None
            else None
        ),
        runnerErrorDiagnostic=_sanitize_runner_error_diagnostic(
            safe_record.get("runnerErrorDiagnostic")
        ),
    )


def _gate1a_selected_attempt_preflight(
    *,
    status: Gate1ASelectedAttemptPreflightStatus,
    reason: Gate1ASelectedAttemptPreflightReason,
    request_digest: str,
    counter_store_writable: bool,
    fallback_receipt_path_available: bool,
    selected_scope_budget_available: bool,
    idempotency_collision: bool,
    pending_inflight_consistent: bool,
) -> Gate1ASelectedAttemptPreflight:
    return Gate1ASelectedAttemptPreflight(
        status=status,
        reason=reason,
        requestDigest=request_digest,
        counterStoreWritable=counter_store_writable,
        fallbackReceiptPathAvailable=fallback_receipt_path_available,
        selectedScopeBudgetAvailable=selected_scope_budget_available,
        idempotencyCollision=idempotency_collision,
        pendingInFlightConsistent=pending_inflight_consistent,
    )


def _scope_key(
    *,
    selected_bot_digest: str,
    trusted_owner_user_id_digest: str,
    environment: str,
    counter_date: str,
) -> str:
    return "|".join(
        (
            counter_date,
            selected_bot_digest,
            trusted_owner_user_id_digest,
            environment,
        )
    )


def _find_request_scope(
    scopes: dict[str, Any],
    *,
    request_digest: str,
    selected_bot_digest: str,
    trusted_owner_user_id_digest: str,
    environment: str,
) -> dict[str, Any] | None:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for scope_key, scope in scopes.items():
        if not isinstance(scope_key, str) or not isinstance(scope, dict):
            continue
        parts = scope_key.split("|")
        if len(parts) != 4:
            continue
        _counter_date_value, bot_digest, owner_digest, scope_environment = parts
        if (
            bot_digest != selected_bot_digest
            or owner_digest != trusted_owner_user_id_digest
            or scope_environment != environment
        ):
            continue
        requests = scope.get("requests")
        if isinstance(requests, dict) and request_digest in requests:
            candidates.append((scope_key, scope))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def _is_retryable_terminal_request_record(record: Mapping[str, object]) -> bool:
    status = str(record.get("status") or "")
    reason = str(record.get("reason") or "")
    if status == "error" and reason in _RETRYABLE_TERMINAL_ERROR_REASONS:
        return True
    return False


def _counter_date(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _coerce_now_ms(value: int | None) -> int:
    if value is not None:
        return max(0, int(value))
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _safe_reason(value: str) -> str:
    if not _SAFE_LABEL_RE.match(value):
        return "internal_error"
    return value


def _sanitize_runner_error_diagnostic(
    value: object,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    sanitized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if key in _RUNNER_ERROR_DIAGNOSTIC_STRING_FIELDS:
            if isinstance(item, str) and _SAFE_LABEL_RE.match(item):
                sanitized[key] = item
            continue
        if key in _RUNNER_ERROR_DIAGNOSTIC_DIGEST_FIELDS:
            if isinstance(item, str) and _DIGEST_RE.match(item):
                sanitized[key] = item
            continue
        if key in _RUNNER_ERROR_DIAGNOSTIC_BOOL_FIELDS:
            if isinstance(item, bool):
                sanitized[key] = item
            continue
        if key in _RUNNER_ERROR_DIAGNOSTIC_LIST_FIELDS:
            if not isinstance(item, (list, tuple)):
                continue
            tool_names: list[str] = []
            for candidate in item:
                if (
                    isinstance(candidate, str)
                    and _SAFE_TOOL_NAME_RE.match(candidate)
                    and candidate not in tool_names
                ):
                    tool_names.append(candidate)
            if tool_names:
                sanitized[key] = tool_names
            continue
        if key in _RUNNER_ERROR_DIAGNOSTIC_TRACEBACK_MARKER_FIELDS:
            if not isinstance(item, (list, tuple)):
                continue
            markers: list[str] = []
            for candidate in item:
                if (
                    isinstance(candidate, str)
                    and _SAFE_LABEL_RE.match(candidate)
                    and candidate not in markers
                ):
                    markers.append(candidate)
                if len(markers) >= 12:
                    break
            if markers:
                sanitized[key] = markers
            continue
        if key in _RUNNER_ERROR_DIAGNOSTIC_PREVIEW_FIELDS:
            preview = _safe_runner_error_preview_or_none(item)
            if preview is not None:
                sanitized[key] = preview
    if "stage" not in sanitized or "reasonCode" not in sanitized:
        return None
    sanitized["schemaVersion"] = _RUNNER_ERROR_DIAGNOSTIC_SCHEMA_VERSION
    return {key: sanitized[key] for key in sorted(sanitized)}


def _sanitize_context_continuity_diagnostic(
    value: object,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    sanitized: dict[str, object] = {
        "schemaVersion": _CONTEXT_CONTINUITY_SCHEMA_VERSION,
        "source": "server_runtime_config",
        "phase": "pre_gate8",
        "localOnly": True,
        "diagnosticOnly": True,
        "responseAuthority": "none",
        "clientMessagesTrustedForContinuity": False,
    }
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if key in _CONTEXT_CONTINUITY_LABEL_FIELDS:
            if isinstance(item, str) and _SAFE_LABEL_RE.match(item):
                sanitized[key] = item
            continue
        if key in _CONTEXT_CONTINUITY_BOOL_FIELDS:
            sanitized[key] = item is True
            continue
        if key in _CONTEXT_CONTINUITY_INT_FIELDS:
            sanitized[key] = max(0, _nonnegative_int(item))
            continue
        if key in _CONTEXT_CONTINUITY_LIST_FIELDS:
            if not isinstance(item, (list, tuple)):
                continue
            reason_codes: list[str] = []
            for candidate in item:
                if (
                    isinstance(candidate, str)
                    and _SAFE_LABEL_RE.match(candidate)
                    and not _CONTEXT_REASON_CODE_FORBIDDEN_RE.search(candidate)
                    and candidate not in reason_codes
                ):
                    reason_codes.append(candidate)
                if len(reason_codes) >= 16:
                    break
            sanitized[key] = reason_codes
    sanitized["schemaVersion"] = _CONTEXT_CONTINUITY_SCHEMA_VERSION
    sanitized["source"] = "server_runtime_config"
    sanitized["phase"] = "pre_gate8"
    sanitized["localOnly"] = True
    sanitized["diagnosticOnly"] = True
    sanitized["responseAuthority"] = "none"
    sanitized["clientMessagesTrustedForContinuity"] = False
    sanitized["productionAuthorityAllowed"] = False
    sanitized["transcriptWriteAllowed"] = False
    sanitized["sseWriteAllowed"] = False
    sanitized["dbWriteAllowed"] = False
    return {key: sanitized[key] for key in sorted(sanitized)}


def _safe_runner_error_preview_or_none(value: object) -> str | None:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > 256:
        return None
    if _RUNNER_ERROR_DIAGNOSTIC_PREVIEW_FORBIDDEN_RE.search(text):
        return None
    return text


def _safe_timestamp(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9TZ:.+-]{10,40}", value.strip()))


def _nonnegative_int(value: int | str | None) -> int:
    try:
        return max(0, int(value if value is not None else 0))
    except (TypeError, ValueError):
        return 0


def _validate_digest(value: str, message: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise ValueError(message)


def _validate_safe_label(value: str, message: str) -> None:
    if not isinstance(value, str) or not _SAFE_LABEL_RE.match(value):
        raise ValueError(message)


def _validate_egress_host_class(value: str) -> None:
    if not isinstance(value, str) or not _SAFE_EGRESS_HOST_CLASS_RE.match(value):
        raise ValueError("egress host class must be a sanitized class label")


__all__ = [
    "Gate5B4C3ShadowDeliveryReceipt",
    "Gate5B4C3ShadowDeliveryEvidence",
    "Gate1ASelectedAttemptPreflight",
    "Gate5B4C3ShadowCounterReservation",
    "Gate5B4C3ShadowCounterState",
    "Gate5B4C3ShadowCounterStore",
]
