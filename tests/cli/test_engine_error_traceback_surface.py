"""When the engine's run_async raises and recovery doesn't recover, the
``attempt_error`` carries the real upstream cause (a tool that raised, a
LiteLlm provider failure, an ADK invariant violation, ...). Pre-fix, the
engine only kept ``str(attempt_error)`` and emitted ``EngineResult(error=...)``
— the exception CLASS and the traceback (the only signals that let the
operator name the actual trigger) were lost.

This test pins the new structural contract:

  * Each orphan ``tool_end`` event carries a sanitized ``errorDetail``
    mapping (errorClass / message / tracebackPreview) so the dashboard
    can show *why* that specific tool was swept.
  * A new ``engine_error_detail`` status event fires ONCE per turn,
    BEFORE the orphan sweep + EngineResult, so the Work pane can render
    a single banner with the real upstream class + sanitized traceback.
  * Cancellation paths still emit ``turn_end`` with reason
    ``user_interrupt`` and DO NOT carry an errorDetail (no upstream
    exception to report).

Sanitization rules borrow from the existing gate5b boundary so the same
path/secret patterns are redacted at the public seam.
"""
from __future__ import annotations

from magi_agent.cli.engine import MagiEngineDriver


def test_sanitized_traceback_helper_redacts_paths_and_secrets() -> None:
    raw = (
        'Traceback (most recent call last):\n'
        '  File "/Users/kevin/secret/runner.py", line 12, in run\n'
        '    auth_token = "sk-live-AAAAAAAAAAAAAAAAAAAAAAAAA"\n'
        'RuntimeError: provider call failed for /Users/kevin\n'
    )
    safe = MagiEngineDriver._sanitize_traceback_for_status(raw)
    # Home-dir leak gone.
    assert "/Users/kevin" not in safe, safe
    # API-key fragment gone.
    assert "sk-live-" not in safe, safe
    # The structural shape is preserved enough to read.
    assert "RuntimeError" in safe
    assert "Traceback" in safe


def test_sanitized_traceback_helper_caps_length() -> None:
    huge = "x" * 50_000
    safe = MagiEngineDriver._sanitize_traceback_for_status(huge)
    # Hard cap so the status payload never balloons regardless of input.
    assert len(safe) <= 2_500, len(safe)
    # Cap marker so the consumer can tell it was truncated.
    assert "truncated" in safe.lower() or safe.endswith("…")


def test_sanitized_traceback_helper_empty_input() -> None:
    assert MagiEngineDriver._sanitize_traceback_for_status("") == ""
    assert MagiEngineDriver._sanitize_traceback_for_status(None) == ""  # type: ignore[arg-type]


def test_capture_error_detail_returns_class_message_and_traceback() -> None:
    try:
        raise RuntimeError("provider returned 503 for /Users/kevin/cache")
    except RuntimeError as exc:
        detail = MagiEngineDriver._capture_error_detail(exc)
    assert detail["errorClass"] == "RuntimeError"
    assert "503" in detail["message"]
    # Path leak gone from the message too.
    assert "/Users/kevin" not in detail["message"]
    tb = detail["tracebackPreview"]
    assert isinstance(tb, str)
    assert "RuntimeError" in tb
    assert "/Users/kevin" not in tb


def test_capture_error_detail_handles_exception_without_message() -> None:
    try:
        raise StopAsyncIteration
    except StopAsyncIteration as exc:
        detail = MagiEngineDriver._capture_error_detail(exc)
    # Class name still surfaces even when str(exc) is empty.
    assert detail["errorClass"] == "StopAsyncIteration"
    # Message empty is fine; class is the authoritative signal.
    assert isinstance(detail["message"], str)
    assert isinstance(detail["tracebackPreview"], str)


def test_orphan_tool_events_carry_error_detail_when_supplied() -> None:
    detail = {
        "errorClass": "RuntimeError",
        "message": "provider returned 503",
        "tracebackPreview": "Traceback ... RuntimeError: 503",
    }
    [event] = MagiEngineDriver._synthesize_orphan_tool_results(
        {"tu-1": "Bash"},
        turn_id="turn-x",
        reason="engine_error",
        error_detail=detail,
    )
    assert event["reason"] == "engine_error"
    # The structured detail is attached so the dashboard can render per-tool
    # cause without re-parsing the preview string.
    assert event["errorDetail"] == detail
    # Preview string still matches the reason (back-compat for prose surfaces).
    assert "engine error" in str(event["output_preview"]).lower()


def test_orphan_tool_events_omit_error_detail_on_user_interrupt() -> None:
    # A real user cancel has no upstream exception to report; the orphan
    # event must not carry a fake errorDetail.
    [event] = MagiEngineDriver._synthesize_orphan_tool_results(
        {"tu-1": "Bash"},
        turn_id="turn-x",
        reason="user_interrupt",
    )
    assert "errorDetail" not in event, event
    assert event["reason"] == "user_interrupt"


def test_orphan_tool_events_back_compat_when_no_detail_passed() -> None:
    # engine_error without an error_detail (legacy call site) must still
    # produce a well-formed event — never crash on the absent kwarg.
    [event] = MagiEngineDriver._synthesize_orphan_tool_results(
        {"tu-1": "Bash"},
        turn_id="turn-x",
        reason="engine_error",
    )
    assert "errorDetail" not in event, event
    assert event["reason"] == "engine_error"
