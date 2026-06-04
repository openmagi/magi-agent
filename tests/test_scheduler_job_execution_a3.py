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
    assert "CronCreate" in tuple(fields.get("disabledToolsets", ()))

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
    )

    assert len(runner.calls) == 1
    ex = result.executions[0]
    assert ex.status == "timed_out"
    assert ex.runner_invoked is True
    # Evidence captures the failed status for an aborted turn.
    assert ex.evidence is not None
    assert ex.evidence.status in {"failed", "unknown"}


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
