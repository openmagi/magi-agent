from magi_agent.channels.workflow_routing import (
    WorkflowRouteDecision,
    decide_workflow_route,
)


def test_default_decision_is_not_routed_and_flags_false():
    d = decide_workflow_route(eligible=False, confirmed=False, enabled=False)
    assert d.routed is False
    assert d.route_attached is False
    assert d.execution_attached is False


def test_routes_only_when_eligible_confirmed_and_enabled():
    d = decide_workflow_route(eligible=True, confirmed=True, enabled=True)
    assert d.routed is True
    assert d.route_attached is False
    assert d.execution_attached is False


def test_any_missing_precondition_blocks_routing():
    assert decide_workflow_route(eligible=True, confirmed=True, enabled=False).routed is False
    assert decide_workflow_route(eligible=True, confirmed=False, enabled=True).routed is False
    assert decide_workflow_route(eligible=False, confirmed=True, enabled=True).routed is False


def test_model_construct_disabled():
    import pytest
    with pytest.raises(TypeError):
        WorkflowRouteDecision.model_construct()


def test_dispatcher_seam_defaults_to_none():
    from magi_agent.channels.dispatcher import maybe_route_to_workflow
    assert maybe_route_to_workflow(eligible=False, confirmed=False, enabled=False) is None
