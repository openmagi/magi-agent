"""PR-H: instrumentation for the silent ``turn_end``-not-emitted finalize path.

Kevin's 0.1.86 Tesla 10-K capture showed 39 ``tool_end`` rows for the main
turn and ZERO ``turn_end`` events. The dashboard rendered "Work started,
but no final answer text arrived. Please try again." but no layer logged
which finalize stage swallowed the result.

This module pins six trace helpers in
:mod:`magi_agent.runtime.child_runner_live` that stamp every step of the
main-turn finalize path. All gated on the existing
``MAGI_CHILD_RUNNER_EMPTY_DEBUG`` env (no new flag). All write through
``_emit_trace`` (stderr, never raises).

Default-OFF: each helper is silent when the env is empty / not in
``{"1","true","yes","on"}``; default-ON paths emit exactly one stderr
line per stamp. Every helper swallows its own exceptions so trace logging
can never break a turn.
"""

from __future__ import annotations

from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_trace_chat_turn_handler_exit,
    _maybe_log_trace_chat_turn_start,
    _maybe_log_trace_engine_run_turn_stream_finalize,
    _maybe_log_trace_governed_turn_end_audit_fired,
    _maybe_log_trace_governed_yield_loop_exit,
    _maybe_log_trace_turn_engine_stream_consumed,
)


# ---------------------------------------------------------------------- #
# 1. chat_routes / streaming_chat_route handler entry + exit             #
# ---------------------------------------------------------------------- #


def test_chat_turn_start_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_chat_turn_start({}, session_id="sess-1", turn_id="t-1")
    assert capsys.readouterr().err == ""


