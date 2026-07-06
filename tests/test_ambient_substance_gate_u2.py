"""U2 (ambient goal loop) - substance signals for the ambient continuation gate.

This unit adds two INERT pieces to the engine driver (design section 4.2 and
6.2): the pure ``_turn_is_substantive`` helper and a turn-cumulative
``turn_tool_ends_total`` counter (signal S1). Nothing reads them yet; U5 wires
the substance gate. These tests pin their contract RED-first:

- ``_turn_is_substantive`` truth table over all 8 combinations of (S1, S2, S3),
  asserting OR semantics, plus the ``open_todos is None`` (empty-ledger) case.
- ``turn_tool_ends_total`` accumulates across MULTIPLE re-invocations within one
  turn (here: goal-nudge self-check re-invocations) and is NEVER reset per
  attempt. The inert counter is mirrored to a private ``_last_turn_tool_ends_total``
  attribute purely so this test can observe it; no live branch reads it, so turn
  output stays byte-identical (proven separately by the untouched golden suite).
"""
from __future__ import annotations

import pytest

from magi_agent.engine.driver import _turn_is_substantive
from magi_agent.runtime.goal_nudge import GoalNudge

# Reuse the existing driver multi-attempt harness (fake ADK adapter + runner).
from tests.cli.test_engine_goal_nudge import (
    FakeRunner,
    _make_driver,
    _patch_lazy_deps,
    _run_drive,
)


# ---------------------------------------------------------------------------
# _turn_is_substantive: pure OR truth table over S1/S2/S3
# ---------------------------------------------------------------------------

# (S1, S2, S3) -> expected. Encoded as concrete helper inputs below.
_TRUTH_TABLE = [
    (False, False, False, False),
    (False, False, True, True),
    (False, True, False, True),
    (False, True, True, True),
    (True, False, False, True),
    (True, False, True, True),
    (True, True, False, True),
    (True, True, True, True),
]


@pytest.mark.parametrize("s1, s2, s3, expected", _TRUTH_TABLE)
def test_turn_is_substantive_truth_table(
    s1: bool, s2: bool, s3: bool, expected: bool
) -> None:
    result = _turn_is_substantive(
        tool_ends_total=1 if s1 else 0,
        # S2 false uses an explicit ledger-present-but-no-open-todos snapshot (0)
        # to prove 0 open todos is NOT substantive (distinct from the None case).
        open_todos=1 if s2 else 0,
        new_evidence_records=1 if s3 else 0,
    )
    assert result is expected


def test_turn_is_substantive_none_ledger_is_not_substantive() -> None:
    # ``_open_todo_count`` returns None for an empty ledger snapshot; that must
    # read as NOT substantive on the S2 axis (only S1/S3 can rescue it).
    assert _turn_is_substantive(
        tool_ends_total=0, open_todos=None, new_evidence_records=0
    ) is False
    assert _turn_is_substantive(
        tool_ends_total=1, open_todos=None, new_evidence_records=0
    ) is True
    assert _turn_is_substantive(
        tool_ends_total=0, open_todos=None, new_evidence_records=1
    ) is True


def test_turn_is_substantive_counts_higher_values() -> None:
    # OR semantics hold for counts > 1 too (no accidental == 1 gate).
    assert _turn_is_substantive(
        tool_ends_total=5, open_todos=0, new_evidence_records=0
    ) is True
    assert _turn_is_substantive(
        tool_ends_total=0, open_todos=3, new_evidence_records=0
    ) is True
    assert _turn_is_substantive(
        tool_ends_total=0, open_todos=0, new_evidence_records=7
    ) is True


# ---------------------------------------------------------------------------
# turn_tool_ends_total: cumulative across re-invocations, never reset per attempt
# ---------------------------------------------------------------------------


def test_turn_tool_ends_total_accumulates_across_reinvocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A goal-nudge turn re-invokes the run; the counter must sum tool_end
    events across ALL attempts (ok OR blocked), never resetting per attempt."""
    runner = FakeRunner(
        events_per_call=[
            # attempt 1: tool round-trip (ok) + text -> clean stop -> nudge #1
            [
                {"type": "tool_start", "id": "c1", "name": "Bash"},
                {"type": "tool_end", "id": "c1", "status": "ok"},
                {"type": "text_delta", "text": "step one"},
            ],
            # attempt 2: tool round-trip (blocked) + text -> clean stop -> nudge #2
            [
                {"type": "tool_start", "id": "c2", "name": "Bash"},
                {"type": "tool_end", "id": "c2", "status": "blocked"},
                {"type": "text_delta", "text": "step two"},
            ],
            # attempt 3: text only, no tool -> goal latch holds -> break
            [{"type": "text_delta", "text": "done"}],
        ]
    )
    _patch_lazy_deps(monkeypatch, runner)
    nudge = GoalNudge(goal="finish", mode="goal", max_nudges=3, required_evidence=())
    driver, _ = _make_driver(runner, goal_nudge=nudge)

    _run_drive(driver)

    # Initial + two nudge re-invocations = 3 run_async calls (multi-attempt turn).
    assert len(runner.calls) == 3
    # Two tool_end events (one ok in attempt 1, one blocked in attempt 2),
    # accumulated across re-invocations and NOT reset per attempt.
    assert driver._last_turn_tool_ends_total == 2


def test_turn_tool_ends_total_zero_for_toolless_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conversational (no-tool) turn leaves the counter at 0 (S1 false)."""
    runner = FakeRunner(events_per_call=[[{"type": "text_delta", "text": "hi"}]])
    _patch_lazy_deps(monkeypatch, runner)
    driver, _ = _make_driver(runner, goal_nudge=None)

    _run_drive(driver)

    assert len(runner.calls) == 1
    assert driver._last_turn_tool_ends_total == 0
