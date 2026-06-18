from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from magi_agent.missions.work_queue.models import WorkTask


class WorkTaskRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    outcome: Literal["completed", "failed"]
    summary: str | None = None
    error: str | None = None


@runtime_checkable
class WorkTaskRunner(Protocol):
    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        ...


class SafeLocalWorkTaskRunner:
    """Default stub: constructs no network/ADK authority, never runs the task."""

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        return WorkTaskRunResult(
            outcome="failed",
            error="work-queue live runner requires explicit operator wiring",
        )
