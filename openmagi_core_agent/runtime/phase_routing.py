from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openmagi_core_agent.runtime.model_tiers import (
    ModelTier,
    ModelTierRegistry,
    ModelUsagePhase,
)
from openmagi_core_agent.runtime.reliability_budget import (
    ReliabilityBudgetDecision,
    ReliabilityBudgetLedger,
    ReliabilityBudgetPolicy,
    ReliabilityBudgetRequest,
)


EscalationPolicy = Literal[
    "none",
    "same_model_validator_first",
    "bounded_stronger_verifier",
]
DenialReason = Literal[
    "budget_too_low",
    "unsupported_model_capability",
    "invalid_model_route",
    "sota_escalation_cap_exceeded",
]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_REVIEW_PHASES = frozenset({"final_verification", "high_risk_review"})


class PhaseReliabilityPolicy(Protocol):
    max_sota_escalations: int
    sota_escalation_allowed: bool


class PhasePolicyRegistry(Protocol):
    def for_recipe(self, recipe_id: str, *, modelTier: ModelTier) -> PhaseReliabilityPolicy | None:
        ...


class _NoopPhaseReliabilityPolicy:
    max_sota_escalations = 0
    sota_escalation_allowed = False


class _NoopPhasePolicyRegistry:
    def for_recipe(self, recipe_id: str, *, modelTier: ModelTier) -> PhaseReliabilityPolicy:
        return _NoopPhaseReliabilityPolicy()


class PhaseRoute(BaseModel):
    model_config = _MODEL_CONFIG

    phase: ModelUsagePhase
    provider: str
    model: str
    tier: ModelTier
    capabilities: tuple[str, ...] = ()
    escalation_policy: EscalationPolicy = Field(default="none", alias="escalationPolicy")
    verifier_tier: ModelTier | None = Field(default=None, alias="verifierTier")
    route_denied: bool = Field(default=False, alias="routeDenied")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    estimated_cost_usd: float = Field(default=0.0, ge=0, alias="estimatedCostUsd")


