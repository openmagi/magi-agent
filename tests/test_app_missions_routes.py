"""Behaviour tests for the local mission routes (PR-M2 read + PR-M3 control).

These routes are the OSS-local product surface: the work_queue SQLite store is
the mission source of truth, projected via the PR-M1 kernel
(``magi_agent.missions.projection``) into the ``MissionSummary`` shape the chat
"Missions" panel renders. The raw ``/api/work-queue/v1`` board API is untouched.

Repo convention: tests live under ``tests/`` (pyproject ``testpaths``) so they
run in CI. See design doc
``docs/plans/2026-07-05-magi-missions-workqueue-unification-design.md`` sections
5.2 / 6.2 / 6.3.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "local-dev-token"


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Return ``(client, store)`` sharing a temp work_queue DB.

    The routes resolve their own store via ``work_queue_db_path_from_env`` (the
    ``MAGI_WORK_QUEUE_DB_PATH`` env), so the seeding ``store`` and the route's
    store point at the same file. WAL means committed seed rows are visible.
    """
    db = tmp_path / "wq.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db))
    monkeypatch.chdir(tmp_path)
    store = SqliteWorkQueueStore(db)
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    try:
        yield client, store
    finally:
        store.close()


def _seed(store: SqliteWorkQueueStore, task_id: str, status: str, **kw) -> WorkTask:
    task = WorkTask(id=task_id, title=f"title-{task_id}", status=status, created_at=1_700_000_000, **kw)
    store.create(task)
    return task


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def test_missions_list_requires_auth(env):
    client, _ = env
    r = client.get("/v1/app/missions", headers={"x-gateway-token": "wrong"})
    assert r.status_code == 401


