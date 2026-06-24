"""Ledger-based orchestration loop — single-agent mode.

Default-OFF.  Activated only when ``MAGI_LEDGER_ORCHESTRATOR_ENABLED=true``.
No ADK runner, provider call, browser, or live execution is attached in tests.

Architecture
------------
The orchestrator drives a plan-execute-assess-stall-detect cycle:

    1. ``initialise_task_ledger()`` — build the initial TaskLedgerContract.
    2. Loop:
       a. ``select_next_step()``  — pick the first pending step whose
          ``depends_on_fact_ids`` are all resolved (no unverified guesses used
          as chain inputs without an implicit verifier step).
       b. ``execute_step()``     — delegate to the injected ``StepExecutor``.
       c. ``update_facts()``     — merge new facts into the task ledger.
       d. ``update_progress()``  — append a progress entry with the verdict.
       e. ``detect_stall()``     — check all budget gates.
       f. If stall: ``replan_or_terminate()`` — invoke the ``ReplanCallback``
          or produce a graceful partial answer.
       g. If acceptance criteria satisfied: ``assemble_answer()``.
    3. Return ``LedgerOrchestratorResult``.

The orchestrator is injected with:
  - A ``StepExecutor`` callable — in production this wraps the real ADK runner;
    in tests a ``FakeStepExecutor`` is supplied (no network, no model calls).
  - A ``ReplanCallback`` callable — produces new plan steps from the current
    ledger + stall verdict.
  - A ``LedgerOrchestratorConfig`` that carries the ``LedgerBudgetPolicy`` and
    the ``MAGI_LEDGER_ORCHESTRATOR_ENABLED`` flag.

``MAGI_LEDGER_ORCHESTRATOR_ENABLED=false`` (default): callers fall back to the
existing flat ``run_gaia_question()`` loop untouched.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.recipes.ledger_budget import LedgerBudgetPolicy, default_gaia_policy
from magi_agent.recipes.ledger_progress import (
    ProgressLedgerEntry,
    ProgressLedgerContract,
    ProgressStepVerdict,
    StallKind,
    StallVerdict,
    derive_step_verdict,
    detect_stall,
    make_progress_ledger,
    make_progress_ledger_entry,
    update_progress_ledger,
)
from magi_agent.recipes.ledger_task import (
    LedgerFact,
    LedgerFactKind,
    LedgerPlanStep,
    TaskLedgerContract,
    make_task_ledger,
    update_task_ledger,
    transitively_invalidated_fact_ids,
)


# ---------------------------------------------------------------------------
# Environment gate
# ---------------------------------------------------------------------------

def _ledger_orchestrator_enabled() -> bool:
    """Return True when ``MAGI_LEDGER_ORCHESTRATOR_ENABLED`` truthy."""
    # I-4: routed through the typed flag registry. Pre-I-4 truthy set
    # ``{1, true, yes}`` widens to canonical ``{1, true, yes, on}``.
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool("MAGI_LEDGER_ORCHESTRATOR_ENABLED")


# ---------------------------------------------------------------------------
# Step execution protocol
# ---------------------------------------------------------------------------

class StepResult(BaseModel):
    """Outcome of a single orchestration step."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    facts_added: tuple[LedgerFact, ...] = Field(default=())
    facts_upgraded: tuple[LedgerFact, ...] = Field(default=())
    facts_contradicted: tuple[str, ...] = Field(default=())
    """fact_ids where value_digest mismatched — chain invalidation required."""

    tokens_used: int = Field(default=0, ge=0)
    wall_ms: int = Field(default=0, ge=0)
    partial_answer: str | None = Field(default=None)
    """Non-None when the step produced a partial answer (e.g. budget-terminated)."""


class StepExecutor(Protocol):
    """Protocol for the injectable step executor.

    In production: wraps the ADK runner.
    In tests: a ``FakeStepExecutor`` that returns pre-configured results.
    """

    def __call__(
        self,
        step: LedgerPlanStep,
        task_ledger: TaskLedgerContract,
        progress_ledger: ProgressLedgerContract,
    ) -> StepResult:
        ...


class ReplanCallback(Protocol):
    """Protocol for the injectable re-plan callback.

    Called when stall-detection fires.  Returns new pending plan steps or an
    empty tuple to trigger graceful termination.
    """

    def __call__(
        self,
        task_ledger: TaskLedgerContract,
        progress_ledger: ProgressLedgerContract,
        stall_verdict: StallVerdict,
    ) -> tuple[LedgerPlanStep, ...]:
        ...


