from magi_agent.channels.research_command import (
    ResearchCommandResult,
    prepare_research_command,
)


def test_prepare_returns_bundle_and_cost_confirm():
    out = prepare_research_command(
        query="compare X vs Y",
        per_child_token_estimate=8000,
        model_microcents_per_1k=120,
    )
    assert isinstance(out, ResearchCommandResult)
    assert out.cost_estimate.estimated_child_agents >= 0
    assert "credits" in out.confirm_prompt.lower() or "크레딧" in out.confirm_prompt
    assert out.awaiting_confirmation is True


def test_empty_query_is_blocked():
    import pytest
    with pytest.raises(ValueError):
        prepare_research_command(query="   ", per_child_token_estimate=8000, model_microcents_per_1k=120)


def test_model_construct_disabled():
    import pytest
    with pytest.raises(TypeError):
        ResearchCommandResult.model_construct()


def test_query_is_stripped():
    out = prepare_research_command(
        query="   compare X vs Y   ",
        per_child_token_estimate=8000,
        model_microcents_per_1k=120,
    )
    assert out.query == "compare X vs Y"


def test_compiled_bundle_is_carried_for_execution():
    out = prepare_research_command(
        query="q",
        per_child_token_estimate=8000,
        model_microcents_per_1k=120,
    )
    # the bundle's contract must match the contract the cost estimate was computed from
    assert out.compiled_bundle.contract.workflow_id == out.cost_estimate.workflow_id
