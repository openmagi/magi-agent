"""ON-path verification for the /workflows-style background-task UX.

Per the flag-promotion-verification rule: "OFF is byte-identical" docstring is
not the same as "ON is a no-op." Before flipping any of the five background-
task flags to ON by default (or before deploying with them on), the ON path
must be exercised end-to-end under hermetic conditions.

This file walks the full live chain — enqueue tool -> store -> gateway
dispatcher -> InjectingWorkTaskRunner -> per-session inject buffer -> chat
consumer (drains into the next prompt) — with all five flags ON and a fake
child runner so no provider key is needed. If any seam regresses on the ON
path this file is the canary.

The five flags exercised:
  - MAGI_BACKGROUND_TASK_TOOL_ENABLED
  - MAGI_BACKGROUND_TASKS_ATTACHED
  - MAGI_WORK_QUEUE_EXECUTOR_ENABLED
  - MAGI_BACKGROUND_LIVE_RUNNER_ENABLED
  - MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED
"""

from __future__ import annotations

import pytest

from magi_agent.missions.work_queue import inject_buffer as ib
from magi_agent.missions.work_queue.runner import (
    ChildRunnerWorkTaskRunner,
    InjectingWorkTaskRunner,
)
from magi_agent.missions.work_queue.store import (
    SqliteWorkQueueStore,
    work_queue_db_path_from_env,
)
from magi_agent.plugins.native.scheduled_work import run_in_background
from magi_agent.tools.context import ToolContext
from magi_agent.transport import chat_routes


SESSION_ID = "onpath-sess"


@pytest.fixture
def _all_flags_on(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(tmp_path / "wq.db"))
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_TOOL_ENABLED", "1")
    monkeypatch.setenv("MAGI_BACKGROUND_TASKS_ATTACHED", "1")
    monkeypatch.setenv("MAGI_WORK_QUEUE_EXECUTOR_ENABLED", "1")
    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED", "1")
    ib.reset_for_tests()
    yield
    ib.reset_for_tests()


class _FakeChild:
    """Stand-in for ``RealLocalChildRunner`` — returns a canned summary."""

    def __init__(self, workspace, summary="report done"):
        self.workspace = workspace
        self._summary = summary

    async def run_child(self, request):
        return {"status": "completed", "summary": self._summary}


def test_full_chain_enqueue_run_consume(_all_flags_on):
    # 1) ENTRANCE — model calls the enqueue tool. Honest gate must accept,
    #    task lands in the durable store with status="todo".
    ctx = ToolContext(botId="bot", sessionId=SESSION_ID)
    res = run_in_background({"title": "Write Q2 report", "body": "use csv"}, ctx)
    assert res.status == "ok"
    task_id = res.output["taskId"]
    assert task_id

    store = SqliteWorkQueueStore(work_queue_db_path_from_env())
    enqueued = store.get(task_id)
    assert enqueued and enqueued.status == "todo" and enqueued.session_id == SESSION_ID

    # 2) EXECUTION — emulate one dispatcher tick by driving the live runner shape
    #    (ChildRunnerWorkTaskRunner + InjectingWorkTaskRunner) against the
    #    claimed task with a fake child. This mirrors what the gateway watcher
    #    does each tick under MAGI_BACKGROUND_LIVE_RUNNER_ENABLED.
    factory = lambda ws: _FakeChild(ws, summary="Q2 report ready: 3 sections")  # noqa: E731
    live = ChildRunnerWorkTaskRunner(factory)
    sink = InjectingWorkTaskRunner(live)

    import asyncio

    result = asyncio.run(sink.run_task(enqueued))
    assert result.outcome == "completed"
    assert "Q2 report ready" in (result.summary or "")

    # 3) BUFFER — the InjectingWorkTaskRunner enqueued a completion note keyed
    #    by the task's session_id.
    assert ib.peek_size(SESSION_ID) == 1

    # 4) CONSUMER — the next chat-prompt assembly drains the buffer and folds
    #    a system-note block above the user's prompt.
    folded = chat_routes._apply_background_inject(SESSION_ID, "what's next?")
    assert "Q2 report ready" in folded
    assert folded.endswith("what's next?")
    # Buffer cleared after drain so we don't double-inject on the turn after.
    assert ib.peek_size(SESSION_ID) == 0


def test_tool_flag_off_short_circuits_chain(monkeypatch, tmp_path):
    """Removing MAGI_BACKGROUND_TASK_TOOL_ENABLED keeps the entrance dark.

    The downstream layers are independent of this flag, but the entrance gate
    must refuse to enqueue so nothing reaches the dispatcher.
    """
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(tmp_path / "wq.db"))
    monkeypatch.delenv("MAGI_BACKGROUND_TASK_TOOL_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_BACKGROUND_TASKS_ATTACHED", "1")

    ctx = ToolContext(botId="bot", sessionId=SESSION_ID)
    res = run_in_background({"title": "x"}, ctx)
    assert res.status == "blocked"
    assert res.error_code == "background_task_tool_disabled"


def test_consumer_flag_off_holds_buffer_for_next_promotion(monkeypatch):
    """With the consumer flag OFF, completion notes still accumulate but
    don't reach the prompt — verifying the buffer stays available so a future
    operator can flip the flag mid-soak without losing prior completions."""
    monkeypatch.delenv("MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED", raising=False)
    ib.reset_for_tests()
    ib.enqueue(SESSION_ID, "earlier completion: report ready")

    folded = chat_routes._apply_background_inject(SESSION_ID, "hello")
    # Consumer OFF -> prompt is byte-identical and the buffer stays put.
    assert folded == "hello"
    assert ib.peek_size(SESSION_ID) == 1