# ---------------------------------------------------------------------------
# Orchestrator configuration
# ---------------------------------------------------------------------------

class LedgerOrchestratorConfig(BaseModel):
    """Configuration contract for the LedgerOrchestrator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    budget_policy: LedgerBudgetPolicy
    """Per-task budget contract."""

    orchestration_mode: Literal["single_agent"] = "single_agent"
    """Orchestration mode.  Only ``"single_agent"`` is supported in Phase 3."""


# ---------------------------------------------------------------------------
# Orchestrator result
# ---------------------------------------------------------------------------

class LedgerOrchestratorResult(BaseModel):
    """Final result of a ledger-orchestrated run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    final_answer: str
    """The best available answer — may be partial if budget was exhausted."""

    task_ledger: TaskLedgerContract
    progress_ledger: ProgressLedgerContract
    stall_verdict: StallVerdict | None = None
    """Non-None when the run was terminated by stall-detection."""

    termination_reason: Literal[
        "acceptance_criteria_satisfied",
        "plan_exhausted",
        "stall_threshold_exceeded",
        "step_budget_exhausted",
        "token_budget_exhausted",
        "wall_budget_exhausted",
        "replan_count_exhausted",
    ] = "plan_exhausted"


# ---------------------------------------------------------------------------
# Core orchestration helpers
# ---------------------------------------------------------------------------

def _select_next_step(
    task_ledger: TaskLedgerContract,
    progress_ledger: ProgressLedgerContract,
) -> LedgerPlanStep | None:
    """Pick the next executable pending step.

    A step is executable when all ``depends_on_fact_ids`` are present in the
    task ledger AND none of the dependencies is a ``working_guess`` (guesses
    require an implicit verifier step before the downstream step can run).

    Returns ``None`` when no step is ready.
    """
    resolved_ids = frozenset(f.fact_id for f in task_ledger.facts)
    guess_ids = frozenset(f.fact_id for f in task_ledger.facts if f.kind == LedgerFactKind.working_guess)

    for step in task_ledger.plan:
        if step.status != "pending":
            continue
        deps = frozenset(step.depends_on_fact_ids)
        if not deps.issubset(resolved_ids):
            # Dependencies not yet available — skip.
            continue
        speculative_deps = deps & guess_ids
        if speculative_deps:
            # Speculative chain — inject implicit verifier step (return it instead).
            return _make_implicit_verifier_step(step, speculative_deps)
        return step
    return None


def _make_implicit_verifier_step(
    downstream_step: LedgerPlanStep,
    speculative_dep_ids: frozenset[str],
) -> LedgerPlanStep:
    """Build an implicit ``research_verifier`` step for speculative dependencies."""
    from magi_agent.recipes.ledger_task import make_plan_step
    dep_list = "-".join(sorted(speculative_dep_ids))[:60]
    return make_plan_step(
        step_id=f"implicit-verifier:{downstream_step.step_id}:{dep_list}",
        description=f"Verify speculative inputs before {downstream_step.step_id}",
        worker_role="research_verifier",
        depends_on_fact_ids=tuple(sorted(speculative_dep_ids)),
        produces_fact_ids=tuple(sorted(speculative_dep_ids)),
    )


def _apply_step_result_to_ledger(
    task_ledger: TaskLedgerContract,
    result: StepResult,
) -> TaskLedgerContract:
    """Merge step facts into the task ledger, handling contradictions and upgrades."""
    existing: dict[str, LedgerFact] = {f.fact_id: f for f in task_ledger.facts}

    # Apply contradictions — mark dependent facts as open questions.
    for cid in result.facts_contradicted:
        invalid_ids = transitively_invalidated_fact_ids(task_ledger, cid)
        for iid in invalid_ids:
            if iid in existing:
                old = existing[iid]
                existing[iid] = old.model_copy(update={"kind": LedgerFactKind.open_question})

    # Apply upgrades.
    for fact in result.facts_upgraded:
        existing[fact.fact_id] = fact

    # Apply new facts.
    for fact in result.facts_added:
        if fact.fact_id not in existing:
            existing[fact.fact_id] = fact

    return update_task_ledger(task_ledger, facts=tuple(existing.values()))