def test_chat_turn_start_fires_when_flag_on(capsys) -> None:
    _maybe_log_trace_chat_turn_start(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        session_id="sess-1",
        turn_id="t-1",
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[chat_routes.trace] turn_start" in err
    assert "session_id='sess-1'" in err
    assert "turn_id='t-1'" in err


def test_chat_turn_handler_exit_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_chat_turn_handler_exit(
        {},
        session_id="sess-1",
        turn_id="t-1",
        final_text_len=0,
        exception=None,
    )
    assert capsys.readouterr().err == ""


def test_chat_turn_handler_exit_normal_path(capsys) -> None:
    _maybe_log_trace_chat_turn_handler_exit(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "yes"},
        session_id="sess-1",
        turn_id="t-1",
        final_text_len=42,
        exception=None,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[chat_routes.trace] turn_handler_exit" in err
    assert "session_id='sess-1'" in err
    assert "turn_id='t-1'" in err
    assert "final_text_len=42" in err
    assert "exception=None" in err


def test_chat_turn_handler_exit_exception_path(capsys) -> None:
    _maybe_log_trace_chat_turn_handler_exit(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "on"},
        session_id="sess-1",
        turn_id="t-1",
        final_text_len=0,
        exception=RuntimeError,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    # Logs ONLY the exception class name (the message can carry user data).
    assert "exception=RuntimeError" in err
    # Defensive: never the str() of the exception value.
    assert "Traceback" not in err


# ---------------------------------------------------------------------- #
# 2. turn_engine.py stream-consumed stamp                                #
# ---------------------------------------------------------------------- #


def test_turn_engine_stream_consumed_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_turn_engine_stream_consumed({}, turn_id="t-1", items=0, terminal_kind=None)
    assert capsys.readouterr().err == ""


def test_turn_engine_stream_consumed_fires_when_flag_on(capsys) -> None:
    _maybe_log_trace_turn_engine_stream_consumed(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        turn_id="t-1",
        items=5,
        terminal_kind="EngineResult",
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[turn_engine.trace] stream_consumed" in err
    assert "turn_id='t-1'" in err
    assert "items=5" in err
    assert "terminal_kind='EngineResult'" in err


# ---------------------------------------------------------------------- #
# 3. governed_turn.py yield_loop_exit (finally block)                    #
# ---------------------------------------------------------------------- #


def test_yield_loop_exit_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_governed_yield_loop_exit({}, reason="normal", items_yielded=0)
    assert capsys.readouterr().err == ""


def test_yield_loop_exit_normal_reason(capsys) -> None:
    _maybe_log_trace_governed_yield_loop_exit(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        reason="normal",
        items_yielded=12,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[governed_turn.trace] yield_loop_exit" in err
    assert "reason='normal'" in err
    assert "items_yielded=12" in err


def test_yield_loop_exit_exception_reason(capsys) -> None:
    _maybe_log_trace_governed_yield_loop_exit(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        reason="exception",
        items_yielded=3,
    )
    err = capsys.readouterr().err
    assert "reason='exception'" in err
    assert "items_yielded=3" in err


# ---------------------------------------------------------------------- #
# 4. governed_turn._AfterTurnEndCollector.run_audit                      #
# ---------------------------------------------------------------------- #


def test_turn_end_audit_fired_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_governed_turn_end_audit_fired({}, session_id="sess-1", result_text_len=0)
    assert capsys.readouterr().err == ""


def test_turn_end_audit_fired_when_flag_on(capsys) -> None:
    _maybe_log_trace_governed_turn_end_audit_fired(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "true"},
        session_id="sess-1",
        result_text_len=128,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[governed_turn.trace] turn_end_audit_fired" in err
    assert "session_id='sess-1'" in err
    assert "result_text_len=128" in err


# ---------------------------------------------------------------------- #
# 5. cli/engine.py MagiEngineDriver.run_turn_stream finalize             #
# ---------------------------------------------------------------------- #


def test_run_turn_stream_finalize_silent_when_flag_off(capsys) -> None:
    _maybe_log_trace_engine_run_turn_stream_finalize(
        {},
        turn_id="t-1",
        terminal=None,
        text_len=0,
        exception=None,
    )
    assert capsys.readouterr().err == ""


def test_run_turn_stream_finalize_normal_path(capsys) -> None:
    _maybe_log_trace_engine_run_turn_stream_finalize(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        turn_id="t-1",
        terminal="EngineResult",
        text_len=256,
        exception=None,
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "[engine.trace] run_turn_stream_finalize" in err
    assert "turn_id='t-1'" in err
    assert "terminal='EngineResult'" in err
    assert "text_len=256" in err
    assert "exception=None" in err


def test_run_turn_stream_finalize_exception_path(capsys) -> None:
    _maybe_log_trace_engine_run_turn_stream_finalize(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        turn_id="t-1",
        terminal=None,
        text_len=0,
        exception=ValueError,
    )
    err = capsys.readouterr().err
    assert "exception=ValueError" in err
    # Never log message/value text; only class name.
    assert "Traceback" not in err


# ---------------------------------------------------------------------- #
# 6. Defensive: exotic input never raises                                #
# ---------------------------------------------------------------------- #


class _Boom:
    def __repr__(self) -> str:  # pragma: no cover - intentional fault path
        raise RuntimeError("repr blew up")


def test_helpers_never_raise_on_exotic_input(capsys) -> None:
    """Every trace helper MUST swallow its own faults.

    A logging helper that raises would break the live turn it is trying to
    diagnose. Feed every helper an object whose ``__repr__`` raises and
    confirm none propagates.
    """
    env = {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"}
    boom = _Boom()
    # All six helpers MUST swallow the repr fault internally.
    _maybe_log_trace_chat_turn_start(env, session_id=boom, turn_id=boom)  # type: ignore[arg-type]
    _maybe_log_trace_chat_turn_handler_exit(
        env,
        session_id=boom,
        turn_id=boom,
        final_text_len=0,
        exception=None,  # type: ignore[arg-type]
    )
    _maybe_log_trace_turn_engine_stream_consumed(
        env,
        turn_id=boom,
        items=0,
        terminal_kind=boom,  # type: ignore[arg-type]
    )
    _maybe_log_trace_governed_yield_loop_exit(
        env,
        reason=boom,
        items_yielded=0,  # type: ignore[arg-type]
    )
    _maybe_log_trace_governed_turn_end_audit_fired(
        env,
        session_id=boom,
        result_text_len=0,  # type: ignore[arg-type]
    )
    _maybe_log_trace_engine_run_turn_stream_finalize(
        env,
        turn_id=boom,
        terminal=boom,
        text_len=0,
        exception=None,  # type: ignore[arg-type]
    )
    # Reaching here = no helper raised.
    # capsys may capture nothing (all reprs failed inside the try/except),
    # which is the documented fail-safe behavior.
    _ = capsys.readouterr()
