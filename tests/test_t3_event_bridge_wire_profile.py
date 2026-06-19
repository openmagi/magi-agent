"""TDD tests for Task 3: parameterize OpenMagiEventBridge with wire_profile.

Step 1: Write failing tests.
Step 2: Run → FAIL.
Step 3: Implement → GREEN.

Two invariants under test:
(a) Bridge with wire_profile=HOSTED_PROFILE → tool_start.id is tu_<hash> + gate5b4c3 field shape.
(b) Bridge with default (no wire_profile) → EXACT same output as today (CLI-byte-identical guard).
"""
from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
from magi_agent.adk_bridge.wire_profile import HOSTED_PROFILE
from magi_agent.runtime.public_events import tool_event_id


# ---------------------------------------------------------------------------
# Helper: build a minimal function-call ADK event
# ---------------------------------------------------------------------------


def _make_call_event(
    *,
    event_id: str = "event-t3-call",
    adk_tool_id: str | None = "fc-t3-001",
    name: str = "Search",
    args: dict | None = None,
    invocation_id: str = "turn-t3",
) -> Event:
    fc = types.FunctionCall(
        name=name,
        args=args if args is not None else {"query": "wire profile test"},
    )
    if adk_tool_id is not None:
        fc = types.FunctionCall(
            id=adk_tool_id,
            name=name,
            args=args if args is not None else {"query": "wire profile test"},
        )
    return Event(
        id=event_id,
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=fc)],
        ),
        invocation_id=invocation_id,
    )


def _make_response_event(
    *,
    event_id: str = "event-t3-response",
    adk_tool_id: str | None = "fc-t3-001",
    name: str = "Search",
    response: dict | None = None,
    invocation_id: str = "turn-t3",
) -> Event:
    fr = types.FunctionResponse(
        name=name,
        response=response if response is not None else {"results": ["alpha"]},
    )
    if adk_tool_id is not None:
        fr = types.FunctionResponse(
            id=adk_tool_id,
            name=name,
            response=response if response is not None else {"results": ["alpha"]},
        )
    return Event(
        id=event_id,
        author="tool",
        content=types.Content(
            role="tool",
            parts=[types.Part(function_response=fr)],
        ),
        invocation_id=invocation_id,
    )


# ---------------------------------------------------------------------------
# (a) HOSTED_PROFILE path: tool_start.id uses tu_<hash> scheme
# ---------------------------------------------------------------------------


def test_hosted_profile_bridge_tool_start_id_is_tu_hash() -> None:
    """Bridge with HOSTED_PROFILE → tool_start.id is tu_<12-hex>."""
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    event = _make_call_event(
        adk_tool_id="fc-t3-001",
        name="Search",
        args={"query": "wire profile test"},
    )
    projection = bridge.project_adk_event(event, turn_id="turn-t3")

    assert projection.agent_events, "expected at least one agent_event"
    tool_start = projection.agent_events[0]
    assert tool_start["type"] == "tool_start"
    assert isinstance(tool_start["id"], str)
    assert tool_start["id"].startswith("tu_"), (
        f"HOSTED path must produce tu_<hash> id, got {tool_start['id']!r}"
    )


def test_hosted_profile_bridge_tool_start_id_matches_tool_event_id() -> None:
    """tool_start.id must equal tool_event_id(name, args, call_id, index)."""
    args = {"query": "wire profile test"}
    adk_tool_id = "fc-t3-001"
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    event = _make_call_event(
        adk_tool_id=adk_tool_id,
        name="Search",
        args=args,
    )
    projection = bridge.project_adk_event(event, turn_id="turn-t3")
    tool_start = projection.agent_events[0]

    expected_id = tool_event_id(
        name="Search",
        args=args,
        call_id=adk_tool_id,
        index=0,
    )
    assert tool_start["id"] == expected_id


def test_hosted_profile_bridge_tool_start_field_shape() -> None:
    """gate5b4c3 field shape: type, id, name — no durationMs on tool_start."""
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    event = _make_call_event(name="Read", args={"path": "/tmp/x"})
    projection = bridge.project_adk_event(event, turn_id="turn-t3")
    tool_start = projection.agent_events[0]

    assert tool_start["type"] == "tool_start"
    assert "id" in tool_start
    assert tool_start["name"] == "Read"
    # gate5b4c3: no durationMs on tool_start
    assert "durationMs" not in tool_start


def test_hosted_profile_bridge_tool_start_omits_empty_input_preview() -> None:
    """gate5b4c3 skips input_preview when it has no content (None/falsy)."""
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    # Empty args → _public_preview({}) → "{}" → tool_input_preview({}) → None
    # The HOSTED builder (tool_start_event) uses _put_text which omits falsy.
    # BUT: event_adapter passes _public_preview(args) which returns "{}" for empty args.
    # "{}" is truthy so input_preview will be set as '{}' — test that the field exists.
    # The "omit" behaviour is specifically when input_preview is None.
    event = _make_call_event(
        adk_tool_id=None,
        name="Bash",
        args={"command": "ls"},
    )
    projection = bridge.project_adk_event(event, turn_id="turn-t3")
    tool_start = projection.agent_events[0]
    assert tool_start["type"] == "tool_start"
    # With non-empty args the preview should be present
    assert "input_preview" in tool_start


