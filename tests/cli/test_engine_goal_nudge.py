"""Tests for PR4 — GoalNudge integration in MagiEngineDriver._drive (TDD).

Uses a fake ADK adapter/runner (no real ADK import) to verify:
- goal_nudge=None → no extra runs (byte-identical parity with today)
- mode="goal" + evidence missing → exactly ONE nudge per stop (latch), then break
- mode="grind" + unmet → re-nudges up to max_nudges, then stops (hard cap)
- goal met (evidence present) → no nudge fired
- tool firing resets the goal latch (re-arm for mode="goal")
- nudge message threaded as the next run_async newMessage
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.contracts import Terminal
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.goal_nudge import GoalNudge, build_nudge_message


# ---------------------------------------------------------------------------
# Fake ADK infrastructure
# ---------------------------------------------------------------------------


@dataclass
class FakeRunnerCall:
    """Record of a single run_async call."""
    invocation_id: str
    new_message_text: str


@dataclass
class _FakeEvent:
    """Minimal stand-in for an ADK event yielded by run_async."""
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)

    # Make duck-typing work with OpenMagiEventBridge.project_adk_event
    def __init__(self, event_type: str, **kwargs: Any) -> None:
        self.event_type = event_type
        self.payload = kwargs


# A sentinel so we can tell the fake runner to yield a tool event.
_TOOL_EVENT = "tool_event"
_TEXT_EVENT = "text_event"


class FakeRunner:
    """Fake runner that records run_async calls and yields configurable events."""

    def __init__(self, *, events_per_call: list[list[dict[str, Any]]] | None = None) -> None:
        """
        events_per_call: list of event-lists, one per successive run_async call.
            Each event dict has "type" key (projected public type).
            If None, yields no events (empty turn) each call.
        """
        self.calls: list[FakeRunnerCall] = []
        self._events_per_call: list[list[dict[str, Any]]] = events_per_call or []
        self._call_index = 0
        self.agent = None  # no gate wiring needed

    def _events_for_this_call(self) -> list[dict[str, Any]]:
        if self._call_index < len(self._events_per_call):
            events = self._events_per_call[self._call_index]
        else:
            events = []
        self._call_index += 1
        return events

    def run_async(self, *, user_id: str, session_id: str, invocation_id: str, new_message: Any) -> AsyncIterator[Any]:
        # Extract the text from the Content object
        parts = getattr(new_message, "parts", None) or []
        text = ""
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                text = t
                break
        self.calls.append(FakeRunnerCall(invocation_id=invocation_id, new_message_text=text))
        events = self._events_for_this_call()
        return self._make_aiter(events)

    @staticmethod
    def _make_aiter(events: list[dict[str, Any]]) -> AsyncIterator[Any]:
        """Return an async iterator over fake ADK events."""
        return _FakeADKStream(events)


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
    """Minimal ADK event that the bridge can project."""
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict


# ---------------------------------------------------------------------------
# Fake ADK dependencies (bridge + adapter wiring)
# ---------------------------------------------------------------------------


class FakeEventBridge:
    """Fake OpenMagiEventBridge that converts _FakeADKEvent → public events."""

    def project_adk_event(self, adk_event: object, *, turn_id: str) -> Any:
        event_dict = getattr(adk_event, "_event_dict", {})
        # Return a projection with agent_events
        return _FakeBridgeResult(event_dict)


class _FakeBridgeResult:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict

    @property
    def agent_events(self) -> list[dict[str, Any]]:
        if not self._event_dict:
            return []
        return [self._event_dict]


def _fake_sanitize(d: dict[str, Any]) -> dict[str, Any] | None:
    """Fake _sanitize_agent_event — passes everything through."""
    return d if d else None


class FakeRunnerTurnInputCls:
    """Fake RunnerTurnInput constructor that stores args for inspection."""

    def __new__(cls, **kwargs: Any) -> "FakeRunnerTurnInputCls":  # type: ignore[misc]
        obj = object.__new__(cls)
        for k, v in kwargs.items():
            setattr(obj, k, v)
        return obj


class FakeRunnerAdapter:
    def __init__(self, *, runner: FakeRunner) -> None:
        self.runner = runner

    def run_turn(self, runner_input: Any) -> AsyncIterator[Any]:
        new_message = getattr(runner_input, "newMessage", None)
        user_id = getattr(runner_input, "userId", "cli")
        session_id = getattr(runner_input, "sessionId", "s")
        invocation_id = getattr(runner_input, "invocationId", "t")
        return self.runner.run_async(
            user_id=user_id,
            session_id=session_id,
            invocation_id=invocation_id,
            new_message=new_message,
        )


# ---------------------------------------------------------------------------
# Helper: build a driver with patched lazy deps
# ---------------------------------------------------------------------------


def _make_driver(
    runner: FakeRunner,
    goal_nudge: GoalNudge | None = None,
    max_event_count: int = 4096,
) -> tuple[MagiEngineDriver, FakeRunner]:
    driver = MagiEngineDriver(
        runner=runner,
        max_event_count=max_event_count,
        user_id="cli",
        goal_nudge=goal_nudge,
    )
    return driver, runner


def _patch_lazy_deps(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    """Monkey-patch _lazy_engine_deps to return our fake deps."""
    import magi_agent.cli.engine as engine_mod
    from types import SimpleNamespace

    # We need google.genai.types-like objects
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

    fake_deps = {
        "types": FakeTypes(),
        "OpenMagiEventBridge": lambda **kwargs: FakeEventBridge(),
        "OpenMagiRunnerAdapter": lambda **kwargs: FakeRunnerAdapter(runner=runner),
        "RunnerTurnInput": FakeRunnerTurnInputCls,
        "sanitize_agent_event": _fake_sanitize,
    }
    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", lambda: fake_deps)


def _run_drive(driver: MagiEngineDriver, *, prompt: str = "do the thing") -> list[Any]:
    """Run _drive synchronously and collect all yielded items."""

    async def _collect() -> list[Any]:
        cancel = asyncio.Event()
        items = []
        async for item in driver.run_turn_stream(
            runtime=None,
            turn_input={"prompt": prompt, "session_id": "test-session", "turn_id": "test-turn"},
            cancel=cancel,
        ):
            items.append(item)
        return items

    return asyncio.get_event_loop().run_until_complete(_collect())


# ---------------------------------------------------------------------------
# Tests: goal_nudge=None → parity (no extra runs)
# ---------------------------------------------------------------------------


class TestGoalNudgeNone:
    def test_no_goal_nudge_single_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With goal_nudge=None, the driver runs exactly once regardless of content."""
        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        driver, _ = _make_driver(runner, goal_nudge=None)
        items = _run_drive(driver, prompt="test prompt")

        # Exactly one run_async call
        assert len(runner.calls) == 1
        # Terminal result is completed
        terminal = items[-1]
        assert terminal.terminal == Terminal.completed

    def test_no_goal_nudge_prompt_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        driver, _ = _make_driver(runner, goal_nudge=None)
        _run_drive(driver, prompt="hello world")

        assert runner.calls[0].new_message_text == "hello world"

    def test_no_goal_nudge_with_events_still_single_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = FakeRunner(events_per_call=[[{"type": "text_delta", "text": "done"}]])
        _patch_lazy_deps(monkeypatch, runner)
        driver, _ = _make_driver(runner, goal_nudge=None)
        _run_drive(driver)

        assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# Tests: mode="goal" — exactly one nudge per stop, latch
