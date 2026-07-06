"""Golden characterization of the SEAM 2 deterministic auto-continue executor.

U3 safety net. This locks the EXACT status/pause event sequence the SEAM 2
deterministic executor (``engine/driver.py`` clean-break block, the
``seam2_outcome == "continue" and self._auto_continue_enabled`` arm) emits for
every terminal outcome, so the U3 extraction into a private helper is provably
byte-identical. It must stay GREEN before and after the refactor.

The five locked outcomes (design 5.3 / U3 row in section 13):
  (a) ``continue``  -> ``goal_loop_continuation`` re-invoke, then completion.
  (b) ``wrap_up``   -> ``goal_loop_wrap_up`` final continuation.
  (c) pause ``no_progress``            -> ``goal_paused(no_progress)``.
  (d) pause ``waiting_on_approvals``   -> ``goal_paused(waiting_on_approvals)``.
  (e) budget stop (max continuations)  -> ``goal_loop_exhausted`` + pause.

Reuses the hermetic fake-adapter / fake-bridge harness from
``test_engine_auto_continue.py`` (no real ADK / litellm import), so the driven
path is exactly the production SEAM 2 executor.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver

from tests.cli.test_engine_auto_continue import (
    FakeRunner,
    _blocked_tool_end,
    _LedgerReader,
    _ok_tool_end,
    _patch_lazy_deps,
    _run_drive,
    _text,
    _todos,
)


# --------------------------------------------------------------------------- #
# Snapshot helper: the ordered sequence of goal-loop / pause status payloads,  #
# stripped to the fields the executor deterministically assigns.               #
# --------------------------------------------------------------------------- #

_EXECUTOR_TYPES = frozenset(
    {
        "goal_loop_continuation",
        "goal_loop_wrap_up",
        "goal_loop_exhausted",
        "goal_paused",
        "goal_loop_complete",
    }
)


def _executor_sequence(items: list[Any]) -> list[dict[str, Any]]:
    """Ordered list of the executor-emitted status payloads (verbatim dicts)."""
    out: list[dict[str, Any]] = []
    for it in items:
        payload = getattr(it, "payload", None)
        if isinstance(payload, dict) and payload.get("type") in _EXECUTOR_TYPES:
            out.append(dict(payload))
    return out


def _build_driver(runner: FakeRunner, reader: _LedgerReader) -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=runner,
        user_id="cli",
        evidence_first=True,
        plan_ledger_reader=reader,
        required_evidence=(),
        auto_continue_enabled=True,
    )


# --------------------------------------------------------------------------- #
# (a) continue -> re-invoke, then complete when the ledger clears              #
# --------------------------------------------------------------------------- #


def test_golden_continue_then_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRunner(
        events_per_call=[
            [_ok_tool_end(), _text("I'll continue with the next step.")],
            [_text("All done.")],
        ]
    )
    _patch_lazy_deps(monkeypatch, runner)
    reader = _LedgerReader(
        [
            _todos(("t1", "pending"), ("t2", "pending")),
            _todos(("t1", "completed"), ("t2", "pending")),
            _todos(("t1", "completed"), ("t2", "completed")),
            _todos(("t1", "completed"), ("t2", "completed")),
        ]
    )
    items = _run_drive(_build_driver(runner, reader))

    assert len(runner.calls) == 2
    assert _executor_sequence(items) == [
        {
            "type": "goal_loop_continuation",
            "reason": "progress",
            "continuation": 1,
            "openTodos": 1,
            "source": "auto_continue",
        },
        {
            "type": "goal_loop_complete",
            "reason": "ledger_all_complete",
            "continuations": 0,
        },
    ]
    assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (b) + (c) two consecutive no-progress -> wrap_up -> honest no_progress pause #
# --------------------------------------------------------------------------- #


def test_golden_no_progress_wrap_up_then_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FakeRunner(
        events_per_call=[
            [_text("I'll continue.")],
            [_text("I'll continue.")],
            [_text("Working on it.")],
        ]
    )
    _patch_lazy_deps(monkeypatch, runner)
    reader = _LedgerReader([_todos(("t1", "in_progress"))])
    items = _run_drive(_build_driver(runner, reader))

    assert len(runner.calls) == 3
    assert _executor_sequence(items) == [
        {
            "type": "goal_loop_continuation",
            "reason": "no_progress_retry",
            "continuation": 1,
            "openTodos": 1,
            "source": "auto_continue",
        },
        {
            "type": "goal_loop_wrap_up",
            "reason": "no_progress_wrap_up",
            "continuation": 2,
            "openTodos": 1,
            "source": "auto_continue",
        },
        {
            "type": "goal_paused",
            "reason": "no_progress",
            "objective": None,
            "openTodos": 1,
        },
    ]
    assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (d) blocked-only attempt -> immediate waiting_on_approvals pause             #
# --------------------------------------------------------------------------- #


def test_golden_blocked_only_waiting_on_approvals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FakeRunner(
        events_per_call=[[_blocked_tool_end(), _text("Waiting on approval.")]]
    )
    _patch_lazy_deps(monkeypatch, runner)
    reader = _LedgerReader([_todos(("t1", "in_progress"))])
    items = _run_drive(_build_driver(runner, reader))

    assert len(runner.calls) == 1
    assert _executor_sequence(items) == [
        {
            "type": "goal_paused",
            "reason": "waiting_on_approvals",
            "objective": None,
            "openTodos": 1,
        },
    ]
    assert items[-1].terminal == Terminal.completed


# --------------------------------------------------------------------------- #
# (e) max-continuations budget stop -> goal_loop_exhausted + pause             #
# --------------------------------------------------------------------------- #


def test_golden_max_continuations_budget_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import magi_agent.runtime.goal_loop_auto_continue as ac_mod

    tiny = ac_mod.AutoContinueBudgets(max_continuations=2, wall_clock_seconds=0)
    monkeypatch.setattr(ac_mod, "budgets_for_intensity", lambda *, mission: tiny)
    runner = FakeRunner(
        events_per_call=[[_ok_tool_end(f"c{i}"), _text("step")] for i in range(6)]
    )
    _patch_lazy_deps(monkeypatch, runner)
    reader = _LedgerReader([_todos((f"t{i}", "in_progress")) for i in range(20)])
    items = _run_drive(_build_driver(runner, reader))

    assert len(runner.calls) == 3
    assert _executor_sequence(items) == [
        {
            "type": "goal_loop_continuation",
            "reason": "progress",
            "continuation": 1,
            "openTodos": 1,
            "source": "auto_continue",
        },
        {
            "type": "goal_loop_continuation",
            "reason": "progress",
            "continuation": 2,
            "openTodos": 1,
            "source": "auto_continue",
        },
        {
            "type": "goal_loop_exhausted",
            "reason": "max_continuations",
            "continuations": 2,
        },
        {
            "type": "goal_paused",
            "reason": "budget_exhausted",
            "objective": None,
            "openTodos": 1,
        },
    ]
    assert items[-1].terminal == Terminal.completed


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
