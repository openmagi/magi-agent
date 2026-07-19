"""Deterministic auto-continue decision layer for the engine clean-break loop.

Root cause this addresses (verified in ``engine/driver.py`` SEAM 2): at the clean
break the driver already reads the durable todo ledger and computes a
``"continue"`` outcome via ``resolve_pre_judge_outcome``, then deliberately
DEGRADES to a bare ``break`` because continuation authority historically lived
only inside the goal-loop judge, which was double-gated (a per-send composer
toggle AND a strict default-OFF ``MAGI_GOAL_LOOP_ENABLED``). Both off meant the
agent stopped mid-multi-step-task and reported "I'll continue..." then ended the
turn.

This module supplies the missing authority WITHOUT handing the runaway direction
to an LLM. Completion is still judged by the existing evidence-first ladder in
``goal_loop_evidence.resolve_pre_judge_outcome`` (ledger-first, no model call for
the common case). What lives HERE is the *brake*: whether a computed
``"continue"`` is allowed to actually re-invoke the runner.

The brake is a MEASURABLE-PROGRESS gate, NOT a model judgment. The judge is
fail-closed toward continuing, so the only runaway direction is judge-spin; a
progress gate keyed on real tool effects / ledger deltas / new evidence records
bounds it deterministically:

* A continuation is only "productive" if the just-finished attempt produced at
  least one *ok* ``tool_end`` (blocked / needs-approval / errored tool ends do
  NOT count), OR the durable todo ledger changed (a delta), OR new evidence
  records were collected.
* An attempt that carries the SAME non-empty approval-required control set as
  the immediately-prior attempt (the owed approvals did NOT shrink) pauses with
  ``goal_paused(waiting_on_approvals)`` EVEN when unrelated ok tool ends made
  the attempt look productive. Progress on unrelated tools must not defeat the
  approval brake when the approval set itself is not shrinking; otherwise a run
  that mixes blocked approval calls with successful unrelated calls re-injects
  the same obligations forever until the wall-clock ceiling.
* Two consecutive no-progress continuations trigger ONE wrap-up invocation
  ("report what is done / not done"); if that too makes no progress the turn
  pauses honestly with ``goal_paused(no_progress)``.
* An attempt whose ONLY activity was blocked / needs-approval tool ends (no ok
  tool end, no ledger delta, no new evidence) pauses immediately with
  ``goal_paused(waiting_on_approvals)`` rather than spinning.
* Hard budgets (max continuations, wall-clock) cap the loop regardless.

Pure: no I/O, no model call, no ADK import, no clock read (the caller passes the
elapsed seconds). Trivially unit-testable.

Design: highest-leverage agent-auto-continue fix, ledger-first decision ladder +
ambient/mission intensity budgets. The Goal-mission composer toggle becomes an
INTENSITY control (ambient vs mission budgets), not an on/off master.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "AutoContinueBudgets",
    "AttemptProgress",
    "AutoContinueDecision",
    "AutoContinueOutcome",
    "AMBIENT_BUDGETS",
    "MISSION_BUDGETS",
    "budgets_for_intensity",
    "attempt_made_progress",
    "ledger_open_snapshot",
    "ledger_changed",
    "decide_auto_continue",
]

#: The five terminal + one non-terminal outcomes the engine branch acts on.
#:
#: * ``continue``: re-invoke the runner with the continuation prompt.
#: * ``wrap_up``: re-invoke ONCE with a wrap-up prompt (report done / not done).
#: * ``paused_no_progress``: emit ``goal_paused(no_progress)`` and stop.
#: * ``paused_waiting_on_approvals``: emit ``goal_paused(waiting_on_approvals)``
#:   and stop (the attempt only produced blocked / needs-approval tool ends).
#: * ``stop_budget``: a hard budget (max continuations / wall-clock) was hit.
#: * ``stop``: nothing to continue (the ledger-first ladder did not ask to
#:   continue); the engine keeps its pre-existing terminal behaviour.
AutoContinueOutcome = Literal[
    "continue",
    "wrap_up",
    "paused_no_progress",
    "paused_waiting_on_approvals",
    "stop_budget",
    "stop",
]

#: How many consecutive no-progress continuations are tolerated before the
#: engine spends its single wrap-up invocation. The design fixes this at 2
#: ("2 consecutive no-progress continuations -> ONE wrap-up invocation").
NO_PROGRESS_LIMIT = 2


@dataclass(frozen=True)
class AutoContinueBudgets:
    """Hard bounds on the ambient / mission auto-continue loop.

    Kevin policy: budgets are VERY GENEROUS and default-ON (hosted too). The
    brake that actually prevents runaway is the measurable-progress gate, not
    these ceilings; the ceilings are the last-resort backstop.
    """

    #: Upper bound on productive continuations within a single turn.
    max_continuations: int
    #: Wall-clock ceiling in seconds; ``0`` disables the wall-clock check.
    wall_clock_seconds: int
    #: Consecutive no-progress continuations tolerated before the wrap-up
    #: invocation is spent. Kept per-budget so mission mode can differ if ever
    #: needed; both currently use :data:`NO_PROGRESS_LIMIT`.
    no_progress_limit: int = NO_PROGRESS_LIMIT


#: Ambient budgets: the loop is ON for EVERY turn (the composer toggle is an
#: intensity control, not an on/off). Generous per Kevin's policy: 20
#: continuations, 60 minutes wall-clock.
AMBIENT_BUDGETS = AutoContinueBudgets(
    max_continuations=20,
    wall_clock_seconds=60 * 60,
)

#: Mission budgets: the composer Goal-mission toggle raises the ceiling. Even
#: more generous: 40 continuations, 120 minutes.
MISSION_BUDGETS = AutoContinueBudgets(
    max_continuations=40,
    wall_clock_seconds=120 * 60,
)


def budgets_for_intensity(*, mission: bool) -> AutoContinueBudgets:
    """Return the budget set for the requested intensity.

    ``mission`` True (the composer Goal-mission toggle was on) selects the
    higher :data:`MISSION_BUDGETS`; otherwise the ambient default.
    """
    return MISSION_BUDGETS if mission else AMBIENT_BUDGETS


@dataclass(frozen=True)
class AttemptProgress:
    """The measurable signals produced by ONE runner attempt.

    All three are things the engine can observe deterministically from the
    projected event stream + the durable ledger + the evidence collector. None
    of them is a model judgment.
    """

    #: Count of ``tool_end`` events with ``status == "ok"`` this attempt.
    #: Blocked / needs-approval / errored tool ends are excluded by the caller.
    ok_tool_ends: int
    #: Count of ``tool_end`` events that were blocked / needs-approval this
    #: attempt (projected as ``status != "ok"``). Used to distinguish the
    #: "only blocked activity" pause from a genuine no-progress stall.
    blocked_tool_ends: int
    #: True iff the durable todo ledger changed vs the snapshot captured before
    #: this attempt (computed by the caller via :func:`ledger_changed`).
    ledger_changed: bool
    #: Count of NEW evidence records collected during this attempt (delta vs the
    #: count captured before it).
    new_evidence_records: int
    #: Sorted, de-duplicated approval-required control identifiers observed in
    #: THIS attempt's tool_end stream (the ``controlRef`` of every ``tool_end``
    #: whose control projection is ``approval_required``, plus owed-artifact
    #: markers). Empty when the attempt raised no approval-required control, so
    #: the approval brake below is inert and decisions stay byte-identical.
    approval_fingerprint: tuple[str, ...] = ()


def attempt_made_progress(progress: AttemptProgress) -> bool:
    """True iff the attempt produced measurable progress.

    Progress is any of: >=1 ok tool end, a ledger delta, or >=1 new evidence
    record. Blocked / needs-approval tool ends explicitly do NOT count (that is
    the whole point: they are the runaway direction).
    """
    return (
        progress.ok_tool_ends > 0
        or progress.ledger_changed
        or progress.new_evidence_records > 0
    )


def ledger_open_snapshot(ledger_snapshot: tuple[object, ...]) -> tuple[tuple[str, str], ...]:
    """Project a ledger snapshot to a comparable ``(content, status)`` tuple.

    Defensive ``getattr`` so a malformed item can never raise inside the loop.
    Order-preserving; two snapshots are equal iff every item's content + status
    match in order.
    """
    projected: list[tuple[str, str]] = []
    for item in ledger_snapshot:
        content = getattr(item, "content", None)
        status = getattr(item, "status", None)
        projected.append((str(content), str(status)))
    return tuple(projected)


def ledger_changed(
    before: tuple[object, ...],
    after: tuple[object, ...],
) -> bool:
    """True iff the durable todo ledger changed between two snapshots.

    A change is any difference in the ordered ``(content, status)`` projection:
    a todo completed, a new todo added, a status advanced, etc. An empty->empty
    or identical->identical transition is NOT a change.
    """
    return ledger_open_snapshot(before) != ledger_open_snapshot(after)


@dataclass(frozen=True)
class AutoContinueDecision:
    """The engine-facing decision: what to do at the clean break."""

    outcome: AutoContinueOutcome
    #: Human-readable reason code for the emitted status / pause event.
    reason: str
    #: The no-progress streak AFTER folding in this attempt (for telemetry and
    #: for the engine to carry into the next iteration).
    no_progress_streak: int


def decide_auto_continue(
    *,
    ledger_wants_continue: bool,
    progress: AttemptProgress,
    continuations_used: int,
    prior_no_progress_streak: int,
    elapsed_seconds: float,
    budgets: AutoContinueBudgets,
    wrap_up_already_spent: bool,
    prior_approval_fingerprint: tuple[str, ...] = (),
) -> AutoContinueDecision:
    """Decide whether a computed ledger ``continue`` is allowed to re-invoke.

    ``prior_approval_fingerprint`` is the approval-required control set observed
    on the IMMEDIATELY-PRIOR attempt (``progress.approval_fingerprint`` is this
    attempt's). Each attempt is exactly one model invocation, so an approval set
    that is non-empty and IDENTICAL across the prior and current attempt is two
    consecutive occurrences of an unchanged obligation (N=2) and triggers the
    approval brake; that is why the comparison needs no separate counter.

    Ladder (deterministic, no model call):

    1. If the ledger-first ladder did NOT ask to continue -> ``stop`` (the
       engine keeps its existing terminal behaviour; this function is inert).
    2. Hard budget: continuations already at ``max_continuations`` OR wall-clock
       exceeded -> ``stop_budget``.
    3. Approval brake: this attempt's approval-required control set is non-empty
       and IDENTICAL to the prior attempt's (the owed approvals did not shrink)
       -> ``paused_waiting_on_approvals``, EVEN if the attempt otherwise made
       measurable progress. Unrelated ok tool ends must not defeat the brake
       while the approval set is stuck. A shrunk / grown / first-seen set does
       not match the prior fingerprint, so it falls through to the progress
       gate (genuine movement on the obligations).
    4. The attempt made measurable progress -> ``continue`` (streak resets to 0).
    5. No progress AND the ONLY activity was blocked / needs-approval tool ends
       (no ok tool end, no ledger delta, no new evidence, >=1 blocked end) ->
       ``paused_waiting_on_approvals`` immediately (do not spin on approvals).
    6. No progress, folding this attempt into the streak reaches the
       ``no_progress_limit``:
         * if the single wrap-up invocation has NOT been spent -> ``wrap_up``.
         * otherwise -> ``paused_no_progress``.
    7. No progress but still under the streak limit -> ``continue`` (give the
       agent another concrete step; the streak carries forward).
    """
    if not ledger_wants_continue:
        return AutoContinueDecision(
            outcome="stop",
            reason="ledger_no_continue",
            no_progress_streak=prior_no_progress_streak,
        )

    if continuations_used >= budgets.max_continuations:
        return AutoContinueDecision(
            outcome="stop_budget",
            reason="max_continuations",
            no_progress_streak=prior_no_progress_streak,
        )
    if budgets.wall_clock_seconds > 0 and elapsed_seconds >= budgets.wall_clock_seconds:
        return AutoContinueDecision(
            outcome="stop_budget",
            reason="wall_clock",
            no_progress_streak=prior_no_progress_streak,
        )

    if (
        progress.approval_fingerprint
        and progress.approval_fingerprint == prior_approval_fingerprint
    ):
        # The same non-empty approval-required control set came back this
        # attempt as last attempt: the owed approvals are not shrinking, so
        # re-invoking cannot clear them. Pause honestly instead of riding
        # unrelated ok tool ends into a re-injection loop.
        return AutoContinueDecision(
            outcome="paused_waiting_on_approvals",
            reason="waiting_on_approvals",
            no_progress_streak=prior_no_progress_streak,
        )

    if attempt_made_progress(progress):
        return AutoContinueDecision(
            outcome="continue",
            reason="progress",
            no_progress_streak=0,
        )

    # No measurable progress from here down.
    if progress.blocked_tool_ends > 0:
        # The only thing the attempt did was hit blocked / needs-approval tool
        # ends. Spinning cannot clear an approval the model cannot grant.
        return AutoContinueDecision(
            outcome="paused_waiting_on_approvals",
            reason="waiting_on_approvals",
            no_progress_streak=prior_no_progress_streak,
        )

    streak = prior_no_progress_streak + 1
    if streak >= budgets.no_progress_limit:
        if not wrap_up_already_spent:
            return AutoContinueDecision(
                outcome="wrap_up",
                reason="no_progress_wrap_up",
                no_progress_streak=streak,
            )
        return AutoContinueDecision(
            outcome="paused_no_progress",
            reason="no_progress",
            no_progress_streak=streak,
        )
    return AutoContinueDecision(
        outcome="continue",
        reason="no_progress_retry",
        no_progress_streak=streak,
    )
