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


# ---------------------------------------------------------------------------
# ChildRunnerWorkTaskRunner tests (PR1 — real runner)
# ---------------------------------------------------------------------------
from magi_agent.missions.work_queue.runner import ChildRunnerWorkTaskRunner  # noqa: E402


class _FakeChildRunner:
    """Captures the request + constructed workspace; returns a canned mapping."""

    def __init__(self, output, *, workspace=None, record=None):
        self._output = output
        self.workspace = workspace
        self.seen_request = None
        self._record = record

    async def run_child(self, request):
        self.seen_request = request
        if self._record is not None:
            self._record["request"] = request
            self._record["workspace"] = self.workspace
        return self._output


def _factory_for(output, record):
    def _make(workspace):
        return _FakeChildRunner(output, workspace=workspace, record=record)

    return _make


def test_child_runner_maps_completed_output():
    rec = {}
    runner = ChildRunnerWorkTaskRunner(
        _factory_for({"status": "completed", "summary": "report ready"}, rec)
    )
    task = WorkTask(id="t1", title="Write report", status="running", created_at=1)
    res = asyncio.run(runner.run_task(task))
    assert res.outcome == "completed" and res.summary == "report ready"


def test_child_runner_maps_failed_output():
    rec = {}
    runner = ChildRunnerWorkTaskRunner(
        _factory_for({"status": "failed", "summary": "model route unknown"}, rec)
    )
    task = WorkTask(id="t2", title="x", status="running", created_at=1)
    res = asyncio.run(runner.run_task(task))
    assert res.outcome == "failed" and "model route unknown" in (res.error or "")


def test_child_runner_blocked_status_is_failure():
    rec = {}
    runner = ChildRunnerWorkTaskRunner(_factory_for({"status": "blocked", "summary": ""}, rec))
    task = WorkTask(id="t3", title="x", status="running", created_at=1)
    res = asyncio.run(runner.run_task(task))
    assert res.outcome == "failed"


def test_child_runner_passes_resolved_workspace_to_factory():
    rec = {}
    runner = ChildRunnerWorkTaskRunner(
        _factory_for({"status": "completed", "summary": "ok"}, rec),
        workspace_resolver=lambda sid: f"/ws/{sid}",
    )
    task = WorkTask(id="t4", title="x", status="running", created_at=1, session_id="sess9")
    asyncio.run(runner.run_task(task))
    assert rec["workspace"] == "/ws/sess9"  # Q4=B shared session workspace


def test_child_runner_builds_objective_from_title_and_body():
    rec = {}
    runner = ChildRunnerWorkTaskRunner(_factory_for({"status": "completed", "summary": "ok"}, rec))
    task = WorkTask(id="t5", title="Build X", body="with constraints Y", status="running", created_at=1)
    asyncio.run(runner.run_task(task))
    objective = getattr(rec["request"], "objective", "")
    assert "Build X" in objective and "with constraints Y" in objective


def test_child_runner_request_carries_task_identity():
    rec = {}
    runner = ChildRunnerWorkTaskRunner(_factory_for({"status": "completed", "summary": "ok"}, rec))
    task = WorkTask(id="task-abc", title="x", status="running", created_at=1, session_id="s1")
    asyncio.run(runner.run_task(task))
    req = rec["request"]
    assert getattr(req, "task_id", None) == "task-abc"
    assert getattr(req, "parent_execution_id", None) == "s1"


def test_child_runner_under_goal_mode_wrapper():
    # ChildRunnerWorkTaskRunner composes with the existing GoalModeRunner.
    rec = {}
    inner = ChildRunnerWorkTaskRunner(_factory_for({"status": "completed", "summary": "turnX"}, rec))
    wrapped = GoalModeRunner(inner, _SatisfyAfter("X"))
    task = WorkTask(id="g1", title="goal", status="running", created_at=1, goal_mode=True, goal_max_turns=3)
    res = asyncio.run(wrapped.run_task(task))
    assert res.outcome == "completed"
