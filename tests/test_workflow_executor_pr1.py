"""PR1 — Bounded workflow-executor skeleton.

Tests cover the five mandatory behaviours:
1. validation-fail ⇒ no dispatch
2. Semaphore actually bounds in-flight CHILDREN through the executor's real
   dispatch path (not a standalone stdlib proof).
3. disabled (MAGI_WORKFLOW_EXECUTOR_ENABLED=off) ⇒ no execution
4. dry-run parity — the executor's dry-run pass produces output consistent
   with WorkflowDryRunReport (no regression against existing dry_run module).
5. Semaphore cap is derived from ParallelToolPolicyDecision.tool_class_limit.max_concurrent
   (clamped at 16), not just from config.max_concurrent.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence

import pytest

from magi_agent.harness.parallel_execution import (
    ParallelExecutionScope,
    ParallelToolPolicyInput,
    ToolLimitMetadata,
    build_parallel_tool_policy_decision,
)
from magi_agent.workflows.compiler import (
    CompiledWorkflowContract,
    compile_governed_workflow,
    WorkflowCompileInput,
)
from magi_agent.workflows.dry_run import (
    WorkflowDryRunReport,
    dry_run_governed_workflow,
)
from magi_agent.workflows.registry import WorkflowRegistryEntry


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_DIGEST = "sha256:" + "a" * 64


def _registry_entry(
    workflow_id: str = "openmagi.research.cited",
    version: str = "1.0.0",
    status: str = "active",
) -> WorkflowRegistryEntry:
    return WorkflowRegistryEntry(
        workflowId=workflow_id,
        version=version,
        ownerRef="team-digest:research",
        status=status,
        sourceDigest=_DIGEST,
        promotionHistory=("draft:2026-05-01", "staging:2026-05-02", "active:2026-05-03"),
        compatibleRuntimeContractVersion="programmable-determinism.v1",
    )


def _valid_contract(n_recipes: int = 1) -> CompiledWorkflowContract:
    """Build a contract that passes validate_compiled_workflow().

    *n_recipes* controls how many selected recipe IDs are included (1–8).
    The executor generates one child task per selected recipe, so passing
    ``n_recipes > cap`` lets tests observe bounding behaviour.

    Each recipe maps to a separate workflow registry entry with a distinct
    version string so all recipes pass the ``selected_workflow_not_registered``
    validation check (recipe IDs are ``{workflowId}.v{version}``).
    """
    n_recipes = max(1, min(n_recipes, 8))

    # Build one registry entry per recipe, using patch versions 0..n_recipes-1.
    entries = tuple(
        _registry_entry(version=f"1.0.{i}") for i in range(n_recipes)
    )
    # Recipe ID must match the pattern ``{workflowId}.v{version}``.
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


def _make_parallel_policy_decision(max_concurrent: int) -> object:
    """Build a real ParallelToolPolicyDecision with the given maxConcurrent cap."""
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
            "toolClassLimit": {"toolClass": "read_only", "maxConcurrent": max_concurrent},
            "turnLimit": {"toolClass": "turn", "maxConcurrent": max_concurrent},
        }
    )
    return build_parallel_tool_policy_decision(policy_input)


def _invalid_contract() -> CompiledWorkflowContract:
    """Build a contract that FAILS validate_compiled_workflow():
    - references an unknown tool in toolAllowlist
    - missing noMatchTerminalState
    """
    entry = _registry_entry()
    # We build via model_validate directly to bypass compile so we can
    # produce an invalid contract (compile always produces Literal[False] flags,
    # so we must inject bad logical data via a valid Pydantic structure).
    data = {
        "workflowId": "openmagi.research.cited",
        "version": "1.0.0",
        "selectedRecipes": ("openmagi.research.cited.v1.0.0",),
        "registeredWorkflows": (entry.model_dump(by_alias=True, mode="python"),),
        # toolAllowlist references a tool not in availableTools
        "toolAllowlist": ("UnknownTool",),
        "toolDenylist": (),
        "evidenceRequirements": ("SourceInspection",),
        "validatorRefs": ("deterministic-verifier",),
        "contextProjectionPolicy": "explicit",
        "outputProjectionMode": "structured_claims_only",
        "repairPolicy": "retry-once",
        "approvalPolicy": "auto",
        "budgets": {"maxIterations": 10, "wallClockTimeoutMs": 60_000},
        "hardInvariants": {
            "rawDraftStreamingForbidden": True,
            "toolhostOnlyExecution": True,
            "validatorBeforeProjection": True,
        },
        "effectivePolicySnapshotDigest": _DIGEST,
        "availableTools": ("SourceLedgerRead",),  # UnknownTool not here
        "availableValidators": ("deterministic-verifier",),
        "availableRenderers": ("structured_claims_only",),
        "evidenceProducers": ("SourceInspection",),
        "routePrecedence": (),
        "noMatchTerminalState": "block",
        "trafficAttached": False,
        "executionAttached": False,
    }
    return CompiledWorkflowContract.model_validate(data)


# ---------------------------------------------------------------------------
# Fake child runner for local-fake mode
# ---------------------------------------------------------------------------

class _FakeChildRunner:
    """Simple fake that returns a completed stub immediately."""

    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: int = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        task_id = getattr(request, "task_id", "task-unknown")
        return {
            "childExecutionId": f"child-{task_id}",
            "status": "completed",
            "summary": "local fake completed",
            "evidenceRefs": (f"evidence:{task_id[:8]}-src1",),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


class _InstrumentedFakeRunner:
    """Fake child runner that tracks the peak number of concurrently executing children.

    Each ``run_child`` call:
    1. Increments the in-flight counter.
    2. Records the new peak if higher than the previous peak.
    3. Yields to the event loop (``await asyncio.sleep(0)``) so other coroutines
       can enter before this one exits — this is the key step that exposes any
       failure in concurrency bounding.
    4. Decrements the in-flight counter.
    """

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
        # Yield — lets other tasks that are waiting on the semaphore try to
        # acquire it while this task is "in-flight".
        await asyncio.sleep(0)
        task_id = getattr(request, "task_id", "task-unknown")
        self._current -= 1
        return {
            "childExecutionId": f"child-{task_id}",
            "status": "completed",
            "summary": "local fake completed",
            "evidenceRefs": (f"evidence:{task_id[:8]}-src1",),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


class _EnvLiveChildRunner:
    """Live child runner double constructed by env-default workflow wiring."""

    openmagi_live_provider = True
    instances: list["_EnvLiveChildRunner"] = []

    def __init__(self, *, toolset_profile: str = "none", workspace_root: str | None = None) -> None:
        self.toolset_profile = toolset_profile
        self.workspace_root = workspace_root
        self.calls = 0
        type(self).instances.append(self)

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        task_id = getattr(request, "task_id", "task-unknown")
        return {
            "childExecutionId": f"child-{task_id}",
            "status": "completed",
            "summary": "env live child completed",
            "evidenceRefs": ("evidence:1111111111111111",),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


# ---------------------------------------------------------------------------
# Test 1 — validation failure ⇒ no dispatch
# ---------------------------------------------------------------------------

def test_validation_fail_causes_no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A contract that fails validate_compiled_workflow() must result in
    zero child dispatches and a clear refusal result."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        execute_workflow,
    )

    fake_runner = _FakeChildRunner()
    contract = _invalid_contract()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner)
    )

    assert result.status == "validation_failed"
    assert len(result.validation_reason_codes) > 0
    assert result.child_tasks_dispatched == 0
    assert fake_runner.calls == 0


# ---------------------------------------------------------------------------
# Test 2 — Executor's per-child dispatch is genuinely bounded ≤ cap
# ---------------------------------------------------------------------------

def test_executor_per_child_dispatch_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executor must bound in-flight CHILDREN through its own dispatch path.

    Strategy:
    - Build a contract with 8 selected recipes → 8 child tasks.
    - Use an instrumented fake child runner that tracks peak concurrent
      in-flight count (increments on entry, yields to event loop, decrements
      on exit).
    - Configure cap=3 (well below task count) via config.max_concurrent.
    - Call execute_workflow() through the REAL executor path (env gate on).
    - Assert peak in-flight ≤ 3 (not 8 or more).

    This test would FAIL with the pre-fix implementation that wrapped the
    entire batch call in one semaphore slot rather than each child individually.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        _bounded_semaphore_cap,
        execute_workflow,
    )

    # Verify the clamping helper still works correctly (kept from prior test).
    assert _bounded_semaphore_cap(1) == 1
    assert _bounded_semaphore_cap(16) == 16
    assert _bounded_semaphore_cap(17) == 16
    assert _bounded_semaphore_cap(64) == 16
    assert _bounded_semaphore_cap(0) == 1  # lower-bound clamped to 1

    # Build a contract with 8 recipes → 8 child task slots.
    # With cap=3 the executor must queue tasks rather than run all 8 at once.
    contract = _valid_contract(n_recipes=8)
    cap = 3

    instrumented_runner = _InstrumentedFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        localFakeChildRunnerEnabled=True,
        maxConcurrent=cap,
    )

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=instrumented_runner)
    )

    # All 8 tasks should have been dispatched (non-disabled).
    assert result.child_tasks_dispatched > 0, (
        f"Expected some tasks dispatched, got status={result.status!r}"
    )
    # The peak in-flight children MUST NOT exceed the configured cap.
    assert instrumented_runner.peak <= cap, (
        f"Peak in-flight children was {instrumented_runner.peak}, "
        f"expected ≤ {cap}. "
        "This means the executor is NOT bounding per-child concurrency."
    )
    # Sanity: all tasks were called (runner.calls == tasks dispatched).
    assert instrumented_runner.calls == result.child_tasks_dispatched


# ---------------------------------------------------------------------------
# Test 3 — disabled env gate ⇒ no execution
# ---------------------------------------------------------------------------

def test_disabled_env_gate_causes_no_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """When MAGI_WORKFLOW_EXECUTOR_ENABLED is not set (or falsy), the executor
    must return a dry-run/disabled result without dispatching children."""
    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        execute_workflow,
    )

    fake_runner = _FakeChildRunner()
    contract = _valid_contract()
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner)
    )

    assert result.status == "disabled"
    assert result.child_tasks_dispatched == 0
    assert fake_runner.calls == 0


def test_env_default_live_child_runner_dispatches_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Default execute_workflow(config=None) should honour the installed/full
    env gates and construct a live child runner without caller injection."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "readonly")
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))
    _EnvLiveChildRunner.instances.clear()

    import magi_agent.runtime.child_runner_live as live_mod
    from magi_agent.harness.workflow_executor import execute_workflow

    monkeypatch.setattr(live_mod, "RealLocalChildRunner", _EnvLiveChildRunner)

    result = asyncio.run(execute_workflow(_valid_contract()))

    assert result.status == "accepted"
    assert result.child_tasks_dispatched == 1
    assert len(_EnvLiveChildRunner.instances) == 1
    assert _EnvLiveChildRunner.instances[0].calls == 1
    assert _EnvLiveChildRunner.instances[0].toolset_profile == "readonly"
    assert _EnvLiveChildRunner.instances[0].workspace_root == str(tmp_path)


def test_env_default_executor_without_live_child_runner_stays_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)

    from magi_agent.harness.workflow_executor import execute_workflow

    result = asyncio.run(execute_workflow(_valid_contract()))

    assert result.status == "disabled"
    assert result.child_tasks_dispatched == 0


# ---------------------------------------------------------------------------
# Test 4 — dry-run parity: executor's dry-run pass matches dry_run module
# ---------------------------------------------------------------------------

def test_dry_run_parity_with_existing_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executor's internal dry-run pass must produce a WorkflowDryRunReport
    that is consistent with dry_run_governed_workflow() for the same contract."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        execute_workflow,
    )

    contract = _valid_contract()

    # Canonical dry-run from existing module
    canonical_dry_run: WorkflowDryRunReport = dry_run_governed_workflow(contract)
    assert canonical_dry_run.ok is True
    assert canonical_dry_run.model_call_attempted is False
    assert canonical_dry_run.tool_call_attempted is False

    # Executor with local-fake enabled (still dry-run because local-fake returns stubs)
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )
    fake_runner = _FakeChildRunner()

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=fake_runner)
    )

    # Executor must have performed a dry-run pass (returned in result)
    assert result.dry_run_report is not None
    assert isinstance(result.dry_run_report, WorkflowDryRunReport)

    # Dry-run fields must match the canonical output
    assert result.dry_run_report.ok == canonical_dry_run.ok
    assert result.dry_run_report.selected_recipe_ids == canonical_dry_run.selected_recipe_ids
    assert result.dry_run_report.effective_policy_snapshot_digest == (
        canonical_dry_run.effective_policy_snapshot_digest
    )
    assert result.dry_run_report.model_call_attempted is False
    assert result.dry_run_report.tool_call_attempted is False
    assert result.dry_run_report.network_attempted is False
    assert result.dry_run_report.filesystem_attempted is False


# ---------------------------------------------------------------------------
# Test 5 — semaphore cap is derived from ParallelToolPolicyDecision
# ---------------------------------------------------------------------------

def test_semaphore_cap_derived_from_parallel_policy_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The semaphore cap MUST follow ParallelToolPolicyDecision.tool_class_limit.max_concurrent
    (clamped at 16), not just config.max_concurrent.

    Strategy:
    - Build a real ParallelToolPolicyDecision with maxConcurrent=2.
    - Config.max_concurrent is set to 16 (the default hard limit).
    - Build a contract with 8 recipes (8 child tasks).
    - Use an _InstrumentedFakeRunner to measure peak in-flight.
    - Assert that peak in-flight ≤ 2 (the policy decision's cap), not ≤ 16.

    Also verify the _cap_from_policy helper directly:
    - When policy.tool_class_limit.max_concurrent < config_max_concurrent,
      the policy value wins (policy is the tighter constraint).
    - When policy.tool_class_limit.max_concurrent > _PR1_MAX_CONCURRENT=16,
      the hard PR1 ceiling still applies (cap is clamped at 16).
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        _cap_from_policy,
        execute_workflow,
    )

    # --- Part A: unit-test _cap_from_policy directly ---

    # Policy cap=4, config_max=16 → use policy value (4 < 16)
    decision_4 = _make_parallel_policy_decision(4)
    assert _cap_from_policy(decision_4, config_max_concurrent=16) == 4

    # Policy cap=4, config_max=2 → use config value (2 < 4, config is tighter)
    assert _cap_from_policy(decision_4, config_max_concurrent=2) == 2

    # Policy cap=32 (exceeds PR1 ceiling of 16) → clamped to 16
    decision_32 = _make_parallel_policy_decision(32)
    assert _cap_from_policy(decision_32, config_max_concurrent=16) == 16

    # No policy → fall through to config_max_concurrent (clamped at 16)
    assert _cap_from_policy(None, config_max_concurrent=8) == 8
    assert _cap_from_policy(None, config_max_concurrent=20) == 16  # clamped

    # --- Part B: end-to-end through execute_workflow ---

    # Policy says max 2 concurrent; config says max 16.
    # The executor must derive cap=2 from the policy.
    decision_2 = _make_parallel_policy_decision(2)
    contract = _valid_contract(n_recipes=8)  # 8 tasks > cap=2

    instrumented_runner = _InstrumentedFakeRunner()
    config = WorkflowExecutorConfig(
        enabled=True,
        localFakeChildRunnerEnabled=True,
        maxConcurrent=16,       # high config cap — should be overridden by policy
        parallelPolicy=decision_2,
    )

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=instrumented_runner)
    )

    assert result.child_tasks_dispatched > 0, (
        f"Expected tasks dispatched, got status={result.status!r}"
    )
    assert instrumented_runner.peak <= 2, (
        f"Peak in-flight was {instrumented_runner.peak}, expected ≤ 2. "
        "The executor is NOT reading the cap from ParallelToolPolicyDecision."
    )
    assert instrumented_runner.calls == result.child_tasks_dispatched


# ---------------------------------------------------------------------------
# Test 6 — "partial" child status is NOT misreported as "blocked"
# ---------------------------------------------------------------------------

class _PartialChildRunner:
    """Fake runner that raises on every call, causing the recipe layer to
    produce ``ResearchChildRunnerResult.status == "partial"`` (which is what
    ``_status_and_reasons()`` returns whenever any ``ChildRunnerResult`` comes
    back with ``status="error"``).

    This exercises the aggregation branch that previously fell through to
    ``"blocked"`` because only accepted/error/blocked/disabled were checked
    at the executor level.
    """

    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: int = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        # Raising causes LocalChildRunnerBoundary to return
        # ChildRunnerResult(status="error"), which makes _status_and_reasons()
        # return ("partial", ...) for the ResearchChildRunnerResult.
        raise RuntimeError("simulated partial child failure")


def test_partial_child_status_is_not_misreported_as_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``"partial"`` child result MUST NOT be aggregated as ``"blocked"``.

    Before the fix, any unrecognised child status fell through the chain and
    landed on the final ``else: executor_status = "blocked"`` branch, so a
    partial child was silently mis-classified.

    After the fix the executor propagates ``"partial"`` at the aggregate level
    whenever any child returns that status.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        execute_workflow,
    )

    partial_runner = _PartialChildRunner()
    contract = _valid_contract(n_recipes=2)
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
    )

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=partial_runner)
    )

    assert result.status == "partial", (
        f"Expected status='partial' when children return partial, "
        f"got {result.status!r}. Was it misreported as 'blocked'?"
    )
    assert result.child_tasks_dispatched > 0
    assert partial_runner.calls > 0


# ---------------------------------------------------------------------------
# Test 7 — total-agents-per-run cap is enforced by the executor
# ---------------------------------------------------------------------------

def test_executor_blocks_before_dispatch_when_total_agent_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executor must enforce the run-level ≤1000 total-agent cap.

    The child boundary has its own cap helper, but the workflow executor is the
    orchestrator that fans out child tasks.  If the run-level budget is already
    exhausted, no child should be dispatched at all.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        execute_workflow,
    )
    from magi_agent.runtime.child_runner_boundary import MAX_TOTAL_AGENTS_PER_RUN

    runner = _FakeChildRunner()
    contract = _valid_contract(n_recipes=1)
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
        maxTotalAgentsPerRun=MAX_TOTAL_AGENTS_PER_RUN,
        agentsSpawnedSoFar=MAX_TOTAL_AGENTS_PER_RUN,
    )

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(contract, config=config, child_runner=runner)
    )

    assert result.status == "blocked"
    assert result.validation_reason_codes == ("total_agents_per_run_exceeded",)
    assert result.child_tasks_dispatched == 0
    assert runner.calls == 0


def test_executor_opt_in_can_route_child_through_real_adk_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executor should wire the opt-in real-child ADK surface end to end."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        WorkflowExecutorResult,
        execute_workflow,
    )
    from magi_agent.runtime.adk_turn_runner import (
        LocalAdkReplayRunner,
        LocalAdkTurnRunnerBoundary,
    )

    fake_runner = _FakeChildRunner()
    replay_runner = LocalAdkReplayRunner()
    adk_boundary = LocalAdkTurnRunnerBoundary.from_local_test_runner(replay_runner)
    contract = _valid_contract(n_recipes=1)
    config = WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
        realChildExecutionPackEnabled=True,
    )

    result: WorkflowExecutorResult = asyncio.run(
        execute_workflow(
            contract,
            config=config,
            child_runner=fake_runner,
            adk_turn_boundary=adk_boundary,
        )
    )

    assert result.status == "accepted"
    assert result.child_tasks_dispatched == 1
    assert fake_runner.calls == 0
    assert len(replay_runner.calls) == 1