# ---------------------------------------------------------------------------


class TestGoalModeOnce:
    def test_goal_mode_no_evidence_fires_one_nudge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """mode="goal" + no evidence → exactly 1 nudge, then break on second stop."""
        runner = FakeRunner(events_per_call=[
            [],   # initial call: no events, clean stop
            [],   # nudge call: no events, clean stop → latch fires, break
        ])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="write tests", mode="goal", max_nudges=3, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        items = _run_drive(driver)

        # Initial + exactly 1 nudge = 2 run_async calls total
        assert len(runner.calls) == 2
        # Second call's message is the nudge
        assert "write tests" in runner.calls[1].new_message_text
        # Before finishing (goal mode phrasing)
        assert "Before finishing" in runner.calls[1].new_message_text
        # Final result is completed (not error)
        assert items[-1].terminal == Terminal.completed

    def test_goal_mode_latch_prevents_second_nudge_per_stop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After the first nudge stop in goal mode, the latch fires and we break."""
        runner = FakeRunner(events_per_call=[
            [],  # initial stop
            [],  # nudge stop → latch fires → break (no third call)
            [],  # this would be a third call if the latch weren't working
        ])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="x", mode="goal", max_nudges=5, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        _run_drive(driver)

        # Must be exactly 2 (initial + 1 nudge), NOT 3
        assert len(runner.calls) == 2

    def test_goal_mode_status_event_emitted_before_nudge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A goal_nudge status event must be yielded before re-invoking."""
        runner = FakeRunner(events_per_call=[[], []])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="x", mode="goal", max_nudges=3, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        items = _run_drive(driver)

        # Find the goal_nudge status event
        nudge_events = [
            i for i in items
            if isinstance(i, RuntimeEvent) and i.type == "status"
            and isinstance(i.payload, dict)
            and i.payload.get("type") == "goal_nudge"
        ]
        assert len(nudge_events) == 1
        assert nudge_events[0].payload["mode"] == "goal"
        assert nudge_events[0].payload["nudge"] == 1
        assert nudge_events[0].payload["max"] == 3


