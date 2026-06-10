"""Route the catalog ``AskUserQuestion`` tool to the GA blocking-question flow.

Inventory **B14** / doc 12 PR2. The ``AskUserQuestion`` manifest is declared in
:mod:`magi_agent.tools.catalog` but had **no handler bound**, so the
``cli/wiring.py`` ``registration.handler is not None`` filter silently dropped it
— the model could never call it even though ``docs/tools.md`` advertises it.

The blocking-question ENGINE already exists and is fully tested:
:func:`magi_agent.harness.general_automation.question_tool.general_automation_question_handler`
(``GeneralAutomationQuestion`` + ``ControlRequestStore`` ``user_question``
linkage). It was simply **not routed** (see that module's docstring §43-46:
"the production runner does not yet route … ready for the runner to attach").

This module is that routing. It mirrors the
:class:`magi_agent.introspection.tool.InspectSelfEvidenceToolHost` binder
pattern: the handler is ALWAYS bound (so a dispatch never raises ``KeyError``),
but the tool is only ADVERTISED to the model (registry ``enabled=True``) when the
strict default-OFF ``MAGI_PLAN_MODE_TOOLS_ENABLED`` gate is on. When the gate is
off the tool stays manifest-only and a dispatch returns a structured
``blocked`` no-op, so exposure and behaviour are byte-identical to ``main``.

The external tool name stays ``AskUserQuestion`` (the catalog name the model
sees); internally it delegates to the ``GeneralAutomationQuestion`` handler — no
new pack, control surface, or resume mechanism is introduced. The GA handler's
own ``MAGI_GA_LIVE_ENABLED`` + ``agent_role == "general"`` activation still
applies, so a non-general role or GA-live-off is inert (``blocked``).

Durable persistence of the pending question (across session restart) is owned by
cluster 09-permissions (A7); the ``ControlRequestStore`` here is in-memory.
"""
from __future__ import annotations

from collections.abc import Mapping

from magi_agent.harness.general_automation.question_tool import (
    general_automation_question_handler,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


#: Catalog name the model sees. Internally delegated to the GA question handler.
ASK_USER_QUESTION_TOOL_NAME = "AskUserQuestion"


class AskUserQuestionToolHost:
    """Bind the ``AskUserQuestion`` handler to a :class:`ToolRegistry`.

    Mirrors :class:`~magi_agent.introspection.tool.InspectSelfEvidenceToolHost`:
    the handler is bound unconditionally so an execution-time dispatch returns a
    structured ``blocked`` result rather than raising; the tool is only
    advertised (``enabled=True``) when ``enabled`` (the
    ``MAGI_PLAN_MODE_TOOLS_ENABLED`` gate) is true.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)

    def bind(self, registry: ToolRegistry) -> None:
        registration = registry.resolve_registration(ASK_USER_QUESTION_TOOL_NAME)
        if registration is None:
            return  # manifest not registered — nothing to bind
        if registration.handler is not None:
            return  # already bound

        host = self  # capture for closure

        async def _handler(
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            return host._handle(arguments, context)

        registry.bind_handler(
            ASK_USER_QUESTION_TOOL_NAME,
            _handler,
            enabled_by_registry_policy=host.enabled,
        )
        # The catalog manifest is ``enabled_by_default=True``, so binding a
        # handler with the gate OFF would otherwise pass the ``cli/wiring.py``
        # ``handler is not None`` filter AND stay advertised — newly exposing a
        # tool that ``main`` (no handler) never exposed. Explicitly disable it
        # when the gate is off so OFF behaviour is byte-identical to ``main``.
        if not host.enabled:
            registry.disable(ASK_USER_QUESTION_TOOL_NAME)

    def _handle(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if not self.enabled:
            return ToolResult(
                status="blocked",
                error_code="plan_mode_tools_disabled",
                error_message=(
                    "AskUserQuestion is not enabled "
                    "(MAGI_PLAN_MODE_TOOLS_ENABLED off)."
                ),
                metadata={
                    "toolName": ASK_USER_QUESTION_TOOL_NAME,
                    "reason": "plan_mode_tools_disabled",
                },
            )

        # Delegate to the EXISTING GA blocking-question handler. Its own
        # MAGI_GA_LIVE_ENABLED + general-role activation governs whether it
        # blocks the turn (needs_approval / pending_control_request) or returns
        # an inert blocked no-op. The internal handler reports its own
        # ``GeneralAutomationQuestion`` toolName; surface ``AskUserQuestion`` so
        # the model-facing receipt matches the catalog name.
        result = general_automation_question_handler(dict(arguments), context)
        metadata = dict(result.metadata)
        metadata["toolName"] = ASK_USER_QUESTION_TOOL_NAME
        metadata.setdefault("delegatedTo", "GeneralAutomationQuestion")
        return result.model_copy(update={"metadata": metadata})


def bind_ask_user_question_handler(
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
    AskUserQuestionToolHost(enabled=enabled).bind(registry)


__all__ = [
    "ASK_USER_QUESTION_TOOL_NAME",
    "AskUserQuestionToolHost",
    "bind_ask_user_question_handler",
]
