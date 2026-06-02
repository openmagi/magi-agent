from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.recipes.phase_routing_defaults import recipe_phase_routing_planner
from magi_agent.runtime.phase_routing import (
    PhaseRoutingPlanner,
    PhaseRoutingRequest,
)


def _recipe_planner() -> PhaseRoutingPlanner:
    return recipe_phase_routing_planner()


def test_intent_and_source_extraction_can_use_cheap_model_but_final_verification_can_escalate() -> None:
    planner = _recipe_planner()

    plan = planner.plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("intent_classification", "source_extraction", "final_verification"),
            budgetUsd=0.05,
        )
    )

    assert plan.phase_routes["intent_classification"].tier == "cheap"
    assert plan.phase_routes["source_extraction"].tier == "cheap"
    assert plan.phase_routes["final_verification"].escalation_policy in {
        "same_model_validator_first",
        "bounded_stronger_verifier",
    }
    assert plan.max_sota_escalations <= 1
    assert plan.route_denied is False


def test_high_risk_review_requires_bounded_stronger_verifier() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("high_risk_review",),
            budgetUsd=0.25,
        )
    )

    route = plan.phase_routes["high_risk_review"]
    assert route.escalation_policy == "bounded_stronger_verifier"
    assert route.tier == "cheap"
    assert route.verifier_tier == "sota"
    assert plan.max_sota_escalations == 1


def test_sota_escalation_cap_is_enforced_per_plan() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("final_verification", "high_risk_review"),
            budgetUsd=0.25,
        )
    )

    bounded_routes = [
        route
        for route in plan.phase_routes.values()
        if route.escalation_policy == "bounded_stronger_verifier"
    ]
    assert len(bounded_routes) == plan.max_sota_escalations
    assert plan.route_denied is True
    assert plan.denial_reason == "sota_escalation_cap_exceeded"
    assert "phase:high_risk_review:sota_escalation_cap_exceeded" in plan.reason_codes


def test_positive_but_insufficient_review_budget_denies_instead_of_silent_downgrade() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("high_risk_review",),
            budgetUsd=0.01,
        )
    )

    assert plan.route_denied is True
    assert plan.denial_reason == "budget_too_low"
    assert plan.fallback_to_typescript is True
    assert plan.fallback_reason == "python_phase_route_budget_too_low"
    assert plan.phase_routes["high_risk_review"].verifier_tier is None


def test_known_sota_review_route_requires_enough_budget_for_estimated_cost() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="openai",
            defaultModel="gpt-5.5",
            phases=("high_risk_review",),
            budgetUsd=0.001,
        )
    )

    assert plan.route_denied is True
    assert plan.denial_reason == "budget_too_low"
    assert plan.phase_routes["high_risk_review"].route_denied is True
    assert "phase:high_risk_review:total_cost_cap_exceeded" in plan.reason_codes


def test_unknown_model_labels_are_invalid_direct_phase_routes() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="example",
            defaultModel="unknown-model",
            phases=("high_risk_review",),
            budgetUsd=0.25,
        )
    )

    assert plan.route_denied is True
    assert plan.denial_reason == "invalid_model_route"
    assert plan.fallback_to_typescript is True
    assert plan.fallback_reason == "python_phase_route_invalid_model_route"
    assert "unknown_model_standard_no_elevated_capabilities" in plan.reason_codes


def test_budget_too_low_denies_route_instead_of_silent_downgrade() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("final_verification",),
            budgetUsd=0.0,
        )
    )

    assert plan.route_denied is True
    assert plan.denial_reason == "budget_too_low"
    assert plan.fallback_to_typescript is True
    assert plan.fallback_reason == "python_phase_route_budget_too_low"


def test_unsupported_provider_capability_rejects_route() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.dev-coding",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("code_search",),
            budgetUsd=0.05,
        )
    )

    assert plan.route_denied is True
    assert plan.denial_reason == "unsupported_model_capability"
    assert "phase:code_search:requires:coding" in plan.reason_codes


def test_forged_phase_route_is_rejected_by_request_model() -> None:
    with pytest.raises(ValidationError):
        PhaseRoutingRequest(
            recipeIds=("openmagi.research",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("source_extraction; rm -rf /",),
            budgetUsd=0.05,
        )


def test_type_script_fallback_is_explicit_route_option_with_deterministic_reason() -> None:
    plan = _recipe_planner().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.dev-coding",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("patch_generation",),
            budgetUsd=0.01,
        )
    )

    assert plan.route_denied is True
    assert plan.fallback_to_typescript is True
    assert plan.fallback_reason == "python_phase_route_unsupported_model_capability"
    assert plan.reason_codes == ("phase:patch_generation:requires:coding",)


def test_core_phase_routing_default_has_no_recipe_owned_coding_requirement() -> None:
    plan = PhaseRoutingPlanner.with_default_registry().plan(
        PhaseRoutingRequest(
            recipeIds=("openmagi.dev-coding",),
            defaultProvider="google",
            defaultModel="gemini-3.5-flash",
            phases=("patch_generation",),
            budgetUsd=0.05,
        )
    )

    assert plan.route_denied is False
    assert plan.reason_codes == ()
