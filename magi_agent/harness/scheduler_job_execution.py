"""A3 — Gated ADK turn execution for due scheduler jobs (shadow-first, default off).

Boundary module.  This module wires a *due* scheduler job to ACTUALLY run a
single non-interactive, auto-approved agent turn — but the wiring is gated and
shadow-first:

    MAGI_SCHEDULER_EXECUTOR_ENABLED   default OFF.  When off, behavior is exactly
                                       A2's local_fake tick (zero execution).
    MAGI_SCHEDULER_SHADOW             when the executor is enabled, default ON.
                                       Shadow builds the execution PLAN and records
                                       evidence, but does NOT invoke the runner.
                                       Real invocation only when executor ENABLED
                                       and shadow OFF.
    MAGI_CRON_TIMEOUT                  inactivity timeout, default 600s (HERMES-style).

ADK-First / injection
---------------------
The actual turn runner is injected via the ``CronTurnRunner`` Protocol.  This
module never constructs a real ADK client and never imports ``magi_agent.adk_bridge``
or ``google.adk`` — those would taint the boundary import graph (see the A2 purity
test, mirrored for this module).  The live path composes ``OpenMagiRunnerAdapter``
from ``magi_agent/adk_bridge/runner_adapter.py`` *outside* this module (the caller
builds the adapter-backed runner and passes it in).  The Protocol's ``run_turn``
mirrors that adapter's call shape: a single non-interactive turn given the plan
(prompt + disabled toolsets + timeout), returning a status.

Authority flags
--------------
All authority flags on the resulting ``SchedulerTickResult`` remain
``Literal[False]`` and are NOT flipped by code.  Gating is purely an env-evaluated
runtime branch; it never mutates the frozen flag models.

Toolset strip
------------
``CRON_DISABLED_TOOLSETS`` always disables interactive / self-scheduling tools for
cron-spawned turns (see the constant's docstring for the name mapping and why).

Evidence
--------
Every execution (shadow plan OR live run) records an ``EvidenceRecord`` (reused
from ``magi_agent.evidence``).  Shadow records the *intended* {prompt,
disabledToolsets, timeout} without running.

Forbidden imports: urllib, socket, subprocess, http, requests, magi_agent.adk_bridge,
google.adk — none appear in this module or its local import graph.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.harness.scheduler_executor import (
    ScheduledJobRecord,
    ScheduledJobSource,
    SchedulerTickResult,
    tick,
)
from magi_agent.harness.scheduler_runtime import SchedulerLease
from magi_agent.permissions.auto_control import (
    AutoPermissionConfig,
    AutoPermissionDecision,
    AutoPermissionDecisionRequest,
    AutoPermissionGuardDecision,
    evaluate_auto_permission,
)

# ---------------------------------------------------------------------------
# Module config
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

# Default inactivity timeout (seconds) for a cron-spawned turn.  HERMES-style 600s.
_DEFAULT_CRON_TIMEOUT_SECONDS = 600.0

# A zero/placeholder digest accepted by auto_control's require_digest().
_ZERO_DIGEST = "sha256:" + "0" * 64

# ---------------------------------------------------------------------------
# CRON_DISABLED_TOOLSETS
# ---------------------------------------------------------------------------
#
# Cron-spawned turns run unattended and non-interactively, so they must never be
# able to (a) schedule MORE background work (cron/task fan-out), (b) send messages
# to users/channels, or (c) block on an interactive clarify/ask-user prompt.
#
# Name mapping (Magi's tool vocabulary -> the generic category in the spec).  The
# canonical Magi tool names come from magi_agent/tools/catalog.py and the
# gate1a_readonly_tools forbidden-name list (the authoritative vocabulary):
#
#   cron/scheduler-mutation  -> "CronCreate", "CronUpdate", "CronDelete"
#       (a cron job cannot schedule more cron — no self-replication)
#   self-scheduling tasks    -> "TaskCreate", "TaskStop", "TaskWait"
#       (no spawning/awaiting background tasks from an unattended turn)
#   messaging / channel-send -> "TelegramSend", "DiscordSend", "FileDeliver", "FileSend"
#       (delivery is A4's job; an A3 turn must not push to a channel)
#   clarify / ask-user       -> "AskUserQuestion"
#       (no interactive prompt — there is no human in the loop for a cron turn)
#
CRON_DISABLED_TOOLSETS: tuple[str, ...] = (
    # cron mutation
    "CronCreate",
    "CronUpdate",
    "CronDelete",
    # self-scheduling background tasks
    "TaskCreate",
    "TaskStop",
    "TaskWait",
    # messaging / channel send
    "TelegramSend",
    "DiscordSend",
    "FileDeliver",
    "FileSend",
    # interactive clarify / ask-user
    "AskUserQuestion",
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class JobExecutionConfig(BaseModel):
    """Frozen config controlling whether/how due jobs execute a real turn."""

    model_config = _MODEL_CONFIG

    executor_enabled: bool = Field(default=False, alias="executorEnabled")
    shadow: bool = Field(default=True, alias="shadow")
    timeout_seconds: float = Field(
        default=_DEFAULT_CRON_TIMEOUT_SECONDS, alias="timeoutSeconds", gt=0
    )

    @classmethod
    def from_env(cls) -> JobExecutionConfig:
        """Build config from env (evaluated at runtime; never mutates flags).

        - MAGI_SCHEDULER_EXECUTOR_ENABLED: "1"/"true" -> enabled (default OFF).
        - MAGI_SCHEDULER_SHADOW: "0"/"false" -> live; anything else -> shadow.
          Shadow-first: when the executor is enabled but shadow is unset, default
          to shadow so the first rollout is non-invasive.
        - MAGI_CRON_TIMEOUT: positive float seconds (default 600).
        """
        enabled = _env_flag("MAGI_SCHEDULER_EXECUTOR_ENABLED", default=False)
        shadow = _env_flag("MAGI_SCHEDULER_SHADOW", default=True)
        timeout = _env_timeout("MAGI_CRON_TIMEOUT", default=_DEFAULT_CRON_TIMEOUT_SECONDS)
        return cls(
            executorEnabled=enabled,
            shadow=shadow,
            timeoutSeconds=timeout,
        )


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_timeout(name: str, *, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


# ---------------------------------------------------------------------------
# Turn plan / runner Protocol / result
# ---------------------------------------------------------------------------

class CronTurnPlan(BaseModel):
    """Immutable plan describing the single non-interactive cron turn to run."""

    model_config = _MODEL_CONFIG

    job_id: str = Field(alias="jobId")
    prompt: str = Field(alias="prompt")
    disabled_toolsets: tuple[str, ...] = Field(alias="disabledToolsets")
    timeout_seconds: float = Field(alias="timeoutSeconds", gt=0)


CronTurnStatus = Literal["completed", "timed_out", "failed", "skipped"]


class CronTurnResult(BaseModel):
    """Immutable result returned by a CronTurnRunner for a single turn."""

    model_config = _MODEL_CONFIG

    status: CronTurnStatus
    job_id: str = Field(alias="jobId")
    runner_invoked: bool = Field(default=True, alias="runnerInvoked")


@runtime_checkable
class CronTurnRunner(Protocol):
    """Seam for running a single non-interactive, auto-approved cron turn.

    The real implementation composes ``OpenMagiRunnerAdapter`` (see
    ``magi_agent/adk_bridge/runner_adapter.py``) and wraps the event collection in
    ``asyncio.wait_for(..., timeout=plan.timeout_seconds)`` — mirroring
    ``magi_agent/runtime/adk_turn_runner.py``.  Tests inject a fake so this module
    never imports ADK.
    """

    async def run_turn(self, plan: CronTurnPlan) -> CronTurnResult:
        """Run one turn for *plan* and return its terminal status."""
        ...


# ---------------------------------------------------------------------------
# Execution record + result
# ---------------------------------------------------------------------------

ExecutionMode = Literal["shadow", "live"]


class JobExecution(BaseModel):
    """Frozen record of a single due-job execution (shadow plan or live run)."""

    # arbitrary_types_allowed: AutoPermissionDecision is a sealed non-pydantic model.
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=True,
        hide_input_in_errors=True,
    )

    job_id: str = Field(alias="jobId")
    mode: ExecutionMode
    plan: CronTurnPlan
    runner_invoked: bool = Field(alias="runnerInvoked")
    status: CronTurnStatus
    approval: AutoPermissionDecision | None = Field(default=None, alias="approval")
    evidence: EvidenceRecord | None = Field(default=None, alias="evidence")


class JobExecutionResult(BaseModel):
    """Frozen aggregate: the underlying tick result plus per-job executions."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=True,
        hide_input_in_errors=True,
    )

    tick_result: SchedulerTickResult = Field(alias="tickResult")
    executions: tuple[JobExecution, ...] = Field(default=(), alias="executions")


