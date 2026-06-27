"""PR-K: engine LLM dispatch trace (llm_call_start / completed / exception).

Kevin's 0.1.88 SOTA-spawn trace surfaced ``status=failed`` for
anthropic / google / fireworks children but the engine never logged
whether the adapter.run_turn dispatch even ENTERED a real LLM call,
let alone whether it raised. PR-K wires three matched stamps around
the canonical ``MagiEngineDriver._drive`` dispatch site:

* ``[engine.trace] llm_call_start attempt=<N> turn_id=<X>`` BEFORE
  ``adapter.run_turn(runner_input).__aiter__()``;
* ``[engine.trace] llm_call_completed attempt=<N> turn_id=<X>`` AFTER
  the inner event loop drains normally (no exception, no cancel);
* ``[engine.trace] llm_call_exception attempt=<N> turn_id=<X>
  exception=<class> message_first80=<sanitized>`` inside the existing
  ``except Exception as exc`` branch BEFORE the recovery layer
  captures the exception into ``attempt_error``.

The instrumentation is gated on the existing
``MAGI_CHILD_RUNNER_EMPTY_DEBUG`` env (no new flag). The OFF path is
byte-identical.

Coverage:

* helper-direct env gating + format for all three stamps;
* integration via ``MagiEngineDriver.run_turn_stream`` using
  ``MockRunner`` (normal text turn) : both ``llm_call_start`` and
  ``llm_call_completed`` MUST appear, ``llm_call_exception`` MUST NOT;
* integration via a runner whose ``run_async`` raises : both
  ``llm_call_start`` and ``llm_call_exception`` MUST appear, the
  exception class + message MUST surface, and the engine MUST report
  a Terminal.error result (the recovery layer captures the exception);
* OFF-path regression: with the env unset NONE of the three stamps
  appear.

Note: the engine has additional adapter.run_turn dispatch sites
(zero-edit + recovery re-invocation branches) that PR-K does NOT
instrument. The canonical ``_drive`` outer-loop dispatch is the only
one the operator needs to disambiguate the 0.1.88 SOTA-spawn failure;
follow-up PRs can extend if needed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.cli.headless import drain
from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_trace_engine_llm_call_completed,
    _maybe_log_trace_engine_llm_call_exception,
    _maybe_log_trace_engine_llm_call_start,
)
from tests.support.engine_fakes import MockRunner, text_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turn_input(session_id: str = "s-pr-k", turn_id: str = "t-pr-k") -> dict[str, Any]:
    return {"prompt": "go", "session_id": session_id, "turn_id": turn_id}


class _AlwaysFailRunner:
    """``run_async`` raises the given error on EVERY invocation.

    Matches the pattern in ``magi_agent/cli/tests/test_engine_recovery.py``
    so the engine's existing recovery / terminal-error path is exercised.
    """

    def __init__(self, error: Exception) -> None:
        self.invocations = 0
        self._error = error

    async def run_async(self, **_kwargs: object):
        self.invocations += 1
        raise self._error
        if False:  # pragma: no cover - generator type hint
            yield None


# ---------------------------------------------------------------------------
# 1. Helper-direct env gating + format
# ---------------------------------------------------------------------------


def test_llm_call_start_silent_when_flag_off(capsys: pytest.CaptureFixture) -> None:
    _maybe_log_trace_engine_llm_call_start({}, attempt=1, turn_id="t-1")
    assert capsys.readouterr().err == ""


def test_llm_call_completed_silent_when_flag_off(
    capsys: pytest.CaptureFixture,
) -> None:
    _maybe_log_trace_engine_llm_call_completed({}, attempt=1, turn_id="t-1")
    assert capsys.readouterr().err == ""


def test_llm_call_exception_silent_when_flag_off(
    capsys: pytest.CaptureFixture,
) -> None:
    _maybe_log_trace_engine_llm_call_exception(
        {}, attempt=1, turn_id="t-1", exception=RuntimeError("boom")
    )
    assert capsys.readouterr().err == ""


def test_llm_call_start_logs_attempt_and_turn_id(
    capsys: pytest.CaptureFixture,
) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_engine_llm_call_start(env, attempt=2, turn_id="t-abc")
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[engine.trace] llm_call_start" in err
    assert "attempt=2" in err
    assert "turn_id='t-abc'" in err


def test_llm_call_completed_logs_attempt_and_turn_id(
    capsys: pytest.CaptureFixture,
) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_engine_llm_call_completed(env, attempt=3, turn_id="t-xyz")
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[engine.trace] llm_call_completed" in err
    assert "attempt=3" in err
    assert "turn_id='t-xyz'" in err


def test_llm_call_exception_logs_class_and_truncated_message(
    capsys: pytest.CaptureFixture,
) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    long_msg = "boom-" + ("y" * 200)  # > 80 chars
    _maybe_log_trace_engine_llm_call_exception(
        env, attempt=1, turn_id="t-1", exception=ValueError(long_msg)
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[engine.trace] llm_call_exception" in err
    assert "attempt=1" in err
    assert "turn_id='t-1'" in err
    assert "exception=ValueError" in err
    # Message truncated to 80 chars: "y" repeated 200 times must NOT appear.
    assert ("y" * 200) not in err
    assert "boom-" in err  # prefix of the (truncated) message is present


# ---------------------------------------------------------------------------
# 2. Integration with MagiEngineDriver : required test names
# ---------------------------------------------------------------------------


def test_llm_call_start_and_completed_emitted_on_normal_call(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal MockRunner turn must emit BOTH ``llm_call_start`` and
    ``llm_call_completed`` AND must NOT emit ``llm_call_exception``."""
    monkeypatch.setenv(CHILD_RUNNER_EMPTY_DEBUG_ENV, "1")

    runner = MockRunner([text_event("hello", partial=False, turn_complete=True)])
    driver = MagiEngineDriver(runner=runner)
    cancel = asyncio.Event()

    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input(turn_id="t-normal"), cancel=cancel))
    )
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.completed

    err = capsys.readouterr().err
    assert "[engine.trace] llm_call_start" in err
    assert "attempt=1" in err
    assert "turn_id='t-normal'" in err
    assert "[engine.trace] llm_call_completed" in err
    # The exception variant MUST NOT appear on a clean run.
    assert "[engine.trace] llm_call_exception" not in err


