"""Bounded workflow-executor — PR1 (skeleton) + PR3 (resumability).

Architecture:
    recipe/contract ──validate_compiled_workflow()  ← MUST pass
            ▼
      harness/workflow_executor  ── asyncio.Semaphore(≤16) per CHILD
            ├─ PR3 cache lookup BEFORE dispatch (skip if already accepted)
            ├─ dispatch children via per-child semaphore-gated fan-out
            │      └─ each child: LocalChildRunnerBoundary (LOCAL-FAKE only in PR1)
            ├─ PR3 cache store AFTER accepted completion
            └─ emit WorkflowRun evidence stub + event_projection progress

Concurrency model:
    The executor fans out one coroutine PER CHILD TASK and gates each
    individually under ``async with semaphore`` so that the number of
    *in-flight children* is bounded to ≤ cap at all times.  The cap is
    derived from ``ParallelToolPolicyDecision.tool_class_limit.max_concurrent``
    (when supplied) and hard-clamped to ≤ 16 (PR1 ceiling).

Env gate: ``MAGI_WORKFLOW_EXECUTOR_ENABLED`` (default OFF).
When off the executor returns ``status="disabled"`` without any dispatch,
preserving byte-identical dry-run / local-fake behaviour.

PR3 — Resumability (within-run / within-session cache):
    An optional ``WorkflowResultCache`` may be passed to ``execute_workflow``.
    When supplied:
    - Before each child dispatch: if the cache holds an accepted result for
      that task_id, the child is served from cache (no re-dispatch).
    - After each child completes with status="accepted": the result is stored
      in the cache for future resume calls within the same run/session.
    - Cache-hit and cache-store events are emitted via
      ``meta_orchestration.event_projection`` for Work Console observability.
    When ``result_cache=None`` (default): no cache logic runs; the executor
    is byte-identical to PR1/PR2.

No ``Literal[False]`` authority flags are flipped here — all child execution
remains in local-fake mode.  Real ADK child execution is deferred to PR2.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.gates.workflow_executor_readiness import WorkflowExecutionMode
from magi_agent.harness.cross_review import CrossReviewStep
from magi_agent.harness.parallel_execution import ParallelToolPolicyDecision
from magi_agent.harness.workflow_result_cache import (
    CachedChildResult,
    WorkflowResultCache,
)
from magi_agent.runtime.child_runner_boundary import MAX_TOTAL_AGENTS_PER_RUN
from magi_agent.runtime.public_events import runtime_trace_event as _trace_event
from magi_agent.recipes.research_child_runner import (
    ResearchChildRunnerConfig,
    ResearchChildRunnerRecipe,
    ResearchChildRunnerResult,
    ResearchChildTaskSpec,
    ResearchSynthesisRequest,
)
from magi_agent.workflows.compiler import (
    CompiledWorkflowContract,
    validate_compiled_workflow,
    WorkflowValidationVerdict,
)
from magi_agent.workflows.dry_run import (
    WorkflowDryRunReport,
    dry_run_governed_workflow,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: PR1 hard cap on concurrency — ADK will lift this ceiling in PR2.
_PR1_MAX_CONCURRENT: int = 16

#: Env variable that enables live dispatch (default OFF).
_EXECUTOR_ENV_VAR: str = "MAGI_WORKFLOW_EXECUTOR_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _executor_enabled() -> bool:
    """Return True only when the env gate is explicitly set to a truthy value."""
    return os.environ.get(_EXECUTOR_ENV_VAR, "").lower() in _TRUE_STRINGS


# ---------------------------------------------------------------------------
# PR6 — best-effort telemetry helpers (never crash a run)
# ---------------------------------------------------------------------------

def _bump(
    telemetry: "WorkflowExecutorTelemetry | None",
    field: str,
    *,
    by: int = 1,
) -> None:
    """Increment a telemetry counter; best-effort, never raises."""
    if telemetry is None:
        return
    try:
        setattr(telemetry, field, getattr(telemetry, field) + by)
    except Exception:
        pass


def _record_high_water(
    telemetry: "WorkflowExecutorTelemetry | None",
    observed: int,
) -> None:
    """Record the run's peak in-flight concurrency if higher than seen so far."""
    if telemetry is None:
        return
    try:
        if observed > telemetry.concurrency_high_water:
            telemetry.concurrency_high_water = observed
    except Exception:
        pass


