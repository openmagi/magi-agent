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

Deferred follow-on work (Track F daemon)
-----------------------------------------
The following items are intentionally deferred to the Track-F daemon follow-on PR:

(a) **Periodic loop driver**: This module provides ``execute_due_jobs`` as a
    callable, but there is NO production loop driver that calls it periodically.
    A persistent daemon loop (e.g. asyncio task, thread, or process) that invokes
    ``execute_due_jobs`` on a configurable tick interval must be built by the
    Track-F daemon PR.

(b) **Persistent ScheduledJobSource**: Only ``InMemoryJobSource`` (in-memory, lost
    on restart) is provided here.  A durable source backed by a database, Redis, or
    the job_queue store must be implemented by Track F so scheduled jobs survive
    process restarts.

(c) **CronTurnRunnerAdapter**: The ``CronTurnRunner`` Protocol does NOT match the
    real ``magi_agent.adk_bridge.runner_adapter.OpenMagiRunnerAdapter`` signature
    (which takes ``RunnerTurnInput``, not ``CronTurnPlan``).  A
    ``CronTurnRunnerAdapter`` bridging ``OpenMagiRunnerAdapter.run_turn(RunnerTurnInput)``
    to ``CronTurnRunner.run_turn(CronTurnPlan)`` must be built by Track F before
    live use.

The gate stays default-OFF (``MAGI_SCHEDULER_EXECUTOR_ENABLED`` unset) until (a),
(b), and (c) are complete and the Track-F daemon has been deployed.

(d) **Per-bot canary-scope selection**: ``execute_due_jobs`` enforces env-wide
    blockers (oc-cron guard, kill-switch) but does NOT enforce per-bot canary scope
    (``selectedBotDigest``, ``selectedOwnerUserIdDigest``, ``environmentAllowlist``).
    The Track-F loop driver, which has bot/user identity, must apply the A5 readiness
    gate per-bot before calling ``execute_due_jobs``.

