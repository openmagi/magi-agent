"""PR3 — Resumability: within-run result cache for the workflow executor.

Tests lock the four mandatory PR3 behaviours:

1. Stop mid-run → resume → completed children NOT re-executed.
   A pre-populated cache representing "children that already finished in a
   prior partial run" makes those children skip dispatch entirely.  Call
   counts on the fake runner prove the already-completed tasks were NOT
   re-dispatched.

2. Partial-failure resume: children that failed or were pending in the prior
   run ARE re-dispatched; already-succeeded children are served from cache.

3. Default-OFF parity: when ``MAGI_WORKFLOW_EXECUTOR_ENABLED`` is off the
   cache has zero effect on the code path — byte-identical to before.

4. Semaphore still bounds: even with some children served from cache, the
   in-flight cap is respected for the children that DO need dispatching.

Cache key: a sha1 digest of ``(workflow_id, version, recipe_index, recipe_id)``
formatted as ``"wf-cache-<hex16>"``, computed by ``_child_cache_key()`` inside
the executor.  This is NOT the ``ResearchChildTaskSpec.task_id`` field (which
can be collapsed by the recipe runner's ``_PRIVATE_TEXT_RE`` sanitizer).
The key is stable across re-runs of the same contract.

Cache scope: within-run / within-session only.  The ``WorkflowResultCache``
is an in-memory dict that callers create and pass to ``execute_workflow``.
No durable storage, no cross-session persistence.

Observability: ``execute_workflow`` builds a ``runtime_trace_event`` dict for
each cache-hit and cache-store.  When an ``event_sink`` callable is passed to
``execute_workflow``, each event dict is forwarded to the sink.  Test 9 passes
a collecting sink and asserts that the correct number of cache-hit and
cache-store events flow through it with the expected key fields.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from magi_agent.workflows.compiler import (
    CompiledWorkflowContract,
    compile_governed_workflow,
    WorkflowCompileInput,
)
from magi_agent.workflows.registry import WorkflowRegistryEntry

_DIGEST = "sha256:" + "a" * 64


# ---------------------------------------------------------------------------
# Contract builder helpers (same pattern as PR1 tests)
# ---------------------------------------------------------------------------

def _registry_entry(
    workflow_id: str = "openmagi.research.cited",
    version: str = "1.0.0",
) -> WorkflowRegistryEntry:
    return WorkflowRegistryEntry(
        workflowId=workflow_id,
        version=version,
        ownerRef="team-digest:research",
        status="active",
        sourceDigest=_DIGEST,
        promotionHistory=("draft:2026-05-01", "staging:2026-05-02", "active:2026-05-03"),
        compatibleRuntimeContractVersion="programmable-determinism.v1",
    )


def _valid_contract(n_recipes: int = 3) -> CompiledWorkflowContract:
    """Build a contract with *n_recipes* selected recipes (1–8)."""
    n_recipes = max(1, min(n_recipes, 8))
    entries = tuple(
        _registry_entry(version=f"1.0.{i}") for i in range(n_recipes)
    )
    recipe_ids = tuple(
        f"openmagi.research.cited.v1.0.{i}" for i in range(n_recipes)
    )
    config = WorkflowCompileInput(
        workflowId="openmagi.research.cited",
        version="1.0.0",
        selectedRecipes=recipe_ids,
        registeredWorkflows=entries,
        toolAllowlist=("SourceLedgerRead", "SearchFiles"),
        toolDenylist=(),
        evidenceRequirements=("SourceInspection",),
        validatorRefs=("deterministic-verifier",),
        projectionPolicy="structured_claims_only",
        repairPolicy="retry-once",
        approvalPolicy="auto",
        contextProjectionPolicy="explicit",
        budgets={"maxIterations": 10, "wallClockTimeoutMs": 60_000},
        hardInvariants={
            "rawDraftStreamingForbidden": True,
            "toolhostOnlyExecution": True,
            "validatorBeforeProjection": True,
        },
        effectivePolicySnapshotDigest=_DIGEST,
        availableTools=("SourceLedgerRead", "SearchFiles"),
        availableValidators=("deterministic-verifier",),
        availableRenderers=("structured_claims_only",),
        evidenceProducers=("SourceInspection",),
        routePrecedence=(),
        noMatchTerminalState="block",
    )
    return compile_governed_workflow(config)


# ---------------------------------------------------------------------------
# Fake child runner that tracks calls by task_id
# ---------------------------------------------------------------------------

_FAKE_EVIDENCE_REF = "evidence:abcdef1234567890"
_FAKE_CHILD_EXEC_ID = "child:1234567890abcdef"


class _TrackingFakeRunner:
    """Fake child runner that records dispatch calls.

    Uses hardcoded evidence/child refs that are valid per _CHILD_OUTPUT_REF_RE
    (``^(?:child|evidence|...):[A-Za-z0-9._:-]+$``), so the recipe does not
    sanitize them into invalid forms.  The task_id field is sanitized by the
    recipe runner's _PRIVATE_TEXT_RE, so we do NOT rely on task_id contents
    for uniqueness — the executor's cache uses stable sha1 keys instead.
    """

    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: int = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        return {
            "childExecutionId": _FAKE_CHILD_EXEC_ID,
            "status": "completed",
            "summary": "fake completed",
            "evidenceRefs": (_FAKE_EVIDENCE_REF,),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


class _SlowTrackingFakeRunner:
    """Fake runner that yields to the event loop so concurrency tests work."""

    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: int = 0
        self._current: int = 0
        self.peak: int = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        self._current += 1
        if self._current > self.peak:
            self.peak = self._current
        await asyncio.sleep(0)
        self._current -= 1
        return {
            "childExecutionId": _FAKE_CHILD_EXEC_ID,
            "status": "completed",
            "summary": "fake completed",
            "evidenceRefs": (_FAKE_EVIDENCE_REF,),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


# ---------------------------------------------------------------------------
# Test 1 — stop mid-run → resume → completed children NOT re-dispatched
# ---------------------------------------------------------------------------

def test_resume_skips_already_completed_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-populate the cache with the first child's result.

    On the (simulated) resume call, only the remaining children should be
    dispatched.  The first child — the one already in the cache — must NOT
    be re-dispatched.

    Cache keys are stable sha1-based keys derived via _child_cache_key()
    (not the sanitized task_id field, which collapses across tasks due to
    recipe-runner _PRIVATE_TEXT_RE sanitization).
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        _child_cache_key,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import (
        WorkflowResultCache,
        CachedChildResult,
    )

    contract = _valid_contract(n_recipes=3)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    # --- First run: simulate it completing only child 0.
    # Use _child_cache_key to derive the stable executor cache key for index=0.
    recipe_id_0 = contract.selected_recipes[0]
    key_0 = _child_cache_key(contract, 0, recipe_id_0)

    # Pre-populate cache with an "accepted" result for child 0.
    cache = WorkflowResultCache()
    cache.store(key_0, CachedChildResult(task_id=key_0, status="accepted"))

    # --- Resume run: children 1 and 2 should be dispatched; child 0 must NOT.
    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner, result_cache=cache)
    )

    # The overall workflow should still succeed.
    assert result.status in {"accepted", "partial"}

    # Children 1 and 2 WERE dispatched (they were not in the cache).
    assert fake_runner.calls == 2, (
        f"Expected exactly 2 dispatches (children 1 and 2), got {fake_runner.calls}."
    )

    # Total children accounted for = 3 (1 from cache + 2 dispatched).
    assert result.child_tasks_dispatched == 2


# ---------------------------------------------------------------------------
# Test 2 — partial-failure resume: failed/pending re-run; succeeded cached
# ---------------------------------------------------------------------------

def test_partial_failure_resume_reruns_failed_not_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache holds an accepted result for child 1 (index=1).

    Children 0 and 2 are NOT in the cache (simulating failure/pending in
    a prior run).  Only children 0 and 2 should be re-dispatched.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        _child_cache_key,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import (
        WorkflowResultCache,
        CachedChildResult,
    )

    contract = _valid_contract(n_recipes=3)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    # Child 1 already succeeded; children 0 and 2 need to re-run.
    recipe_id_1 = contract.selected_recipes[1]
    key_1 = _child_cache_key(contract, 1, recipe_id_1)
    cache = WorkflowResultCache()
    cache.store(key_1, CachedChildResult(task_id=key_1, status="accepted"))

    asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner, result_cache=cache)
    )

    # Children 0 and 2 must be dispatched; child 1 must NOT.
    assert fake_runner.calls == 2, (
        f"Expected exactly 2 re-dispatches (children 0 and 2), got {fake_runner.calls}."
    )


# ---------------------------------------------------------------------------
# Test 3 — default-OFF: no cache effect when executor is disabled
# ---------------------------------------------------------------------------

def test_default_off_cache_has_no_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When MAGI_WORKFLOW_EXECUTOR_ENABLED is off, the executor returns
    status='disabled' regardless of what the cache contains."""
    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import (
        WorkflowResultCache,
        CachedChildResult,
    )

    contract = _valid_contract(n_recipes=2)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)

    # Pre-populate a cache (should have zero effect).
    # Note: the key format "wf-task-..." is intentionally stale/irrelevant here —
    # the executor is disabled so it returns "disabled" before any cache logic runs.
    cache = WorkflowResultCache()
    cache.store(
        "wf-task-0-openmagi-research-cited-v1-0-0",
        CachedChildResult(task_id="wf-task-0-openmagi-research-cited-v1-0-0", status="accepted"),
    )

    from magi_agent.harness.workflow_executor import execute_workflow

    result = asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner, result_cache=cache)
    )

    # Must be disabled — no child dispatch at all.
    assert result.status == "disabled"
    assert fake_runner.calls == 0


