from __future__ import annotations

from types import SimpleNamespace

from magi_agent.observability.transcript import set_active_transcript_sink
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundary,
    _shadow_session_id,
    _transcript_subagent_record,
    _transcript_tool_call_record,
    _transcript_tool_result_record,
)
from tests.support.gate5b4c3_factories import make_shadow_generation_request


# ---- pure record builders -------------------------------------------------

def test_tool_call_record_carries_full_args():
    fc = {"name": "Bash", "args": {"command": "ls -la"}, "id": "adk-1"}
    rec = _transcript_tool_call_record(fc, call_id="ev-1")
    assert rec == {
        "type": "tool_call",
        "tool_name": "Bash",
        "args": {"command": "ls -la"},
        "call_id": "ev-1",
    }


def test_tool_result_record_carries_full_output_and_status():
    resp = {"name": "Bash", "response": {"status": "error", "stderr": "boom"}, "id": "adk-1"}
    rec = _transcript_tool_result_record(resp, call_id="ev-1")
    assert rec["type"] == "tool_result"
    assert rec["call_id"] == "ev-1"
    assert rec["tool_name"] == "Bash"
    assert rec["status"] == "error"
    assert rec["output"] == {"status": "error", "stderr": "boom"}


def test_subagent_record_for_spawn_agent_flattens_prompt_and_keeps_args():
    fc = {"name": "SpawnAgent", "args": {"prompt": "Audit auth.py", "persona": "security"}, "id": "x"}
    rec = _transcript_subagent_record(fc)
    assert rec is not None
    assert rec["type"] == "subagent_spawn"
    assert rec["prompt"] == "Audit auth.py"
    assert rec["persona"] == "security"
    # non-lossy: full args retained even if flattening misses a key
    assert rec["args"] == {"prompt": "Audit auth.py", "persona": "security"}


def test_subagent_record_none_for_non_spawn_tool():
    assert _transcript_subagent_record({"name": "Bash", "args": {}}) is None


# ---- boundary emit methods ------------------------------------------------

def _capture_sink():
    captured: list[tuple] = []
    set_active_transcript_sink(lambda e, s, t: captured.append((e, s, t)))
    return captured


def test_emit_record_tags_session_and_turn_from_request():
    captured = _capture_sink()
    try:
        boundary = Gate5B4C3LiveRunnerBoundary()
        request = make_shadow_generation_request(sanitized_current_turn_text="hi")
        boundary._emit_record({"type": "tool_call"}, request=request)
        assert len(captured) == 1
        event, session_id, turn_id = captured[0]
        assert event == {"type": "tool_call"}
        assert session_id == _shadow_session_id(request)
        assert turn_id == request.turn.turn_id
    finally:
        set_active_transcript_sink(None)


def test_emit_record_is_noop_without_sink():
    set_active_transcript_sink(None)
    boundary = Gate5B4C3LiveRunnerBoundary()
    request = make_shadow_generation_request(sanitized_current_turn_text="hi")
    # Must not raise when no sink is registered.
    boundary._emit_record({"type": "tool_call"}, request=request)


def test_emit_turn_completion_writes_message_then_turn_end():
    captured = _capture_sink()
    try:
        boundary = Gate5B4C3LiveRunnerBoundary()
        request = make_shadow_generation_request(sanitized_current_turn_text="hi")
        result = SimpleNamespace(
            output_text_internal="the final answer",
            status="completed",
            reason="runner_completed",
            usage_internal={"input": 10, "output": 5},
            selected_provider="anthropic",
            selected_model="claude-sonnet-4-6",
            event_count=7,
            latency_ms=1234,
        )
        boundary._emit_turn_completion(request, result)

        types = [e["type"] for (e, _s, _t) in captured]
        assert types == ["message", "turn_end"]
        message = captured[0][0]
        assert message["role"] == "assistant"
        assert message["content"] == "the final answer"
        turn_end = captured[1][0]
        assert turn_end["terminal"] == "completed"
        assert turn_end["usage"] == {"input": 10, "output": 5}
    finally:
        set_active_transcript_sink(None)


def test_emit_turn_completion_skips_message_when_no_output():
    captured = _capture_sink()
    try:
        boundary = Gate5B4C3LiveRunnerBoundary()
        request = make_shadow_generation_request(sanitized_current_turn_text="hi")
        result = SimpleNamespace(
            output_text_internal=None,
            status="error",
            reason="runner_error",
            usage_internal=None,
            selected_provider="",
            selected_model="",
            event_count=0,
            latency_ms=3,
        )
        boundary._emit_turn_completion(request, result)
        types = [e["type"] for (e, _s, _t) in captured]
        assert types == ["turn_end"]
        assert captured[0][0]["terminal"] == "error"
    finally:
        set_active_transcript_sink(None)
