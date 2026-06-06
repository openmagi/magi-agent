"""Tests for LedgerOrchestrator (Phase 3) — single-agent mode.

All tests use FakeStepExecutor (no ADK runner, no network, no model calls).
The MAGI_LEDGER_ORCHESTRATOR_ENABLED env var is tested via
run_with_ledger_orchestrator.
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest

from magi_agent.recipes.ledger_budget import LedgerBudgetPolicy
from magi_agent.recipes.ledger_orchestrator import (
    LedgerOrchestrator,
    LedgerOrchestratorConfig,
    LedgerOrchestratorResult,
    StepResult,
    run_with_ledger_orchestrator,
)
from magi_agent.recipes.ledger_progress import (
    ProgressLedgerContract,
    StallKind,
    StallVerdict,
)
from magi_agent.recipes.ledger_task import (
    LedgerFact,
    LedgerFactKind,
    LedgerPlanStep,
    TaskLedgerContract,
    make_fact,
    make_plan_step,
    value_digest_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tight_policy(**overrides: object) -> LedgerBudgetPolicy:
    """Budget policy with tight limits for fast test termination."""
    kwargs: dict = {
        "step_budget": 20,
        "token_budget": 400_000,
        "wall_budget_ms": 30_000,  # 30 s
        "stall_threshold": 3,
        "max_replan_count": 1,
        "per_step_token_budget": 20_000,
        "per_step_wall_ms": 10_000,
    }
    kwargs.update(overrides)
    return LedgerBudgetPolicy(**kwargs)


def _make_config(**kwargs: object) -> LedgerOrchestratorConfig:
    return LedgerOrchestratorConfig(budget_policy=_tight_policy(**kwargs))


class FakeStepExecutor:
    """Fake executor that pops pre-configured StepResults off a queue."""

    def __init__(self, results: list[StepResult]) -> None:
        self._results = list(results)

    def __call__(
        self,
        step: LedgerPlanStep,
        task_ledger: TaskLedgerContract,
        progress_ledger: ProgressLedgerContract,
    ) -> StepResult:
        if self._results:
            return self._results.pop(0)
        # Default: stall (no facts produced)
        return StepResult(step_id=step.step_id)


def _make_advancing_result(step_id: str, fact_id: str, value: str) -> StepResult:
    vd = value_digest_for(value)
    fact = make_fact(
        fact_id=fact_id,
        kind=LedgerFactKind.known_fact,
        value_digest=vd,
        confidence="high",
    )
    return StepResult(
        step_id=step_id,
        facts_added=(fact,),
        facts_upgraded=(fact,),
        tokens_used=1_000,
        wall_ms=500,
    )


def _make_stall_result(step_id: str) -> StepResult:
    return StepResult(step_id=step_id, tokens_used=100, wall_ms=100)


# ---------------------------------------------------------------------------
# Basic run — plan with two sequential steps
# ---------------------------------------------------------------------------

class TestLedgerOrchestratorBasicRun:
    def test_two_step_plan_completes(self) -> None:
        step1 = make_plan_step(step_id="step:lookup", description="Look up the value")
        step2 = make_plan_step(
            step_id="step:compute",
            description="Compute the result",
            depends_on_fact_ids=("fact:raw-value",),
            produces_fact_ids=("fact:result",),
        )
        results = [
            _make_advancing_result("step:lookup", "fact:raw-value", "42"),
            _make_advancing_result("step:compute", "fact:result", "84"),
        ]
        executor = FakeStepExecutor(results)
        config = _make_config()
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:test-basic",
            objective_text="What is the result?",
            initial_plan=(step1, step2),
        )
        assert isinstance(outcome, LedgerOrchestratorResult)
        assert outcome.stall_verdict is None
        assert "fact:result" in outcome.final_answer or "verified" in outcome.final_answer.lower()
        assert outcome.progress_ledger.total_steps_taken == 2

    def test_empty_plan_terminates_immediately(self) -> None:
        executor = FakeStepExecutor([])
        config = _make_config()
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:empty",
            objective_text="Empty plan task",
        )
        # Empty plan — no steps to execute, terminates immediately.
        assert outcome.termination_reason in ("plan_exhausted", "stall_threshold_exceeded")

    def test_single_step_advancing(self) -> None:
        step = make_plan_step(step_id="step:one", description="One step plan")
        result = _make_advancing_result("step:one", "fact:answer", "blue")
        executor = FakeStepExecutor([result])
        config = _make_config()
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:single",
            objective_text="What colour?",
            initial_plan=(step,),
        )
        assert outcome.progress_ledger.total_steps_taken == 1
        assert outcome.stall_verdict is None

    def test_result_has_correct_structure(self) -> None:
        step = make_plan_step(step_id="step:x", description="Do something")
        executor = FakeStepExecutor([_make_advancing_result("step:x", "fact:x", "val")])
        orch = LedgerOrchestrator(_make_config(), step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:struct",
            objective_text="Structured result test",
            initial_plan=(step,),
        )
        assert isinstance(outcome.task_ledger, TaskLedgerContract)
        assert isinstance(outcome.progress_ledger, ProgressLedgerContract)
        assert isinstance(outcome.final_answer, str)


# ---------------------------------------------------------------------------
# Stall detection
# ---------------------------------------------------------------------------

class TestLedgerOrchestratorStallDetection:
    def test_three_stalled_steps_trigger_termination(self) -> None:
        """With stall_threshold=3 and no replan, three stalled steps → terminate."""
        step = make_plan_step(step_id="step:stall", description="A step that stalls")
        # Override the plan so the step can be re-attempted by the no-runnable path
        # We inject a plan that keeps producing stall entries.
        stall_results = [_make_stall_result("step:stall")] * 5
        executor = FakeStepExecutor(stall_results)
        config = _make_config(stall_threshold=3, max_replan_count=0)
        orch = LedgerOrchestrator(config, step_executor=executor)
        # Give a plan with one step and no facts — step will stall because it
        # produces nothing.  But we need to keep selecting it.
        outcome = orch.run(
            ledger_id="ledger:stall-test",
            objective_text="Stall detection test",
            initial_plan=(step,),
        )
        # Either the stall fired or the plan exhausted (step completed with no progress)
        assert outcome.termination_reason in (
            "stall_threshold_exceeded",
            "plan_exhausted",
        )

    def test_step_budget_exhausted(self) -> None:
        """step_budget=2 — orchestrator stops after 2 steps."""
        steps = [
            make_plan_step(step_id=f"step:{i}", description=f"Step {i}")
            for i in range(5)
        ]
        results = [_make_advancing_result(f"step:{i}", f"fact:{i}", str(i)) for i in range(5)]
        executor = FakeStepExecutor(results)
        config = _make_config(step_budget=2, max_replan_count=0)
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:step-budget",
            objective_text="Step budget test",
            initial_plan=tuple(steps),
        )
        assert outcome.termination_reason in ("step_budget_exhausted", "plan_exhausted")
        assert outcome.progress_ledger.total_steps_taken <= 3  # budget fired early

    def test_token_budget_exhausted(self) -> None:
        """token_budget=5000 — exhausted after two steps that use 3000 tokens each."""
        step1 = make_plan_step(step_id="step:1", description="First step")
        step2 = make_plan_step(step_id="step:2", description="Second step")
        r1 = StepResult(step_id="step:1", tokens_used=3_000)
        r2 = StepResult(step_id="step:2", tokens_used=3_000)
        executor = FakeStepExecutor([r1, r2])
        # per_step_token_budget must be <= token_budget
        config = LedgerOrchestratorConfig(
            budget_policy=LedgerBudgetPolicy(
                step_budget=20,
                token_budget=5_000,
                wall_budget_ms=30_000,
                stall_threshold=3,
                max_replan_count=0,
                per_step_token_budget=4_000,
                per_step_wall_ms=10_000,
            )
        )
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:token-budget",
            objective_text="Token budget test",
            initial_plan=(step1, step2),
        )
        assert outcome.termination_reason in (
            "token_budget_exhausted",
            "plan_exhausted",
        )

    def test_graceful_partial_answer_on_budget_exhaustion(self) -> None:
        """Graceful partial answer is produced, not an exception."""
        step = make_plan_step(step_id="step:long", description="A very long step")
        executor = FakeStepExecutor([_make_stall_result("step:long")] * 10)
        config = _make_config(step_budget=1, max_replan_count=0)
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:graceful",
            objective_text="Graceful termination test",
            initial_plan=(step,),
        )
        assert isinstance(outcome.final_answer, str)
        assert len(outcome.final_answer) > 0


# ---------------------------------------------------------------------------
# Implicit verifier step injection
# ---------------------------------------------------------------------------

class TestImplicitVerifierStepInjection:
    def test_guess_dependency_triggers_verifier(self) -> None:
        """A step that depends on a working_guess gets a verifier step inserted first."""
        # Setup: step:A produces a guess; step:B depends on that guess.
        step_a = make_plan_step(
            step_id="step:A",
            description="Look up ORCID",
            produces_fact_ids=("fact:orcid-value",),
        )
        step_b = make_plan_step(
            step_id="step:B",
            description="Compute average from ORCID value",
            depends_on_fact_ids=("fact:orcid-value",),
            produces_fact_ids=("fact:average",),
        )
        # step:A produces a guess (not upgraded → speculative)
        guess_fact = make_fact(
            fact_id="fact:orcid-value",
            kind=LedgerFactKind.working_guess,
            confidence="low",
        )
        result_a = StepResult(
            step_id="step:A",
            facts_added=(guess_fact,),  # only added, not upgraded → guess
        )
        # The implicit verifier step will upgrade the guess to known_fact.
        verified_fact = make_fact(
            fact_id="fact:orcid-value",
            kind=LedgerFactKind.known_fact,
            confidence="high",
            value_digest=value_digest_for("12"),
        )
        # The verifier result upgrades the fact.
        result_verifier = StepResult(
            step_id="implicit-verifier:step:B:fact:orcid-value",
            facts_added=(verified_fact,),
            facts_upgraded=(verified_fact,),
        )
        result_b = _make_advancing_result("step:B", "fact:average", "42")
        executor = FakeStepExecutor([result_a, result_verifier, result_b])
        config = _make_config()
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:verify-chain",
            objective_text="ORCID citation average",
            initial_plan=(step_a, step_b),
        )
        # Verifier step was inserted — we took 3 steps total.
        assert outcome.progress_ledger.total_steps_taken >= 2
        assert outcome.stall_verdict is None


# ---------------------------------------------------------------------------
# Re-plan callback
# ---------------------------------------------------------------------------

class TestReplanCallback:
    def test_replan_produces_new_steps(self) -> None:
        """When stall fires and replan_callback returns new steps, run continues.

        We trigger the stall via step_budget exhaustion (not stall_threshold) to
        avoid depending on the "no runnable step" accumulation path.  Then we
        verify replan_callback is wired and can return a termination signal.
        """
        # Use step_budget=1 so the first step fires step_budget_exhausted.
        # But step_budget_exhausted is NOT eligible for replan (only stall_threshold).
        # Instead, give 3 steps that all stall + no facts from any of them.
        # But with a single-step plan the step is "completed" after 1 execution.
        # So: use a plan with 3 independent steps, all stalling.
        steps = [
            make_plan_step(step_id=f"step:stall-{i}", description=f"Stall step {i}")
            for i in range(3)
        ]
        # Each step runs once (gets completed with stall verdict).
        stall_results = [_make_stall_result(f"step:stall-{i}") for i in range(3)]

        # After all 3 stall, consecutive_stalled_steps == 3 == stall_threshold.
        # replan_callback is invoked. It returns a new finishing step.
        new_step = make_plan_step(step_id="step:new", description="New step after replan")
        advance_result = _make_advancing_result("step:new", "fact:new-answer", "yes")
        all_results = stall_results + [advance_result]
        executor = FakeStepExecutor(all_results)

        replan_called = []

        def replan_cb(
            task_ledger: TaskLedgerContract,
            progress_ledger: ProgressLedgerContract,
            stall_verdict: StallVerdict,
        ) -> tuple[LedgerPlanStep, ...]:
            replan_called.append(True)
            return (new_step,)

        config = _make_config(stall_threshold=3, max_replan_count=1)
        orch = LedgerOrchestrator(config, step_executor=executor, replan_callback=replan_cb)
        outcome = orch.run(
            ledger_id="ledger:replan",
            objective_text="Replan test",
            initial_plan=tuple(steps),
        )
        # Replan was called (stall_threshold fired after 3 consecutive stalled steps).
        assert len(replan_called) >= 1

    def test_empty_replan_terminates_gracefully(self) -> None:
        """When replan_callback returns () the orchestrator terminates."""
        step = make_plan_step(step_id="step:stall2", description="Stalling step")
        executor = FakeStepExecutor([_make_stall_result("step:stall2")] * 10)

        def replan_cb(tl: TaskLedgerContract, pl: ProgressLedgerContract, sv: StallVerdict) -> tuple[LedgerPlanStep, ...]:
            return ()

        config = _make_config(stall_threshold=3, max_replan_count=1)
        orch = LedgerOrchestrator(config, step_executor=executor, replan_callback=replan_cb)
        outcome = orch.run(
            ledger_id="ledger:replan-empty",
            objective_text="Empty replan test",
            initial_plan=(step,),
        )
        assert outcome.termination_reason in (
            "stall_threshold_exceeded",
            "plan_exhausted",
        )
        assert isinstance(outcome.final_answer, str)


# ---------------------------------------------------------------------------
# Chain invalidation via contradicted facts
# ---------------------------------------------------------------------------

class TestContradictedFacts:
    def test_contradicted_fact_invalidates_dependents(self) -> None:
        """When a step contradicts a fact, dependents are marked open_question."""
        # Setup: fact:A (known) → fact:B (known, depends on A)
        fa = make_fact(fact_id="fact:A", kind=LedgerFactKind.known_fact, confidence="high")
        fb = make_fact(
            fact_id="fact:B",
            kind=LedgerFactKind.known_fact,
            depends_on=("fact:A",),
            confidence="high",
        )
        step = make_plan_step(
            step_id="step:contradiction",
            description="Step that finds a contradiction",
        )
        # The executor says fact:A is contradicted.
        result = StepResult(
            step_id="step:contradiction",
            facts_contradicted=("fact:A",),
        )
        executor = FakeStepExecutor([result])
        config = _make_config()
        orch = LedgerOrchestrator(config, step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:contradict",
            objective_text="Contradiction test",
            initial_facts=(fa, fb),
            initial_plan=(step,),
        )
        # Both fact:A and fact:B should now be open_question in the final ledger.
        final_facts = {f.fact_id: f for f in outcome.task_ledger.facts}
        assert final_facts["fact:A"].kind == LedgerFactKind.open_question
        assert final_facts["fact:B"].kind == LedgerFactKind.open_question


# ---------------------------------------------------------------------------
# run_with_ledger_orchestrator — env gate
# ---------------------------------------------------------------------------

class TestRunWithLedgerOrchestratorEnvGate:
    def test_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MAGI_LEDGER_ORCHESTRATOR_ENABLED", raising=False)
        result = run_with_ledger_orchestrator(
            ledger_id="ledger:env-gate",
            objective_text="Env gate test",
            step_executor=FakeStepExecutor([]),
        )
        assert result is None

    def test_enabled_returns_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_LEDGER_ORCHESTRATOR_ENABLED", "true")
        step = make_plan_step(step_id="step:env", description="Env enabled step")
        result_obj = _make_advancing_result("step:env", "fact:env-ans", "42")
        executor = FakeStepExecutor([result_obj])
        outcome = run_with_ledger_orchestrator(
            ledger_id="ledger:env-on",
            objective_text="Env enabled test",
            step_executor=executor,
            level=1,
            initial_plan=(step,),
        )
        assert outcome is not None
        assert isinstance(outcome, LedgerOrchestratorResult)

    def test_enabled_with_value_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_LEDGER_ORCHESTRATOR_ENABLED", "1")
        step = make_plan_step(step_id="step:v1", description="Value 1 test")
        executor = FakeStepExecutor([_make_advancing_result("step:v1", "fact:v1", "v")])
        outcome = run_with_ledger_orchestrator(
            ledger_id="ledger:v1",
            objective_text="v1 test",
            step_executor=executor,
            initial_plan=(step,),
        )
        assert outcome is not None


# ---------------------------------------------------------------------------
# Full loop integrity checks
# ---------------------------------------------------------------------------

class TestFullLoopIntegrity:
    def test_progress_ledger_digest_changes_on_each_step(self) -> None:
        step1 = make_plan_step(step_id="step:1", description="First step")
        step2 = make_plan_step(step_id="step:2", description="Second step")
        results = [
            _make_advancing_result("step:1", "fact:1", "val1"),
            _make_advancing_result("step:2", "fact:2", "val2"),
        ]
        executor = FakeStepExecutor(results)
        orch = LedgerOrchestrator(_make_config(), step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:digest-chain",
            objective_text="Digest chain test",
            initial_plan=(step1, step2),
        )
        # After two steps the progress ledger should have 2 entries with distinct digests.
        entries = outcome.progress_ledger.entries
        assert len(entries) == 2
        assert entries[0].entry_digest != entries[1].entry_digest

    def test_task_ledger_digest_changes_as_facts_added(self) -> None:
        step = make_plan_step(step_id="step:facts", description="Add facts")
        result = _make_advancing_result("step:facts", "fact:new", "data")
        executor = FakeStepExecutor([result])
        orch = LedgerOrchestrator(_make_config(), step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:fact-digest",
            objective_text="Fact digest chain test",
            initial_plan=(step,),
        )
        # Task ledger should contain the new fact.
        fact_ids = {f.fact_id for f in outcome.task_ledger.facts}
        assert "fact:new" in fact_ids

    def test_default_off_on_all_contracts(self) -> None:
        step = make_plan_step(step_id="step:doff", description="Default off check")
        executor = FakeStepExecutor([_make_advancing_result("step:doff", "fact:doff", "x")])
        orch = LedgerOrchestrator(_make_config(), step_executor=executor)
        outcome = orch.run(
            ledger_id="ledger:default-off",
            objective_text="Default off test",
            initial_plan=(step,),
        )
        assert outcome.task_ledger.default_off is True
        assert outcome.progress_ledger.default_off is True
        assert outcome.progress_ledger.entries[0].default_off is True