# ---------------------------------------------------------------------------
# Test 4 — semaphore still bounds when some children come from cache
# ---------------------------------------------------------------------------

def test_semaphore_bounds_even_with_cached_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With some children served from cache, the semaphore still limits the
    peak concurrency of the remaining dispatched children to ≤ cap.

    Uses 8 child tasks (max in PR1), caches 3, and bounds at cap=2.
    The runner's peak in-flight count must be ≤ 2.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.parallel_execution import (
        ParallelExecutionScope,
        ParallelToolPolicyInput,
        build_parallel_tool_policy_decision,
    )
    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import (
        WorkflowResultCache,
        CachedChildResult,
    )

    contract = _valid_contract(n_recipes=8)
    fake_runner = _SlowTrackingFakeRunner()

    scope = ParallelExecutionScope.model_validate(
        {"runOn": "main", "agentRole": "general", "spawnDepth": 0}
    )
    policy_input = ParallelToolPolicyInput.model_validate(
        {
            "toolName": "workflow-child-dispatch",
            "toolClass": "read_only",
            "sideEffectClass": "read_only",
            "manifestParallelSafetyProof": True,
            "scope": scope,
            "toolClassLimit": {"toolClass": "read_only", "maxConcurrent": 2},
            "turnLimit": {"toolClass": "turn", "maxConcurrent": 2},
        }
    )
    policy = build_parallel_tool_policy_decision(policy_input)
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
        parallel_policy=policy,
    )

    # Pre-cache 3 of the 8 children (indices 0, 3, 6) using stable cache keys.
    from magi_agent.harness.workflow_executor import _child_cache_key as _cck
    cache = WorkflowResultCache()
    for i in (0, 3, 6):
        recipe_id = contract.selected_recipes[i]
        key = _cck(contract, i, recipe_id)
        cache.store(key, CachedChildResult(task_id=key, status="accepted"))

    asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner, result_cache=cache)
    )

    # 5 children were dispatched (8 total − 3 cached).
    assert fake_runner.calls == 5, (
        f"Expected 5 dispatches, got {fake_runner.calls}."
    )

    # Peak concurrency must be ≤ 2 (semaphore cap).
    assert fake_runner.peak <= 2, (
        f"Semaphore was not respected: peak in-flight was {fake_runner.peak} (cap=2)"
    )


