"""Tests for MaxStepsBrakeControl — profile-aware default-ON, behind MAGI_MAX_STEPS_BRAKE_ENABLED.

Verifies:
- With flag on and iteration at the final step, on_before_model injects the wrap-up
  message into llm_request.contents and clears tools.
- With flag explicitly OFF ("0"), control is not registered in the plane.
- With flag unset (no profile), profile default ON applies: control IS registered.
- Before the final iteration, no mutation occurs.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.adk_bridge.control_plane import (
    CONTROL_PLANE_PLUGIN_NAME,
    MaxStepsBrakeControl,
)
from magi_agent.adk_bridge.local_runner import LocalInertLlm, LOCAL_INERT_MODEL_NAME
from magi_agent.runtime.turn_policy import MAX_STEPS_WRAP_UP_MESSAGE


def _inert_model_factory(_cfg):
    return LocalInertLlm(model=LOCAL_INERT_MODEL_NAME)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# MaxStepsBrakeControl directly
# ---------------------------------------------------------------------------


class _FakeLlmRequest:
    """Fake LlmRequest-like with mutable contents and config."""

    def __init__(self, contents=None, tools=None):
        self.contents = list(contents or [])
        self.config = _FakeConfig(tools=list(tools or []))


class _FakeConfig:
    def __init__(self, tools=None):
        self.tools = list(tools or [])


class _FakeCallbackContext:
    pass


def _has_wrap_up(contents) -> bool:
    """Check if any content item contains the wrap-up message."""
    for c in contents:
        if isinstance(c, dict) and c.get("content") == MAX_STEPS_WRAP_UP_MESSAGE:
            return True
        # ADK genai Content object: check parts text.
        parts = getattr(c, "parts", None) or []
        for p in parts:
            text = getattr(p, "text", None)
            if text == MAX_STEPS_WRAP_UP_MESSAGE:
                return True
    return False


def test_brake_fires_on_final_iteration() -> None:
    """On the last allowed iteration, wrap-up message injected and tools cleared."""
    ctrl = MaxStepsBrakeControl(max_iterations=5, iteration=4)  # 4 == 5-1 (final)
    request = _FakeLlmRequest(
        contents=[{"role": "user", "content": "initial"}],
        tools=[{"type": "function", "name": "Read"}],
    )

    _run(ctrl.on_before_model(callback_context=_FakeCallbackContext(), llm_request=request))

    # Wrap-up message was appended (as genai Content or dict).
    assert _has_wrap_up(request.contents)
    # Tools were cleared.
    assert request.config.tools == []


def test_brake_does_not_fire_before_final_iteration() -> None:
    """On a non-final iteration, no mutation."""
    ctrl = MaxStepsBrakeControl(max_iterations=5, iteration=3)  # 3 < 4 (not final)
    initial = [{"role": "user", "content": "initial"}]
    request = _FakeLlmRequest(
        contents=list(initial),
        tools=[{"type": "function", "name": "Read"}],
    )

    _run(ctrl.on_before_model(callback_context=_FakeCallbackContext(), llm_request=request))

    assert request.contents == initial  # unchanged
    assert len(request.config.tools) == 1  # unchanged


def test_brake_fires_when_iteration_exceeds_max() -> None:
    """Beyond-final iterations also fire the brake."""
    ctrl = MaxStepsBrakeControl(max_iterations=3, iteration=10)
    request = _FakeLlmRequest(
        contents=[],
        tools=[{"type": "function", "name": "Read"}],
    )

    _run(ctrl.on_before_model(callback_context=_FakeCallbackContext(), llm_request=request))

    assert _has_wrap_up(request.contents)
    assert request.config.tools == []


def test_brake_with_empty_tools_brake_applied_but_no_tools_disabled() -> None:
    """Brake fires even when tools list is empty (tools_disabled=False case)."""
    ctrl = MaxStepsBrakeControl(max_iterations=3, iteration=2)
    request = _FakeLlmRequest(contents=[], tools=[])

    _run(ctrl.on_before_model(callback_context=_FakeCallbackContext(), llm_request=request))

    # Wrap-up injected even with empty tools.
    assert _has_wrap_up(request.contents)


def test_brake_max_iterations_zero_no_op() -> None:
    """max_iterations=0 is treated as disabled (no brake)."""
    ctrl = MaxStepsBrakeControl(max_iterations=0, iteration=0)
    request = _FakeLlmRequest(
        contents=[{"role": "user", "content": "initial"}],
        tools=[{"type": "function"}],
    )
    initial_len = len(request.contents)

    _run(ctrl.on_before_model(callback_context=_FakeCallbackContext(), llm_request=request))

    assert len(request.contents) == initial_len  # no wrap-up injected


# ---------------------------------------------------------------------------
# MaxStepsBrakeControl does NOT run on real LlmRequest (ADK objects)
# — integration: plane receives mutation via contents + config.tools
# ---------------------------------------------------------------------------


def test_brake_clears_adk_llm_request_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """With real ADK LlmRequest, tools in config are cleared on the final step."""
    from google.adk.models import LlmRequest
    from google.genai import types

    ctrl = MaxStepsBrakeControl(max_iterations=3, iteration=2)

    req = LlmRequest()
    req.contents = []
    # LlmRequest.config.tools is a list of Tool objects or dicts; we use a minimal
    # representation that survives the clear (list attribute).
    if req.config is None:
        from google.adk.models.llm_request import LlmConfig
        req.config = LlmConfig()

    # Patch tools onto config as a plain list (simulate presence).
    req.config.tools = [types.Tool(function_declarations=[])]

    _run(ctrl.on_before_model(callback_context=None, llm_request=req))

    assert req.config.tools == [] or req.config.tools is None


# ---------------------------------------------------------------------------
# Flag-gated registration in build_default_plane
# ---------------------------------------------------------------------------


def test_max_steps_brake_not_registered_when_flag_explicitly_off() -> None:
    # Explicit "0" overrides the profile default ON.
    from magi_agent.adk_bridge.control_plane import build_default_plane

    plane = build_default_plane(os_environ={"MAGI_MAX_STEPS_BRAKE_ENABLED": "0"})
    control_names = {c.name for c in plane._controls}
    assert not any("max_steps" in name for name in control_names)


def test_max_steps_brake_registered_by_profile_default() -> None:
    # Unset (no explicit value, no safe profile) takes the profile default ON.
    from magi_agent.adk_bridge.control_plane import build_default_plane

    plane = build_default_plane(os_environ={})
    control_names = {c.name for c in plane._controls}
    assert any("max_steps" in name for name in control_names)


def test_max_steps_brake_registered_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_MAX_STEPS_BRAKE_ENABLED", "1")

    from magi_agent.adk_bridge.control_plane import build_default_plane

    plane = build_default_plane(os_environ={"MAGI_MAX_STEPS_BRAKE_ENABLED": "1"})
    control_names = {c.name for c in plane._controls}
    assert any("max_steps" in name for name in control_names)


# ---------------------------------------------------------------------------
# Verify MAGI_MAX_STEPS_BRAKE_ENABLED default is OFF in both runners
# ---------------------------------------------------------------------------


def test_max_steps_brake_not_in_local_runner_when_explicitly_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    monkeypatch.setenv("MAGI_MAX_STEPS_BRAKE_ENABLED", "0")

    from magi_agent.adk_bridge import local_runner

    bundle = local_runner.build_local_adk_runner()
    plane_plugin = next(
        p for p in bundle.runner.plugin_manager.plugins
        if p.name == CONTROL_PLANE_PLUGIN_NAME
    )
    control_names = {c.name for c in plane_plugin._p._controls}
    assert not any("max_steps" in name for name in control_names)


# ---------------------------------------------------------------------------
# Phase 5 Task 5.1: typed-context entry point (template for the seam migrations)
# ---------------------------------------------------------------------------


def test_max_steps_brake_reads_request_via_typed_context() -> None:
    """apply_before_model takes a ControlPlaneContext; behavior is identical to
    on_before_model — wrap-up injected and tools cleared on the final iteration."""
    from magi_agent.packs.context import ControlPlaneContext

    ctrl = MaxStepsBrakeControl(max_iterations=2, iteration=1)  # final iteration
    req = _FakeLlmRequest(
        contents=[{"role": "user", "content": "go"}],
        tools=[{"type": "function", "name": "Read"}],
    )
    ctx = ControlPlaneContext.minimal()
    _run(ctrl.apply_before_model(ctx, llm_request=req))

    assert _has_wrap_up(req.contents)
    assert req.config.tools == []


def test_max_steps_brake_typed_context_not_final_is_noop() -> None:
    """Before the final iteration the typed-context path mutates nothing."""
    from magi_agent.packs.context import ControlPlaneContext

    ctrl = MaxStepsBrakeControl(max_iterations=5, iteration=2)  # not final
    initial = [{"role": "user", "content": "go"}]
    req = _FakeLlmRequest(contents=list(initial), tools=[{"type": "function"}])
    _run(ctrl.apply_before_model(ControlPlaneContext.minimal(), llm_request=req))

    assert req.contents == initial
    assert len(req.config.tools) == 1


def test_max_steps_brake_on_before_model_delegates_to_apply() -> None:
    """on_before_model is a thin delegate; the legacy ADK hook still works."""
    ctrl = MaxStepsBrakeControl(max_iterations=3, iteration=2)
    req = _FakeLlmRequest(contents=[], tools=[{"type": "function", "name": "Read"}])
    _run(ctrl.on_before_model(callback_context=_FakeCallbackContext(), llm_request=req))

    assert _has_wrap_up(req.contents)
    assert req.config.tools == []


def test_max_steps_brake_not_in_real_runner_when_explicitly_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_MAX_STEPS_BRAKE_ENABLED", "0")

    from magi_agent.cli.providers import ProviderConfig
    from magi_agent.cli.real_runner import build_cli_model_runner

    cli_runner = build_cli_model_runner(
        ProviderConfig(provider="openai", model="gpt-4o", api_key="x"),
        model_factory=_inert_model_factory,
        tools=[],
        instruction="test",
    )
    plane_plugin = next(
        p for p in cli_runner._runner.plugin_manager.plugins
        if p.name == CONTROL_PLANE_PLUGIN_NAME
    )
    control_names = {c.name for c in plane_plugin._p._controls}
    assert not any("max_steps" in name for name in control_names)
