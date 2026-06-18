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
