"""PR-K: deeper trace at the collector terminal.

Kevin's 0.1.88 SOTA-spawn trace pinpointed status=failed
reason=child_llm_collector_status_failed for anthropic / google /
fireworks children but did NOT surface WHAT made the Terminal !=
completed. The collector's existing ``status`` token collapses every
non-``completed`` terminal to the single string ``"failed"``; the new
``[governed_collector.trace] terminal_consumed`` stamp closes that
diagnostic gap.

This test module covers:

* :func:`_maybe_log_trace_governed_collector_terminal` directly (env
  gating, format, fail-safe behaviour, defensive ``getattr`` for
  ``error_code`` / ``reason`` / ``error``);
* the integration with :func:`collect_governed_child_turn` : the
  ``items_yielded`` counter MUST match the number of non-terminal
  events the stream produced.

All tests use ``capsys`` (the trace helper prints to ``sys.stderr``
via ``_emit_trace``); no file IO, no real provider calls.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.runtime.child_governed_collector import collect_governed_child_turn
from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_trace_governed_collector_terminal,
)
from magi_agent.runtime.events import RuntimeEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_empty_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force MAGI_CHILD_RUNNER_EMPTY_DEBUG=1 for every test in this module.

    The collector reads ``os.environ`` directly so we patch it here. Tests
    that need the OFF behaviour explicitly clear the var inside the test
    body.
    """
    monkeypatch.setenv(CHILD_RUNNER_EMPTY_DEBUG_ENV, "1")


async def _stream_completed(deltas: list[str]):
    for delta in deltas:
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": delta})
    yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)


async def _stream_aborted(deltas: list[str]):
    for delta in deltas:
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": delta})
    yield EngineResult(
        terminal=Terminal.aborted,
        usage={},
        cost_usd=0.0,
        error="customize_policy_blocked: slot=pre_final; reason=test",
    )


def _engine_result_with_extras(
    terminal: Terminal,
    *,
    error: str | None = None,
    error_code: object = None,
    reason: object = None,
) -> EngineResult:
    """Build a real EngineResult and attach future-engine-version extras.

    EngineResult is a non-frozen dataclass so attribute assignment is
    permitted. The collector uses ``isinstance(item, EngineResult)`` so
    the SimpleNamespace fake would not be recognised as a terminal.
    """
    result = EngineResult(terminal=terminal, usage={}, cost_usd=0.0, error=error)
    if error_code is not None:
        result.error_code = error_code  # type: ignore[attr-defined]
    if reason is not None:
        result.reason = reason  # type: ignore[attr-defined]
    return result


async def _stream_error_terminal_with_extras():
    """Yield 3 events then a custom terminal carrying error_code / reason."""
    yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "x"})
    yield RuntimeEvent(type="status", payload={"type": "turn.started"})
    yield RuntimeEvent(type="status", payload={"type": "tool_start"})
    yield _engine_result_with_extras(
        Terminal.error,
        error="kaboom",
        error_code="rate_limit_exceeded",
        reason="provider returned 429",
    )


# ---------------------------------------------------------------------------
# 1. Helper-direct format + env gating
# ---------------------------------------------------------------------------


def test_helper_silent_when_flag_off(capsys: pytest.CaptureFixture) -> None:
    """Default-OFF: zero output when the empty-debug env is unset."""
    _maybe_log_trace_governed_collector_terminal(
        {},  # no MAGI_CHILD_RUNNER_EMPTY_DEBUG
        terminal=EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0),
        status="completed",
        summary_len=5,
        evidence_refs_count=0,
        items_yielded=3,
    )
    assert capsys.readouterr().err == ""


def test_helper_logs_completed_terminal_with_all_counters(
    capsys: pytest.CaptureFixture,
) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_governed_collector_terminal(
        env,
        terminal=EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0),
        status="completed",
        summary_len=42,
        evidence_refs_count=2,
        items_yielded=7,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[governed_collector.trace] terminal_consumed" in err
    assert "terminal=completed" in err
    assert "status=completed" in err
    assert "summary_len=42" in err
    assert "evidence_refs=2" in err
    assert "items_yielded=7" in err
    assert "error_code=None" in err
    assert "reason=None" in err
    assert "error=None" in err


def test_helper_logs_error_code_and_reason_when_present(
    capsys: pytest.CaptureFixture,
) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    terminal = _engine_result_with_extras(
        Terminal.error,
        error="boom",
        error_code="rate_limit_exceeded",
        reason="provider returned 429",
    )
    _maybe_log_trace_governed_collector_terminal(
        env,
        terminal=terminal,
        status="failed",
        summary_len=0,
        evidence_refs_count=0,
        items_yielded=0,
    )
    err = capsys.readouterr().err
    assert "terminal=error" in err
    assert "status=failed" in err
    assert "error_code='rate_limit_exceeded'" in err
    assert "reason='provider returned 429'" in err
    assert "error='boom'" in err


