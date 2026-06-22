"""Engine integration: PR-C clean-break goal-loop judge branch.

Reuses the fake-adapter / fake-bridge infrastructure from
``test_engine_goal_nudge.py`` so no real ADK / litellm import is required.
A hand-authored fake judge caller replaces the cheap-tier model so the test
is fully hermetic.

Cases:
  - No policy on the ContextVar → engine path byte-identical to today.
  - Policy + judge says complete → terminate with ``goal_loop_complete``.
  - Policy + judge says incomplete → drive ONE continuation (re-invoke
    ``run_async`` with the policy's continuation prompt) then terminate on
    the next clean break.
  - Policy + reached ``policy.max_turns`` → ``goal_loop_exhausted``.
  - Policy + factory returns None → ``goal_loop_judge_unavailable``.
  - Policy + judge parse failures past budget → ``goal_loop_judge_unavailable``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.goal_loop_policy import (
    DEFAULT_CONTINUATION_TEMPLATE,
    GoalLoopPolicy,
)
from magi_agent.runtime.per_turn_goal_loop_context import (
    reset_per_turn_goal_loop_policy,
    set_per_turn_goal_loop_policy,
)


# ---------------------------------------------------------------------------
# Fake ADK adapter / bridge (same shape as test_engine_goal_nudge.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeRunnerCall:
    invocation_id: str
    new_message_text: str


@dataclass
class _FakeEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


class FakeRunner:
    def __init__(
        self, *, events_per_call: list[list[dict[str, Any]]] | None = None
    ) -> None:
        self.calls: list[FakeRunnerCall] = []
        self._events_per_call = events_per_call or []
        self._call_index = 0
        self.agent = None

    def _events_for_this_call(self) -> list[dict[str, Any]]:
        events = (
            self._events_per_call[self._call_index]
            if self._call_index < len(self._events_per_call)
            else []
        )
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
        e = self._events[self._index]
        self._index += 1
        return _FakeADKEvent(e)

    async def aclose(self) -> None:
        pass


class _FakeADKEvent:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict


class _FakeBridgeResult:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict

    @property
    def agent_events(self) -> list[dict[str, Any]]:
        return [self._event_dict] if self._event_dict else []


class FakeEventBridge:
    def project_adk_event(self, adk_event: object, *, turn_id: str) -> Any:
        return _FakeBridgeResult(getattr(adk_event, "_event_dict", {}))


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


# ---------------------------------------------------------------------------
# Hermetic judge helpers
# ---------------------------------------------------------------------------


def _policy(*, max_turns: int = 20, parse_budget: int = 2) -> GoalLoopPolicy:
    return GoalLoopPolicy(
        enabled=True,
        objective="Analyze Tesla 10-K and write a final report",
        max_turns=max_turns,
        judge_provider=None,
        judge_model=None,
        judge_parse_failures_budget=parse_budget,
        continuation_template=DEFAULT_CONTINUATION_TEMPLATE,
    )


def _judge_factory(*responses: str):
    """Build a factory whose caller yields the given responses in order, then
    raises StopIteration so the test's accidental over-call is loud."""
    iterator = iter(responses)

    async def _caller(_: str) -> str:
        return next(iterator)

    def _factory(_policy: object) -> object:
        return _caller

    return _factory


def _run_drive(driver: MagiEngineDriver, *, prompt: str = "do the thing") -> list[Any]:
    async def _collect() -> list[Any]:
        cancel = asyncio.Event()
        items: list[Any] = []
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


def _make_driver(
    runner: FakeRunner,
    *,
    goal_loop_judge_factory: object | None = None,
) -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=runner,
        user_id="cli",
        goal_loop_judge_factory=goal_loop_judge_factory,  # type: ignore[arg-type]
    )


