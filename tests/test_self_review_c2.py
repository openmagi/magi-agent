"""C2 — LearningPipelineSink: TDD test suite.

Tests
-----
1. Mapping: ReviewCandidate → LearningCandidate field mapping (kind, proposal, provenance).
2. Gate-off (MAGI_SELF_REVIEW_PIPELINE_ENABLED=0): receive() records candidate but does NOT
   run eval-gate, does NOT write to store.
3. Shadow mode (MAGI_SELF_REVIEW_SHADOW=1 + pipeline enabled): receive() also skips eval-gate
   and store writes.
4. Example candidate + eval passes → auto_activate called → item becomes active.
5. Rule candidate + eval passes → stays proposed (NO auto_activate) — the core invariant.
6. Insufficient eval samples (sample_n < MIN_EVAL_SAMPLE_SIZE) → candidate stays proposed,
   NOT activated.
7. Eval-gate regression (after_mean < before_mean beyond band) → candidate stays proposed.
8. Evidence redaction: routing EvidenceRecord has no raw proposal text.
9. Evidence record: contains candidate kind, eval verdict, resulting status.
10. Multiple candidates in one call → each routed independently.
11. Store idempotency: same candidate twice → second call is skipped (existing-status guard).
12. Exception in eval-gate → fail-open (no re-raise), candidate stays proposed/not activated.
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from typing import Any

import pytest

from magi_agent.harness.self_review import ReviewCandidate
from magi_agent.harness.self_review_pipeline import (
    LearningPipelineSink,
    PipelineSinkConfig,
    RoutingDecision,
    _map_candidate,
)
from magi_agent.learning.candidates import LearningCandidate
from magi_agent.learning.eval_gate import (
    EvalGateConfig,
    MIN_EVAL_SAMPLE_SIZE,
    StaticCheckSet,
    run_eval_gate,
)
from magi_agent.learning.models import LearningKind, LearningScope, Provenance
from magi_agent.learning.store import SqliteLearningStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_store() -> SqliteLearningStore:
    """Create an in-memory SQLite learning store for test isolation."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
    return SqliteLearningStore(db_path=tmp, workspace_root="")


def _make_memory_candidate(
    session_id: str = "sess-1",
    turn_id: str = "turn-1",
    proposal: str = "User prefers concise answers.",
    confidence: float = 0.8,
    mode: str = "live",
) -> ReviewCandidate:
    return ReviewCandidate(
        kind="memory",
        proposal=proposal,
        provenanceDigest="abc" + "0" * 61,
        confidence=confidence,
        sessionId=session_id,
        turnId=turn_id,
        acted=False,
        mode=mode,  # type: ignore[arg-type]
    )


def _make_skill_candidate(
    session_id: str = "sess-1",
    turn_id: str = "turn-1",
    proposal: str = "Always summarize at the end.",
    confidence: float = 0.7,
    mode: str = "live",
) -> ReviewCandidate:
    return ReviewCandidate(
        kind="skill",
        proposal=proposal,
        provenanceDigest="def" + "0" * 61,
        confidence=confidence,
        sessionId=session_id,
        turnId=turn_id,
        acted=False,
        mode=mode,  # type: ignore[arg-type]
    )


def _passing_checkset() -> StaticCheckSet:
    """Returns a checkset that passes: before <= after, >= MIN_EVAL_SAMPLE_SIZE samples."""
    scores = tuple(0.5 for _ in range(MIN_EVAL_SAMPLE_SIZE))
    # 0.0 regression satisfies <=MAX_REGRESSION_BAND (equal before/after scores)
    return StaticCheckSet(before=scores, after=scores)


def _failing_checkset() -> StaticCheckSet:
    """Returns a checkset with a regression that fails the gate."""
    before = tuple(0.9 for _ in range(MIN_EVAL_SAMPLE_SIZE))
    after = tuple(0.1 for _ in range(MIN_EVAL_SAMPLE_SIZE))
    return StaticCheckSet(before=before, after=after)


def _insufficient_checkset() -> StaticCheckSet:
    """Returns a checkset with fewer samples than MIN_EVAL_SAMPLE_SIZE."""
    n = max(0, MIN_EVAL_SAMPLE_SIZE - 1)
    scores = tuple(0.8 for _ in range(n))
    return StaticCheckSet(before=scores, after=scores)


_NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. Mapping: ReviewCandidate → LearningCandidate
# ---------------------------------------------------------------------------


class TestCandidateMapping:
    """_map_candidate converts ReviewCandidate fields to LearningCandidate correctly."""

    def test_memory_kind_maps_to_example(self) -> None:
        rc = _make_memory_candidate()
        lc = _map_candidate(rc)
        assert lc.kind == "example"

    def test_skill_kind_maps_to_example(self) -> None:
        rc = _make_skill_candidate()
        lc = _map_candidate(rc)
        assert lc.kind == "example"

    def test_proposal_becomes_content_behavior_only(self) -> None:
        """behavior == proposal; situation is a distinct provenance descriptor."""
        proposal = "User prefers bullet points."
        rc = _make_memory_candidate(proposal=proposal)
        lc = _map_candidate(rc)
        # behavior must equal the raw proposal
        assert lc.content["behavior"] == proposal
        # situation must NOT duplicate the raw proposal (distinct, non-inflating)
        assert lc.content["situation"] != proposal

    def test_situation_and_behavior_are_distinct(self) -> None:
        """situation != behavior for every candidate kind to prevent self-scoring."""
        for make_fn in (_make_memory_candidate, _make_skill_candidate):
            rc = make_fn()
            lc = _map_candidate(rc)
            assert lc.content["situation"] != lc.content["behavior"], (
                f"situation and behavior must differ (kind={rc.kind})"
            )

    def test_proposal_becomes_rationale(self) -> None:
        proposal = "Always verify before committing."
        rc = _make_skill_candidate(proposal=proposal)
        lc = _map_candidate(rc)
        assert lc.rationale == proposal

    def test_provenance_digest_prefix_in_source_signal_ref(self) -> None:
        # The source_signal_ref includes the first 16 chars of the provenance
        # digest (truncated for redaction safety, not the full 64-char digest).
        digest = "abc" + "0" * 61
        rc = _make_memory_candidate()
        lc = _map_candidate(rc)
        # First 16 chars of the digest must appear in the ref
        assert digest[:16] in lc.source_signal_ref

    def test_session_id_in_provenance(self) -> None:
        rc = _make_memory_candidate(session_id="sess-map")
        lc = _map_candidate(rc)
        assert "sess-map" in lc.provenance.session_ids

    def test_derived_by_reflection(self) -> None:
        rc = _make_memory_candidate()
        lc = _map_candidate(rc)
        assert lc.provenance.derived_by == "reflection"

    def test_scope_task_kind_is_self_review(self) -> None:
        rc = _make_memory_candidate()
        lc = _map_candidate(rc)
        assert lc.scope.task_kind == "self-review"

    def test_result_is_learning_candidate(self) -> None:
        rc = _make_memory_candidate()
        lc = _map_candidate(rc)
        assert isinstance(lc, LearningCandidate)

    def test_learning_candidate_is_frozen(self) -> None:
        rc = _make_memory_candidate()
        lc = _map_candidate(rc)
        with pytest.raises(Exception):
            lc.kind = "rule"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Gate-off → no eval-gate, no store writes
# ---------------------------------------------------------------------------


