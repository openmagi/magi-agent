import json

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
from magi_agent.transport.sse import InMemorySseWriter


def test_event_bridge_live_compatible_tool_events_include_event_ids_and_digest_refs() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    call_event = Event(
        id="event-live-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-live-1",
                        name="Search",
                        args={"query": "adk migration"},
                    )
                )
            ],
        ),
        invocation_id="turn-live",
    )
    response_event = Event(
        id="event-live-response",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-live-1",
                        name="Search",
                        response={
                            "results": ["alpha"],
                            "resultRef": "receipt:sha256:" + ("a" * 64),
                            "sourceRefs": ["source:web:source-live-1"],
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-live",
    )

    call_projection = bridge.project_adk_event(call_event, turn_id="turn-live")
    response_projection = bridge.project_adk_event(response_event, turn_id="turn-live")

    call_public = call_projection.agent_events[0]
    response_public = response_projection.agent_events[0]

    assert call_public["eventId"] == "event-live-call:tool-start-0"
    assert call_public["inputDigest"].startswith("sha256:")
    assert response_public["eventId"] == "event-live-response:tool-end-0"
    assert response_public["outputDigest"].startswith("sha256:")
    assert response_public["transcriptRefs"] == [
        "receipt:sha256:" + ("a" * 64),
        "source:web:source-live-1",
    ]


def test_event_bridge_live_compatible_drops_malformed_tool_transcript_refs() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(
        id="event-live-response-bad-refs",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-live-bad-refs",
                        name="Search",
                        response={
                            "results": ["alpha"],
                            "resultRef": "not-a-receipt",
                            "sourceRefs": ["not-a-source-ref"],
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-live",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-live")
    public_event = projection.agent_events[0]

    assert public_event["type"] == "tool_end"
    assert public_event["outputDigest"].startswith("sha256:")
    assert "transcriptRefs" not in public_event


def test_event_bridge_live_compatible_tool_event_id_hashes_unsafe_adk_event_id() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(
        id="/Users/kevin/workspace/internal-event",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-live-unsafe-event-id",
                        name="Search",
                        args={"query": "public"},
                    )
                )
            ],
        ),
        invocation_id="turn-live",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-live")
    writer = InMemorySseWriter()
    writer.agent(projection.agent_events[0])
    payload = json.loads(
        next(
            line.removeprefix("data: ")
            for line in writer.body.splitlines()
            if line.startswith("data: ")
        )
    )

    assert payload["eventId"].startswith(("adk-event-", "event:"))
    assert "/Users/kevin" not in writer.body
    assert "workspace" not in payload["eventId"]


def test_event_bridge_treats_benign_finish_in_error_field_as_non_failure() -> None:
    # Regression: Gemini/ADK can surface a benign finish status ("completed")
    # in the event error field. That must NOT project a failure (terminal_abort
    # trace / error event / aborted turn_end), which would render a spurious
    # "응답 생성이 중단되었습니다: completed" interruption downstream.
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(
        id="event-benign-finish",
        author="model",
        error_message="completed",
        invocation_id="turn-live",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-live")

    # The error branch must be bypassed: no error event, no terminal_abort
    # trace, no turn.failed, and no aborted turn_end echoing the benign reason.
    # (Downstream receipt/content logic still governs the real turn_end.)
    assert "error" not in {ev.get("type") for ev in projection.agent_events}
    assert not any(
        ev.get("type") == "runtime_trace" and ev.get("phase") == "terminal_abort"
        for ev in projection.agent_events
    )
    assert not any(
        ev.get("type") == "turn_end"
        and ev.get("status") == "aborted"
        and ev.get("reason") == "completed"
        for ev in projection.agent_events
    )
    assert not any(
        ne.type == "turn.failed" for ne in projection.normalized_events
    )


def test_event_bridge_still_reports_a_real_error_field() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(
        id="event-real-error",
        author="model",
        error_code="SAFETY",
        error_message="blocked by safety filter",
        invocation_id="turn-live",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-live")

    event_types = {ev.get("type") for ev in projection.agent_events}
    assert "error" in event_types
    assert any(
        ev.get("type") == "turn_end" and ev.get("status") == "aborted"
        for ev in projection.agent_events
    )