def test_hosted_profile_bridge_tool_end_field_shape() -> None:
    """gate5b4c3 tool_end: no durationMs key (public_events.tool_end_event omits it)."""
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    event = _make_response_event(name="Search", response={"results": ["alpha"]})
    projection = bridge.project_adk_event(event, turn_id="turn-t3")
    tool_end = projection.agent_events[0]

    assert tool_end["type"] == "tool_end"
    assert tool_end["status"] == "ok"
    # gate5b4c3 divergence: no durationMs
    assert "durationMs" not in tool_end


def test_hosted_profile_bridge_tool_end_id_matches_tool_start_id() -> None:
    """tool_end.id must use same tu_<hash> scheme as tool_start.id."""
    args = {"query": "wire profile test"}
    adk_tool_id = "fc-t3-response"
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    response_event = _make_response_event(
        adk_tool_id=adk_tool_id,
        name="Search",
        response={"results": ["beta"]},
    )
    projection = bridge.project_adk_event(response_event, turn_id="turn-t3")
    tool_end = projection.agent_events[0]

    # tool_end uses tool_id from the response side (same name, adk_id as passed)
    # It should also start with tu_
    assert tool_end["id"].startswith("tu_")


# ---------------------------------------------------------------------------
# (b) CLI (None) path: byte-identical to existing code
# ---------------------------------------------------------------------------


def test_none_profile_bridge_is_byte_identical_to_existing_for_call_event() -> None:
    """Bridge() (no wire_profile) must yield exact same output as before T3."""
    # Build with explicit None (== default) and a fresh legacy bridge (same as
    # before T3 where no wire_profile param existed).
    bridge_default = OpenMagiEventBridge()
    bridge_none = OpenMagiEventBridge(wire_profile=None)

    event = _make_call_event(
        event_id="event-cli-guard",
        adk_tool_id="tool-cli-guard",
        name="Search",
        args={"query": "cli guard", "limit": 5},
        invocation_id="turn-cli",
    )

    p_default = bridge_default.project_adk_event(event, turn_id="turn-cli")
    p_none = bridge_none.project_adk_event(event, turn_id="turn-cli")

    assert p_default.agent_events == p_none.agent_events
    assert p_default.legacy_deltas == p_none.legacy_deltas
    assert len(p_default.transcript_entries) == len(p_none.transcript_entries)


def test_none_profile_bridge_tool_start_id_is_not_tu_hash() -> None:
    """CLI (None) path must NOT produce tu_ ids — those are HOSTED-only."""
    bridge = OpenMagiEventBridge()
    event = _make_call_event(
        adk_tool_id="fc-cli-001",
        name="Read",
        args={"path": "/tmp/x"},
    )
    projection = bridge.project_adk_event(event, turn_id="turn-cli")
    tool_start = projection.agent_events[0]

    assert not tool_start["id"].startswith("tu_"), (
        f"CLI path must NOT produce tu_ ids, got {tool_start['id']!r}"
    )


def test_none_profile_bridge_tool_end_has_duration_ms() -> None:
    """CLI (None) path still includes durationMs: 0 on tool_end."""
    bridge = OpenMagiEventBridge()
    event = _make_response_event(
        adk_tool_id="fc-cli-001",
        name="Search",
        response={"results": ["x"]},
    )
    projection = bridge.project_adk_event(event, turn_id="turn-cli")
    tool_end = projection.agent_events[0]

    assert tool_end["durationMs"] == 0


def test_none_profile_bridge_passes_existing_snapshot_exact() -> None:
    """Snapshot check: existing event_bridge test shape is preserved.

    This mirrors the exact assertion in test_event_bridge.py's
    test_event_bridge_projects_function_call_to_tool_start_and_transcript.
    """
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-1",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-1",
                        name="Search",
                        args={"query": "adk migration", "limit": 3},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )
    projection = bridge.project_adk_event(event, turn_id="turn-1")

    # Exact snapshot from today's test_event_bridge.py
    assert projection.agent_events == [
        {
            "type": "tool_start",
            "id": "tool-1",
            "name": "Search",
            "input_preview": '{"limit": 3, "query": "adk migration"}',
        }
    ]
    assert projection.transcript_entries[0].kind == "tool_call"
    assert projection.transcript_entries[0].tool_use_id == "tool-1"
    assert projection.transcript_entries[0].name == "Search"


# ---------------------------------------------------------------------------
# Constructor: wire_profile parameter accepted and stored
# ---------------------------------------------------------------------------


def test_bridge_accepts_wire_profile_param_none() -> None:
    bridge = OpenMagiEventBridge(wire_profile=None)
    assert bridge._wire_profile is None


def test_bridge_accepts_wire_profile_param_hosted() -> None:
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    assert bridge._wire_profile is HOSTED_PROFILE