# ---------------------------------------------------------------------------
# Plan / evidence / approval builders
# ---------------------------------------------------------------------------

def _build_plan(job: ScheduledJobRecord, *, config: JobExecutionConfig) -> CronTurnPlan:
    prompt = (
        f"Run scheduled job {job.job_id} (schedule: {job.schedule_expr}). "
        "This is an unattended, non-interactive cron turn. Do not contact the user, "
        "do not send to any channel, and do not schedule additional cron or tasks."
    )
    return CronTurnPlan(
        jobId=job.job_id,
        prompt=prompt,
        disabledToolsets=CRON_DISABLED_TOOLSETS,
        timeoutSeconds=config.timeout_seconds,
    )


def _auto_approve(job_id: str, *, now: datetime) -> AutoPermissionDecision:
    """Auto-approve a non-interactive cron turn via the auto_control pattern.

    The cron turn requests a single non-mutating "run a scheduled turn" permission
    with a passing deterministic guard.  Mutating tool authority is denied by the
    toolset strip + the auto_control mutating-marker rules, so this approval only
    grants the right to *start* the unattended turn.
    """
    guard = AutoPermissionGuardDecision(
        guardId="guard.scheduler.cron.autoapprove",
        stage="after_approval",
        hardInvariant=False,
        deterministicVerdict="pass",
        configuredMode="enforce",
    )
    request = AutoPermissionDecisionRequest(
        requestId=f"req.cron.{_safe_ref_suffix(job_id)}",
        actionRef="scheduler.cron.turn.autoapproved",
        actionDigest=_ZERO_DIGEST,
        requestedPermissionRefs=("scheduler.cron.turn.autoapproved",),
        policySnapshotDigest=_ZERO_DIGEST,
        guardDecisions=(guard,),
        adminPolicyRef="scheduler.cron.policy",
        adminPolicyDigest=_ZERO_DIGEST,
    )
    config = AutoPermissionConfig(
        enabled=True,
        autoAllowPermissionRefs=("scheduler.cron.turn.autoapproved",),
    )
    return evaluate_auto_permission(request, config=config, now=now)