def _mark_step_status(
    task_ledger: TaskLedgerContract,
    step_id: str,
    status: Literal["pending", "in_progress", "completed", "skipped", "failed"],
) -> TaskLedgerContract:
    """Return a new task ledger with the named step's status updated."""
    new_plan = tuple(
        s.model_copy(update={"status": status}) if s.step_id == step_id else s
        for s in task_ledger.plan
    )
    return update_task_ledger(task_ledger, plan=new_plan)


def _all_plan_steps_done(task_ledger: TaskLedgerContract) -> bool:
    return all(
        s.status in ("completed", "skipped", "failed")
        for s in task_ledger.plan
    )


def _assemble_answer(task_ledger: TaskLedgerContract) -> str:
    """Assemble the best available answer from verified facts in the task ledger.

    Concatenates all known facts and verified intermediates into a structured
    summary.

    H-36 (item 5, REVIEW-A ``review/recipes-orchestration.md`` L6): this
    function is currently **benchmark-only / deterministic test fixture**, NOT
    a production assistant projection. The intended production path is an
    LLM call over the ledger projection; until that lands, the structured
    string below is the dormant default. ``LedgerOrchestrator`` itself is
    default-OFF (``MAGI_LEDGER_ORCHESTRATOR_ENABLED``), so no live user
    surface ships this output today — it appears only in GAIA-style
    benchmark harness runs and recipe tests.

    DO NOT wire this into a live answer-emit path without first replacing
    the body with the real LLM projection: a benchmark-shaped
    "deterministic structured string" leaked into a user-visible surface
    would be a clear regression.
    """
    known = task_ledger.known_facts()
    if not known:
        guesses = task_ledger.working_guesses()
        if guesses:
            parts = [f"{f.fact_id}: {f.kind.value} (confidence={f.confidence})" for f in guesses]
            return "Partial answer (unverified): " + "; ".join(parts)
        return "No answer available"
    parts = []
    for fact in known:
        label = fact.public_label or fact.fact_id
        parts.append(f"{label} (confidence={fact.confidence})")
    return "Answer based on verified facts: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class LedgerOrchestrator:
    """Single-agent ledger-based orchestration loop.

    Usage
    -----
    ::

        config = LedgerOrchestratorConfig(budget_policy=default_gaia_policy(2))
        orchestrator = LedgerOrchestrator(config, step_executor=my_executor)
        result = orchestrator.run(
            ledger_id="ledger:q123",
            objective_text="What is the average citation count?",
            initial_plan=(step1, step2),
        )
    """

    def __init__(
        self,
        config: LedgerOrchestratorConfig,
        *,
        step_executor: StepExecutor,
        replan_callback: ReplanCallback | None = None,
    ) -> None:
        self._config = config
        self._step_executor = step_executor
        self._replan_callback = replan_callback or _noop_replan

    def run(
        self,
        *,
        ledger_id: str,
        objective_text: str,
        initial_facts: tuple[LedgerFact, ...] = (),
        initial_plan: tuple[LedgerPlanStep, ...] = (),
        acceptance_criteria_ref: str | None = None,
    ) -> LedgerOrchestratorResult:
        """Execute the ledger orchestration loop.

        Parameters
        ----------
        ledger_id:
            Stable public identifier for this orchestration run.
        objective_text:
            The full task objective.  Its sha256 is stored; the raw text is not
            persisted in the contract.
        initial_facts:
            Known facts at the start of the run (usually empty).
        initial_plan:
            Initial plan steps.  May be empty if the orchestrator should derive
            the plan on the first step.
        acceptance_criteria_ref:
            Optional ``criteria_set_id`` governing done-ness.

        Returns
        -------
        LedgerOrchestratorResult
            Structured result including the final answer, full ledger state, and
            the stall verdict if the run was terminated by budget exhaustion.
        """
        policy = self._config.budget_policy

        # Initialise ledgers.
        task_ledger = make_task_ledger(
            ledger_id=ledger_id,
            objective_text=objective_text,
            facts=initial_facts,
            plan=initial_plan,
            acceptance_criteria_ref=acceptance_criteria_ref,
        )
        progress_ledger = make_progress_ledger(
            progress_id=f"progress:{ledger_id}",
            task_ledger_id=ledger_id,
            stall_threshold=policy.stall_threshold,
            step_budget=policy.step_budget,
            token_budget=policy.token_budget,
            wall_budget_ms=policy.wall_budget_ms,
            max_replan_count=policy.max_replan_count,
        )

        run_start_ms = int(time.monotonic() * 1000)
        replan_count = 0

        while True:
            # ------------------------------------------------------------------
            # Budget gate — check before selecting next step.
            # ------------------------------------------------------------------
            elapsed_ms = int(time.monotonic() * 1000) - run_start_ms
            total_wall = progress_ledger.total_wall_ms + elapsed_ms

            stall = detect_stall(
                consecutive_stalled_steps=progress_ledger.consecutive_stalled_steps,
                stall_threshold=policy.stall_threshold,
                total_steps_taken=progress_ledger.total_steps_taken,
                step_budget=policy.step_budget,
                total_tokens_used=progress_ledger.total_tokens_used,
                token_budget=policy.token_budget,
                total_wall_ms=total_wall,
                wall_budget_ms=policy.wall_budget_ms,
                replan_count=replan_count,
                max_replan_count=policy.max_replan_count,
            )

            if stall.kind != StallKind.ok:
                return self._handle_stall(
                    stall, task_ledger, progress_ledger, replan_count
                )

            # ------------------------------------------------------------------
            # Plan done?
            # ------------------------------------------------------------------
            if _all_plan_steps_done(task_ledger) and task_ledger.plan:
                return LedgerOrchestratorResult(
                    final_answer=_assemble_answer(task_ledger),
                    task_ledger=task_ledger,
                    progress_ledger=progress_ledger,
                    termination_reason="plan_exhausted",
                )

            # ------------------------------------------------------------------
            # Select next step.
            # ------------------------------------------------------------------
            next_step = _select_next_step(task_ledger, progress_ledger)
            if next_step is None:
                # No runnable step — stall.
                entry = _make_stall_entry(
                    step_id=f"no-runnable-step:{progress_ledger.total_steps_taken}",
                    progress=progress_ledger,
                )
                progress_ledger = update_progress_ledger(progress_ledger, entry)
                continue

            # Mark step in_progress (if it's in the plan — implicit steps are not).
            is_plan_step = any(s.step_id == next_step.step_id for s in task_ledger.plan)
            if is_plan_step:
                task_ledger = _mark_step_status(task_ledger, next_step.step_id, "in_progress")

            # ------------------------------------------------------------------
            # Execute step.
            # ------------------------------------------------------------------
            step_start = int(time.monotonic() * 1000)
            result = self._step_executor(next_step, task_ledger, progress_ledger)
            step_wall_ms = int(time.monotonic() * 1000) - step_start

            # ------------------------------------------------------------------
            # Apply facts.
            # ------------------------------------------------------------------
            task_ledger = _apply_step_result_to_ledger(task_ledger, result)

            # Mark plan step completed.
            if is_plan_step:
                task_ledger = _mark_step_status(task_ledger, next_step.step_id, "completed")

            # ------------------------------------------------------------------
            # Derive verdict + update progress.
            # ------------------------------------------------------------------
            verdict = derive_step_verdict(
                tuple(f.fact_id for f in result.facts_added),
                tuple(f.fact_id for f in result.facts_upgraded),
                result.facts_contradicted,
                tokens_used=result.tokens_used,
                per_step_token_budget=policy.per_step_token_budget,
            )
            entry = make_progress_ledger_entry(
                entry_id=f"entry:{next_step.step_id}:{progress_ledger.total_steps_taken}",
                step_id=next_step.step_id,
                step_verdict=verdict,
                facts_added=tuple(f.fact_id for f in result.facts_added),
                facts_upgraded=tuple(f.fact_id for f in result.facts_upgraded),
                facts_contradicted=result.facts_contradicted,
                tokens_used=result.tokens_used,
                wall_ms=step_wall_ms,
            )
            progress_ledger = update_progress_ledger(progress_ledger, entry)

    def _handle_stall(
        self,
        stall: StallVerdict,
        task_ledger: TaskLedgerContract,
        progress_ledger: ProgressLedgerContract,
        replan_count: int,
    ) -> LedgerOrchestratorResult:
        """Handle stall: attempt re-plan or terminate gracefully."""
        policy = self._config.budget_policy

        # Can we re-plan?
        can_replan = (
            stall.kind == StallKind.stall_threshold_exceeded
            and replan_count < policy.max_replan_count
        )

        if can_replan:
            new_steps = self._replan_callback(task_ledger, progress_ledger, stall)
            if new_steps:
                # Splice new steps into the plan.
                remaining = tuple(s for s in task_ledger.plan if s.status == "pending")
                # Replace remaining pending steps with the new plan.
                done_steps = tuple(s for s in task_ledger.plan if s.status != "pending")
                task_ledger = update_task_ledger(task_ledger, plan=(*done_steps, *new_steps))
                progress_ledger = update_progress_ledger(
                    progress_ledger,
                    _make_stall_entry(
                        step_id=f"replan:{replan_count + 1}",
                        progress=progress_ledger,
                    ),
                    replan_count=replan_count + 1,
                )
                # Re-enter the loop via recursion (bounded by max_replan_count).
                return self.run(
                    ledger_id=task_ledger.ledger_id,
                    objective_text="",  # objective_digest already set — text unused
                    initial_facts=task_ledger.facts,
                    initial_plan=task_ledger.plan,
                    acceptance_criteria_ref=task_ledger.acceptance_criteria_ref,
                )

        # Graceful termination.
        termination_reason = _stall_kind_to_termination_reason(stall.kind)
        return LedgerOrchestratorResult(
            final_answer=_assemble_answer(task_ledger),
            task_ledger=task_ledger,
            progress_ledger=progress_ledger,
            stall_verdict=stall,
            termination_reason=termination_reason,
        )


