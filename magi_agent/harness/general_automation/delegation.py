"""Track 19 PR10 — General-Automation scoped delegation (receipt-backed).

This module ports OpenCode's ``task`` subagent-delegation ergonomics to the
``general`` agent role, but instead of returning the child's last *text* part it
returns a **receipt-backed** result: a token-validated
:class:`~magi_agent.meta_orchestration.child_acceptance.ChildAcceptanceVerdict`
produced from a runtime-issued
:class:`~magi_agent.evidence.child_runtime_envelope.ChildRuntimeEnvelope`. The
child's work is *evidenced*, which is strictly better than a bare text blob.

This is a thin *consumer* that REUSES the existing child machinery — it does not
invent a new child runner, envelope, or acceptance mechanism:

* the envelope is the existing ``ChildRuntimeEnvelope`` (runtime-issued by the
  runner), revalidated by the acceptance path;
* acceptance runs through the existing token-validated
  :func:`~magi_agent.meta_orchestration.child_acceptance.accept_real_child_envelope`;
* the depth cap reuses
  :data:`~magi_agent.harness.goal_loop.DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH` (=2) via
  :func:`~magi_agent.harness.goal_loop.validate_goal_loop_spawn_depth`.

Activation requires BOTH:

* ``MAGI_GA_LIVE_ENABLED`` truthy (single-source flag, default OFF), and
* the active ``agent_role`` is ``general`` (derived from the tool
  ``execution_contract``, mirroring ``live_gate.py`` / ``plan_act_switch.py``).

When inactive the delegation is *inert* — no verdict is produced and callers
proceed unchanged (byte-identical to ``main``).

**Unwired seam / no child execution.** Real child execution is gated off
repo-wide; the production runner does not spawn at a clean seam here. This module
therefore EXPECTS an already-runtime-issued envelope (produced by the gated
runner) and only performs the *request-bounding* + *receipt-backed acceptance*
step. It NEVER flips a child-execution authority flag to ``True``
(``runner_attached`` / ``child_execution_attached`` / ``production_authority``
remain ``Literal[False]`` on the envelope), and it surfaces
``real_child_runner_executed = False``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    project_child_runtime_envelope,
)
from magi_agent.harness.goal_loop import (
    DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH,
    GoalLoopSpawnDepthPolicy,
    validate_goal_loop_spawn_depth,
)
from magi_agent.meta_orchestration.child_acceptance import (
    ChildAcceptancePolicy,
    ChildAcceptanceVerdict,
    accept_real_child_envelope,
)
from magi_agent.tools.context import ToolContext

_GA_ROLE = "general"
_INERT_REASON = "delegation_inert"
_DEPTH_EXCEEDED_REASON = "spawn_depth_exceeded"
_ACCEPTED_REASON = "delegation_accepted"


# Depth policy bounded to the goal-loop default (min 0, max 2). Reused rather
# than redefined so the cap tracks DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH.
_GA_DELEGATION_DEPTH_POLICY = GoalLoopSpawnDepthPolicy(
    minDepth=1,
    maxDepth=DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH,
    defaultDepth=1,
)


class GeneralAutomationDelegationRequest(BaseModel):
    """A scoped GA sub-task delegation request (depth-bounded, ref-safe).

    Carries only public, ref-safe identifiers — never raw sub-task prose. The
    actual sub-task body lives behind ``objective_ref`` (e.g. an artifact/prompt
    ref), mirroring how the runtime issues prompts by reference.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
        hide_input_in_errors=True,
    )

    task_id: str = Field(alias="taskId")
    objective_ref: str = Field(alias="objectiveRef")
    spawn_depth: int = Field(default=1, alias="spawnDepth")

    @field_validator("task_id", "objective_ref")
    @classmethod
    def _validate_refs(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("general automation delegation refs must be non-empty")
        return value

    @field_validator("spawn_depth", mode="before")
    @classmethod
    def _validate_spawn_depth(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("spawnDepth must be an integer")
        if value < 1:
            raise ValueError("spawnDepth must be a positive child depth")
        return value


@dataclass(frozen=True)
class GeneralAutomationDelegationOutcome:
    """Result of evaluating a scoped GA delegation request.

    ``active`` is ``False`` whenever the delegation does not fire (flag-OFF or
    non-general). In that case ``verdict`` is ``None`` and callers proceed
    unchanged.

    ``verdict`` is the *receipt-backed* result — a token-validated
    :class:`ChildAcceptanceVerdict` from the existing acceptance path — and is
    ``None`` when the request is denied by the depth cap (``spawn_depth_exceeded``)
    or when the delegation is inert.

    ``real_child_runner_executed`` is always ``False``: this seam never enables
    real child execution; it only bounds the request and runs receipt-backed
    acceptance over an envelope the gated runner already issued.
    """

    active: bool
    reason: str
    verdict: ChildAcceptanceVerdict | None = None
    receipt_ref: str | None = None
    real_child_runner_executed: Literal[False] = False

    def public_projection(self) -> dict[str, object]:
        """Digest/ref-safe projection — no raw child transcript / secret."""
        projection: dict[str, object] = {
            "active": self.active,
            "reason": self.reason,
            "realChildRunnerExecuted": False,
        }
        if self.receipt_ref is not None:
            projection["receiptRef"] = self.receipt_ref
        if self.verdict is not None:
            # public_projection() is already secret-scrubbed + token-gated.
            projection["verdict"] = self.verdict.public_projection()
        return projection


def _inert() -> GeneralAutomationDelegationOutcome:
    return GeneralAutomationDelegationOutcome(active=False, reason=_INERT_REASON)


def build_general_automation_delegation(
    *,
    request: GeneralAutomationDelegationRequest,
    accepted_envelope: ChildRuntimeEnvelope | object,
    receipt_ref: str,
    policy: ChildAcceptancePolicy | Mapping[str, object],
    context: ToolContext,
    env: Mapping[str, str] | None = None,
) -> GeneralAutomationDelegationOutcome:
    """Bound a scoped GA sub-task and return a receipt-backed child verdict.

    Fires only when the live flag is ON AND the role is ``general``. When it
    fires it:

    1. enforces the depth cap (``spawn_depth <= DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH``)
       via :func:`validate_goal_loop_spawn_depth`; a request beyond depth 2 is
       *denied* (``spawn_depth_exceeded``, no verdict) — acceptance is never run;
    2. runs the existing token-validated
       :func:`accept_real_child_envelope` over the runtime-issued envelope to
       produce a receipt-backed :class:`ChildAcceptanceVerdict` (NOT a bare text
       return).

    Otherwise it is inert (no verdict); flag-OFF / non-general is byte-identical
    to ``main``. This never enables child execution and never flips an authority
    flag — the envelope's child-execution flags remain ``Literal[False]``.
    """
    if not general_automation_live_enabled(env):
        return _inert()
    if _agent_role(context) != _GA_ROLE:
        return _inert()

    # Depth cap reuses the goal-loop default (2). Deny anything deeper rather
    # than letting it reach acceptance.
    try:
        validate_goal_loop_spawn_depth(
            request.spawn_depth,
            policy=_GA_DELEGATION_DEPTH_POLICY,
        )
    except ValueError:
        return GeneralAutomationDelegationOutcome(
            active=True,
            reason=_DEPTH_EXCEEDED_REASON,
        )

    # Receipt-backed acceptance via the EXISTING token-validated path. A forged /
    # mismatched envelope degrades to a rejected verdict (never an accepted one);
    # this code never issues the envelope or flips an authority flag itself.
    verdict = accept_real_child_envelope(
        accepted_envelope,
        receipt_ref=receipt_ref,
        policy=policy,
    )
    return GeneralAutomationDelegationOutcome(
        active=True,
        reason=_ACCEPTED_REASON,
        verdict=verdict,
        receipt_ref=receipt_ref,
    )


def project_delegated_child_envelope(envelope: ChildRuntimeEnvelope) -> dict[str, object]:
    """Public, secret-scrubbed projection of the delegated child envelope.

    Thin wrapper over the existing
    :func:`~magi_agent.evidence.child_runtime_envelope.project_child_runtime_envelope`
    so callers never serialize the raw envelope (which carries private metadata /
    transcript refs).
    """
    return project_child_runtime_envelope(envelope).model_dump(by_alias=True)


def _agent_role(context: ToolContext) -> str:
    contract = context.execution_contract
    if isinstance(contract, Mapping):
        for key in ("agentRole", "agent_role"):
            value = contract.get(key)
            if isinstance(value, str):
                return value.strip().casefold().replace("-", "_")
    # No execution_contract present — unknown role, not "general" (bypass).
    return ""


__all__ = [
    "GeneralAutomationDelegationOutcome",
    "GeneralAutomationDelegationRequest",
    "build_general_automation_delegation",
    "project_delegated_child_envelope",
]
