"""Tests for _ExtendedControlPlanePlugin.on_tool_error_callback forwarding.

Regression guard: before the fix, _ExtendedControlPlanePlugin did NOT expose
on_tool_error_callback, so a FileEdit that RAISES (the gate5b primary path —
see edit_retry_reflection.py module docstring, lines 35-36) never triggered the
corrective reflection re-injection under the plane.

These tests verify:
1. on_tool_error_callback is forwarded to any wrapped plugin that implements it
   (direct unit test — no ADK Runner required).
2. Under the full plane + _ExtendedControlPlanePlugin, a FileEdit that raises
   ValueError("old_text_not_found") produces the corrective reflection response,
   matching pre-migration behavior (end-to-end ADK Runner test).
3. The signature of _ExtendedControlPlanePlugin.on_tool_error_callback matches
   the installed ADK 1.33 BasePlugin.on_tool_error_callback exactly.
4. First-non-None fan-out: when multiple plugins implement on_tool_error_callback,
   the first non-None result wins.
5. No plugin handles the error -> returns None (transparent pass-through).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.adk_bridge.control_plane import (
    ControlPlane,
    _EditRetryLoopControl,
    _ExtendedControlPlanePlugin,
)
from magi_agent.adk_bridge.edit_retry_reflection import (
    EDIT_RETRY_REFLECTION_RESPONSE_TYPE,
    MagiEditRetryReflectionPlugin,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal tool fake with a name attribute."""

    def __init__(self, name: str = "FileEdit") -> None:
        self.name = name


class _FakeCtx:
    """Minimal tool-context fake with an invocation_id."""

    def __init__(self, invocation_id: str = "inv-test") -> None:
        self.invocation_id = invocation_id


def _make_edit_retry_plugin(max_attempts: int = 2) -> MagiEditRetryReflectionPlugin:
    return MagiEditRetryReflectionPlugin(max_attempts=max_attempts)


def _make_plane_with_edit_retry(
    edit_retry_plugin: MagiEditRetryReflectionPlugin,
) -> tuple[ControlPlane, _ExtendedControlPlanePlugin]:
    """Build a minimal plane + extended plugin with only the edit-retry adapter."""
    plane = ControlPlane()
    plane.register(_EditRetryLoopControl(edit_retry_plugin))
    extended = _ExtendedControlPlanePlugin(plane, resilience_plugin=None)
    return plane, extended


# ---------------------------------------------------------------------------
# 1. Signature conformance
# ---------------------------------------------------------------------------


def test_on_tool_error_callback_signature_matches_adk() -> None:
    """_ExtendedControlPlanePlugin.on_tool_error_callback must match ADK 1.33 exactly.

    ADK 1.33 BasePlugin.on_tool_error_callback signature (installed package):
        async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error)
        -> Optional[dict]
    """
    sig = inspect.signature(_ExtendedControlPlanePlugin.on_tool_error_callback)
    params = set(sig.parameters) - {"self"}
    assert params == {"tool", "tool_args", "tool_context", "error"}


def test_on_tool_error_callback_is_on_extended_not_base_plugin() -> None:
    """_ExtendedControlPlanePlugin overrides on_tool_error_callback.

    The base ControlPlanePlugin must NOT expose on_tool_error_callback so the
    extended subclass is the authoritative override point.
    """
    from magi_agent.adk_bridge.control_plane import ControlPlanePlugin

    # Base plugin does not have our implementation — only the extended class does.
    assert not hasattr(ControlPlanePlugin, "on_tool_error_callback") or (
        ControlPlanePlugin.on_tool_error_callback
        is BasePlugin.on_tool_error_callback
    )
    # The extended class has our forwarding override.
    assert (
        _ExtendedControlPlanePlugin.on_tool_error_callback
        is not BasePlugin.on_tool_error_callback
    )


# ---------------------------------------------------------------------------
# 2. Unit: direct forwarding to edit-retry plugin
# ---------------------------------------------------------------------------


def test_on_tool_error_forwarded_to_edit_retry_plugin() -> None:
    """A raised FileEdit error is forwarded to the edit-retry plugin and the
    corrective reflection response is returned."""
    edit_retry_plugin = _make_edit_retry_plugin(max_attempts=2)
    _, extended = _make_plane_with_edit_retry(edit_retry_plugin)

    result = _run(
        extended.on_tool_error_callback(
            tool=_FakeTool("FileEdit"),
            tool_args={"path": "a.py", "oldText": "old", "newText": "new"},
            tool_context=_FakeCtx("inv-forward"),
            error=ValueError("old_text_not_found"),
        )
    )

    assert result is not None, "expected corrective reflection response, got None"
    assert result.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    assert result.get("error_type") == "edit_apply_failed"
    assert result.get("retry_attempt") == 1
    guidance = result.get("reflection_guidance", "")
    assert "old_string was not found" in guidance