def _safe_ref_suffix(job_id: str) -> str:
    # auto_control's require_safe_ref allows [A-Za-z0-9][A-Za-z0-9_.:!-]; job ids
    # like "job:abc-1" are already compatible, but normalize defensively.
    cleaned = "".join(ch if (ch.isalnum() or ch in "_.:!-") else "_" for ch in job_id)
    return cleaned or "job"


def _build_evidence(
    *,
    job_id: str,
    plan: CronTurnPlan,
    mode: ExecutionMode,
    status: CronTurnStatus,
    runner_invoked: bool,
    now: datetime,
) -> EvidenceRecord:
    evidence_status = "ok" if status in {"completed", "skipped"} else "failed"
    return EvidenceRecord(
        type="custom:SchedulerCronExecution",
        status=evidence_status,
        observedAt=int(now.astimezone(UTC).timestamp() * 1000),
        source=EvidenceSource(kind="execution_contract"),
        fields={
            "jobId": job_id,
            "mode": mode,
            "turnStatus": status,
            "runnerInvoked": runner_invoked,
            "disabledToolsets": list(plan.disabled_toolsets),
            "timeoutSeconds": plan.timeout_seconds,
            "promptDigestLen": len(plan.prompt),
        },
    )


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def execute_due_jobs(
    *,
    now: datetime,
    source: ScheduledJobSource,
    lease: SchedulerLease | None,
    owner_digest: str,
    runner: CronTurnRunner,
    config: JobExecutionConfig | None = None,
    lock_dir: Path | None = None,
) -> JobExecutionResult:
    """Run one scheduler tick, then (if gated on) execute each fired due job.

    Gate OFF (default): returns exactly the A2 ``tick()`` result and no executions
    — byte-for-byte preserving A2 behavior (the runner is never touched).

    Shadow (executor on, shadow on): builds the per-job plan + evidence, records
    the intended turn, but does NOT invoke the runner.

    Live (executor on, shadow off): auto-approves the non-interactive turn, then
    invokes the injected runner with the stripped toolsets + inactivity timeout,
    and records evidence for the resulting status (including timeouts).

    The frozen authority flags on the tick result are never mutated; the gate is a
    runtime env branch only.  Lease/lock failures from A2 still short-circuit (no
    execution without a valid lease).
    """
    resolved_config = config if config is not None else JobExecutionConfig.from_env()

    # 1. A2 tick is the single source of truth for "what fired".  We capture the
    #    fired records so an enabled executor can act on exactly those jobs.
    fired_records: list[ScheduledJobRecord] = []
    # list_all() is the Protocol enumeration seam; due records identified by id.
    # Only materialized when the executor is enabled (else the capture hook is
    # never wired and A2 behavior is byte-for-byte preserved).
    all_by_id: dict[str, ScheduledJobRecord] = (
        {job.job_id: job for job in source.list_all()}
        if resolved_config.executor_enabled
        else {}
    )

    def _capture(job_id: str, _receipt: dict[str, object]) -> None:
        record = all_by_id.get(job_id)
        if record is not None:
            fired_records.append(record)

    tick_result = tick(
        now=now,
        source=source,
        lease=lease,
        lock_dir=lock_dir,
        owner_digest=owner_digest,
        _on_receipt=_capture if resolved_config.executor_enabled else None,
    )

    # 2. Gate OFF, or nothing fired, or lease blocked -> no execution.
    if not resolved_config.executor_enabled or not fired_records:
        return JobExecutionResult(tickResult=tick_result, executions=())

    executions: list[JobExecution] = []
    for record in fired_records:
        plan = _build_plan(record, config=resolved_config)

        if resolved_config.shadow:
            evidence = _build_evidence(
                job_id=record.job_id,
                plan=plan,
                mode="shadow",
                status="skipped",
                runner_invoked=False,
                now=now,
            )
            executions.append(
                JobExecution(
                    jobId=record.job_id,
                    mode="shadow",
                    plan=plan,
                    runnerInvoked=False,
                    status="skipped",
                    approval=None,
                    evidence=evidence,
                )
            )
            continue

        # Live path: auto-approve, then invoke the injected runner under timeout.
        approval = _auto_approve(record.job_id, now=now)
        if not approval.allowed:
            evidence = _build_evidence(
                job_id=record.job_id,
                plan=plan,
                mode="live",
                status="skipped",
                runner_invoked=False,
                now=now,
            )
            executions.append(
                JobExecution(
                    jobId=record.job_id,
                    mode="live",
                    plan=plan,
                    runnerInvoked=False,
                    status="skipped",
                    approval=approval,
                    evidence=evidence,
                )
            )
            continue

        turn_result = _run_turn_sync(runner, plan)
        evidence = _build_evidence(
            job_id=record.job_id,
            plan=plan,
            mode="live",
            status=turn_result.status,
            runner_invoked=turn_result.runner_invoked,
            now=now,
        )
        executions.append(
            JobExecution(
                jobId=record.job_id,
                mode="live",
                plan=plan,
                runnerInvoked=turn_result.runner_invoked,
                status=turn_result.status,
                approval=approval,
                evidence=evidence,
            )
        )

    return JobExecutionResult(tickResult=tick_result, executions=tuple(executions))


