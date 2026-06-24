"""Operator-opt-in trace logging for child-runner dispatch path.

#990 added six trace helpers in child_runner_live + two in boundary that
log on entry / route / key / turn_enter / turn_exit / degraded /
boundary_output / envelope_coercion. Kevin's 0.1.84 SOTA-spawn repro
with the env ON produced ZERO lines: the helpers were calling
``_logger.warning(...)`` but ``magi-serve`` doesn't call
``logging.basicConfig`` / ``dictConfig`` so the root logger has no
handler attached. Emit goes through ``sys.stderr`` now (same stream
uvicorn/pydantic warnings use); this module pins that channel via
``capsys``.
"""

from __future__ import annotations

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


def test_trace_entry_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_entry({}, provider="anthropic", model="claude-opus-4-8")
    assert capsys.readouterr().err == ""


def test_trace_entry_fires_when_flag_on(capsys) -> None:
    _maybe_log_trace_entry(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[child_runner.trace] entry" in err
    assert "anthropic" in err
    assert "claude-opus-4-8" in err


def test_trace_route_logs_validated_status(capsys) -> None:
    _maybe_log_trace_route(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        validated=False,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "route_resolved" in err
    assert "validated=False" in err


def test_trace_key_logs_resolved_status(capsys) -> None:
    _maybe_log_trace_key(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        key_resolved=False,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "key_resolved" in err
    assert "resolved=False" in err


def test_trace_turn_enter_and_exit(capsys) -> None:
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "yes"}
    _maybe_log_trace_turn_enter(env, provider="anthropic", model="claude-opus-4-8")
    _maybe_log_trace_turn_exit(
        env,
        provider="anthropic",
        model="claude-opus-4-8",
        final_text_len=0,
        evidence_refs_count=0,
    )
    err = capsys.readouterr().err
    lines = [line for line in err.split("\n") if line.strip()]
    assert len(lines) == 2
    assert "turn_enter" in lines[0]
    assert "turn_exit" in lines[1]
    assert "final_text_len=0" in lines[1]
    assert "evidence_refs=0" in lines[1]


def test_trace_degraded_logs_reason(capsys) -> None:
    _maybe_log_trace_degraded(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "on"},
        status="blocked",
        reason="child_provider_key_missing",
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "degraded" in err
    assert "status=blocked" in err
    assert "child_provider_key_missing" in err


# ---------------------------------------------------------------------- #
# child_runner_boundary trace helpers                                    #
# ---------------------------------------------------------------------- #


def test_boundary_output_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_boundary_output({}, output={"status": "completed", "summary": "hi"})
    assert capsys.readouterr().err == ""


def test_boundary_output_logs_status_and_summary_len(capsys) -> None:
    _maybe_log_trace_boundary_output(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        output={"status": "completed", "summary": "hello", "evidenceRefs": ("evidence:abc",)},
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "boundary_output" in err
    assert "status='completed'" in err
    assert "summary_len=5" in err
    assert "evidence_refs=1" in err


def test_boundary_output_handles_missing_status(capsys) -> None:
    """The bug shape we're hunting: child output WITHOUT a ``status`` key
    would be silently coerced to ``completed`` by ``_envelope_from_output``.
    The boundary log must surface the absent-status signal so the operator
    sees the coercion is about to happen."""
    _maybe_log_trace_boundary_output(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        output={"summary": ""},  # NO status key.
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "status=None" in err


def test_envelope_coercion_logs_input_and_coerced(capsys) -> None:
    _maybe_log_trace_envelope_coercion(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        input_status=None,
        coerced_status="completed",
        summary_len=0,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "envelope_coercion" in err
    assert "input_status=None" in err
    assert "coerced_status='completed'" in err


def test_envelope_coercion_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_envelope_coercion(
        {},
        input_status="completed",
        coerced_status="completed",
        summary_len=10,
    )
    assert capsys.readouterr().err == ""


def test_trace_helpers_never_raise(capsys) -> None:
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
    err = capsys.readouterr().err
    # All 8 should have logged without raising.
    assert err.count("\n") == 8