def test_on_tool_error_non_edit_tool_returns_none() -> None:
    """Errors from non-edit tools must pass through (return None)."""
    edit_retry_plugin = _make_edit_retry_plugin(max_attempts=2)
    _, extended = _make_plane_with_edit_retry(edit_retry_plugin)

    result = _run(
        extended.on_tool_error_callback(
            tool=_FakeTool("Bash"),
            tool_args={"command": "ls"},
            tool_context=_FakeCtx("inv-bash"),
            error=ValueError("nonzero_exit"),
        )
    )

    assert result is None


def test_on_tool_error_no_plugins_returns_none() -> None:
    """An empty plane (no controls) returns None — transparent pass-through."""
    plane = ControlPlane()
    extended = _ExtendedControlPlanePlugin(plane, resilience_plugin=None)

    result = _run(
        extended.on_tool_error_callback(
            tool=_FakeTool("FileEdit"),
            tool_args={},
            tool_context=_FakeCtx(),
            error=ValueError("old_text_not_found"),
        )
    )

    assert result is None


def test_on_tool_error_first_non_none_wins() -> None:
    """When two adapters implement on_tool_error_callback, the first non-None wins."""

    class _FirstPlugin:
        name = "first"

        async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
            return {"from": "first"}

        async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
            return None

    class _SecondPlugin:
        name = "second"
        called = False

        async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
            _SecondPlugin.called = True
            return {"from": "second"}

        async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
            return None

    from magi_agent.adk_bridge.control_plane import BaseLoopControl

    class _FirstAdapter(BaseLoopControl):
        name = "first_adapter"

        def __init__(self) -> None:
            self._plugin = _FirstPlugin()

        async def on_after_tool(self, *, tool, args, tool_context, result):
            return None

    class _SecondAdapter(BaseLoopControl):
        name = "second_adapter"

        def __init__(self) -> None:
            self._plugin = _SecondPlugin()

        async def on_after_tool(self, *, tool, args, tool_context, result):
            return None

    plane = ControlPlane()
    plane.register(_FirstAdapter())
    plane.register(_SecondAdapter())
    extended = _ExtendedControlPlanePlugin(plane, resilience_plugin=None)

    result = _run(
        extended.on_tool_error_callback(
            tool=_FakeTool("AnyTool"),
            tool_args={},
            tool_context=_FakeCtx(),
            error=ValueError("boom"),
        )
    )

    assert result == {"from": "first"}
    assert not _SecondPlugin.called, "second plugin must not be called after first returns non-None"


# ---------------------------------------------------------------------------
# 3. End-to-end: plane + _ExtendedControlPlanePlugin through ADK Runner
#
# This mirrors the structure of tests/test_edit_retry_reflection_wiring.py but
# wires the plugin through the control plane as it is in production.
# ---------------------------------------------------------------------------


from collections.abc import AsyncGenerator

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types


_APP_NAME = "magi-plane-error-itest"
_APP_IDENTIFIER = "magi_plane_error_itest"
_USER_ID = "user-plane-error"


class _ScriptedFileEditLlm(BaseLlm):
    """Fake LLM that issues one FileEdit (which will raise), then finishes.

    Captures the function_response the model sees on the second model call so
    tests can assert on corrective injection content.
    """

    model: str = "magi-scripted-file-edit-llm"

    def model_post_init(self, _context: object) -> None:
        object.__setattr__(self, "_calls", 0)
        object.__setattr__(self, "tool_results_seen", [])

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        object.__setattr__(self, "_calls", self._calls + 1)
        call_index = self._calls

        # Capture every function_response this model call can see.
        for content in llm_request.contents or ():
            for part in content.parts or ():
                fr = getattr(part, "function_response", None)
                if fr is not None and fr.response is not None:
                    self.tool_results_seen.append(dict(fr.response))

        if call_index == 1:
            # First call: issue a FileEdit that will raise ValueError.
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name="FileEdit",
                            args={"path": "src/app.py", "oldText": "old", "newText": "new"},
                        )
                    ],
                )
            )
            return

        # Second call: finish (the corrective guidance already injected).
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="edit done")],
            )
        )


def _file_edit_raises():
    def FileEdit(path: str = "", oldText: str = "", newText: str = "") -> dict:
        raise ValueError("old_text_not_found")

    return FileEdit


def _build_plane_runner(
    edit_retry_plugin: MagiEditRetryReflectionPlugin | None,
) -> tuple[Runner, _ScriptedFileEditLlm]:
    llm = _ScriptedFileEditLlm()
    agent = Agent(
        name="magi_plane_error_agent",
        model=llm,
        instruction="plane on_tool_error integration test agent",
        tools=[FunctionTool(_file_edit_raises())],
    )

    if edit_retry_plugin is not None:
        plane = ControlPlane()
        plane.register(_EditRetryLoopControl(edit_retry_plugin))
        plugin = _ExtendedControlPlanePlugin(plane, resilience_plugin=None)
    else:
        plugin = None

    app = App(
        name=_APP_IDENTIFIER,
        root_agent=agent,
        plugins=[plugin] if plugin is not None else [],
    )
    runner = Runner(
        app=app,
        app_name=_APP_NAME,
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        artifact_service=InMemoryArtifactService(),
    )
    return runner, llm


