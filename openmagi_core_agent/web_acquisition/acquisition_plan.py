from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.web_acquisition.policy import (
    content_digest,
    normalize_public_url,
    normalize_query,
    redact_public_text,
    safe_metadata,
    synthetic_url_ref,
    url_policy_error,
)


AcquisitionPhase: TypeAlias = Literal[
    "web_search",
    "fetch",
    "reader_extract",
    "metadata_jsonld",
    "browser_snapshot_fallback",
]
ProviderCapability: TypeAlias = Literal[
    "search_api",
    "fetch",
    "reader_extraction",
    "metadata_jsonld",
    "browser_snapshot",
]
PlanStatus: TypeAlias = Literal["disabled", "planned", "blocked"]
PhaseStatus: TypeAlias = Literal["planned", "blocked"]
FallbackApprovalStatus: TypeAlias = Literal["not_required", "required", "approved"]
QualityDecisionStatus: TypeAlias = Literal[
    "not_evaluated",
    "quality_sufficient",
    "fallback_recommended",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PHASES: tuple[tuple[AcquisitionPhase, ProviderCapability], ...] = (
    ("web_search", "search_api"),
    ("fetch", "fetch"),
    ("reader_extract", "reader_extraction"),
    ("metadata_jsonld", "metadata_jsonld"),
    ("browser_snapshot_fallback", "browser_snapshot"),
)


class _PlanModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class WebAcquisitionPlanConfig(_PlanModel):
    enabled: bool = False
    max_phase_attempts: int = Field(default=1, ge=0, le=5, alias="maxPhaseAttempts")
    timeout_ms: int = Field(default=30_000, ge=1, alias="timeoutMs")
    min_quality_score: float = Field(default=0.6, ge=0.0, le=1.0, alias="minQualityScore")
    production_network_enabled: Literal[False] = Field(
        default=False,
        alias="productionNetworkEnabled",
    )
    production_browser_enabled: Literal[False] = Field(
        default=False,
        alias="productionBrowserEnabled",
    )


class WebAcquisitionPlanRequest(_PlanModel):
    turn_id: str = Field(default="turn-local", alias="turnId")
    query: str | None = None
    url: str | None = None
    allow_browser_fallback: bool = Field(default=False, alias="allowBrowserFallback")
    approval_granted: bool = Field(default=False, alias="approvalGranted")
    observed_quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        alias="observedQualityScore",
    )
    metadata: Mapping[str, object] = Field(default_factory=dict)


