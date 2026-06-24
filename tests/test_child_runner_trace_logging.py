"""Operator-opt-in trace logging for child-runner dispatch path.

#918 added empty-result logging at the two ``_collect_turn_text`` branch
exits. Kevin's 0.1.82 SOTA-spawn repro with ``MAGI_CHILD_RUNNER_EMPTY_DEBUG=1``
showed: liveChildRunnerAttached=true, tool_status=ok, empty summary, AND
ZERO ``[child_runner.empty_debug]`` lines for the anthropic/fireworks/
gemini cases. That means the existing collector loggers (which sit AT
the end of both branches) never executed — the dispatch ended via a
DIFFERENT path that never reached ``_collect_turn_text`` at all.

This module adds six trace helpers in ``child_runner_live`` (entry,
route resolved, key resolved, turn enter, turn exit, degraded) and
two in ``child_runner_boundary`` (boundary output received, envelope
status coercion). Reusing the same ``MAGI_CHILD_RUNNER_EMPTY_DEBUG``
env keeps the operator's existing opt-in working: the next repro
prints the missing trail without rebuilding the wheel.
"""

from __future__ import annotations

import logging

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


# ---------------------------------------------------------------------- #
# child_runner_live trace helpers                                        #
# ---------------------------------------------------------------------- #


def test_trace_entry_silent_when_flag_off(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _maybe_log_trace_entry({}, provider="anthropic", model="claude-opus-4-8")
    assert caplog.records == []


def test_trace_entry_fires_when_flag_on(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_trace_entry(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "[child_runner.trace] entry" in msg
    assert "anthropic" in msg
    assert "claude-opus-4-8" in msg


def test_trace_route_logs_validated_status(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_trace_route(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        validated=False,
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "route_resolved" in msg
    assert "validated=False" in msg


def test_trace_key_logs_resolved_status(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_trace_key(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        key_resolved=False,
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "key_resolved" in msg
    assert "resolved=False" in msg


def test_trace_turn_enter_and_exit(caplog) -> None:
    caplog.set_level(logging.WARNING)
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "yes"}
    _maybe_log_trace_turn_enter(env, provider="anthropic", model="claude-opus-4-8")
    _maybe_log_trace_turn_exit(
        env,
        provider="anthropic",
        model="claude-opus-4-8",
        final_text_len=0,
        evidence_refs_count=0,
    )
    assert len(caplog.records) == 2
    assert "turn_enter" in caplog.records[0].getMessage()
    exit_msg = caplog.records[1].getMessage()
    assert "turn_exit" in exit_msg
    assert "final_text_len=0" in exit_msg
    assert "evidence_refs=0" in exit_msg


def test_trace_degraded_logs_reason(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_trace_degraded(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "on"},
        status="blocked",
        reason="child_provider_key_missing",
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "degraded" in msg
    assert "status=blocked" in msg
    assert "child_provider_key_missing" in msg


# ---------------------------------------------------------------------- #
# child_runner_boundary trace helpers                                    #
# ---------------------------------------------------------------------- #


def test_boundary_output_silent_when_flag_off(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _maybe_log_trace_boundary_output({}, output={"status": "completed", "summary": "hi"})
    assert caplog.records == []


def test_boundary_output_logs_status_and_summary_len(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_trace_boundary_output(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        output={"status": "completed", "summary": "hello", "evidenceRefs": ("evidence:abc",)},
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "boundary_output" in msg
    assert "status='completed'" in msg
    assert "summary_len=5" in msg
    assert "evidence_refs=1" in msg


def test_boundary_output_handles_missing_status(caplog) -> None:
    """The bug shape we're hunting: child output WITHOUT a ``status`` key
    would be silently coerced to ``completed`` by ``_envelope_from_output``.
    The boundary log must surface the absent-status signal so the operator
    sees the coercion is about to happen."""
    caplog.set_level(logging.WARNING)
    _maybe_log_trace_boundary_output(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        output={"summary": ""},  # NO status key.
    )
    assert len(caplog.records) == 1
    assert "status=None" in caplog.records[0].getMessage()


def test_envelope_coercion_logs_input_and_coerced(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_trace_envelope_coercion(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        input_status=None,
        coerced_status="completed",
        summary_len=0,
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "envelope_coercion" in msg
    assert "input_status=None" in msg
    assert "coerced_status='completed'" in msg


def test_envelope_coercion_silent_when_flag_off(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _maybe_log_trace_envelope_coercion(
        {},
        input_status="completed",
        coerced_status="completed",
        summary_len=10,
    )
    assert caplog.records == []


def test_trace_helpers_never_raise(caplog) -> None:
    """Trace logging must never break a turn even on exotic inputs."""
    caplog.set_level(logging.WARNING)
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
    # All 8 should have logged without raising.
    assert len(caplog.records) == 8
