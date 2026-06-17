from __future__ import annotations

import pytest

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


# ---------------------------------------------------------------------------
# Cross-store parity tests — identical contract verified against BOTH stores
# ---------------------------------------------------------------------------


def _make_stores(tmp_path):
    """Yield (label, store) pairs for parametrize."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore, InMemoryWorkQueueStore

    return [
        ("sqlite", SqliteWorkQueueStore(tmp_path / "parity.db")),
        ("inmemory", InMemoryWorkQueueStore()),
    ]


@pytest.fixture(params=["sqlite", "inmemory"])
def store(request, tmp_path):
    """Parametrized fixture yielding a fresh store of each kind."""
    from magi_agent.missions.work_queue.store import SqliteWorkQueueStore, InMemoryWorkQueueStore

    if request.param == "sqlite":
        return SqliteWorkQueueStore(tmp_path / "parity.db")
    return InMemoryWorkQueueStore()


# (a) claim single-winner: 2nd claim on a running task → None, lock stays first claimer
def test_parity_claim_single_winner(store):
    store.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    first = store.claim("t", claimer="w1", now=1000, worker_pid=111)
    second = store.claim("t", claimer="w2", now=1000, worker_pid=222)
    assert first is not None
    assert first.status == "running"
    assert first.claim_lock == "w1"
    assert second is None                          # CAS loser
    assert store.get("t").claim_lock == "w1"       # lock still belongs to first claimer


# (b) recompute_ready DAG: root promotes, child blocked until parent completed, then promotes
def test_parity_recompute_ready_dag(store):
    store.create(WorkTask(id="p", title="parent", status="todo", created_at=1))
    store.create(WorkTask(id="c", title="child", status="todo", created_at=1))
    store.link("p", "c")
    promoted = store.recompute_ready()
    assert promoted == 1                           # only root (p) promotes
    assert store.get("p").status == "ready"
    assert store.get("c").status == "todo"         # blocked: parent not done
    store._set_status("p", "completed")
    promoted2 = store.recompute_ready()
    assert promoted2 == 1                          # child now promotes
    assert store.get("c").status == "ready"


# (c) record_failure breaker boundary: limit=2 → 1st→ready, 2nd consecutive→blocked
def test_parity_record_failure_breaker_boundary(store):
    store.create(WorkTask(id="t", title="x", status="running", created_at=1))
    t1 = store.record_failure("t", outcome="crashed", failure_limit=2)
    assert t1.status == "ready"                    # 1st failure → retry
    assert t1.consecutive_failures == 1
    store._set_status("t", "running")
    t2 = store.record_failure("t", outcome="crashed", failure_limit=2)
    assert t2.status == "blocked"                  # 2nd consecutive → blocked
    assert t2.consecutive_failures == 2


# (d) complete: sets completed, resets consecutive_failures=0, idempotent on 2nd call
def test_parity_complete_idempotent(store):
    store.create(WorkTask(id="t", title="x", status="running", created_at=1,
                          consecutive_failures=1))
    done = store.complete("t", result="ok")
    assert done.status == "completed"
    assert done.result == "ok"
    assert done.consecutive_failures == 0
    assert done.current_run_id is None             # run pointer cleared
    # Second call on already-completed task must be idempotent: no error, status stays
    done2 = store.complete("t", result="ignored")
    assert done2.status == "completed"             # still completed, not mutated


# (e) release_stale_claims: dead worker→reclaimed to ready; alive+fresh→extended stays running
#     and last_heartbeat_at is refreshed on extend
def test_parity_release_stale_claims(store):
    from magi_agent.missions.work_queue.store import CLAIM_TTL_SECONDS

    # Dead-worker path
    store.create(WorkTask(id="dead", title="x", status="ready", created_at=1))
    store.claim("dead", claimer="w1", now=1000, worker_pid=111)
    n = store.release_stale_claims(
        now=1000 + CLAIM_TTL_SECONDS + 1, pid_alive=lambda pid: False
    )
    assert n == 1
    td = store.get("dead")
    assert td.status == "ready" and td.claim_lock is None

    # Alive-and-fresh path: claim expires, but worker is alive and heartbeat is fresh.
    # release_stale_claims must EXTEND (not reclaim) and refresh last_heartbeat_at.
    from magi_agent.missions.work_queue.store import CLAIM_HEARTBEAT_MAX_STALE_SECONDS

    store.create(WorkTask(id="alive", title="y", status="ready", created_at=1))
    # Claim at t=2000; claim expires at 2000+CLAIM_TTL_SECONDS.
    store.claim("alive", claimer="w2", now=2000, worker_pid=222)
    # extend_now is 1 second past claim expiry — claim_expires < extend_now, so the task
    # appears in the stale-scan — but the worker is alive and heartbeat is not stale
    # (hb=2000, age=CLAIM_TTL_SECONDS+1 < CLAIM_HEARTBEAT_MAX_STALE_SECONDS).
    extend_now = 2000 + CLAIM_TTL_SECONDS + 1
    n2 = store.release_stale_claims(
        now=extend_now, pid_alive=lambda pid: True
    )
    assert n2 == 0                                 # extended, not reclaimed
    ta = store.get("alive")
    assert ta.status == "running"
    # last_heartbeat_at must be refreshed to extend_now (not the original claim timestamp)
    assert ta.last_heartbeat_at == extend_now


# (f) find_by_idempotency_key: hit + miss
def test_parity_find_by_idempotency_key(store):
    store.create(WorkTask(id="t", title="x", status="todo", created_at=1, idempotency_key="k1"))
    hit = store.find_by_idempotency_key("k1")
    assert hit is not None and hit.id == "t"
    miss = store.find_by_idempotency_key("nope")
    assert miss is None