def test_helper_never_raises_on_exotic_terminal(
    capsys: pytest.CaptureFixture,
) -> None:
    """Fail-safe: a terminal whose attribute access raises must not propagate."""

    class _Boom:
        def __getattr__(self, name: str) -> object:  # pragma: no cover - fault path
            raise RuntimeError("attribute access blew up")

    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    # The mere fact that this returns proves the helper swallowed the fault.
    _maybe_log_trace_governed_collector_terminal(
        env,
        terminal=_Boom(),
        status="failed",
        summary_len=0,
        evidence_refs_count=0,
        items_yielded=0,
    )
    _ = capsys.readouterr()


# ---------------------------------------------------------------------------
# 2. Integration with collect_governed_child_turn : required test names
# ---------------------------------------------------------------------------


def test_terminal_completed_emits_completed_status_trace(
    capsys: pytest.CaptureFixture,
) -> None:
    """Happy-path: Terminal.completed surfaces ``status=completed`` AND
    ``terminal=completed`` in the trace line."""
    asyncio.run(collect_governed_child_turn(_stream_completed(["he", "llo"])))
    err = capsys.readouterr().err
    assert "[governed_collector.trace] terminal_consumed" in err
    assert "terminal=completed" in err
    assert "status=completed" in err
    assert "summary_len=5" in err  # "hello" = 5 chars


def test_terminal_failed_emits_failed_status_with_terminal_name(
    capsys: pytest.CaptureFixture,
) -> None:
    """Non-completed terminal preserves the underlying enum NAME (``aborted``)
    in the trace even though the public ``status`` collapses to ``failed``."""
    asyncio.run(collect_governed_child_turn(_stream_aborted(["oops"])))
    err = capsys.readouterr().err
    assert "[governed_collector.trace] terminal_consumed" in err
    # status collapses to "failed" (public contract) ...
    assert "status=failed" in err
    # ... but the trace surfaces the underlying Terminal enum name so the
    # operator can distinguish aborted / max_turns / error.
    assert "terminal=aborted" in err
    # The EngineResult.error field flows through as one of the top-3
    # additional attributes the helper surfaces (kept defensive so a
    # missing field would print error=None).
    assert "error='customize_policy_blocked: slot=pre_final; reason=test'" in err


def test_items_yielded_counter_matches_stream_length(
    capsys: pytest.CaptureFixture,
) -> None:
    """The new ``items_yielded`` counter must equal the number of NON-terminal
    events the stream produced (not the number of text deltas)."""

    async def _three_non_terminals_then_done():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "a"})
        yield RuntimeEvent(type="status", payload={"type": "turn.started"})
        yield RuntimeEvent(type="tool", payload={"type": "tool_end"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    asyncio.run(collect_governed_child_turn(_three_non_terminals_then_done()))
    err = capsys.readouterr().err
    assert "[governed_collector.trace] terminal_consumed" in err
    assert "items_yielded=3" in err


def test_error_code_and_reason_appear_when_present(
    capsys: pytest.CaptureFixture,
) -> None:
    """A terminal carrying ``error_code`` / ``reason`` (future engine versions
    or test doubles) must surface both fields in the trace line."""
    asyncio.run(collect_governed_child_turn(_stream_error_terminal_with_extras()))
    err = capsys.readouterr().err
    assert "[governed_collector.trace] terminal_consumed" in err
    assert "terminal=error" in err
    assert "status=failed" in err
    assert "items_yielded=3" in err
    assert "error_code='rate_limit_exceeded'" in err
    assert "reason='provider returned 429'" in err
    assert "error='kaboom'" in err


# ---------------------------------------------------------------------------
# 3. OFF-path integration regression
# ---------------------------------------------------------------------------


def test_collector_integration_silent_when_env_off(
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When MAGI_CHILD_RUNNER_EMPTY_DEBUG is OFF the collector must NOT emit
    the terminal_consumed trace line : back-compat with pre-PR-K behaviour."""
    monkeypatch.delenv(CHILD_RUNNER_EMPTY_DEBUG_ENV, raising=False)
    # Sanity: env is actually OFF for this test
    assert os.environ.get(CHILD_RUNNER_EMPTY_DEBUG_ENV) is None
    asyncio.run(collect_governed_child_turn(_stream_completed(["hi"])))
    err = capsys.readouterr().err
    assert "[governed_collector.trace] terminal_consumed" not in err
