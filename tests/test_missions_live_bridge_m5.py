"""PR-M5 live bridge: durable work_queue task -> live SSE mission event.

Scope (design doc 2026-07-05-magi-missions-workqueue-unification, section 6.5):
the create-time seam only. ``run_in_background`` already creates a durable
``WorkTask`` via ``store.create_idempotent``; M5 additionally surfaces that
task as a ``mission_created`` agent event on the SAME pending-agent-events SSE
path SpawnAgent uses (``chat_routes_local._push_agent_event`` ->
``local_turn_store._upsert_mission``). Status is projected through the M1
kernel (``projection.map_task_status``), never the raw ``TaskStatus``.

The transition (``mission_updated``) half of the doc's 6.5 sketch is NOT wired
here: the driver runs in a background worker thread with no live per-turn SSE,
and the ``MAGI_WORK_QUEUE_NOTIFY_ENABLED`` path is a poll-based terminal-event
tailer with a log-only sink that never reaches the browser. See the M5 PR body
/ executor report for the STOP-and-report evidence. Durable transitions are
already surfaced by the panel's ``missionRefreshSeq`` poll against the M2
``/v1/app/missions`` route.
"""
from __future__ import annotations

from magi_agent.plugins.native.scheduled_work import run_in_background
from magi_agent.tools.context import ToolContext


def _enable(monkeypatch, tmp_path):
    db = tmp_path / "wq.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db))
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_TOOL_ENABLED", "1")
    monkeypatch.setenv("MAGI_BACKGROUND_TASKS_ATTACHED", "1")
    return db


def _ctx_with_emit(events: list):
    """A serve-shaped ToolContext whose emit_agent_event captures events.

    Mirrors the sync ``_push_agent_event`` wired on the local serve turn
    (``chat_routes_local.py``): a sync callable that returns None.
    """
    def _emit(event):
        events.append(dict(event))
        return None

    return ToolContext(botId="bot-1", sessionId="sess-1", emit_agent_event=_emit)


def test_run_in_background_emits_mission_created(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    events: list = []
    res = run_in_background({"title": "Write the Q2 report", "body": "use the csv"}, _ctx_with_emit(events))

    assert res.status == "ok"
    task_id = res.output["taskId"]

    created = [e for e in events if e.get("type") == "mission_created"]
    assert len(created) == 1, f"expected exactly one mission_created, got {events!r}"
    mission = created[0]["mission"]
    assert mission["id"] == task_id
    assert mission["title"] == "Write the Q2 report"
    assert mission["kind"] == "manual"
    assert mission["metadata"]["workTaskId"] == task_id


def test_status_is_projected_not_raw_task_status(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    events: list = []
    run_in_background({"title": "projected status"}, _ctx_with_emit(events))

    mission = [e for e in events if e.get("type") == "mission_created"][0]["mission"]
    # A freshly-created task is TaskStatus "todo"; the projection collapses that
    # to MissionStatus "queued". The live event must carry the projected value,
    # never the raw work_queue status.
    assert mission["status"] == "queued"
    assert mission["status"] != "todo"


def test_goal_mode_kind_and_bare_durable_id(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    events: list = []
    res = run_in_background(
        {"title": "keep iterating", "goal_mode": True, "goal_max_turns": 4},
        _ctx_with_emit(events),
    )
    task_id = res.output["taskId"]
    mission = [e for e in events if e.get("type") == "mission_created"][0]["mission"]

    assert mission["kind"] == "goal"
    # Durable rows use the BARE task id (design 6.5). The ephemeral goal-loop
    # id scheme "goal:{turn_id}" belongs only to the in-memory goal mission
    # emitted by chat_routes_local; a durable task must never carry that prefix.
    assert mission["id"] == task_id
    assert not mission["id"].startswith("goal:")


def test_emit_absent_does_not_break_tool(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    # No emitter wired (no live turn / hosted non-serve path): the tool must
    # still create the task and return its ack. The live emit is best-effort.
    res = run_in_background({"title": "no emitter"}, ToolContext(botId="bot-1", sessionId="s"))
    assert res.status == "ok"
    assert res.output["taskId"]


def test_emitter_exception_is_swallowed(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)

    def _boom(_event):
        raise RuntimeError("subscriber gone")

    ctx = ToolContext(botId="bot-1", sessionId="s", emit_agent_event=_boom)
    res = run_in_background({"title": "raising emitter"}, ctx)
    # A misbehaving emitter must never fail the tool call.
    assert res.status == "ok"
    assert res.output["taskId"]
