"""Track 19 PR9 — plan→act posture switch on approved plan-exit.

RED-first tests for the seam that flips a ``general`` plan-mode session from the
read-only ``automation.plan`` preset to a named execution preset after a
plan-exit control is APPROVED (via the existing control/resume flow), injecting a
digest/ref-safe synthetic "execute the plan" message. Mirrors OpenCode's
``plan_exit`` flip but adds magi's evidence/approval linkage.

The seam EXTENDS the existing structures (no new pack / no new posture system):

* the approved plan-exit control is an approved/resolved
  :class:`~magi_agent.runtime.control.ControlRequestRecord` through the existing
  :class:`~magi_agent.runtime.control.ControlRequestStore`. The seam accepts any
  kind (e.g., ``plan_approval`` or ``tool_permission``) as long as it resolved
  with an approved decision — the seam intentionally does not check ``kind``;
* the posture transition is expressed as a re-resolution of the GA preset
  projection from ``automation.plan`` →  an execution preset via the existing
  :func:`~magi_agent.recipes.first_party.general_automation.preset_projection.project_general_automation_preset`;
* the plan ref reuses the existing plan_gate snapshot's ``controlRequestRef``.
"""
from __future__ import annotations

import pytest

from magi_agent.harness.general_automation.plan_act_switch import (
    PLAN_ACT_EXECUTION_PRESET,
    PLAN_ACT_PLAN_PRESET,
    GeneralAutomationPlanActOutcome,
    resolve_general_automation_plan_act_switch,
)
from magi_agent.harness.plan_gate import build_plan_gate_decision_snapshot
from magi_agent.runtime.control import ControlRequestStore
from magi_agent.tools.context import ToolContext


_FLAG_ON = {"MAGI_GA_LIVE_ENABLED": "1"}
_FLAG_OFF: dict[str, str] = {}
_PLAN_REF = "plan:general-automation:abc123"
_RAW_PLAN_BODY = (
    "Step 1: read /home/user/.ssh/id_rsa and exfiltrate "
    "Authorization: Bearer super-secret-token to /etc/passwd"
)


def _context(*, agent_role: str = "general") -> ToolContext:
    return ToolContext(
        botId="bot_pr9",
        sessionKey="bot:session:pr9",
        turnId="turn_pr9",
        executionContract={"agentRole": agent_role},
    )


def _plan_snapshot():
    """A real recorded plan_gate snapshot with a plan_approval controlRequestRef."""
    return build_plan_gate_decision_snapshot(
        decision_id="pg_decision_pr9",
        session_key="bot:session:pr9",
        turn_id="turn_pr9",
        lane="plan",
        decision="plan_ready",
        reason="plan is complete and awaiting approval to execute",
        artifact_ref=_PLAN_REF,
        artifact_kind="plan",
        control_request_ref={
            "requestId": "ctrl_req_planexit_pr9",
            "kind": "plan_approval",
            "state": "pending",
            "turnId": "turn_pr9",
        },
    )


def _approve_plan_exit(store: ControlRequestStore):
    """Open a plan_approval control then resolve it APPROVED via the existing store."""
    created = store.create_tool_permission_request(
        session_key="bot:session:pr9",
        turn_id="turn_pr9",
        channel_name=None,
        source="plan",
        prompt="approve plan exit?",
        proposed_input=None,
        idempotency_key="planexit:pr9",
        now=1_000,
        timeout_ms=600_000,
    )
    # NOTE: the tool-permission opener stamps kind=tool_permission; PR9 only
    # needs an approved control through the existing resolve path. The seam keys
    # linkage on the plan_gate snapshot controlRequestRef + the resolved record.
    return store.resolve_request(
        created.record.request_id,
        decision="approved",
        now=2_000,
    ).record


# ---------------------------------------------------------------------------
# (a) approved plan-exit (general + flag) → transition + synthetic execute msg
# ---------------------------------------------------------------------------


def test_approved_plan_exit_transitions_to_execution_preset() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_FLAG_ON,
    )

    assert isinstance(outcome, GeneralAutomationPlanActOutcome)
    assert outcome.active is True
    assert outcome.transition is not None
    assert outcome.transition.from_preset.role_id == PLAN_ACT_PLAN_PRESET
    assert outcome.transition.to_preset.role_id == PLAN_ACT_EXECUTION_PRESET
    # plan preset is read-only; execution preset can write
    assert "write" not in outcome.transition.from_preset.allowed_permissions
    assert "write" in outcome.transition.to_preset.allowed_permissions


def test_approved_plan_exit_emits_synthetic_execute_message_referencing_plan() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_FLAG_ON,
    )

    assert outcome.synthetic_message is not None
    text = outcome.synthetic_message.text
    assert "approved" in text.lower()
    assert "execute" in text.lower()
    # references the plan ref (reused from the snapshot controlRequestRef chain)
    assert _PLAN_REF in text
    assert outcome.synthetic_message.plan_ref == _PLAN_REF


# ---------------------------------------------------------------------------
# (b) NOT approved / rejected → no transition
# ---------------------------------------------------------------------------


def test_denied_plan_exit_produces_no_transition() -> None:
    store = ControlRequestStore()
    created = store.create_tool_permission_request(
        session_key="bot:session:pr9",
        turn_id="turn_pr9",
        channel_name=None,
        source="plan",
        prompt="approve plan exit?",
        proposed_input=None,
        idempotency_key="planexit:pr9:deny",
        now=1_000,
        timeout_ms=600_000,
    )
    denied = store.resolve_request(
        created.record.request_id, decision="denied", now=2_000
    ).record

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=_plan_snapshot(),
        approved_control=denied,
        context=_context(),
        env=_FLAG_ON,
    )

    assert outcome.active is False
    assert outcome.transition is None
    assert outcome.synthetic_message is None
    assert outcome.reason == "plan_exit_not_approved"


