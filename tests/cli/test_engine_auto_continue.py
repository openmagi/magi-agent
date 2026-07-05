"""Engine integration: ledger-first auto-continue at SEAM 2.

The highest-leverage fix. Reuses the hermetic fake-adapter / fake-bridge shape
from ``test_engine_goal_pause.py`` (no real ADK / litellm import). Proves that
when ``auto_continue_enabled=True`` and the durable todo ledger still has open
todos, SEAM 2's computed "continue" verdict actually RE-INVOKES the runner
(instead of the historic bare break), bounded by the measurable-progress gate:

  - open todos + an ok tool end -> re-invoke, then stop when the ledger clears.
  - two consecutive no-progress attempts -> ONE wrap-up invocation -> then an
    honest goal_paused(no_progress). No infinite "I'll continue" loop.
  - an attempt whose only activity was a blocked tool end -> immediate
    goal_paused(waiting_on_approvals).
  - max-continuations budget -> goal_loop_exhausted + goal_paused.
  - auto_continue_enabled=False -> byte-identical to the historic bare break
    (this is the regression guard for the existing SEAM 2 contract).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.plan_ledger import TodoItem


# ---------------------------------------------------------------------------
# Fake ADK adapter / bridge (same shape as test_engine_goal_pause.py)
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


def _ok_tool_end(tool_id: str = "call-1") -> dict[str, Any]:
    return {"type": "tool_end", "id": tool_id, "status": "ok"}


def _blocked_tool_end(tool_id: str = "call-b") -> dict[str, Any]:
    return {"type": "tool_end", "id": tool_id, "status": "error"}


def _text(t: str) -> dict[str, Any]:
    return {"type": "text_delta", "text": t}


class _LedgerReader:
    """A plan-ledger reader whose snapshot advances through a script.

    Each call to ``__call__`` returns the next scripted snapshot (staying on the
    last one once the script is exhausted), so the engine sees the ledger change
    across continuations exactly as a real durable ledger would.
    """

    def __init__(self, snapshots: list[tuple[TodoItem, ...]]) -> None:
        self._snapshots = snapshots
        self._i = 0

    def __call__(self, _sid: str) -> tuple[TodoItem, ...]:
        snap = self._snapshots[min(self._i, len(self._snapshots) - 1)]
        self._i += 1
        return snap


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


# ---------------------------------------------------------------------------
# The fix: open todos + progress -> re-invoke, then stop when ledger clears
# ---------------------------------------------------------------------------


class TestAutoContinueDrivesContinuation:
    def test_open_todos_with_progress_reinvokes_then_completes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Attempt 1: an ok tool end + open todo -> "continue" -> re-invoke.
        # Attempt 2: all todos completed -> SEAM 2 "done" -> goal_loop_complete.
        runner = FakeRunner(
            events_per_call=[
                [_ok_tool_end(), _text("I'll continue with the next step.")],
                [_text("All done.")],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        # Reader is read pre-attempt-1 (empty for delta baseline), at SEAM-2 of
        # attempt 1 (open todo), pre-attempt-2, and at SEAM-2 of attempt 2 (all
        # completed). Provide enough scripted snapshots.
        reader = _LedgerReader(
            [
                _todos(("t1", "pending"), ("t2", "pending")),  # pre-attempt-1 baseline
                # attempt-1 SEAM 2: t1 advanced (DELTA -> progress), t2 still open
                # -> resolve_pre_judge_outcome returns "continue".
                _todos(("t1", "completed"), ("t2", "pending")),
                _todos(("t1", "completed"), ("t2", "completed")),  # pre-attempt-2
                # attempt-2 SEAM 2: all completed -> "done".
                _todos(("t1", "completed"), ("t2", "completed")),
            ]
        )
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=reader,
            required_evidence=(),
            auto_continue_enabled=True,
        )
        items = _run_drive(driver)
        # Two runner invocations: original + one auto-continuation.
        assert len(runner.calls) == 2
        # The continuation prompt is the generic auto-continue nudge.
        assert "Continue executing the next concrete step" in runner.calls[1].new_message_text
        cont = _payloads_of_type(items, "goal_loop_continuation")
        assert len(cont) == 1
        assert cont[0].get("source") == "auto_continue"
        complete = _payloads_of_type(items, "goal_loop_complete")
        assert len(complete) == 1
        assert complete[0].get("reason") == "ledger_all_complete"
        assert items[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# The brake: no progress -> wrap-up -> honest pause (no infinite loop)
# ---------------------------------------------------------------------------


class TestAutoContinueProgressGate:
    def test_two_no_progress_then_wrap_up_then_paused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every attempt: no ok tool end, no ledger delta, no evidence -> stalls.
        # Streak: attempt1 -> continue(1), attempt2 -> wrap_up(2),
        # attempt3 -> paused_no_progress. Bounded, no model call.
        runner = FakeRunner(
            events_per_call=[
                [_text("I'll continue.")],
                [_text("I'll continue.")],
                [_text("Working on it.")],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        # Ledger never changes (same open todo every read) -> no ledger delta.
        reader = _LedgerReader([_todos(("t1", "in_progress"))])
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=reader,
            required_evidence=(),
            auto_continue_enabled=True,
        )
        items = _run_drive(driver)
        # original + 1 continuation + 1 wrap-up = 3 invocations, then pause.
        assert len(runner.calls) == 3
        cont = _payloads_of_type(items, "goal_loop_continuation")
        assert len(cont) == 1  # the first no-progress retry
        wrap = _payloads_of_type(items, "goal_loop_wrap_up")
        assert len(wrap) == 1
        assert "report" in runner.calls[2].new_message_text.lower()
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "no_progress"
        assert items[-1].terminal == Terminal.completed

    def test_blocked_only_pauses_waiting_on_approvals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[
                [_blocked_tool_end(), _text("Waiting on approval.")],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        reader = _LedgerReader([_todos(("t1", "in_progress"))])
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=reader,
            required_evidence=(),
            auto_continue_enabled=True,
        )
        items = _run_drive(driver)
        # No re-invocation: a blocked-only attempt pauses immediately.
        assert len(runner.calls) == 1
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "waiting_on_approvals"
        assert not _payloads_of_type(items, "goal_loop_continuation")
        assert items[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# Budget backstop
# ---------------------------------------------------------------------------


class TestAutoContinueBudget:
    def test_max_continuations_exhausts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force a tiny budget by monkeypatching the ambient budget selection so
        # the loop hits the ceiling quickly. Each attempt makes progress (ok tool
        # end) so the ONLY thing that stops it is the hard budget.
        import magi_agent.runtime.goal_loop_auto_continue as ac_mod

        tiny = ac_mod.AutoContinueBudgets(max_continuations=2, wall_clock_seconds=0)
        monkeypatch.setattr(
            ac_mod, "budgets_for_intensity", lambda *, mission: tiny
        )
        runner = FakeRunner(
            events_per_call=[[_ok_tool_end(f"c{i}"), _text("step")] for i in range(6)]
        )
        _patch_lazy_deps(monkeypatch, runner)
        # Ledger keeps an open todo AND changes each read so "done" never fires
        # and progress is always True -> only the budget stops the loop.
        reader = _LedgerReader(
            [_todos((f"t{i}", "in_progress")) for i in range(20)]
        )
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=reader,
            required_evidence=(),
            auto_continue_enabled=True,
        )
        items = _run_drive(driver)
        # original + 2 continuations, then max_continuations trips.
        assert len(runner.calls) == 3
        exhausted = _payloads_of_type(items, "goal_loop_exhausted")
        assert len(exhausted) == 1
        assert exhausted[0].get("reason") == "max_continuations"
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "budget_exhausted"
        assert items[-1].terminal == Terminal.completed


# ---------------------------------------------------------------------------
# Regression guard: OFF is byte-identical to the historic bare break
# ---------------------------------------------------------------------------


class TestAutoContinueOffByteIdentical:
    def test_off_open_todos_bare_break(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # evidence_first ON + open todos but auto_continue_enabled=False: SEAM 2
        # "continue" degrades to the bare break exactly as before (one runner
        # call, no continuation, no pause).
        events = [_ok_tool_end(), _text("I'll continue.")]

        runner = FakeRunner(events_per_call=[list(events)])
        _patch_lazy_deps(monkeypatch, runner)
        reader = _LedgerReader([_todos(("t1", "in_progress"))])
        off = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=reader,
            required_evidence=(),
            auto_continue_enabled=False,
        )
        off_items = _run_drive(off)
        assert len(runner.calls) == 1  # no re-invocation
        assert not _payloads_of_type(off_items, "goal_loop_continuation")
        assert not _payloads_of_type(off_items, "goal_paused")
        assert not _payloads_of_type(off_items, "goal_loop_complete")
        assert off_items[-1].terminal == Terminal.completed

    def test_off_status_stream_matches_pristine_driver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events = [_ok_tool_end(), _text("answer.")]

        runner_a = FakeRunner(events_per_call=[list(events)])
        _patch_lazy_deps(monkeypatch, runner_a)
        baseline = MagiEngineDriver(runner=runner_a, user_id="cli")
        base_items = _run_drive(baseline)

        runner_b = FakeRunner(events_per_call=[list(events)])
        _patch_lazy_deps(monkeypatch, runner_b)
        off = MagiEngineDriver(
            runner=runner_b,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=_LedgerReader([_todos(("t1", "in_progress"))]),
            required_evidence=(),
            auto_continue_enabled=False,
        )
        off_items = _run_drive(off)
        assert _all_status_types(off_items) == _all_status_types(base_items)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
