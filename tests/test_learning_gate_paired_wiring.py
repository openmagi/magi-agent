"""Task 2 — wire ``decision_rule="paired_significance"`` into ``run_eval_gate``.

These tests drive ``run_eval_gate`` with the paired-significance decision rule
over the deterministic ``StaticCheckSet`` and assert the harm-gated, abstaining
activation policy:

- ``improved``      → passed, activated (example), verdict "improved".
- ``inconclusive``  → not passed, not activated, item stays proposed (defer).
- ``regressed``     → not passed, not activated, item stays proposed; the
  ``verdict="regressed"`` IS the rollback signal — no rollback called here.
- ``underpowered``  → not passed, not activated (defer — too few samples).
- ``rule`` + improved → never auto-activated (stays proposed).
- decision carries delta/se/ci fields.

The default ``strict_band`` path must stay byte-identical to today; one test
pins that on the same tuples.
"""
from __future__ import annotations

from magi_agent.learning.candidates import LearningCandidate
from magi_agent.learning.eval_gate import (
    EvalGateConfig,
    StaticCheckSet,
    run_eval_gate,
)
from magi_agent.learning.models import LearningScope, Provenance
from magi_agent.learning.store import SqliteLearningStore


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_learning_pr4_eval_gate.py)
# ---------------------------------------------------------------------------


def _store(tmp_path) -> SqliteLearningStore:
    return SqliteLearningStore(db_path="learning.db", workspace_root=str(tmp_path))


def _candidate(
    *,
    kind: str = "example",
    rationale: str = "prefer concise answers",
    tag: str = "style",
    sid: str = "sess-1",
) -> LearningCandidate:
    if kind == "rule":
        content = {"when": "user asks", "then": rationale}
    elif kind == "eval":
        content = {"input": "user asks", "expected": rationale}
    else:
        content = {"situation": "user asks", "behavior": rationale}
    return LearningCandidate(
        kind=kind,
        scope=LearningScope(taskKind="general", tags=(tag,)),
        content=content,
        rationale=rationale,
        provenance=Provenance(
            sessionIds=(sid,),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
        sourceSignalRef=f"signal:diff@{sid}",
    )


def _paired_config() -> EvalGateConfig:
    return EvalGateConfig(decisionRule="paired_significance")


# Tuples chosen to land each verdict from ``paired_verdict`` (min_n=4):
# - improved:     ci_low > 0 → consistent positive delta, non-zero variance.
# - regressed:    ci_high < 0 → consistent negative delta.
# - inconclusive: ci straddles 0 → delta near 0 with spread.
# - underpowered: n < 4.


def _improved_checkset() -> StaticCheckSet:
    # deltas (after-before): +0.3, +0.4, +0.5, +0.3 → all positive, ci_low > 0.
    return StaticCheckSet(
        before=(0.5, 0.5, 0.4, 0.6),
        after=(0.8, 0.9, 0.9, 0.9),
    )


def _regressed_checkset() -> StaticCheckSet:
    # deltas: -0.3, -0.4, -0.5, -0.3 → all negative, ci_high < 0.
    return StaticCheckSet(
        before=(0.8, 0.9, 0.9, 0.9),
        after=(0.5, 0.5, 0.4, 0.6),
    )


def _inconclusive_checkset() -> StaticCheckSet:
    # deltas: +0.4, -0.4, +0.4, -0.4 → mean 0, wide spread → ci straddles 0.
    return StaticCheckSet(
        before=(0.5, 0.9, 0.5, 0.9),
        after=(0.9, 0.5, 0.9, 0.5),
    )


def _underpowered_checkset() -> StaticCheckSet:
    # n=2 < min_n=4 → underpowered regardless of delta.
    return StaticCheckSet(before=(0.5, 0.5), after=(0.9, 0.9))


# ---------------------------------------------------------------------------
# Paired-significance verdict × activation matrix
# ---------------------------------------------------------------------------


def test_improved_example_activates(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_improved_checkset(),
        config=_paired_config(),
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()

    assert decision.verdict == "improved"
    assert decision.passed is True
    assert decision.activated is True
    assert decision.eval_observation_ref
    assert item is not None
    assert item.status == "active"


def test_inconclusive_example_deferred_stays_proposed(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_inconclusive_checkset(),
        config=_paired_config(),
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()

    assert decision.verdict == "inconclusive"
    assert decision.passed is False
    assert decision.activated is False
    assert decision.eval_observation_ref  # observation still recorded
    assert item is not None
    assert item.status == "proposed"


def test_regressed_example_not_activated_no_rollback(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_regressed_checkset(),
        config=_paired_config(),
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()

    # verdict="regressed" IS the rollback signal for downstream; the gate only
    # records it and never auto-activates / never rolls back here.
    assert decision.verdict == "regressed"
    assert decision.passed is False
    assert decision.activated is False
    assert decision.eval_observation_ref
    assert item is not None
    assert item.status == "proposed"


def test_underpowered_example_deferred(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_underpowered_checkset(),
        config=_paired_config(),
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()

    assert decision.verdict == "underpowered"
    assert decision.passed is False
    assert decision.activated is False
    assert decision.eval_observation_ref
    assert item is not None
    assert item.status == "proposed"


def test_improved_rule_not_auto_activated(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="rule"),),
        store=store,
        checkset=_improved_checkset(),
        config=_paired_config(),
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()

    # rule never auto-activates regardless of verdict (no-direct-mutation).
    assert decision.verdict == "improved"
    assert decision.activated is False
    assert item is not None
    assert item.status == "proposed"


def test_decision_carries_delta_se_ci(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_improved_checkset(),
        config=_paired_config(),
    )
    decision = decisions[0]
    store.close()

    # mean delta is positive; se > 0 (non-zero variance); ci bracket delta.
    assert decision.delta > 0
    assert decision.se > 0
    assert decision.ci_low < decision.delta < decision.ci_high
    # back-compat sign: regression = mean(before) - mean(after) = -delta.
    assert decision.regression == -decision.delta
    assert decision.sample_n == 4


def test_strict_band_default_unchanged_on_same_tuples(tmp_path) -> None:
    """Default (strict_band) path is unchanged: an improving example activates,
    and the new paired fields stay at their empty/zero defaults."""
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_improved_checkset(),
        # no config → default strict_band
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()

    assert decision.passed is True
    assert decision.activated is True
    assert item is not None
    assert item.status == "active"
    # strict_band leaves the paired fields at defaults.
    assert decision.verdict == ""
    assert decision.delta == 0.0
    assert decision.se == 0.0
    assert decision.ci_low == 0.0
    assert decision.ci_high == 0.0
