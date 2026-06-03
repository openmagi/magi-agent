from magi_agent.workflows.cost_estimate import (
    WorkflowCostEstimate,
    estimate_workflow_cost,
)
from magi_agent.recipes.workflow_recipe import build_deep_research_workflow


def _contract():
    bundle = build_deep_research_workflow(peer_attestations=[], min_peer_support=2)
    return bundle.contract


def test_estimate_is_deterministic_and_nonnegative():
    c = _contract()
    e1 = estimate_workflow_cost(c, per_child_token_estimate=8000, model_microcents_per_1k=120)
    e2 = estimate_workflow_cost(c, per_child_token_estimate=8000, model_microcents_per_1k=120)
    assert isinstance(e1, WorkflowCostEstimate)
    assert e1 == e2
    assert e1.estimated_total_tokens >= 0
    assert e1.estimated_credits_microcents >= 0
    assert e1.estimated_child_agents == len(c.selected_recipes)


def test_estimate_is_frozen():
    c = _contract()
    e = estimate_workflow_cost(c, per_child_token_estimate=8000, model_microcents_per_1k=120)
    import pytest
    with pytest.raises(Exception):
        e.estimated_total_tokens = 0  # type: ignore[misc]


def test_model_construct_disabled():
    import pytest
    with pytest.raises(TypeError):
        WorkflowCostEstimate.model_construct()