def _snapshot(
    telemetry: "WorkflowExecutorTelemetry | None",
) -> dict[str, int] | None:
    if telemetry is None:
        return None
    try:
        return telemetry.snapshot()
    except Exception:
        return None


def _emit_telemetry_event(
    event_sink: Callable[[dict[str, object]], None] | None,
    telemetry: "WorkflowExecutorTelemetry | None",
    evidence_ref: str,
    contract: CompiledWorkflowContract,
    mode: str,
) -> None:
    """Emit a single observability trace carrying the counter snapshot.

    Best-effort: a missing sink, missing telemetry, or a throwing sink never
    crashes the run.
    """
    if event_sink is None or telemetry is None:
        return
    try:
        snap = telemetry.snapshot()
        event = _trace_event(
            turn_id=evidence_ref,
            phase="verifier_blocked",
            severity="info",
            title="Workflow executor telemetry",
            detail=(
                f"workflow_telemetry mode={mode} "
                f"runs={snap['runs']} agentsSpawned={snap['agentsSpawned']} "
                f"concurrencyHighWater={snap['concurrencyHighWater']} "
                f"filteredClaims={snap['filteredClaims']} "
                f"workflow_id={contract.workflow_id}"
            ),
        )
        event_sink(event)
    except Exception:
        pass  # telemetry is best-effort — never crash the run


def _bounded_semaphore_cap(requested: int) -> int:
    """Clamp *requested* concurrency to [1, _PR1_MAX_CONCURRENT]."""
    if not isinstance(requested, int) or isinstance(requested, bool):
        return _PR1_MAX_CONCURRENT
    return max(1, min(requested, _PR1_MAX_CONCURRENT))


def _cap_from_policy(
    policy: ParallelToolPolicyDecision | None,
    *,
    config_max_concurrent: int = _PR1_MAX_CONCURRENT,
) -> int:
    """Derive the semaphore cap from a ``ParallelToolPolicyDecision``.

    Resolution order:
    1. If *policy* is supplied, read ``policy.tool_class_limit.max_concurrent``.
    2. Clamp to ``min(policy_value, config_max_concurrent, _PR1_MAX_CONCURRENT)``.
    3. Enforce lower bound of 1.

    When *policy* is ``None``, fall back to *config_max_concurrent* clamped at
    ``_PR1_MAX_CONCURRENT``.
    """
    if policy is not None:
        decision_cap = policy.tool_class_limit.max_concurrent
        raw = min(decision_cap, config_max_concurrent)
    else:
        raw = config_max_concurrent
    return max(1, min(raw, _PR1_MAX_CONCURRENT))


