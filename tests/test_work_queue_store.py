from magi_agent.missions.work_queue.models import WorkTask, DONE_STATES


def test_worktask_defaults():
    t = WorkTask(id="t1", title="do thing", status="todo", created_at=1)
    assert t.status == "todo"
    assert t.consecutive_failures == 0
    assert t.goal_mode is False
    assert "completed" in DONE_STATES