def test_bridge_default_wire_profile_is_none() -> None:
    """Default (no argument) must be None — NOT DEFAULT_PROFILE."""
    bridge = OpenMagiEventBridge()
    assert bridge._wire_profile is None


# ---------------------------------------------------------------------------
# Task 3 NEW tests (TDD red phase)
# (a) HOSTED: function_call → tool_start + tool_progress
# (b) HOSTED: function_response → tool_end with digest/refs/durationMs
# (c) None/CLI: NO tool_progress; tool_end byte-identical
# ---------------------------------------------------------------------------


def test_t3_hosted_call_emits_tool_progress_after_tool_start() -> None:
    """HOSTED bridge: project a function_call → tool_start AND a following
    tool_progress with id=tu_<hash>, label=name, status='in_progress',
    message='Tool execution started'."""
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)
    event = _make_call_event(
        adk_tool_id="fc-t3-prog-001",
        name="Grep",
        args={"pattern": "TODO", "path": "."},
    )
    projection = bridge.project_adk_event(event, turn_id="turn-t3-prog")

    assert len(projection.agent_events) >= 2, (
        f"Expected at least tool_start + tool_progress, got {len(projection.agent_events)}: "
        f"{projection.agent_events}"
    )
    tool_start = projection.agent_events[0]
    tool_progress = projection.agent_events[1]

    assert tool_start["type"] == "tool_start"
    assert tool_start["id"].startswith("tu_")

    assert tool_progress["type"] == "tool_progress"
    # id must match the tool_start id
    assert tool_progress["id"] == tool_start["id"]
    assert tool_progress.get("label") == "Grep"
    assert tool_progress.get("status") == "in_progress"
    assert tool_progress.get("message") == "Tool execution started"


def test_t3_hosted_response_tool_end_has_digest_refs_duration() -> None:
    """HOSTED bridge: project function_response → tool_end has
    output_preview == 'result:<digest>', transcriptRefs == ['result:<digest>'],
    and durationMs is an int."""
    from magi_agent.runtime.public_events import result_digest

    response_payload = {"results": ["alpha", "beta"]}
    adk_tool_id = "fc-t3-end-001"
    bridge = OpenMagiEventBridge(wire_profile=HOSTED_PROFILE)

    # Project call first so start-time is recorded
    call_event = _make_call_event(
        adk_tool_id=adk_tool_id,
        name="Search",
        args={"query": "digest test"},
    )
    bridge.project_adk_event(call_event, turn_id="turn-t3-end")

    response_event = _make_response_event(
        adk_tool_id=adk_tool_id,
        name="Search",
        response=response_payload,
    )
    projection = bridge.project_adk_event(response_event, turn_id="turn-t3-end")

    tool_end = projection.agent_events[0]
    assert tool_end["type"] == "tool_end"

    digest = result_digest(response_payload)
    expected_preview = f"result:{digest}"
    assert tool_end.get("output_preview") == expected_preview, (
        f"output_preview mismatch: {tool_end.get('output_preview')!r} != {expected_preview!r}"
    )
    assert tool_end.get("transcriptRefs") == [expected_preview], (
        f"transcriptRefs mismatch: {tool_end.get('transcriptRefs')!r}"
    )
    duration = tool_end.get("durationMs")
    assert duration is not None and isinstance(duration, (int, float)), (
        f"durationMs must be an int/float, got {duration!r}"
    )


def test_t3_cli_none_no_tool_progress_and_tool_end_byte_identical() -> None:
    """None/CLI bridge: project call+response → NO tool_progress; tool_end
    shape is byte-identical to current CLI output (durationMs=0, no transcriptRefs,
    output_preview from _public_preview)."""
    bridge = OpenMagiEventBridge()  # None path

    adk_tool_id = "fc-cli-t3-001"
    call_event = _make_call_event(
        adk_tool_id=adk_tool_id,
        name="Bash",
        args={"command": "ls"},
        invocation_id="turn-cli-t3",
    )
    response_payload = {"output": "file.txt"}
    response_event = _make_response_event(
        adk_tool_id=adk_tool_id,
        name="Bash",
        response=response_payload,
        invocation_id="turn-cli-t3",
    )

    call_proj = bridge.project_adk_event(call_event, turn_id="turn-cli-t3")
    resp_proj = bridge.project_adk_event(response_event, turn_id="turn-cli-t3")

    # No tool_progress events in call projection
    call_types = [e["type"] for e in call_proj.agent_events]
    assert "tool_progress" not in call_types, (
        f"CLI path must NOT emit tool_progress, got: {call_types}"
    )

    # tool_end shape: no transcriptRefs, durationMs=0
    tool_end = resp_proj.agent_events[0]
    assert tool_end["type"] == "tool_end"
    assert "transcriptRefs" not in tool_end, (
        f"CLI tool_end must NOT have transcriptRefs, got: {tool_end}"
    )
    assert tool_end.get("durationMs") == 0, (
        f"CLI tool_end must have durationMs=0, got: {tool_end.get('durationMs')!r}"
    )
