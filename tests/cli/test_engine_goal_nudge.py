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

    return asyncio.run(_collect())


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
        """When real evidence satisfies required_evidence, no nudge is fired (goal mode).

        Drives the full real path: evidence_collector → _collect_evidence →
        _goal_is_met → real FinalOutputGate evaluation → goal met → no nudge.
        No monkeypatching of _goal_is_met.
        """
        satisfying_records = [
            {
                "type": "SourceInspection",
                "sourceRef": "web:example.com",
                "evidenceRef": "ev:0001:evidence_record",
            }
        ]

        def fake_collector(turn_id: str) -> list[object]:
            return satisfying_records  # type: ignore[return-value]

        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="research complete", mode="goal", max_nudges=3,
                          required_evidence=("source_ledger",))
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=4096,
            user_id="cli",
            goal_nudge=nudge,
            evidence_collector=fake_collector,
        )
        _run_drive(driver)
        # Evidence satisfied via real FinalOutputGate → goal_is_met True → no nudge
        assert len(runner.calls) == 1

    def test_no_nudge_when_goal_met_grind_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even grind mode stops nudging when real evidence satisfies the goal.

        Drives the full real path: evidence_collector → _collect_evidence →
        _goal_is_met → real FinalOutputGate evaluation → goal met → no nudge.
        No monkeypatching of _goal_is_met. Grind mode would otherwise nudge to
        max_nudges (5), so a single call proves the real gate suppressed it.
        """
        satisfying_records = [
            {
                "type": "SourceInspection",
                "sourceRef": "web:example.com",
                "evidenceRef": "ev:0001:evidence_record",
            }
        ]

        def fake_collector(turn_id: str) -> list[object]:
            return satisfying_records  # type: ignore[return-value]

        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="x", mode="grind", max_nudges=5,
                          required_evidence=("source_ledger",))
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=4096,
            user_id="cli",
            goal_nudge=nudge,
            evidence_collector=fake_collector,
        )
        _run_drive(driver)
        # Evidence satisfied via real FinalOutputGate → goal_is_met True → no nudge
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


# ---------------------------------------------------------------------------
# Tests: evidence_collector DI seam — end-to-end coverage
# ---------------------------------------------------------------------------


class TestEvidenceCollectorDISeam:
    """Verify the evidence_collector constructor param wires through correctly.

    These tests exercise the full end-to-end path:
      evidence_collector callable → _collect_evidence → goal_is_met → nudge decision

    without monkeypatching _goal_is_met, so the real FinalOutputGate is exercised.
    """

    def test_collector_providing_satisfying_evidence_suppresses_nudge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When evidence_collector returns records satisfying required_evidence,
        goal_is_met returns True and no nudge is fired.

        Uses a SourceInspection record (satisfies 'source_ledger') so the real
        FinalOutputGate evaluates to done (no missing_required_evidence reason code).
        """
        # A SourceInspection record satisfies the "source_ledger" check.
        satisfying_records = [
            {
                "type": "SourceInspection",
                "sourceRef": "web:example.com",
                "evidenceRef": "ev:0001:evidence_record",
            }
        ]

        def fake_collector(turn_id: str) -> list[object]:
            return satisfying_records  # type: ignore[return-value]

        runner = FakeRunner(events_per_call=[[]])  # single clean stop
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(
            goal="research done",
            mode="goal",
            max_nudges=3,
            required_evidence=("source_ledger",),
        )
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=4096,
            user_id="cli",
            goal_nudge=nudge,
            evidence_collector=fake_collector,
        )
        _run_drive(driver)

        # Evidence satisfied → goal_is_met True → no nudge → only the initial run
        assert len(runner.calls) == 1

    def test_collector_returning_empty_nudges_to_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When evidence_collector is provided but returns no records, required_evidence
        is declared but absent → goal_is_met False → nudges proceed to max_nudges cap.
        """
        def empty_collector(turn_id: str) -> list[object]:
            return []

        max_n = 2
        runner = FakeRunner(events_per_call=[[] for _ in range(max_n + 2)])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(
            goal="research done",
            mode="grind",
            max_nudges=max_n,
            required_evidence=("source_ledger",),
        )
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=4096,
            user_id="cli",
            goal_nudge=nudge,
            evidence_collector=empty_collector,
        )
        _run_drive(driver)

        # Evidence absent → goal never met → grind to cap: initial + max_n nudges
        assert len(runner.calls) == max_n + 1

    def test_collector_none_with_required_evidence_nudges_to_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default (evidence_collector=None) + required_evidence non-empty →
        _collect_evidence returns () → goal_is_met False → nudges to cap.

        This confirms the default (collector=None) path is byte-identical to
        pre-seam behaviour.
        """
        max_n = 2
        runner = FakeRunner(events_per_call=[[] for _ in range(max_n + 2)])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(
            goal="task done",
            mode="grind",
            max_nudges=max_n,
            required_evidence=("source_ledger",),
        )
        # No evidence_collector provided — uses the default None path
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=4096,
            user_id="cli",
            goal_nudge=nudge,
        )
        _run_drive(driver)

        # No collector → () → goal never met → grind to cap
        assert len(runner.calls) == max_n + 1

    def test_collector_called_with_correct_turn_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The evidence_collector receives the turn_id from the current turn."""
        collected_turn_ids: list[str] = []

        satisfying_records = [
            {
                "type": "SourceInspection",
                "sourceRef": "web:example.com",
                "evidenceRef": "ev:0001:evidence_record",
            }
        ]

        def recording_collector(turn_id: str) -> list[object]:
            collected_turn_ids.append(turn_id)
            return satisfying_records  # type: ignore[return-value]

        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(
            goal="check done",
            mode="goal",
            max_nudges=3,
            required_evidence=("source_ledger",),
        )
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=4096,
            user_id="cli",
            goal_nudge=nudge,
            evidence_collector=recording_collector,
        )
        _run_drive(driver, prompt="do work")

        # Collector was called at least once (at the stop-check)
        assert len(collected_turn_ids) >= 1
        # The turn_id passed was the one from the turn input ("test-turn" is the
        # default in _run_drive's turn_input dict)
        assert all(tid == "test-turn" for tid in collected_turn_ids)


# ---------------------------------------------------------------------------
# Tests: error during nudge re-invocation
# ---------------------------------------------------------------------------


class FakeRunnerWithError:
    """Runner whose N-th run_async call raises instead of yielding events.

    The first ``clean_calls`` calls yield ``events_for_clean_calls`` (one list
    per call); subsequent calls raise ``error``.
    """

    def __init__(
        self,
        *,
        events_for_clean_calls: list[list[dict[str, Any]]],
        error: Exception,
    ) -> None:
        self.calls: list[FakeRunnerCall] = []
        self._events_for_clean = events_for_clean_calls
        self._error = error
        self._call_index = 0
        self.agent = None

    def run_async(self, *, user_id: str, session_id: str, invocation_id: str, new_message: Any) -> AsyncIterator[Any]:
        parts = getattr(new_message, "parts", None) or []
        text = ""
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                text = t
                break
        self.calls.append(FakeRunnerCall(invocation_id=invocation_id, new_message_text=text))
        idx = self._call_index
        self._call_index += 1
        if idx < len(self._events_for_clean):
            return FakeRunner._make_aiter(self._events_for_clean[idx])
        # Beyond clean calls → raise
        return _FakeErrorStream(self._error)


class _FakeErrorStream:
    """Async iterator that raises on first __anext__ call."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def __aiter__(self) -> "_FakeErrorStream":
        return self

    async def __anext__(self) -> Any:
        raise self._error

    async def aclose(self) -> None:
        pass