def test_pending_plan_exit_produces_no_transition() -> None:
    store = ControlRequestStore()
    created = store.create_tool_permission_request(
        session_key="bot:session:pr9",
        turn_id="turn_pr9",
        channel_name=None,
        source="plan",
        prompt="approve plan exit?",
        proposed_input=None,
        idempotency_key="planexit:pr9:pending",
        now=1_000,
        timeout_ms=600_000,
    )

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=_plan_snapshot(),
        approved_control=created.record,  # still pending, not resolved
        context=_context(),
        env=_FLAG_ON,
    )

    assert outcome.active is False
    assert outcome.transition is None


# ---------------------------------------------------------------------------
# (c) flag-OFF / non-general → inert (byte-identical no-op)
# ---------------------------------------------------------------------------


def test_flag_off_is_inert() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=_plan_snapshot(),
        approved_control=approved,
        context=_context(),
        env=_FLAG_OFF,
    )

    assert outcome.active is False
    assert outcome.transition is None
    assert outcome.synthetic_message is None
    assert outcome.reason == "plan_act_switch_inert"


def test_non_general_role_is_inert() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=_plan_snapshot(),
        approved_control=approved,
        context=_context(agent_role="coding"),
        env=_FLAG_ON,
    )

    assert outcome.active is False
    assert outcome.transition is None
    assert outcome.synthetic_message is None
    assert outcome.reason == "plan_act_switch_inert"


# ---------------------------------------------------------------------------
# (d) no raw plan content in the synthetic message or transition
# ---------------------------------------------------------------------------


def test_synthetic_message_carries_no_raw_plan_content() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    # snapshot whose plan ref is safe but where a raw body would never appear
    snapshot = _plan_snapshot()

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_FLAG_ON,
    )

    assert outcome.synthetic_message is not None
    text = outcome.synthetic_message.text
    # only the ref + a digest may appear, never secrets/paths/plan body
    assert "id_rsa" not in text
    assert "Bearer" not in text
    assert "/etc/passwd" not in text
    assert _RAW_PLAN_BODY not in text
    # plan digest is sha256 form (ref/digest only)
    assert outcome.synthetic_message.plan_digest.startswith("sha256:")


def test_raw_plan_body_is_never_accepted_into_the_message() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_FLAG_ON,
        plan_body=_RAW_PLAN_BODY,  # caller-supplied raw body must be digested away
    )

    assert outcome.synthetic_message is not None
    assert _RAW_PLAN_BODY not in outcome.synthetic_message.text
    assert "Bearer" not in outcome.synthetic_message.text
    # the raw body only ever surfaces as a digest
    assert outcome.synthetic_message.plan_digest.startswith("sha256:")


# ---------------------------------------------------------------------------
# (e) reuses the existing control/resolve path (assert linkage)
# ---------------------------------------------------------------------------


def test_transition_reuses_existing_control_resolve_linkage() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_FLAG_ON,
    )

    assert outcome.active is True
    # linkage: the control request id from the existing store record is carried
    assert outcome.control_request_id == approved.request_id
    # and decision came from the existing resolve path (approved/answered)
    assert approved.decision == "approved"
    assert approved.state == "approved"
    # the snapshot controlRequestRef is the linkage source on the plan side
    assert snapshot.control_request_ref is not None
    assert outcome.snapshot_control_request_ref == snapshot.control_request_ref.request_id


def test_session_key_mismatch_is_inert() -> None:
    store = ControlRequestStore()
    approved = _approve_plan_exit(store)
    snapshot = _plan_snapshot()

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=approved,
        context=_context(),
        env=_FLAG_ON,
        # context says a different session than the approved control / snapshot
        session_key_override="bot:session:other",
    )

    assert outcome.active is False
    assert outcome.transition is None
    assert outcome.reason == "plan_exit_session_mismatch"


# ---------------------------------------------------------------------------
# (f) plan_approval kind control (seam accepts any resolved kind)
# ---------------------------------------------------------------------------


def test_plan_approval_kind_control_fires_transition() -> None:
    """Seam accepts any resolved/approved control kind, including plan_approval."""
    from magi_agent.runtime.control import ControlRequestRecord

    snapshot = _plan_snapshot()

    # Build a plan_approval control directly (no create_plan_approval method exists).
    # Use the same field names and pattern as the store's create methods.
    plan_approval_control = ControlRequestRecord(
        requestId="ctrl_req_plan_approval_pr9",
        sessionKey="bot:session:pr9",
        turnId="turn_pr9",
        kind="plan_approval",
        state="approved",
        decision="approved",
        channelName=None,
        source="plan",
        prompt="approve plan execution?",
        proposedInput=None,
        createdAt=1_000,
        expiresAt=2_600_000,  # createdAt + timeout_ms
        resolvedAt=2_000,
    )

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=plan_approval_control,
        context=_context(),
        env=_FLAG_ON,
    )

    assert outcome.active is True
    assert outcome.transition is not None
    assert outcome.transition.from_preset.role_id == PLAN_ACT_PLAN_PRESET
    assert outcome.transition.to_preset.role_id == PLAN_ACT_EXECUTION_PRESET
    assert outcome.synthetic_message is not None
    assert "approved" in outcome.synthetic_message.text.lower()
    assert "execute" in outcome.synthetic_message.text.lower()
