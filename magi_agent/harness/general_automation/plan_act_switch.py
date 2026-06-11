"""Track 19 PR9 — plan→act posture switch on approved plan-exit.

This module ports OpenCode's ``plan_exit`` flip — which flips the agent from
read-only *plan* mode to *build* mode after the user approves the plan, injecting
an "execute the plan" message — to the ``general`` agent role, but with magi's
evidence/approval linkage.

It **extends the EXISTING** structures; it does NOT add a new pack or a new
posture system:

* the posture transition is expressed as a *re-resolution* of the existing GA
  preset projection from the ``automation.plan`` preset (read+meta) to a named
  execution preset (default ``automation.files``) via the existing
  :func:`~magi_agent.recipes.first_party.general_automation.preset_projection.project_general_automation_preset`.
  ``project_general_automation_preset(role_id)`` is the existing input that
  drives preset selection — the ``role_id`` IS the re-resolution knob. No new
  posture model is introduced.
* the approval comes through the existing control/resume flow: an APPROVED
  :class:`~magi_agent.runtime.control.ControlRequestRecord` (resolved
  ``decision="approved"`` via :meth:`ControlRequestStore.resolve_request`). The
  plan side reuses the existing :class:`PlanGateDecisionSnapshot`'s
  ``controlRequestRef`` for linkage.
* the synthetic "the plan is approved — execute it" message is digest/ref-safe:
  it carries only the plan *ref* (from the snapshot) plus a sha256 digest of any
  plan body. Raw plan content is never surfaced.

Activation requires BOTH (mirroring PR2/PR6/PR7):

* ``MAGI_GA_LIVE_ENABLED`` truthy (single-source flag, default OFF), and
* ``agent_role == "general"``.

When inactive — non-general role or flag-OFF — the seam is inert: it returns an
inactive outcome with no transition and no synthetic message, so flag-OFF /
non-general behavior is byte-identical to ``main``. The plan_gate snapshot's
``Literal[False]`` write-attached / execution-attached flags are untouched here;
this seam only *re-resolves the preset projection* and *produces a message* — it
attaches nothing live and edits no hard-safety/sealed surface.

Wiring seam: like PR3's completion-repair decision, PR5's max-steps brake, PR6's
constraint re-injection, and PR7's question tool/resume, the production turn loop
does not yet call :func:`resolve_general_automation_plan_act_switch` at the
plan-exit boundary. The function is declared and exercised by tests, ready for
the runner to attach — without inventing a new pack or posture mechanism.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json

from magi_agent.config.env import (
    general_automation_live_enabled,
    plan_act_gate_enabled,
)
from magi_agent.harness.plan_gate import (
    AttachedPlanGateDecisionSnapshot,
    PlanGateDecisionSnapshot,
    attach_plan_gate_execution,
)
from magi_agent.recipes.first_party.general_automation.preset_projection import (
    GeneralAutomationPresetProjection,
    project_general_automation_preset,
)
from magi_agent.runtime.control import ControlRequestRecord
from magi_agent.tools.context import ToolContext
from magi_agent.transport.tool_preview import sanitize_tool_preview


#: The read-only GA preset a ``general`` plan-mode session resolves before the
#: plan is approved. Re-resolved away from on plan-exit approval.
PLAN_ACT_PLAN_PRESET = "automation.plan"

#: The execution preset the session re-resolves TO once the plan is approved.
#: ``automation.files`` is the canonical execution preset (read+write+meta with
#: workspace-write/external-dir approvals still required). A caller may pass a
#: different execution preset id explicitly.
PLAN_ACT_EXECUTION_PRESET = "automation.files"

_GA_ROLE = "general"
_INERT_REASON = "plan_act_switch_inert"
_NOT_APPROVED_REASON = "plan_exit_not_approved"
_SESSION_MISMATCH_REASON = "plan_exit_session_mismatch"
_MAX_MESSAGE_REF_CHARS = 220

#: Decisions on the existing control resolve path that count as a plan-exit
#: approval. ``approved`` is the canonical plan_approval decision; ``answered``
#: is accepted for the user_question-style resolve path that some surfaces use.
_APPROVED_DECISIONS = frozenset({"approved", "answered"})


@dataclass(frozen=True)
class GeneralAutomationPlanActTransition:
    """A posture transition expressed via the existing preset re-resolution.

    Both ends are the existing
    :class:`~magi_agent.recipes.first_party.general_automation.preset_projection.GeneralAutomationPresetProjection`
    produced by ``project_general_automation_preset`` — no new posture type.
    """

    from_preset: GeneralAutomationPresetProjection
    to_preset: GeneralAutomationPresetProjection


@dataclass(frozen=True)
class GeneralAutomationPlanActMessage:
    """The synthetic "plan approved — execute it" message (digest/ref-safe)."""

    text: str
    plan_ref: str
    plan_digest: str


@dataclass(frozen=True)
class GeneralAutomationPlanActOutcome:
    """Result of evaluating a plan-exit control for the plan→act switch.

    ``active`` is ``False`` whenever the switch does not fire (flag-OFF,
    non-general, not-approved, session mismatch). In that case ``transition`` and
    ``synthetic_message`` are ``None`` and callers proceed unchanged.
    """

    active: bool
    reason: str
    transition: GeneralAutomationPlanActTransition | None = None
    synthetic_message: GeneralAutomationPlanActMessage | None = None
    control_request_id: str | None = None
    snapshot_control_request_ref: str | None = None


def _inert(reason: str = _INERT_REASON) -> GeneralAutomationPlanActOutcome:
    return GeneralAutomationPlanActOutcome(active=False, reason=reason)


def resolve_general_automation_plan_act_switch(
    *,
    snapshot: PlanGateDecisionSnapshot,
    approved_control: ControlRequestRecord,
    context: ToolContext,
    env: Mapping[str, str] | None = None,
    execution_preset: str = PLAN_ACT_EXECUTION_PRESET,
    plan_body: str | None = None,
    session_key_override: str | None = None,
) -> GeneralAutomationPlanActOutcome:
    """Evaluate an approved plan-exit control → plan→act posture switch.

    Fires only when the live flag is ON AND the role is ``general`` AND the
    ``approved_control`` was resolved approved on the existing control path AND
    its session matches the plan-mode session. When it fires it returns:

    * a :class:`GeneralAutomationPlanActTransition` re-resolving the GA preset
      projection from ``automation.plan`` → *execution_preset* (the existing
      ``project_general_automation_preset`` re-resolution input), and
    * a digest/ref-safe :class:`GeneralAutomationPlanActMessage` referencing the
      plan ref (reused from the snapshot ``controlRequestRef`` chain) — no raw
      plan content.

    Otherwise it is inert (no transition, no message); flag-OFF / non-general is
    byte-identical to ``main``.
    """
    if not general_automation_live_enabled(env):
        return _inert()
    if _agent_role(context) != _GA_ROLE:
        return _inert()

    # Only an APPROVED control on the existing resolve path may flip the posture.
    # state mirrors decision after resolve_request; both checked to defend against records built outside the store.
    if (
        approved_control.state not in {"approved", "answered"}
        or approved_control.decision not in _APPROVED_DECISIONS
    ):
        return _inert(_NOT_APPROVED_REASON)

    # The approved plan-exit control must belong to the same session as the
    # plan-mode session (snapshot + context). Prevents cross-session flips.
    session_key = session_key_override or context.session_key or ""
    if (
        approved_control.session_key != session_key
        or snapshot.session_key != session_key
    ):
        return _inert(_SESSION_MISMATCH_REASON)

    transition = GeneralAutomationPlanActTransition(
        from_preset=project_general_automation_preset(PLAN_ACT_PLAN_PRESET),
        to_preset=project_general_automation_preset(execution_preset),
    )

    plan_ref = _plan_ref(snapshot)
    message = _build_synthetic_message(plan_ref=plan_ref, plan_body=plan_body)

    snapshot_ref = (
        snapshot.control_request_ref.request_id
        if snapshot.control_request_ref is not None
        else None
    )

    return GeneralAutomationPlanActOutcome(
        active=True,
        reason="plan_exit_approved",
        transition=transition,
        synthetic_message=message,
        control_request_id=approved_control.request_id,
        snapshot_control_request_ref=snapshot_ref,
    )


@dataclass(frozen=True)
class PlanActSwitchWiringResult:
    """Result of the runner-facing plan_act wiring seam (cluster 06 PR4 / B9).

    ``outcome`` is the underlying
    :class:`GeneralAutomationPlanActOutcome` from
    :func:`resolve_general_automation_plan_act_switch`.

    ``attached_snapshot`` is non-``None`` ONLY when the strict default-OFF
    ``MAGI_PLAN_ACT_GATE_ENABLED`` gate is ON *and* the resolver fired — in that
    case the runner has attached execution to a NEW
    :class:`~magi_agent.harness.plan_gate.AttachedPlanGateDecisionSnapshot`. When
    the gate is OFF (default) it is ``None`` and behaviour is byte-identical to
    ``main`` (the resolver still runs but nothing is attached).
    """

    outcome: GeneralAutomationPlanActOutcome
    attached_snapshot: AttachedPlanGateDecisionSnapshot | None = None


def wire_plan_act_switch_gate(
    *,
    snapshot: PlanGateDecisionSnapshot,
    approved_control: ControlRequestRecord,
    context: ToolContext,
    env: Mapping[str, str] | None = None,
    execution_preset: str = PLAN_ACT_EXECUTION_PRESET,
    plan_body: str | None = None,
    session_key_override: str | None = None,
) -> PlanActSwitchWiringResult:
    """Runner-facing seam that gates plan_act attachment behind an explicit flag.

    Inventory B9 / cluster 06 PR4: the GA
    ``plan_gate -> plan_act_switch -> delegation`` chain is self-consistent but
    *inert* because no runner calls
    :func:`resolve_general_automation_plan_act_switch`. This seam lets the
    production turn loop call it at the plan-exit boundary, behind the strict
    default-OFF ``MAGI_PLAN_ACT_GATE_ENABLED`` gate.

    * Gate OFF (default): the seam is inert — it returns an inactive outcome
      with no attached snapshot, never flipping the source snapshot's
      ``Literal[False]`` write/execution-attached flags. Byte-identical to
      ``main``.
    * Gate ON: it delegates to
      :func:`resolve_general_automation_plan_act_switch` (which still enforces
      ``MAGI_GA_LIVE_ENABLED`` + general role + approved/matching control). When
      that resolver fires, it projects the source snapshot onto a NEW
      :class:`~magi_agent.harness.plan_gate.AttachedPlanGateDecisionSnapshot` —
      recording that execution was attached without mutating the original
      immutable snapshot.
    """
    if not plan_act_gate_enabled(env):
        return PlanActSwitchWiringResult(outcome=_inert())

    outcome = resolve_general_automation_plan_act_switch(
        snapshot=snapshot,
        approved_control=approved_control,
        context=context,
        env=env,
        execution_preset=execution_preset,
        plan_body=plan_body,
        session_key_override=session_key_override,
    )

    if not outcome.active:
        return PlanActSwitchWiringResult(outcome=outcome)

    attached = attach_plan_gate_execution(
        snapshot,
        approved_control_request_id=approved_control.request_id,
    )
    return PlanActSwitchWiringResult(outcome=outcome, attached_snapshot=attached)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan_ref(snapshot: PlanGateDecisionSnapshot) -> str:
    """The plan ref to reference in the synthetic message.

    Prefer the recorded artifact ref (the plan artifact); fall back to the
    plan_gate snapshot's ``controlRequestRef`` request id, then the decision id.
    All are public, ref-safe identifiers — never raw plan content.
    """
    artifact_ref = snapshot.artifact_impact.artifact_ref
    if artifact_ref:
        return artifact_ref
    if snapshot.control_request_ref is not None:
        return snapshot.control_request_ref.request_id
    return snapshot.decision_id


def _build_synthetic_message(
    *,
    plan_ref: str,
    plan_body: str | None,
) -> GeneralAutomationPlanActMessage:
    safe_ref = _safe_ref(plan_ref)
    plan_digest = _digest(plan_body if plan_body is not None else plan_ref)
    text = (
        f"The plan at {safe_ref} is approved — you may now execute it. "
        f"(plan digest {plan_digest})"
    )
    return GeneralAutomationPlanActMessage(
        text=text,
        plan_ref=safe_ref,
        plan_digest=plan_digest,
    )


def _safe_ref(value: str) -> str:
    """Scrub + cap a ref for inclusion in the synthetic message body.

    Defensive: a ref should already be a public identifier, but we run it through
    the transport secret scrubber and cap it so a malformed ref can never carry a
    secret/path into the injected message.
    """
    scrubbed = sanitize_tool_preview(value)
    if len(scrubbed) > _MAX_MESSAGE_REF_CHARS:
        return f"{scrubbed[: _MAX_MESSAGE_REF_CHARS - 3]}..."
    return scrubbed


def _agent_role(context: ToolContext) -> str:
    contract = context.execution_contract
    if isinstance(contract, Mapping):
        for key in ("agentRole", "agent_role"):
            value = contract.get(key)
            if isinstance(value, str):
                return value.strip().casefold().replace("-", "_")
    return ""


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "PLAN_ACT_EXECUTION_PRESET",
    "PLAN_ACT_PLAN_PRESET",
    "GeneralAutomationPlanActMessage",
    "GeneralAutomationPlanActOutcome",
    "GeneralAutomationPlanActTransition",
    "PlanActSwitchWiringResult",
    "resolve_general_automation_plan_act_switch",
    "wire_plan_act_switch_gate",
]