def test_mission_action_requires_auth(env):
    client, store = env
    _seed(store, "t1", "failed")
    r = client.post("/v1/app/missions/t1/retry", headers={"x-gateway-token": "wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# M2 read: list
# ---------------------------------------------------------------------------


def test_list_projects_mission_shape(env):
    client, store = env
    _seed(store, "t-run", "running", session_id="sess-1", goal_mode=True, goal_max_turns=9)
    body = client.get("/v1/app/missions").json()
    assert "missions" in body
    row = next(m for m in body["missions"] if m["id"] == "t-run")
    # projected, not raw WorkTask
    assert row["status"] == "running"
    assert row["kind"] == "goal"
    assert row["channel_type"] == "app"
    assert row["channel_id"] == "sess-1"
    assert row["budget_turns"] == 9
    assert row["idempotencyKey"] == "wq:t-run"
    assert row["metadata"]["work_queue_status"] == "running"


def test_list_status_filter_uses_mission_status(env):
    client, store = env
    # triage/todo/ready all project to mission-status "queued"
    _seed(store, "a", "ready")
    _seed(store, "b", "todo")
    _seed(store, "c", "running")
    _seed(store, "d", "completed")
    queued = client.get("/v1/app/missions", params={"status": "queued"}).json()["missions"]
    assert {m["id"] for m in queued} == {"a", "b"}
    running = client.get("/v1/app/missions", params={"status": "running"}).json()["missions"]
    assert {m["id"] for m in running} == {"c"}
    completed = client.get("/v1/app/missions", params={"status": "completed"}).json()["missions"]
    assert {m["id"] for m in completed} == {"d"}


def test_list_limit_clamped(env):
    client, store = env
    for i in range(5):
        _seed(store, f"t{i}", "ready")
    assert len(client.get("/v1/app/missions", params={"limit": 2}).json()["missions"]) == 2
    # limit below 1 clamps up to 1
    assert len(client.get("/v1/app/missions", params={"limit": 0}).json()["missions"]) == 1
    # limit above 100 clamps down (all 5 seeded rows returned, no error)
    assert len(client.get("/v1/app/missions", params={"limit": 9999}).json()["missions"]) == 5


def test_list_empty_store_returns_empty(env):
    client, _ = env
    assert client.get("/v1/app/missions").json() == {"missions": []}


# ---------------------------------------------------------------------------
# M2 read: detail (events + runs projected)
# ---------------------------------------------------------------------------


def test_detail_projects_events_and_runs(env):
    client, store = env
    _seed(store, "t1", "ready")
    store.claim("t1", claimer="w1", now=1_700_000_100, worker_pid=1)
    store.complete("t1", result="done")
    body = client.get("/v1/app/missions/t1").json()
    assert body["mission"]["id"] == "t1"
    assert body["mission"]["status"] == "completed"
    # claimed + completed events projected; ignorable/internal kinds dropped
    event_types = [e["event_type"] for e in body["events"]]
    assert "claimed" in event_types
    assert "completed" in event_types
    # one run, projected to mission-run shape
    assert len(body["runs"]) == 1
    assert body["runs"][0]["status"] == "completed"
    # used_turns reflects run count
    assert body["mission"]["used_turns"] == 1


def test_detail_missing_returns_404(env):
    client, _ = env
    assert client.get("/v1/app/missions/nope").status_code == 404


# ---------------------------------------------------------------------------
# M3 control: transition matrix (5.2)
# ---------------------------------------------------------------------------


def _status_of(store, task_id):
    task = store.get(task_id)
    return task.status if task else None


def _event_kinds(store, task_id):
    return [e["kind"] for e in store.list_task_events(task_id)]


# cancel: legal from every non-closed status; rejected from closed states.
@pytest.mark.parametrize("start", ["triage", "todo", "ready", "running", "blocked"])
def test_cancel_legal_archives_task(env, start):
    client, store = env
    _seed(store, "t", start)
    r = client.post("/v1/app/missions/t/cancel")
    assert r.status_code == 200
    assert r.json()["mission"]["status"] == "cancelled"
    assert _status_of(store, "t") == "archived"
    assert "cancel_requested" in _event_kinds(store, "t")


@pytest.mark.parametrize("start", ["completed", "failed", "archived"])
def test_cancel_illegal_from_closed_states(env, start):
    client, store = env
    _seed(store, "t", start)
    r = client.post("/v1/app/missions/t/cancel")
    assert r.status_code == 409
    assert r.json()["error"] == "invalid_transition"
    assert _status_of(store, "t") == start


def test_cancel_releases_open_claim(env):
    client, store = env
    _seed(store, "t", "ready")
    store.claim("t", claimer="w1", now=1_700_000_100, worker_pid=1)
    assert store.get("t").claim_lock == "w1"
    client.post("/v1/app/missions/t/cancel")
    task = store.get("t")
    assert task.status == "archived"
    assert task.claim_lock is None
    # open run row closed
    runs = store.list_task_runs("t")
    assert runs and runs[-1]["ended_at"] is not None


# retry: legal only from failed / blocked.
@pytest.mark.parametrize("start", ["failed", "blocked"])
def test_retry_legal_resets_to_ready(env, start):
    client, store = env
    _seed(store, "t", start, consecutive_failures=2, last_failure_error="boom")
    r = client.post("/v1/app/missions/t/retry")
    assert r.status_code == 200
    # ready -> mission-status "queued"
    assert r.json()["mission"]["status"] == "queued"
    task = store.get("t")
    assert task.status == "ready"
    assert task.consecutive_failures == 0
    assert "retry_requested" in _event_kinds(store, "t")


@pytest.mark.parametrize("start", ["triage", "todo", "ready", "running", "completed", "archived"])
def test_retry_illegal_from_other_states(env, start):
    client, store = env
    _seed(store, "t", start)
    r = client.post("/v1/app/missions/t/retry")
    assert r.status_code == 409
    assert _status_of(store, "t") == start


# unblock: legal only from blocked.
def test_unblock_legal_from_blocked(env):
    client, store = env
    _seed(store, "t", "blocked")
    r = client.post("/v1/app/missions/t/unblock")
    assert r.status_code == 200
    assert r.json()["mission"]["status"] == "queued"
    assert store.get("t").status == "ready"
    assert "unblocked" in _event_kinds(store, "t")


@pytest.mark.parametrize("start", ["triage", "todo", "ready", "running", "failed", "completed", "archived"])
def test_unblock_illegal_from_other_states(env, start):
    client, store = env
    _seed(store, "t", start)
    r = client.post("/v1/app/missions/t/unblock")
    assert r.status_code == 409
    assert _status_of(store, "t") == start


def test_transition_missing_task_returns_404(env):
    client, _ = env
    for action in ("cancel", "retry", "unblock"):
        assert client.post(f"/v1/app/missions/nope/{action}").status_code == 404


# comment: ledger-only append (D5), status unchanged.
def test_comment_appends_event_without_status_change(env):
    client, store = env
    _seed(store, "t", "running")
    r = client.post("/v1/app/missions/t/comments", json={"author": "kevin", "message": "hi"})
    assert r.status_code == 200
    assert _status_of(store, "t") == "running"
    events = store.list_task_events("t")
    comment = next(e for e in events if e["kind"] == "comment")
    assert comment["payload"] == {"author": "kevin", "message": "hi"}


def test_comment_defaults_author_when_absent(env):
    client, store = env
    _seed(store, "t", "running")
    client.post("/v1/app/missions/t/comments", json={"message": "no author"})
    comment = next(e for e in store.list_task_events("t") if e["kind"] == "comment")
    assert comment["payload"]["author"] == "user"


def test_comment_missing_task_returns_404(env):
    client, _ = env
    assert client.post("/v1/app/missions/nope/comments", json={"message": "x"}).status_code == 404


def test_comment_empty_message_rejected(env):
    client, store = env
    _seed(store, "t", "running")
    assert client.post("/v1/app/missions/t/comments", json={"message": "   "}).status_code == 400
    assert client.post("/v1/app/missions/t/comments", json={}).status_code == 400
