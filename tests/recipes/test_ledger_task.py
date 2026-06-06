"""Tests for TaskLedgerContract (Phase 1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.recipes.ledger_task import (
    LedgerFactKind,
    LedgerFact,
    LedgerPlanStep,
    TaskLedgerContract,
    kind_from_support_verdict,
    make_fact,
    make_plan_step,
    make_task_ledger,
    update_task_ledger,
    transitively_invalidated_fact_ids,
    value_digest_for,
)


# ---------------------------------------------------------------------------
# LedgerFactKind helpers
# ---------------------------------------------------------------------------

class TestKindFromSupportVerdict:
    def test_supported_maps_to_known_fact(self) -> None:
        assert kind_from_support_verdict("supported") == LedgerFactKind.known_fact

    def test_weak_maps_to_working_guess(self) -> None:
        assert kind_from_support_verdict("weak") == LedgerFactKind.working_guess

    def test_not_evaluated_maps_to_working_guess(self) -> None:
        assert kind_from_support_verdict("not_evaluated") == LedgerFactKind.working_guess

    def test_stale_maps_to_working_guess(self) -> None:
        assert kind_from_support_verdict("stale") == LedgerFactKind.working_guess

    def test_unsupported_maps_to_open_question(self) -> None:
        assert kind_from_support_verdict("unsupported") == LedgerFactKind.open_question

    def test_contradicted_maps_to_open_question(self) -> None:
        assert kind_from_support_verdict("contradicted") == LedgerFactKind.open_question

    def test_unknown_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown"):
            kind_from_support_verdict("totally_made_up")


# ---------------------------------------------------------------------------
# value_digest_for
# ---------------------------------------------------------------------------

class TestValueDigestFor:
    def test_returns_sha256_format(self) -> None:
        d = value_digest_for("hello world")
        assert d.startswith("sha256:")
        assert len(d) == 71

    def test_different_values_different_digests(self) -> None:
        assert value_digest_for("abc") != value_digest_for("xyz")

    def test_same_value_same_digest(self) -> None:
        assert value_digest_for("abc") == value_digest_for("abc")


# ---------------------------------------------------------------------------
# LedgerFact construction
# ---------------------------------------------------------------------------

class TestLedgerFactConstruction:
    def test_minimal_fact(self) -> None:
        fact = make_fact(fact_id="fact:orcid-avg", kind=LedgerFactKind.working_guess)
        assert fact.fact_id == "fact:orcid-avg"
        assert fact.kind == LedgerFactKind.working_guess
        assert fact.default_off is True

    def test_fact_with_all_fields(self) -> None:
        vd = value_digest_for("42")
        fact = make_fact(
            fact_id="fact:citation-count",
            kind=LedgerFactKind.known_fact,
            claim_ref_id="claim:citation-count-1",
            value_digest=vd,
            confidence="high",
            depends_on=("fact:orcid-avg",),
            public_label="citation count",
        )
        assert fact.confidence == "high"
        assert fact.value_digest == vd
        assert "fact:orcid-avg" in fact.depends_on

    def test_frozen_rejects_mutation(self) -> None:
        fact = make_fact(fact_id="fact:x", kind=LedgerFactKind.open_question)
        with pytest.raises((TypeError, ValidationError)):
            fact.kind = LedgerFactKind.known_fact  # type: ignore[misc]

    def test_duplicate_depends_on_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            LedgerFact(
                fact_id="fact:x",
                kind=LedgerFactKind.working_guess,
                depends_on=("fact:a", "fact:a"),
            )

    def test_bad_fact_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LedgerFact(fact_id="has spaces in it!", kind=LedgerFactKind.known_fact)

    def test_unsafe_public_label_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LedgerFact(
                fact_id="fact:x",
                kind=LedgerFactKind.known_fact,
                public_label="my api_key value",
            )

    def test_public_label_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LedgerFact(
                fact_id="fact:x",
                kind=LedgerFactKind.known_fact,
                public_label="x" * 81,
            )

    def test_bad_value_digest_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LedgerFact(
                fact_id="fact:x",
                kind=LedgerFactKind.known_fact,
                value_digest="not-a-digest",
            )

    def test_public_projection_shape(self) -> None:
        fact = make_fact(
            fact_id="fact:pub-count",
            kind=LedgerFactKind.known_fact,
            confidence="medium",
        )
        proj = fact.public_projection()
        assert proj["factId"] == "fact:pub-count"
        assert proj["kind"] == "known_fact"
        assert proj["confidence"] == "medium"
        assert proj["defaultOff"] is True


# ---------------------------------------------------------------------------
# LedgerPlanStep construction
# ---------------------------------------------------------------------------

class TestLedgerPlanStepConstruction:
    def test_minimal_step(self) -> None:
        step = make_plan_step(
            step_id="step:lookup-orcid",
            description="Look up author ORCID profile",
        )
        assert step.step_id == "step:lookup-orcid"
        assert step.status == "pending"
        assert step.worker_role == "orchestrator"
        assert step.default_off is True

    def test_step_with_role(self) -> None:
        step = make_plan_step(
            step_id="step:search-web",
            description="Search the web for publication data",
            worker_role="research_searcher",
        )
        assert step.worker_role == "research_searcher"

    def test_description_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError, match="description"):
            LedgerPlanStep(
                step_id="step:x",
                description="x" * 241,
                worker_role="orchestrator",
            )

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LedgerPlanStep(step_id="step:x", description="   ", worker_role="orchestrator")

    def test_duplicate_fact_ids_in_depends_on_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            LedgerPlanStep(
                step_id="step:x",
                description="Some step",
                worker_role="orchestrator",
                depends_on_fact_ids=("fact:a", "fact:a"),
            )

    def test_public_projection_shape(self) -> None:
        step = make_plan_step(
            step_id="step:verify",
            description="Verify the ORCID citation count",
            worker_role="research_verifier",
            depends_on_fact_ids=("fact:orcid-avg",),
            produces_fact_ids=("fact:citation-verified",),
        )
        proj = step.public_projection()
        assert proj["stepId"] == "step:verify"
        assert proj["workerRole"] == "research_verifier"
        assert "fact:orcid-avg" in proj["dependsOnFactIds"]


# ---------------------------------------------------------------------------
# TaskLedgerContract construction + digest
# ---------------------------------------------------------------------------

class TestTaskLedgerContract:
    def test_empty_ledger(self) -> None:
        ledger = make_task_ledger(
            ledger_id="ledger:test-1",
            objective_text="What is the average citation count for this author?",
        )
        assert ledger.ledger_id == "ledger:test-1"
        assert ledger.default_off is True
        assert ledger.ledger_digest.startswith("sha256:")
        assert len(ledger.facts) == 0
        assert len(ledger.plan) == 0

    def test_objective_digest_is_sha256(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:1", objective_text="Find the answer")
        assert ledger.objective_digest.startswith("sha256:")

    def test_different_objective_different_digest(self) -> None:
        l1 = make_task_ledger(ledger_id="ledger:1", objective_text="Question A")
        l2 = make_task_ledger(ledger_id="ledger:1", objective_text="Question B")
        assert l1.ledger_digest != l2.ledger_digest
        assert l1.objective_digest != l2.objective_digest

    def test_same_contents_same_digest(self) -> None:
        l1 = make_task_ledger(ledger_id="ledger:1", objective_text="Q")
        l2 = make_task_ledger(ledger_id="ledger:1", objective_text="Q")
        assert l1.ledger_digest == l2.ledger_digest

    def test_frozen_rejects_mutation(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:1", objective_text="Q")
        with pytest.raises((TypeError, ValidationError)):
            ledger.ledger_id = "ledger:2"  # type: ignore[misc]

    def test_bad_ledger_digest_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ledger_digest"):
            TaskLedgerContract(
                ledger_id="ledger:1",
                objective_digest="sha256:" + "a" * 64,
                ledger_digest="sha256:" + "b" * 64,  # wrong
            )

    def test_duplicate_fact_ids_rejected(self) -> None:
        f1 = make_fact(fact_id="fact:x", kind=LedgerFactKind.known_fact)
        f2 = make_fact(fact_id="fact:x", kind=LedgerFactKind.working_guess)  # same id!
        with pytest.raises(ValidationError, match="unique"):
            make_task_ledger(
                ledger_id="ledger:1",
                objective_text="Q",
                facts=(f1, f2),
            )

    def test_duplicate_step_ids_rejected(self) -> None:
        s1 = make_plan_step(step_id="step:a", description="Step A")
        s2 = make_plan_step(step_id="step:a", description="Step A again")
        with pytest.raises(ValidationError, match="unique"):
            make_task_ledger(
                ledger_id="ledger:1",
                objective_text="Q",
                plan=(s1, s2),
            )

    def test_fact_lookup_helpers(self) -> None:
        f1 = make_fact(fact_id="fact:known", kind=LedgerFactKind.known_fact)
        f2 = make_fact(fact_id="fact:guess", kind=LedgerFactKind.working_guess)
        f3 = make_fact(fact_id="fact:open", kind=LedgerFactKind.open_question)
        ledger = make_task_ledger(
            ledger_id="ledger:1",
            objective_text="Q",
            facts=(f1, f2, f3),
        )
        assert len(ledger.known_facts()) == 1
        assert len(ledger.working_guesses()) == 1
        assert len(ledger.open_questions()) == 1
        assert ledger.fact_by_id("fact:known") is f1
        assert ledger.fact_by_id("fact:missing") is None

    def test_public_projection_shape(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:1", objective_text="Q")
        proj = ledger.public_projection()
        assert proj["ledgerId"] == "ledger:1"
        assert proj["defaultOff"] is True
        assert "ledgerDigest" in proj


# ---------------------------------------------------------------------------
# update_task_ledger
# ---------------------------------------------------------------------------

class TestUpdateTaskLedger:
    def test_add_facts(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:1", objective_text="Q")
        f1 = make_fact(fact_id="fact:x", kind=LedgerFactKind.working_guess)
        updated = update_task_ledger(ledger, facts=(f1,))
        assert len(updated.facts) == 1
        assert updated.ledger_digest != ledger.ledger_digest

    def test_add_plan(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:1", objective_text="Q")
        s1 = make_plan_step(step_id="step:1", description="Do something")
        updated = update_task_ledger(ledger, plan=(s1,))
        assert len(updated.plan) == 1

    def test_update_preserves_objective_digest(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:1", objective_text="Q")
        f1 = make_fact(fact_id="fact:x", kind=LedgerFactKind.known_fact)
        updated = update_task_ledger(ledger, facts=(f1,))
        assert updated.objective_digest == ledger.objective_digest

    def test_round_trip_serialisation(self) -> None:
        f1 = make_fact(fact_id="fact:x", kind=LedgerFactKind.working_guess)
        ledger = make_task_ledger(
            ledger_id="ledger:rt",
            objective_text="round trip test",
            facts=(f1,),
        )
        raw = ledger.model_dump(by_alias=True, mode="python", warnings=False)
        restored = TaskLedgerContract.model_validate(raw)
        assert restored.ledger_digest == ledger.ledger_digest


# ---------------------------------------------------------------------------
# transitively_invalidated_fact_ids
# ---------------------------------------------------------------------------

class TestTransitivelyInvalidatedFactIds:
    def _make_ledger_with_chain(self) -> TaskLedgerContract:
        # Chain: fact:A → fact:B → fact:C  (B depends on A, C depends on B)
        fa = make_fact(fact_id="fact:A", kind=LedgerFactKind.known_fact)
        fb = make_fact(fact_id="fact:B", kind=LedgerFactKind.known_fact, depends_on=("fact:A",))
        fc = make_fact(fact_id="fact:C", kind=LedgerFactKind.known_fact, depends_on=("fact:B",))
        return make_task_ledger(ledger_id="ledger:chain", objective_text="chain test", facts=(fa, fb, fc))

    def test_invalidating_root_propagates_to_all(self) -> None:
        ledger = self._make_ledger_with_chain()
        result = transitively_invalidated_fact_ids(ledger, "fact:A")
        assert result == frozenset({"fact:A", "fact:B", "fact:C"})

    def test_invalidating_middle_does_not_include_root(self) -> None:
        ledger = self._make_ledger_with_chain()
        result = transitively_invalidated_fact_ids(ledger, "fact:B")
        assert "fact:A" not in result
        assert "fact:B" in result
        assert "fact:C" in result

    def test_invalidating_leaf_returns_only_leaf(self) -> None:
        ledger = self._make_ledger_with_chain()
        result = transitively_invalidated_fact_ids(ledger, "fact:C")
        assert result == frozenset({"fact:C"})

    def test_invalidating_unknown_fact_returns_singleton(self) -> None:
        ledger = self._make_ledger_with_chain()
        result = transitively_invalidated_fact_ids(ledger, "fact:NONEXISTENT")
        assert result == frozenset({"fact:NONEXISTENT"})

    def test_diamond_dependency(self) -> None:
        # A → B, A → C, B → D, C → D (diamond)
        fa = make_fact(fact_id="fact:A", kind=LedgerFactKind.known_fact)
        fb = make_fact(fact_id="fact:B", kind=LedgerFactKind.known_fact, depends_on=("fact:A",))
        fc = make_fact(fact_id="fact:C", kind=LedgerFactKind.known_fact, depends_on=("fact:A",))
        fd = make_fact(
            fact_id="fact:D",
            kind=LedgerFactKind.known_fact,
            depends_on=("fact:B", "fact:C"),
        )
        ledger = make_task_ledger(
            ledger_id="ledger:diamond",
            objective_text="diamond",
            facts=(fa, fb, fc, fd),
        )
        result = transitively_invalidated_fact_ids(ledger, "fact:A")
        assert result == frozenset({"fact:A", "fact:B", "fact:C", "fact:D"})

    def test_isolated_facts_not_invalidated(self) -> None:
        fa = make_fact(fact_id="fact:A", kind=LedgerFactKind.known_fact)
        fb = make_fact(fact_id="fact:B", kind=LedgerFactKind.known_fact)  # no depends_on
        ledger = make_task_ledger(
            ledger_id="ledger:isolated",
            objective_text="isolated",
            facts=(fa, fb),
        )
        result = transitively_invalidated_fact_ids(ledger, "fact:A")
        assert result == frozenset({"fact:A"})
        assert "fact:B" not in result
