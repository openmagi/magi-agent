"""Tests for the manifest-routed Enter/ExitPlanMode toolhost (doc 12 PR2).

The catalog ``EnterPlanMode`` / ``ExitPlanMode`` manifests exist but had no
handler bound, so the ``cli/wiring.py`` ``registration.handler is not None``
filter silently dropped them. This binds them behind the strict default-OFF
``MAGI_PLAN_MODE_TOOLS_ENABLED`` gate:

* ``EnterPlanMode`` → a read-only plan-mode posture marker (``ok``).
* ``ExitPlanMode``  → request a plan-exit approval (blocks the turn via an
  ``approval_required`` control projection), reusing the existing GA control
  projection mechanics that
  :func:`magi_agent.harness.general_automation.plan_act_switch.resolve_general_automation_plan_act_switch`
  consumes on approval.
"""
from __future__ import annotations

import asyncio

from magi_agent.tools.context import ToolContext
from magi_agent.tools.plan_mode_toolhost import (
    ENTER_PLAN_MODE_TOOL_NAME,
    EXIT_PLAN_MODE_TOOL_NAME,
    PlanModeToolHost,
    bind_plan_mode_handlers,
)
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


def test_bind_off_keeps_manifests_unadvertised() -> None:
    registry = _registry()
    bind_plan_mode_handlers(registry, enabled=False)

    assert registry.is_enabled(ENTER_PLAN_MODE_TOOL_NAME) is False
    assert registry.is_enabled(EXIT_PLAN_MODE_TOOL_NAME) is False


def test_bind_on_advertises_manifests() -> None:
    registry = _registry()
    bind_plan_mode_handlers(registry, enabled=True)

    assert registry.is_enabled(ENTER_PLAN_MODE_TOOL_NAME) is True
    assert registry.is_enabled(EXIT_PLAN_MODE_TOOL_NAME) is True


def test_enter_plan_mode_off_blocked_noop() -> None:
    registry = _registry()
    bind_plan_mode_handlers(registry, enabled=False)
    registration = registry.resolve_registration(ENTER_PLAN_MODE_TOOL_NAME)
    assert registration is not None and registration.handler is not None

    result = asyncio.run(registration.handler({}, _general_context()))
    assert result.status == "blocked"
    assert result.metadata.get("reason") == "plan_mode_tools_disabled"


def test_enter_plan_mode_on_returns_read_only_marker() -> None:
    registry = _registry()
    bind_plan_mode_handlers(registry, enabled=True)
    registration = registry.resolve_registration(ENTER_PLAN_MODE_TOOL_NAME)
    assert registration is not None and registration.handler is not None

    result = asyncio.run(registration.handler({}, _general_context()))
    assert result.status == "ok"
    assert result.metadata.get("runtimeMode") == "plan"
    assert result.metadata.get("mutationsBlocked") is True


def test_exit_plan_mode_on_requests_approval() -> None:
    registry = _registry()
    bind_plan_mode_handlers(registry, enabled=True)
    registration = registry.resolve_registration(EXIT_PLAN_MODE_TOOL_NAME)
    assert registration is not None and registration.handler is not None

    result = asyncio.run(
        registration.handler(
            {"plan": "Step 1: do X. Step 2: do Y."},
            _general_context(),
        )
    )
    # ExitPlanMode blocks the turn pending a plan-exit approval.
    assert result.status == "needs_approval"
    assert result.metadata.get("pendingControlRequest") is True
    projection = result.metadata.get("controlProjection")
    assert isinstance(projection, dict)
    assert projection.get("controlType") == "approval_required"
    # Leak-safety: the raw plan body never surfaces — only a digest/ref.
    assert "do X" not in repr(result.metadata)


def test_exit_plan_mode_off_blocked_noop() -> None:
    registry = _registry()
    bind_plan_mode_handlers(registry, enabled=False)
    registration = registry.resolve_registration(EXIT_PLAN_MODE_TOOL_NAME)
    assert registration is not None and registration.handler is not None

    result = asyncio.run(registration.handler({"plan": "x"}, _general_context()))
    assert result.status == "blocked"
    assert result.metadata.get("reason") == "plan_mode_tools_disabled"


def test_toolhost_class_default_disabled() -> None:
    assert PlanModeToolHost().enabled is False