def _payloads_of_type(items: list[Any], type_: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        payload = getattr(it, "payload", None)
        if isinstance(payload, dict) and payload.get("type") == type_:
            out.append(payload)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNoPolicy:
    def test_byte_identical_when_context_var_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Default ContextVar (None) → engine never enters the goal-loop branch.
        # Exactly one run_async call, terminal completed, no goal_loop_* events.
        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        driver = _make_driver(runner, goal_loop_judge_factory=_judge_factory())
        items = _run_drive(driver)
        assert len(runner.calls) == 1
        assert items[-1].terminal == Terminal.completed
        for ev in items:
            payload = getattr(ev, "payload", {})
            if isinstance(payload, dict):
                assert not str(payload.get("type", "")).startswith("goal_loop_"), payload


class TestJudgeComplete:
    def test_judge_complete_terminates_with_goal_loop_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[[{"type": "text_delta", "text": "Final answer: 2."}]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = _make_driver(
            runner,
            goal_loop_judge_factory=_judge_factory(
                '{"complete": true, "reason": "produced final answer"}'
            ),
        )
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        assert len(runner.calls) == 1  # judge said complete → no continuation
        completed = _payloads_of_type(items, "goal_loop_complete")
        assert len(completed) == 1
        assert "produced final answer" in completed[0].get("reason", "")
        assert items[-1].terminal == Terminal.completed


class TestJudgeIncomplete:
    def test_judge_incomplete_drives_one_continuation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Call 1 emits plan-only text → judge says incomplete → continuation.
        # Call 2 emits final answer → judge says complete → terminate.
        runner = FakeRunner(
            events_per_call=[
                [{"type": "text_delta", "text": "Refreshed Plan: I'll fetch next."}],
                [{"type": "text_delta", "text": "Final answer: done."}],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = _make_driver(
            runner,
            goal_loop_judge_factory=_judge_factory(
                '{"complete": false, "reason": "plan only, no execution"}',
                '{"complete": true, "reason": "produced final answer"}',
            ),
        )
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        # Two run_async calls — original plus exactly one continuation.
        assert len(runner.calls) == 2
        # The continuation message must be the generic policy template.
        assert runner.calls[1].new_message_text == DEFAULT_CONTINUATION_TEMPLATE
        cont = _payloads_of_type(items, "goal_loop_continuation")
        assert len(cont) == 1
        assert cont[0].get("continuation") == 1
        completed = _payloads_of_type(items, "goal_loop_complete")
        assert len(completed) == 1
        assert items[-1].terminal == Terminal.completed


class TestMaxTurns:
    def test_max_turns_reached_emits_goal_loop_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # max_turns=2 → up to 2 continuations, then exhausted (and break).
        # We need 3 clean breaks total: original + cont1 + cont2 (which trips
        # the >=max_turns check on the next clean-break decision).
        runner = FakeRunner(
            events_per_call=[
                [{"type": "text_delta", "text": "step 1 plan"}],
                [{"type": "text_delta", "text": "step 2 plan"}],
                [{"type": "text_delta", "text": "step 3 plan"}],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = _make_driver(
            runner,
            goal_loop_judge_factory=_judge_factory(
                '{"complete": false, "reason": "not yet"}',
                '{"complete": false, "reason": "not yet"}',
                # No third response needed — branch terminates on the next
                # clean break BEFORE calling the judge again.
            ),
        )
        token = set_per_turn_goal_loop_policy(_policy(max_turns=2))
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        # 3 invocations (original + 2 continuations) — the third clean break
        # exits via max_turns BEFORE calling the judge a 3rd time.
        assert len(runner.calls) == 3
        exhausted = _payloads_of_type(items, "goal_loop_exhausted")
        assert len(exhausted) == 1
        assert exhausted[0].get("continuations") == 2
        assert exhausted[0].get("max") == 2
        assert items[-1].terminal == Terminal.completed


class TestJudgeUnavailable:
    def test_no_factory_emits_judge_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[[{"type": "text_delta", "text": "draft text"}]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = _make_driver(runner, goal_loop_judge_factory=None)
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        unavail = _payloads_of_type(items, "goal_loop_judge_unavailable")
        assert len(unavail) == 1
        assert unavail[0].get("reason") == "no_judge_factory"
        # Original turn still completes — the goal-loop just doesn't drive any
        # continuations.
        assert len(runner.calls) == 1
        assert items[-1].terminal == Terminal.completed

    def test_factory_returning_none_emits_judge_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[[]])
        _patch_lazy_deps(monkeypatch, runner)
        driver = _make_driver(runner, goal_loop_judge_factory=lambda _p: None)
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        unavail = _payloads_of_type(items, "goal_loop_judge_unavailable")
        assert len(unavail) == 1
        assert unavail[0].get("reason") == "judge_factory_returned_none"

    def test_parse_failures_past_budget_emits_judge_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both judge responses unparsable → budget=1 means: first parse-fail
        # counts as not-complete (continuation), second clean-break trips the
        # ">= budget" check and terminates as judge_unavailable.
        runner = FakeRunner(
            events_per_call=[
                [{"type": "text_delta", "text": "draft 1"}],
                [{"type": "text_delta", "text": "draft 2"}],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = _make_driver(
            runner,
            goal_loop_judge_factory=_judge_factory(
                "not json at all",  # parse fail 1 → counter goes to 1; budget hits → break
            ),
        )
        token = set_per_turn_goal_loop_policy(_policy(parse_budget=1))
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        unavail = _payloads_of_type(items, "goal_loop_judge_unavailable")
        # The post-judge check (parse_failures >= budget) trips immediately
        # since budget=1.
        assert len(unavail) == 1
        assert unavail[0].get("reason") == "parse_failure_budget_exhausted"
