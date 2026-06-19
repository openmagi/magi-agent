"""TDD tests for AdkWorkTaskRunner.

Uses a fake OpenMagiRunnerAdapter so tests never import google.adk.
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.missions.work_queue.adk_work_task_runner import AdkWorkTaskRunner
from magi_agent.missions.work_queue.runner import WorkTaskRunResult
from magi_agent.missions.work_queue.models import WorkTask


class _FakeAdkAdapter:
    def __init__(self, events=None, raises=None, sleep=0.0):
        self._events = events or []
        self._raises = raises
        self._sleep = sleep
        self.captured = None

    async def collect_events(self, turn_input):
        self.captured = turn_input
        if self._sleep:
            await asyncio.sleep(self._sleep)
        if self._raises:
            raise self._raises
        return list(self._events)


def _task(*, body=None):
    return WorkTask(id="t", title="say hi", status="running", created_at=1, body=body)


def test_adk_runner_completed_on_events():
    r = AdkWorkTaskRunner(_FakeAdkAdapter(events=[object()]))
    out = asyncio.run(r.run_task(_task()))
    assert out.outcome == "completed"


def test_adk_runner_failed_on_empty_events():
    r = AdkWorkTaskRunner(_FakeAdkAdapter(events=[]))
    out = asyncio.run(r.run_task(_task()))
    assert out.outcome == "failed" and "no events" in (out.error or "")


def test_adk_runner_failed_on_exception():
    # error field must contain the exception type name but NOT the message
    # (str(exc) can embed credentials/auth headers that would leak via board API).
    r = AdkWorkTaskRunner(_FakeAdkAdapter(raises=RuntimeError("boom")))
    out = asyncio.run(r.run_task(_task()))
    assert out.outcome == "failed"
    assert "RuntimeError" in (out.error or "")
    assert "boom" not in (out.error or "")


def test_adk_runner_failed_on_timeout():
    r = AdkWorkTaskRunner(_FakeAdkAdapter(events=[object()], sleep=0.05), default_timeout_seconds=0.01)
    out = asyncio.run(r.run_task(_task()))
    assert out.outcome == "failed" and "timeout" in (out.error or "").lower()


def test_adk_runner_uses_body_when_present():
    fake = _FakeAdkAdapter(events=[object()])
    r = AdkWorkTaskRunner(fake)
    asyncio.run(r.run_task(_task(body="extra context")))
    # The synthesized turn_input must contain both title and body text.
    assert fake.captured is not None
    assert "extra context" in str(fake.captured)


def test_adk_runner_build_turn_input_exception_is_caught():
    """_build_turn_input failure (e.g. ImportError) must return failed, not propagate."""

    class _BrokenAdapter:
        async def collect_events(self, turn_input):  # pragma: no cover
            return []

    runner = AdkWorkTaskRunner(_BrokenAdapter())

    # Patch _build_turn_input to raise ImportError (simulates missing dep).
    original = runner._build_turn_input

    def _raise(_plan):
        raise ImportError("google.genai not installed")

    runner._build_turn_input = _raise  # type: ignore[method-assign]
    out = asyncio.run(runner.run_task(_task()))
    assert out.outcome == "failed"
    assert "ImportError" in (out.error or "")
    assert "google.genai not installed" not in (out.error or "")
