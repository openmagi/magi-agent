from __future__ import annotations

from collections.abc import Callable, Mapping
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


@runtime_checkable
class _ChildRunnerLike(Protocol):
    async def run_child(self, request: object) -> Mapping[str, object]:
        ...


def _child_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


class ChildRunnerWorkTaskRunner:
    """WorkTaskRunner that executes a task as ONE child-runner turn.

    Reuses ``RealLocalChildRunner`` via an injected ``child_runner_factory`` so
    the queue dispatcher drives real model-backed work without importing ADK
    here. The factory receives the resolved workspace root (Q4=B: the task's
    shared session workspace) and returns an object exposing
    ``run_child(request) -> Mapping``. The child runner never raises; its
    ``status`` is mapped to a ``WorkTaskRunResult``.
    """

    def __init__(
        self,
        child_runner_factory: Callable[[str | None], _ChildRunnerLike],
        *,
        workspace_resolver: Callable[[str | None], str | None] | None = None,
    ) -> None:
        self._factory = child_runner_factory
        self._workspace_resolver = workspace_resolver or (lambda _session_id: None)

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        request = self._build_request(task)
        workspace = self._workspace_resolver(task.session_id)
        runner = self._factory(workspace)
        output = await runner.run_child(request)
        return self._map_output(output)

    @staticmethod
    def _build_request(task: WorkTask) -> object:
        from magi_agent.runtime.child_runner_boundary import ChildTaskRequest

        objective = task.title if not task.body else f"{task.title}\n\n{task.body}"
        return ChildTaskRequest(
            parentExecutionId=task.session_id or "background",
            turnId=f"bg-run-{task.current_run_id or 0}",
            taskId=task.id,
            objective=objective,
        )

    @staticmethod
    def _map_output(output: Mapping[str, object]) -> WorkTaskRunResult:
        status = _child_text(output.get("status"))
        summary = _child_text(output.get("summary"))
        if status == "completed":
            return WorkTaskRunResult(outcome="completed", summary=summary or None)
        return WorkTaskRunResult(
            outcome="failed",
            error=summary or f"child runner returned status={status or 'unknown'}",
        )


class InjectingWorkTaskRunner:
    """Transparent decorator that pushes a task's terminal summary to the chat
    inject buffer so the next chat turn picks it up.

    Wraps any ``WorkTaskRunner`` and delegates ``run_task`` unchanged. After the
    inner runner returns, it formats a one-line note (completed/failed) and
    enqueues it on ``inject_buffer`` keyed by the task's ``session_id``. Tasks
    without a session id (background-only / no chat surface) are skipped.

    The driver is unaware — same Protocol, no callback hook needed. The chat-
    side consumer (``_apply_background_inject``) drains and folds these into
    the next prompt when the consumer flag is on.
    """

    def __init__(self, inner: WorkTaskRunner) -> None:
        self._inner = inner

    async def run_task(self, task: WorkTask) -> WorkTaskRunResult:
        result = await self._inner.run_task(task)
        self._emit(task, result)
        return result

    @staticmethod
    def _emit(task: WorkTask, result: WorkTaskRunResult) -> None:
        if not task.session_id:
            return
        from magi_agent.missions.work_queue import inject_buffer

        short = (task.id or "")[:6] or "?"
        title = (task.title or "").strip()
        if result.outcome == "completed":
            summary = (result.summary or "").strip()
            note = f"Background task {short} ({title!r}) completed."
            if summary:
                note = f"{note}\nResult: {summary}"
        else:
            error = (result.error or "").strip() or "unknown error"
            note = f"Background task {short} ({title!r}) failed: {error}"
        inject_buffer.enqueue(task.session_id, note)
