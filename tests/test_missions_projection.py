"""Tests for the pure work_queue -> mission projection kernel (PR-M1).

Repo convention: tests live under ``tests/`` (pyproject ``testpaths``), NOT
colocated, so this exhaustiveness suite actually runs in CI. See the design
doc ``docs/plans/2026-07-05-magi-missions-workqueue-unification-design.md``
section 5 for the mapping tables under test.
"""
from __future__ import annotations

import inspect
from typing import get_args

import pytest

from magi_agent.missions import projection
from magi_agent.missions.projection import (
    IGNORABLE_STORE_EVENT_KINDS,
    STORE_EVENT_KIND_TO_MISSION_EVENT,
    STORE_EVENT_KINDS,
    STORE_RUN_STATUS_TO_MISSION_RUN_STATUS,
    MissionEventType,
    MissionRunStatus,
    MissionStatus,
    map_task_event,
    map_task_run,
    map_task_status,
    project_task_to_mission_summary,
)
from magi_agent.missions.work_queue.models import TaskStatus, WorkTask
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore


# ---------------------------------------------------------------------------
# 5.1 status-map exhaustiveness (fails if a future TaskStatus is added unmapped)
# ---------------------------------------------------------------------------


def test_map_task_status_covers_every_task_status_literal() -> None:
    mission_values = set(get_args(MissionStatus))
    for status in get_args(TaskStatus):
        mapped = map_task_status(status)
        assert mapped in mission_values, f"{status!r} -> {mapped!r} not a MissionStatus"


def test_map_task_status_exact_table_5_1() -> None:
    assert {status: map_task_status(status) for status in get_args(TaskStatus)} == {
        "triage": "queued",
        "todo": "queued",
        "ready": "queued",
        "running": "running",
        "blocked": "blocked",
        "completed": "completed",
        "failed": "failed",
        "archived": "cancelled",
    }


def test_map_task_status_never_produces_waiting_or_paused() -> None:
    produced = {map_task_status(s) for s in get_args(TaskStatus)}
    assert "waiting" not in produced
    assert "paused" not in produced


def test_map_task_status_unmapped_raises() -> None:
    with pytest.raises(KeyError):
        map_task_status("no_such_status")


# ---------------------------------------------------------------------------
# 5.5 event-kind exhaustiveness, pinned to the actual store write sites
# ---------------------------------------------------------------------------


def _store_event_kinds_from_source() -> set[str]:
    """Parse the literal ``kind`` strings the store passes to ``_append_event``.

    This ties STORE_EVENT_KINDS to the real source: if a future store change
    adds/removes a ``_append_event(conn, id, "<kind>", ...)`` call, this test
    fails until STORE_EVENT_KINDS (and the mapping) are updated.
    """
    src = inspect.getsource(SqliteWorkQueueStore)
    kinds: set[str] = set()
    marker = "self._append_event(conn, "
    for line in src.splitlines():
        idx = line.find(marker)
        if idx == -1:
            continue
        rest = line[idx + len(marker) :]
        # rest looks like: <id_expr>, "<kind>", <payload>)
        first_comma = rest.find(",")
        if first_comma == -1:
            continue
        after_id = rest[first_comma + 1 :].strip()
        if not after_id.startswith('"'):
            continue
        end = after_id.find('"', 1)
        kinds.add(after_id[1:end])
    return kinds


def test_store_event_kinds_matches_real_store_write_sites() -> None:
    assert _store_event_kinds_from_source() == set(STORE_EVENT_KINDS)


def test_every_store_event_kind_is_mapped_or_ignorable() -> None:
    mission_event_values = set(get_args(MissionEventType))
    for kind in STORE_EVENT_KINDS:
        if kind in IGNORABLE_STORE_EVENT_KINDS:
            assert map_task_event({"kind": kind}) is None
            continue
        assert kind in STORE_EVENT_KIND_TO_MISSION_EVENT, f"{kind!r} neither mapped nor ignorable"
        assert STORE_EVENT_KIND_TO_MISSION_EVENT[kind] in mission_event_values