class TestNudgeRunError:
    def test_nudge_run_error_after_prior_output_is_terminal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the FIRST run yields output (triggering a nudge) and the NUDGE
        re-invocation RAISES, the result must be Terminal.error — NOT retried.

        Correctness rationale
        ---------------------
        The recovery guard in ``_drive`` is:

            should_retry = (
                self._recovery is not None
                and yielded_events == 0        # <-- ALL events across all invocations
                and attempt_yielded == 0
                and recovery_attempts < self._recovery.max_attempts
            )

        After the first clean run emitted at least one event, ``yielded_events > 0``.
        Therefore ``should_retry`` is ``False`` even when ``self._recovery`` is set —
        the engine cannot safely re-run without risking double-emission of already-
        delivered output.  The error surfaces immediately as ``Terminal.error``.

        This test locks in that boundary: a nudge that errors after prior output
        produced is terminal, not silently swallowed and not retried.
        """
        # First call: yields one text event (prior output = 1 event) + clean stop.
        # Second call (nudge): raises immediately.
        runner = FakeRunnerWithError(
            events_for_clean_calls=[[{"type": "text_delta", "text": "partial output"}]],
            error=RuntimeError("nudge invocation exploded"),
        )
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="finish the job", mode="goal", max_nudges=3, required_evidence=())
        driver, _ = _make_driver(runner, goal_nudge=nudge)
        items = _run_drive(driver)

        # Two run_async calls: the initial (clean) + one nudge attempt (error).
        assert len(runner.calls) == 2, (
            f"Expected 2 run_async calls (initial + 1 nudge), got {len(runner.calls)}"
        )

        # The nudge re-invocation raised → engine_error is set → Terminal.error.
        terminal = items[-1]
        assert terminal.terminal == Terminal.error, (
            f"Expected Terminal.error after nudge-run exception, got {terminal.terminal!r}"
        )
        assert terminal.error is not None and "exploded" in terminal.error, (
            f"Expected error message to contain 'exploded', got {terminal.error!r}"
        )
