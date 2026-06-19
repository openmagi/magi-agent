"""PR3 — gateway live-runner wiring (default-OFF)."""

import asyncio

from magi_agent.gateway.watchers import build_local_work_queue_driver
from magi_agent.missions.work_queue import inject_buffer as ib
from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.runner import (
    InjectingWorkTaskRunner,
    SafeLocalWorkTaskRunner,
)


def test_default_off_uses_safe_stub(monkeypatch):
    monkeypatch.delenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", raising=False)
    driver = build_local_work_queue_driver()
    # No change to historical behaviour: the inner runner stays the safe stub
    # and the dispatcher returns failed on turn 1.
    runner = driver._runner
    # Unwrap GoalModeRunner -> safe stub
    inner = getattr(runner, "_inner", runner)
    assert isinstance(inner, SafeLocalWorkTaskRunner)


def test_flag_on_wraps_with_injecting_decorator(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(tmp_path / "wq.db"))
    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")
    driver = build_local_work_queue_driver()
    # The outer runner is GoalModeRunner; its inner should now be wrapped in
    # InjectingWorkTaskRunner so terminal results flow into the inject buffer.
    inner = getattr(driver._runner, "_inner", None)
    assert isinstance(inner, InjectingWorkTaskRunner)


def test_flag_on_inner_runner_is_child_runner_wired(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(tmp_path / "wq.db"))
    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")
    driver = build_local_work_queue_driver()
    # Live shape: GoalModeRunner -> InjectingWorkTaskRunner -> ChildRunnerWorkTaskRunner.
    from magi_agent.missions.work_queue.runner import ChildRunnerWorkTaskRunner

    injecting = driver._runner._inner
    assert isinstance(injecting, InjectingWorkTaskRunner)
    innermost = injecting._inner
    assert isinstance(innermost, ChildRunnerWorkTaskRunner)
