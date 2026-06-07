"""Task 4 — repeated evaluation + adaptive escalation.

A real agent-driven evaluator is NONDETERMINISTIC: a single eval is noisy, so a
genuinely-good candidate often lands ``inconclusive``.  Repeating the eval and
averaging per-case scores shrinks the per-case noise → shrinks the delta SE →
can resolve ``inconclusive`` into ``improved``/``regressed``.

These tests pin:
  * ``RepeatedCheckSet`` averaging semantics (deterministic, multi-value,
    length-guard);
  * adaptive escalation in ``run_eval_gate`` (inconclusive → escalate up to the
    cap → improved; the final repeat count surfaces on the decision + stats);
  * underpowered does NOT escalate (more repeats can't add cases);
  * the ``n_repeats=1, max_repeats=1`` default is byte-identical to single-shot.
"""
from __future__ import annotations

import pytest

from magi_agent.learning.candidates import LearningCandidate
from magi_agent.learning.eval_gate import (
    EvalGateConfig,
    RepeatedCheckSet,
    StaticCheckSet,
    run_eval_gate,
)
from magi_agent.learning.models import LearningScope, Provenance
from magi_agent.learning.store import SqliteLearningStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(tmp_path) -> SqliteLearningStore:
    return SqliteLearningStore(db_path="learning.db", workspace_root=str(tmp_path))


