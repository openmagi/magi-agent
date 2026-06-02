"""PR6 — Gate rollout for the bounded workflow-executor.

This is the FIRST real child-execution path in the runtime, so it rolls out
through the EXISTING gate pipeline (``gates/``) following the same
readiness-gate pattern used by ``gate7_readiness`` and the canary ladder:

    disabled
        ▼  (gate enabled + selected canary scope matched, but pre-canary stage)
    shadow      ── validate + dry-run on REAL traffic, ZERO live dispatch
        ▼  (promoted to canary stage 186bf3d7 / fleet)
    live        ── bounded per-child fan-out actually dispatches

Mandatory behaviours under test:
1. shadow mode never dispatches — validate + dry-run happen, child runner call
   count == 0, and the result is DISTINCT from fully-disabled.
2. gate promotion flips execution only at the right gate — live dispatch is
   enabled only at/after the canary stage; earlier stages stay shadow/dry-run.
3. telemetry counters emit — runs / agents-spawned / concurrency-high-water /
   filtered-claims counters are populated and observable.
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from magi_agent.gates.workflow_executor_readiness import (
    WorkflowExecutorReadinessConfig,
    resolve_workflow_execution_mode,
    workflow_executor_readiness_health_metadata,
)
from magi_agent.harness.workflow_executor import (
    WorkflowExecutorConfig,
    WorkflowExecutorTelemetry,
    execute_workflow,
)
from magi_agent.workflows.compiler import (
    CompiledWorkflowContract,
    compile_governed_workflow,
    WorkflowCompileInput,
)
from magi_agent.workflows.registry import WorkflowRegistryEntry


_DIGEST = "sha256:" + "a" * 64

#: The infra canary bot id (see gates/api_canary_ladder.py).
_CANARY_BOT = "186bf3d7"
_CANARY_OWNER = "owner-186bf3d7"


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Contract fixtures (mirrors test_workflow_executor_pr1.py)
# ---------------------------------------------------------------------------

def _registry_entry(version: str = "1.0.0") -> WorkflowRegistryEntry:
    return WorkflowRegistryEntry(
        workflowId="openmagi.research.cited",
        version=version,
        ownerRef="team-digest:research",
        status="active",
        sourceDigest=_DIGEST,
        promotionHistory=("draft:2026-05-01", "staging:2026-05-02", "active:2026-05-03"),
        compatibleRuntimeContractVersion="programmable-determinism.v1",
    )


def _valid_contract(n_recipes: int = 1) -> CompiledWorkflowContract:
    n_recipes = max(1, min(n_recipes, 8))
    entries = tuple(_registry_entry(version=f"1.0.{i}") for i in range(n_recipes))
    recipe_ids = tuple(f"openmagi.research.cited.v1.0.{i}" for i in range(n_recipes))
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


class _CountingChildRunner:
    """Fake child runner that records how many times a child was dispatched."""

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


def _live_config() -> WorkflowExecutorConfig:
    return WorkflowExecutorConfig(
        enabled=True,
        local_fake_child_runner_enabled=True,
        max_concurrent=4,
    )


# ===========================================================================
# Readiness gate: rollout-stage resolution following the gates/ pattern
# ===========================================================================

def test_readiness_gate_disabled_by_default_has_no_authority() -> None:
    meta = workflow_executor_readiness_health_metadata(
        WorkflowExecutorReadinessConfig(),
        bot_id=_CANARY_BOT,
        user_id=_CANARY_OWNER,
    )
    assert meta["enabled"] is False
    assert meta["status"] == "disabled"
    assert meta["executionMode"] == "disabled"
    assert meta["liveDispatchAllowed"] is False
    assert meta["selectedScopeMatched"] is False
    assert meta["reasonCodes"] == ["gate_disabled"]


def test_readiness_gate_enabled_pre_canary_is_shadow_only() -> None:
    """Enabled + selected scope but stage below canary ⇒ shadow (no live dispatch)."""
    meta = workflow_executor_readiness_health_metadata(
        WorkflowExecutorReadinessConfig(
            enabled=True,
            shadowModeEnabled=True,
            killSwitchEnabled=False,
            selectedBotDigest=_sha256(_CANARY_BOT),
            selectedOwnerUserIdDigest=_sha256(_CANARY_OWNER),
            environment="production",
            environmentAllowlist=("production",),
            promotedGate=3,  # gate3 reached, canary stage NOT yet
        ),
        bot_id=_CANARY_BOT,
        user_id=_CANARY_OWNER,
    )
    assert meta["enabled"] is True
    assert meta["status"] == "shadow"
    assert meta["executionMode"] == "shadow"
    assert meta["selectedScopeMatched"] is True
    assert meta["liveDispatchAllowed"] is False
    assert "selected_shadow_ready" in meta["reasonCodes"]


def test_readiness_gate_flips_live_only_at_canary_stage() -> None:
    """Live dispatch is enabled ONLY once the canary gate (>=5 + canary flag) is reached."""
    base = dict(
        enabled=True,
        shadowModeEnabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256(_CANARY_BOT),
        selectedOwnerUserIdDigest=_sha256(_CANARY_OWNER),
        environment="production",
        environmentAllowlist=("production",),
    )
    # gate 5 reached but canary promotion flag NOT set ⇒ still shadow.
    pre_canary = workflow_executor_readiness_health_metadata(
        WorkflowExecutorReadinessConfig(**base, promotedGate=5),
        bot_id=_CANARY_BOT,
        user_id=_CANARY_OWNER,
    )
    assert pre_canary["executionMode"] == "shadow"
    assert pre_canary["liveDispatchAllowed"] is False

    # gate 5 reached AND canary promotion flag set ⇒ live.
    canary = workflow_executor_readiness_health_metadata(
        WorkflowExecutorReadinessConfig(
            **base, promotedGate=5, canaryPromotionConfirmed=True
        ),
        bot_id=_CANARY_BOT,
        user_id=_CANARY_OWNER,
    )
    assert canary["status"] == "live"
    assert canary["executionMode"] == "live"
    assert canary["liveDispatchAllowed"] is True
    assert "selected_canary_live_ready" in canary["reasonCodes"]


def test_readiness_gate_non_selected_bot_fails_closed_to_disabled() -> None:
    meta = workflow_executor_readiness_health_metadata(
        WorkflowExecutorReadinessConfig(
            enabled=True,
            shadowModeEnabled=True,
            killSwitchEnabled=False,
            selectedBotDigest=_sha256("other-bot"),
            selectedOwnerUserIdDigest=_sha256(_CANARY_OWNER),
            environment="production",
            environmentAllowlist=("production",),
            promotedGate=5,
            canaryPromotionConfirmed=True,
        ),
        bot_id=_CANARY_BOT,
        user_id=_CANARY_OWNER,
    )
    assert meta["executionMode"] == "disabled"
    assert meta["liveDispatchAllowed"] is False
    assert "bot_not_selected" in meta["reasonCodes"]


def test_readiness_gate_ignores_forged_live_dispatch_flag() -> None:
    """A forged liveDispatchAllowed env flag cannot grant live authority."""
    cfg = WorkflowExecutorReadinessConfig(
        enabled=True,
        shadowModeEnabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256(_CANARY_BOT),
        selectedOwnerUserIdDigest=_sha256(_CANARY_OWNER),
        environment="production",
        environmentAllowlist=("production",),
        promotedGate=3,  # below canary
        liveDispatchAllowed=True,  # forged
    )
    # Pydantic Literal[False] coerces/serializes to False either way.
    assert cfg.live_dispatch_allowed is False
    meta = workflow_executor_readiness_health_metadata(
        cfg, bot_id=_CANARY_BOT, user_id=_CANARY_OWNER
    )
    assert meta["executionMode"] == "shadow"
    assert meta["liveDispatchAllowed"] is False


def test_resolve_execution_mode_helper_matches_metadata() -> None:
    cfg = WorkflowExecutorReadinessConfig(
        enabled=True,
        shadowModeEnabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256(_CANARY_BOT),
        selectedOwnerUserIdDigest=_sha256(_CANARY_OWNER),
        environment="production",
        environmentAllowlist=("production",),
        promotedGate=5,
        canaryPromotionConfirmed=True,
    )
    assert resolve_workflow_execution_mode(
        cfg, bot_id=_CANARY_BOT, user_id=_CANARY_OWNER
    ) == "live"


# ===========================================================================
# Executor shadow mode: validate + dry-run on real traffic, ZERO dispatch
# ===========================================================================

def test_shadow_mode_never_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shadow runs validate + dry-run but dispatches NO child (call count == 0)."""
    # Env gate ON to prove shadow is NOT just the env-off short-circuit.
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=3)
    runner = _CountingChildRunner()

    result = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="shadow",
        )
    )

    assert result.status == "shadow"
    # The governance gate STILL ran on real traffic:
    assert result.dry_run_report is not None
    # But ZERO children were dispatched:
    assert runner.calls == 0
    assert result.child_tasks_dispatched == 0


