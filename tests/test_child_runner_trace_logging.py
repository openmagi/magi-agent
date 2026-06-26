"""Operator-opt-in trace logging for child-runner dispatch path.

#990 added six trace helpers in child_runner_live + two in boundary that
log on entry / route / key / turn_enter / turn_exit / degraded /
boundary_output / envelope_coercion. Kevin's 0.1.84 SOTA-spawn repro
with the env ON produced ZERO lines: the helpers were calling
``_logger.warning(...)`` but ``magi-serve`` doesn't call
``logging.basicConfig`` / ``dictConfig`` so the root logger has no
handler attached.

PR #994 routed emit through ``sys.stderr``. PR-G then moved the channel
to a dedicated file (``~/.openmagi/trace.log`` by default, override with
``MAGI_TRACE_LOG_PATH``) after Kevin's 0.1.86 long-running session showed
the uvicorn stderr FD wedging mid-session. These tests pin the file
channel by pointing the sink at ``tmp_path`` and reading the file back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.runtime import trace_sink
from magi_agent.runtime.child_runner_boundary import (
    _maybe_log_trace_boundary_output,
    _maybe_log_trace_envelope_coercion,
)
from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_trace_degraded,
    _maybe_log_trace_entry,
    _maybe_log_trace_key,
    _maybe_log_trace_route,
    _maybe_log_trace_turn_enter,
    _maybe_log_trace_turn_exit,
)


@pytest.fixture
def trace_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the file-backed sink at ``tmp_path/trace.log`` for this test.

    Also clears the module-level FD before and after so a previous test
    cannot leak its open handle into this one.
    """
    path = tmp_path / "trace.log"
    monkeypatch.setenv(trace_sink.MAGI_TRACE_LOG_PATH_ENV, str(path))
    trace_sink.reset_trace_fd_for_tests()
    yield path
    trace_sink.reset_trace_fd_for_tests()


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------- #
# child_runner_live trace helpers                                        #
# ---------------------------------------------------------------------- #


def test_trace_entry_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_trace_entry({}, provider="anthropic", model="claude-opus-4-8")
    assert _read_lines(trace_log) == []


def test_trace_entry_fires_when_flag_on(trace_log: Path) -> None:
    _maybe_log_trace_entry(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    assert "[child_runner.trace] entry" in lines[0]
    assert "anthropic" in lines[0]
    assert "claude-opus-4-8" in lines[0]


def test_trace_route_logs_validated_status(trace_log: Path) -> None:
    _maybe_log_trace_route(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        validated=False,
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    assert "route_resolved" in lines[0]
    assert "validated=False" in lines[0]


def test_trace_key_logs_resolved_status(trace_log: Path) -> None:
    _maybe_log_trace_key(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        key_resolved=False,
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    assert "key_resolved" in lines[0]
    assert "resolved=False" in lines[0]


def test_trace_turn_enter_and_exit(trace_log: Path) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "yes"}
    _maybe_log_trace_turn_enter(env, provider="anthropic", model="claude-opus-4-8")
    _maybe_log_trace_turn_exit(
        env,
        provider="anthropic",
        model="claude-opus-4-8",
        final_text_len=0,
        evidence_refs_count=0,
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 2
    assert "turn_enter" in lines[0]
    assert "turn_exit" in lines[1]
    assert "final_text_len=0" in lines[1]
    assert "evidence_refs=0" in lines[1]


def test_trace_degraded_logs_reason(trace_log: Path) -> None:
    _maybe_log_trace_degraded(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "on"},
        status="blocked",
        reason="child_provider_key_missing",
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    assert "degraded" in lines[0]
    assert "status=blocked" in lines[0]
    assert "child_provider_key_missing" in lines[0]


# ---------------------------------------------------------------------- #
# child_runner_boundary trace helpers                                    #
# ---------------------------------------------------------------------- #


def test_boundary_output_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_trace_boundary_output({}, output={"status": "completed", "summary": "hi"})
    assert _read_lines(trace_log) == []


def test_boundary_output_logs_status_and_summary_len(trace_log: Path) -> None:
    _maybe_log_trace_boundary_output(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        output={"status": "completed", "summary": "hello", "evidenceRefs": ("evidence:abc",)},
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    assert "boundary_output" in lines[0]
    assert "status='completed'" in lines[0]
    assert "summary_len=5" in lines[0]
    assert "evidence_refs=1" in lines[0]


def test_boundary_output_handles_missing_status(trace_log: Path) -> None:
    """The bug shape we're hunting: child output WITHOUT a ``status`` key
    would be silently coerced to ``completed`` by ``_envelope_from_output``.
    The boundary log must surface the absent-status signal so the operator
    sees the coercion is about to happen."""
    _maybe_log_trace_boundary_output(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        output={"summary": ""},  # NO status key.
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    assert "status=None" in lines[0]


def test_envelope_coercion_logs_input_and_coerced(trace_log: Path) -> None:
    _maybe_log_trace_envelope_coercion(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        input_status=None,
        coerced_status="completed",
        summary_len=0,
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    assert "envelope_coercion" in lines[0]
    assert "input_status=None" in lines[0]
    assert "coerced_status='completed'" in lines[0]


def test_envelope_coercion_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_trace_envelope_coercion(
        {},
        input_status="completed",
        coerced_status="completed",
        summary_len=10,
    )
    assert _read_lines(trace_log) == []


def test_trace_helpers_never_raise(trace_log: Path) -> None:
    """Trace logging must never break a turn even on exotic inputs."""
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_entry(env, provider=None, model=None)
    _maybe_log_trace_route(env, provider=None, model=None, validated=None)
    _maybe_log_trace_key(env, provider=None, model=None, key_resolved=None)
    _maybe_log_trace_turn_enter(env, provider=None, model=None)
    _maybe_log_trace_turn_exit(
        env, provider=None, model=None, final_text_len=0, evidence_refs_count=0
    )
    _maybe_log_trace_degraded(env, status=None, reason=None)
    _maybe_log_trace_boundary_output(env, output=None)
    _maybe_log_trace_envelope_coercion(env, input_status=None, coerced_status=None, summary_len=0)
    lines = _read_lines(trace_log)
    # All 8 should have logged without raising.
    assert len(lines) == 8
