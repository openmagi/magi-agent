"""Tests for the manifest-routed ``AskUserQuestion`` toolhost (doc 12 PR2).

The catalog ``AskUserQuestion`` manifest exists but had no handler bound, so the
``cli/wiring.py`` ``registration.handler is not None`` filter silently dropped
it. This binds it to the EXISTING
:func:`magi_agent.harness.general_automation.question_tool.general_automation_question_handler`
implementation behind the strict default-OFF ``MAGI_PLAN_MODE_TOOLS_ENABLED``
gate.
"""
from __future__ import annotations

import asyncio

from magi_agent.tools.ask_user_question_toolhost import (
    AskUserQuestionToolHost,
    bind_ask_user_question_handler,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools import register_core_tool_manifests


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    return registry


def _general_context() -> ToolContext:
    return ToolContext(
        bot_id="b",
        user_id="u",
        session_id="s",
        session_key="s",
        turn_id="t",
        execution_contract={"agentRole": "general"},
    )


def test_bind_off_keeps_manifest_unadvertised() -> None:
    registry = _registry()
    bind_ask_user_question_handler(registry, enabled=False)

    # The handler is bound (so dispatch never KeyErrors) but the tool is NOT
    # advertised when the gate is off — exposure is byte-identical to main.
    assert registry.is_enabled("AskUserQuestion") is False


def test_bind_on_advertises_manifest() -> None:
    registry = _registry()
    bind_ask_user_question_handler(registry, enabled=True)

    assert registry.is_enabled("AskUserQuestion") is True


def test_handler_off_returns_blocked_noop() -> None:
    registry = _registry()
    bind_ask_user_question_handler(registry, enabled=False)
    registration = registry.resolve_registration("AskUserQuestion")
    assert registration is not None and registration.handler is not None

    result = asyncio.run(
        registration.handler(
            {"header": "Pick", "question": "Which one?"},
            _general_context(),
        )
    )
    assert result.status == "blocked"
    assert result.metadata.get("reason") == "plan_mode_tools_disabled"


def test_handler_on_blocks_turn_with_pending_control_request() -> None:
    registry = _registry()
    bind_ask_user_question_handler(registry, enabled=True)
    registration = registry.resolve_registration("AskUserQuestion")
    assert registration is not None and registration.handler is not None

    result = asyncio.run(
        registration.handler(
            {
                "header": "Choose a path",
                "question": "Should I proceed with option A or B?",
                "options": [{"label": "A"}, {"label": "B"}],
            },
            _general_context(),
        )
    )

    # Delegates to the existing GA question handler → blocking control request.
    assert result.status == "needs_approval"
    assert result.metadata.get("pendingControlRequest") is True
    assert result.metadata.get("controlProjection") is not None
    # Leak-safety: only option labels surface, not raw question text.
    assert result.metadata.get("optionLabels") == ["A", "B"]


def test_handler_on_non_general_role_is_inert() -> None:
    registry = _registry()
    bind_ask_user_question_handler(registry, enabled=True)
    registration = registry.resolve_registration("AskUserQuestion")
    assert registration is not None and registration.handler is not None

    context = ToolContext(
        bot_id="b",
        user_id="u",
        session_id="s",
        session_key="s",
        turn_id="t",
        execution_contract={"agentRole": "coding"},
    )
    result = asyncio.run(
        registration.handler(
            {"header": "h", "question": "q"},
            context,
        )
    )
    # GA handler is inert for non-general roles → blocked no-op.
    assert result.status == "blocked"


def test_toolhost_class_default_disabled() -> None:
    assert AskUserQuestionToolHost().enabled is False
