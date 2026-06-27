"""PR-K: run_governed_turn yield-loop exception trace.

The PR-H ``[governed_turn.trace] yield_loop_exit reason=exception``
stamp told the operator the loop raised but did not name WHAT raised.
PR-K adds a dedicated ``except Exception`` branch above the pre-
existing BaseException catch that surfaces the exception class plus
the FIRST 80 chars of ``str(exc)`` (stripped) BEFORE re-raising. The
exception flow is preserved: the new branch ALWAYS re-raises so the
upstream consumer / finalize trace is byte-identical to today.

Coverage:

* :func:`_maybe_log_trace_governed_yield_loop_exception` direct (env
  gating, format, sanitisation, fail-safe);
* :func:`run_governed_turn` integration : an engine whose stream
  raises an :class:`Exception` mid-loop must (a) emit the new trace
  line and (b) re-raise the exception unchanged;
* a normal-completion run MUST NOT emit the new trace.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_trace_governed_yield_loop_exception,
)
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.turn_context import TurnContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NormalEngine:
    """Engine stub that yields one event then exits cleanly (no terminal)."""

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: object,
        gate: object,
    ):
        yield "first-event"


class _RaisingEngine:
    """Engine stub that yields one event then raises an Exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: object,
        gate: object,
    ):
        yield "first-event"
        raise self._exc


def _runtime_with(engine: object) -> SimpleNamespace:
    return SimpleNamespace(engine=engine, gate=None)


async def _drain(agen) -> list[object]:
    out: list[object] = []
    async for item in agen:
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 1. Helper-direct
# ---------------------------------------------------------------------------


def test_helper_silent_when_flag_off(capsys: pytest.CaptureFixture) -> None:
    _maybe_log_trace_governed_yield_loop_exception(
        {},
        exception=RuntimeError("hidden"),
    )
    assert capsys.readouterr().err == ""


def test_helper_logs_class_and_truncated_message(
    capsys: pytest.CaptureFixture,
) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    # 200-char message ensures truncation to 80 is visible.
    long_msg = "boom-" + ("x" * 195)
    _maybe_log_trace_governed_yield_loop_exception(
        env,
        exception=RuntimeError(long_msg),
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[governed_turn.trace] yield_loop_exception" in err
    assert "exception=RuntimeError" in err
    # Must be truncated to <= 80 chars of message; the full 200-char body
    # must not appear in the trace line.
    assert "boom-" in err
    assert ("x" * 195) not in err
    # Sanitised value is wrapped in repr() so the message segment is
    # quoted; verify the start of the truncated body is present.
    assert "message_first80=" in err


def test_helper_strips_leading_trailing_whitespace(
    capsys: pytest.CaptureFixture,
) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_governed_yield_loop_exception(
        env,
        exception=ValueError("  surrounded  \n"),
    )
    err = capsys.readouterr().err
    # The repr of the stripped message is 'surrounded' (no leading/trailing
    # whitespace).
    assert "message_first80='surrounded'" in err


def test_helper_never_raises_on_exotic_exception(
    capsys: pytest.CaptureFixture,
) -> None:
    """An exception whose __str__ raises must not break the helper."""

    class _BadStr(Exception):
        def __str__(self) -> str:  # pragma: no cover - intentional fault path
            raise RuntimeError("str() blew up")

    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_governed_yield_loop_exception(env, exception=_BadStr())
    err = capsys.readouterr().err
    # Helper emitted the line (class name + empty message_first80) without
    # propagating the inner failure.
    assert "[governed_turn.trace] yield_loop_exception" in err
    assert "exception=_BadStr" in err


# ---------------------------------------------------------------------------
# 2. Integration with run_governed_turn : required test names
# ---------------------------------------------------------------------------


def test_exception_raised_in_yield_loop_logged_then_reraised(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an Exception propagates out of the engine stream the new
    ``yield_loop_exception`` trace MUST fire BEFORE the re-raise, and the
    exception MUST surface to the caller unchanged."""
    monkeypatch.setenv(CHILD_RUNNER_EMPTY_DEBUG_ENV, "1")

    exc = RuntimeError("kaboom-during-yield")
    rt = _runtime_with(_RaisingEngine(exc))
    ctx = TurnContext(prompt="go", session_id="s-pr-k", turn_id="t-pr-k")

    with pytest.raises(RuntimeError, match="kaboom-during-yield"):
        asyncio.run(_drain(run_governed_turn(ctx, runtime=rt)))

    err = capsys.readouterr().err
    assert "[governed_turn.trace] yield_loop_exception" in err
    assert "exception=RuntimeError" in err
    assert "kaboom-during-yield" in err
    # Sibling PR-H exit stamp must STILL fire on the same exit path so the
    # operator gets both lines (PR-K narrows what raised; PR-H bookends).
    assert "[governed_turn.trace] yield_loop_exit" in err
    assert "reason='exception'" in err


def test_normal_completion_does_not_emit_exception_trace(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean async-for completion MUST NOT emit the new exception trace.

    Only the PR-H ``yield_loop_exit reason='normal'`` line is expected.
    """
    monkeypatch.setenv(CHILD_RUNNER_EMPTY_DEBUG_ENV, "1")

    rt = _runtime_with(_NormalEngine())
    ctx = TurnContext(prompt="go", session_id="s-pr-k", turn_id="t-pr-k-2")

    asyncio.run(_drain(run_governed_turn(ctx, runtime=rt)))

    err = capsys.readouterr().err
    assert "[governed_turn.trace] yield_loop_exception" not in err
    # PR-H sibling line still fires; reason='normal'.
    assert "[governed_turn.trace] yield_loop_exit" in err
    assert "reason='normal'" in err