class TestPipelineGateOff:
    """When MAGI_SELF_REVIEW_PIPELINE_ENABLED is off (default), sink receives but does nothing."""

    def test_gate_off_no_store_writes(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=False, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        sink.receive(candidate)

        # Nothing written to store
        page = store.list(tenant_id="local")
        assert len(page.items) == 0

    def test_gate_off_returns_routing_decision_with_gate_off_status(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=False, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision is not None
        assert decision.gate_off is True

    def test_gate_off_no_eval_run(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=False, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision.eval_verdict is None

    def test_gate_off_no_item_id(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=False, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision.item_id is None

    def test_default_config_is_gate_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_SELF_REVIEW_PIPELINE_ENABLED", raising=False)
        cfg = PipelineSinkConfig.from_env()
        assert cfg.pipeline_enabled is False

    def test_explicit_env_enables_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_PIPELINE_ENABLED", "1")
        monkeypatch.setenv("MAGI_SELF_REVIEW_SHADOW", "0")
        cfg = PipelineSinkConfig.from_env()
        assert cfg.pipeline_enabled is True


# ---------------------------------------------------------------------------
# 3. Shadow mode → no eval-gate, no store writes (even when pipeline enabled)
# ---------------------------------------------------------------------------


class TestShadowModeSkipsEval:
    """Shadow mode: pipeline enabled but shadow=True → no eval, no store writes."""

    def test_shadow_mode_no_store_writes(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=True)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate(mode="shadow")
        sink.receive(candidate)
        page = store.list(tenant_id="local")
        assert len(page.items) == 0

    def test_shadow_mode_decision_marks_shadow(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=True)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate(mode="shadow")
        decision = sink.receive(candidate)
        assert decision.shadow is True
        assert decision.eval_verdict is None

    def test_shadow_env_default_is_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_PIPELINE_ENABLED", "1")
        monkeypatch.delenv("MAGI_SELF_REVIEW_SHADOW", raising=False)
        cfg = PipelineSinkConfig.from_env()
        assert cfg.shadow is True


# ---------------------------------------------------------------------------
# 4. Example candidate + passing eval → auto_activate
# ---------------------------------------------------------------------------


class TestExamplePassesAutoActivate:
    """An example-class candidate (memory/skill) that passes eval is auto_activated."""

    def test_memory_candidate_passes_eval_becomes_active(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)

        assert decision.resulting_status == "active"
        assert decision.activated is True
        item = store.get(decision.item_id, tenant_id="local")
        assert item is not None
        assert item.status == "active"

    def test_skill_candidate_passes_eval_becomes_active(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_skill_candidate()
        decision = sink.receive(candidate)

        assert decision.resulting_status == "active"
        assert decision.activated is True

    def test_example_eval_passed_true_in_decision(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision.eval_verdict is not None
        assert decision.eval_verdict.passed is True


# ---------------------------------------------------------------------------
# 5. Rule candidate + passing eval → stays proposed (NO auto_activate)
# ---------------------------------------------------------------------------


class TestRuleNeverAutoActivates:
    """Core invariant: a rule-class candidate NEVER auto_activates even if eval passes.

    A 'rule' LearningCandidate requires human approval_ref per policy.
    The sink must use auto_activate_examples=True which respects the gate's
    rule→proposed branch; OR it must explicitly NOT call auto_activate for rules.
    Either way: rule + pass → status stays 'proposed'.
    """

    def test_rule_candidate_stays_proposed_after_pass(self) -> None:
        """Build a rule LearningCandidate manually and run through the gate."""
        from magi_agent.learning.eval_gate import StaticCheckSet

        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)

        # Manufacture a rule ReviewCandidate — C2 must NOT activate it
        # even if the eval passes.  We inject it via a subclass that overrides
        # _map_candidate to produce a "rule" LearningCandidate.
        class RuleForcingSink(LearningPipelineSink):
            """Forces all mapped candidates to kind='rule' for this test."""

            def _map(self, rc: ReviewCandidate) -> LearningCandidate:
                base = _map_candidate(rc)
                # Rebuild with kind='rule' + rule-required content shape
                return LearningCandidate(
                    kind="rule",
                    scope=base.scope,
                    content={"when": base.rationale, "then": base.rationale},
                    rationale=base.rationale,
                    provenance=base.provenance,
                    sourceSignalRef=base.source_signal_ref,
                )

        sink = RuleForcingSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)

        # Eval passed but item must NOT be activated
        assert decision.eval_verdict is not None
        assert decision.eval_verdict.passed is True
        assert decision.activated is False
        assert decision.resulting_status == "proposed"

        # Double-check via store
        item = store.get(decision.item_id, tenant_id="local")
        assert item is not None
        assert item.status == "proposed"
        assert item.approval_ref is None

    def test_rule_direct_auto_activate_raises_policy_violation(self) -> None:
        """auto_activate on a rule raises PolicyViolation — proves the store enforcement."""
        from magi_agent.learning.policy import PolicyViolation
        from magi_agent.learning.models import LearningItem, LearningScope, Provenance
        from magi_agent.learning.eval_gate import StaticCheckSet, _to_item

        store = _tmp_store()
        lc = LearningCandidate(
            kind="rule",
            scope=LearningScope(taskKind="self-review"),
            content={"when": "test", "then": "test"},
            rationale="test rule",
            provenance=Provenance(
                sessionIds=("sess-x",),
                derivedBy="reflection",
                createdAt="2026-06-07T12:00:00Z",
            ),
            sourceSignalRef="signal:test@sess-x",
        )
        item = _to_item(lc)
        store.propose(item)

        # Record a passing eval observation so the ref is valid
        eval_ref = store.record_eval_observation(
            item_id=item.id,
            before={"mean": 0.5, "n": MIN_EVAL_SAMPLE_SIZE},
            after={"mean": 0.5, "n": MIN_EVAL_SAMPLE_SIZE},
            sample_n=MIN_EVAL_SAMPLE_SIZE,
            passed=True,
        )

        # auto_activate on a rule must raise PolicyViolation
        with pytest.raises(PolicyViolation):
            store.auto_activate(item.id, eval_observation_ref=eval_ref)


# ---------------------------------------------------------------------------
# 6. Insufficient samples → stays proposed
# ---------------------------------------------------------------------------


class TestInsufficientSamplesStaysProposed:
    def test_insufficient_samples_not_activated(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_insufficient_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)

        assert decision.eval_verdict is not None
        assert decision.eval_verdict.passed is False
        assert decision.activated is False
        assert decision.resulting_status == "proposed"

    def test_insufficient_samples_item_in_store_as_proposed(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_insufficient_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)

        item = store.get(decision.item_id, tenant_id="local")
        assert item is not None
        assert item.status == "proposed"


# ---------------------------------------------------------------------------
# 7. Regression fails the gate → stays proposed
# ---------------------------------------------------------------------------


class TestRegressionFailsGate:
    def test_regression_candidate_stays_proposed(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_failing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)

        assert decision.eval_verdict is not None
        assert decision.eval_verdict.passed is False
        assert decision.activated is False
        assert decision.resulting_status == "proposed"

    def test_regression_eval_verdict_records_regression_value(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_failing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)

        # Regression = before_mean - after_mean = 0.9 - 0.1 = 0.8
        assert decision.eval_verdict is not None
        assert decision.eval_verdict.regression > 0.0


# ---------------------------------------------------------------------------
# 8. Evidence redaction: no raw proposal text in the EvidenceRecord
# ---------------------------------------------------------------------------


class TestEvidenceRedaction:
    def test_evidence_has_no_raw_proposal(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        secret_proposal = "SECRET PROPOSAL TEXT XYZ"
        candidate = _make_memory_candidate(proposal=secret_proposal)
        decision = sink.receive(candidate)

        evidence_json = decision.evidence.model_dump_json()
        assert secret_proposal not in evidence_json

    def test_evidence_type_is_custom(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision.evidence.type.startswith("custom:")

    def test_evidence_contains_provenance_digest_not_proposal(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        evidence_json = decision.evidence.model_dump_json()
        # provenance digest fragment must appear (it's a safe digest)
        assert "abc" in evidence_json


# ---------------------------------------------------------------------------
# 9. Evidence record contains routing metadata
# ---------------------------------------------------------------------------


class TestEvidenceRoutingMetadata:
    def test_evidence_fields_contain_candidate_kind(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        fields = dict(decision.evidence.fields)
        assert "candidateKind" in fields or "candidate_kind" in fields

    def test_evidence_fields_contain_resulting_status(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        fields = dict(decision.evidence.fields)
        assert "resultingStatus" in fields or "resulting_status" in fields

    def test_evidence_fields_contain_eval_verdict(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        fields = dict(decision.evidence.fields)
        assert "evalPassed" in fields or "eval_passed" in fields

    def test_evidence_status_ok_on_success(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision.evidence.status == "ok"


# ---------------------------------------------------------------------------
# 10. Multiple candidates routed independently
# ---------------------------------------------------------------------------


class TestMultipleCandidates:
    def test_two_memory_candidates_both_routed(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        c1 = _make_memory_candidate(proposal="Fact one.", session_id="s1", turn_id="t1")
        c2 = _make_memory_candidate(proposal="Fact two.", session_id="s2", turn_id="t2")
        d1 = sink.receive(c1)
        d2 = sink.receive(c2)

        assert d1.item_id != d2.item_id
        assert d1.resulting_status == "active"
        assert d2.resulting_status == "active"

    def test_memory_and_skill_candidates_both_routed(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        c1 = _make_memory_candidate()
        c2 = _make_skill_candidate()
        d1 = sink.receive(c1)
        d2 = sink.receive(c2)

        assert d1.item_id is not None
        assert d2.item_id is not None
        assert d1.item_id != d2.item_id


# ---------------------------------------------------------------------------
# 11. Store idempotency: same candidate twice → second is skipped
# ---------------------------------------------------------------------------


class TestStoreIdempotency:
    def test_duplicate_candidate_is_skipped(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        d1 = sink.receive(candidate)
        d2 = sink.receive(candidate)

        # Second call is skipped (already exists in active/proposed state)
        assert d1.item_id == d2.item_id
        assert d2.skipped is True

    def test_store_has_exactly_one_item_after_duplicate(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        sink.receive(candidate)
        sink.receive(candidate)

        page = store.list(tenant_id="local")
        assert len(page.items) == 1


# ---------------------------------------------------------------------------
# 12. Exception in eval-gate → fail-open
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_broken_checkset_does_not_reraise(self) -> None:
        """A checkset that raises must not propagate the exception out of receive()."""
        from magi_agent.learning.eval_gate import CheckSet, StaticCheckSet

        class BrokenCheckSet:
            def run(
                self, candidate: LearningCandidate
            ) -> tuple[tuple[float, ...], tuple[float, ...]]:
                raise RuntimeError("checkset exploded")

        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=BrokenCheckSet(),
            config=config,
        )
        candidate = _make_memory_candidate()
        # Must not raise
        decision = sink.receive(candidate)
        assert decision is not None
        assert decision.activated is False

    def test_broken_checkset_evidence_status_failed(self) -> None:
        class BrokenCheckSet:
            def run(
                self, candidate: LearningCandidate
            ) -> tuple[tuple[float, ...], tuple[float, ...]]:
                raise RuntimeError("checkset exploded")

        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=BrokenCheckSet(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision.evidence.status == "failed"


# ---------------------------------------------------------------------------
# 13. PipelineSinkConfig frozen model
# ---------------------------------------------------------------------------


class TestPipelineSinkConfig:
    def test_config_frozen(self) -> None:
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        with pytest.raises(Exception):
            config.pipeline_enabled = False  # type: ignore[misc]

    def test_from_env_disabled_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGI_SELF_REVIEW_PIPELINE_ENABLED", raising=False)
        cfg = PipelineSinkConfig.from_env()
        assert cfg.pipeline_enabled is False

    def test_from_env_shadow_default_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_PIPELINE_ENABLED", "1")
        monkeypatch.delenv("MAGI_SELF_REVIEW_SHADOW", raising=False)
        cfg = PipelineSinkConfig.from_env()
        assert cfg.shadow is True

    def test_from_env_shadow_can_be_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGI_SELF_REVIEW_PIPELINE_ENABLED", "1")
        monkeypatch.setenv("MAGI_SELF_REVIEW_SHADOW", "0")
        cfg = PipelineSinkConfig.from_env()
        assert cfg.shadow is False


# ---------------------------------------------------------------------------
# 14. RoutingDecision model is frozen
# ---------------------------------------------------------------------------


class TestRoutingDecisionModel:
    def test_routing_decision_is_frozen(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=True, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        with pytest.raises(Exception):
            decision.activated = False  # type: ignore[misc]

    def test_routing_decision_gate_off_has_no_eval_verdict(self) -> None:
        store = _tmp_store()
        config = PipelineSinkConfig(pipeline_enabled=False, shadow=False)
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
            config=config,
        )
        candidate = _make_memory_candidate()
        decision = sink.receive(candidate)
        assert decision.eval_verdict is None


# ---------------------------------------------------------------------------
# 15. Satisfies CandidateSink protocol
# ---------------------------------------------------------------------------


class TestSatisfiesCandidateSinkProtocol:
    def test_pipeline_sink_satisfies_protocol(self) -> None:
        from magi_agent.harness.self_review import CandidateSink

        store = _tmp_store()
        sink = LearningPipelineSink(
            store=store,
            checkset=_passing_checkset(),
        )
        assert isinstance(sink, CandidateSink)
