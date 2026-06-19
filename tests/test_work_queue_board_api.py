# tests/test_work_queue_board_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.board_api import build_work_queue_board_router


class _Runtime:
    class config:
        gateway_token = "secret"


def _client(tmp_path):
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    app = FastAPI()
    app.include_router(build_work_queue_board_router(s, _Runtime()))
    return TestClient(app), s


def test_tasks_requires_auth(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/work-queue/v1/tasks").status_code == 401          # no bearer
    r = client.get("/api/work-queue/v1/tasks", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200 and r.json()["tasks"][0]["id"] == "t"


def test_task_detail_and_404(tmp_path):
    client, _ = _client(tmp_path)
    h = {"Authorization": "Bearer secret"}
    assert client.get("/api/work-queue/v1/tasks/t", headers=h).json()["task"]["id"] == "t"
    assert client.get("/api/work-queue/v1/tasks/nope", headers=h).status_code == 404


def test_events_and_runs_endpoints(tmp_path):
    client, s = _client(tmp_path)
    s.claim("t", claimer="w1", now=1000, worker_pid=1); s.complete("t", result="D")
    h = {"Authorization": "Bearer secret"}
    ev = client.get("/api/work-queue/v1/tasks/t/events", headers=h).json()
    assert any(e["kind"] == "completed" for e in ev["events"])
    rn = client.get("/api/work-queue/v1/tasks/t/runs", headers=h).json()
    assert rn["runs"][0]["outcome"] == "completed"


def test_no_token_configured_denies_all(tmp_path):
    """Security: when no gateway token is configured, all requests must be denied."""
    class _NoTokenRuntime:
        class config:
            gateway_token = None

    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    s.create(WorkTask(id="t", title="x", status="ready", created_at=1))
    app = FastAPI()
    app.include_router(build_work_queue_board_router(s, _NoTokenRuntime()))
    client = TestClient(app)

    # No Authorization header → 401
    assert client.get("/api/work-queue/v1/tasks").status_code == 401

    # With Authorization header (even with a token) → still 401 (fail-closed)
    assert (
        client.get(
            "/api/work-queue/v1/tasks",
            headers={"Authorization": "Bearer anything"},
        ).status_code
        == 401
    )

    # Same for task detail
    assert (
        client.get(
            "/api/work-queue/v1/tasks/t",
            headers={"Authorization": "Bearer anything"},
        ).status_code
        == 401
    )


def test_board_api_gate_default_off(monkeypatch):
    from magi_agent.missions.work_queue.board_api import is_work_queue_board_api_enabled
    monkeypatch.delenv("MAGI_WORK_QUEUE_BOARD_API_ENABLED", raising=False)
    assert is_work_queue_board_api_enabled() is False
    monkeypatch.setenv("MAGI_WORK_QUEUE_BOARD_API_ENABLED", "1")
    assert is_work_queue_board_api_enabled() is True
