"""Tests for R2 — empty-response recovery + budget grace in MagiEngineDriver._drive.

Uses the same fake ADK adapter/runner harness as test_engine_goal_nudge.py
(no real ADK import) to verify:

- flag ON + tools-ran-but-no-text → exactly one corrective re-invocation with
  build_empty_response_message(), one 'empty_response_recovery' status event,
  and the second attempt's text reaches the consumer;
- flag OFF (config None, the default) → single invocation, no status event
  (byte-identical regression guard);
- recovery attempt also empty → no third invocation (max_recoveries=1);
- attempt with text → no recovery;
- event-budget exhaustion → exactly one grace re-invocation with
  build_grace_message(), and the grace attempt may stream events past the
  original cap (allowance added to the cap, not reset);
- goal-nudge + recovery both on → the recovery branch wins the empty stop and
  goal-nudge accounting is unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver, build_empty_response_recovery_config
from magi_agent.runtime.empty_response_recovery import (
    EmptyResponseRecoveryConfig,
    build_empty_response_message,
    build_grace_message,
)
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.goal_nudge import GoalNudge, build_nudge_message


# ---------------------------------------------------------------------------
# Fake ADK infrastructure (mirrors tests/cli/test_engine_goal_nudge.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeRunnerCall:
    invocation_id: str
    new_message_text: str


class FakeRunner:
    """Fake runner that records run_async calls and yields configurable events."""

    def __init__(
        self, *, events_per_call: list[list[dict[str, Any]]] | None = None
    ) -> None:
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

    def run_async(
        self,
        *,
        user_id: str,
        session_id: str,
        invocation_id: str,
        new_message: Any,
    ) -> AsyncIterator[Any]:
        parts = getattr(new_message, "parts", None) or []
        text = ""
        for p in parts:
            t = getattr(p, "text", None)
            if t:
                text = t
                break
        self.calls.append(
            FakeRunnerCall(invocation_id=invocation_id, new_message_text=text)
        )
        return _FakeADKStream(self._events_for_this_call())


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


class FakeEventBridge:
    def project_adk_event(self, adk_event: object, *, turn_id: str) -> Any:
        return _FakeBridgeResult(getattr(adk_event, "_event_dict", {}))


class _FakeBridgeResult:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict

    @property
    def agent_events(self) -> list[dict[str, Any]]:
        if not self._event_dict:
            return []
        return [self._event_dict]


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


def _patch_lazy_deps(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    import magi_agent.cli.engine as engine_mod

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
    async def _collect() -> list[Any]:
        cancel = asyncio.Event()
        items = []
        async for item in driver.run_turn_stream(
            runtime=None,
            turn_input={
                "prompt": prompt,
                "session_id": "test-session",
                "turn_id": "test-turn",
            },
            cancel=cancel,
        ):
            items.append(item)
        return items

    return asyncio.run(_collect())


def _status_events(items: list[Any], status_type: str) -> list[RuntimeEvent]:
    return [
        i
        for i in items
        if isinstance(i, RuntimeEvent)
        and i.type == "status"
        and isinstance(i.payload, dict)
        and i.payload.get("type") == status_type
    ]


_TOOL_EVENTS = [
    {"type": "tool_start", "id": "t1", "name": "bash"},
    {"type": "tool_end", "id": "t1", "name": "bash"},
]
_TEXT_EVENT = {"type": "text_delta", "delta": "the final answer"}


_CFG = EmptyResponseRecoveryConfig(enabled=True, max_recoveries=1)


# ---------------------------------------------------------------------------
# (a) flag ON: tools ran, no text → exactly one corrective re-invocation
# ---------------------------------------------------------------------------


class TestEmptyResponseRecoveryFires:
    def test_recovery_reinvokes_once_with_corrective_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[list(_TOOL_EVENTS), [dict(_TEXT_EVENT)]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_CFG)
        items = _run_drive(driver)

        assert len(runner.calls) == 2
        assert runner.calls[1].new_message_text == build_empty_response_message()

        status = _status_events(items, "empty_response_recovery")
        assert len(status) == 1
        assert status[0].payload["recovery"] == 1
        assert status[0].payload["max"] == 1

        # The second attempt's text reached the consumer.
        texts = [
            i
            for i in items
            if isinstance(i, RuntimeEvent)
            and isinstance(i.payload, dict)
            and i.payload.get("type") == "text_delta"
        ]
        assert any(
            e.payload.get("delta") == "the final answer" for e in texts
        )
        assert items[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# (b) flag OFF (default) → byte-identical single invocation
# ---------------------------------------------------------------------------


class TestFlagOffParity:
    def test_default_off_single_invocation_no_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)])
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner)  # config defaults to None
        items = _run_drive(driver)

        assert len(runner.calls) == 1
        assert _status_events(items, "empty_response_recovery") == []
        assert _status_events(items, "empty_response_grace") == []
        assert items[-1].terminal == Terminal.completed

    def test_disabled_config_single_invocation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)])
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            empty_response_recovery=EmptyResponseRecoveryConfig(enabled=False),
        )
        items = _run_drive(driver)

        assert len(runner.calls) == 1
        assert _status_events(items, "empty_response_recovery") == []


# ---------------------------------------------------------------------------
# (c) recovery attempt also empty → no third invocation (budget = 1)
# ---------------------------------------------------------------------------


class TestRecoveryBudget:
    def test_no_second_recovery_when_retry_also_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[list(_TOOL_EVENTS), list(_TOOL_EVENTS), [dict(_TEXT_EVENT)]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_CFG)
        items = _run_drive(driver)

        # Initial + ONE recovery; the third configured call must never happen.
        assert len(runner.calls) == 2
        assert len(_status_events(items, "empty_response_recovery")) == 1
        assert items[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# (d) attempt with text → no recovery
# ---------------------------------------------------------------------------


class TestNoRecoveryWhenTextSeen:
    def test_tools_plus_text_is_a_normal_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[list(_TOOL_EVENTS) + [dict(_TEXT_EVENT)]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_CFG)
        items = _run_drive(driver)

        assert len(runner.calls) == 1
        assert _status_events(items, "empty_response_recovery") == []

    def test_text_only_stop_is_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[[dict(_TEXT_EVENT)]])
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_CFG)
        items = _run_drive(driver)

        assert len(runner.calls) == 1
        assert _status_events(items, "empty_response_recovery") == []


# ---------------------------------------------------------------------------
# (e) grace: budget exhaustion → one grace re-invocation, allowance honored
# ---------------------------------------------------------------------------


class TestBudgetGrace:
    def test_grace_reinvokes_once_after_budget_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # max_event_count=2: attempt 1's two tool events exhaust the budget
        # with no text. The grace attempt streams THREE events (2 past the
        # original cap) — proving the allowance is added to the cap, not reset.
        grace_events = [
            {"type": "tool_start", "id": "g1", "name": "bash"},
            {"type": "tool_end", "id": "g1", "name": "bash"},
            dict(_TEXT_EVENT),
        ]
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS), grace_events])
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=2,
            empty_response_recovery=EmptyResponseRecoveryConfig(
                enabled=True, max_recoveries=1, grace_event_allowance=64
            ),
        )
        items = _run_drive(driver)

        assert len(runner.calls) == 2
        assert runner.calls[1].new_message_text == build_grace_message()

        status = _status_events(items, "empty_response_grace")
        assert len(status) == 1

        # All three grace-attempt events were streamed (allowance honored).
        texts = [
            i
            for i in items
            if isinstance(i, RuntimeEvent)
            and isinstance(i.payload, dict)
            and i.payload.get("delta") == "the final answer"
        ]
        assert len(texts) == 1
        assert items[-1].terminal == Terminal.completed

    def test_no_grace_when_flag_off_budget_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS), [dict(_TEXT_EVENT)]])
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, max_event_count=2)
        items = _run_drive(driver)

        assert len(runner.calls) == 1
        assert _status_events(items, "empty_response_grace") == []

    def test_only_one_grace_ever(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Grace attempt ALSO exhausts the (raised) cap with no text → no second
        # grace. (graces_used == 1 blocks it.)
        runner = FakeRunner(
            events_per_call=[
                list(_TOOL_EVENTS),
                [
                    {"type": "tool_start", "id": f"g{i}", "name": "bash"}
                    for i in range(70)
                ],
                [dict(_TEXT_EVENT)],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=2,
            empty_response_recovery=EmptyResponseRecoveryConfig(
                enabled=True, max_recoveries=1, grace_event_allowance=64
            ),
        )
        items = _run_drive(driver)

        # Initial + 1 grace only — the third configured call never happens.
        assert len(runner.calls) == 2
        assert len(_status_events(items, "empty_response_grace")) == 1


# ---------------------------------------------------------------------------
# (f) goal-nudge + recovery both on → recovery wins the empty stop
# ---------------------------------------------------------------------------


class TestOrderingVsGoalNudge:
    def test_recovery_branch_wins_empty_stop_then_nudge_accounting_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Attempt 1: tools, no text → must be consumed by RECOVERY (specific
        # corrective message), NOT by goal-nudge. Attempt 2: text → normal
        # stop → goal-nudge evaluates as usual (goal unmet → one nudge).
        runner = FakeRunner(
            events_per_call=[list(_TOOL_EVENTS), [dict(_TEXT_EVENT)], []]
        )
        _patch_lazy_deps(monkeypatch, runner)
        nudge = GoalNudge(goal="finish", mode="goal", max_nudges=3, required_evidence=())
        driver = MagiEngineDriver(
            runner=runner,
            goal_nudge=nudge,
            empty_response_recovery=_CFG,
        )
        items = _run_drive(driver)

        assert len(runner.calls) == 3
        # The empty stop got the recovery message, not the nudge.
        assert runner.calls[1].new_message_text == build_empty_response_message()
        # Goal-nudge accounting unchanged: exactly one nudge afterwards.
        assert runner.calls[2].new_message_text == build_nudge_message(nudge)

        recovery_status = _status_events(items, "empty_response_recovery")
        nudge_status = _status_events(items, "goal_nudge")
        assert len(recovery_status) == 1
        assert len(nudge_status) == 1
        assert nudge_status[0].payload["nudge"] == 1


# ---------------------------------------------------------------------------
# build_empty_response_recovery_config (env → config wiring helper)
# ---------------------------------------------------------------------------


class TestBuildConfigFromEnv:
    def test_explicit_off_returns_none(self) -> None:
        # Explicit "0" disables recovery regardless of profile.
        assert (
            build_empty_response_recovery_config(
                {"MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "0"}
            )
            is None
        )

    def test_unset_profile_default_on_returns_config(self) -> None:
        # Unset under a non-safe profile: profile default ON gives a config.
        cfg = build_empty_response_recovery_config({})
        assert cfg is not None
        assert cfg.enabled is True

    def test_safe_runtime_profile_keeps_off(self) -> None:
        # Safe profile ("eval") keeps the profile default OFF.
        assert (
            build_empty_response_recovery_config({"MAGI_RUNTIME_PROFILE": "eval"})
            is None
        )

    def test_enabled_returns_config(self) -> None:
        cfg = build_empty_response_recovery_config(
            {
                "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
                "MAGI_EMPTY_RESPONSE_MAX_RECOVERIES": "2",
            }
        )
        assert cfg is not None
        assert cfg.enabled is True
        assert cfg.max_recoveries == 2
        assert cfg.grace_event_allowance == 64