# ---------------------------------------------------------------------------
# Test 5 — newly completed children are stored in the cache
# ---------------------------------------------------------------------------

def test_completed_children_are_stored_in_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After execute_workflow completes, the result_cache should contain
    entries for all successfully accepted children (not disabled/blocked/error).

    Cache keys are the stable sha1 keys from _child_cache_key(), not the
    sanitized task_id fields (which collapse across tasks due to
    _PRIVATE_TEXT_RE sanitization in the recipe runner).
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        _child_cache_key,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import WorkflowResultCache

    contract = _valid_contract(n_recipes=3)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )
    cache = WorkflowResultCache()

    asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner, result_cache=cache)
    )

    # All 3 children completed → all 3 should be in the cache.
    assert fake_runner.calls == 3
    assert len(cache) == 3

    # Each stable cache key (by recipe index) should be present.
    for i in range(3):
        recipe_id = contract.selected_recipes[i]
        key = _child_cache_key(contract, i, recipe_id)
        assert cache.get(key) is not None, (
            f"Expected child {i} to be cached (key={key!r}) after completion."
        )


# ---------------------------------------------------------------------------
# Test 6 — no cache passed: execute_workflow behaves like PR1 (no regression)
# ---------------------------------------------------------------------------

def test_no_cache_passed_behaves_like_pr1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When result_cache=None (default), execute_workflow behaves exactly as
    in PR1 — all children are dispatched, no cache effects."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )

    contract = _valid_contract(n_recipes=2)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    result = asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner)
    )

    # All 2 children should be dispatched — no cache to serve from.
    assert fake_runner.calls == 2
    assert result.child_tasks_dispatched == 2


