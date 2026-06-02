from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from google.adk.agents import Agent

from magi_agent.harness.resolved import build_default_resolved_harness_state
from magi_agent.hooks.bus import HookBus, HookBusRunResult, RegisteredHook
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.tools.manifest import ToolSource


ADK_CALLBACK_MAPPING = {
    "before_agent_callback": HookPoint.BEFORE_TURN_START,
    "after_agent_callback": HookPoint.AFTER_TURN_END,
    "before_model_callback": HookPoint.BEFORE_LLM_CALL,
    "after_model_callback": HookPoint.AFTER_LLM_CALL,
    "on_model_error_callback": HookPoint.ON_ERROR,
    "before_tool_callback": HookPoint.BEFORE_TOOL_USE,
    "after_tool_callback": HookPoint.AFTER_TOOL_USE,
    "on_tool_error_callback": HookPoint.ON_ERROR,
}


@dataclass
class RecordingHookBus:
    result: HookBusRunResult
    calls: list[tuple[HookPoint, HookContext]]

    async def run_async(
        self,
        *,
        point: HookPoint,
        context: HookContext,
        harness_state: object,
    ) -> HookBusRunResult:
        self.calls.append((point, context))
        return self.result


def continue_result() -> HookBusRunResult:
    harness_state = build_default_resolved_harness_state()
    return HookBusRunResult(
        final_action="continue",
        results=(),
        observation={
            "effective_hooks": (),
            "skipped_by_scope": (),
            "failed_open": (),
            "failed_closed": (),
            "blocked_by": (),
        },
        harness_state=harness_state,
    )


def block_result() -> HookBusRunResult:
    harness_state = build_default_resolved_harness_state()
    return HookBusRunResult(
        final_action="block",
        results=(HookResult(action="block", reason="denied"),),
        observation={
            "effective_hooks": ("safetyGate",),
            "skipped_by_scope": (),
            "failed_open": (),
            "failed_closed": (),
            "blocked_by": ("safetyGate",),
        },
        harness_state=harness_state,
    )


def context_factory(invocation: object) -> HookContext:
    callback_name = getattr(invocation, "callback_name")
    return HookContext(bot_id="bot-1", turn_id=callback_name)


def manifest(name: str, *, blocking: bool = True) -> HookManifest:
    return HookManifest(
        name=name,
        point=HookPoint.BEFORE_TOOL_USE,
        description=f"{name} hook",
        source=ToolSource(kind="builtin", package="test"),
        priority=0,
        blocking=blocking,
        fail_open=False,
    )


async def invoke_callback(name: str, callback: Callable[..., object]) -> object:
    if name in {"before_agent_callback", "after_agent_callback"}:
        return await callback(callback_context="callback-context")
    if name == "before_model_callback":
        return await callback(callback_context="callback-context", llm_request="llm-request")
    if name == "after_model_callback":
        return await callback(callback_context="callback-context", llm_response="llm-response")
    if name == "on_model_error_callback":
        return await callback(
            callback_context="callback-context",
            llm_request="llm-request",
            error=RuntimeError("model"),
        )
    if name == "before_tool_callback":
        return await callback(
            tool="tool",
            args={"path": "SOUL.md"},
            tool_context="tool-context",
        )
    if name == "after_tool_callback":
        return await callback(
            tool="tool",
            args={"path": "SOUL.md"},
            tool_context="tool-context",
            tool_response={"ok": True},
        )
    if name == "on_tool_error_callback":
        return await callback(
            tool="tool",
            args={"path": "SOUL.md"},
            tool_context="tool-context",
            error=RuntimeError("tool"),
        )
    raise AssertionError(f"unexpected callback name {name}")


