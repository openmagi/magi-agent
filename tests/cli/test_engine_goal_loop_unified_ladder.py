"""U4 unified clean-break ladder: policy-present branch restructure.

Design ``2026-07-06-magi-ambient-goal-loop-completion-design.md`` sections 5.2
and 5.4. The policy-present (mission today) branch is restructured so that:

  (a) a pre-judge ``continue`` outcome (open ledger) routes to the shared
      deterministic executor with NO judge call (economics fix, GAP-2); the
      judge is confined to the ``defer_to_judge`` (no-ledger) case.
  (b) a judge-driven ``not complete`` verdict is gated through the SAME
      measurable-progress brake as the deterministic path (design 5.4): two
      consecutive no-progress continuations spend the single wrap-up then pause
      honestly, so a stall no longer runs to ``max_turns`` continuations.
  (c) ``max_turns`` and the judge parse-failure budget remain absolute
      backstops, byte-preserved (existing tests in ``test_engine_goal_pause`` /
      ``test_engine_goal_loop_judge`` cover the ``auto_continue_enabled=False``
      path; these add explicit ``auto_continue_enabled=True`` cases).
  (d) the evidence-contract ``done`` / ``pause`` pre-judge arms short-circuit
      BEFORE any continuation routing, unchanged.

Reuses the hermetic fake-adapter / fake-bridge harness from
``test_engine_goal_pause`` (no real ADK / litellm import), so the driven path is
exactly the production clean-break block.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.per_turn_goal_loop_context import (
    reset_per_turn_goal_loop_policy,
    set_per_turn_goal_loop_policy,
)

from tests.cli.test_engine_goal_pause import (
    FakeRunner,
    _BlockedGate,
    _exploding_judge_factory,
    _patch_lazy_deps,
    _payloads_of_type,
    _policy,
    _run_drive,
    _scripted_judge_factory,
    _todos,
)


# --------------------------------------------------------------------------- #
# Local event / ledger helpers (trivial dict shapes the bridge passes through) #
# --------------------------------------------------------------------------- #


def _ok_tool_end(tool_id: str = "call-1") -> dict[str, Any]:
    return {"type": "tool_end", "id": tool_id, "status": "ok"}


def _text(t: str) -> dict[str, Any]:
    return {"type": "text_delta", "text": t}


class _ScriptedLedger:
    """A plan-ledger reader whose snapshot advances through a script.

    Saturates on the final snapshot once the script is exhausted, so the engine
    sees the ledger change across continuations exactly as a durable ledger
    would.
    """

    def __init__(self, snapshots: list[tuple[Any, ...]]) -> None:
        self._snapshots = snapshots
        self._i = 0

    def __call__(self, _sid: str) -> tuple[Any, ...]:
        snap = self._snapshots[min(self._i, len(self._snapshots) - 1)]
        self._i += 1
        return snap


def _continuations(items: list[Any]) -> list[dict[str, Any]]:
    return _payloads_of_type(items, "goal_loop_continuation")


class _CountingJudgeFactory:
    """A judge factory that records how many times the judge caller is invoked.

    ``evaluate_goal_completion`` swallows caller exceptions (they degrade to a
    ``judge_call_failed`` not-complete verdict), so a raising factory cannot
    prove "judge never invoked"; an explicit invocation counter can. Both the
    factory build and each caller invocation bump the counter.
    """

    def __init__(self) -> None:
        self.factory_builds = 0
        self.caller_calls = 0

    def __call__(self, _policy: object) -> object:
        self.factory_builds += 1

        async def _caller(_: str) -> str:
            self.caller_calls += 1
            return '{"complete": false, "reason": "unexpected judge call"}'

        return _caller


# --------------------------------------------------------------------------- #
# (a) mission + open ledger -> deterministic continue, judge NEVER invoked      #
# --------------------------------------------------------------------------- #


class TestLedgerContinueIsDeterministic:
    def test_open_ledger_continue_never_calls_judge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Attempt 1: a tool ran and the ledger still has an open todo -> pre-judge
        # "continue". With auto_continue_enabled the ladder routes this to the
        # deterministic executor (source "auto_continue"), NOT the judge.
        # Attempt 2: ledger all complete -> pre-judge "done".
        runner = FakeRunner(
            events_per_call=[
                [_ok_tool_end(), _text("Working the next step.")],
                [_text("All done.")],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        reader = _ScriptedLedger(
            [
                _todos(("t1", "pending"), ("t2", "pending")),  # a1 pre-attempt
                _todos(("t1", "completed"), ("t2", "pending")),  # a1 pre-judge
                _todos(("t1", "completed"), ("t2", "completed")),  # a2 pre-attempt
                _todos(("t1", "completed"), ("t2", "completed")),  # a2 pre-judge
            ]
        )
        judge = _CountingJudgeFactory()
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=reader,
            required_evidence=(),
            auto_continue_enabled=True,
            goal_loop_judge_factory=judge,
        )
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)

        assert len(runner.calls) == 2  # one deterministic continuation
        # The judge is NEVER consulted on the ledger-continue path.
        assert judge.caller_calls == 0
        assert judge.factory_builds == 0
        cont = _continuations(items)
        assert len(cont) == 1
        # Deterministic (shared executor) continuation, not a judge continuation.
        assert cont[0].get("source") == "auto_continue"
        assert "judgeReason" not in cont[0]
        complete = _payloads_of_type(items, "goal_loop_complete")
        assert len(complete) == 1
        assert complete[0].get("reason") == "ledger_all_complete"
        assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (b) mission + no ledger stall -> judge, then wrap-up, then honest pause       #
# --------------------------------------------------------------------------- #


class TestJudgeStallIsProgressBraked:
    def test_no_ledger_stall_wraps_up_then_pauses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Empty ledger -> pre-judge "defer_to_judge". The judge keeps answering
        # "not complete" while the model makes ZERO measurable progress (no tools,
        # no ledger, no evidence). The 5.4 brake must: continue once, wrap-up
        # once, then pause honestly -- NOT run to max_turns (20) continuations.
        runner = FakeRunner(
            events_per_call=[
                [_text("I'll continue.")],
                [_text("Still going.")],
                [_text("Working on it.")],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: (),  # no ledger signal
            required_evidence=(),
            auto_continue_enabled=True,
            goal_loop_judge_factory=_scripted_judge_factory(
                '{"complete": false, "reason": "not yet"}',
                '{"complete": false, "reason": "not yet"}',
                '{"complete": false, "reason": "not yet"}',
            ),
        )
        token = set_per_turn_goal_loop_policy(_policy(max_turns=20))
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)

        # Three model calls only (original + 2 braked continuations), NOT 20.
        assert len(runner.calls) == 3
        cont = _continuations(items)
        assert len(cont) == 1
        assert cont[0].get("continuation") == 1
        wrap = _payloads_of_type(items, "goal_loop_wrap_up")
        assert len(wrap) == 1
        assert wrap[0].get("continuation") == 2
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "no_progress"
        assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (c) max_turns + parse-budget preserved even with the brake active            #
# --------------------------------------------------------------------------- #


class TestBackstopsPreservedWithBrake:
    def test_max_turns_still_fires_when_progress_keeps_continuing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Progress every attempt (a tool runs) -> the brake returns "continue"
        # every time (streak resets), so it never pauses; max_turns=2 remains the
        # absolute backstop and trips goal_loop_exhausted + max_turns pause.
        runner = FakeRunner(
            events_per_call=[
                [_ok_tool_end("c1"), _text("step 1")],
                [_ok_tool_end("c2"), _text("step 2")],
                [_ok_tool_end("c3"), _text("step 3")],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: (),  # defer_to_judge every break
            required_evidence=(),
            auto_continue_enabled=True,
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

        assert len(runner.calls) == 3
        exhausted = _payloads_of_type(items, "goal_loop_exhausted")
        assert len(exhausted) == 1
        assert exhausted[0].get("continuations") == 2
        assert exhausted[0].get("max") == 2
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "max_turns_exhausted"
        assert items[-1].terminal == Terminal.completed

    def test_parse_budget_still_fails_closed_with_brake_active(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A non-JSON judge answer past the parse-failure budget must terminate
        # via goal_loop_judge_unavailable + parse_failure_budget pause BEFORE the
        # brake ever runs -- byte-preserved with auto_continue_enabled=True.
        runner = FakeRunner(
            events_per_call=[[_text("draft")]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: (),  # defer_to_judge
            required_evidence=(),
            auto_continue_enabled=True,
            goal_loop_judge_factory=_scripted_judge_factory("not json at all"),
        )
        token = set_per_turn_goal_loop_policy(_policy(parse_budget=1))
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)

        assert len(runner.calls) == 1  # no continuation
        unavail = _payloads_of_type(items, "goal_loop_judge_unavailable")
        assert len(unavail) == 1
        assert unavail[0].get("reason") == "parse_failure_budget_exhausted"
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "parse_failure_budget"
        assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (d) evidence-contract done / pause arms unchanged (short-circuit first)       #
# --------------------------------------------------------------------------- #


class TestPreJudgeArmsUnchanged:
    def test_done_arm_short_circuits_before_deterministic_routing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # All-complete ledger -> pre-judge "done" wins even with the deterministic
        # routing enabled: goal_loop_complete, judge never called, ONE model call.
        runner = FakeRunner(
            events_per_call=[[_text("Final answer.")]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        snapshot = _todos(("t1", "completed"), ("t2", "completed"))
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: snapshot,
            required_evidence=(),
            auto_continue_enabled=True,
            goal_loop_judge_factory=_exploding_judge_factory(),
        )
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)

        assert len(runner.calls) == 1  # no continuation of any kind
        complete = _payloads_of_type(items, "goal_loop_complete")
        assert len(complete) == 1
        assert complete[0].get("reason") == "ledger_all_complete"
        assert not _continuations(items)
        assert items[-1].terminal == Terminal.completed

    def test_pause_arm_short_circuits_before_deterministic_routing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unverifiable required evidence -> pre-judge "pause" wins even with the
        # deterministic routing enabled: goal_paused(evidence_unverifiable), judge
        # never called, ONE model call.
        import magi_agent.evidence.final_output_gate as gate_mod

        monkeypatch.setattr(gate_mod, "FinalOutputGate", _BlockedGate)
        runner = FakeRunner(
            events_per_call=[[_text("Partial work.")]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        snapshot = _todos(("t1", "completed"), ("t2", "in_progress"))
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: snapshot,
            required_evidence=("source_ledger",),
            auto_continue_enabled=True,
            goal_loop_judge_factory=_exploding_judge_factory(),
        )
        token = set_per_turn_goal_loop_policy(_policy())
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)

        assert len(runner.calls) == 1
        paused = _payloads_of_type(items, "goal_paused")
        assert len(paused) == 1
        assert paused[0].get("reason") == "evidence_unverifiable"
        assert paused[0].get("openTodos") == 1
        assert not _payloads_of_type(items, "goal_loop_complete")
        assert not _continuations(items)
        assert items[-1].terminal == Terminal.completed


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