# ---------------------------------------------------------------------------
# Test 7 — WorkflowResultCache unit: get/store/len/immutability
# ---------------------------------------------------------------------------

def test_workflow_result_cache_unit_behaviour() -> None:
    """WorkflowResultCache is a simple dict-backed store.

    - get() returns None for unknown keys.
    - store() writes a CachedChildResult keyed by task_id.
    - len() reflects the number of stored entries.
    - Storing a second entry with the same key overwrites (idempotent).
    """
    from magi_agent.harness.workflow_result_cache import (
        WorkflowResultCache,
        CachedChildResult,
    )

    cache = WorkflowResultCache()
    assert len(cache) == 0
    assert cache.get("task-x") is None

    result_a = CachedChildResult(task_id="task-x", status="accepted")
    cache.store("task-x", result_a)
    assert len(cache) == 1
    assert cache.get("task-x") is not None
    assert cache.get("task-x").status == "accepted"

    # Overwrite with same key — still len 1.
    result_b = CachedChildResult(task_id="task-x", status="accepted")
    cache.store("task-x", result_b)
    assert len(cache) == 1

    # Different key — len 2.
    cache.store("task-y", CachedChildResult(task_id="task-y", status="accepted"))
    assert len(cache) == 2
    assert cache.get("task-y") is not None


# ---------------------------------------------------------------------------
# Test 8 — only accepted results are cached (not error/blocked/disabled)
# ---------------------------------------------------------------------------

def test_only_accepted_results_are_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error/blocked/disabled child results must NOT be stored in the cache.

    A child runner that returns "blocked" status for all tasks must leave
    the cache empty after the workflow run.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import WorkflowResultCache

    class _BlockingRunner:
        openmagi_local_fake_provider = True

        async def run_child(self, request: object) -> dict[str, object]:
            task_id = getattr(request, "task_id", "task-unknown")
            return {
                "childExecutionId": f"child-{task_id}",
                "status": "blocked",
                "summary": "blocked",
                "evidenceRefs": (),
                "artifactRefs": (),
                "auditEventRefs": (),
            }

    contract = _valid_contract(n_recipes=2)
    cache = WorkflowResultCache()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    asyncio.run(
        execute_workflow(
            contract, config=config, child_runner=_BlockingRunner(), result_cache=cache
        )
    )

    # No accepted results → cache must remain empty.
    assert len(cache) == 0, (
        f"Cache should be empty after all-blocked run, but contains {len(cache)} entries."
    )


# ---------------------------------------------------------------------------
# Test 9 — event_sink receives cache-hit and cache-store events
# ---------------------------------------------------------------------------

def test_event_sink_receives_cache_hit_and_cache_store_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing an event_sink to execute_workflow proves observability is real.

    Setup: 3-recipe contract, child 0 pre-cached (simulates prior partial run).
    Expected events forwarded to sink:
    - 1 cache-hit event (child 0 served from cache)
    - 2 cache-store events (children 1 and 2 complete and get stored)
    Total: 3 events.

    Each event must be a dict with ``type="runtime_trace"``,
    ``severity="info"``, and a ``detail`` field containing the appropriate
    ``workflow_resume cache_hit`` or ``workflow_resume cache_store`` marker.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        _child_cache_key,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import (
        CachedChildResult,
        WorkflowResultCache,
    )

    contract = _valid_contract(n_recipes=3)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    # Pre-populate cache with child 0 (simulates first partial run completing it).
    recipe_id_0 = contract.selected_recipes[0]
    key_0 = _child_cache_key(contract, 0, recipe_id_0)
    cache = WorkflowResultCache()
    cache.store(key_0, CachedChildResult(task_id=key_0, status="accepted"))

    # Collecting sink captures every event forwarded by the executor.
    collected_events: list[dict[str, Any]] = []

    def _sink(event: dict[str, object]) -> None:
        collected_events.append(event)

    asyncio.run(
        execute_workflow(
            contract,
            config=config,
            child_runner=fake_runner,
            result_cache=cache,
            event_sink=_sink,
        )
    )

    # 1 cache-hit (child 0) + 2 cache-store (children 1 and 2) = 3 events total.
    assert len(collected_events) == 3, (
        f"Expected 3 observability events (1 hit + 2 store), got {len(collected_events)}: "
        f"{collected_events}"
    )

    # Every event must be a runtime_trace dict with severity=info.
    for ev in collected_events:
        assert isinstance(ev, dict), f"Event must be a dict, got {type(ev)}"
        assert ev.get("type") == "runtime_trace", f"Unexpected event type: {ev}"
        assert ev.get("severity") == "info", f"Unexpected severity: {ev}"

    # Exactly 1 cache-hit event.
    hit_events = [ev for ev in collected_events if "cache_hit" in ev.get("detail", "")]
    assert len(hit_events) == 1, (
        f"Expected 1 cache-hit event, got {len(hit_events)}: {hit_events}"
    )

    # Exactly 2 cache-store events.
    store_events = [ev for ev in collected_events if "cache_store" in ev.get("detail", "")]
    assert len(store_events) == 2, (
        f"Expected 2 cache-store events, got {len(store_events)}: {store_events}"
    )


