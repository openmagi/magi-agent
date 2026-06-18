from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from magi_agent.harness.goal_judge import GoalJudge, JudgeVerdict
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


DEFAULT_GOAL_MAX_TURNS = 6


class GoalModeRunner:
    """WorkTaskRunner that runs a goal_mode task in a judge-driven Ralph loop.

    Non-goal_mode tasks delegate once to the inner runner (unchanged). goal_mode
    tasks re-run the inner runner until the judge declares the task's goal
    satisfied or ``goal_max_turns`` is exhausted. Composes the existing
    GoalJudge primitive; the WorkQueueDriver is unaware (same Protocol).
    """

    def __init__(self, inner: WorkTaskRunner, judge: GoalJudge, *, default_max_turns: int = DEFAULT_GOAL_MAX_TURNS) -> None:
        self._inner = inner
        self._judge = judge
        self._default_max_turns = default_max_turns

    def _safe_judge(self, goal: str, transcript: str) -> JudgeVerdict | None:
        try:
            return self._judge.judge(goal, transcript)
        except Exception:  # noqa: BLE001 — judge errors must not crash the loop; bounded by max_turns
            return None

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        if not task.goal_mode:
            return await self._inner.run_task(task)
        max_turns = task.goal_max_turns or self._default_max_turns
        goal = task.title if not task.body else f"{task.title}\n\n{task.body}"
        transcript = ""
        for _turn in range(max_turns):
            result = await self._inner.run_task(task)
            if result.outcome == "failed":
                return result
            transcript += (result.summary or "") + "\n"
            verdict = self._safe_judge(goal, transcript)
            if verdict is not None and verdict.satisfied:
                return WorkTaskRunResult(outcome="completed", summary=result.summary)
        return WorkTaskRunResult(
            outcome="failed", error=f"goal not satisfied within {max_turns} turns"
        )
