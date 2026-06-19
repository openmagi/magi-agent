"""PR2 — run_in_background enqueue tool (entrance seam, default-OFF/honest)."""

from magi_agent.plugins.native.scheduled_work import run_in_background
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore
from magi_agent.tools.context import ToolContext


def _ctx(session_id="sess-1"):
    return ToolContext(botId="bot-1", sessionId=session_id)


def _enable(monkeypatch, tmp_path):
    db = tmp_path / "wq.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db))
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_TOOL_ENABLED", "1")
    monkeypatch.setenv("MAGI_BACKGROUND_TASKS_ATTACHED", "1")
    return db


def test_blocked_when_tool_flag_off(monkeypatch, tmp_path):
    db = tmp_path / "wq.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db))
    monkeypatch.delenv("MAGI_BACKGROUND_TASK_TOOL_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_BACKGROUND_TASKS_ATTACHED", "1")
    res = run_in_background({"title": "Build a report"}, _ctx())
    assert res.status == "blocked"
    assert SqliteWorkQueueStore(db).list_tasks(limit=10) == []


def test_blocked_when_store_not_attached(monkeypatch, tmp_path):
    db = tmp_path / "wq.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db))
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_TOOL_ENABLED", "1")
    monkeypatch.delenv("MAGI_BACKGROUND_TASKS_ATTACHED", raising=False)
    res = run_in_background({"title": "Build a report"}, _ctx())
    assert res.status == "blocked"


def test_title_required(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    res = run_in_background({"title": "   "}, _ctx())
    assert res.status == "blocked" and res.error_code == "title_required"


def test_creates_task_when_enabled(monkeypatch, tmp_path):
    db = _enable(monkeypatch, tmp_path)
    res = run_in_background({"title": "Write the Q2 report", "body": "use the csv"}, _ctx())
    assert res.status == "ok"
    task_id = res.output["taskId"]
    assert task_id and "ack" in res.output
    stored = SqliteWorkQueueStore(db).get(task_id)
    assert stored is not None
    assert stored.title == "Write the Q2 report"
    assert stored.status == "todo"           # dispatcher's recompute_ready promotes todo->ready
    assert stored.session_id == "sess-1"


def test_idempotent_dedup(monkeypatch, tmp_path):
    db = _enable(monkeypatch, tmp_path)
    a = run_in_background({"title": "same", "body": "x"}, _ctx())
    b = run_in_background({"title": "same", "body": "x"}, _ctx())
    assert a.output["taskId"] == b.output["taskId"]
    assert len(SqliteWorkQueueStore(db).list_tasks(limit=10)) == 1


def test_goal_mode_passthrough(monkeypatch, tmp_path):
    db = _enable(monkeypatch, tmp_path)
    res = run_in_background(
        {"title": "keep iterating", "goal_mode": True, "goal_max_turns": 4}, _ctx()
    )
    stored = SqliteWorkQueueStore(db).get(res.output["taskId"])
    assert stored.goal_mode is True and stored.goal_max_turns == 4