Forbidden imports: urllib, socket, subprocess, http, requests, magi_agent.adk_bridge,
google.adk — none appear in this module or its local import graph.
"""
from __future__ import annotations

import os
import json
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.harness.scheduler_executor import (
    ScheduledJobRecord,
    ScheduledJobSource,
    SchedulePolicy,
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

if TYPE_CHECKING:
    from magi_agent.harness.scheduler_delivery import DeliveryReceipt

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
        shadow = _env_shadow_flag("MAGI_SCHEDULER_SHADOW", default=True)
        timeout = _env_timeout("MAGI_CRON_TIMEOUT", default=_DEFAULT_CRON_TIMEOUT_SECONDS)
        return cls(
            executorEnabled=enabled,
            shadow=shadow,
            timeoutSeconds=timeout,
        )


def _env_flag(name: str, *, default: bool) -> bool:
    # I-2 PR A: delegates to the canonical truthy leaf.
    from magi_agent.config._truthy import env_bool  # noqa: PLC0415

    return env_bool(os.environ, name, default=default)


def _env_shadow_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    clean = raw.strip().lower()
    if clean in {"0", "false"}:
        return False
    return True


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
    runner_invoked: bool = Field(alias="runnerInvoked")
    output: str = Field(default="", alias="output")


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
        """Run one turn for *plan* and return its terminal status.

        Obligations for implementors (Track-F CronTurnRunnerAdapter):
        1. The runner MUST honor ``plan.disabled_toolsets`` — these tools must be
           stripped from the agent's available toolset before the turn starts.
           Failing to enforce the strip allows cron turns to fan out (CronCreate),
           send unauthorized channel messages (TelegramSend), or block on
           interactive prompts (AskUserQuestion).
        2. The runner MUST enforce ``plan.timeout_seconds`` via asyncio.wait_for
           (or equivalent) so that hung turns are aborted and ``"timed_out"`` is
           returned rather than blocking the scheduler loop indefinitely.
        3. ``runner_invoked`` must be True iff the underlying ADK runner was
           actually called (False for pre-flight rejections).
        """
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
    delivery_receipt: "DeliveryReceipt | None" = Field(default=None, alias="deliveryReceipt")


def _rebuild_job_execution() -> None:
    """Resolve the DeliveryReceipt forward reference so Pydantic can validate it.

    model_rebuild() is called once at module load time with the resolved type
    passed in via _types_namespace.  The import is inside the function to
    preserve boundary isolation — this module's top-level does not import
    scheduler_delivery.
    """
    from magi_agent.harness.scheduler_delivery import DeliveryReceipt

    JobExecution.model_rebuild(_types_namespace={"DeliveryReceipt": DeliveryReceipt})


_rebuild_job_execution()


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
    last_active_session: "object | None" = None,
    readiness_execution_mode: Literal["disabled", "shadow", "live"] | None = None,
    policy: SchedulePolicy | None = None,
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

    ``last_active_session``: Optional session-context object (a ``DeliveryTarget``
    from ``scheduler_delivery``) passed through to ``resolve_delivery_target``.
    When provided, A4's recency-win rule routes delivery to the active session
    rather than the default local log sink.  When absent (default), delivery falls
    back to the local default sink — preserving existing behavior.
    The future Track-F loop driver will supply this from its session-tracking state.
    """
    requested_config = config if config is not None else JobExecutionConfig.from_env()

    # 0a. oc-cron transition guard: if BOTH the OSS executor and external oc-cron are
    #     active at the same time we refuse to execute (double-fire risk).
    #     This guard is consulted before readiness projection so a configured
    #     scheduler request cannot consume a tick while readiness blocks it.
    if requested_config.executor_enabled:
        from magi_agent.gates.scheduler_executor_readiness import (
            check_oc_cron_transition_guard,
        )
        _guard = check_oc_cron_transition_guard(
            oss_scheduler_enabled=True,
            oc_cron_active=os.environ.get("MAGI_OC_CRON_ACTIVE", "").lower()
            in {"1", "true", "yes", "on"},
        )
        if _guard.get("conflict"):
            # Both schedulers active — true no-op. Do not tick/advance next_run.
            tick_result = _blocked_tick_result(
                now=now,
                source=source,
                reason="oc_cron_conflict",
            )
            return JobExecutionResult(
                tickResult=tick_result,
                executions=(),
            )

    resolved_config = _apply_readiness_mode(
        requested_config,
        readiness_execution_mode=readiness_execution_mode,
    )

    # 0b. Honor A5 env-level blockers (kill-switch).
    #     Per-bot canary-scope selection is applied by the future loop driver that
    #     has bot/user identity; at this layer we enforce env-wide kill-switch only.
    #     The env gate (MAGI_SCHEDULER_EXECUTOR_ENABLED) is already captured in
    #     resolved_config.executor_enabled (via from_env() or explicit config).
    #     The kill-switch is a separate emergency stop that forces shadow regardless
    #     of executor_enabled — reusing the A5 guard so execution and health cannot
    #     diverge.
    #
    #     NOTE: per-bot canary scope (selectedBotDigest, selectedOwnerUserIdDigest,
    #     environmentAllowlist) is intentionally deferred to the loop driver (Track F
    #     daemon) which has bot/user identity. Only env-wide kill-switch is enforced here.
    if resolved_config.executor_enabled:
        import os as _os
        _kill_switch = _os.environ.get("MAGI_SCHEDULER_KILL_SWITCH_ENABLED", "").lower() in {
            "1", "true", "yes", "on"
        }
        if _kill_switch:
            # Kill-switch active: force shadow so the runner is never called.
            resolved_config = JobExecutionConfig(
                executorEnabled=resolved_config.executor_enabled,
                shadow=True,
                timeoutSeconds=resolved_config.timeout_seconds,
            )

    if resolved_config.executor_enabled and not resolved_config.shadow:
        _ensure_sync_live_entrypoint_available()

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
        policy=policy,
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
        delivery_receipt = _deliver_turn_result(
            turn_result, record=record, now=now, last_active_session=last_active_session
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
                deliveryReceipt=delivery_receipt,
            )
        )

    return JobExecutionResult(tickResult=tick_result, executions=tuple(executions))


