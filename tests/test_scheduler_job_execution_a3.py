"""A3 — Gated ADK turn execution for due scheduler jobs (shadow-first, default off).

TDD: RED → GREEN → REFACTOR

Behavior matrix under test:
  - gate OFF (default): no runner call, A2 local_fake behavior byte-for-byte preserved.
  - executor ON + shadow ON: build the execution PLAN, record shadow evidence,
    runner NOT called; stripped toolsets present in plan/evidence.
  - executor ON + shadow OFF (live): fake runner invoked with stripped toolsets +
    inactivity timeout; auto-approve decision applied (and allowed).
  - timeout path: a turn whose runner reports a timeout is recorded as aborted.
  - module import purity: no live ADK / network imports at module top level.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _now_dt(ms: int = 1_000_000) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _make_lease(*, owner_digest: str = "owner:test-abc", now_ms: int = 1_000_000) -> Any:
    from magi_agent.harness.scheduler_runtime import SchedulerLease

    return SchedulerLease(
        leaseId="lease:test-abc",
        ownerDigest=owner_digest,
        acquiredAt=now_ms - 1000,
        expiresAt=now_ms + 60_000,
    )


def _make_source(jobs: list[dict[str, Any]]) -> Any:
    from magi_agent.harness.scheduler_executor import InMemoryJobSource, ScheduledJobRecord

    records: list[ScheduledJobRecord] = []
    for j in jobs:
        records.append(
            ScheduledJobRecord(
                jobId=j["job_id"],
                scheduleExpr=j["schedule_expr"],
                lastFire=j.get("last_fire"),
                nextRun=datetime.fromtimestamp(j["next_run_ms"] / 1000, tz=UTC),
            )
        )
    return InMemoryJobSource(records)


class _FakeCronTurnRunner:
    """Records each turn plan it is asked to run; returns a configurable result.

    Mirrors the CronTurnRunner Protocol: a single async ``run_turn(plan)`` call
    returning a CronTurnResult-shaped object.
    """

    def __init__(self, *, status: str = "completed") -> None:
        self.calls: list[Any] = []
        self._status = status

    async def run_turn(self, plan: Any) -> Any:
        from magi_agent.harness.scheduler_job_execution import CronTurnResult

        self.calls.append(plan)
        return CronTurnResult(
            status=self._status,  # type: ignore[arg-type]
            jobId=plan.job_id,
            runnerInvoked=True,
        )


# ---------------------------------------------------------------------------
# Import / model shape
# ---------------------------------------------------------------------------

def test_module_imports_cleanly() -> None:
    from magi_agent.harness.scheduler_job_execution import (  # noqa: F401
        CRON_DISABLED_TOOLSETS,
        CronTurnPlan,
        CronTurnResult,
        CronTurnRunner,
        JobExecutionConfig,
        execute_due_jobs,
    )


def test_cron_disabled_toolsets_includes_mutation_messaging_clarify() -> None:
    from magi_agent.harness.scheduler_job_execution import CRON_DISABLED_TOOLSETS

    # Cron-mutation tools so a cron job cannot schedule more cron.
    assert "CronCreate" in CRON_DISABLED_TOOLSETS
    assert "CronUpdate" in CRON_DISABLED_TOOLSETS
    assert "CronDelete" in CRON_DISABLED_TOOLSETS
    # Messaging / channel-send tools.
    assert "TelegramSend" in CRON_DISABLED_TOOLSETS
    assert "DiscordSend" in CRON_DISABLED_TOOLSETS
    # Clarify / interactive ask-user tool.
    assert "AskUserQuestion" in CRON_DISABLED_TOOLSETS
    # Self-scheduling background-task spawn.
    assert "TaskCreate" in CRON_DISABLED_TOOLSETS


def test_cron_turn_plan_is_frozen() -> None:
    from magi_agent.harness.scheduler_job_execution import CronTurnPlan

    plan = CronTurnPlan(
        jobId="job:abc",
        prompt="run scheduled job job:abc",
        disabledToolsets=("CronCreate",),
        timeoutSeconds=600.0,
    )
    with pytest.raises(Exception):
        plan.job_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Gate OFF (default) — no runner call, A2 behavior intact
# ---------------------------------------------------------------------------

def test_gate_off_does_not_call_runner_and_matches_a2(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_executor import tick
    from magi_agent.harness.scheduler_job_execution import execute_due_jobs

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner()

    jobs = [{"job_id": "job:off-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]

    # A3 with gate OFF (config default disabled).
    source_a3 = _make_source(jobs)
    a3_result = execute_due_jobs(
        now=now,
        source=source_a3,
        lease=lease,
        lock_dir=tmp_path,
        owner_digest="owner:test-abc",
        runner=runner,
        config=None,  # default -> disabled
    )

    # Pure A2 baseline (same inputs).
    source_a2 = _make_source(jobs)
    a2_result = tick(
        now=now, source=source_a2, lease=lease, lock_dir=tmp_path, owner_digest="owner:test-abc"
    )

    assert runner.calls == [], "runner must not be called when executor is disabled"
    # The tick result mirrors A2 exactly (same evidence digest, fired ids).
    assert a3_result.tick_result.public_projection() == a2_result.public_projection()
    assert a3_result.executions == ()
    assert "job:off-001" in a3_result.tick_result.fired_job_ids


def test_gate_off_env_default(tmp_path: Any, monkeypatch: Any) -> None:
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    config = JobExecutionConfig.from_env()
    assert config.executor_enabled is False

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner()
    source = _make_source(
        [{"job_id": "job:envoff", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )
    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )
    assert runner.calls == []
    assert result.executions == ()


# ---------------------------------------------------------------------------
# Shadow mode — plan + evidence recorded, runner NOT called
# ---------------------------------------------------------------------------

def test_shadow_records_plan_evidence_without_running(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner()

    config = JobExecutionConfig(executor_enabled=True, shadow=True, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:shadow-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )

    assert runner.calls == [], "shadow mode must NOT invoke the runner"
    assert len(result.executions) == 1
    ex = result.executions[0]
    assert ex.job_id == "job:shadow-001"
    assert ex.mode == "shadow"
    assert ex.runner_invoked is False

    # Plan present with stripped toolsets + timeout.
    assert "CronCreate" in ex.plan.disabled_toolsets
    assert "TelegramSend" in ex.plan.disabled_toolsets
    assert "AskUserQuestion" in ex.plan.disabled_toolsets
    assert ex.plan.timeout_seconds == 600.0

    # Evidence record present and carries the intended plan.
    assert ex.evidence is not None
    assert ex.evidence.type.startswith("custom:")
    fields = dict(ex.evidence.fields)
    assert fields.get("mode") == "shadow"
    assert fields.get("runnerInvoked") is False


def test_live_config_without_readiness_mode_is_forced_to_shadow(tmp_path: Any) -> None:
    """Live config alone must not bypass the A5 readiness/canary ladder."""
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:readiness-shadow", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now,
        source=source,
        lease=lease,
        lock_dir=tmp_path,
        owner_digest="owner:test-abc",
        runner=runner,
        config=config,
    )

    assert runner.calls == []
    assert len(result.executions) == 1
    assert result.executions[0].mode == "shadow"
    assert result.executions[0].runner_invoked is False

    # Authority flags remain all-False (frozen, not flipped by code).
    flags = result.tick_result.authority_flags.model_dump(by_alias=True).values()
    assert all(v is False for v in flags)


# ---------------------------------------------------------------------------
# Live mode (executor on, shadow off) — runner invoked with stripped toolsets
# ---------------------------------------------------------------------------

def test_live_invokes_runner_with_stripped_toolsets_and_timeout(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:live-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )

    assert len(runner.calls) == 1, "live mode must invoke the runner exactly once"
    plan = runner.calls[0]
    assert plan.job_id == "job:live-001"
    # Toolset strip is applied to the live plan.
    for forbidden in ("CronCreate", "CronUpdate", "CronDelete", "TelegramSend", "AskUserQuestion"):
        assert forbidden in plan.disabled_toolsets
    assert plan.timeout_seconds == 600.0

    ex = result.executions[0]
    assert ex.mode == "live"
    assert ex.runner_invoked is True
    assert ex.status == "completed"
    assert ex.evidence is not None


def test_live_timeout_is_recorded_as_aborted(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    # Runner reports a timeout for the turn.
    runner = _FakeCronTurnRunner(status="timed_out")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:timeout-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )

    assert len(runner.calls) == 1
    ex = result.executions[0]
    assert ex.status == "timed_out"
    assert ex.runner_invoked is True
    # Evidence captures the failed status for an aborted turn.
    # timed_out deterministically maps to "failed" in _build_evidence.
    assert ex.evidence is not None
    assert ex.evidence.status == "failed"


def test_live_auto_approve_decision_is_allowed(tmp_path: Any) -> None:
    """The cron turn is auto-approved (non-interactive) via the auto_control pattern."""
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner()

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:approve-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )
    ex = result.executions[0]
    assert ex.approval is not None
    assert ex.approval.allowed is True
    assert ex.approval.requires_approval is False
    # Runner only invoked because approval was granted.
    assert len(runner.calls) == 1


# ---------------------------------------------------------------------------
# Lease rejection still blocks under A3 (no execution without valid lease)
# ---------------------------------------------------------------------------

def test_blocked_lease_skips_execution_even_when_enabled(tmp_path: Any) -> None:
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    runner = _FakeCronTurnRunner()
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:nolease", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=None, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )
    assert result.tick_result.status == "tick_blocked_lease"
    assert runner.calls == []
    assert result.executions == ()


# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------

def test_config_from_env_live_and_timeout(monkeypatch: Any) -> None:
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "0")
    monkeypatch.setenv("MAGI_CRON_TIMEOUT", "120")
    config = JobExecutionConfig.from_env()
    assert config.executor_enabled is True
    assert config.shadow is False
    assert config.timeout_seconds == 120.0


def test_config_from_env_defaults_to_shadow_when_enabled(monkeypatch: Any) -> None:
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.delenv("MAGI_SCHEDULER_SHADOW", raising=False)
    monkeypatch.delenv("MAGI_CRON_TIMEOUT", raising=False)
    config = JobExecutionConfig.from_env()
    assert config.executor_enabled is True
    # Shadow-first: when executor is enabled but shadow not explicitly disabled,
    # default to shadow.
    assert config.shadow is True
    assert config.timeout_seconds == 600.0


def test_config_from_env_garbage_shadow_value_stays_shadow(monkeypatch: Any) -> None:
    """A typo in MAGI_SCHEDULER_SHADOW must fail safe to shadow mode."""
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig

    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_SCHEDULER_SHADOW", "xyz")
    config = JobExecutionConfig.from_env()

    assert config.executor_enabled is True
    assert config.shadow is True


# ---------------------------------------------------------------------------
# Module import purity
# ---------------------------------------------------------------------------

def test_scheduler_job_execution_no_live_adk_imports() -> None:
    """The A3 module must not import ADK runners / network libs at top level.

    Same boundary contract as A2's scheduler_executor purity test.  The real ADK
    runner is INJECTED (CronTurnRunner Protocol), so the module graph stays clean.
    """
    import ast
    import subprocess
    import sys
    from pathlib import Path

    src = (
        Path(__file__).parent.parent
        / "magi_agent"
        / "harness"
        / "scheduler_job_execution.py"
    )
    tree = ast.parse(src.read_text())
    direct_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                direct_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                direct_imports.add(node.module.split(".")[0])
    dangerous_direct = {"urllib", "socket", "subprocess"} & direct_imports
    assert not dangerous_direct, (
        f"scheduler_job_execution directly imports dangerous stdlib: {dangerous_direct}"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.scheduler_job_execution")

forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "magi_agent.adk_bridge",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.chat_proxy",
    "magi_agent.runtime_selector",
    "magi_agent.k8s",
    "kubernetes",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "aiohttp",
    "playwright",
    "selenium",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden live/infra modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


# ---------------------------------------------------------------------------
# Module-enforced timeout — the module aborts a hung runner via asyncio.wait_for
# ---------------------------------------------------------------------------

def test_module_enforces_inactivity_timeout_not_runner(tmp_path: Any) -> None:
    """Prove that THIS module enforces the timeout — not the fake runner.

    The fake runner sleeps far longer than the configured timeout (1s vs 0.05s).
    If the module did NOT enforce the timeout the test would hang for 1 second and
    the result status would come from whatever the runner returns (which here would
    never return in time).  Instead the module must abort the coroutine via
    asyncio.wait_for and return status="timed_out" quickly.
    """
    import asyncio

    from magi_agent.harness.scheduler_job_execution import (
        CronTurnResult,
        JobExecutionConfig,
        execute_due_jobs,
    )

    class _HungRunner:
        """Runner that sleeps longer than the configured timeout (simulates hung turn)."""

        def __init__(self) -> None:
            self.started = False

        async def run_turn(self, plan: Any) -> CronTurnResult:
            self.started = True
            # Sleep 1 second — much longer than the 0.05s timeout below.
            await asyncio.sleep(1.0)
            # This line must never be reached; the module must abort the coroutine.
            return CronTurnResult(status="completed", jobId=plan.job_id, runnerInvoked=True)

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    hung_runner = _HungRunner()

    # Small timeout (50 ms) so the test stays fast.
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=0.05)
    source = _make_source(
        [{"job_id": "job:hung-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=hung_runner, config=config,
        readiness_execution_mode="live",
    )

    # The runner was started (the coroutine was entered).
    assert hung_runner.started, "runner coroutine was never entered"

    # The module must have aborted it — exactly one execution recorded.
    assert len(result.executions) == 1
    ex = result.executions[0]

    # Status must be timed_out — the runner never completed so the only way to get
    # this is if the MODULE enforced the timeout (not the runner echoing back).
    assert ex.status == "timed_out", (
        f"expected timed_out but got {ex.status!r}; module may not be enforcing timeout"
    )
    assert ex.runner_invoked is True

    # Evidence must reflect the failure.
    # timed_out deterministically maps to "failed" in _build_evidence.
    assert ex.evidence is not None
    assert ex.evidence.status == "failed", (
        f"evidence status should be 'failed' for a timed-out turn, got {ex.evidence.status!r}"
    )


# ---------------------------------------------------------------------------
# asyncio.run running-loop bomb guard
# ---------------------------------------------------------------------------

def test_execute_due_jobs_from_running_loop_raises_actionable_error(tmp_path: Any) -> None:
    """execute_due_jobs (live config) called from inside asyncio.run must raise
    RuntimeError with the actionable A4 message instead of a cryptic one.
    """
    import asyncio

    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:loop-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    raised: list[Exception] = []

    async def _inner() -> None:
        try:
            execute_due_jobs(
                now=now,
                source=source,
                lease=lease,
                lock_dir=tmp_path,
                owner_digest="owner:test-abc",
                runner=runner,
                config=config,
        readiness_execution_mode="live",
            )
        except RuntimeError as exc:
            raised.append(exc)

    asyncio.run(_inner())

    assert len(raised) == 1, "expected exactly one RuntimeError from the running-loop guard"
    msg = str(raised[0])
    assert "_run_turn_sync called from within a running event loop" in msg, (
        f"expected actionable guard message, got: {msg!r}"
    )
    assert "A4" in msg, f"expected A4 mention in guard message, got: {msg!r}"


def test_running_loop_guard_does_not_advance_job_before_error(tmp_path: Any) -> None:
    """Live sync entrypoint called in an event loop must fail before A2 tick."""
    import asyncio

    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:loop-no-consume", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    async def _inner() -> None:
        execute_due_jobs(
            now=now,
            source=source,
            lease=lease,
            lock_dir=tmp_path,
            owner_digest="owner:test-abc",
            runner=runner,
            config=config,
            readiness_execution_mode="live",
        )

    with pytest.raises(RuntimeError, match="_run_turn_sync"):
        asyncio.run(_inner())

    assert "job:loop-no-consume" in [job.job_id for job in source.due_jobs(now)]
    assert runner.calls == []


# ---------------------------------------------------------------------------
# Multi-job — two due jobs in one tick, both executed
# ---------------------------------------------------------------------------

def test_two_due_jobs_both_executed_in_tick_order(tmp_path: Any) -> None:
    """Two due jobs in one tick (live mode, fake runner): both turns executed
    and executions match tick's fired order.
    """
    from magi_agent.harness.scheduler_job_execution import (
        JobExecutionConfig,
        execute_due_jobs,
    )

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [
            {"job_id": "job:multi-001", "schedule_expr": "every 10m", "next_run_ms": now_ms - 1000},
            {"job_id": "job:multi-002", "schedule_expr": "every 5m", "next_run_ms": now_ms - 500},
        ]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )

    # Both jobs must have been executed.
    assert len(result.executions) == 2, (
        f"expected 2 executions, got {len(result.executions)}"
    )
    assert len(runner.calls) == 2, (
        f"expected runner called twice, got {len(runner.calls)}"
    )

    # Both executions are live and completed.
    for ex in result.executions:
        assert ex.mode == "live"
        assert ex.status == "completed"
        assert ex.runner_invoked is True

    # Execution order matches the fired_job_ids order from the tick.
    fired_ids = list(result.tick_result.fired_job_ids)
    executed_ids = [ex.job_id for ex in result.executions]
    assert executed_ids == fired_ids, (
        f"execution order {executed_ids!r} does not match tick fired order {fired_ids!r}"
    )


# ---------------------------------------------------------------------------
# G1.1 — oc-cron transition guard wired into execute_due_jobs
# ---------------------------------------------------------------------------

def test_both_active_env_no_runner_call(tmp_path: Any, monkeypatch: Any) -> None:
    """When both OSS executor AND oc-cron are active (conflict), execute_due_jobs
    must refuse to execute — runner must NOT be called.

    This verifies that check_oc_cron_transition_guard_from_env() is consulted
    at the start of execute_due_jobs when the executor is enabled, and that a
    conflict causes a safe no-op (tick advances next_run but no execution occurs).
    """
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    # Simulate both OSS executor and legacy oc-cron active.
    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_OC_CRON_ACTIVE", "1")

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")

    # config.executor_enabled=True so the guard path is entered.
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:oc-cron-conflict", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )

    # Guard must have refused execution.
    assert runner.calls == [], (
        "runner must NOT be called when both OSS executor and oc-cron are active (double-fire risk)"
    )
    # No executions in the result.
    assert result.executions == (), (
        "executions must be empty when the oc-cron transition guard fires"
    )
    # The tick still ran (tick_result is present).
    assert result.tick_result is not None


def test_oc_cron_conflict_with_explicit_config_does_not_advance(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The transition guard must be a true no-op even for explicit live config."""
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_OC_CRON_ACTIVE", "1")

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:oc-cron-no-advance", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now,
        source=source,
        lease=lease,
        lock_dir=tmp_path,
        owner_digest="owner:test-abc",
        runner=runner,
        config=config,
        readiness_execution_mode="live",
    )

    assert result.executions == ()
    assert runner.calls == []
    assert "job:oc-cron-no-advance" in [job.job_id for job in source.due_jobs(now)]