def _workflow_run_evidence_ref(contract: CompiledWorkflowContract) -> str:
    """Deterministic stub ref for the WorkflowRun evidence record."""
    seed = (
        f"workflow-run:"
        f"{contract.workflow_id}:"
        f"{contract.version}:"
        f"{contract.effective_policy_snapshot_digest}"
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"evidence:{digest}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class WorkflowExecutorConfig(BaseModel):
    """Minimal configuration for the PR1 executor skeleton."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    enabled: bool = False
    local_fake_child_runner_enabled: bool = Field(
        default=False,
        alias="localFakeChildRunnerEnabled",
    )
    max_concurrent: int = Field(
        default=_PR1_MAX_CONCURRENT,
        alias="maxConcurrent",
        ge=1,
        le=_PR1_MAX_CONCURRENT,
    )
    #: When provided, the semaphore cap is derived from this decision's
    #: ``tool_class_limit.max_concurrent`` (clamped at _PR1_MAX_CONCURRENT=16).
    parallel_policy: ParallelToolPolicyDecision | None = Field(
        default=None,
        alias="parallelPolicy",
    )
    #: PR1: real ADK runner is always False
    adk_runner_attached: Literal[False] = Field(
        default=False,
        alias="adkRunnerAttached",
    )
    #: PR1: production child execution is always False
    production_child_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionChildExecutionEnabled",
    )
    #: Opt-in local real-child execution pack.  Default remains OFF; callers
    #: must also supply an ``adk_turn_boundary`` to ``execute_workflow``.
    real_child_execution_pack_enabled: bool = Field(
        default=False,
        alias="realChildExecutionPackEnabled",
    )
    #: Run-level spawn budget.  The semaphore bounds concurrent children; this
    #: separately bounds the total number of child agents spawned in a parent run.
    max_total_agents_per_run: int = Field(
        default=MAX_TOTAL_AGENTS_PER_RUN,
        alias="maxTotalAgentsPerRun",
        ge=1,
        le=MAX_TOTAL_AGENTS_PER_RUN,
    )
    #: Number of child agents already spawned earlier in this parent run.
    agents_spawned_so_far: int = Field(
        default=0,
        alias="agentsSpawnedSoFar",
        ge=0,
        le=MAX_TOTAL_AGENTS_PER_RUN,
    )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

WorkflowExecutorStatus = Literal[
    "disabled",
    "shadow",
    "validation_failed",
    "accepted",
    "partial",
    "blocked",
    "error",
]

class WorkflowExecutorTelemetry:
    """Mutable best-effort ops counters for a workflow-executor run.

    Counters (per the PR6 ops contract):
    - ``runs`` — number of shadow+live ``execute_workflow`` invocations (gate-passed,
      non-disabled).  Incremented AFTER the disabled early-return so disabled
      invocations are NOT counted.  NOT incremented on ``validation_failed``.
    - ``agents_spawned`` — number of child agents actually dispatched (live only;
      stays 0 in shadow/disabled).
    - ``concurrency_high_water`` — peak number of in-flight children observed.
    - ``filtered_claims`` — claims removed by the cross-review filter.

    Counter mutation NEVER crashes a run: callers mutate fields directly and the
    executor only reads/writes plain ints.  A single instance may be reused
    across runs to accumulate fleet-level totals.
    """

    __slots__ = ("runs", "agents_spawned", "concurrency_high_water", "filtered_claims")

    def __init__(self) -> None:
        self.runs: int = 0
        self.agents_spawned: int = 0
        self.concurrency_high_water: int = 0
        self.filtered_claims: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "runs": self.runs,
            "agentsSpawned": self.agents_spawned,
            "concurrencyHighWater": self.concurrency_high_water,
            "filteredClaims": self.filtered_claims,
        }


class WorkflowExecutorResult(BaseModel):
    """Return value from execute_workflow()."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    status: WorkflowExecutorStatus
    workflow_id: str = Field(alias="workflowId")
    version: str
    validation_reason_codes: tuple[str, ...] = Field(
        default=(),
        alias="validationReasonCodes",
    )
    child_tasks_dispatched: int = Field(default=0, alias="childTasksDispatched")
    dry_run_report: WorkflowDryRunReport | None = Field(
        default=None,
        alias="dryRunReport",
    )
    workflow_run_evidence_ref: str | None = Field(
        default=None,
        alias="workflowRunEvidenceRef",
    )
    #: PR4 — adversarial cross-review outcome (empty unless a cross_review step
    #: ran on the live executor path).  Filtered claims are genuinely removed
    #: from the surviving set, not merely tagged.
    cross_review_surviving_claim_refs: tuple[str, ...] = Field(
        default=(),
        alias="crossReviewSurvivingClaimRefs",
    )
    cross_review_filtered_claim_refs: tuple[str, ...] = Field(
        default=(),
        alias="crossReviewFilteredClaimRefs",
    )
    #: PR4 — critic escalation derived from the cross-review outcome.  When peers
    #: could not corroborate part of the claim set, the cross-review records an
    #: eligible critic escalation (anchored to the verifier_bus llm_critic stage
    #: admitted by ``effective_verifiers(escalationReason=...)``); the escalated
    #: verifier ids are surfaced here so downstream final assembly sees that the
    #: semantic critic was admitted.  Empty unless a cross_review step ran.
    cross_review_escalation_eligible: bool = Field(
        default=False,
        alias="crossReviewEscalationEligible",
    )
    cross_review_escalation_reason: str | None = Field(
        default=None,
        alias="crossReviewEscalationReason",
    )
    cross_review_escalated_verifier_ids: tuple[str, ...] = Field(
        default=(),
        alias="crossReviewEscalatedVerifierIds",
    )
    #: PR6 — execution mode this run resolved to (disabled/shadow/live).
    execution_mode: WorkflowExecutionMode = Field(
        default="disabled",
        alias="executionMode",
    )
    #: PR6 — best-effort ops counter snapshot for this run (runs / agentsSpawned
    #: / concurrencyHighWater / filteredClaims).  ``None`` when no telemetry
    #: instance was supplied — byte-identical to PR1-PR5.
    telemetry_snapshot: dict[str, int] | None = Field(
        default=None,
        alias="telemetrySnapshot",
    )
    #: Authority flags — all False in PR1
    adk_runner_attached: Literal[False] = Field(
        default=False,
        alias="adkRunnerAttached",
    )
    real_child_runner_executed: Literal[False] = Field(
        default=False,
        alias="realChildRunnerExecuted",
    )
    production_authority: Literal[False] = Field(
        default=False,
        alias="productionAuthority",
    )


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

async def execute_workflow(
    contract: CompiledWorkflowContract,
    *,
    config: WorkflowExecutorConfig | None = None,
    child_runner: object | None = None,
    adk_turn_boundary: object | None = None,
    result_cache: WorkflowResultCache | None = None,
    event_sink: Callable[[dict[str, object]], None] | None = None,
    cross_review_step: CrossReviewStep | None = None,
    execution_mode: WorkflowExecutionMode | None = None,
    telemetry: "WorkflowExecutorTelemetry | None" = None,
) -> WorkflowExecutorResult:
    """Execute *contract* according to *config*.

    Governance gate:
    1. Always performs a dry-run pass via ``dry_run_governed_workflow``.
    2. If validation fails, returns ``status="validation_failed"`` with no
       child dispatches.
    3. If ``MAGI_WORKFLOW_EXECUTOR_ENABLED`` is falsy, returns
       ``status="disabled"`` with no child dispatches.
    4. Otherwise fans out children via ``ResearchChildRunnerRecipe`` under an
       ``asyncio.Semaphore`` bounded to ≤ 16 (PR1 cap).

    Context isolation: children return only sanitised refs through the
    ``LocalChildRunnerBoundary`` (local-fake in PR1).  No raw transcripts
    escape into the parent context.

    PR3 — Resumability:
    When *result_cache* is supplied, child tasks with an ``"accepted"`` result
    already in the cache are served immediately without re-dispatch.  After a
    child completes with ``status="accepted"``, its result is stored in the
    cache.  Cache-hit and cache-store observability events are built via
    ``_trace_event`` and, when *event_sink* is supplied, forwarded to the sink
    as plain dicts — enabling testable, in-process observability with no file
    I/O, SQLite, or Redis.  The cache is within-run/within-session only — no
    durable storage is created.  When *result_cache* is ``None`` the function
    is byte-identical to the PR1/PR2 behaviour.

    Args:
        contract: Compiled and validated workflow contract to execute.
        config: Executor configuration (concurrency cap, feature flags).
            Defaults to ``WorkflowExecutorConfig()`` when ``None``.
        child_runner: Optional local-fake child runner (PR1/PR2 only).
        result_cache: Optional within-run result cache (PR3 resumability).
            When ``None`` no cache logic runs.
        event_sink: Optional callable that receives each observability event
            dict emitted during cache-hit and cache-store operations.  When
            ``None`` (default) events are built but not forwarded — the
            function is byte-identical to the no-sink behaviour.
    """
    if config is None:
        config = WorkflowExecutorConfig()

    # --- Step 1: governance gate (dry-run + validation) ---
    dry_run_report = dry_run_governed_workflow(contract)
    verdict: WorkflowValidationVerdict = validate_compiled_workflow(contract)

    if not verdict.ok:
        return WorkflowExecutorResult(
            status="validation_failed",
            workflowId=contract.workflow_id,
            version=contract.version,
            validationReasonCodes=verdict.reason_codes,
            childTasksDispatched=0,
            dryRunReport=dry_run_report,
            workflowRunEvidenceRef=None,
        )

    # --- Step 2: PR6 rollout-mode gate ---
    # The execution mode comes from the gate readiness decision when supplied
    # explicitly; otherwise it falls back to the env gate for byte-identical
    # PR1-PR5 behaviour (env off → disabled, env on → live).  ``shadow`` is a
    # DISTINCT mode: it runs validate + dry-run on real traffic (already done
    # above) but dispatches ZERO children.
    resolved_mode: WorkflowExecutionMode
    if execution_mode is not None:
        resolved_mode = execution_mode
    else:
        resolved_mode = "live" if _executor_enabled() else "disabled"

    if resolved_mode == "disabled":
        return WorkflowExecutorResult(
            status="disabled",
            workflowId=contract.workflow_id,
            version=contract.version,
            validationReasonCodes=(),
            childTasksDispatched=0,
            dryRunReport=dry_run_report,
            workflowRunEvidenceRef=None,
            executionMode="disabled",
            telemetrySnapshot=_snapshot(telemetry),
        )

    # PR6: count the run only after the disabled early-return — "runs" means
    # "executor reached shadow/live (did/attempted work)".  Disabled invocations
    # do NOT increment this counter.  Best-effort — never crash the run.
    _bump(telemetry, "runs")

    if resolved_mode == "shadow":
        # Shadow performed validate + dry-run on real traffic above, but
        # dispatches NO children.  Distinct status from "disabled".
        evidence_ref = _workflow_run_evidence_ref(contract)
        _emit_telemetry_event(event_sink, telemetry, evidence_ref, contract, "shadow")
        return WorkflowExecutorResult(
            status="shadow",
            workflowId=contract.workflow_id,
            version=contract.version,
            validationReasonCodes=(),
            childTasksDispatched=0,
            dryRunReport=dry_run_report,
            workflowRunEvidenceRef=evidence_ref,
            executionMode="shadow",
            telemetrySnapshot=_snapshot(telemetry),
        )

    # --- Step 3: build child tasks from contract's selected recipes ---
    # PR3: compute stable cache keys BEFORE creating ResearchChildTaskSpec objects,
    # because the task_id field passes through a sanitizer that can collapse
    # distinct task identifiers into the same string (e.g. any recipe whose
    # safe-recipe-id contains the substring "sk-<6+chars>" gets redacted by the
    # _PRIVATE_TEXT_RE pattern in ResearchChildRunnerRecipe validators).
    # We key the cache on a sha1 digest of (workflow_id, version, recipe_index,
    # raw_recipe_id) which is stable, unique, and entirely in the executor's
    # control — never touched by the recipe sanitizers.
    recipe_cache_keys: dict[int, str] = {
        index: _child_cache_key(contract, index, recipe_id)
        for index, recipe_id in enumerate(contract.selected_recipes[:8])
    }
    tasks = _tasks_from_contract(contract)
    evidence_ref = _workflow_run_evidence_ref(contract)

    if not tasks:
        # No child tasks to dispatch — return accepted with zero dispatches.
        _emit_telemetry_event(event_sink, telemetry, evidence_ref, contract, "live")
        return WorkflowExecutorResult(
            status="accepted",
            workflowId=contract.workflow_id,
            version=contract.version,
            validationReasonCodes=(),
            childTasksDispatched=0,
            dryRunReport=dry_run_report,
            workflowRunEvidenceRef=evidence_ref,
            executionMode="live",
            telemetrySnapshot=_snapshot(telemetry),
        )

    # --- Step 4: bounded fan-out (per-child semaphore) ---
    # Cap is derived from the ParallelToolPolicyDecision when supplied,
    # otherwise from config.max_concurrent — always clamped to ≤ 16 (PR1 hard limit).
    cap = _cap_from_policy(config.parallel_policy, config_max_concurrent=config.max_concurrent)
    semaphore = asyncio.Semaphore(cap)

    # PR6 concurrency high-water tracking.  ``asyncio.gather`` runs all child
    # coroutines on a single event loop with no preemption between awaits, so a
    # plain int counter is race-free here (no lock required).
    in_flight: int = 0
    high_water: int = 0

    # Each child task gets its OWN single-task synthesis request so the
    # semaphore can gate them individually (true per-child bounding).
    recipe_config = ResearchChildRunnerConfig(
        enabled=config.enabled and config.local_fake_child_runner_enabled,
        localFakeChildRunnerEnabled=config.local_fake_child_runner_enabled,
        realChildExecutionPackEnabled=config.real_child_execution_pack_enabled,
        maxChildTasks=1,  # one task per dispatch call → per-child semaphore works correctly
    )
    recipe = ResearchChildRunnerRecipe(
        recipe_config,
        child_runner=child_runner,
        adk_turn_boundary=adk_turn_boundary,
    )

    parent_execution_id = _execution_id(contract)
    turn_id = _turn_id(contract)
    synthesis_id = _synthesis_id(contract)
    parent_source_refs = ("source:ledger:workflow-executor-pr1",)
    parent_claim_refs = ("claim:workflow:pr1-execution",)

    # --- PR3: split tasks into cached (already accepted) and to-dispatch ------
    # Children that are already in the cache (status="accepted") are served
    # immediately without semaphore contention or a child runner call.
    # Children NOT in the cache proceed through the normal dispatch path.
    #
    # We track:
    #   cache_hit_count  — tasks served directly from cache (contributes
    #                      "accepted" to the aggregation below).
    #   dispatched_results — list[status_str] populated during the fan-out.
    #
    # Using per-task status strings in the aggregation (rather than trying
    # to construct a full ResearchChildRunnerResult for cache hits) keeps the
    # code simple and avoids touching the complex ResearchChildRunnerResult
    # constructor with its authority-flags and synthesis-input requirements.

    # --- PR3 cache-hit pass: count tasks already accepted in a prior partial run.
    # Uses recipe_cache_keys (stable, sanitizer-safe) as the lookup key.
    cache_hit_count: int = 0
    if result_cache is not None:
        for index in range(len(tasks)):
            cache_key = recipe_cache_keys[index]
            cached = result_cache.get(cache_key)
            if cached is not None and cached.status == "accepted":
                cache_hit_count += 1
                # Build and emit the observability trace event.
                # verifier_blocked is the valid generic trace phase; severity=info
                # + detail carry the cache-hit semantic meaning for the Work Console.
                hit_event = _trace_event(
                    turn_id=evidence_ref,
                    phase="verifier_blocked",
                    severity="info",
                    title="Workflow child served from cache",
                    detail=(
                        f"workflow_resume cache_hit index={index} "
                        f"workflow_id={contract.workflow_id}"
                    ),
                )
                if event_sink is not None:
                    try:
                        event_sink(hit_event)
                    except Exception:
                        pass  # observability is best-effort — never crash the run

    # Tasks that still need dispatch (not in cache as accepted).
    # Uses recipe_cache_keys to look up by stable index-based key.
    pending_indexed_tasks: tuple[tuple[int, ResearchChildTaskSpec], ...] = tuple(
        (index, task)
        for index, task in enumerate(tasks)
        if result_cache is None
        or (cached_entry := result_cache.get(recipe_cache_keys[index])) is None
        or cached_entry.status != "accepted"
    )

    if (
        pending_indexed_tasks
        and config.agents_spawned_so_far + len(pending_indexed_tasks)
        > config.max_total_agents_per_run
    ):
        _emit_telemetry_event(event_sink, telemetry, evidence_ref, contract, "live")
        return WorkflowExecutorResult(
            status="blocked",
            workflowId=contract.workflow_id,
            version=contract.version,
            validationReasonCodes=("total_agents_per_run_exceeded",),
            childTasksDispatched=0,
            dryRunReport=dry_run_report,
            workflowRunEvidenceRef=evidence_ref,
            executionMode="live",
            telemetrySnapshot=_snapshot(telemetry),
        )

    # Dispatch each pending child task under the semaphore.
    async def _dispatch_child(
        index: int, task: ResearchChildTaskSpec
    ) -> ResearchChildRunnerResult:
        """Run a single pending child task under the concurrency semaphore.

        After a successful dispatch with status="accepted", stores the result
        in the cache and emits an observability trace event.  When *event_sink*
        is provided the emitted event dict is forwarded to the sink so callers
        can assert that cache-store events actually flow.
        """
        single_task_request = ResearchSynthesisRequest(
            parentExecutionId=parent_execution_id,
            turnId=turn_id,
            synthesisId=synthesis_id,
            objective=f"Execute recipe child {index} for {contract.workflow_id}",
            parentSourceRefs=parent_source_refs,
            parentClaimRefs=parent_claim_refs,
            tasks=(task,),
        )
        nonlocal in_flight, high_water
        async with semaphore:
            # PR6: a child is now actually dispatched — count it and track the
            # peak in-flight concurrency.  Best-effort; never crash the run.
            _bump(telemetry, "agents_spawned")
            in_flight += 1
            if in_flight > high_water:
                high_water = in_flight
            try:
                child_result = await recipe.run(single_task_request)
            finally:
                in_flight -= 1

        # --- PR3 cache store (after accepted completion) -----------------------
        if result_cache is not None and child_result.status == "accepted":
            cache_key = recipe_cache_keys[index]
            result_cache.store(
                cache_key,
                CachedChildResult(task_id=cache_key, status="accepted"),
            )
            # Build and emit the observability trace event.
            store_event = _trace_event(
                turn_id=evidence_ref,
                phase="verifier_blocked",
                severity="info",
                title="Workflow child result cached",
                detail=(
                    f"workflow_resume cache_store index={index} "
                    f"workflow_id={contract.workflow_id}"
                ),
            )
            if event_sink is not None:
                try:
                    event_sink(store_event)
                except Exception:
                    pass  # observability is best-effort — never crash the run

        return child_result

    dispatched_results = await asyncio.gather(
        *(_dispatch_child(index, task) for index, task in pending_indexed_tasks)
    )

    # Aggregate status from per-child results.
    # Cache-hit children count as "accepted" in the aggregation.
    any_accepted = cache_hit_count > 0 or any(r.status == "accepted" for r in dispatched_results)
    any_error = any(r.status == "error" for r in dispatched_results)
    any_partial = any(r.status == "partial" for r in dispatched_results)
    any_blocked = any(r.status == "blocked" for r in dispatched_results)
    all_disabled = (cache_hit_count == 0) and all(r.status == "disabled" for r in dispatched_results)

    # Count children that were actually dispatched (non-disabled) in this run.
    # Cache-hit children are NOT counted here — they were never dispatched.
    dispatched = sum(1 for r in dispatched_results if r.status != "disabled")

    executor_status: WorkflowExecutorStatus
    if all_disabled:
        executor_status = "disabled"
    elif any_error:
        # Mix of errors and successes → partial; pure errors → error.
        executor_status = "partial" if any_accepted else "error"
    elif any_partial:
        # A child already reported partial (error-adjacent): treat the
        # aggregate as partial regardless of other accepted/blocked children.
        executor_status = "partial"
    elif any_accepted and any_blocked:
        executor_status = "partial"
    elif any_blocked:
        executor_status = "blocked"
    elif any_accepted:
        executor_status = "accepted"
    else:
        executor_status = "blocked"

    # --- PR4: adversarial cross-review step (live path only) ---------------
    # When a cross_review step is supplied, independent peers review each
    # other's claims; claims without cross-support are FILTERED via the
    # existing verifier_bus source_claim_link verifier (genuinely removed from
    # the surviving set, not tagged).  The review outcome is recorded as an
    # evidence event through the same event_sink.  When no step is supplied the
    # cross-review fields stay empty — byte-identical to PR3.
    cross_review_surviving: tuple[str, ...] = ()
    cross_review_filtered: tuple[str, ...] = ()
    cross_review_escalation_eligible = False
    cross_review_escalation_reason: str | None = None
    cross_review_escalated_verifier_ids: tuple[str, ...] = ()
    if cross_review_step is not None:
        review = cross_review_step.run()
        cross_review_surviving = review.surviving_claim_refs
        cross_review_filtered = review.filtered_claim_refs
        # Surface the critic escalation the cross-review derived from its
        # filtered claims (anchored to the verifier_bus llm_critic stage).
        cross_review_escalation_eligible = review.escalation.eligible
        cross_review_escalation_reason = review.escalation.reason
        cross_review_escalated_verifier_ids = review.escalation.escalated_verifier_ids
        # PR6: filtered-claims counter reflects the genuinely removed claims.
        _bump(telemetry, "filtered_claims", by=len(cross_review_filtered))
        if event_sink is not None:
            try:
                event_sink(review.evidence_event())
            except Exception:
                pass  # observability is best-effort — never crash the run

    # PR6: record the observed concurrency high-water mark and emit the
    # telemetry counter event (best-effort).
    _record_high_water(telemetry, high_water)
    _emit_telemetry_event(event_sink, telemetry, evidence_ref, contract, "live")

    return WorkflowExecutorResult(
        status=executor_status,
        workflowId=contract.workflow_id,
        version=contract.version,
        validationReasonCodes=(),
        childTasksDispatched=dispatched,
        dryRunReport=dry_run_report,
        workflowRunEvidenceRef=evidence_ref,
        crossReviewSurvivingClaimRefs=cross_review_surviving,
        crossReviewFilteredClaimRefs=cross_review_filtered,
        crossReviewEscalationEligible=cross_review_escalation_eligible,
        crossReviewEscalationReason=cross_review_escalation_reason,
        crossReviewEscalatedVerifierIds=cross_review_escalated_verifier_ids,
        executionMode="live",
        telemetrySnapshot=_snapshot(telemetry),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tasks_from_contract(
    contract: CompiledWorkflowContract,
) -> tuple[ResearchChildTaskSpec, ...]:
    """Derive stub child tasks from the contract's selected recipes.

    PR1 creates one explore-role task per selected recipe, using the
    contract's evidence requirements as claim refs and available tools
    as source refs anchors.  This is intentionally minimal — PR2 will
    wire real task decomposition.
    """
    tasks: list[ResearchChildTaskSpec] = []
    wall_clock_ms = int(contract.budgets.get("wallClockTimeoutMs", 30_000))
    per_child_ms = max(1000, wall_clock_ms // max(len(contract.selected_recipes), 1))

    source_refs = (
        tuple(f"source:ledger:{t}" for t in contract.available_tools[:4])
        or ("source:ledger:workflow-executor-default",)
    )
    claim_refs = (
        tuple(f"claim:workflow:{e}" for e in contract.evidence_requirements[:4])
        or ("claim:workflow:default-run",)
    )

    for index, recipe_id in enumerate(contract.selected_recipes[:8]):
        safe_recipe_id = recipe_id.replace(".", "-").replace("/", "-")[:40]
        tasks.append(
            ResearchChildTaskSpec(
                taskId=f"wf-task-{index}-{safe_recipe_id}",
                childRole="explore",
                objective=f"Execute recipe {recipe_id} for workflow {contract.workflow_id}",
                sourceRefs=source_refs,
                claimRefs=claim_refs,
                spawnDepth=1,
                budgetMs=per_child_ms,
            )
        )
    return tuple(tasks)


def _execution_id(contract: CompiledWorkflowContract) -> str:
    seed = f"exec:{contract.workflow_id}:{contract.version}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"wf-exec-{digest}"


def _turn_id(contract: CompiledWorkflowContract) -> str:
    seed = f"turn:{contract.workflow_id}:{contract.version}:{time.monotonic_ns()}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"wf-turn-{digest}"


def _synthesis_id(contract: CompiledWorkflowContract) -> str:
    seed = f"synth:{contract.workflow_id}:{contract.version}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"wf-synth-{digest}"


def _child_cache_key(
    contract: CompiledWorkflowContract,
    index: int,
    recipe_id: str,
) -> str:
    """Return a stable, sanitizer-safe cache key for a child task.

    The key is a sha1 digest of (workflow_id, version, recipe_index, recipe_id).
    Using a digest avoids the sanitizer collision that affects task_id strings
    containing substrings matched by the recipe runner's _PRIVATE_TEXT_RE
    (e.g. ``sk-`` followed by 6+ chars).  The digest is deterministic across
    re-runs of the same workflow contract.
    """
    seed = (
        f"cache-key:{contract.workflow_id}:{contract.version}:{index}:{recipe_id}"
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"wf-cache-{digest}"


__all__ = [
    "WorkflowExecutionMode",
    "WorkflowExecutorConfig",
    "WorkflowExecutorResult",
    "WorkflowExecutorStatus",
    "WorkflowExecutorTelemetry",
    "execute_workflow",
]
