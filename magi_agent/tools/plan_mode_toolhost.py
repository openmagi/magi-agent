"""Route the catalog Enter/ExitPlanMode tools to the GA plan-act flow.

Inventory **B14** / doc 12 PR2. The ``EnterPlanMode`` / ``ExitPlanMode``
manifests are declared in :mod:`magi_agent.tools.catalog` but had **no handler
bound**, so the ``cli/wiring.py`` ``registration.handler is not None`` filter
silently dropped them — the model could never call them even though
``docs/tools.md`` advertises them.

The plan→act ENGINE already exists and is fully tested:
:func:`magi_agent.harness.general_automation.plan_act_switch.resolve_general_automation_plan_act_switch`
(re-resolves the GA preset projection from ``automation.plan`` to an execution
preset on an APPROVED plan-exit control). That resolver was simply **not routed**
(see that module's docstring §41-43: "the production turn loop does not yet call
… ready for the runner to attach").

This module is the model-facing routing. It mirrors the
:class:`magi_agent.introspection.tool.InspectSelfEvidenceToolHost` binder
pattern: handlers are ALWAYS bound (so a dispatch never raises ``KeyError``), but
the tools are only ADVERTISED (registry ``enabled=True``) when the strict
default-OFF ``MAGI_PLAN_MODE_TOOLS_ENABLED`` gate is on. When the gate is off the
tools stay manifest-only and a dispatch returns a structured ``blocked`` no-op,
so exposure and behaviour are byte-identical to ``main``.

Semantics (no new pack / posture / control mechanism is introduced):

* ``EnterPlanMode`` — return a read-only plan-mode posture marker. The actual
  mutation-blocking is enforced by the existing plan-mode permission path
  (``tools/safety.py`` ``plan_mode_mutation_blocked``); this tool only signals
  the intent so the engine can flip ``RuntimeMode`` to ``plan``.
* ``ExitPlanMode`` — request a plan-exit approval by BLOCKING the turn with an
  ``approval_required`` control projection (the same
  :func:`magi_agent.harness.general_automation.control_projection.build_general_automation_control_projection`
  mechanics the GA question flow uses). The downstream
  ``resolve_general_automation_plan_act_switch`` consumes that approval to flip
  ``automation.plan`` → execution preset and inject the "execute the plan"
  message. Raw plan content is never surfaced — only a sha256 digest + ref.

Durable persistence of the pending plan-exit approval (across session restart) is
owned by cluster 09-permissions (A7); the control projection here is in-memory.
The plan-act runner attachment behind ``MAGI_PLAN_ACT_GATE_ENABLED``
(:func:`...plan_act_switch.wire_plan_act_switch_gate`) is unchanged by this PR.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json

from magi_agent.harness.general_automation.control_projection import (
    GeneralAutomationControlProjectionRequest,
    build_general_automation_control_projection,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


ENTER_PLAN_MODE_TOOL_NAME = "EnterPlanMode"
EXIT_PLAN_MODE_TOOL_NAME = "ExitPlanMode"

_GA_ROLE = "general"
_POLICY_REF = "policy:general-automation:plan-exit"
_SUBJECT_REF_PREFIX = "subject:general-automation-plan-exit:"
_RESUME_REF_PREFIX = "resume:general-automation-plan-exit:"
_PLAN_KEYS = ("plan", "planBody", "plan_body", "summary")


class PlanModeToolHost:
    """Bind the Enter/ExitPlanMode handlers to a :class:`ToolRegistry`.

    Mirrors :class:`~magi_agent.introspection.tool.InspectSelfEvidenceToolHost`:
    handlers are bound unconditionally; the tools are advertised only when
    ``enabled`` (the ``MAGI_PLAN_MODE_TOOLS_ENABLED`` gate) is true.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)

    def bind(self, registry: ToolRegistry) -> None:
        self._bind_one(registry, ENTER_PLAN_MODE_TOOL_NAME, self._handle_enter)
        self._bind_one(registry, EXIT_PLAN_MODE_TOOL_NAME, self._handle_exit)

    def _bind_one(self, registry: ToolRegistry, name: str, impl) -> None:
        registration = registry.resolve_registration(name)
        if registration is None:
            return  # manifest not registered — nothing to bind
        if registration.handler is not None:
            return  # already bound

        host = self  # capture for closure

        async def _handler(
            arguments: dict[str, object],
            context: ToolContext,
            *,
            _impl=impl,
        ) -> ToolResult:
            return _impl(arguments, context)

        registry.bind_handler(
            name,
            _handler,
            enabled_by_registry_policy=host.enabled,
        )
        # The catalog manifests are ``enabled_by_default=True``; binding a
        # handler with the gate OFF would otherwise pass the ``cli/wiring.py``
        # ``handler is not None`` filter AND stay advertised — newly exposing
        # tools that ``main`` (no handler) never exposed. Explicitly disable
        # when the gate is off so OFF behaviour is byte-identical to ``main``.
        if not host.enabled:
            registry.disable(name)

    # -- handlers -----------------------------------------------------------

    def _disabled_result(self, name: str) -> ToolResult:
        return ToolResult(
            status="blocked",
            error_code="plan_mode_tools_disabled",
            error_message=f"{name} is not enabled (MAGI_PLAN_MODE_TOOLS_ENABLED off).",
            metadata={"toolName": name, "reason": "plan_mode_tools_disabled"},
        )

    def _handle_enter(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if not self.enabled:
            return self._disabled_result(ENTER_PLAN_MODE_TOOL_NAME)
        return ToolResult(
            status="ok",
            output={
                "runtimeMode": "plan",
                "note": (
                    "Entered read-only plan mode. Mutating tools are blocked "
                    "until ExitPlanMode is approved."
                ),
            },
            metadata={
                "toolName": ENTER_PLAN_MODE_TOOL_NAME,
                "runtimeMode": "plan",
                "mutationsBlocked": True,
                "permissionClass": "meta",
                "mutatesWorkspace": False,
            },
        )

    def _handle_exit(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if not self.enabled:
            return self._disabled_result(EXIT_PLAN_MODE_TOOL_NAME)

        base_metadata: dict[str, object] = {
            "toolName": EXIT_PLAN_MODE_TOOL_NAME,
            "permissionClass": "meta",
            "dangerous": False,
            "mutatesWorkspace": False,
        }

        if _agent_role(context) != _GA_ROLE:
            return ToolResult(
                status="blocked",
                metadata={**base_metadata, "reason": "plan_exit_inert"},
            )

        plan_digest = _plan_payload_digest(arguments)
        subject_ref = _SUBJECT_REF_PREFIX + _short(plan_digest)
        resume_ref = _RESUME_REF_PREFIX + _short(
            _digest(
                {
                    "sessionKey": context.session_key or "",
                    "turnId": context.turn_id or "",
                    "payloadDigest": plan_digest,
                }
            )
        )
        request = GeneralAutomationControlProjectionRequest(
            controlType="approval_required",
            subjectRef=subject_ref,
            policyRef=_POLICY_REF,
            payloadDigest=plan_digest,
            reasonCodes=("general_automation_plan_exit",),
            resumeRef=resume_ref,
            metadata={"planExit": True},
        )
        projection = build_general_automation_control_projection(request)
        return ToolResult(
            status="needs_approval",
            metadata={
                **base_metadata,
                "reason": "general_automation_plan_exit",
                "pendingControlRequest": True,
                "controlProjection": projection.public_projection(),
                "resumeRef": resume_ref,
            },
        )


def bind_plan_mode_handlers(
    registry: ToolRegistry,
    *,
    enabled: bool | None = None,
) -> None:
    """Convenience binder resolving the env gate when ``enabled`` is None.

    ``enabled=None`` (default) reads ``MAGI_PLAN_MODE_TOOLS_ENABLED`` via the
    single env source of truth (strict default OFF). Pass an explicit bool to
    override (used by tests).
    """
    if enabled is None:
        from magi_agent.config.env import plan_mode_tools_enabled  # noqa: PLC0415

        enabled = plan_mode_tools_enabled()
    PlanModeToolHost(enabled=enabled).bind(registry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_role(context: ToolContext) -> str:
    contract = context.execution_contract
    if isinstance(contract, Mapping):
        for key in ("agentRole", "agent_role"):
            value = contract.get(key)
            if isinstance(value, str):
                return value.strip().casefold().replace("-", "_")
    return ""


def _plan_payload_digest(arguments: Mapping[str, object]) -> str:
    body = ""
    for key in _PLAN_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            body = value
            break
    return _digest({"planBody": body})


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:24]


__all__ = [
    "ENTER_PLAN_MODE_TOOL_NAME",
    "EXIT_PLAN_MODE_TOOL_NAME",
    "PlanModeToolHost",
    "bind_plan_mode_handlers",
]