class WebAcquisitionPlanAttachmentFlags(_PlanModel):
    network_fetched: Literal[False] = Field(default=False, alias="networkFetched")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    live_provider_called: Literal[False] = Field(default=False, alias="liveProviderCalled")
    raw_content_injected: Literal[False] = Field(default=False, alias="rawContentInjected")
    parent_context_injected: Literal[False] = Field(
        default=False,
        alias="parentContextInjected",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @field_serializer(
        "network_fetched",
        "browser_executed",
        "live_provider_called",
        "raw_content_injected",
        "parent_context_injected",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {field.alias or name: False for name, field in cls.model_fields.items()}


class WebAcquisitionPlanPhase(_PlanModel):
    phase: AcquisitionPhase
    provider_capability: ProviderCapability = Field(alias="providerCapability")
    status: PhaseStatus = "planned"
    max_attempts: int = Field(alias="maxAttempts")
    timeout_ms: int = Field(alias="timeoutMs")
    execution_allowed: Literal[False] = Field(default=False, alias="executionAllowed")
    retry_strategy: Literal["bounded_retry_then_fallback"] = Field(
        default="bounded_retry_then_fallback",
        alias="retryStrategy",
    )


class WebAcquisitionQualityDecision(_PlanModel):
    status: QualityDecisionStatus
    observed_quality_score: float | None = Field(
        default=None,
        alias="observedQualityScore",
    )
    min_quality_score: float = Field(alias="minQualityScore")
    reason_code: str = Field(alias="reasonCode")


class WebBrowserFallbackIntent(_PlanModel):
    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    reason_code: str = Field(alias="reasonCode")
    approval_status: FallbackApprovalStatus = Field(alias="approvalStatus")
    provider_capability: Literal["browser_snapshot"] = Field(
        default="browser_snapshot",
        alias="providerCapability",
    )
    url_ref: str = Field(alias="urlRef")
    raw_url: Literal["[redacted]"] = Field(default="[redacted]", alias="rawUrl")


class WebAcquisitionPlanSourceRecord(_PlanModel):
    source_ref: str = Field(alias="sourceRef")
    evidence_ref: str = Field(alias="evidenceRef")
    method: AcquisitionPhase
    provider_capability: ProviderCapability = Field(alias="providerCapability")
    url_ref: str = Field(alias="urlRef")
    content_digest: str = Field(alias="contentDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)


class WebAcquisitionPlanTrace(_PlanModel):
    status: PlanStatus
    phases: tuple[WebAcquisitionPlanPhase, ...]
    source_ledger_records: tuple[WebAcquisitionPlanSourceRecord, ...] = Field(
        default=(),
        alias="sourceLedgerRecords",
    )
    quality_decision: WebAcquisitionQualityDecision = Field(alias="qualityDecision")
    fallback_intent: WebBrowserFallbackIntent | None = Field(
        default=None,
        alias="fallbackIntent",
    )
    diagnostic_metadata: Mapping[str, object] = Field(
        default_factory=dict,
        alias="diagnosticMetadata",
    )
    attachment_flags: WebAcquisitionPlanAttachmentFlags = Field(
        default_factory=WebAcquisitionPlanAttachmentFlags,
        alias="attachmentFlags",
    )


def build_web_acquisition_plan(
    request: WebAcquisitionPlanRequest | Mapping[str, object],
    *,
    config: WebAcquisitionPlanConfig | Mapping[str, object] | None = None,
) -> WebAcquisitionPlanTrace:
    safe_request = WebAcquisitionPlanRequest.model_validate(request)
    safe_config = WebAcquisitionPlanConfig.model_validate(config or {})
    query, query_error = _safe_query(safe_request.query)
    url_ref, url_error, normalized_url = _safe_url_ref(safe_request.url)
    status: PlanStatus = "planned" if safe_config.enabled else "disabled"
    reason_codes = [query_error, url_error]
    reason_codes = [code for code in reason_codes if code]
    if safe_config.enabled and (query_error is not None or url_error is not None):
        status = "blocked"

    phases = _build_phases(safe_config, blocked=status == "blocked")
    quality_decision = _quality_decision(safe_request, safe_config)
    source_records: tuple[WebAcquisitionPlanSourceRecord, ...] = ()
    fallback_intent: WebBrowserFallbackIntent | None = None

    if status != "blocked":
        source_records = (
            WebAcquisitionPlanSourceRecord(
                sourceRef="source:web:plan-1",
                evidenceRef="evidence:web:plan-1",
                method="web_search" if query else "fetch",
                providerCapability="search_api" if query else "fetch",
                urlRef=url_ref or synthetic_url_ref(query or "web-acquisition", prefix="url"),
                contentDigest=content_digest(normalized_url or query or "web-acquisition-plan"),
                metadata=safe_metadata(dict(safe_request.metadata)),
            ),
        )
        if safe_request.allow_browser_fallback:
            fallback_intent = WebBrowserFallbackIntent(
                sourceRef=source_records[0].source_ref,
                evidenceRef=source_records[0].evidence_ref,
                reasonCode=quality_decision.reason_code,
                approvalStatus="approved" if safe_request.approval_granted else "required",
                urlRef=source_records[0].url_ref,
            )

    diagnostics = {
        "enabled": safe_config.enabled,
        "planOnly": True,
        "reasonCodes": tuple(reason_codes),
        "query": redact_public_text(query or "", max_chars=160) or None,
        "productionNetworkEnabled": False,
        "productionBrowserEnabled": False,
    }
    return WebAcquisitionPlanTrace(
        status=status,
        phases=phases,
        sourceLedgerRecords=source_records,
        qualityDecision=quality_decision,
        fallbackIntent=fallback_intent,
        diagnosticMetadata=diagnostics,
        attachmentFlags=WebAcquisitionPlanAttachmentFlags(),
    )


def _build_phases(
    config: WebAcquisitionPlanConfig,
    *,
    blocked: bool,
) -> tuple[WebAcquisitionPlanPhase, ...]:
    return tuple(
        WebAcquisitionPlanPhase(
            phase=phase,
            providerCapability=capability,
            status="blocked" if blocked else "planned",
            maxAttempts=config.max_phase_attempts,
            timeoutMs=config.timeout_ms,
            executionAllowed=False,
        )
        for phase, capability in _PHASES
    )


def _quality_decision(
    request: WebAcquisitionPlanRequest,
    config: WebAcquisitionPlanConfig,
) -> WebAcquisitionQualityDecision:
    score = request.observed_quality_score
    if score is None:
        status: QualityDecisionStatus = "not_evaluated"
        reason = "quality_score_absent"
    elif score < config.min_quality_score:
        status = "fallback_recommended"
        reason = "content_quality_below_threshold"
    else:
        status = "quality_sufficient"
        reason = "content_quality_sufficient"
    return WebAcquisitionQualityDecision(
        status=status,
        observedQualityScore=score,
        minQualityScore=config.min_quality_score,
        reasonCode=reason,
    )


def _safe_query(query: str | None) -> tuple[str | None, str | None]:
    if query is None:
        return None, None
    try:
        return normalize_query(query), None
    except ValueError:
        return None, "query_required"


def _safe_url_ref(url: str | None) -> tuple[str | None, str | None, str | None]:
    if not url:
        return None, None, None
    error = url_policy_error(url)
    if error is not None:
        return None, error, None
    normalized = normalize_public_url(url)
    return synthetic_url_ref(normalized, prefix="url"), None, normalized


__all__ = [
    "WebAcquisitionPlanAttachmentFlags",
    "WebAcquisitionPlanConfig",
    "WebAcquisitionPlanPhase",
    "WebAcquisitionPlanRequest",
    "WebAcquisitionPlanSourceRecord",
    "WebAcquisitionPlanTrace",
    "WebAcquisitionQualityDecision",
    "WebBrowserFallbackIntent",
    "build_web_acquisition_plan",
]