def _candidate(*, kind: str = "example", sid: str = "sess-1") -> LearningCandidate:
    if kind == "rule":
        content = {"when": "user asks", "then": "be concise"}
    elif kind == "eval":
        content = {"input": "user asks", "expected": "concise"}
    else:
        content = {"situation": "user asks", "behavior": "be concise"}
    return LearningCandidate(
        kind=kind,
        scope=LearningScope(taskKind="general", tags=("style",)),
        content=content,
        rationale="prefer concise answers",
        provenance=Provenance(
            sessionIds=(sid,),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
        sourceSignalRef=f"signal:diff@{sid}",
    )


class _SequenceCheckSet:
    """Noisy fake CheckSet whose per-call output is deterministic.

    Each ``run`` pops the next ``(before, after)`` tuple from a fixed sequence,
    cycling when exhausted.  The per-call deltas vary (so a single eval is
    noisy / inconclusive), but the AVERAGE over a full cycle is a clear
    improvement — so averaging across repeats shrinks the SE and resolves the
    verdict.  Deterministic ⇒ the test is reproducible.
    """

    def __init__(
        self,
        sequence: tuple[tuple[tuple[float, ...], tuple[float, ...]], ...],
    ) -> None:
        self._sequence = sequence
        self._i = 0
        self.calls = 0

    def run(
        self, candidate: LearningCandidate
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        out = self._sequence[self._i % len(self._sequence)]
        self._i += 1
        self.calls += 1
        return out


# ---------------------------------------------------------------------------
# RepeatedCheckSet unit
# ---------------------------------------------------------------------------


def test_repeated_deterministic_inner_equals_single() -> None:
    inner = StaticCheckSet(before=(0.5, 0.4, 0.6, 0.5), after=(0.8, 0.9, 0.9, 0.9))
    wrapped = RepeatedCheckSet(inner=inner, repeats=5)
    single = inner.run(_candidate())
    averaged = wrapped.run(_candidate())
    assert averaged == single


def test_repeated_averages_element_wise() -> None:
    seq = (
        ((0.0, 1.0), (1.0, 0.0)),
        ((1.0, 0.0), (0.0, 1.0)),
    )
    inner = _SequenceCheckSet(seq)
    wrapped = RepeatedCheckSet(inner=inner, repeats=2)
    before, after = wrapped.run(_candidate())
    # element-wise mean of the two before tuples / after tuples
    assert before == (0.5, 0.5)
    assert after == (0.5, 0.5)
    assert inner.calls == 2


def test_repeated_requires_positive_repeats() -> None:
    inner = StaticCheckSet(before=(0.5,), after=(0.9,))
    with pytest.raises(ValueError):
        RepeatedCheckSet(inner=inner, repeats=0)
    with pytest.raises(ValueError):
        RepeatedCheckSet(inner=inner, repeats=-3)


def test_repeated_length_mismatch_across_runs_guarded() -> None:
    # Inner returns different-length tuples between calls → incomparable.
    seq = (
        ((0.5, 0.5), (0.9, 0.9)),
        ((0.5,), (0.9,)),
    )
    inner = _SequenceCheckSet(seq)
    wrapped = RepeatedCheckSet(inner=inner, repeats=2)
    with pytest.raises(ValueError):
        wrapped.run(_candidate())


def test_repeated_before_after_length_mismatch_guarded() -> None:
    inner = StaticCheckSet(before=(0.5, 0.5), after=(0.9,))
    wrapped = RepeatedCheckSet(inner=inner, repeats=1)
    with pytest.raises(ValueError):
        wrapped.run(_candidate())


# ---------------------------------------------------------------------------
# Escalation in run_eval_gate
# ---------------------------------------------------------------------------


def _noisy_improving_sequence() -> (
    tuple[tuple[tuple[float, ...], tuple[float, ...]], ...]
):
    """A 4-case noisy sequence whose single-call delta is inconclusive but whose
    averaged delta (over repeats) is a clear, low-variance improvement."""
    # before is steady; after oscillates so a single call has HIGH per-case
    # variance (deltas span +/- → wide CI → inconclusive), but two opposite-phase
    # calls average to a steady positive delta (tight CI → improved).
    #   call A deltas: (.4, -.2, .4, -.2)  -> mean .1, se ~.17 -> inconclusive
    #   call B deltas: (-.2, .4, -.2, .4)  -> same, inconclusive
    #   avg(A,B):      (.1,  .1, .1,  .1)  -> se 0, delta .1 -> improved
    return (
        ((0.5, 0.5, 0.5, 0.5), (0.9, 0.3, 0.9, 0.3)),
        ((0.5, 0.5, 0.5, 0.5), (0.3, 0.9, 0.3, 0.9)),
    )


def test_single_repeat_is_inconclusive(tmp_path) -> None:
    store = _store(tmp_path)
    inner = _SequenceCheckSet(_noisy_improving_sequence())
    decisions = run_eval_gate(
        (_candidate(),),
        store=store,
        checkset=inner,
        config=EvalGateConfig(decisionRule="paired_significance"),  # n=1,max=1
    )
    store.close()
    d = decisions[0]
    assert d.verdict == "inconclusive"
    assert d.repeats == 1
    assert inner.calls == 1


def test_escalation_resolves_inconclusive_to_improved(tmp_path) -> None:
    store = _store(tmp_path)
    inner = _SequenceCheckSet(_noisy_improving_sequence())
    decisions = run_eval_gate(
        (_candidate(),),
        store=store,
        checkset=inner,
        config=EvalGateConfig(
            decisionRule="paired_significance",
            nRepeats=1,
            maxRepeats=8,
        ),
    )
    obs = store.get_eval_observation(decisions[0].eval_observation_ref)
    store.close()
    d = decisions[0]
    assert d.verdict == "improved"
    assert d.passed is True
    # escalated past the starting repeat count, capped at max_repeats.
    assert 1 < d.repeats <= 8
    # final repeats surfaced in the persisted stats.
    assert obs["stats"]["repeats"] == d.repeats
    # the averaged SE is small (noise collapsed by averaging).
    assert d.se < 0.05
    # Each escalation re-runs the averaged eval from scratch (repeats=1, then 2,
    # ... up to the final), so cumulative inner calls = sum(1..final_repeats).
    assert inner.calls == sum(range(1, d.repeats + 1))


def test_escalation_caps_at_max_repeats_when_still_inconclusive(tmp_path) -> None:
    # An inner whose averaged delta is ~0 (genuinely inconclusive) must escalate
    # all the way to the cap, then defer (still inconclusive).
    seq = (
        ((0.5, 0.5, 0.5, 0.5), (0.4, 0.6, 0.4, 0.6)),
        ((0.5, 0.5, 0.5, 0.5), (0.6, 0.4, 0.6, 0.4)),
    )
    store = _store(tmp_path)
    inner = _SequenceCheckSet(seq)
    decisions = run_eval_gate(
        (_candidate(),),
        store=store,
        checkset=inner,
        config=EvalGateConfig(
            decisionRule="paired_significance",
            nRepeats=1,
            maxRepeats=5,
        ),
    )
    store.close()
    d = decisions[0]
    assert d.verdict == "inconclusive"
    assert d.passed is False
    assert d.repeats == 5
    # cumulative inner calls = sum(1..5) (fresh re-run at each escalation step).
    assert inner.calls == sum(range(1, 6))


def test_underpowered_does_not_escalate(tmp_path) -> None:
    # n < min_sample_size → underpowered; more repeats can't add CASES, so the
    # loop must not escalate.
    store = _store(tmp_path)
    inner = _SequenceCheckSet((((0.5, 0.5), (0.9, 0.9)),))
    decisions = run_eval_gate(
        (_candidate(),),
        store=store,
        checkset=inner,
        config=EvalGateConfig(
            decisionRule="paired_significance",
            nRepeats=1,
            maxRepeats=5,
        ),
    )
    store.close()
    d = decisions[0]
    assert d.verdict == "underpowered"
    assert d.repeats == 1
    assert inner.calls == 1


def test_start_at_n_repeats_above_one(tmp_path) -> None:
    # n_repeats=3 with no headroom (max=3) → exactly 3 inner runs, no loop body.
    store = _store(tmp_path)
    inner = _SequenceCheckSet(_noisy_improving_sequence())
    decisions = run_eval_gate(
        (_candidate(),),
        store=store,
        checkset=inner,
        config=EvalGateConfig(
            decisionRule="paired_significance",
            nRepeats=3,
            maxRepeats=3,
        ),
    )
    store.close()
    d = decisions[0]
    assert d.repeats == 3
    assert inner.calls == 3


def test_default_config_single_run(tmp_path) -> None:
    # No n_repeats / max_repeats → single run, repeats==1, existing behavior.
    store = _store(tmp_path)
    inner = _SequenceCheckSet((((0.5, 0.5, 0.4, 0.6), (0.8, 0.9, 0.9, 0.9)),))
    decisions = run_eval_gate(
        (_candidate(),),
        store=store,
        checkset=inner,
        config=EvalGateConfig(decisionRule="paired_significance"),
    )
    store.close()
    d = decisions[0]
    assert d.repeats == 1
    assert inner.calls == 1
    assert d.verdict == "improved"
