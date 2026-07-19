"""Unit tests for the deterministic auto-continue decision layer.

These are pure-function tests: no ADK, no engine, no I/O. They pin the
measurable-progress brake that keeps the ledger-first auto-continue loop from
running away (the judge is fail-closed toward continuing, so the ONLY runaway
direction is judge-spin; the brake here is keyed on real tool effects / ledger
deltas / new evidence, never on model judgment).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from magi_agent.runtime.goal_loop_auto_continue import (
    AMBIENT_BUDGETS,
    MISSION_BUDGETS,
    NO_PROGRESS_LIMIT,
    AttemptProgress,
    AutoContinueBudgets,
    attempt_made_progress,
    budgets_for_intensity,
    decide_auto_continue,
    ledger_changed,
    ledger_open_snapshot,
)


@dataclass(frozen=True)
class _Todo:
    content: str
    status: str


def _progress(
    *,
    ok: int = 0,
    blocked: int = 0,
    ledger: bool = False,
    evidence: int = 0,
    approval: tuple[str, ...] = (),
) -> AttemptProgress:
    return AttemptProgress(
        ok_tool_ends=ok,
        blocked_tool_ends=blocked,
        ledger_changed=ledger,
        new_evidence_records=evidence,
        approval_fingerprint=approval,
    )


# ---------------------------------------------------------------------------
# Budgets (VERY GENEROUS + default-ON, ambient vs mission intensity)
# ---------------------------------------------------------------------------


class TestBudgets:
    def test_ambient_budgets_are_generous(self) -> None:
        assert AMBIENT_BUDGETS.max_continuations >= 20
        assert AMBIENT_BUDGETS.wall_clock_seconds >= 60 * 60
        assert AMBIENT_BUDGETS.no_progress_limit == NO_PROGRESS_LIMIT

    def test_mission_budgets_exceed_ambient(self) -> None:
        assert MISSION_BUDGETS.max_continuations >= AMBIENT_BUDGETS.max_continuations
        assert (
            MISSION_BUDGETS.wall_clock_seconds >= AMBIENT_BUDGETS.wall_clock_seconds
        )

    def test_budgets_for_intensity_selects_set(self) -> None:
        assert budgets_for_intensity(mission=False) is AMBIENT_BUDGETS
        assert budgets_for_intensity(mission=True) is MISSION_BUDGETS


# ---------------------------------------------------------------------------
# Progress signal
# ---------------------------------------------------------------------------


class TestAttemptProgress:
    def test_ok_tool_end_is_progress(self) -> None:
        assert attempt_made_progress(_progress(ok=1)) is True

    def test_ledger_delta_is_progress(self) -> None:
        assert attempt_made_progress(_progress(ledger=True)) is True

    def test_new_evidence_is_progress(self) -> None:
        assert attempt_made_progress(_progress(evidence=2)) is True

    def test_blocked_only_is_not_progress(self) -> None:
        assert attempt_made_progress(_progress(blocked=3)) is False

    def test_nothing_is_not_progress(self) -> None:
        assert attempt_made_progress(_progress()) is False


# ---------------------------------------------------------------------------
# Ledger delta
# ---------------------------------------------------------------------------


class TestLedgerDelta:
    def test_identical_snapshots_no_change(self) -> None:
        a = (_Todo("t1", "in_progress"),)
        b = (_Todo("t1", "in_progress"),)
        assert ledger_changed(a, b) is False

    def test_status_advance_is_change(self) -> None:
        a = (_Todo("t1", "in_progress"),)
        b = (_Todo("t1", "completed"),)
        assert ledger_changed(a, b) is True

    def test_new_todo_is_change(self) -> None:
        a = (_Todo("t1", "completed"),)
        b = (_Todo("t1", "completed"), _Todo("t2", "pending"))
        assert ledger_changed(a, b) is True

    def test_empty_to_empty_no_change(self) -> None:
        assert ledger_changed((), ()) is False

    def test_projection_is_defensive_on_bad_items(self) -> None:
        # An item lacking content/status must not raise.
        class _Bad:
            pass

        snap = ledger_open_snapshot((_Bad(),))
        assert snap == (("None", "None"),)


# ---------------------------------------------------------------------------
# Decision ladder
# ---------------------------------------------------------------------------


class TestDecideAutoContinue:
    def test_ledger_no_continue_is_inert_stop(self) -> None:
        d = decide_auto_continue(
            ledger_wants_continue=False,
            progress=_progress(ok=5),
            continuations_used=0,
            prior_no_progress_streak=0,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "stop"
        assert d.reason == "ledger_no_continue"

    def test_progress_continues_and_resets_streak(self) -> None:
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1),
            continuations_used=3,
            prior_no_progress_streak=1,
            elapsed_seconds=10.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "continue"
        assert d.reason == "progress"
        assert d.no_progress_streak == 0

    def test_max_continuations_is_hard_stop(self) -> None:
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1),
            continuations_used=AMBIENT_BUDGETS.max_continuations,
            prior_no_progress_streak=0,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "stop_budget"
        assert d.reason == "max_continuations"

    def test_wall_clock_is_hard_stop(self) -> None:
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1),
            continuations_used=0,
            prior_no_progress_streak=0,
            elapsed_seconds=AMBIENT_BUDGETS.wall_clock_seconds + 1,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "stop_budget"
        assert d.reason == "wall_clock"

    def test_wall_clock_disabled_when_zero(self) -> None:
        budgets = AutoContinueBudgets(max_continuations=20, wall_clock_seconds=0)
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1),
            continuations_used=0,
            prior_no_progress_streak=0,
            elapsed_seconds=10_000_000.0,
            budgets=budgets,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "continue"

    def test_blocked_only_pauses_waiting_on_approvals(self) -> None:
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(blocked=2),
            continuations_used=0,
            prior_no_progress_streak=0,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "paused_waiting_on_approvals"
        assert d.reason == "waiting_on_approvals"

    def test_first_no_progress_retries(self) -> None:
        # Streak 0 -> 1, below the limit of 2, so continue once more.
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(),
            continuations_used=0,
            prior_no_progress_streak=0,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "continue"
        assert d.reason == "no_progress_retry"
        assert d.no_progress_streak == 1

    def test_second_no_progress_triggers_wrap_up(self) -> None:
        # Streak 1 -> 2, hits the limit, wrap-up not yet spent -> wrap_up.
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(),
            continuations_used=1,
            prior_no_progress_streak=1,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert d.outcome == "wrap_up"
        assert d.reason == "no_progress_wrap_up"
        assert d.no_progress_streak == 2

    def test_no_progress_after_wrap_up_pauses(self) -> None:
        # Wrap-up already spent + still no progress -> honest pause.
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(),
            continuations_used=2,
            prior_no_progress_streak=1,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=True,
        )
        assert d.outcome == "paused_no_progress"
        assert d.reason == "no_progress"

    def test_repeated_approval_set_pauses_despite_interleaved_ok(self) -> None:
        # The incident shape: the same non-empty approval-required control set
        # comes back this attempt as last attempt, but unrelated ok tool ends
        # also succeeded. The approval brake must win over the "ok tool end =
        # progress" reset, so the loop pauses instead of re-injecting the
        # obligations forever.
        fp = ("control:general-automation:sha256:abc", "control:general-automation:sha256:def")
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=5, blocked=2, approval=fp),
            continuations_used=3,
            prior_no_progress_streak=0,
            elapsed_seconds=10.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
            prior_approval_fingerprint=fp,
        )
        assert d.outcome == "paused_waiting_on_approvals"
        assert d.reason == "waiting_on_approvals"

    def test_shrinking_approval_set_allows_continue(self) -> None:
        # Some approvals resolved between attempts (the set shrank), so the
        # fingerprint differs from the prior one: this is genuine progress on
        # the obligations and the brake must NOT fire. With an ok tool end the
        # attempt continues.
        prior = ("control:a", "control:b")
        now = ("control:a",)
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1, blocked=1, approval=now),
            continuations_used=1,
            prior_no_progress_streak=0,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
            prior_approval_fingerprint=prior,
        )
        assert d.outcome == "continue"
        assert d.reason == "progress"

    def test_first_approval_occurrence_does_not_pause(self) -> None:
        # First time this approval set is seen (prior fingerprint empty): a
        # single occurrence must not pause; with progress it continues.
        now = ("control:a",)
        d = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1, blocked=1, approval=now),
            continuations_used=0,
            prior_no_progress_streak=0,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
            prior_approval_fingerprint=(),
        )
        assert d.outcome == "continue"
        assert d.reason == "progress"

    def test_no_approval_obligations_is_byte_identical(self) -> None:
        # No approval fingerprint at all: the new brake is inert and both a
        # representative continue and a representative stop match the pre-brake
        # decisions exactly (default prior fingerprint = empty).
        cont = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1),
            continuations_used=3,
            prior_no_progress_streak=1,
            elapsed_seconds=10.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert cont.outcome == "continue"
        assert cont.reason == "progress"
        assert cont.no_progress_streak == 0

        stop = decide_auto_continue(
            ledger_wants_continue=True,
            progress=_progress(ok=1),
            continuations_used=AMBIENT_BUDGETS.max_continuations,
            prior_no_progress_streak=0,
            elapsed_seconds=0.0,
            budgets=AMBIENT_BUDGETS,
            wrap_up_already_spent=False,
        )
        assert stop.outcome == "stop_budget"
        assert stop.reason == "max_continuations"

    def test_no_infinite_loop_progress_then_stall(self) -> None:
        # Simulate a run: progress, progress, stall, stall -> wrap_up, stall
        # -> paused_no_progress. Prove it terminates in a bounded number of
        # steps with no model call.
        streak = 0
        wrap_up_spent = False
        outcomes: list[str] = []
        script = [
            _progress(ok=1),  # progress
            _progress(ledger=True),  # progress
            _progress(),  # stall 1
            _progress(),  # stall 2 -> wrap_up
            _progress(),  # stall after wrap_up -> paused_no_progress
        ]
        used = 0
        for prog in script:
            d = decide_auto_continue(
                ledger_wants_continue=True,
                progress=prog,
                continuations_used=used,
                prior_no_progress_streak=streak,
                elapsed_seconds=0.0,
                budgets=AMBIENT_BUDGETS,
                wrap_up_already_spent=wrap_up_spent,
            )
            outcomes.append(d.outcome)
            streak = d.no_progress_streak
            if d.outcome == "wrap_up":
                wrap_up_spent = True
            if d.outcome in {"continue", "wrap_up"}:
                used += 1
            if d.outcome.startswith("paused") or d.outcome.startswith("stop"):
                break
        assert outcomes == [
            "continue",
            "continue",
            "continue",
            "wrap_up",
            "paused_no_progress",
        ]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
