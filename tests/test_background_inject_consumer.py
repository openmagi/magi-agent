"""PR3 — inject-buffer consumer + InjectingWorkTaskRunner."""

import asyncio

import pytest

from magi_agent.missions.work_queue import inject_buffer as ib
from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.runner import (
    InjectingWorkTaskRunner,
    WorkTaskRunResult,
)
from magi_agent.transport import chat_routes


@pytest.fixture(autouse=True)
def _reset_buffer():
    ib.reset_for_tests()
    yield
    ib.reset_for_tests()


# ---------------------------------------------------------------------------
# Consumer (chat_routes side)
# ---------------------------------------------------------------------------

def test_consumer_off_is_byte_identical(monkeypatch):
    monkeypatch.delenv("MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED", raising=False)
    ib.enqueue("s1", "report ready")
    out = chat_routes._apply_background_inject("s1", "hello")
    assert out == "hello"
    # Buffer untouched when consumer is off — keeps state available for a future flip.
    assert ib.peek_size("s1") == 1


def test_consumer_on_with_empty_buffer_is_noop(monkeypatch):
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED", "1")
    assert chat_routes._apply_background_inject("s1", "hello") == "hello"


def test_consumer_on_prepends_and_drains(monkeypatch):
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED", "1")
    ib.enqueue("s1", "task ab12 done: report ready")
    out = chat_routes._apply_background_inject("s1", "what's next?")
    assert "task ab12 done: report ready" in out
    assert out.endswith("what's next?")
    assert ib.peek_size("s1") == 0          # drained


def test_consumer_handles_blank_session(monkeypatch):
    monkeypatch.setenv("MAGI_BACKGROUND_TASK_INJECT_CONSUMER_ENABLED", "1")
    ib.enqueue("", "x")                       # blank session never matches
    assert chat_routes._apply_background_inject("", "p") == "p"


# ---------------------------------------------------------------------------
# InjectingWorkTaskRunner (work-queue side sink)
# ---------------------------------------------------------------------------

class _StubRunner:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    async def run_task(self, task):
        self.calls += 1
        return self._result


def _task(session_id="sess-1"):
    return WorkTask(id="t1", title="Write report", status="running", created_at=1, session_id=session_id)


def test_injecting_runner_writes_completion_summary_to_buffer():
    inner = _StubRunner(WorkTaskRunResult(outcome="completed", summary="report ready"))
    runner = InjectingWorkTaskRunner(inner)
    asyncio.run(runner.run_task(_task("s9")))
    assert any("report ready" in n for n in ib.drain("s9"))


def test_injecting_runner_writes_failure_with_error():
    inner = _StubRunner(WorkTaskRunResult(outcome="failed", error="model unreachable"))
    runner = InjectingWorkTaskRunner(inner)
    asyncio.run(runner.run_task(_task("s9")))
    notes = ib.drain("s9")
    assert any("failed" in n.lower() and "model unreachable" in n for n in notes)


def test_injecting_runner_skips_when_task_has_no_session():
    inner = _StubRunner(WorkTaskRunResult(outcome="completed", summary="x"))
    runner = InjectingWorkTaskRunner(inner)
    asyncio.run(runner.run_task(_task(None)))
    assert ib.drain("") == []                 # no session_id -> no inject


def test_injecting_runner_returns_inner_result_unchanged():
    inner_result = WorkTaskRunResult(outcome="completed", summary="ok")
    runner = InjectingWorkTaskRunner(_StubRunner(inner_result))
    out = asyncio.run(runner.run_task(_task("s9")))
    assert out == inner_result                 # decorator is transparent