def _run_turn_sync(runner: CronTurnRunner, plan: CronTurnPlan) -> CronTurnResult:
    """Drive the async runner.run_turn to completion from sync code, enforcing timeout.

    ``execute_due_jobs`` is a sync entrypoint mirroring A2's ``tick`` (the scheduler
    loop drives it off the event loop), so ``asyncio.run`` is correct here.  A
    caller that already owns a running loop should await the runner directly; A4
    (delivery wiring) can add an async entrypoint if needed.  asyncio is imported
    lazily so the module top-level stays minimal and import-clean.

    The inactivity timeout is enforced HERE via ``asyncio.wait_for`` — a runner that
    never returns is aborted by this module (not by the runner itself).  On timeout
    a ``CronTurnResult`` with status ``"timed_out"`` is returned so the caller can
    record evidence and continue to the next job without hanging.
    """
    import asyncio

    async def _run_with_timeout() -> CronTurnResult:
        try:
            return await asyncio.wait_for(
                runner.run_turn(plan), timeout=plan.timeout_seconds
            )
        except asyncio.TimeoutError:
            return CronTurnResult(
                status="timed_out",
                jobId=plan.job_id,
                runnerInvoked=True,
            )

    return asyncio.run(_run_with_timeout())


__all__ = [
    "CRON_DISABLED_TOOLSETS",
    "CronTurnPlan",
    "CronTurnResult",
    "CronTurnRunner",
    "CronTurnStatus",
    "ExecutionMode",
    "JobExecution",
    "JobExecutionConfig",
    "JobExecutionResult",
    "execute_due_jobs",
]
