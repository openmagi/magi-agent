"""Engine-level integration for the user-hook HookBus bridge (cluster doc 11 PR2).

Verifies ``MagiEngineDriver`` wiring:
- ``user_hook_bus=None`` (gate ``MAGI_USER_HOOKS_ENABLED`` OFF) -> no bridge
  attached, the agent's tool callbacks are untouched (byte-identical to today).
- a real HookBus -> the before/after-tool bridges are attached around the turn
  and RESTORED in the ``finally`` (no leak onto the per-runner agent).
- the bridge sits AFTER any pre-existing gate callback (conflict-matrix order).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.contracts import Terminal
from magi_agent.hooks.bus import HookBus, RegisteredHook
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult
from magi_agent.tools.manifest import ToolSource

_SOURCE = ToolSource(kind="builtin", package="test.fixtures")


# ---------------------------------------------------------------------------
# Fake ADK harness (mirrors tests/cli/test_engine_goal_nudge.py, trimmed) but
# with an AGENT that owns before/after tool callbacks so the bridge can attach.
# ---------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self) -> None:
        self.before_tool_callback = None
        self.after_tool_callback = None


class _FakeADKStream:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self._index = 0

    def __aiter__(self) -> "_FakeADKStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return _FakeADKEvent(event)

    async def aclose(self) -> None:
        pass


class _FakeADKEvent:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict


class FakeRunner:
    def __init__(self, *, agent: _FakeAgent | None = None) -> None:
        self.calls = 0
        self.agent = agent
        # Snapshot of the agent's before_tool_callback observed DURING the run.
        self.before_during_run: Any = None

    def run_async(self, *, user_id, session_id, invocation_id, new_message) -> AsyncIterator[Any]:
        self.calls += 1
        if self.agent is not None:
            self.before_during_run = self.agent.before_tool_callback
        return _FakeADKStream([])


class FakeEventBridge:
    def project_adk_event(self, adk_event: object, *, turn_id: str) -> Any:
        return _FakeBridgeResult(getattr(adk_event, "_event_dict", {}))


class _FakeBridgeResult:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict

    @property
    def agent_events(self) -> list[dict[str, Any]]:
        return [self._event_dict] if self._event_dict else []


def _fake_sanitize(d: dict[str, Any]) -> dict[str, Any] | None:
    return d if d else None


class FakeRunnerTurnInputCls:
    def __new__(cls, **kwargs: Any) -> "FakeRunnerTurnInputCls":  # type: ignore[misc]
        obj = object.__new__(cls)
        for k, v in kwargs.items():
            setattr(obj, k, v)
        return obj


class FakeRunnerAdapter:
    def __init__(self, *, runner: FakeRunner) -> None:
        self.runner = runner

    def run_turn(self, runner_input: Any) -> AsyncIterator[Any]:
        return self.runner.run_async(
            user_id=getattr(runner_input, "userId", "cli"),
            session_id=getattr(runner_input, "sessionId", "s"),
            invocation_id=getattr(runner_input, "invocationId", "t"),
            new_message=getattr(runner_input, "newMessage", None),
        )


class FakeContent:
    def __init__(self, *, role: str, parts: list) -> None:
        self.role = role
        self.parts = parts


class FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class FakeTypes:
    Content = FakeContent
    Part = FakePart


def _patch_lazy_deps(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    import magi_agent.cli.engine as engine_mod

    fake_deps = {
        "types": FakeTypes(),
        "OpenMagiEventBridge": lambda **kwargs: FakeEventBridge(),
        "OpenMagiRunnerAdapter": lambda **kwargs: FakeRunnerAdapter(runner=runner),
        "RunnerTurnInput": FakeRunnerTurnInputCls,
        "sanitize_agent_event": _fake_sanitize,
    }
    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", lambda: fake_deps)


def _run(driver: MagiEngineDriver) -> list[Any]:
    async def _collect() -> list[Any]:
        cancel = asyncio.Event()
        items: list[Any] = []
        async for item in driver.run_turn_stream(
            runtime=None,
            turn_input={"prompt": "go", "session_id": "s1", "turn_id": "t1"},
            cancel=cancel,
        ):
            items.append(item)
        return items

    return asyncio.run(_collect())


def _bus(point: HookPoint, handler) -> HookBus:
    manifest = HookManifest(
        name=f"hook-{point.value}", point=point, description="test", source=_SOURCE
    )
    return HookBus(hooks=(RegisteredHook(manifest=manifest, handler=handler),))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_gate_off_no_bus_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """user_hook_bus=None -> agent callbacks untouched, turn completes."""
    agent = _FakeAgent()
    runner = FakeRunner(agent=agent)
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(runner=runner, user_hook_bus=None)
    items = _run(driver)
    assert items[-1].terminal == Terminal.completed
    # No bridge attached at any point.
    assert runner.before_during_run is None
    assert agent.before_tool_callback is None
    assert agent.after_tool_callback is None


def test_agentless_runner_with_bus_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agentless MockRunner stays green even with a bus set."""
    runner = FakeRunner(agent=None)
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(runner=runner, user_hook_bus=HookBus())
    items = _run(driver)
    assert items[-1].terminal == Terminal.completed


def test_bridge_attached_during_run_and_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a bus, the before-tool bridge is attached DURING the run and the
    agent's callbacks are restored (to None) afterwards."""
    observed: list[str] = []

    def _observer(ctx):
        observed.append(ctx.turn_id or "")
        return HookResult(action="continue")

    agent = _FakeAgent()
    runner = FakeRunner(agent=agent)
    _patch_lazy_deps(monkeypatch, runner)
    bus = _bus(HookPoint.BEFORE_TOOL_USE, _observer)
    driver = MagiEngineDriver(runner=runner, user_hook_bus=bus)
    items = _run(driver)
    assert items[-1].terminal == Terminal.completed
    # The bridge was present while the runner ran.
    assert isinstance(runner.before_during_run, list)
    assert len(runner.before_during_run) == 1
    # Restored to the original (None) after the turn — no leak.
    assert agent.before_tool_callback is None
    assert agent.after_tool_callback is None


def test_bridge_preserves_existing_gate_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pre-existing gate callback on the agent stays FIRST; the hook bridge is
    appended after it, then both are removed on restore."""

    async def _gate_cb(*, tool, args, tool_context=None):
        return None

    agent = _FakeAgent()
    agent.before_tool_callback = _gate_cb
    runner = FakeRunner(agent=agent)
    _patch_lazy_deps(monkeypatch, runner)
    bus = HookBus()
    driver = MagiEngineDriver(runner=runner, user_hook_bus=bus)
    _run(driver)
    # During the run the gate must precede the bridge.
    during = runner.before_during_run
    assert isinstance(during, list)
    assert during[0] is _gate_cb
    assert len(during) == 2
    # After the turn, restored to just the original gate callback.
    assert agent.before_tool_callback is _gate_cb