def test_shadow_is_distinct_from_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shadow != disabled: both skip dispatch, but only shadow keeps the dry-run."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=2)
    shadow_runner = _CountingChildRunner()
    disabled_runner = _CountingChildRunner()

    shadow = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=shadow_runner,
            execution_mode="shadow",
        )
    )
    disabled = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=disabled_runner,
            execution_mode="disabled",
        )
    )

    assert shadow.status == "shadow"
    assert disabled.status == "disabled"
    assert shadow.status != disabled.status
    assert shadow_runner.calls == 0
    assert disabled_runner.calls == 0


def test_live_mode_actually_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: live mode DOES dispatch (proves shadow's zero is meaningful)."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=3)
    runner = _CountingChildRunner()

    result = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="live",
        )
    )

    # Live actually fans out: the child runner was called once per recipe and
    # the executor reports the dispatch count.  (The local-fake recipe yields a
    # "blocked" aggregate status; the load-bearing fact here is that dispatch
    # genuinely happened — contrast with shadow's zero calls.)
    assert result.status != "shadow"
    assert result.status != "disabled"
    assert result.execution_mode == "live"
    assert runner.calls == 3
    assert result.child_tasks_dispatched == 3


def test_default_execution_mode_follows_env_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default mode (no execution_mode arg): env off ⇒ disabled, env on ⇒ live.

    This preserves byte-identical PR1-PR5 behaviour when execution_mode is omitted.
    """
    contract = _valid_contract(n_recipes=2)

    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)
    off_runner = _CountingChildRunner()
    off = asyncio.run(
        execute_workflow(contract, config=_live_config(), child_runner=off_runner)
    )
    assert off.status == "disabled"
    assert off_runner.calls == 0

    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    on_runner = _CountingChildRunner()
    on = asyncio.run(
        execute_workflow(contract, config=_live_config(), child_runner=on_runner)
    )
    assert on.status not in {"disabled", "shadow"}
    assert on.execution_mode == "live"
    assert on_runner.calls == 2


# ===========================================================================
# Telemetry counters: runs / agents-spawned / concurrency-high-water / filtered
# ===========================================================================

def test_telemetry_counters_emit_on_live_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=3)
    runner = _CountingChildRunner()
    telemetry = WorkflowExecutorTelemetry()

    result = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="live",
            telemetry=telemetry,
        )
    )

    # Counters reflect real run activity.
    assert telemetry.runs == 1
    assert telemetry.agents_spawned == 3
    assert telemetry.concurrency_high_water >= 1
    assert telemetry.concurrency_high_water <= 4  # bounded by max_concurrent
    assert telemetry.filtered_claims == 0
    # Counters are also surfaced on the result for observability.
    assert result.telemetry_snapshot is not None
    assert result.telemetry_snapshot["runs"] == 1
    assert result.telemetry_snapshot["agentsSpawned"] == 3
    assert result.telemetry_snapshot["concurrencyHighWater"] >= 1


def test_telemetry_counts_runs_but_not_agents_in_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shadow counts the run but spawns zero agents (proves the counter is real)."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=3)
    runner = _CountingChildRunner()
    telemetry = WorkflowExecutorTelemetry()

    asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="shadow",
            telemetry=telemetry,
        )
    )

    assert telemetry.runs == 1
    assert telemetry.agents_spawned == 0
    assert telemetry.concurrency_high_water == 0
    assert runner.calls == 0


def test_telemetry_filtered_claims_counts_cross_review_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filtered-claims counter reflects cross-review filtered claim refs."""
    from magi_agent.harness.cross_review import CrossReviewStep

    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=1)
    runner = _CountingChildRunner()
    telemetry = WorkflowExecutorTelemetry()

    # Build a cross-review step with a claim that has no corroboration ⇒ filtered.
    # ``claim:fact-orphan`` is attested by only one peer ⇒ filtered (min support 2).
    step = CrossReviewStep.model_validate(
        {
            "reviewId": "wf-review-pr6",
            "peerAttestations": (
                {"agent_ref": "peer:a", "claim_refs": ("claim:fact-shared",)},
                {
                    "agent_ref": "peer:b",
                    "claim_refs": ("claim:fact-shared", "claim:fact-orphan"),
                },
            ),
            "minPeerSupport": 2,
        }
    )

    result = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="live",
            cross_review_step=step,
            telemetry=telemetry,
        )
    )

    assert len(result.cross_review_filtered_claim_refs) >= 1
    assert telemetry.filtered_claims == len(result.cross_review_filtered_claim_refs)