def expected_payload_for(name: str) -> Mapping[str, Any]:
    if name == "before_agent_callback":
        return {
            "callback_context": "callback-context",
            "tool_context": None,
            "model_request": None,
            "model_response": None,
            "tool": None,
            "tool_args": None,
            "tool_result": None,
            "error_type": None,
            "raw_args": (),
            "raw_kwargs": {"callback_context": "callback-context"},
        }
    if name == "after_agent_callback":
        return {
            "callback_context": "callback-context",
            "tool_context": None,
            "model_request": None,
            "model_response": None,
            "tool": None,
            "tool_args": None,
            "tool_result": None,
            "error_type": None,
            "raw_args": (),
            "raw_kwargs": {"callback_context": "callback-context"},
        }
    if name == "before_model_callback":
        return {
            "callback_context": "callback-context",
            "tool_context": None,
            "model_request": "llm-request",
            "model_response": None,
            "tool": None,
            "tool_args": None,
            "tool_result": None,
            "error_type": None,
            "raw_args": (),
            "raw_kwargs": {
                "callback_context": "callback-context",
                "llm_request": "llm-request",
            },
        }
    if name == "after_model_callback":
        return {
            "callback_context": "callback-context",
            "tool_context": None,
            "model_request": None,
            "model_response": "llm-response",
            "tool": None,
            "tool_args": None,
            "tool_result": None,
            "error_type": None,
            "raw_args": (),
            "raw_kwargs": {
                "callback_context": "callback-context",
                "llm_response": "llm-response",
            },
        }
    if name == "on_model_error_callback":
        return {
            "callback_context": "callback-context",
            "tool_context": None,
            "model_request": "llm-request",
            "model_response": None,
            "tool": None,
            "tool_args": None,
            "tool_result": None,
            "error_type": RuntimeError,
            "raw_args": (),
            "raw_kwargs": {
                "callback_context": "callback-context",
                "llm_request": "llm-request",
                "error_type": RuntimeError,
            },
        }
    if name == "before_tool_callback":
        return {
            "callback_context": None,
            "tool_context": "tool-context",
            "model_request": None,
            "model_response": None,
            "tool": "tool",
            "tool_args": {"path": "SOUL.md"},
            "tool_result": None,
            "error_type": None,
            "raw_args": (),
            "raw_kwargs": {
                "tool": "tool",
                "args": {"path": "SOUL.md"},
                "tool_context": "tool-context",
            },
        }
    if name == "after_tool_callback":
        return {
            "callback_context": None,
            "tool_context": "tool-context",
            "model_request": None,
            "model_response": None,
            "tool": "tool",
            "tool_args": {"path": "SOUL.md"},
            "tool_result": {"ok": True},
            "error_type": None,
            "raw_args": (),
            "raw_kwargs": {
                "tool": "tool",
                "args": {"path": "SOUL.md"},
                "tool_context": "tool-context",
                "tool_response": {"ok": True},
            },
        }
    if name == "on_tool_error_callback":
        return {
            "callback_context": None,
            "tool_context": "tool-context",
            "model_request": None,
            "model_response": None,
            "tool": "tool",
            "tool_args": {"path": "SOUL.md"},
            "tool_result": None,
            "error_type": RuntimeError,
            "raw_args": (),
            "raw_kwargs": {
                "tool": "tool",
                "args": {"path": "SOUL.md"},
                "tool_context": "tool-context",
                "error_type": RuntimeError,
            },
        }
    raise AssertionError(f"unexpected callback name {name}")


def assert_invocation_payload(invocation: object, expected: Mapping[str, Any]) -> None:
    assert getattr(invocation, "callback_context") == expected["callback_context"]
    assert getattr(invocation, "tool_context") == expected["tool_context"]
    assert getattr(invocation, "model_request") == expected["model_request"]
    assert getattr(invocation, "model_response") == expected["model_response"]
    assert getattr(invocation, "tool") == expected["tool"]
    assert getattr(invocation, "tool_args") == expected["tool_args"]
    assert getattr(invocation, "tool_result") == expected["tool_result"]
    error = getattr(invocation, "error")
    error_type = expected["error_type"]
    if error_type is None:
        assert error is None
    else:
        assert isinstance(error, error_type)
    assert getattr(invocation, "raw_args") == expected["raw_args"]
    raw_kwargs = dict(getattr(invocation, "raw_kwargs"))
    expected_raw_kwargs = dict(expected["raw_kwargs"])
    expected_error_type = expected_raw_kwargs.pop("error_type", None)
    raw_error = raw_kwargs.pop("error", None)
    if expected_error_type is None:
        assert raw_error is None
    else:
        assert isinstance(raw_error, expected_error_type)
    assert raw_kwargs == expected_raw_kwargs