def _apply_readiness_mode(
    config: JobExecutionConfig,
    *,
    readiness_execution_mode: Literal["disabled", "shadow", "live"] | None,
) -> JobExecutionConfig:
    if not config.executor_enabled:
        return config
    if readiness_execution_mode == "disabled":
        return JobExecutionConfig(
            executorEnabled=False,
            shadow=True,
            timeoutSeconds=config.timeout_seconds,
        )
    if readiness_execution_mode == "live":
        return config
    # Missing or shadow readiness cannot grant live runner authority.
    if not config.shadow:
        return JobExecutionConfig(
            executorEnabled=True,
            shadow=True,
            timeoutSeconds=config.timeout_seconds,
        )
    return config


def _ensure_sync_live_entrypoint_available() -> None:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "_run_turn_sync called from within a running event loop; "
        "an async entrypoint will be added in A4 (delivery wiring)"
    )


def _blocked_tick_result(
    *,
    now: datetime,
    source: ScheduledJobSource,
    reason: str,
) -> SchedulerTickResult:
    try:
        skipped_ids = tuple(job.job_id for job in source.list_all())
    except Exception:
        skipped_ids = ()
    digest_payload = {
        "nowUtcIso": now.astimezone(UTC).isoformat(),
        "reason": reason,
        "firedJobIds": [],
        "skippedJobIds": sorted(skipped_ids),
        "status": "tick_completed",
        "schemaVersion": "scheduler_executor.blocked_tick.v1",
    }
    evidence_digest = "sha256:" + hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SchedulerTickResult(
        status="tick_completed",
        firedJobIds=(),
        skippedJobIds=skipped_ids,
        leaseState="valid",
        evidenceDigest=evidence_digest,
    )


def _deliver_turn_result(
    turn_result: CronTurnResult,
    *,
    record: "ScheduledJobRecord",  # noqa: F821 — imported lazily below
    now: datetime,
    last_active_session: "object | None" = None,
) -> object:
    """Call delivery boundary for a completed live turn.

    Imported lazily so scheduler_delivery's import graph does not taint this
    module's top-level (boundary isolation contract).

    ``last_active_session``: optional ``DeliveryTarget`` from scheduler_delivery
    representing the most recent active session.  When provided, A4's recency-win
    rule routes delivery to that session instead of the default local log sink.
    Default is None (local sink) — preserving existing behavior when no session
    context is available (e.g. the future loop driver has not yet wired the seam).
    """
    from magi_agent.harness.scheduler_delivery import (
        deliver,
        resolve_delivery_target,
    )

    # Skip delivery for non-completed turns (timed_out, failed, skipped).
    if turn_result.status not in {"completed"}:
        # Still emit a skipped receipt so callers can audit.  Record the ACTUAL
        # (redacted) outputLength + outputDigest of whatever partial output exists
        # — never deliver it, but the audit trail must reflect the true values.
        import hashlib as _hashlib
        from magi_agent.harness.scheduler_delivery import DeliveryReceipt
        _output: str = getattr(turn_result, "output", "") or ""
        _output_length = len(_output)
        _output_digest = (
            "sha256:" + _hashlib.sha256(_output.encode()).hexdigest()
            if _output
            else _ZERO_DIGEST  # module-local constant (not imported from scheduler_delivery)
        )
        return DeliveryReceipt(
            status="skipped",
            jobId=turn_result.job_id,
            outputLength=_output_length,
            outputDigest=_output_digest,
        )

    # Resolve target with optional session context (A4 recency-win rule).
    target = resolve_delivery_target(record, last_active_session=last_active_session)
    return deliver(turn_result, target=target)


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

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no running loop — safe to use asyncio.run below
    else:
        raise RuntimeError(
            "_run_turn_sync called from within a running event loop; "
            "an async entrypoint will be added in A4 (delivery wiring)"
        )

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
    "ScheduledJobRecord",
]
