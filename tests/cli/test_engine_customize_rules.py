"""Engine-level integration for the customize tool-boundary bridge (N-01).

Verifies ``MagiEngineDriver`` wiring of
:mod:`magi_agent.cli.customize_tool_wiring`:

- all four customize master flags OFF -> no bridge attached, the agent's tool
  callbacks are untouched (byte-identical to today),
- a customize flag ON -> the before/after-tool bridges are attached AROUND the
  turn and RESTORED in the ``finally`` (no leak onto the per-runner agent),
- the customize bridge sits AFTER any gate and user-hook callbacks
  (conflict-matrix order: gate -> user hook -> customize rules).

The harness mirrors ``tests/cli/test_engine_user_hooks.py`` (a fake ADK runner
whose agent owns before/after tool callbacks). The callback-list ordering
contract (first non-None wins for a before callback) is documented on
``engine.py`` ``_build_gate_before_tool``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.customize.store import set_custom_rule
from magi_agent.hooks.bus import HookBus


# ---------------------------------------------------------------------------
# Fake ADK harness (mirrors tests/cli/test_engine_user_hooks.py).
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
        self.before_during_run: Any = None
        self.after_during_run: Any = None

    def run_async(self, *, user_id, session_id, invocation_id, new_message) -> AsyncIterator[Any]:
        self.calls += 1
        if self.agent is not None:
            self.before_during_run = self.agent.before_tool_callback
            self.after_during_run = self.agent.after_tool_callback
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


def _run(driver: MagiEngineDriver, *, gate: object | None = None) -> list[Any]:
    async def _collect() -> list[Any]:
        cancel = asyncio.Event()
        items: list[Any] = []
        async for item in driver.run_turn_stream(
            runtime=None,
            turn_input={"prompt": "go", "session_id": "s1", "turn_id": "t1"},
            cancel=cancel,
            gate=gate,
        ):
            items.append(item)
        return items

    return asyncio.run(_collect())


class _DummyGate:
    """Non-None gate whose ``check`` is never called (no tools fire)."""

    def __init__(self) -> None:
        self.rules = None


def _prompt_injection_rule(*, rid: str = "cr_eng_inject") -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": "before_tool_use",
        "action": "audit",
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "command",
                "value": " --dry-run",
                "condition": {"tool": "shell_exec"},
            },
        },
    }


@pytest.fixture
def customize_on(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_prompt_injection_rule(), path=cfile)
    return cfile


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_flags_off_agent_callbacks_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No customize flag ON -> no bridge attached, callbacks stay None.

    Explicitly zeroes every per-slot flag so the test is hermetic under the
    full-profile overlay (which now seeds MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED=1
    and peers ON by default).
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "0")
    agent = _FakeAgent()
    runner = FakeRunner(agent=agent)
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(runner=runner, user_hook_bus=None)
    items = _run(driver)
    assert items[-1].terminal == Terminal.completed
    assert runner.before_during_run is None
    assert runner.after_during_run is None
    assert agent.before_tool_callback is None
    assert agent.after_tool_callback is None


def test_customize_bridge_attached_during_run_and_restored(
    monkeypatch: pytest.MonkeyPatch, customize_on
) -> None:
    """A customize flag ON -> the bridge is attached DURING the run and the
    agent's callbacks are restored (to None) afterwards."""
    agent = _FakeAgent()
    runner = FakeRunner(agent=agent)
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(runner=runner, user_hook_bus=None)
    items = _run(driver)
    assert items[-1].terminal == Terminal.completed
    # The bridge was present while the runner ran.
    assert isinstance(runner.before_during_run, list)
    assert len(runner.before_during_run) == 1
    assert (
        runner.before_during_run[-1].__module__
        == "magi_agent.cli.customize_tool_wiring"
    )
    assert isinstance(runner.after_during_run, list)
    assert len(runner.after_during_run) == 1
    # Restored to the original (None) after the turn -- no leak.
    assert agent.before_tool_callback is None
    assert agent.after_tool_callback is None


def test_customize_bridge_appended_after_gate_and_user_hooks(
    monkeypatch: pytest.MonkeyPatch, customize_on
) -> None:
    """With gate + user hook + customize all active, the before list order is
    [gate, user hook, customize] and every layer is removed on restore."""
    agent = _FakeAgent()
    runner = FakeRunner(agent=agent)
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(runner=runner, user_hook_bus=HookBus())
    _run(driver, gate=_DummyGate())

    during = runner.before_during_run
    assert isinstance(during, list)
    assert len(during) == 3
    assert during[0].__module__ == "magi_agent.engine.driver"
    assert during[1].__module__ == "magi_agent.cli.hook_wiring"
    assert during[2].__module__ == "magi_agent.cli.customize_tool_wiring"
    # After the turn the agent is fully restored.
    assert agent.before_tool_callback is None
    assert agent.after_tool_callback is None
