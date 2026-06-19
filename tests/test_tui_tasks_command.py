"""PR4b — `/tasks` slash command lists work-queue tasks for the TUI + headless."""

from __future__ import annotations

import asyncio

from magi_agent.cli.commands.builtins import builtin_commands
from magi_agent.cli.contracts import CommandContext, Text
from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore


def _tasks_command():
    for cmd in builtin_commands():
        if cmd.name == "tasks":
            return cmd
    raise AssertionError("/tasks command not registered in default local commands")


def _seed(db_path, *tasks):
    store = SqliteWorkQueueStore(db_path)
    for t in tasks:
        store.create(t)
    return store


def _ctx(cwd=".", session=None):
    return CommandContext(cwd=cwd, session=session)


def _task(tid, title, status, *, created_at=1, session_id=None):
    return WorkTask(id=tid, title=title, status=status, created_at=created_at, session_id=session_id)


def test_tasks_command_registered_in_both_surfaces():
    cmd = _tasks_command()
    assert cmd.surface.tui is True and cmd.surface.headless is True


def test_tasks_renders_empty_when_no_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(tmp_path / "wq.db"))
    res = asyncio.run(_tasks_command().call(None, _ctx()))
    assert isinstance(res, Text)
    assert "no background tasks" in res.text.lower()


def test_tasks_renders_running_and_queued_and_done(tmp_path, monkeypatch):
    db = tmp_path / "wq.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db))
    _seed(
        db,
        _task("ta", "Write report", "running", created_at=3),
        _task("tb", "Crunch CSV", "ready", created_at=2),
        _task("tc", "Old job", "completed", created_at=1),
    )
    res = asyncio.run(_tasks_command().call(None, _ctx()))
    assert isinstance(res, Text)
    text = res.text
    # Short id prefix surfaced (first 6 chars), title preserved.
    assert "ta" in text and "Write report" in text
    assert "tb" in text and "Crunch CSV" in text
    assert "tc" in text and "Old job" in text


def test_tasks_groups_active_then_done(tmp_path, monkeypatch):
    db = tmp_path / "wq.db"
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(db))
    _seed(
        db,
        _task("td", "active1", "running", created_at=1),
        _task("te", "done1", "completed", created_at=2),
    )
    res = asyncio.run(_tasks_command().call(None, _ctx()))
    text = res.text
    # Active section appears before the done section.
    active_idx = text.lower().find("active")
    done_idx = text.lower().find("done")
    assert 0 <= active_idx < done_idx