def test_telemetry_event_sink_receives_counter_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counters are observable via the event_sink (best-effort telemetry surface)."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=2)
    runner = _CountingChildRunner()
    telemetry = WorkflowExecutorTelemetry()
    events: list[dict[str, object]] = []

    asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="live",
            event_sink=events.append,
            telemetry=telemetry,
        )
    )

    # A telemetry trace event was emitted carrying the run/agent counts.
    counter_events = [
        e
        for e in events
        if isinstance(e.get("detail"), str)
        and "workflow_telemetry" in str(e.get("detail"))
    ]
    assert counter_events, "expected a workflow_telemetry counter event"


def test_telemetry_best_effort_does_not_crash_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A throwing event_sink must not crash the run (telemetry is best-effort)."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=2)
    runner = _CountingChildRunner()
    telemetry = WorkflowExecutorTelemetry()

    def _boom(_event: dict[str, object]) -> None:
        raise RuntimeError("sink exploded")

    result = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="live",
            event_sink=_boom,
            telemetry=telemetry,
        )
    )

    assert result.status not in {"disabled", "shadow", "validation_failed"}
    assert telemetry.runs == 1
    assert telemetry.agents_spawned == 2


def test_telemetry_runs_zero_for_disabled_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled mode does NOT count as a run — runs counter stays 0."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=2)
    runner = _CountingChildRunner()
    telemetry = WorkflowExecutorTelemetry()

    result = asyncio.run(
        execute_workflow(
            contract,
            config=_live_config(),
            child_runner=runner,
            execution_mode="disabled",
            telemetry=telemetry,
        )
    )

    assert result.status == "disabled"
    # Disabled invocations are NOT counted in runs (fix #3).
    assert telemetry.runs == 0
    assert telemetry.agents_spawned == 0
    assert runner.calls == 0


def test_telemetry_runs_zero_for_validation_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validation_failed path does NOT count as a run — runs counter stays 0."""
    import unittest.mock as mock
    from magi_agent.workflows.compiler import WorkflowValidationVerdict

    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    contract = _valid_contract(n_recipes=1)
    runner = _CountingChildRunner()
    telemetry = WorkflowExecutorTelemetry()

    # Force validation to fail by patching validate_compiled_workflow.
    failing_verdict = WorkflowValidationVerdict(
        ok=False,
        reason_codes=("test_forced_failure",),
    )
    with mock.patch(
        "magi_agent.harness.workflow_executor.validate_compiled_workflow",
        return_value=failing_verdict,
    ):
        result = asyncio.run(
            execute_workflow(
                contract,
                config=_live_config(),
                child_runner=runner,
                telemetry=telemetry,
            )
        )

    assert result.status == "validation_failed"
    # validation_failed returns before the mode gate — runs must NOT be counted.
    assert telemetry.runs == 0
    assert telemetry.agents_spawned == 0
