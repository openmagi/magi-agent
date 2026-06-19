"""TDD tests for Task 4A: call/response correlation fix in OpenMagiEventBridge.

Bug: _project_function_response_part computed tool_id with args={} (no args on
a FunctionResponse), so the HOSTED tool_end id differed from the tool_start id.
Fix: per-instance correlation store mirrors gate5b4c3's pattern.

Test: feed a call event then the matching response event through ONE
HOSTED_PROFILE bridge; assert tool_start["id"] == tool_end["id"].
"""
from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
from magi_agent.adk_bridge.wire_profile import HOSTED_PROFILE
from magi_agent.runtime.public_events import tool_event_id


# ---------------------------------------------------------------------------
# Helpers — build call and response ADK events
# ---------------------------------------------------------------------------


def _make_call_event(
    *,
    name: str = "Calculation",
    args: dict | None = None,
    adk_id: str | None = "calculation-call-001",
    invocation_id: str = "turn-corr-001",
    event_id: str = "evt-call",
) -> Event:
    real_args = args if args is not None else {"expression": "1 + 1"}
    fc_kwargs: dict = {"name": name, "args": real_args}
    if adk_id is not None:
        fc_kwargs["id"] = adk_id
    return Event(
        id=event_id,
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(**fc_kwargs))],
        ),
        invocation_id=invocation_id,
    )


def _make_response_event(
    *,
    name: str = "Calculation",
    response: dict | None = None,
    adk_id: str | None = "calculation-call-001",
    invocation_id: str = "turn-corr-001",
    event_id: str = "evt-response",
) -> Event:
    real_response = response if response is not None else {"status": "ok", "result": 2}
    fr_kwargs: dict = {"name": name, "response": real_response}
    if adk_id is not None:
        fr_kwargs["id"] = adk_id
    return Event(
        id=event_id,
        author="tool",
        content=types.Content(
            role="tool",
            parts=[types.Part(function_response=types.FunctionResponse(**fr_kwargs))],
        ),
        invocation_id=invocation_id,
    )


# ---------------------------------------------------------------------------
# Part A — correlation: tool_start.id == tool_end.id (same bridge instance)
# ---------------------------------------------------------------------------


def test_hosted_profile_call_and_response_ids_are_equal() -> None:
    """Core correlation test: tool_start and tool_end share the same tu_<hash> id.

    Before the fix: response side computed id with args={}, producing a DIFFERENT
    tu_<hash> than the call side (which used the real args).
    After the fix: response side looks up the recorded call-side id.
    """
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    turn_id = "turn-corr-001"

    call_event = _make_call_event(
        name="Calculation",
        args={"expression": "1 + 1"},
        adk_id="calculation-call-001",
    )
    response_event = _make_response_event(
        name="Calculation",
        response={"status": "ok", "result": 2},
        adk_id="calculation-call-001",
    )

    call_projection = bridge.project_adk_event(call_event, turn_id=turn_id)
    response_projection = bridge.project_adk_event(response_event, turn_id=turn_id)

    assert call_projection.agent_events, "expected tool_start"
    assert response_projection.agent_events, "expected tool_end"

    tool_start = call_projection.agent_events[0]
    tool_end = response_projection.agent_events[0]

    assert tool_start["type"] == "tool_start"
    assert tool_end["type"] == "tool_end"
    assert tool_start["id"] == tool_end["id"], (
        f"HOSTED call/response ids must match.\n"
        f"  tool_start id: {tool_start['id']!r}\n"
        f"  tool_end id:   {tool_end['id']!r}\n"
        "Fix: correlation store must record call-side id and reuse it on response."
    )
    assert tool_start["id"].startswith("tu_"), (
        f"Expected tu_<hash> id, got {tool_start['id']!r}"
    )


