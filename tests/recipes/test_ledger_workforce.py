"""Tests for LedgerWorkforce Phase 4 — RoleAssignmentPolicy + batch_independent_steps."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.recipes.ledger_task import (
    LedgerFactKind,
    make_fact,
    make_plan_step,
    make_task_ledger,
    update_task_ledger,
)
from magi_agent.recipes.ledger_workforce import (
    LedgerOrchestrationMode,
    RoleAssignmentPolicy,
    StepBatch,
    assign_worker_role,
    batch_independent_steps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_ledger(ledger_id: str = "ledger:wf") -> object:
    return make_task_ledger(ledger_id=ledger_id, objective_text="Workforce test")


# ---------------------------------------------------------------------------
# LedgerOrchestrationMode
# ---------------------------------------------------------------------------

class TestLedgerOrchestrationMode:
    def test_single_agent_value(self) -> None:
        assert LedgerOrchestrationMode.single_agent.value == "single_agent"

    def test_multi_agent_value(self) -> None:
        assert LedgerOrchestrationMode.multi_agent_workforce.value == "multi_agent_workforce"

    def test_mode_is_string_enum(self) -> None:
        mode = LedgerOrchestrationMode("single_agent")
        assert mode == LedgerOrchestrationMode.single_agent


# ---------------------------------------------------------------------------
# RoleAssignmentPolicy construction
# ---------------------------------------------------------------------------

class TestRoleAssignmentPolicyConstruction:
    def test_default_policy(self) -> None:
        policy = RoleAssignmentPolicy()
        assert policy.default_off is True
        assert policy.orchestration_mode == LedgerOrchestrationMode.multi_agent_workforce

    def test_frozen(self) -> None:
        policy = RoleAssignmentPolicy()
        with pytest.raises((TypeError, ValidationError)):
            policy.policy_name = "changed"  # type: ignore[misc]

    def test_policy_digest_format(self) -> None:
        policy = RoleAssignmentPolicy()
        d = policy.policy_digest()
        assert d.startswith("sha256:")
        assert len(d) == 71

    def test_policy_digest_stable(self) -> None:
        p1 = RoleAssignmentPolicy()
        p2 = RoleAssignmentPolicy()
        assert p1.policy_digest() == p2.policy_digest()

    def test_public_projection_contains_digest(self) -> None:
        policy = RoleAssignmentPolicy()
        proj = policy.public_projection()
        assert proj["policyDigest"] == policy.policy_digest()
        assert proj["defaultOff"] is True


# ---------------------------------------------------------------------------
# Role assignment — deterministic lookup
# ---------------------------------------------------------------------------

class TestRoleAssignment:
    def _make_ledger_with_fact(self, kind: LedgerFactKind, fact_id: str) -> object:
        fact = make_fact(fact_id=fact_id, kind=kind)
        ledger = make_task_ledger(ledger_id="ledger:ra", objective_text="role assign test")
        return update_task_ledger(ledger, facts=(fact,))  # type: ignore[arg-type]

    def test_search_step_with_open_question_assigns_searcher(self) -> None:
        ledger = self._make_ledger_with_fact(LedgerFactKind.open_question, "fact:q")
        step = make_plan_step(
            step_id="step:s",
            description="Search for publication data",
            depends_on_fact_ids=("fact:q",),
        )
        role = assign_worker_role(step, ledger)  # type: ignore[arg-type]
        assert role == "research_searcher"

    def test_verify_step_assigns_verifier(self) -> None:
        ledger = self._make_ledger_with_fact(LedgerFactKind.working_guess, "fact:guess")
        step = make_plan_step(
            step_id="step:v",
            description="Verify the ORCID value",
            depends_on_fact_ids=("fact:guess",),
        )
        role = assign_worker_role(step, ledger)  # type: ignore[arg-type]
        assert role == "research_verifier"

    def test_synthesize_step_assigns_reviewer(self) -> None:
        ledger = self._make_ledger_with_fact(LedgerFactKind.known_fact, "fact:k")
        step = make_plan_step(
            step_id="step:syn",
            description="Synthesize and write the final answer",
            depends_on_fact_ids=("fact:k",),
        )
        role = assign_worker_role(step, ledger)  # type: ignore[arg-type]
        assert role == "synthesis_reviewer"

    def test_inspect_step_assigns_inspector(self) -> None:
        ledger = self._make_ledger_with_fact(LedgerFactKind.known_fact, "fact:k2")
        step = make_plan_step(
            step_id="step:ins",
            description="Inspect the attachment file",
            depends_on_fact_ids=("fact:k2",),
        )
        role = assign_worker_role(step, ledger)  # type: ignore[arg-type]
        assert role == "source_inspector"

    def test_no_deps_step_gets_role(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:nodeps", objective_text="no deps")
        step = make_plan_step(step_id="step:nd", description="Search for initial data")
        role = assign_worker_role(step, ledger)
        assert role in ("research_searcher", "source_inspector", "claim_mapper",
                         "research_verifier", "synthesis_reviewer")

    def test_all_returned_roles_are_valid(self) -> None:
        """Every assignment must be a valid ResearchChildRoleName."""
        from magi_agent.research.child_roles import RESEARCH_CHILD_ROLE_NAMES
        ledger = make_task_ledger(ledger_id="ledger:allroles", objective_text="all roles")
        descriptions = [
            "Search for data",
            "Inspect the file",
            "Map the claims",
            "Verify the result",
            "Synthesize the answer",
            "Unknown action",
        ]
        for i, desc in enumerate(descriptions):
            step = make_plan_step(step_id=f"step:desc-{i}", description=desc)
            role = assign_worker_role(step, ledger)
            assert role in RESEARCH_CHILD_ROLE_NAMES, f"Invalid role {role!r} for {desc!r}"


# ---------------------------------------------------------------------------
# batch_independent_steps
# ---------------------------------------------------------------------------

class TestBatchIndependentSteps:
    def test_no_plan_returns_none(self) -> None:
        ledger = make_task_ledger(ledger_id="ledger:empty-batch", objective_text="empty")
        result = batch_independent_steps(ledger)
        assert result is None

    def test_two_independent_steps_batched(self) -> None:
        """Two steps with no shared dependencies or produces can be batched together."""
        step_a = make_plan_step(
            step_id="step:A",
            description="Search for author",
            produces_fact_ids=("fact:author",),
        )
        step_b = make_plan_step(
            step_id="step:B",
            description="Search for publication count",
            produces_fact_ids=("fact:pub-count",),
        )
        ledger = make_task_ledger(
            ledger_id="ledger:batch2",
            objective_text="batch two",
            plan=(step_a, step_b),
        )
        batch = batch_independent_steps(ledger)
        assert batch is not None
        assert len(batch.steps) == 2
        assert len(batch.assigned_roles) == 2
        assert batch.default_off is True

    def test_step_with_unsatisfied_deps_not_included(self) -> None:
        """A step whose dependency is not in the ledger should be excluded."""
        step_a = make_plan_step(
            step_id="step:A",
            description="First step",
            produces_fact_ids=("fact:A",),
        )
        step_b = make_plan_step(
            step_id="step:B",
            description="Second step depends on A",
            depends_on_fact_ids=("fact:A",),
            produces_fact_ids=("fact:B",),
        )
        ledger = make_task_ledger(
            ledger_id="ledger:dep",
            objective_text="dep test",
            plan=(step_a, step_b),
        )
        batch = batch_independent_steps(ledger)
        assert batch is not None
        assert len(batch.steps) == 1
        assert batch.steps[0].step_id == "step:A"

    def test_step_with_guess_dep_excluded(self) -> None:
        """Steps whose dependency is a working_guess are excluded from batch."""
        guess = make_fact(fact_id="fact:guess", kind=LedgerFactKind.working_guess)
        step = make_plan_step(
            step_id="step:dep-on-guess",
            description="Use the guess",
            depends_on_fact_ids=("fact:guess",),
        )
        ledger = make_task_ledger(
            ledger_id="ledger:guess-dep",
            objective_text="guess dep",
            facts=(guess,),
            plan=(step,),
        )
        batch = batch_independent_steps(ledger)
        # Step has a guess dep — excluded from batch.
        assert batch is None

    def test_write_conflict_prevents_batching(self) -> None:
        """Two steps producing the same fact_id cannot be in the same batch."""
        step_a = make_plan_step(
            step_id="step:A",
            description="Find the count",
            produces_fact_ids=("fact:count",),
        )
        step_b = make_plan_step(
            step_id="step:B",
            description="Also find the count",
            produces_fact_ids=("fact:count",),  # write conflict!
        )
        ledger = make_task_ledger(
            ledger_id="ledger:conflict",
            objective_text="conflict",
            plan=(step_a, step_b),
        )
        batch = batch_independent_steps(ledger)
        assert batch is not None
        assert len(batch.steps) == 1  # only step_a included; step_b excluded

    def test_completed_steps_not_included(self) -> None:
        """Steps that are already completed/skipped/failed are not batched."""
        from magi_agent.recipes.ledger_task import make_plan_step
        step_done = make_plan_step(
            step_id="step:done",
            description="Already done",
            status="completed",
        )
        step_pending = make_plan_step(
            step_id="step:pending",
            description="Search for pending data",
            status="pending",
        )
        ledger = make_task_ledger(
            ledger_id="ledger:mixed",
            objective_text="mixed status",
            plan=(step_done, step_pending),
        )
        batch = batch_independent_steps(ledger)
        assert batch is not None
        assert len(batch.steps) == 1
        assert batch.steps[0].step_id == "step:pending"

    def test_batch_has_correct_id_format(self) -> None:
        step = make_plan_step(step_id="step:x", description="Search for x")
        ledger = make_task_ledger(
            ledger_id="ledger:bid",
            objective_text="batch id test",
            plan=(step,),
        )
        batch = batch_independent_steps(ledger, batch_id_prefix="run", batch_number=7)
        assert batch is not None
        assert batch.batch_id == "run:7"

    def test_step_with_known_fact_dep_included(self) -> None:
        """Steps whose dependency is a known_fact are eligible for batching."""
        fact = make_fact(fact_id="fact:known", kind=LedgerFactKind.known_fact)
        step = make_plan_step(
            step_id="step:uses-known",
            description="Use the known fact to map claims",
            depends_on_fact_ids=("fact:known",),
        )
        ledger = make_task_ledger(
            ledger_id="ledger:known-dep",
            objective_text="known dep",
            facts=(fact,),
            plan=(step,),
        )
        batch = batch_independent_steps(ledger)
        assert batch is not None
        assert len(batch.steps) == 1


# ---------------------------------------------------------------------------
# StepBatch validation
# ---------------------------------------------------------------------------

class TestStepBatch:
    def test_mismatched_roles_rejected(self) -> None:
        step = make_plan_step(step_id="step:z", description="Do z")
        with pytest.raises(ValidationError, match="same length"):
            StepBatch(
                batch_id="batch:0",
                steps=(step,),
                assigned_roles=(),  # mismatched
            )

    def test_valid_batch_construction(self) -> None:
        step = make_plan_step(step_id="step:w", description="Search for w")
        batch = StepBatch(
            batch_id="batch:1",
            steps=(step,),
            assigned_roles=("research_searcher",),
        )
        assert batch.default_off is True
        assert batch.batch_id == "batch:1"
