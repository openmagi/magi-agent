"""WS3 PR3b engine seams: evidence-first completion + honest goal_paused.

Reuses the hermetic fake-adapter/fake-bridge shape from
``test_engine_goal_loop_judge.py`` (no real ADK / litellm import). Covers:

  - SEAM 2 (the "full" profile deliverable, loop OFF -> goal_loop_policy None):
    all-complete ledger -> goal_loop_complete{ledger_all_complete} with ZERO
    judge calls; evidence-blocked -> goal_paused{evidence_unverifiable}; OFF
    branch byte-identical to the bare break.
  - SEAM 1 (lab loop ON): all-complete ledger short-circuits BEFORE the judge.
  - SEAM 3 (lab loop ON): parse-budget / max-turns exhaustion ALSO emit
    goal_paused additively (existing events still fire).
  - Reader 2 (cli/wiring.py): required_evidence reaches the engine gated ONLY on
    the evidence-first flag, NEVER on MAGI_GOAL_NUDGE_ENABLED.
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
from magi_agent.runtime.plan_ledger import TodoItem


# ---------------------------------------------------------------------------
# Fake ADK adapter / bridge (same shape as test_engine_goal_loop_judge.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeRunnerCall:
    invocation_id: str
    new_message_text: str


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
# Helpers
# ---------------------------------------------------------------------------


def _todos(*pairs: tuple[str, str]) -> tuple[TodoItem, ...]:
    return tuple(TodoItem(content=c, status=s) for c, s in pairs)  # type: ignore[arg-type]


def _policy(*, max_turns: int = 20, parse_budget: int = 2) -> GoalLoopPolicy:
    return GoalLoopPolicy(
        enabled=True,
        objective="Analyze the 10-K and write a final report",
        max_turns=max_turns,
        judge_provider=None,
        judge_model=None,
        judge_parse_failures_budget=parse_budget,
        continuation_template=DEFAULT_CONTINUATION_TEMPLATE,
    )


def _exploding_judge_factory():
    """A factory whose caller fails loudly if the judge is ever consulted."""

    async def _caller(_: str) -> str:
        raise AssertionError("judge must NOT be called on the pre-judge short-circuit")

    def _factory(_policy: object) -> object:
        return _caller

    return _factory


def _scripted_judge_factory(*responses: str):
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


def _payloads_of_type(items: list[Any], type_: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        payload = getattr(it, "payload", None)
        if isinstance(payload, dict) and payload.get("type") == type_:
            out.append(payload)
    return out


def _all_status_types(items: list[Any]) -> list[str]:
    out: list[str] = []
    for it in items:
        payload = getattr(it, "payload", None)
        if isinstance(payload, dict) and "type" in payload:
            out.append(str(payload["type"]))
    return out


class _BlockedGate:
    """Fake FinalOutputGate that always returns a hard-failure decision."""

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    def evaluate(self, *_a: object, **_k: object) -> object:
        class _Blocked:
            status = "blocked"
            reason_codes = ("numeric_claim_mismatch",)

        return _Blocked()


# ---------------------------------------------------------------------------
# SEAM 2 - the "full" profile deliverable (loop OFF -> goal_loop_policy None)
# ---------------------------------------------------------------------------


class TestSeam2FullProfile:
    def test_seam2_full_profile_done_with_loop_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[[{"type": "text_delta", "text": "All done."}]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        snapshot = _todos(("t1", "completed"), ("t2", "completed"))
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: snapshot,
            required_evidence=(),
            # A judge factory is present but MUST NOT be called: no policy is set
            # so goal_loop_policy is None and SEAM 2 (outside the guard) fires.
            goal_loop_judge_factory=_exploding_judge_factory(),
        )
        # NO set_per_turn_goal_loop_policy -> goal_loop_policy is None ("full").
        items = _run_drive(driver)
        complete = _payloads_of_type(items, "goal_loop_complete")
        assert len(complete) == 1
        assert complete[0].get("reason") == "ledger_all_complete"
        # Zero judge calls: exactly the one original run, no continuation.
        assert len(runner.calls) == 1
        assert items[-1].terminal == Terminal.completed

    def test_seam2_full_profile_pause_with_loop_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import magi_agent.evidence.final_output_gate as gate_mod

        monkeypatch.setattr(gate_mod, "FinalOutputGate", _BlockedGate)
        runner = FakeRunner(
            events_per_call=[[{"type": "text_delta", "text": "Partial work."}]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        snapshot = _todos(("t1", "completed"), ("t2", "in_progress"))
        # required_evidence non-empty is supplied DIRECTLY to the engine (the
        # engine-side terminus of Reader 2); MAGI_GOAL_NUDGE_ENABLED is never set.
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: snapshot,
            required_evidence=("source_ledger",),
            goal_loop_judge_factory=_exploding_judge_factory(),
        )
        items = _run_drive(driver)
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "evidence_unverifiable"
        assert paused[0].get("openTodos") == 1  # t2 in_progress
        # Output preserved, no synthetic success.
        assert not _payloads_of_type(items, "goal_loop_complete")
        assert items[-1].terminal == Terminal.completed

    def test_seam2_off_path_byte_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # evidence_first OFF + loop OFF -> the SEAM-2 location is byte-identical
        # to the bare break: identical status-event stream to a pristine driver.
        events = [[{"type": "text_delta", "text": "answer."}]]

        runner_a = FakeRunner(events_per_call=[list(events[0])])
        _patch_lazy_deps(monkeypatch, runner_a)
        baseline = MagiEngineDriver(runner=runner_a, user_id="cli")
        base_items = _run_drive(baseline)

        runner_b = FakeRunner(events_per_call=[list(events[0])])
        _patch_lazy_deps(monkeypatch, runner_b)
        off = MagiEngineDriver(
            runner=runner_b,
            user_id="cli",
            evidence_first=False,
            plan_ledger_reader=lambda _sid: _todos(("t1", "completed")),
            required_evidence=("source_ledger",),
        )
        off_items = _run_drive(off)

        assert _all_status_types(off_items) == _all_status_types(base_items)
        assert not _payloads_of_type(off_items, "goal_paused")
        assert not _payloads_of_type(off_items, "goal_loop_complete")
        assert off_items[-1].terminal == base_items[-1].terminal


# ---------------------------------------------------------------------------
# SEAM 1 - lab loop ON, pre-judge short-circuit before the judge
# ---------------------------------------------------------------------------


class TestSeam1LabLoop:
    def test_seam1_pre_judge_done_short_circuits_before_judge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[[{"type": "text_delta", "text": "Final answer."}]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        snapshot = _todos(("t1", "completed"), ("t2", "completed"))
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: snapshot,
            required_evidence=(),
            goal_loop_judge_factory=_exploding_judge_factory(),
        )
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        complete = _payloads_of_type(items, "goal_loop_complete")
        assert len(complete) == 1
        assert complete[0].get("reason") == "ledger_all_complete"
        assert len(runner.calls) == 1  # judge never consulted
        assert items[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# SEAM 3 - lab loop ON, honest pause additive to existing terminations
# ---------------------------------------------------------------------------


class TestSeam3HonestPause:
    def test_engine_emits_goal_paused_on_budget_exhaustion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[[{"type": "text_delta", "text": "draft"}]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: _todos(("t1", "in_progress")),
            required_evidence=(),
            goal_loop_judge_factory=_scripted_judge_factory("not json at all"),
        )
        token = set_per_turn_goal_loop_policy(_policy(parse_budget=1))
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        # Both the existing event AND the new honest pause fire.
        unavail = _payloads_of_type(items, "goal_loop_judge_unavailable")
        assert len(unavail) == 1
        assert unavail[0].get("reason") == "parse_failure_budget_exhausted"
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "parse_failure_budget"
        assert paused[0].get("openTodos") == 1
        assert items[-1].terminal == Terminal.completed

    def test_engine_emits_goal_paused_on_max_turns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[
                [{"type": "text_delta", "text": "step 1"}],
                [{"type": "text_delta", "text": "step 2"}],
                [{"type": "text_delta", "text": "step 3"}],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        # Reader returns OPEN todos so the SEAM-1 short-circuit defers to the
        # judge; the judge says incomplete twice, then max_turns trips.
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: _todos(("t1", "in_progress")),
            required_evidence=(),
            goal_loop_judge_factory=_scripted_judge_factory(
                '{"complete": false, "reason": "not yet"}',
                '{"complete": false, "reason": "not yet"}',
            ),
        )
        token = set_per_turn_goal_loop_policy(_policy(max_turns=2))
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        exhausted = _payloads_of_type(items, "goal_loop_exhausted")
        assert len(exhausted) == 1
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "max_turns_exhausted"
        assert items[-1].terminal == Terminal.completed

    def test_off_path_byte_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Lab loop ON, evidence-first OFF -> the goal-loop block is byte-identical
        # to pre-PR3b: judge says complete, no goal_paused, no pre-judge reason.
        runner = FakeRunner(
            events_per_call=[[{"type": "text_delta", "text": "Final answer: 2."}]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=False,
            plan_ledger_reader=lambda _sid: _todos(("t1", "completed")),
            required_evidence=(),
            goal_loop_judge_factory=_scripted_judge_factory(
                '{"complete": true, "reason": "produced final answer"}'
            ),
        )
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)
        complete = _payloads_of_type(items, "goal_loop_complete")
        assert len(complete) == 1
        assert complete[0].get("reason") == "produced final answer"  # judge, not ledger
        assert not _payloads_of_type(items, "goal_paused")
        assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# Reader 2 independence (cli/wiring.py) - the arch-tdd trap proof
# ---------------------------------------------------------------------------


class TestReader2Independence:
    def test_reader2_reaches_engine_without_nudge_gate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from magi_agent.cli.wiring import build_headless_runtime

        monkeypatch.setenv("MAGI_GOAL_COMPLETION_EVIDENCE_FIRST_ENABLED", "1")
        monkeypatch.setenv(
            "MAGI_GOAL_NUDGE_REQUIRED_EVIDENCE", "source_ledger,calculation_evidence"
        )
        monkeypatch.delenv("MAGI_GOAL_NUDGE_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_GOAL_LOOP_ENABLED", raising=False)
        rt = build_headless_runtime(
            runner=FakeRunner(),
            session_id="s1",
            cwd=str(tmp_path),
        )
        engine = rt.engine
        assert engine._evidence_first is True
        # Reader 2 reached the engine WITHOUT the nudge gate being set.
        assert engine._required_evidence == ("source_ledger", "calculation_evidence")
        assert engine._goal_nudge is None

    def test_off_flags_engine_byte_identical_di(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.delenv("MAGI_GOAL_COMPLETION_EVIDENCE_FIRST_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_GOAL_NUDGE_REQUIRED_EVIDENCE", raising=False)
        monkeypatch.delenv("MAGI_PLAN_LEDGER_DURABLE_ENABLED", raising=False)
        from magi_agent.cli.wiring import build_headless_runtime

        rt = build_headless_runtime(
            runner=FakeRunner(),
            session_id="s1",
            cwd=str(tmp_path),
        )
        assert rt.engine._evidence_first is False
        assert rt.engine._required_evidence == ()
        assert rt.engine._plan_ledger_reader is None

    def test_plan_ledger_reader_wired_when_durable_on(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setenv("MAGI_PLAN_LEDGER_DURABLE_ENABLED", "1")
        from magi_agent.cli.wiring import build_headless_runtime

        snapshot = _todos(("t1", "completed"))

        class _FakeHandlerSet:
            def snapshot_for(self, _sid: str) -> tuple[TodoItem, ...]:
                return snapshot

        runner = FakeRunner()
        runner.plan_ledger_handler_set = _FakeHandlerSet()  # type: ignore[attr-defined]
        rt = build_headless_runtime(
            runner=runner,
            session_id="s1",
            cwd=str(tmp_path),
        )
        reader = rt.engine._plan_ledger_reader
        assert reader is not None
        assert reader("s1") == snapshot