def test_oc_cron_conflict_blocks_before_disabled_readiness_tick(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Readiness-disabled live config must not consume a tick during oc-cron overlap."""
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    monkeypatch.delenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_OC_CRON_ACTIVE", "1")

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")
    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [
            {
                "job_id": "job:oc-readiness-disabled",
                "schedule_expr": "every 10m",
                "next_run_ms": now_ms - 500,
            }
        ]
    )

    result = execute_due_jobs(
        now=now,
        source=source,
        lease=lease,
        lock_dir=tmp_path,
        owner_digest="owner:test-abc",
        runner=runner,
        config=config,
        readiness_execution_mode="disabled",
    )

    assert result.executions == ()
    assert result.tick_result.fired_job_ids == ()
    assert runner.calls == []
    assert "job:oc-readiness-disabled" in [job.job_id for job in source.due_jobs(now)]


def test_oss_only_active_no_guard_block(tmp_path: Any, monkeypatch: Any) -> None:
    """When only the OSS executor is active (oc-cron off), the guard must NOT block.

    The runner must be called normally.
    """
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    monkeypatch.setenv("MAGI_SCHEDULER_EXECUTOR_ENABLED", "1")
    monkeypatch.delenv("MAGI_OC_CRON_ACTIVE", raising=False)

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:oss-only", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )

    # With only OSS executor active, the guard must not block.
    assert len(runner.calls) == 1, "runner must be called when only OSS executor is active"


# ---------------------------------------------------------------------------
# G1.2 — Kill-switch forces shadow (no runner call)
# ---------------------------------------------------------------------------

def test_kill_switch_on_forces_shadow_no_runner_call(tmp_path: Any, monkeypatch: Any) -> None:
    """When MAGI_SCHEDULER_KILL_SWITCH_ENABLED=1, execute_due_jobs must force shadow
    and NOT call the runner even if executor_enabled=True and shadow=False.
    """
    from magi_agent.harness.scheduler_job_execution import JobExecutionConfig, execute_due_jobs

    monkeypatch.setenv("MAGI_SCHEDULER_KILL_SWITCH_ENABLED", "1")
    monkeypatch.delenv("MAGI_OC_CRON_ACTIVE", raising=False)

    now_ms = 1_000_000
    now = _now_dt(now_ms)
    lease = _make_lease(now_ms=now_ms)
    runner = _FakeCronTurnRunner(status="completed")

    config = JobExecutionConfig(executor_enabled=True, shadow=False, timeout_seconds=600.0)
    source = _make_source(
        [{"job_id": "job:kill-switch", "schedule_expr": "every 10m", "next_run_ms": now_ms - 500}]
    )

    result = execute_due_jobs(
        now=now, source=source, lease=lease, lock_dir=tmp_path,
        owner_digest="owner:test-abc", runner=runner, config=config,
        readiness_execution_mode="live",
    )

    # Kill-switch must force shadow — runner must NOT be called.
    assert runner.calls == [], (
        "runner must NOT be called when MAGI_SCHEDULER_KILL_SWITCH_ENABLED=1"
    )
    # Executions are present (shadow mode) but runner_invoked must be False.
    assert len(result.executions) == 1
    assert result.executions[0].mode == "shadow"
    assert result.executions[0].runner_invoked is False
