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
    # eval cases are the holdout; they are registered (proposed), not activated
    # as behavior.
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
