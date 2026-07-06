"""U5 ambient activation: driver-side finish-the-job baseline + nudge retirement.

Design ``2026-07-06-magi-ambient-goal-loop-completion-design.md`` sections 5.1,
5.2, 6.3, 8. U5 is the activation moment: with a wiring-built ambient factory
injected, the engine synthesizes an ambient ``GoalLoopPolicy`` at the clean break
WHEN no per-turn ContextVar policy was published (toggle off), the substance gate
passes, and auto-continue is enabled. The captured turn objective is the ORIGINAL
user text (A-1). Explicit mission intensity bypasses the substance gate (OD-6).

Driver-level cases (a) (b) (c) (f) from the U5 row of section 13. The wiring-level
cases (d SpawnAgent child / e goal-loop-OFF nudge escape hatch) and the ambient DI
+ goal_nudge supersession live in ``test_wiring_ambient_activation_u5.py``.

Reuses the hermetic fake-adapter / fake-bridge harness from
``test_engine_goal_pause`` so the driven path is exactly the production clean-break
block (no real ADK / litellm import).
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.runtime.goal_loop_policy import (
    DEFAULT_CONTINUATION_TEMPLATE,
    GoalLoopPolicy,
)
from magi_agent.runtime.per_turn_goal_intensity import (
    reset_per_turn_goal_mission,
    set_per_turn_goal_mission,
)

from tests.cli.test_engine_goal_pause import (
    FakeRunner,
    _all_status_types,
    _exploding_judge_factory,
    _patch_lazy_deps,
    _payloads_of_type,
    _run_drive,
    _scripted_judge_factory,
)


# --------------------------------------------------------------------------- #
# Local helpers                                                                #
# --------------------------------------------------------------------------- #


def _ok_tool_end(tool_id: str = "call-1") -> dict[str, Any]:
    return {"type": "tool_end", "id": tool_id, "status": "ok"}


def _text(t: str) -> dict[str, Any]:
    return {"type": "text_delta", "text": t}


def _continuations(items: list[Any]) -> list[dict[str, Any]]:
    return _payloads_of_type(items, "goal_loop_continuation")


class _RecordingAmbientFactory:
    """A wiring-built ambient factory double: records every objective it is asked
    to synthesize for, and returns a real ambient ``GoalLoopPolicy`` (or ``None``
    for an empty objective, mirroring ``build_ambient_goal_loop_policy``)."""

    def __init__(self, *, max_turns: int = 3, parse_budget: int = 2) -> None:
        self.objectives: list[str] = []
        self._max_turns = max_turns
        self._parse_budget = parse_budget

    def __call__(self, objective: str) -> GoalLoopPolicy | None:
        self.objectives.append(objective)
        if not objective.strip():
            return None
        return GoalLoopPolicy(
            enabled=True,
            objective=objective,
            max_turns=self._max_turns,
            judge_provider=None,
            judge_model=None,
            judge_parse_failures_budget=self._parse_budget,
            continuation_template=DEFAULT_CONTINUATION_TEMPLATE,
        )


# --------------------------------------------------------------------------- #
# (a) toggle-OFF tool turn, NO ledger -> ambient judge, objective == user text  #
# --------------------------------------------------------------------------- #


class TestAmbientNoLedgerSynthesis:
    def test_toolless_free_no_ledger_gets_ambient_judge_capped_at_three(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A toggle-OFF turn that did real tool work but wrote NO todos (GAP-1).
        # Every attempt makes progress (a tool runs) so the measurable-progress
        # brake keeps returning "continue"; the AMBIENT ceiling (max_turns=3) is
        # the backstop -> exactly 3 judge continuations, then goal_loop_exhausted.
        runner = FakeRunner(
            events_per_call=[
                [_ok_tool_end("c1"), _text("step 1")],
                [_ok_tool_end("c2"), _text("step 2")],
                [_ok_tool_end("c3"), _text("step 3")],
                [_ok_tool_end("c4"), _text("step 4")],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        factory = _RecordingAmbientFactory(max_turns=3)
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=None,  # NO ledger -> pre-judge skipped -> judge path
            required_evidence=(),
            auto_continue_enabled=True,
            ambient_goal_policy_factory=factory,
            goal_loop_judge_factory=_scripted_judge_factory(
                '{"complete": false, "reason": "not yet"}',
                '{"complete": false, "reason": "not yet"}',
                '{"complete": false, "reason": "not yet"}',
            ),
        )
        # NO set_per_turn_goal_loop_policy -> ambient (toggle off).
        items = _run_drive(driver, prompt="Refactor the parser and add tests")

        # Objective is the ORIGINAL user text (A-1), synthesized every clean break.
        assert factory.objectives, "ambient factory was never asked to synthesize"
        assert all(
            o == "Refactor the parser and add tests" for o in factory.objectives
        )
        # Original call + 3 braked continuations, capped by the ambient ceiling.
        assert len(runner.calls) == 4
        cont = _continuations(items)
        assert len(cont) == 3
        assert all(c.get("source") == "judge" for c in cont)
        exhausted = _payloads_of_type(items, "goal_loop_exhausted")
        assert len(exhausted) == 1
        assert exhausted[0].get("max") == 3
        assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (b) bare "hi" turn -> ZERO extra model calls, ambient factory NEVER consulted #
# --------------------------------------------------------------------------- #


class TestChatOnlyTurnCostsNothing:
    def test_bare_hi_makes_a_single_model_call_and_no_synthesis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No tool, no ledger, no evidence -> substance gate fails -> NO synthesis,
        # NO judge, NO continuation. Exactly one model call. This also locks in the
        # nudge-retirement saving: no goal_nudge is wired (goal-loop-ON supersedes
        # it), so a chat-only turn costs zero extra calls (today it costs +1 nudge).
        runner = FakeRunner(events_per_call=[[_text("Hello!")]])
        _patch_lazy_deps(monkeypatch, runner)
        factory = _RecordingAmbientFactory()
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: (),  # empty ledger, present reader
            required_evidence=(),
            auto_continue_enabled=True,
            ambient_goal_policy_factory=factory,
            # An exploding judge proves the ambient judge path is never reached.
            goal_loop_judge_factory=_exploding_judge_factory(),
            # NO goal_nudge: goal-loop-ON supersedes it (design 6.5).
        )
        items = _run_drive(driver, prompt="hi")

        assert len(runner.calls) == 1  # single model invocation, zero extra
        assert factory.objectives == []  # substance gate blocked synthesis
        assert not _continuations(items)
        assert not _payloads_of_type(items, "goal_loop_complete")
        assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (c) auto-continue OFF (safe / flag-0 resolution) -> byte-identical, no synth   #
# --------------------------------------------------------------------------- #


class TestAutoContinueOffIsByteIdentical:
    def test_factory_present_but_auto_continue_off_never_synthesizes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The safe / eval / explicit-flag-0 resolution: auto_continue_enabled is
        # False (the wiring also passes factory=None there, but even a stray
        # factory must be inert). A tool turn that would otherwise be substantive
        # must produce the SAME status stream as a pristine driver, and the factory
        # must never be consulted.
        events = [[_ok_tool_end(), _text("done.")]]

        runner_a = FakeRunner(events_per_call=[list(events[0])])
        _patch_lazy_deps(monkeypatch, runner_a)
        baseline = MagiEngineDriver(runner=runner_a, user_id="cli")
        base_items = _run_drive(baseline, prompt="do the multi-step thing")

        runner_b = FakeRunner(events_per_call=[list(events[0])])
        _patch_lazy_deps(monkeypatch, runner_b)
        factory = _RecordingAmbientFactory()
        off = MagiEngineDriver(
            runner=runner_b,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=lambda _sid: (),
            required_evidence=(),
            auto_continue_enabled=False,  # safe / flag-0 resolution
            ambient_goal_policy_factory=factory,
        )
        off_items = _run_drive(off, prompt="do the multi-step thing")

        assert factory.objectives == []  # gated on auto_continue_enabled
        assert _all_status_types(off_items) == _all_status_types(base_items)
        assert len(runner_b.calls) == len(runner_a.calls) == 1
        assert off_items[-1].terminal == base_items[-1].terminal


# --------------------------------------------------------------------------- #
# (f) mission intensity ON + toolless turn -> synthesizes, bypassing the gate    #
# --------------------------------------------------------------------------- #


class TestMissionBypassesSubstanceGate:
    def test_mission_intensity_synthesizes_on_a_toolless_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Explicit mission intensity (the composer toggle) with a toolless turn and
        # no ContextVar policy: the substance gate is BYPASSED (OD-6), so the driver
        # still synthesizes an ambient policy and runs the ladder. Contrast with
        # case (b), where an identical toolless turn WITHOUT mission does nothing.
        runner = FakeRunner(events_per_call=[[_text("A one-line answer.")]])
        _patch_lazy_deps(monkeypatch, runner)
        factory = _RecordingAmbientFactory()
        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            evidence_first=True,
            plan_ledger_reader=None,  # no ledger -> judge is the only completion
            required_evidence=(),
            auto_continue_enabled=True,
            ambient_goal_policy_factory=factory,
            goal_loop_judge_factory=_scripted_judge_factory(
                '{"complete": true, "reason": "trivial objective satisfied"}'
            ),
        )
        token = set_per_turn_goal_mission(True)  # explicit mission intensity
        try:
            items = _run_drive(driver, prompt="say hello")
        finally:
            reset_per_turn_goal_mission(token)

        # Synthesis happened despite ZERO tools (mission bypassed the gate).
        assert factory.objectives == ["say hello"]
        # The ladder ran: the judge (the only completion authority with no ledger)
        # was consulted and returned complete.
        complete = _payloads_of_type(items, "goal_loop_complete")
        assert len(complete) == 1
        assert complete[0].get("reason") == "trivial objective satisfied"
        assert len(runner.calls) == 1
        assert items[-1].terminal == Terminal.completed


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
