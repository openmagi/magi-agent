"""AdkWorkTaskRunner: OpenMagiRunnerAdapter -> WorkTaskRunner bridge.

Mirrors ``magi_agent/harness/cron_turn_runner_adapter.py`` for work-queue tasks.
The bridge:

    WorkTask  --synthesize-->  RunnerTurnInput
              --collect_events(OpenMagiRunnerAdapter)-->  list[event]
              --map-->  WorkTaskRunResult

Injection / import purity
-------------------------
The ``OpenMagiRunnerAdapter`` (which owns the real ADK runner) is INJECTED via
the constructor; this module never constructs an ADK client and never imports
``google.adk`` at module top level.  ``google.genai.types`` and
``RunnerTurnInput`` are imported LAZILY inside the synthesis helper so
importing this module does not taint the boundary import graph.  Tests inject
a fake adapter exposing only ``collect_events``.

Error / timeout mapping
-----------------------
- Empty event list  →  ``outcome="failed"``, ``error="no events returned"``
- Runner exception  →  ``outcome="failed"``, ``error=f"adk error: {exc}"``
- asyncio.TimeoutError  →  ``outcome="failed"``, ``error="timeout"``
- Non-empty events  →  ``outcome="completed"``, ``summary=<digest>``

Forbidden imports: google.adk, urllib, socket, subprocess, http, requests at
module top level — verified by test.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.runner import WorkTaskRunResult


# ---------------------------------------------------------------------------
# Pydantic model (analog of CronTurnPlan)
# ---------------------------------------------------------------------------


class WorkTaskPlan(BaseModel):
    """Thin projection of a WorkTask used as input to the ADK runner turn."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    prompt: str
    timeout_seconds: float = 300.0


# ---------------------------------------------------------------------------
# Protocol seam (keeps the real OpenMagiRunnerAdapter off the import graph)
# ---------------------------------------------------------------------------


class _RunnerAdapterPort(Protocol):
    """Minimal seam the adapter needs from OpenMagiRunnerAdapter."""

    async def collect_events(self, turn_input: object) -> list[object]:
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_session_suffix(task_id: str) -> str:
    """Normalize a task id into an identifier-safe, collision-free suffix."""
    h = hashlib.sha256(task_id.encode()).hexdigest()[:8]
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "_", task_id)
    return f"{cleaned or 'task'}:{h}"


def _build_turn_input(plan: WorkTaskPlan) -> object:
    """Synthesize a RunnerTurnInput for an unattended work-queue turn.

    ADK imports are lazy to preserve boundary import purity.
    """
    from google.genai import types  # lazy: keeps module top-level ADK-free  # noqa: PLC0415
    from magi_agent.adk_bridge.runner_adapter import RunnerTurnInput  # noqa: PLC0415
    from magi_agent.harness.resolved import build_default_resolved_harness_state  # noqa: PLC0415

    suffix = _safe_session_suffix(plan.task_id)
    harness_state = build_default_resolved_harness_state(
        agent_role="general",
        spawn_depth=1,
    )
    return RunnerTurnInput(
        userId=f"worker:task:{suffix}",
        sessionId=f"agent:task:{suffix}",
        turnId=f"task-turn:{suffix}",
        invocationId=f"task-inv:{suffix}",
        newMessage=types.Content(role="user", parts=[types.Part(text=plan.prompt)]),
        harnessState=harness_state,
    )


def _summarize_events(events: list[object]) -> str:
    """Produce a compact, redaction-safe digest of collected events."""
    if not events:
        return ""
    joined = "␟".join(repr(event) for event in events)
    digest = hashlib.sha256(joined.encode()).hexdigest()
    return f"events:{len(events)}:sha256:{digest}"


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class AdkWorkTaskRunner:
    """Drives a WorkTask through the local ADK runner.

    Parameters
    ----------
    runner_adapter:
        Duck-typed ``OpenMagiRunnerAdapter`` (or any object exposing an async
        ``collect_events(RunnerTurnInput) -> list[event]``).  Owns the real
        ADK runner; never constructed here.
    default_timeout_seconds:
        Per-task timeout applied via ``asyncio.wait_for``.
    """

    def __init__(
        self,
        runner_adapter: _RunnerAdapterPort,
        *,
        default_timeout_seconds: float = 300.0,
    ) -> None:
        self._runner_adapter = runner_adapter
        self._default_timeout_seconds = default_timeout_seconds

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        prompt = task.title if not task.body else f"{task.title}\n\n{task.body}"
        plan = WorkTaskPlan(
            task_id=task.id,
            prompt=prompt,
            timeout_seconds=self._default_timeout_seconds,
        )
        turn_input = _build_turn_input(plan)

        try:
            events = await asyncio.wait_for(
                self._runner_adapter.collect_events(turn_input),
                timeout=plan.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return WorkTaskRunResult(outcome="failed", error="timeout")
        except Exception as exc:  # noqa: BLE001
            return WorkTaskRunResult(outcome="failed", error=f"adk error: {exc}")

        if not events:
            return WorkTaskRunResult(outcome="failed", error="no events returned")

        return WorkTaskRunResult(
            outcome="completed",
            summary=_summarize_events(events),
        )


__all__ = ["AdkWorkTaskRunner", "WorkTaskPlan"]
