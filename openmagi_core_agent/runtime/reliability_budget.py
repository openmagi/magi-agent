from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openmagi_core_agent.runtime.model_tiers import ModelTier, ModelUsagePhase


ReservationStatus = Literal["reserved", "denied"]
FallbackAction = Literal["ask_user", "stop", "fallback_to_typescript"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class ReliabilityBudgetRequest(BaseModel):
    model_config = _MODEL_CONFIG

    phase: ModelUsagePhase
    requested_tier: ModelTier = Field(alias="requestedTier")
    reason: str
    estimated_cost_usd: float = Field(default=0.0, ge=0, alias="estimatedCostUsd")
    retry_count: int = Field(default=0, ge=0, alias="retryCount")
    elapsed_ms: int = Field(default=0, ge=0, alias="elapsedMs")
    fallback_to_typescript: bool = Field(default=False, alias="fallbackToTypeScript")


class ReliabilityBudgetDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ReservationStatus
    reason_code: str = Field(alias="reasonCode")
    fallback_action: FallbackAction | None = Field(default=None, alias="fallbackAction")
    estimated_cost_usd: float = Field(alias="estimatedCostUsd")
    reserved_cost_usd: float = Field(default=0.0, alias="reservedCostUsd")


class ReliabilityBudgetLedger(BaseModel):
    model_config = _MODEL_CONFIG

    cheap_call_count: int = Field(default=0, alias="cheapCallCount")
    standard_call_count: int = Field(default=0, alias="standardCallCount")
    sota_escalation_count: int = Field(default=0, alias="sotaEscalationCount")
    fallback_to_typescript_count: int = Field(default=0, alias="fallbackToTypeScriptCount")
    total_reserved_cost_usd: float = Field(default=0.0, alias="totalReservedCostUsd")
    phase_counts: dict[str, int] = Field(default_factory=dict, alias="phaseCounts")


class ReliabilityBudgetPolicy:
    def __init__(
        self,
        *,
        maxSotaEscalations: int = 1,
        maxCheapCalls: int = 20,
        maxStandardCalls: int = 10,
        maxTotalCostUsd: float = 1.0,
        maxRetries: int = 2,
        maxWallTimeMs: int = 120_000,
    ) -> None:
        self.max_sota_escalations = maxSotaEscalations
        self.max_cheap_calls = maxCheapCalls
        self.max_standard_calls = maxStandardCalls
        self.max_total_cost_usd = maxTotalCostUsd
        self.max_retries = maxRetries
        self.max_wall_time_ms = maxWallTimeMs
        self._cheap_calls = 0
        self._standard_calls = 0
        self._sota_escalations = 0
        self._fallback_to_typescript = 0
        self._total_cost = 0.0
        self._phase_counts: dict[str, int] = {}

    def reserve(self, request: ReliabilityBudgetRequest) -> ReliabilityBudgetDecision:
        if request.retry_count > self.max_retries:
            return self._deny(request, "retry_cap_exceeded")
        if request.elapsed_ms > self.max_wall_time_ms:
            return self._deny(request, "wall_time_cap_exceeded")
        if self._total_cost + request.estimated_cost_usd > self.max_total_cost_usd:
            return self._deny(request, "total_cost_cap_exceeded")
        if request.requested_tier == "cheap" and self._cheap_calls >= self.max_cheap_calls:
            return self._deny(request, "cheap_call_cap_exceeded")
        if request.requested_tier == "standard" and self._standard_calls >= self.max_standard_calls:
            return self._deny(request, "standard_call_cap_exceeded")
        if request.requested_tier == "sota" and self._sota_escalations >= self.max_sota_escalations:
            return self._deny(request, "sota_escalation_cap_exceeded")

        if request.requested_tier == "cheap":
            self._cheap_calls += 1
        elif request.requested_tier == "sota":
            self._sota_escalations += 1
        elif request.requested_tier == "standard":
            self._standard_calls += 1
        self._total_cost += request.estimated_cost_usd
        self._phase_counts[request.phase] = self._phase_counts.get(request.phase, 0) + 1
        return ReliabilityBudgetDecision(
            status="reserved",
            reasonCode="reserved",
            estimatedCostUsd=request.estimated_cost_usd,
            reservedCostUsd=self._total_cost,
        )

    def ledger(self) -> ReliabilityBudgetLedger:
        return ReliabilityBudgetLedger(
            cheapCallCount=self._cheap_calls,
            standardCallCount=self._standard_calls,
            sotaEscalationCount=self._sota_escalations,
            fallbackToTypeScriptCount=self._fallback_to_typescript,
            totalReservedCostUsd=self._total_cost,
            phaseCounts=dict(sorted(self._phase_counts.items())),
        )

    def _deny(
        self,
        request: ReliabilityBudgetRequest,
        reason_code: str,
    ) -> ReliabilityBudgetDecision:
        fallback: FallbackAction = "fallback_to_typescript" if request.fallback_to_typescript else "ask_user"
        if request.requested_tier == "sota":
            fallback = "ask_user"
        if reason_code in {"retry_cap_exceeded", "wall_time_cap_exceeded"}:
            fallback = "stop"
        if fallback == "fallback_to_typescript":
            self._fallback_to_typescript += 1
        return ReliabilityBudgetDecision(
            status="denied",
            reasonCode=reason_code,
            fallbackAction=fallback,
            estimatedCostUsd=request.estimated_cost_usd,
            reservedCostUsd=self._total_cost,
        )


__all__ = [
    "FallbackAction",
    "ReliabilityBudgetDecision",
    "ReliabilityBudgetLedger",
    "ReliabilityBudgetPolicy",
    "ReliabilityBudgetRequest",
    "ReservationStatus",
]
