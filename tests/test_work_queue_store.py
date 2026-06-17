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


def test_recompute_ready_respects_parents(tmp_path):
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore

    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="p", title="parent", status="todo", created_at=1))
    s.create(WorkTask(id="c", title="child", status="todo", created_at=1))
    s.link("p", "c")
    assert s.recompute_ready() == 1          # only p (no parents) promotes
    assert s.get("p").status == "ready"
    assert s.get("c").status == "todo"       # blocked by undone parent
    # mark parent completed -> child promotes
    s._set_status("p", "completed")
    assert s.recompute_ready() == 1
    assert s.get("c").status == "ready"


def test_claim_is_atomic_single_winner(tmp_path):
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    first = s.claim("t", claimer="w1", now=1000, worker_pid=111)
    second = s.claim("t", claimer="w2", now=1000, worker_pid=222)
    assert first is not None and first.status == "running" and first.claim_lock == "w1"
    assert second is None                         # CAS loser
    assert s.get("t").claim_lock == "w1"