def _make_stall_entry(
    step_id: str,
    progress: ProgressLedgerContract,
) -> ProgressLedgerEntry:
    return make_progress_ledger_entry(
        entry_id=f"entry:{step_id}:{progress.total_steps_taken}",
        step_id=step_id,
        step_verdict=ProgressStepVerdict.stalled,
    )


def _stall_kind_to_termination_reason(
    kind: StallKind,
) -> Literal[
    "acceptance_criteria_satisfied",
    "plan_exhausted",
    "stall_threshold_exceeded",
    "step_budget_exhausted",
    "token_budget_exhausted",
    "wall_budget_exhausted",
    "replan_count_exhausted",
]:
    _MAP = {
        StallKind.stall_threshold_exceeded: "stall_threshold_exceeded",
        StallKind.step_budget_exhausted: "step_budget_exhausted",
        StallKind.token_budget_exhausted: "token_budget_exhausted",
        StallKind.wall_budget_exhausted: "wall_budget_exhausted",
        StallKind.replan_count_exhausted: "replan_count_exhausted",
    }
    return _MAP.get(kind, "plan_exhausted")  # type: ignore[return-value]


def _noop_replan(
    task_ledger: TaskLedgerContract,
    progress_ledger: ProgressLedgerContract,
    stall_verdict: StallVerdict,
) -> tuple[LedgerPlanStep, ...]:
    """Default no-op re-plan callback — returns empty tuple (terminates)."""
    return ()