class PhaseRoutingRequest(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_ids: tuple[str, ...] = Field(alias="recipeIds")
    default_provider: str = Field(alias="defaultProvider")
    default_model: str = Field(alias="defaultModel")
    phases: tuple[ModelUsagePhase, ...]
    budget_usd: float = Field(default=0.0, ge=0, alias="budgetUsd")

    @field_validator("recipe_ids")
    @classmethod
    def _validate_recipe_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        clean = tuple(dict.fromkeys(item for item in value if item.strip()))
        if not clean:
            raise ValueError("recipeIds must include at least one recipe")
        return clean

    @field_validator("phases")
    @classmethod
    def _validate_phases(cls, value: tuple[ModelUsagePhase, ...]) -> tuple[ModelUsagePhase, ...]:
        clean = tuple(dict.fromkeys(value))
        if not clean:
            raise ValueError("phases must include at least one phase")
        return clean


class PhaseRoutingPlan(BaseModel):
    model_config = _MODEL_CONFIG

    phase_routes: Mapping[ModelUsagePhase, PhaseRoute] = Field(alias="phaseRoutes")
    route_denied: bool = Field(default=False, alias="routeDenied")
    denial_reason: DenialReason | None = Field(default=None, alias="denialReason")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    fallback_to_typescript: bool = Field(default=False, alias="fallbackToTypeScript")
    fallback_reason: str | None = Field(default=None, alias="fallbackReason")
    max_sota_escalations: int = Field(default=0, ge=0, alias="maxSotaEscalations")
    estimated_cost_usd: float = Field(default=0.0, ge=0, alias="estimatedCostUsd")
    budget_decisions: Mapping[ModelUsagePhase, ReliabilityBudgetDecision] = Field(
        default_factory=dict,
        alias="budgetDecisions",
    )
    budget_ledger: ReliabilityBudgetLedger = Field(
        default_factory=ReliabilityBudgetLedger,
        alias="budgetLedger",
    )


class PhaseRoutingPlanner:
    def __init__(
        self,
        *,
        model_registry: ModelTierRegistry,
        policy_registry: PhasePolicyRegistry,
        phase_capability_requirements: Mapping[ModelUsagePhase, str] | None = None,
    ) -> None:
        self._model_registry = model_registry
        self._policy_registry = policy_registry
        self._phase_capability_requirements = dict(phase_capability_requirements or {})

    @classmethod
    def with_default_registry(cls) -> Self:
        return cls(
            model_registry=ModelTierRegistry.with_defaults(),
            policy_registry=_NoopPhasePolicyRegistry(),
        )

    def plan(self, request: PhaseRoutingRequest) -> PhaseRoutingPlan:
        resolved = self._model_registry.resolve(
            provider=request.default_provider,
            model=request.default_model,
        )
        max_sota_escalations = _max_sota_escalations(
            self._policy_registry,
            recipe_ids=request.recipe_ids,
            model_tier=resolved.tier,
        )
        if "unknown_model_standard_no_elevated_capabilities" in resolved.reason_codes:
            return _denied_plan(
                request,
                resolved,
                denial_reason="invalid_model_route",
                reason_codes=tuple(resolved.reason_codes),
                fallback_reason="python_phase_route_invalid_model_route",
                max_sota_escalations=max_sota_escalations,
            )
        if any(phase in _REVIEW_PHASES for phase in request.phases) and request.budget_usd <= 0:
            return _denied_plan(
                request,
                resolved,
                denial_reason="budget_too_low",
                reason_codes=("phase_review_requires_positive_budget",),
                fallback_reason="python_phase_route_budget_too_low",
                max_sota_escalations=max_sota_escalations,
            )

        routes: dict[ModelUsagePhase, PhaseRoute] = {}
        budget_decisions: dict[ModelUsagePhase, ReliabilityBudgetDecision] = {}
        budget_policy = ReliabilityBudgetPolicy(
            maxSotaEscalations=max_sota_escalations,
            maxCheapCalls=max(1, len(request.phases) + 8),
            maxStandardCalls=max(1, len(request.phases) + 8),
            maxTotalCostUsd=request.budget_usd,
        )
        reason_codes: list[str] = []
        route_denied = False
        denial_reason: DenialReason | None = None
        for phase in request.phases:
            requirement = self._phase_capability_requirements.get(phase)
            phase_reason_codes: tuple[str, ...] = ()
            denied = False
            budget_tier = _budget_tier_for_phase(resolved.tier, phase)
            budget_decision = budget_policy.reserve(
                ReliabilityBudgetRequest(
                    phase=phase,
                    requestedTier=budget_tier,
                    reason=f"phase:{phase}",
                    estimatedCostUsd=_phase_cost_estimate(budget_tier, phase),
                    fallbackToTypeScript=True,
                )
            )
            budget_decisions[phase] = budget_decision
            if budget_decision.status == "denied":
                denied = True
                route_denied = True
                budget_denial = _denial_reason_for_budget(budget_decision.reason_code)
                denial_reason = denial_reason or budget_denial
                phase_reason_codes = _merge_reason_codes(
                    phase_reason_codes,
                    (f"phase:{phase}:{budget_decision.reason_code}",),
                )
            if requirement is not None and requirement not in resolved.capabilities:
                denied = True
                route_denied = True
                denial_reason = denial_reason or "unsupported_model_capability"
                phase_reason_codes = _merge_reason_codes(
                    phase_reason_codes,
                    (f"phase:{phase}:requires:{requirement}",),
                )
                reason_codes.extend(phase_reason_codes)
            escalation_policy: EscalationPolicy = "none"
            verifier_tier: ModelTier | None = None
            if phase in _REVIEW_PHASES and not denied:
                if resolved.tier == "cheap":
                    if budget_tier == "sota":
                        escalation_policy = "bounded_stronger_verifier"
                        verifier_tier = "sota"
                else:
                    escalation_policy = "same_model_validator_first"
            reason_codes.extend(phase_reason_codes)
            routes[phase] = PhaseRoute(
                phase=phase,
                provider=resolved.provider,
                model=resolved.model,
                tier=resolved.tier,
                capabilities=resolved.capabilities,
                escalationPolicy=escalation_policy,
                verifierTier=verifier_tier,
                routeDenied=denied,
                reasonCodes=phase_reason_codes,
                estimatedCostUsd=budget_decision.estimated_cost_usd,
            )

        fallback_reason = None
        if route_denied:
            fallback_reason = (
                "python_phase_route_budget_too_low"
                if denial_reason == "budget_too_low"
                else f"python_phase_route_{denial_reason}"
            )
        return PhaseRoutingPlan(
            phaseRoutes=routes,
            routeDenied=route_denied,
            denialReason=denial_reason,
            reasonCodes=tuple(sorted(dict.fromkeys(reason_codes))),
            fallbackToTypeScript=route_denied,
            fallbackReason=fallback_reason,
            maxSotaEscalations=max_sota_escalations,
            estimatedCostUsd=sum(route.estimated_cost_usd for route in routes.values()),
            budgetDecisions=budget_decisions,
            budgetLedger=budget_policy.ledger(),
        )


def _denied_plan(
    request: PhaseRoutingRequest,
    resolved: object,
    *,
    denial_reason: DenialReason,
    reason_codes: tuple[str, ...],
    fallback_reason: str,
    max_sota_escalations: int,
) -> PhaseRoutingPlan:
    routes = {
        phase: PhaseRoute(
            phase=phase,
            provider=getattr(resolved, "provider"),
            model=getattr(resolved, "model"),
            tier=getattr(resolved, "tier"),
            capabilities=getattr(resolved, "capabilities"),
            escalationPolicy="same_model_validator_first" if phase in _REVIEW_PHASES else "none",
            routeDenied=True,
            reasonCodes=reason_codes,
            estimatedCostUsd=_phase_cost_estimate(getattr(resolved, "tier"), phase),
        )
        for phase in request.phases
    }
    return PhaseRoutingPlan(
        phaseRoutes=routes,
        routeDenied=True,
        denialReason=denial_reason,
        reasonCodes=reason_codes,
        fallbackToTypeScript=True,
        fallbackReason=fallback_reason,
        maxSotaEscalations=max_sota_escalations,
        estimatedCostUsd=sum(route.estimated_cost_usd for route in routes.values()),
    )


def _max_sota_escalations(
    policy_registry: PhasePolicyRegistry,
    *,
    recipe_ids: tuple[str, ...],
    model_tier: ModelTier,
) -> int:
    values: list[int] = []
    for recipe_id in recipe_ids:
        policy = policy_registry.for_recipe(recipe_id, modelTier=model_tier)
        if policy is not None and policy.sota_escalation_allowed:
            values.append(policy.max_sota_escalations)
    return max(values, default=0)


def _merge_reason_codes(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*left, *right)))


def _budget_tier_for_phase(tier: ModelTier, phase: ModelUsagePhase) -> ModelTier:
    if tier == "cheap" and phase in _REVIEW_PHASES:
        return "sota"
    return tier


def _denial_reason_for_budget(reason_code: str) -> DenialReason:
    if reason_code == "sota_escalation_cap_exceeded":
        return "sota_escalation_cap_exceeded"
    return "budget_too_low"


def _phase_cost_estimate(tier: ModelTier, phase: ModelUsagePhase) -> float:
    if tier == "cheap":
        return 0.002 if phase not in _REVIEW_PHASES else 0.006
    if tier == "sota":
        return 0.025
    return 0.01


__all__ = [
    "PhaseRoute",
    "PhaseRoutingPlan",
    "PhaseRoutingPlanner",
    "PhaseRoutingRequest",
]