# ---------------------------------------------------------------------------
# Test 10 — raising event_sink must never crash the workflow run
# ---------------------------------------------------------------------------

def test_raising_event_sink_does_not_abort_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sink that raises on every call must NOT abort the workflow run.

    Observability is best-effort: a broken sink must be silently suppressed so
    that the children still complete, results are still cached, and the returned
    status reflects the actual child outcomes.

    Setup: 3-recipe contract, child 0 pre-cached (generates a cache-hit event),
    children 1 and 2 dispatched (generate two cache-store events).  The sink
    raises RuntimeError on every invocation.

    Assertions:
    - The run completes without raising.
    - result.status is "accepted" (not an error status).
    - All 3 results end up in the cache (2 newly stored + 1 pre-seeded).
    - The fake runner was called exactly 2 times (only children 1 and 2).
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        _child_cache_key,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import (
        CachedChildResult,
        WorkflowResultCache,
    )

    contract = _valid_contract(n_recipes=3)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    # Pre-seed child 0 (triggers a cache-hit event → sink raises → must be swallowed).
    recipe_id_0 = contract.selected_recipes[0]
    key_0 = _child_cache_key(contract, 0, recipe_id_0)
    cache = WorkflowResultCache()
    cache.store(key_0, CachedChildResult(task_id=key_0, status="accepted"))

    def _raising_sink(event: dict[str, object]) -> None:  # noqa: ARG001
        raise RuntimeError("sink intentionally broken")

    # Must NOT raise.
    result = asyncio.run(
        execute_workflow(
            contract,
            config=config,
            child_runner=fake_runner,
            result_cache=cache,
            event_sink=_raising_sink,
        )
    )

    # Run must complete normally — result unaffected by the broken sink.
    assert result.status == "accepted", (
        f"Expected status='accepted' with raising sink, got {result.status!r}"
    )
    # Children 1 and 2 were dispatched; child 0 was served from cache.
    assert fake_runner.calls == 2
    # All 3 results in cache: 1 pre-seeded + 2 newly stored.
    assert len(cache) == 3


# ---------------------------------------------------------------------------
# Test 11 — all-tasks-pre-cached: status="accepted", zero dispatches
# ---------------------------------------------------------------------------

def test_all_tasks_pre_cached_returns_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every child task is already in the cache as 'accepted',
    the executor must return status='accepted' with zero new dispatches.

    This exercises the ``all([])-is-True`` path in the aggregation:
    - ``dispatched_results`` is an empty tuple (nothing was dispatched).
    - ``cache_hit_count > 0`` makes ``any_accepted=True``.
    - ``all_disabled`` is False (cache_hit_count > 0 short-circuits it).
    - The status must therefore be 'accepted', not 'disabled' or 'blocked'.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        _child_cache_key,
        execute_workflow,
    )
    from magi_agent.harness.workflow_result_cache import (
        CachedChildResult,
        WorkflowResultCache,
    )

    n = 3
    contract = _valid_contract(n_recipes=n)
    fake_runner = _TrackingFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    # Pre-seed ALL children as accepted.
    cache = WorkflowResultCache()
    for i in range(n):
        recipe_id = contract.selected_recipes[i]
        key = _child_cache_key(contract, i, recipe_id)
        cache.store(key, CachedChildResult(task_id=key, status="accepted"))

    result = asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner, result_cache=cache)
    )

    # No dispatches — all served from cache.
    assert fake_runner.calls == 0, (
        f"Expected 0 dispatches (all pre-cached), got {fake_runner.calls}."
    )
    # childTasksDispatched counts only actually-dispatched children, not cache hits.
    assert result.child_tasks_dispatched == 0

    # Status must be 'accepted' — cache hits count as accepted in aggregation.
    assert result.status == "accepted", (
        f"Expected status='accepted' for all-pre-cached run, got {result.status!r}. "
        "Check the all([])-is-True path in the aggregation logic."
    )
