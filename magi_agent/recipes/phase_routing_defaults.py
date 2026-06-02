from __future__ import annotations

from collections.abc import Mapping

from magi_agent.recipes.reliability_policy import RecipeReliabilityPolicyRegistry
from magi_agent.runtime.model_tiers import ModelTierRegistry, ModelUsagePhase
from magi_agent.runtime.phase_routing import PhaseRoutingPlanner


CODING_PHASE_CAPABILITY_REQUIREMENTS: Mapping[ModelUsagePhase, str] = {
    "code_search": "coding",
    "patch_planning": "coding",
    "patch_generation": "coding",
}


def recipe_phase_routing_planner() -> PhaseRoutingPlanner:
    return PhaseRoutingPlanner(
        model_registry=ModelTierRegistry.with_defaults(),
        policy_registry=RecipeReliabilityPolicyRegistry.with_defaults(),
        phase_capability_requirements=CODING_PHASE_CAPABILITY_REQUIREMENTS,
    )


__all__ = [
    "CODING_PHASE_CAPABILITY_REQUIREMENTS",
    "recipe_phase_routing_planner",
]
