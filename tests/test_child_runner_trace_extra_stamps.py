"""PR-1 (Investigation instrumentation): 3 new trace stamps for the silent
anthropic/google child-runner dispatch hunt.

Kevin's 0.1.85 SOTA-spawn trace shows anthropic/google child runners return
``status=ok`` with an empty summary in 100~250ms while openai dispatches a
real LLM call in 5~18s. The trace surface added by #990 stops at
``turn_enter`` / ``turn_exit``; the gap between those two stamps is exactly
where the silent-empty dispatch lives.

This module pins the three new stamps that close that gap:

* ``_maybe_log_trace_drive_one_turn`` (enter + exit of ``_drive_one_turn``)
  so the operator sees the live config the dispatch actually used (provider
  + model + ``id(config)``) instead of the init-field placeholder the
  #918 collector loggers print.
* ``_maybe_log_trace_engine_stream_yield`` (bounded cadence: first five +
  the last) inside the governed-turn stream loop so the operator can see
  whether the engine yielded ANY items at all and whether each item carried
  a ``text_delta`` payload or only recipe-metadata ``evidence_refs``.
* ``_maybe_log_trace_envelope_pre`` (right before
  ``_envelope_from_output(request, output, ...)`` runs in
  ``_run_live_child``) so the operator can see the raw ``status`` +
  ``summary[:80]`` + ``evidence_refs`` count one beat earlier than the
  existing ``_maybe_log_trace_boundary_output`` helper.

All three obey ``MAGI_CHILD_RUNNER_EMPTY_DEBUG`` (no new env). Default-OFF.

PR-G moved the emit channel from stderr to a dedicated file (default
``~/.openmagi/trace.log``; override with ``MAGI_TRACE_LOG_PATH``) so the
trace stamps no longer vanish into a wedged uvicorn stderr FD during
long-running sessions. These tests read the file channel back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.runtime import trace_sink
from magi_agent.runtime.child_runner_boundary import (
    _maybe_log_trace_envelope_pre,
)
from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_trace_drive_one_turn,
    _maybe_log_trace_engine_stream_yield,
)


@pytest.fixture
def trace_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
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
# _maybe_log_trace_drive_one_turn                                        #
# ---------------------------------------------------------------------- #


def test_drive_one_turn_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_trace_drive_one_turn(
        {},
        phase="enter",
        provider="anthropic",
        model="claude-opus-4-8",
        config_id=12345,
    )
    assert _read_lines(trace_log) == []


def test_drive_one_turn_logs_enter_and_exit_phases(trace_log: Path) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_drive_one_turn(
        env,
        phase="enter",
        provider="anthropic",
        model="claude-opus-4-8",
        config_id=12345,
    )
    _maybe_log_trace_drive_one_turn(
        env,
        phase="exit",
        provider="anthropic",
        model="claude-opus-4-8",
        config_id=12345,
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 2
    assert "[child_runner.trace] drive_one_turn_enter" in lines[0]
    assert "[child_runner.trace] drive_one_turn_exit" in lines[1]
    assert "provider='anthropic'" in lines[0]
    assert "model='claude-opus-4-8'" in lines[0]
    assert "config_id=12345" in lines[0]
    assert "config_id=12345" in lines[1]


# ---------------------------------------------------------------------- #
# _maybe_log_trace_engine_stream_yield                                   #
# ---------------------------------------------------------------------- #


def test_engine_stream_yield_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_trace_engine_stream_yield(
        {},
        index=0,
        kind="text_delta",
        has_text_delta=True,
        evidence_refs_in_payload=0,
    )
    assert _read_lines(trace_log) == []


def test_engine_stream_yield_logs_fields(trace_log: Path) -> None:
    _maybe_log_trace_engine_stream_yield(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        index=3,
        kind="text_delta",
        has_text_delta=True,
        evidence_refs_in_payload=2,
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    line = lines[0]
    assert "[governed_turn.trace] stream_yield" in line
    assert "i=3" in line
    assert "kind=text_delta" in line
    assert "has_text_delta=True" in line
    assert "evidence_refs_in_payload=2" in line


# ---------------------------------------------------------------------- #
# _maybe_log_trace_envelope_pre                                          #
# ---------------------------------------------------------------------- #


def test_envelope_pre_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_trace_envelope_pre(
        {},
        output={"status": "completed", "summary": "hi", "evidenceRefs": ()},
    )
    assert _read_lines(trace_log) == []


def test_envelope_pre_logs_status_summary_first80_and_refs(trace_log: Path) -> None:
    summary = "a" * 100  # > 80 chars so the truncation is visible.
    _maybe_log_trace_envelope_pre(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        output={
            "status": "completed",
            "summary": summary,
            "evidenceRefs": ("evidence:abc", "evidence:def"),
        },
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    line = lines[0]
    assert "[boundary.trace] envelope_pre" in line
    assert "status='completed'" in line
    # The helper must surface up to the first 80 chars (not the whole 100).
    assert "summary_first80=" in line
    assert "a" * 80 in line
    assert "a" * 90 not in line
    assert "evidence_refs=2" in line


# ---------------------------------------------------------------------- #
# Robustness: never raise on exotic inputs                              #
# ---------------------------------------------------------------------- #


def test_new_trace_helpers_never_raise(trace_log: Path) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    _maybe_log_trace_drive_one_turn(env, phase="enter", provider=None, model=None, config_id=None)
    _maybe_log_trace_drive_one_turn(env, phase="exit", provider=None, model=None, config_id=None)
    _maybe_log_trace_engine_stream_yield(
        env, index=0, kind=None, has_text_delta=False, evidence_refs_in_payload=0
    )
    _maybe_log_trace_envelope_pre(env, output=None)
    lines = _read_lines(trace_log)
    # All 4 invocations must have produced a single line each without raising.
    assert len(lines) == 4
