"""Tests for ControlPlanePlugin — the single fan-out ADK BasePlugin.

Verifies:
- ControlPlanePlugin is a BasePlugin subclass.
- before_tool_callback forwards to the plane (deny -> returns deny_result dict).
- before_tool_callback forwards to the plane (allow -> returns None).
- after_tool_callback forwards to the plane (override -> returns override dict).
- after_tool_callback forwards to the plane (no override -> returns None).
- before_model_callback forwards to the plane (mutations applied; returns None).
- Argument names match the installed ADK 1.33 BasePlugin signatures exactly.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    ControlPlane,
    ControlPlanePlugin,
    ToolDecision,
)


def _make_plane(*controls) -> ControlPlane:
    """Build a ControlPlane bypassing the register() guard for internal dispatch tests."""
    plane = ControlPlane()
    for ctrl in controls:
        plane._controls.append(ctrl)
    return plane


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _DenyAll(BaseLoopControl):
    name = "deny_all"
    deny_result = {"denied": True}

    async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
        return ToolDecision(action="deny", deny_result=self.deny_result)


class _OverrideAfter(BaseLoopControl):
    name = "override_after"
    override = {"after_override": True}

    async def on_after_tool(self, *, tool, args, tool_context, result) -> dict | None:
        return self.override


class _MutateRequest(BaseLoopControl):
    name = "mutate_req"

    async def on_before_model(self, *, callback_context, llm_request) -> None:
        llm_request["mutated"] = True


# ---------------------------------------------------------------------------
# ControlPlanePlugin is a BasePlugin
# ---------------------------------------------------------------------------


def test_control_plane_plugin_is_base_plugin() -> None:
    plane = ControlPlane()
    plugin = ControlPlanePlugin(plane)
    assert isinstance(plugin, BasePlugin)


def test_control_plane_plugin_name() -> None:
    plane = ControlPlane()
    plugin = ControlPlanePlugin(plane)
    assert plugin.name == "magi_control_plane"


# ---------------------------------------------------------------------------
# Signature conformance: arg names must match installed ADK 1.33 BasePlugin
# ---------------------------------------------------------------------------


def test_before_tool_callback_signature_matches_adk() -> None:
    """Verified ADK 1.33 before_tool_callback signature:
    async def before_tool_callback(self, *, tool, tool_args, tool_context)
    Returns Optional[dict].
    """
    sig = inspect.signature(ControlPlanePlugin.before_tool_callback)
    params = set(sig.parameters) - {"self"}
    assert params == {"tool", "tool_args", "tool_context"}


def test_after_tool_callback_signature_matches_adk() -> None:
    """Verified ADK 1.33 after_tool_callback signature:
    async def after_tool_callback(self, *, tool, tool_args, tool_context, result)
    Returns Optional[dict].
    """
    sig = inspect.signature(ControlPlanePlugin.after_tool_callback)
    params = set(sig.parameters) - {"self"}
    assert params == {"tool", "tool_args", "tool_context", "result"}


def test_before_model_callback_signature_matches_adk() -> None:
    """Verified ADK 1.33 before_model_callback signature:
    async def before_model_callback(self, *, callback_context, llm_request)
    Returns Optional[LlmResponse] — plugin returns None (mutates only).
    """
    sig = inspect.signature(ControlPlanePlugin.before_model_callback)
    params = set(sig.parameters) - {"self"}
    assert params == {"callback_context", "llm_request"}


# ---------------------------------------------------------------------------
# before_tool_callback forwarding
# ---------------------------------------------------------------------------


def test_before_tool_deny_returns_deny_result() -> None:
    plane = _make_plane(_DenyAll())
    plugin = ControlPlanePlugin(plane)

    result = _run(
        plugin.before_tool_callback(tool=None, tool_args={}, tool_context=None)
    )

    assert result == {"denied": True}


def test_before_tool_allow_returns_none() -> None:
    plane = ControlPlane()  # no controls -> allow
    plugin = ControlPlanePlugin(plane)

    result = _run(
        plugin.before_tool_callback(tool=None, tool_args={}, tool_context=None)
    )

    assert result is None


def test_before_tool_rewrite_mutates_tool_args_and_returns_none() -> None:
    class _Rewrite(BaseLoopControl):
        name = "rewrite"

        async def on_before_tool(self, *, tool, args, tool_context) -> ToolDecision | None:
            return ToolDecision(action="rewrite", updated_args={"rewritten": True})

    plane = _make_plane(_Rewrite())
    plugin = ControlPlanePlugin(plane)
    tool_args = {"original": True}

    result = _run(
        plugin.before_tool_callback(tool=None, tool_args=tool_args, tool_context=None)
    )

    assert result is None  # no short-circuit for rewrite
    assert tool_args == {"rewritten": True}


# ---------------------------------------------------------------------------
# after_tool_callback forwarding
# ---------------------------------------------------------------------------


def test_after_tool_override_returned() -> None:
    plane = ControlPlane().register(_OverrideAfter())
    plugin = ControlPlanePlugin(plane)

    result = _run(
        plugin.after_tool_callback(
            tool=None, tool_args={}, tool_context=None, result={"orig": 1}
        )
    )

    assert result == {"after_override": True}


def test_after_tool_no_override_returns_none() -> None:
    plane = ControlPlane()  # no controls
    plugin = ControlPlanePlugin(plane)

    result = _run(
        plugin.after_tool_callback(
            tool=None, tool_args={}, tool_context=None, result={"orig": 1}
        )
    )

    assert result is None


# ---------------------------------------------------------------------------
# before_model_callback forwarding
# ---------------------------------------------------------------------------


def test_before_model_mutation_applied_returns_none() -> None:
    plane = ControlPlane().register(_MutateRequest())
    plugin = ControlPlanePlugin(plane)
    request = {}

    result = _run(
        plugin.before_model_callback(callback_context=None, llm_request=request)
    )

    assert result is None
    assert request["mutated"] is True


def test_before_model_empty_plane_returns_none() -> None:
    plane = ControlPlane()
    plugin = ControlPlanePlugin(plane)

    result = _run(
        plugin.before_model_callback(callback_context=None, llm_request={})
    )

    assert result is None