def test_all_adk_callbacks_map_to_openmagi_hook_points() -> None:
    from magi_agent.adk_bridge.callback_adapter import build_adk_callback_adapter

    calls: list[tuple[HookPoint, HookContext]] = []
    invocations: list[object] = []

    def recording_context_factory(invocation: object) -> HookContext:
        invocations.append(invocation)
        return context_factory(invocation)

    bus = RecordingHookBus(result=continue_result(), calls=calls)
    adapter = build_adk_callback_adapter(
        hook_bus=bus,
        hook_context_factory=recording_context_factory,
        harness_state=build_default_resolved_harness_state(),
    )

    for callback_name, expected_point in ADK_CALLBACK_MAPPING.items():
        assert adapter.mapping[callback_name] is expected_point
        assert asyncio.run(invoke_callback(callback_name, adapter.callbacks[callback_name])) is None
        assert calls[-1][0] is expected_point
        assert calls[-1][1].turn_id == callback_name
        assert_invocation_payload(invocations[-1], expected_payload_for(callback_name))


def test_official_adk_agent_accepts_adapter_callbacks_without_runner() -> None:
    from magi_agent.adk_bridge.callback_adapter import build_adk_callback_adapter

    adapter = build_adk_callback_adapter(
        hook_bus=RecordingHookBus(result=continue_result(), calls=[]),
        hook_context_factory=context_factory,
        harness_state=build_default_resolved_harness_state(),
    )

    agent = Agent(
        name="CallbackBoundaryAgent",
        model="gemini-2.5-flash",
        instruction="Use OpenMagi callback boundary.",
        **adapter.callbacks,
    )

    assert agent.name == "CallbackBoundaryAgent"


@pytest.mark.parametrize(
    "final_action",
    ("block", "replace", "skip", "pending_control_request"),
)
def test_before_tool_non_continue_actions_raise_explicit_openmagi_exception(
    final_action: str,
) -> None:
    from magi_agent.adk_bridge.callback_adapter import (
        OpenMagiAdkCallbackBlocked,
        build_adk_callback_adapter,
    )

    harness_state = build_default_resolved_harness_state()
    hook_result = (
        HookResult(action="permission_decision", decision="ask", reason="denied")
        if final_action == "pending_control_request"
        else HookResult(action=final_action, reason="denied")
    )
    result = HookBusRunResult(
        final_action=final_action,
        results=(hook_result,),
        observation={
            "effective_hooks": ("safetyGate",),
            "skipped_by_scope": (),
            "failed_open": (),
            "failed_closed": (),
            "blocked_by": ("safetyGate",),
        },
        harness_state=harness_state,
    )
    adapter = build_adk_callback_adapter(
        hook_bus=RecordingHookBus(result=result, calls=[]),
        hook_context_factory=context_factory,
        harness_state=build_default_resolved_harness_state(),
    )

    with pytest.raises(OpenMagiAdkCallbackBlocked) as exc_info:
        asyncio.run(
            adapter.callbacks["before_tool_callback"](
                tool="tool",
                args={"path": "SOUL.md"},
                tool_context="tool-context",
            )
        )

    assert exc_info.value.callback_name == "before_tool_callback"
    assert exc_info.value.hook_point is HookPoint.BEFORE_TOOL_USE
    assert exc_info.value.run_result.final_action == final_action


def test_non_blocking_observer_does_not_delay_callback_and_telemetry_fails_open() -> None:
    async def scenario() -> None:
        from magi_agent.adk_bridge.callback_adapter import build_adk_callback_adapter

        observer_started = asyncio.Event()
        release_observer = asyncio.Event()

        async def observer(_: HookContext) -> HookResult:
            observer_started.set()
            await release_observer.wait()
            raise RuntimeError("observer failed")

        bus = HookBus(
            hooks=(RegisteredHook(manifest=manifest("observer", blocking=False), handler=observer),)
        )
        adapter = build_adk_callback_adapter(
            hook_bus=bus,
            hook_context_factory=context_factory,
            harness_state=build_default_resolved_harness_state(),
        )

        result = await asyncio.wait_for(
            adapter.callbacks["before_tool_callback"](
                tool="tool",
                args={},
                tool_context="tool-context",
            ),
            timeout=0.05,
        )

        assert result is None
        await asyncio.wait_for(observer_started.wait(), timeout=0.2)
        assert bus.drain_observer_telemetry() == ()

        release_observer.set()
        drained = ()
        for _ in range(20):
            await asyncio.sleep(0.01)
            drained = bus.drain_observer_telemetry()
            if drained:
                break

        assert drained[0].source_hook == "observer"
        assert drained[0].status == "failed_open"

    asyncio.run(scenario())


def test_callback_adapter_import_does_not_connect_runner_traffic_or_routes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.adk_bridge.callback_adapter")
forbidden_modules = (
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"unexpected modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