# ---------------------------------------------------------------------------
# GAIA harness integration helper
# ---------------------------------------------------------------------------

def run_with_ledger_orchestrator(
    *,
    ledger_id: str,
    objective_text: str,
    step_executor: StepExecutor,
    level: int = 2,
    replan_callback: ReplanCallback | None = None,
    initial_facts: tuple[LedgerFact, ...] = (),
    initial_plan: tuple[LedgerPlanStep, ...] = (),
) -> LedgerOrchestratorResult | None:
    """Convenience wrapper for the GAIA harness.

    Returns ``None`` when ``MAGI_LEDGER_ORCHESTRATOR_ENABLED`` is False so the
    caller can fall back to the flat loop.

    Parameters
    ----------
    ledger_id:
        Stable public identifier for this orchestration run.
    objective_text:
        The task objective text.
    step_executor:
        The injectable step executor.
    level:
        GAIA question level (1, 2, or 3) — determines budget policy defaults.
    replan_callback:
        Optional re-plan callback.
    initial_facts:
        Optional initial facts.
    initial_plan:
        Optional initial plan.
    """
    if not _ledger_orchestrator_enabled():
        return None
    config = LedgerOrchestratorConfig(budget_policy=default_gaia_policy(level))
    orchestrator = LedgerOrchestrator(
        config,
        step_executor=step_executor,
        replan_callback=replan_callback,
    )
    return orchestrator.run(
        ledger_id=ledger_id,
        objective_text=objective_text,
        initial_facts=initial_facts,
        initial_plan=initial_plan,
    )


__all__ = [
    "LedgerOrchestrator",
    "LedgerOrchestratorConfig",
    "LedgerOrchestratorResult",
    "ReplanCallback",
    "StepExecutor",
    "StepResult",
    "run_with_ledger_orchestrator",
]
