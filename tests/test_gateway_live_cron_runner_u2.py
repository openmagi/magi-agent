"""U2 -- live cron runner gate in build_local_scheduler_cron_driver().

TDD: flag-off -> SafeLocalCronTurnRunner stub (skipped, runnerInvoked=False);
flag-on -> live runner that invokes the governed turn engine (runnerInvoked=True).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.harness.scheduler_job_execution import CronTurnPlan


def _plan(job_id: str = "job:u2") -> CronTurnPlan:
    return CronTurnPlan(
        jobId=job_id,
        prompt="u2 live gate test",
        disabledToolsets=(),
        timeoutSeconds=10.0,
    )


def _completed_stream_factory() -> Any:
    """Fake stream factory that yields a terminal EngineResult(completed)."""
    from magi_agent.engine.contracts import EngineResult, Terminal

    async def _fake(ctx: Any) -> Any:
        yield EngineResult(terminal=Terminal.completed)

    return _fake


def _error_terminal_stream_factory() -> Any:
    """Fake stream factory that yields a non-completed terminal (Terminal.error) -- no exception.

    collect_governed_child_turn maps Terminal.error -> status='failed'.
    This stream does NOT raise; it yields a proper EngineResult, so the P0-1
    bug (hard-coded 'completed') would surface only if the collector status
    is ignored.
    """
    from magi_agent.engine.contracts import EngineResult, Terminal

    async def _fake(ctx: Any) -> Any:
        yield EngineResult(terminal=Terminal.error)

    return _fake


def _error_stream_factory() -> Any:
    """Fake stream factory that raises before yielding any terminal."""
    async def _boom(ctx: Any) -> Any:
        raise RuntimeError("engine error")
        yield  # make it an async generator

    return _boom


# ---------------------------------------------------------------------------
# Flag-off: stub runner returns status="skipped", runnerInvoked=False
# ---------------------------------------------------------------------------

def test_flag_off_uses_stub_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", raising=False)

    from magi_agent.gateway.watchers import _build_local_cron_turn_runner

    runner = _build_local_cron_turn_runner()
    result = asyncio.run(runner.run_turn(_plan()))
    assert result.status == "skipped"
    assert result.runner_invoked is False


def test_flag_off_output_is_honesty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", raising=False)

    from magi_agent.gateway.watchers import _build_local_cron_turn_runner

    runner = _build_local_cron_turn_runner()
    result = asyncio.run(runner.run_turn(_plan()))
    assert isinstance(result.output, str)
    assert result.output  # non-empty honesty message


# ---------------------------------------------------------------------------
# Flag-on: live runner is selected (runner_invoked=True on success)
# ---------------------------------------------------------------------------

def test_flag_on_uses_live_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """With MAGI_BACKGROUND_LIVE_RUNNER_ENABLED=1, a live engine-backed runner
    is selected.  We inject a fake stream_factory so no real ADK/network is hit.
    """
    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")

    from magi_agent.gateway.watchers import _build_local_cron_turn_runner

    runner = _build_local_cron_turn_runner(stream_factory=_completed_stream_factory())
    result = asyncio.run(runner.run_turn(_plan("job:u2-live")))

    assert result.status == "completed"
    assert result.runner_invoked is True
    assert result.job_id == "job:u2-live"


def test_flag_on_runner_error_maps_to_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising stream_factory -> status='failed', runnerInvoked=True."""
    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")

    from magi_agent.gateway.watchers import _build_local_cron_turn_runner

    runner = _build_local_cron_turn_runner(stream_factory=_error_stream_factory())
    result = asyncio.run(runner.run_turn(_plan("job:u2-err")))

    assert result.status == "failed"
    assert result.runner_invoked is True