async def _run_turn(runner: Runner) -> list[object]:
    session = await runner.session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID
    )
    events: list[object] = []
    async for event in runner.run_async(
        user_id=_USER_ID,
        session_id=session.id,
        new_message=types.Content(
            role="user", parts=[types.Part(text="edit the file")]
        ),
    ):
        events.append(event)
    return events


def test_plane_raises_path_injects_corrective_message() -> None:
    """Core regression guard: FileEdit raises ValueError under the plane ->
    corrective reflection response is injected into the next model turn.

    This is the PRIMARY production path: gate5b FileEdit raises ValueError
    (not returns an error dict), so on_tool_error_callback is the live seam.
    Without the fix (_ExtendedControlPlanePlugin missing on_tool_error_callback),
    the injection never happened and the ValueError propagated unhandled.
    """
    edit_retry_plugin = _make_edit_retry_plugin(max_attempts=2)
    runner, llm = _build_plane_runner(edit_retry_plugin)

    asyncio.run(_run_turn(runner))

    injected = [
        r
        for r in llm.tool_results_seen
        if isinstance(r, dict)
        and r.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    ]
    assert injected, (
        f"expected corrective reflection response via plane on_tool_error_callback, "
        f"saw tool_results_seen={llm.tool_results_seen}"
    )
    guidance = injected[0]["reflection_guidance"]
    assert "old_string was not found" in guidance
    assert injected[0]["error_type"] == "edit_apply_failed"
    assert injected[0]["retry_attempt"] == 1


def test_plane_raises_path_no_plugin_propagates_error() -> None:
    """Without the plugin under the plane, the ValueError is NOT intercepted."""
    runner, llm = _build_plane_runner(None)

    raised = False
    try:
        asyncio.run(_run_turn(runner))
    except ValueError as exc:
        raised = exc.args[0] == "old_text_not_found"

    injected = [
        r
        for r in llm.tool_results_seen
        if isinstance(r, dict)
        and r.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    ]
    assert injected == []
    assert raised is True


def test_plane_raises_path_budget_exhausted_propagates_error() -> None:
    """max_attempts=1 -> budget exhausted on first attempt -> ValueError propagates."""
    edit_retry_plugin = _make_edit_retry_plugin(max_attempts=1)
    runner, llm = _build_plane_runner(edit_retry_plugin)

    raised = False
    try:
        asyncio.run(_run_turn(runner))
    except ValueError as exc:
        raised = exc.args[0] == "old_text_not_found"

    injected = [
        r
        for r in llm.tool_results_seen
        if isinstance(r, dict)
        and r.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
    ]
    assert injected == [], "budget=1 must fail closed immediately (no injection)"
    assert raised is True


# ---------------------------------------------------------------------------
# 4. Generic tool-exception reflection through build_default_plugin
#
# MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED=1 -> a generic (non-edit) tool raise
# is converted into a corrective dict; flag unset -> byte-identical fan-out
# (returns None, the error propagates as today).
# ---------------------------------------------------------------------------


def test_build_default_plugin_flag_on_reflects_generic_tool_raise() -> None:
    from magi_agent.adk_bridge.control_plane import build_default_plugin
    from magi_agent.adk_bridge.tool_exception_reflection import (
        TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE,
    )

    plugin = build_default_plugin({"MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "1"})

    result = _run(
        plugin.on_tool_error_callback(
            tool=_FakeTool("Bash"),
            tool_args={"command": "ls"},
            tool_context=_FakeCtx("inv-generic-on"),
            error=ValueError("command exploded"),
        )
    )

    assert result is not None, "flag-on must convert a generic tool raise"
    assert result["response_type"] == TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE
    assert result["status"] == "error"
    assert result["error_type"] == "ValueError"
    assert "command exploded" in result["error_message"]


def test_build_default_plugin_flag_unset_generic_tool_raise_returns_none() -> None:
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    plugin = build_default_plugin({})

    result = _run(
        plugin.on_tool_error_callback(
            tool=_FakeTool("Bash"),
            tool_args={"command": "ls"},
            tool_context=_FakeCtx("inv-generic-off"),
            error=ValueError("command exploded"),
        )
    )

    assert result is None, "flag unset must keep byte-identical fan-out (None)"


def test_build_default_plugin_edit_retry_keeps_priority_over_generic() -> None:
    """When both reflections are on, FileEdit raises keep the specialized
    edit-retry response (the generic plugin hard-skips edit tools)."""
    from magi_agent.adk_bridge.control_plane import build_default_plugin

    plugin = build_default_plugin(
        {
            "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
            "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "1",
        }
    )

    result = _run(
        plugin.on_tool_error_callback(
            tool=_FakeTool("FileEdit"),
            tool_args={"path": "a.py", "oldText": "old", "newText": "new"},
            tool_context=_FakeCtx("inv-priority"),
            error=ValueError("old_text_not_found"),
        )
    )

    assert result is not None
    assert result.get("response_type") == EDIT_RETRY_REFLECTION_RESPONSE_TYPE