# ---------------------------------------------------------------------------
# Tests: mode="grind" — re-nudges up to max_nudges
# ---------------------------------------------------------------------------


class TestGrindMode:
    def test_grind_mode_fires_up_to_max_nudges(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """mode="grind" with max_nudges=3 → initial + 3 nudges = 4 total calls."""
        max_n = 3
        runner = FakeRunner(events_per_call=[[] for _ in range(max_n + 1 + 1)])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="keep going", mode="grind", max_nudges=max_n, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        _run_drive(driver)

        # 1 initial + max_n nudges = max_n + 1 total calls
        assert len(runner.calls) == max_n + 1

    def test_grind_mode_stops_at_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After max_nudges, grind stops even if goal unmet."""
        max_n = 2
        runner = FakeRunner(events_per_call=[[] for _ in range(max_n + 5)])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="x", mode="grind", max_nudges=max_n, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        items = _run_drive(driver)

        assert len(runner.calls) == max_n + 1
        assert items[-1].terminal == Terminal.completed

    def test_grind_mode_uses_grind_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Grind nudge messages use the 'Keep working' phrasing."""
        runner = FakeRunner(events_per_call=[[], [], []])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="finish the task", mode="grind", max_nudges=2, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        _run_drive(driver)

        # All nudge calls (index 1 onward) use grind phrasing
        for call in runner.calls[1:]:
            assert "Keep working" in call.new_message_text
            assert "finish the task" in call.new_message_text

    def test_grind_mode_emits_status_events_for_each_nudge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One goal_nudge status event per nudge re-invocation."""
        max_n = 3
        runner = FakeRunner(events_per_call=[[] for _ in range(max_n + 2)])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="x", mode="grind", max_nudges=max_n, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        items = _run_drive(driver)

        nudge_events = [
            i for i in items
            if isinstance(i, RuntimeEvent) and i.type == "status"
            and isinstance(i.payload, dict)
            and i.payload.get("type") == "goal_nudge"
        ]
        assert len(nudge_events) == max_n
        for idx, ev in enumerate(nudge_events, start=1):
            assert ev.payload["nudge"] == idx
            assert ev.payload["mode"] == "grind"


# ---------------------------------------------------------------------------
# Tests: tool firing resets the latch
# ---------------------------------------------------------------------------


class TestToolResetsLatch:
    def test_tool_event_resets_goal_latch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A tool event in a run re-arms the goal latch for mode='goal'.

        Sequence:
        1. Initial run: tool fires → latch resets. Then stop.
        2. Nudge fires (latch was reset by tool), goal_check_pending=True.
        3. Second nudge stop: latch is set → break.
        Total calls: 1 (initial) + 2 (nudge×2 with latch reset after first) ← wait,
        let's trace carefully:

        Initial run (goal_check_pending=False):
            - tool fires → goal_check_pending=False (reset)
            - clean stop → goal_check_pending NOT set yet; nudge fires → goal_check_pending=True; nudge 1
        Nudge call 1 (goal_check_pending=True):
            - no tool → clean stop → latch is set → BREAK

        So total = 2 calls (initial + 1 nudge).
        The key point: the tool reset the latch so a nudge was allowed after the initial stop.
        Compare to no-tool: initial stop with no prior tool → same result (1 nudge).

        Now test the RESET EFFECT: tool in a nudge run re-arms for another nudge.
        Sequence:
        1. Initial run: clean stop → nudge 1 (goal_check_pending=True)
        2. Nudge 1 run: tool fires → latch resets (goal_check_pending=False) → clean stop → nudge 2 allowed
        3. Nudge 2 run: no tool → clean stop → latch fires → BREAK
        Total = 3 calls.
        """
        runner = FakeRunner(events_per_call=[
            [],  # initial: clean stop
            [{"type": "tool_start", "id": "t1", "name": "bash"}],  # nudge 1: tool fires
            [],  # nudge 2: no tool, clean stop → latch → break
        ])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="deploy", mode="goal", max_nudges=5, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        _run_drive(driver)

        # 3 calls: initial + 2 nudges (tool reset latch in nudge 1 → nudge 2 allowed)
        assert len(runner.calls) == 3


# ---------------------------------------------------------------------------
# Tests: goal met → no nudge
# ---------------------------------------------------------------------------


class TestGoalMetNoNudge:
    def test_no_nudge_when_evidence_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When goal_is_met returns True, no nudge is fired."""
        # We test this by using required_evidence=("source_ledger",) and
        # monkeypatching goal_is_met to return True.
        import magi_agent.cli.engine as engine_mod

        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="research complete", mode="goal", max_nudges=3,
                          required_evidence=("source_ledger",))
        driver, _ = _make_driver(runner, goal_nudge=nudge)

        # Patch goal_is_met to always return True
        monkeypatch.setattr(engine_mod, "_goal_is_met", lambda *a, **kw: True)

        _run_drive(driver)
        # Only initial run — no nudge
        assert len(runner.calls) == 1

    def test_no_nudge_when_goal_met_grind_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even grind mode stops nudging when goal is met."""
        import magi_agent.cli.engine as engine_mod

        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="x", mode="grind", max_nudges=5,
                          required_evidence=("source_ledger",))
        driver, _ = _make_driver(runner, goal_nudge=nudge)

        monkeypatch.setattr(engine_mod, "_goal_is_met", lambda *a, **kw: True)
        _run_drive(driver)
        assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# Tests: nudge message as newMessage
# ---------------------------------------------------------------------------


class TestNudgeMessageThreaded:
    def test_nudge_message_is_exact_build_nudge_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The re-invocation newMessage must be build_nudge_message(nudge)."""
        runner = FakeRunner(events_per_call=[[], []])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="implement feature X", mode="goal", max_nudges=3,
                          required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        _run_drive(driver)

        expected = build_nudge_message(nudge)
        assert runner.calls[1].new_message_text == expected

    def test_grind_nudge_message_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = FakeRunner(events_per_call=[[], []])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="deploy all services", mode="grind", max_nudges=1,
                          required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        _run_drive(driver)

        expected = build_nudge_message(nudge)
        assert runner.calls[1].new_message_text == expected
