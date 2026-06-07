"""PR4 — Eval gate (regression check before activation).

TDD test suite (written first).  Covers:

1. Regression beyond the allowed band → candidate NOT activated (stays
   proposed); an eval observation is still recorded.
2. Pass + ``example`` → ``auto_activate`` → item is ``active`` and carries the
   eval observation ref.
3. Pass + ``rule`` → stays ``proposed`` (awaits human approval); eval ref
   recorded for later approval.
4. Direct activation attempt without an eval ref → ``PolicyViolation``
   (policy:self-improvement.eval-observation-required@1).
5. Insufficient sample size → not activated (stays proposed).
6. Eval ref is recorded via ``record_eval_observation`` on every path.
7. ``verifier_bus`` exposes the ``learning-eval`` verifier metadata
   (metadata-only); the ``memory-continuity`` preset wires the gate.
8. Executor integration: ``store=None`` → candidates-only, no writes (OFF/PR3
   parity); ``store`` injected + gated ON → items land in store with the
   correct statuses.

No real LLM, no live eval execution, no network — the checkset is a
deterministic injected evaluator.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from magi_agent.harness.learning_executor import (
    LearningReflectionConfig,
    _REFLECTION_ENV_VAR,
    run_reflection,
)
from magi_agent.harness.presets import builtin_preset_by_key
from magi_agent.harness.verifier_bus import (
    VerifierMetadata,
    build_default_verifier_bus_metadata,
)
from magi_agent.learning.candidates import (
    LearningCandidate,
    LocalFakeTranscriptSource,
    SessionTrace,
)
from magi_agent.learning.eval_gate import (
    LEARNING_EVAL_VERIFIER_ID,
    MIN_EVAL_SAMPLE_SIZE,
    MAX_REGRESSION_BAND,
    CheckSet,
    EvalGateConfig,
    EvalGateDecision,
    StaticCheckSet,
    run_eval_gate,
)
from magi_agent.learning.models import LearningItem, LearningScope, Provenance
from magi_agent.learning.policy import PolicyViolation, assert_activation_allowed
from magi_agent.learning.store import SqliteLearningStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(tmp_path) -> SqliteLearningStore:
    return SqliteLearningStore(
        db_path="learning.db", workspace_root=str(tmp_path)
    )


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


def _passing_checkset() -> StaticCheckSet:
    """before/after scores that improve (no regression), enough samples."""
    return StaticCheckSet(
        before=(1.0, 1.0, 1.0, 1.0),
        after=(1.0, 1.0, 1.0, 1.0),
    )


def _regressing_checkset() -> StaticCheckSet:
    """after much worse than before → regression beyond band."""
    return StaticCheckSet(
        before=(1.0, 1.0, 1.0, 1.0),
        after=(0.0, 0.0, 0.0, 0.0),
    )


def _undersized_checkset() -> StaticCheckSet:
    """Fewer than MIN_EVAL_SAMPLE_SIZE samples even though it 'passes'."""
    return StaticCheckSet(before=(1.0,), after=(1.0,))


# ---------------------------------------------------------------------------
# Verifier-bus + preset wiring
# ---------------------------------------------------------------------------


def test_verifier_bus_exposes_learning_eval_metadata_only() -> None:
    bus = build_default_verifier_bus_metadata()
    by_id = {v.verifier_id: v for v in bus.verifiers}

    assert LEARNING_EVAL_VERIFIER_ID in by_id
    verifier = by_id[LEARNING_EVAL_VERIFIER_ID]
    assert isinstance(verifier, VerifierMetadata)
    # metadata-only / no live attachments
    assert verifier.metadata_only is True
    assert verifier.execution_attached is False
    assert verifier.runner_attached is False
    assert verifier.route_attached is False
    assert verifier.canary_attached is False
    # default-off, opt-out, non-hard-safety
    assert verifier.default_enabled is False
    assert verifier.disabled is True
    assert verifier.hard_safety is False


def test_memory_continuity_preset_wires_learning_eval_gate() -> None:
    preset = builtin_preset_by_key("memory-continuity")
    assert "learning-eval" in preset.verifier_gates
    assert LEARNING_EVAL_VERIFIER_ID == "learning-eval"


# ---------------------------------------------------------------------------
# Decision logic + policy enforcement
# ---------------------------------------------------------------------------


def test_pass_example_auto_activates_with_eval_ref(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_passing_checkset(),
    )
    store.close()

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.activated is True
    assert decision.eval_observation_ref
    item = SqliteLearningStore(
        db_path="learning.db", workspace_root=str(tmp_path)
    ).get(decision.item_id)
    assert item is not None
    assert item.status == "active"
    assert item.eval_observation_ref == decision.eval_observation_ref


def test_auto_activate_examples_true_preserves_pr4_behaviour(tmp_path) -> None:
    """GOVERNANCE: the default ``auto_activate_examples=True`` keeps the original
    PR4 behaviour — a passing example auto-activates."""
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_passing_checkset(),
        auto_activate_examples=True,  # explicit; equals the default
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()
    assert decision.activated is True
    assert item is not None
    assert item.status == "active"


def test_auto_activate_examples_false_leaves_example_proposed(tmp_path) -> None:
    """GOVERNANCE: with ``auto_activate_examples=False`` a passing example is NOT
    auto-activated — it stays ``proposed`` (awaits human approval) BUT the eval
    observation is still recorded so a later approval has the measurement data."""
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_passing_checkset(),
        auto_activate_examples=False,
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()
    # The eval passed, but nothing was activated.
    assert decision.passed is True
    assert decision.activated is False
    # The eval observation is still recorded (data preserved for later approval).
    # The ref lives in the observations table; the item row only carries it once
    # a human approval / auto-activation stamps it, so the proposed item's own
    # ``eval_observation_ref`` stays unset here.
    assert decision.eval_observation_ref
    assert item is not None
    assert item.status == "proposed"


def test_pass_rule_stays_proposed_with_eval_ref(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="rule"),),
        store=store,
        checkset=_passing_checkset(),
    )

    decision = decisions[0]
    assert decision.activated is False
    assert decision.eval_observation_ref
    item = store.get(decision.item_id)
    store.close()
    assert item is not None
    # rule requires human approval (no-direct-mutation) — stays proposed
    assert item.status == "proposed"
    # eval ref recorded so a human can later approve()
    assert decision.eval_observation_ref


def test_regression_beyond_band_not_activated_but_observation_recorded(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_regressing_checkset(),
    )

    decision = decisions[0]
    assert decision.passed is False
    assert decision.activated is False
    assert decision.eval_observation_ref  # observation still recorded
    item = store.get(decision.item_id)
    store.close()
    assert item is not None
    assert item.status == "proposed"


def test_insufficient_sample_size_not_activated(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=_undersized_checkset(),
    )

    decision = decisions[0]
    assert decision.passed is False
    assert decision.activated is False
    item = store.get(decision.item_id)
    store.close()
    assert item is not None
    assert item.status == "proposed"


def test_eval_candidate_registered(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="eval"),),
        store=store,
        checkset=_passing_checkset(),
    )
    decision = decisions[0]
    item = store.get(decision.item_id)
    store.close()
    assert item is not None
    # eval cases get no special handling: like every candidate they are simply
    # proposed (never auto-activated as behavior).  "registered" here just means
    # "written to the store as a proposed item".
    assert item.kind == "eval"
    assert item.status == "proposed"
    assert decision.eval_observation_ref  # observation recorded on every path


def test_direct_activation_without_eval_ref_raises_policy_violation() -> None:
    item = LearningItem(
        id="learning:x",
        kind="example",
        scope=LearningScope(taskKind="general"),
        content={"situation": "s", "behavior": "b"},
        rationale="r",
        provenance=Provenance(
            sessionIds=("s1",),
            derivedBy="reflection",
            createdAt="2026-06-03T10:00:00Z",
        ),
    )
    with pytest.raises(PolicyViolation):
        assert_activation_allowed(item, eval_observation_ref=None)


def test_every_path_records_eval_observation(tmp_path) -> None:
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (
            _candidate(kind="example", rationale="a", sid="s-a"),
            _candidate(kind="rule", rationale="b", sid="s-b"),
            _candidate(kind="example", rationale="c", sid="s-c"),
        ),
        store=store,
        checkset=_regressing_checkset(),  # all fail, but obs still recorded
    )
    store.close()
    assert all(d.eval_observation_ref for d in decisions)


# ---------------------------------------------------------------------------
# Executor integration — OFF byte-identical, ON writes through gate
# ---------------------------------------------------------------------------


def test_executor_store_none_is_candidates_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    traces = (
        SessionTrace(
            sessionId="s1",
            turns=(
                {"role": "user", "text": "no"},
                {"role": "assistant", "text": "draft"},
                {"role": "user", "text": "actually do X instead"},
                {"role": "assistant", "text": "final"},
            ),
            finalOutput="final",
            draftOutput="draft",
            ts="2026-06-03T10:00:00Z",
        ),
    )
    source = LocalFakeTranscriptSource(traces=traces)
    result = asyncio.run(
        run_reflection(
            source=source,
            config=LearningReflectionConfig(enabled=True),
            store=None,
        )
    )
    assert result.status == "ok"
    # store=None ⇒ no DB file written at all
    db_file = tmp_path / "learning.db"
    assert not db_file.exists()


def test_executor_with_store_writes_through_gate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    traces = (
        SessionTrace(
            sessionId="s1",
            turns=(
                {"role": "user", "text": "no"},
                {"role": "assistant", "text": "draft"},
                {"role": "user", "text": "actually do X instead"},
                {"role": "assistant", "text": "final"},
            ),
            finalOutput="final",
            draftOutput="draft",
            ts="2026-06-03T10:00:00Z",
        ),
    )
    source = LocalFakeTranscriptSource(traces=traces)
    store = _store(tmp_path)
    result = asyncio.run(
        run_reflection(
            source=source,
            config=LearningReflectionConfig(enabled=True),
            store=store,
            checkset=_passing_checkset(),
        )
    )
    assert result.status == "ok"
    # at least one item landed in the store as proposed or active
    page = store.list(tenant_id="local")
    store.close()
    assert len(page.items) >= 1
    statuses = {item.status for item in page.items}
    assert statuses <= {"proposed", "active"}


def test_static_checkset_is_a_checkset() -> None:
    assert isinstance(_passing_checkset(), CheckSet)


# ---------------------------------------------------------------------------
# PR4 code-review fixes
# ---------------------------------------------------------------------------


def test_rerun_over_active_example_skips_cleanly(tmp_path) -> None:
    """CRITICAL #1: re-running the gate over an already-active example must not
    crash (store.propose would raise on a non-proposed item).  The second run
    skips the candidate gracefully: item stays ``active``, no exception, the
    decision is marked skipped/not-activated, and nothing is re-proposed."""
    candidate = _candidate(kind="example")

    store = _store(tmp_path)
    first = run_eval_gate(
        (candidate,), store=store, checkset=_passing_checkset()
    )
    store.close()
    assert first[0].activated is True

    # Second run over the same now-active candidate must not raise.
    store2 = _store(tmp_path)
    second = run_eval_gate(
        (candidate,), store=store2, checkset=_passing_checkset()
    )
    decision = second[0]
    assert decision.skipped is True
    assert decision.activated is False
    assert decision.passed is False
    assert decision.reason
    # Item is untouched: still active, single version.
    item = store2.get(decision.item_id)
    store2.close()
    assert item is not None
    assert item.status == "active"


def test_one_skipped_candidate_does_not_abort_batch(tmp_path) -> None:
    """CRITICAL #1: a skipped (already-active) candidate must not abort the
    rest of the batch — later candidates are still processed."""
    active_candidate = _candidate(kind="example", rationale="a", sid="s-a")
    fresh_candidate = _candidate(kind="example", rationale="b", sid="s-b")

    store = _store(tmp_path)
    run_eval_gate((active_candidate,), store=store, checkset=_passing_checkset())
    store.close()

    store2 = _store(tmp_path)
    decisions = run_eval_gate(
        (active_candidate, fresh_candidate),
        store=store2,
        checkset=_passing_checkset(),
    )
    store2.close()
    assert len(decisions) == 2
    by_id = {d.item_id: d for d in decisions}
    active_decision = next(d for d in decisions if d.skipped)
    fresh_decision = next(d for d in decisions if not d.skipped)
    assert active_decision.activated is False
    assert fresh_decision.activated is True


def test_propose_toctou_race_skips_candidate_not_aborts_batch(tmp_path) -> None:
    """TOCTOU: a candidate that flips to active BETWEEN the gate's skip-check
    ``store.get`` and its ``store.propose`` must be skipped, not abort the batch.

    run_eval_gate does ``store.get`` (sees proposed/None) then ``store.propose``.
    If a concurrent activation lands in that window, propose() raises ValueError;
    the gate must catch it, mark that candidate skipped, and continue so the rest
    of the batch still completes.
    """
    racy_candidate = _candidate(kind="example", rationale="a", sid="s-race")
    fresh_candidate = _candidate(kind="example", rationale="b", sid="s-fresh")

    base = _store(tmp_path)

    class _RacyStore:
        """Delegates to a real store but, on the FIRST propose, simulates a
        concurrent flip-to-active in the get→propose window by activating the
        item first — so the real propose() then raises ValueError."""

        def __init__(self, inner: SqliteLearningStore) -> None:
            self._inner = inner
            self._raced = False

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def get(self, item_id, *, tenant_id="local"):
            return self._inner.get(item_id, tenant_id=tenant_id)

        def propose(self, item):
            if not self._raced and item.rationale == "a":
                self._raced = True
                # Concurrent activation lands in the window: propose then raises.
                self._inner.propose(item)
                ref = self._inner.record_eval_observation(
                    item_id=item.id,
                    before={"mean": 0.5, "n": 4},
                    after={"mean": 0.9, "n": 4},
                    sample_n=4,
                    passed=True,
                )
                self._inner.auto_activate(item.id, eval_observation_ref=ref)
            return self._inner.propose(item)

    racy = _RacyStore(base)
    decisions = run_eval_gate(
        (racy_candidate, fresh_candidate),
        store=racy,
        checkset=_passing_checkset(),
    )
    base.close()

    assert len(decisions) == 2
    by_rationale = {d.item_id: d for d in decisions}
    raced = next(
        d for d in decisions if d.item_id == _racy_item_id(racy_candidate, base)
    )
    assert raced.skipped is True
    assert raced.activated is False
    # The other candidate still processes normally — batch was not aborted.
    fresh = next(d for d in decisions if d.item_id != raced.item_id)
    assert fresh.activated is True


def _racy_item_id(candidate, store) -> str:
    from magi_agent.learning.eval_gate import _candidate_item_id

    return _candidate_item_id(candidate)


def test_item_ids_differ_for_refs_differing_only_by_version_suffix(tmp_path) -> None:
    """CRITICAL #2: two candidates whose source refs differ only by a ``:v1``
    suffix must get DISTINCT item ids (no collapse / no collision), and neither
    id may end in the reserved ``:v<n>`` suffix."""
    c_plain = _candidate(kind="example")
    c_versioned = c_plain.model_copy(
        update={"source_signal_ref": c_plain.source_signal_ref + ":v1"}
    )
    assert c_plain.source_signal_ref != c_versioned.source_signal_ref

    store = _store(tmp_path)
    decisions = run_eval_gate(
        (c_plain, c_versioned), store=store, checkset=_passing_checkset()
    )
    store.close()
    ids = {d.item_id for d in decisions}
    assert len(ids) == 2  # distinct ids, no collision
    import re as _re

    assert all(not _re.search(r":v\d+$", i) for i in ids)


def test_unequal_length_checkset_raises(tmp_path) -> None:
    """IMPORTANT #6: a mismatched evaluator (different before/after lengths)
    must raise ValueError rather than silently averaging over a min slice."""

    class _MismatchedCheckSet(StaticCheckSet):
        def run(self, candidate):  # type: ignore[override]
            return (1.0, 1.0, 1.0, 1.0), (1.0, 1.0)

    store = _store(tmp_path)
    with pytest.raises(ValueError):
        run_eval_gate(
            (_candidate(kind="example"),),
            store=store,
            checkset=_MismatchedCheckSet(before=(1.0,) * 4, after=(1.0,) * 4),
        )
    store.close()


def test_empty_checkset_fails_not_activated_observation_recorded(tmp_path) -> None:
    """MINOR: empty checkset → sample_n=0 → gate fails, not activated, but an
    eval observation is still recorded."""
    store = _store(tmp_path)
    decisions = run_eval_gate(
        (_candidate(kind="example"),),
        store=store,
        checkset=StaticCheckSet(before=(), after=()),
    )
    decision = decisions[0]
    assert decision.sample_n == 0
    assert decision.passed is False
    assert decision.activated is False
    assert decision.eval_observation_ref  # still recorded
    item = store.get(decision.item_id)
    store.close()
    assert item is not None
    assert item.status == "proposed"


def test_custom_regression_band_boundary(tmp_path) -> None:
    """MINOR: custom maxRegressionBand=0.1 — regression exactly 0.1 passes
    (``<=``), regression just over 0.1 fails."""
    config = EvalGateConfig(maxRegressionBand=0.1)

    # before mean 1.0, after mean 0.9 → regression 0.1 (== band) → passes.
    at_band = StaticCheckSet(
        before=(1.0, 1.0, 1.0, 1.0), after=(0.9, 0.9, 0.9, 0.9)
    )
    store = _store(tmp_path)
    d_at = run_eval_gate(
        (_candidate(kind="example", sid="s-at"),),
        store=store,
        checkset=at_band,
        config=config,
    )[0]
    store.close()
    assert d_at.passed is True
    assert d_at.activated is True

    # before mean 1.0, after mean 0.8 → regression 0.2 (> band) → fails.
    over_band = StaticCheckSet(
        before=(1.0, 1.0, 1.0, 1.0), after=(0.8, 0.8, 0.8, 0.8)
    )
    store2 = _store(tmp_path)
    d_over = run_eval_gate(
        (_candidate(kind="example", sid="s-over"),),
        store=store2,
        checkset=over_band,
        config=config,
    )[0]
    store2.close()
    assert d_over.passed is False
    assert d_over.activated is False


# ---------------------------------------------------------------------------
# IMPORTANT #3 — gate decisions surfaced on the reflection result
# ---------------------------------------------------------------------------


def _reflecting_traces() -> tuple[SessionTrace, ...]:
    return (
        SessionTrace(
            sessionId="s1",
            turns=(
                {"role": "user", "text": "no"},
                {"role": "assistant", "text": "draft"},
                {"role": "user", "text": "actually do X instead"},
                {"role": "assistant", "text": "final"},
            ),
            finalOutput="final",
            draftOutput="draft",
            ts="2026-06-03T10:00:00Z",
        ),
    )


def test_result_carries_gate_decisions_when_store_injected(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    source = LocalFakeTranscriptSource(traces=_reflecting_traces())
    store = _store(tmp_path)
    result = asyncio.run(
        run_reflection(
            source=source,
            config=LearningReflectionConfig(enabled=True),
            store=store,
            checkset=_passing_checkset(),
        )
    )
    store.close()
    assert result.eval_gate_decisions is not None
    assert all(isinstance(d, EvalGateDecision) for d in result.eval_gate_decisions)
    activated = sum(1 for d in result.eval_gate_decisions if d.activated)
    proposed = sum(1 for d in result.eval_gate_decisions if not d.activated)
    assert result.counters["items_activated"] == activated
    assert result.counters["items_proposed"] == proposed
    assert result.counters["items_activated"] + result.counters["items_proposed"] == len(
        result.eval_gate_decisions
    )


def test_result_store_none_has_no_gate_decisions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(_REFLECTION_ENV_VAR, "1")
    source = LocalFakeTranscriptSource(traces=_reflecting_traces())
    result = asyncio.run(
        run_reflection(
            source=source,
            config=LearningReflectionConfig(enabled=True),
            store=None,
        )
    )
    assert result.eval_gate_decisions is None
    # counters unchanged from PR3 parity — no learning-layer keys added.
    assert "items_activated" not in result.counters
    assert "items_proposed" not in result.counters
