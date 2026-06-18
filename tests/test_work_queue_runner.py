import asyncio
from magi_agent.missions.work_queue.runner import SafeLocalWorkTaskRunner, WorkTaskRunResult
from magi_agent.missions.work_queue.models import WorkTask


def test_safe_local_runner_never_executes():
    r = SafeLocalWorkTaskRunner()
    task = WorkTask(id="t", title="x", status="running", created_at=1)
    res = asyncio.run(r.run_task(task))
    assert isinstance(res, WorkTaskRunResult)
    assert res.outcome == "failed"
    assert "operator wiring" in (res.error or "")


# ---------------------------------------------------------------------------
# GoalModeRunner tests (Task 1)
# ---------------------------------------------------------------------------
from magi_agent.missions.work_queue.runner import GoalModeRunner  # noqa: E402
from magi_agent.harness.goal_judge import JudgeVerdict  # noqa: E402


class _CountingRunner:
    def __init__(self): self.calls = 0
    async def run_task(self, task):
        self.calls += 1
        return WorkTaskRunResult(outcome="completed", summary=f"turn{self.calls}")

class _FailRunner:
    async def run_task(self, task):
        return WorkTaskRunResult(outcome="failed", error="boom")

class _SatisfyAfter:
    """Judge satisfied only once transcript mentions 'turn{n}'."""
    def __init__(self, n): self.n = n
    def judge(self, goal, transcript_excerpt):
        return JudgeVerdict(satisfied=(f"turn{self.n}" in transcript_excerpt), raw="x")

class _NeverSatisfied:
    def judge(self, goal, transcript_excerpt):
        return JudgeVerdict(satisfied=False, raw="x")

class _RaisingJudge:
    def judge(self, goal, transcript_excerpt):
        raise RuntimeError("judge down")

def test_non_goal_mode_delegates_once():
    inner = _CountingRunner()
    r = GoalModeRunner(inner, _NeverSatisfied())
    task = WorkTask(id="t", title="x", status="running", created_at=1)  # goal_mode False
    res = asyncio.run(r.run_task(task))
    assert res.outcome == "completed" and inner.calls == 1

def test_goal_mode_loops_until_judge_satisfied():
    inner = _CountingRunner()
    r = GoalModeRunner(inner, _SatisfyAfter(3))
    task = WorkTask(id="t", title="x", status="running", created_at=1, goal_mode=True, goal_max_turns=5)
    res = asyncio.run(r.run_task(task))
    assert res.outcome == "completed" and inner.calls == 3

def test_goal_mode_exhausts_to_failed():
    inner = _CountingRunner()
    r = GoalModeRunner(inner, _NeverSatisfied())
    task = WorkTask(id="t", title="x", status="running", created_at=1, goal_mode=True, goal_max_turns=4)
    res = asyncio.run(r.run_task(task))
    assert res.outcome == "failed" and "4 turns" in (res.error or "") and inner.calls == 4

def test_goal_mode_bails_on_inner_failure():
    inner = _FailRunner()
    r = GoalModeRunner(inner, _NeverSatisfied())
    task = WorkTask(id="t", title="x", status="running", created_at=1, goal_mode=True, goal_max_turns=5)
    res = asyncio.run(r.run_task(task))
    assert res.outcome == "failed" and res.error == "boom"

def test_goal_mode_judge_exception_treated_as_unsatisfied():
    inner = _CountingRunner()
    r = GoalModeRunner(inner, _RaisingJudge())
    task = WorkTask(id="t", title="x", status="running", created_at=1, goal_mode=True, goal_max_turns=2)
    res = asyncio.run(r.run_task(task))
    assert res.outcome == "failed" and inner.calls == 2     # bounded by max_turns, no infinite loop

def test_goal_mode_uses_default_max_turns_when_none():
    inner = _CountingRunner()
    r = GoalModeRunner(inner, _NeverSatisfied(), default_max_turns=3)
    task = WorkTask(id="t", title="x", status="running", created_at=1, goal_mode=True)  # goal_max_turns None
    res = asyncio.run(r.run_task(task))
    assert res.outcome == "failed" and inner.calls == 3
