"""A-driver — CronTurnRunnerAdapter: OpenMagiRunnerAdapter -> CronTurnRunner bridge.

This is the (c) deliverable from the Track-F deferral list in
``scheduler_job_execution.py``.  The ``CronTurnRunner`` Protocol that
``execute_due_jobs`` expects takes a ``CronTurnPlan`` and returns a
``CronTurnResult``.  The real turn engine is
``magi_agent.adk_bridge.runner_adapter.OpenMagiRunnerAdapter`` whose
``collect_events(RunnerTurnInput) -> list[event]`` shape does NOT match.  This
adapter bridges the two:

    CronTurnPlan  --synthesize-->  RunnerTurnInput
                  --collect_events(OpenMagiRunnerAdapter)-->  list[event]
                  --map-->  CronTurnResult

Injection / import purity
-------------------------
The ``OpenMagiRunnerAdapter`` (which owns the real ADK runner) is INJECTED via the
constructor; this module never constructs an ADK client and never imports
``google.adk`` at module top level.  ``google.genai.types`` and
``build_default_resolved_harness_state`` are imported LAZILY inside the synthesis
helper so importing this module does not taint the boundary import graph (mirrors
the A3 purity contract).  Tests inject a fake adapter exposing only
``collect_events``.

disabled_toolsets enforcement (the strip contract)
--------------------------------------------------
The cron toolset-strip contract (``CRON_DISABLED_TOOLSETS`` via
``plan.disabled_toolsets``) must be honored BEFORE the child turn.  The
``OpenMagiRunnerAdapter`` wraps an ADK agent whose toolset is fixed at
construction time — so the actual strip must be applied when that agent is built
(by the caller / Track-F daemon).  This adapter ENFORCES the contract at the
boundary: when an ``exposed_toolsets_provider`` is supplied, the adapter checks
that none of ``plan.disabled_toolsets`` are exposed by the wrapped agent BEFORE
invoking it.  If a disabled toolset is still exposed, the turn is rejected
pre-flight with ``status="failed"`` and ``runner_invoked=False`` — the runner is
never called, so a mis-built agent cannot fan out (CronCreate), send to a channel
(TelegramSend), or block on an interactive prompt (AskUserQuestion).  When no
provider is supplied, the strip is the caller's documented responsibility (the
wrapped agent MUST be pre-stripped); the adapter still passes the disabled list
through identity so an introspecting caller can audit it.

Error / timeout mapping
-----------------------
- A runner exception mid-turn maps to ``status="failed"`` with
  ``runner_invoked=True`` (the failure happened after invocation).
- The inactivity timeout is NOT owned here: ``execute_due_jobs`` /
  ``_run_turn_sync`` wraps ``run_turn`` in ``asyncio.wait_for(...,
  timeout=plan.timeout_seconds)`` and converts a TimeoutError into a
  ``timed_out`` result.  This adapter simply runs to completion; a hung wrapped
  runner is aborted by that outer wait_for.

Forbidden imports: google.adk, urllib, socket, subprocess, http, requests at
module top level — verified by test.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from typing import Protocol

from magi_agent.harness.scheduler_job_execution import CronTurnPlan, CronTurnResult


class _RunnerAdapterPort(Protocol):
    """Minimal seam the adapter needs from OpenMagiRunnerAdapter."""

    async def collect_events(self, turn_input: object) -> list[object]:
        ...


def _safe_session_suffix(job_id: str) -> str:
    """Normalize a job id into an identifier-safe, collision-free session/invocation suffix.

    A short sha256 hash of the ORIGINAL job_id is appended so that distinct ids
    differing only by separator (``job:a`` vs ``job-a`` vs ``job_a``) all produce
    *different* suffixes.  Without the hash all three normalize to ``job_a``, which
    would assign identical session_id/turn_id/invocation_id to different jobs and
    corrupt each other's ADK session state.
    """
    h = hashlib.sha256(job_id.encode()).hexdigest()[:8]
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "_", job_id)
    return f"{cleaned or 'job'}:{h}"


class CronTurnRunnerAdapter:
    """Bridges ``OpenMagiRunnerAdapter`` to the ``CronTurnRunner`` Protocol.

    Parameters
    ----------
    runner_adapter:
        The injected ``OpenMagiRunnerAdapter`` (or any object exposing an async
        ``collect_events(RunnerTurnInput) -> list[event]``).  Owns the real ADK
        runner; never constructed here.
    exposed_toolsets_provider:
        Optional callable returning the toolset names the wrapped agent currently
        exposes.  When supplied, the adapter enforces the strip contract by
        rejecting (pre-flight ``failed``) any turn whose ``plan.disabled_toolsets``
        intersect the exposed set.  When ``None``, the strip is the caller's
        documented responsibility.
    """

    def __init__(
        self,
        *,
        runner_adapter: _RunnerAdapterPort,
        exposed_toolsets_provider: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self._runner_adapter = runner_adapter
        self._exposed_toolsets_provider = exposed_toolsets_provider

    async def run_turn(self, plan: CronTurnPlan) -> CronTurnResult:
        # 1. Honor the toolset-strip contract BEFORE invoking the runner.
        violation = self._disabled_toolset_violation(plan)
        if violation is not None:
            return CronTurnResult(
                status="failed",
                jobId=plan.job_id,
                runnerInvoked=False,
                output="",
            )

        # 2. Synthesize the RunnerTurnInput from the cron plan.
        turn_input = self._build_turn_input(plan)

        # 3. Invoke the injected adapter; map exceptions -> failed (runner WAS hit).
        try:
            events = await self._runner_adapter.collect_events(turn_input)
        except Exception:  # noqa: BLE001 — any runner error is a failed turn.
            return CronTurnResult(
                status="failed",
                jobId=plan.job_id,
                runnerInvoked=True,
                output="",
            )

        # 4. Map the collected events into a terminal CronTurnResult.
        return CronTurnResult(
            status="completed",
            jobId=plan.job_id,
            runnerInvoked=True,
            output=_summarize_events(events),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _disabled_toolset_violation(self, plan: CronTurnPlan) -> str | None:
        if self._exposed_toolsets_provider is None:
            return None
        exposed = set(self._exposed_toolsets_provider())
        for name in plan.disabled_toolsets:
            if name in exposed:
                return name
        return None

    def _build_turn_input(self, plan: CronTurnPlan) -> object:
        """Synthesize a RunnerTurnInput for an unattended cron turn.

        ADK identity for a cron turn is deterministic and self-contained: there is
        no human user and no pre-existing chat session, so we derive a stable
        synthetic ``user_id``/``session_id``/``invocation_id`` from the job id.  The
        prompt becomes the ADK ``Content`` ``new_message``.  ``harness_state`` is a
        default resolved snapshot (general role, spawn_depth=1 — a cron turn is a
        child, not a human-initiated main turn).  ADK imports are lazy to preserve
        boundary import purity.
        """
        from google.genai import types  # lazy: keeps module top-level ADK-free
        from magi_agent.adk_bridge.runner_adapter import RunnerTurnInput
        from magi_agent.harness.resolved import build_default_resolved_harness_state

        suffix = _safe_session_suffix(plan.job_id)
        harness_state = build_default_resolved_harness_state(
            agent_role="general",
            spawn_depth=1,
        )
        return RunnerTurnInput(
            userId=f"scheduler:cron:{suffix}",
            sessionId=f"agent:cron:{suffix}",
            turnId=f"cron-turn:{suffix}",
            invocationId=f"cron-inv:{suffix}",
            newMessage=types.Content(role="user", parts=[types.Part(text=plan.prompt)]),
            harnessState=harness_state,
        )


def _summarize_events(events: list[object]) -> str:
    """Produce a compact, redaction-safe digest of the collected events.

    We never surface raw event payloads as the cron turn output (delivery is A4's
    job and must stay redaction-safe); a stable sha256 digest of the event count +
    string forms is enough for audit/evidence while leaking nothing.
    """
    if not events:
        return ""
    joined = "␟".join(repr(event) for event in events)
    digest = hashlib.sha256(joined.encode()).hexdigest()
    return f"events:{len(events)}:sha256:{digest}"


__all__ = ["CronTurnRunnerAdapter"]
