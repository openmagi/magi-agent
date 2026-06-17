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


def test_stale_claim_reclaimed_when_worker_dead(tmp_path):
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore, CLAIM_TTL_SECONDS
    from magi_agent.missions.work_queue.models import WorkTask
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    s.claim("t", claimer="w1", now=1000, worker_pid=111)
    # TTL expired, worker dead -> reclaim to ready
    n = s.release_stale_claims(now=1000 + CLAIM_TTL_SECONDS + 1, pid_alive=lambda pid: False)
    assert n == 1
    t = s.get("t")
    assert t.status == "ready" and t.claim_lock is None


def test_stale_claim_extended_when_worker_alive_and_fresh(tmp_path):
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore, CLAIM_TTL_SECONDS
    from magi_agent.missions.work_queue.models import WorkTask
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    s.claim("t", claimer="w1", now=1000, worker_pid=111)
    s.heartbeat("t", claimer="w1", now=1000 + CLAIM_TTL_SECONDS - 1)  # fresh heartbeat
    n = s.release_stale_claims(now=1000 + CLAIM_TTL_SECONDS + 1, pid_alive=lambda pid: True)
    assert n == 0                                  # extended, not reclaimed
    assert s.get("t").status == "running"


def test_idempotency_lookup(tmp_path):
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="t", title="x", status="todo", created_at=1, idempotency_key="abc"))
    assert s.find_by_idempotency_key("abc").id == "t"
    assert s.find_by_idempotency_key("nope") is None


def test_circuit_breaker_blocks_after_limit(tmp_path):
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="t", title="x", status="running", created_at=1))
    s.record_failure("t", outcome="crashed", failure_limit=2)
    assert s.get("t").status == "ready"            # 1st failure -> retry
    s.create(WorkTask(id="t2", title="y", status="running", created_at=1))
    s._set_status("t", "running")
    s.record_failure("t", outcome="crashed", failure_limit=2)
    assert s.get("t").status == "blocked"          # 2nd consecutive -> blocked


# ---------------------------------------------------------------------------
# InMemoryWorkQueueStore tests
# ---------------------------------------------------------------------------

def test_inmemory_claim_is_atomic_single_winner():
    """InMemory double must mirror the Sqlite CAS claim: first wins, second returns None."""
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask

    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    first = s.claim("t", claimer="w1", now=1000, worker_pid=111)
    second = s.claim("t", claimer="w2", now=1000, worker_pid=222)
    assert first is not None and first.status == "running" and first.claim_lock == "w1"
    assert second is None                          # CAS loser
    assert s.get("t").claim_lock == "w1"


def test_inmemory_recompute_ready_respects_dag():
    """InMemory double must respect DAG parent-gating in recompute_ready."""
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask

    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="p", title="parent", status="todo", created_at=1))
    s.create(WorkTask(id="c", title="child", status="todo", created_at=1))
    s.link("p", "c")
    assert s.recompute_ready() == 1               # only p (no parents) promotes
    assert s.get("p").status == "ready"
    assert s.get("c").status == "todo"            # blocked by undone parent
    s._set_status("p", "completed")
    assert s.recompute_ready() == 1
    assert s.get("c").status == "ready"


def test_inmemory_find_by_idempotency_key():
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask

    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="todo", created_at=1, idempotency_key="abc"))
    assert s.find_by_idempotency_key("abc").id == "t"
    assert s.find_by_idempotency_key("nope") is None


def test_inmemory_record_failure_blocks_after_limit():
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask

    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="running", created_at=1))
    s.record_failure("t", outcome="crashed", failure_limit=2)
    assert s.get("t").status == "ready"            # 1st failure -> retry
    s._set_status("t", "running")
    s.record_failure("t", outcome="crashed", failure_limit=2)
    assert s.get("t").status == "blocked"          # 2nd consecutive -> blocked


def test_inmemory_complete():
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore
    from magi_agent.missions.work_queue.models import WorkTask

    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="running", created_at=1))
    done = s.complete("t", result="ok")
    assert done.status == "completed"
    assert done.result == "ok"


def test_inmemory_stale_claim_reclaimed():
    from magi_agent.missions.work_queue.store import InMemoryWorkQueueStore, CLAIM_TTL_SECONDS
    from magi_agent.missions.work_queue.models import WorkTask

    s = InMemoryWorkQueueStore()
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    s.claim("t", claimer="w1", now=1000, worker_pid=111)
    n = s.release_stale_claims(now=1000 + CLAIM_TTL_SECONDS + 1, pid_alive=lambda pid: False)
    assert n == 1
    t = s.get("t")
    assert t.status == "ready" and t.claim_lock is None


def test_work_queue_store_protocol_is_satisfied_by_both_implementations(tmp_path):
    """Both SqliteWorkQueueStore and InMemoryWorkQueueStore satisfy WorkQueueStore Protocol."""
    from magi_agent.missions.work_queue.store import (
        WorkQueueStore,
        SqliteWorkQueueStore,
        InMemoryWorkQueueStore,
    )

    assert isinstance(SqliteWorkQueueStore(tmp_path / "wq.db"), WorkQueueStore)
    assert isinstance(InMemoryWorkQueueStore(), WorkQueueStore)
