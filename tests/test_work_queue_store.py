from magi_agent.missions.work_queue.models import WorkTask, DONE_STATES


def test_worktask_defaults():
    t = WorkTask(id="t1", title="do thing", status="todo", created_at=1)
    assert t.status == "todo"
    assert t.consecutive_failures == 0
    assert t.goal_mode is False
    assert "completed" in DONE_STATES


def test_create_get_roundtrip_survives_new_instance(tmp_path):
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    db = tmp_path / "wq.db"
    s1 = SqliteWorkQueueStore(db)
    s1.create(WorkTask(id="t1", title="x", status="todo", created_at=1))
    s1.close()
    s2 = SqliteWorkQueueStore(db)  # fresh instance, same file
    got = s2.get("t1")
    assert got is not None and got.title == "x" and got.status == "todo"