def test_hosted_profile_call_id_matches_tool_event_id() -> None:
    """The correlated id equals tool_event_id(name, real_args, call_id, index)."""
    args = {"expression": "1 + 1"}
    adk_id = "calculation-call-001"
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    turn_id = "turn-corr-002"

    call_event = _make_call_event(name="Calculation", args=args, adk_id=adk_id)
    projection = bridge.project_adk_event(call_event, turn_id=turn_id)
    tool_start = projection.agent_events[0]

    expected = tool_event_id(name="Calculation", args=args, call_id=adk_id, index=0)
    assert tool_start["id"] == expected, (
        f"tool_start id should equal tool_event_id(name, real_args, call_id, index).\n"
        f"  expected: {expected!r}\n"
        f"  got:      {tool_start['id']!r}"
    )


def test_hosted_profile_response_id_equals_call_id_without_adk_id() -> None:
    """Correlation via pending-by-name fallback when adk_id is absent on both sides."""
    args = {"expression": "2 * 3"}
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    turn_id = "turn-corr-003"

    call_event = _make_call_event(
        name="Calculation", args=args, adk_id=None
    )
    response_event = _make_response_event(
        name="Calculation", response={"status": "ok", "result": 6}, adk_id=None
    )

    call_projection = bridge.project_adk_event(call_event, turn_id=turn_id)
    response_projection = bridge.project_adk_event(response_event, turn_id=turn_id)

    tool_start_id = call_projection.agent_events[0]["id"]
    tool_end_id = response_projection.agent_events[0]["id"]

    assert tool_start_id == tool_end_id, (
        f"Correlation via name fallback failed: {tool_start_id!r} != {tool_end_id!r}"
    )


def test_cli_path_unaffected_by_correlation_store() -> None:
    """CLI (None wire_profile) path is byte-for-byte unchanged after the fix."""
    bridge = OpenMagiEventBridge(wire_profile=None)
    turn_id = "turn-cli-guard"

    call_event = _make_call_event(
        name="Search", args={"query": "test"}, adk_id="search-001"
    )
    response_event = _make_response_event(
        name="Search", response={"results": ["x"]}, adk_id="search-001"
    )

    call_projection = bridge.project_adk_event(call_event, turn_id=turn_id)
    response_projection = bridge.project_adk_event(response_event, turn_id=turn_id)

    tool_start = call_projection.agent_events[0]
    tool_end = response_projection.agent_events[0]

    # CLI path should still produce "adk-tool-call:search-001" / "adk-tool-response:search-001"
    # (different kinds, so ids differ — that is the existing behaviour we must not break).
    assert not tool_start["id"].startswith("tu_"), "CLI must NOT produce tu_ ids"
    # durationMs: 0 is still present on CLI path
    assert tool_end.get("durationMs") == 0, "CLI tool_end must still have durationMs: 0"


def test_multiple_tool_calls_correlate_independently() -> None:
    """Two different tool calls within the same bridge instance correlate separately."""
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    turn_id = "turn-multi-001"

    call_a = _make_call_event(
        name="Calculation", args={"expression": "1 + 1"}, adk_id="call-a",
        event_id="evt-a-call"
    )
    call_b = _make_call_event(
        name="Search", args={"query": "hello"}, adk_id="call-b",
        event_id="evt-b-call"
    )
    response_a = _make_response_event(
        name="Calculation", response={"status": "ok", "result": 2}, adk_id="call-a",
        event_id="evt-a-resp"
    )
    response_b = _make_response_event(
        name="Search", response={"status": "ok", "results": []}, adk_id="call-b",
        event_id="evt-b-resp"
    )

    proj_a_call = bridge.project_adk_event(call_a, turn_id=turn_id)
    proj_b_call = bridge.project_adk_event(call_b, turn_id=turn_id)
    proj_a_resp = bridge.project_adk_event(response_a, turn_id=turn_id)
    proj_b_resp = bridge.project_adk_event(response_b, turn_id=turn_id)

    id_a_start = proj_a_call.agent_events[0]["id"]
    id_b_start = proj_b_call.agent_events[0]["id"]
    id_a_end = proj_a_resp.agent_events[0]["id"]
    id_b_end = proj_b_resp.agent_events[0]["id"]

    assert id_a_start == id_a_end, f"tool-A call/response mismatch: {id_a_start!r} != {id_a_end!r}"
    assert id_b_start == id_b_end, f"tool-B call/response mismatch: {id_b_start!r} != {id_b_end!r}"
    assert id_a_start != id_b_start, "different tools must have different ids"