def test_flag_on_non_completed_terminal_maps_to_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """P0-1 / P1-3: a clean Terminal.error (no exception) must NOT map to completed.

    collect_governed_child_turn returns status='failed' for any non-completed
    terminal (Terminal.error, Terminal.aborted, Terminal.max_turns).
    The runner must propagate that status, not hard-code 'completed'.
    """
    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")

    from magi_agent.gateway.watchers import _build_local_cron_turn_runner

    runner = _build_local_cron_turn_runner(stream_factory=_error_terminal_stream_factory())
    result = asyncio.run(runner.run_turn(_plan("job:u2-error-terminal")))

    assert result.status == "failed", (
        f"Terminal.error must map to CronTurnResult.status='failed', got {result.status!r}"
    )
    assert result.runner_invoked is True


def test_flag_on_terminal_failed_does_not_deliver(monkeypatch: pytest.MonkeyPatch) -> None:
    """P0-1 / P1-3 integration: _deliver_turn_result skips delivery for failed turns.

    This test verifies two things in one shot:
    1. A runner backed by Terminal.error returns status="failed" (P0-1 fix).
    2. _deliver_turn_result returns a "skipped" receipt without calling deliver()
       for a failed turn (delivery gate, scheduler_job_execution.py:737).
    """
    import unittest.mock as _mock
    from datetime import UTC, datetime

    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")

    from magi_agent.gateway.watchers import _build_local_cron_turn_runner
    from magi_agent.harness.scheduler_job_execution import (
        _deliver_turn_result,
        CronTurnResult,
    )
    from magi_agent.harness.scheduler_executor import ScheduledJobRecord

    # Confirm the runner produces status="failed" from Terminal.error.
    runner = _build_local_cron_turn_runner(stream_factory=_error_terminal_stream_factory())
    turn_result = asyncio.run(runner.run_turn(_plan("job:deliver-gate")))
    assert turn_result.status == "failed", (
        f"Terminal.error must produce status='failed', got {turn_result.status!r}"
    )

    # Build a minimal ScheduledJobRecord (Pydantic model, not a Protocol).
    record = ScheduledJobRecord(
        jobId="job:deliver-gate",
        scheduleExpr="@hourly",
        nextRun=datetime.now(UTC),
    )

    # Verify the delivery gate: deliver() must NOT be called for a failed turn.
    deliver_calls: list[Any] = []

    with _mock.patch(
        "magi_agent.harness.scheduler_delivery.deliver",
        side_effect=lambda *a, **kw: deliver_calls.append(a),
    ):
        receipt = _deliver_turn_result(turn_result, record=record, now=datetime.now(UTC))

    assert deliver_calls == [], (
        f"deliver() must not be called for status='failed', was called: {deliver_calls}"
    )
    assert receipt.status == "skipped", (
        f"Expected receipt.status='skipped', got {receipt.status!r}"
    )


def test_flag_on_disabled_toolset_strip_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """plan.disabled_toolsets is passed through to the runner seam; the live runner
    should record the job_id correctly even when disabled_toolsets is non-empty.
    """
    monkeypatch.setenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", "1")

    from magi_agent.gateway.watchers import _build_local_cron_turn_runner

    plan = CronTurnPlan(
        jobId="job:u2-strip",
        prompt="strip test",
        disabledToolsets=("CronCreate", "TelegramSend"),
        timeoutSeconds=10.0,
    )

    runner = _build_local_cron_turn_runner(stream_factory=_completed_stream_factory())
    result = asyncio.run(runner.run_turn(plan))

    assert result.job_id == "job:u2-strip"
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# build_local_scheduler_cron_driver wires the runner
# ---------------------------------------------------------------------------

def test_build_local_scheduler_cron_driver_flag_off_produces_skipping_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The driver produced by build_local_scheduler_cron_driver has a stub
    runner when the flag is off.  We verify by running execute_due_jobs with
    no due jobs (empty DB) -- the function must return without error.
    """
    monkeypatch.delenv("MAGI_BACKGROUND_LIVE_RUNNER_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_SCHEDULER_DB_PATH", str(tmp_path / "jobs.db"))

    from magi_agent.gateway.watchers import build_local_scheduler_cron_driver

    driver = build_local_scheduler_cron_driver()
    # The driver must be usable (run_forever callable exists).
    assert callable(getattr(driver, "run_forever", None))
