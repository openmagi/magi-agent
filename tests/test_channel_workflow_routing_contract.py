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


def test_model_construct_force_falses_authority_flags():
    # C-4 PR-E: WorkflowRouteDecision now inherits FalseOnlyAuthorityModel.
    # The legacy ``model_construct`` raised TypeError (fail-CLOSED-via-raise).
    # The kernel routes every construction surface through ``model_validate``,
    # which force-falses Literal[False] fields on input (strictly-stronger
    # fail-CLOSED-via-coerce). The security invariant ("authority flags cannot
    # be turned on through construction") is preserved; only the failure mode
    # changes (raise -> coerce).
    d = WorkflowRouteDecision.model_construct(
        routed=True, route_attached=True, execution_attached=True
    )
    assert d.route_attached is False
    assert d.execution_attached is False


def test_dispatcher_seam_defaults_to_none():
    from magi_agent.channels.dispatcher import maybe_route_to_workflow
    assert maybe_route_to_workflow(eligible=False, confirmed=False, enabled=False) is None


def test_model_copy_cannot_set_authority_flags_true():
    d = decide_workflow_route(eligible=True, confirmed=True, enabled=True)
    copied = d.model_copy(update={"route_attached": True, "execution_attached": True})
    assert copied.route_attached is False
    assert copied.execution_attached is False


def test_dispatcher_seam_returns_decision_when_all_true():
    from magi_agent.channels.dispatcher import maybe_route_to_workflow
    from magi_agent.channels.workflow_routing import WorkflowRouteDecision
    decision = maybe_route_to_workflow(eligible=True, confirmed=True, enabled=True)
    assert isinstance(decision, WorkflowRouteDecision)
    assert decision.routed is True
