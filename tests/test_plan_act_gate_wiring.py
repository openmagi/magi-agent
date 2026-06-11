"""Cluster 06 PR4 (B9) — plan_act_switch runner-wiring gate.

RED-first tests for the runner-facing wiring seam that lets the production turn
loop call the existing ``resolve_general_automation_plan_act_switch`` resolver at
the plan-exit boundary, behind a **default-OFF** ``MAGI_PLAN_ACT_GATE_ENABLED``
gate.

Per docs/plans/2026-06-09-magi-oss-full-activation/06-evidence-enforcement.md PR4:
the GA ``plan_gate -> plan_act_switch -> delegation`` chain is self-consistent
but *inert* — no runner ever calls it, so the plan_gate snapshot's
``*_write_attached`` / ``execution_attached`` flags stay ``Literal[False]``.

This seam wires it minimally:

* OFF (default): the seam is inert — it returns the same inactive outcome the
  resolver would on flag-OFF, and never attaches execution to the snapshot.
  Behaviour is byte-identical to ``main``.
* ON (``MAGI_PLAN_ACT_GATE_ENABLED=1``) AND ``MAGI_GA_LIVE_ENABLED`` ON AND a
  general role with an approved plan-exit control: the resolver fires and the
  seam attaches execution to the plan_gate snapshot — flipping the previously
  ``Literal[False]`` write/execution-attached flags on a NEW attached snapshot
  (the original immutable snapshot is untouched).
"""
from __future__ import annotations

from magi_agent.config.env import plan_act_gate_enabled
from magi_agent.harness.general_automation.plan_act_switch import (
    PLAN_ACT_EXECUTION_PRESET,
    PLAN_ACT_PLAN_PRESET,
    GeneralAutomationPlanActOutcome,
    wire_plan_act_switch_gate,
)
from magi_agent.harness.plan_gate import (
    AttachedPlanGateDecisionSnapshot,
    build_plan_gate_decision_snapshot,
)
from magi_agent.runtime.control import ControlRequestStore
from magi_agent.tools.context import ToolContext


_GATE_AND_LIVE_ON = {"MAGI_PLAN_ACT_GATE_ENABLED": "1", "MAGI_GA_LIVE_ENABLED": "1"}
_GATE_OFF_LIVE_ON = {"MAGI_PLAN_ACT_GATE_ENABLED": "0", "MAGI_GA_LIVE_ENABLED": "1"}
_PLAN_REF = "plan:general-automation:pr4"


def _context(*, agent_role: str = "general") -> ToolContext:
    return ToolContext(
        botId="bot_pr4",
        sessionKey="bot:session:pr4",
        turnId="turn_pr4",
        executionContract={"agentRole": agent_role},
    )


def _plan_snapshot():
    return build_plan_gate_decision_snapshot(
        decision_id="pg_decision_pr4",
        session_key="bot:session:pr4",
        turn_id="turn_pr4",
        lane="plan",
        decision="plan_ready",
        reason="plan complete and awaiting approval to execute",
        artifact_ref=_PLAN_REF,
        artifact_kind="plan",
        control_request_ref={
            "requestId": "ctrl_req_planexit_pr4",
            "kind": "plan_approval",
            "state": "pending",
            "turnId": "turn_pr4",
        },
    )


def _approve_plan_exit(store: ControlRequestStore):
    created = store.create_tool_permission_request(
        session_key="bot:session:pr4",
        turn_id="turn_pr4",
        channel_name=None,
        source="plan",
        prompt="approve plan exit?",
        proposed_input=None,
        idempotency_key="planexit:pr4",
        now=1_000,
        timeout_ms=600_000,
    )
    return store.resolve_request(
        created.record.request_id,
        decision="approved",
        now=2_000,
    ).record


# ---------------------------------------------------------------------------
# env flag — default OFF
# ---------------------------------------------------------------------------


def test_plan_act_gate_defaults_off() -> None:
    assert plan_act_gate_enabled({}) is False
    # Even in a non-safe runtime profile, the gate stays OFF unless explicit.
    assert plan_act_gate_enabled({"MAGI_RUNTIME_PROFILE": "full"}) is False


def test_plan_act_gate_explicit_on() -> None:
    assert plan_act_gate_enabled({"MAGI_PLAN_ACT_GATE_ENABLED": "1"}) is True
    assert plan_act_gate_enabled({"MAGI_PLAN_ACT_GATE_ENABLED": "true"}) is True
    assert plan_act_gate_enabled({"MAGI_PLAN_ACT_GATE_ENABLED": "0"}) is False


# ---------------------------------------------------------------------------
# OFF (default) — inert, byte-identical to main
# ---------------------------------------------------------------------------


def test_wiring_gate_off_is_inert() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    result = wire_plan_act_switch_gate(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_GATE_OFF_LIVE_ON,
    )

    assert isinstance(result.outcome, GeneralAutomationPlanActOutcome)
    assert result.outcome.active is False
    assert result.attached_snapshot is None
    # original snapshot untouched
    assert snapshot.execution_attached is False


# ---------------------------------------------------------------------------
# ON — resolver fires and execution is attached to a NEW snapshot
# ---------------------------------------------------------------------------


def test_wiring_gate_on_attaches_execution() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    result = wire_plan_act_switch_gate(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_GATE_AND_LIVE_ON,
    )

    assert result.outcome.active is True
    assert result.outcome.transition is not None
    assert result.outcome.transition.from_preset.role_id == PLAN_ACT_PLAN_PRESET
    assert result.outcome.transition.to_preset.role_id == PLAN_ACT_EXECUTION_PRESET

    attached = result.attached_snapshot
    assert isinstance(attached, AttachedPlanGateDecisionSnapshot)
    # the previously Literal[False] flags are now attached
    assert attached.execution_attached is True
    assert attached.session_write_attached is True
    assert attached.transcript_write_attached is True
    assert attached.artifact_write_attached is True
    # identity preserved from the source snapshot
    assert attached.decision_id == snapshot.decision_id
    assert attached.lane == snapshot.lane

    # the ORIGINAL immutable snapshot is never mutated
    assert snapshot.execution_attached is False
    assert snapshot.session_impact.session_write_attached is False


def test_wiring_gate_on_non_general_role_inert() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    result = wire_plan_act_switch_gate(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(agent_role="coding"),
        env=_GATE_AND_LIVE_ON,
    )

    assert result.outcome.active is False
    assert result.attached_snapshot is None