def test_mapped_event_kinds_all_target_valid_mission_event_types() -> None:
    mission_event_values = set(get_args(MissionEventType))
    for mapped in STORE_EVENT_KIND_TO_MISSION_EVENT.values():
        assert mapped in mission_event_values


def test_map_task_event_ignorable_kinds_return_none_without_warning(caplog) -> None:
    with caplog.at_level("WARNING"):
        for kind in IGNORABLE_STORE_EVENT_KINDS:
            assert map_task_event({"kind": kind, "id": 1, "created_at": 100}) is None
    assert caplog.records == []


def test_map_task_event_unknown_kind_returns_none_and_warns(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert map_task_event({"kind": "totally_new_kind", "id": 5}) is None
    assert any("totally_new_kind" in r.getMessage() for r in caplog.records)


def test_map_task_event_maps_claimed_shape() -> None:
    out = map_task_event(
        {"id": 7, "task_id": "t1", "run_id": None, "kind": "claimed",
         "payload": {"claimer": "w1"}, "created_at": 1_700_000_000}
    )
    assert out is not None
    assert out["event_type"] == "claimed"
    assert out["payload"] == {"claimer": "w1"}
    assert out["work_queue_kind"] == "claimed"
    assert out["work_queue_event_id"] == 7
    assert out["created_at"] == "2023-11-14T22:13:20+00:00"


def test_map_task_event_claim_extended_maps_to_heartbeat() -> None:
    out = map_task_event({"kind": "claim_extended", "id": 3, "payload": None, "created_at": 1})
    assert out is not None
    assert out["event_type"] == "heartbeat"
    assert out["payload"] == {}


# ---------------------------------------------------------------------------
# 5.5 run mapping
# ---------------------------------------------------------------------------


def test_run_status_map_targets_valid_mission_run_statuses() -> None:
    valid = set(get_args(MissionRunStatus))
    for mapped in STORE_RUN_STATUS_TO_MISSION_RUN_STATUS.values():
        assert mapped in valid


def test_map_task_run_status_mapping() -> None:
    cases = {"running": "running", "done": "completed", "failed": "failed", "released": "cancelled"}
    for raw, expected in cases.items():
        out = map_task_run({"status": raw, "id": 1, "started_at": 100, "ended_at": None})
        assert out["status"] == expected


def test_map_task_run_unknown_status_defaults_failed_and_warns(caplog) -> None:
    with caplog.at_level("WARNING"):
        out = map_task_run({"status": "weird", "id": 9})
    assert out["status"] == "failed"
    assert any("weird" in r.getMessage() for r in caplog.records)


def test_map_task_run_full_shape_and_trigger_type() -> None:
    out = map_task_run(
        {
            "id": 12,
            "task_id": "t1",
            "status": "done",
            "outcome": "completed",
            "worker_pid": 4242,
            "started_at": 1_700_000_000,
            "ended_at": 1_700_000_060,
            "summary": "did the thing",
            "error": None,
        },
        trigger_type="resume",
    )
    assert out["trigger_type"] == "resume"
    assert out["status"] == "completed"
    assert out["started_at"] == "2023-11-14T22:13:20+00:00"
    assert out["finished_at"] == "2023-11-14T22:14:20+00:00"
    assert out["result_preview"] == "did the thing"
    assert out["metadata"]["work_queue_run_id"] == 12
    assert out["metadata"]["work_queue_run_status"] == "done"
    assert out["metadata"]["outcome"] == "completed"
    assert out["metadata"]["worker_pid"] == 4242


def test_map_task_run_default_trigger_is_user() -> None:
    out = map_task_run({"status": "running", "id": 1, "started_at": 1})
    assert out["trigger_type"] == "user"


# ---------------------------------------------------------------------------
# 5.3 WorkTask -> MissionSummary field mapping
# ---------------------------------------------------------------------------


def _goal_task() -> WorkTask:
    return WorkTask(
        id="task-goal-1",
        title="Research the market",
        status="running",
        created_at=1_700_000_000,
        body="A long-running goal.",
        priority=3,
        assignee="analyst",
        session_id="sess-abc",
        idempotency_key="user-key-9",
        goal_mode=True,
        goal_max_turns=25,
        consecutive_failures=1,
        max_retries=3,
        last_failure_error="transient",
        result=None,
        started_at=1_700_000_100,
        completed_at=None,
    )


def _manual_task() -> WorkTask:
    return WorkTask(
        id="task-manual-1",
        title="Tidy the inbox",
        status="archived",
        created_at=1_700_000_000,
        body=None,
        goal_mode=False,
        session_id=None,
        completed_at=1_700_000_500,
    )


def test_project_goal_task_with_session() -> None:
    out = project_task_to_mission_summary(_goal_task(), run_count=4)
    assert out["id"] == "task-goal-1"
    assert out["bot_id"] == "local"
    assert out["kind"] == "goal"
    assert out["title"] == "Research the market"
    assert out["summary"] == "A long-running goal."
    assert out["status"] == "running"
    assert out["priority"] == 3
    assert out["created_by"] == "agent"
    assert out["assignee_profile"] == "analyst"
    # session present -> app channel
    assert out["channel_type"] == "app"
    assert out["channel_id"] == "sess-abc"
    # budget / used turns
    assert out["budget_turns"] == 25
    assert out["used_turns"] == 4
    # identity per 5.4
    assert out["idempotencyKey"] == "wq:task-goal-1"
    # epoch -> ISO
    assert out["created_at"] == "2023-11-14T22:13:20+00:00"
    # raw status preserved + metadata bag
    md = out["metadata"]
    assert md["work_task_id"] == "task-goal-1"
    assert md["work_queue_status"] == "running"
    assert md["task_idempotency_key"] == "user-key-9"
    assert md["consecutive_failures"] == 1
    assert md["max_retries"] == 3
    assert md["last_failure_error"] == "transient"
    assert md["started_at"] == "2023-11-14T22:15:00+00:00"


def test_project_manual_task_without_session_maps_internal_channel() -> None:
    out = project_task_to_mission_summary(_manual_task())
    assert out["kind"] == "manual"
    assert out["summary"] is None
    # archived -> cancelled, raw preserved
    assert out["status"] == "cancelled"
    assert out["metadata"]["work_queue_status"] == "archived"
    # no session -> internal/work-queue channel
    assert out["channel_type"] == "internal"
    assert out["channel_id"] == "work-queue"
    # completed_at projected to ISO; updated_at is the latest known ts (completed)
    assert out["completed_at"] == "2023-11-14T22:21:40+00:00"
    assert out["updated_at"] == "2023-11-14T22:21:40+00:00"
    # default run_count -> 0 used turns
    assert out["used_turns"] == 0


def test_project_respects_bot_id_and_created_by_overrides() -> None:
    out = project_task_to_mission_summary(
        _manual_task(), bot_id="bot-xyz", created_by="system"
    )
    assert out["bot_id"] == "bot-xyz"
    assert out["created_by"] == "system"


def test_summary_status_always_valid_mission_status_for_every_task_status() -> None:
    valid = set(get_args(MissionStatus))
    for status in get_args(TaskStatus):
        task = WorkTask(id="x", title="t", status=status, created_at=1_700_000_000)
        out = project_task_to_mission_summary(task)
        assert out["status"] in valid
        assert out["metadata"]["work_queue_status"] == status


# ---------------------------------------------------------------------------
# purity: the kernel must not pull in FastAPI / network / ADK
# ---------------------------------------------------------------------------


def test_projection_module_is_import_light() -> None:
    src = inspect.getsource(projection)
    for forbidden in ("import fastapi", "google.adk", "import requests", "urllib.request", "import socket"):
        assert forbidden not in src, f"projection.py must stay pure: found {forbidden!r}"