def test_llm_call_exception_emitted_then_reraised(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the runner raises during dispatch the new exception trace must
    fire AND the engine must surface the failure (the existing recovery
    layer captures the exception into ``attempt_error`` and emits a
    Terminal.error envelope; ``llm_call_exception`` precedes that capture
    so the operator sees the failure independent of recovery)."""
    monkeypatch.setenv(CHILD_RUNNER_EMPTY_DEBUG_ENV, "1")

    runner = _AlwaysFailRunner(ValueError("dispatch-side-kaboom"))
    driver = MagiEngineDriver(runner=runner)
    cancel = asyncio.Event()

    _events, terminal = asyncio.run(
        drain(driver.run_turn_stream(None, _turn_input(turn_id="t-fail"), cancel=cancel))
    )
    # Engine's existing exception path: recovery=None → terminal error.
    assert isinstance(terminal, EngineResult)
    assert terminal.terminal is Terminal.error
    # Sanity: the runner was invoked exactly once (no recovery configured).
    assert runner.invocations == 1

    err = capsys.readouterr().err
    assert "[engine.trace] llm_call_start" in err
    assert "[engine.trace] llm_call_exception" in err
    assert "attempt=1" in err
    assert "turn_id='t-fail'" in err
    assert "exception=ValueError" in err
    assert "dispatch-side-kaboom" in err
    # On the exception path the matching ``llm_call_completed`` line MUST
    # NOT appear : completion only fires when the dispatch exited cleanly.
    assert "[engine.trace] llm_call_completed" not in err


def test_no_trace_when_env_off(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OFF-path regression: with the env unset NONE of the three engine
    trace stamps appear : both normal and exception flows are silent."""
    monkeypatch.delenv(CHILD_RUNNER_EMPTY_DEBUG_ENV, raising=False)

    # Normal turn first.
    runner_ok = MockRunner([text_event("ok", partial=False, turn_complete=True)])
    driver_ok = MagiEngineDriver(runner=runner_ok)
    cancel_ok = asyncio.Event()
    asyncio.run(
        drain(driver_ok.run_turn_stream(None, _turn_input(turn_id="t-off-ok"), cancel=cancel_ok))
    )

    # Then a raising turn : same env-OFF flow.
    runner_fail = _AlwaysFailRunner(RuntimeError("silent-failure"))
    driver_fail = MagiEngineDriver(runner=runner_fail)
    cancel_fail = asyncio.Event()
    asyncio.run(
        drain(
            driver_fail.run_turn_stream(None, _turn_input(turn_id="t-off-fail"), cancel=cancel_fail)
        )
    )

    err = capsys.readouterr().err
    assert "[engine.trace] llm_call_start" not in err
    assert "[engine.trace] llm_call_completed" not in err
    assert "[engine.trace] llm_call_exception" not in err
