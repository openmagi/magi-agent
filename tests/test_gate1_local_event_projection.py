from __future__ import annotations

import re
from pathlib import Path

from google.adk.events import Event
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge
from magi_agent.runtime.control import (
    ControlRequestCreatedEvent,
    make_transcript_reference,
)
from magi_agent.runtime.transcript import TranscriptStore
from magi_agent.transport.sse import InMemorySseWriter


FIXTURES = Path(__file__).parent / "fixtures" / "gate1"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ``durationMs`` on the local ``tool_end`` projection is a real ``time.monotonic``
# wall-clock measurement between the projected tool_start and tool_end. In this
# synthetic golden the two events are projected back-to-back, so the value only
# reflects projection-layer CPU time and is nondeterministic (0..~30ms across
# runs). Normalize it to a fixed sentinel so the redaction golden stays
# byte-stable while still asserting the field is present and integer-typed.
_DURATION_MS_RE = re.compile(r'"durationMs":\d+')


def _normalize_duration_ms(body: str) -> str:
    return _DURATION_MS_RE.sub('"durationMs":0', body)


def _append_projection_transcript(
    store: TranscriptStore,
    bridge: OpenMagiEventBridge,
    event: Event,
    *,
    turn_id: str,
) -> None:
    projection = bridge.project_adk_event(event, turn_id=turn_id)
    for entry in projection.transcript_entries:
        store.append(entry)


def _write_projection_sse(
    writer: InMemorySseWriter,
    bridge: OpenMagiEventBridge,
    event: Event,
    *,
    turn_id: str,
) -> None:
    projection = bridge.project_adk_event(event, turn_id=turn_id)
    for agent_event in projection.agent_events:
        writer.agent(agent_event)
    for delta in projection.legacy_deltas:
        writer.legacy_delta(delta)


def test_simple_assistant_text_matches_gate1_jsonl_and_sse_golden(tmp_path: Path) -> None:
    bridge = OpenMagiEventBridge()
    turn_id = "gate1-turn-text"
    partial = Event(
        id="evt-text-partial",
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(text="Hello from Gate 1.")],
        ),
        partial=True,
        invocation_id=turn_id,
    )
    final = Event(
        id="evt-text-final",
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(text="Hello from Gate 1.")],
        ),
        partial=False,
        turn_complete=True,
        invocation_id=turn_id,
        timestamp=1_779_000_001,
    )
    transcript = TranscriptStore(file_path=tmp_path / "simple_assistant_text.jsonl")
    writer = InMemorySseWriter()

    writer.start()
    _write_projection_sse(writer, bridge, partial, turn_id=turn_id)
    _append_projection_transcript(transcript, bridge, final, turn_id=turn_id)
    writer.legacy_finish()

    assert transcript.file_path.read_text(encoding="utf-8") == _fixture(
        "simple_assistant_text.jsonl"
    )
    assert writer.body == _fixture("simple_assistant_text.sse")


def test_tool_call_result_matches_gate1_redacted_jsonl_and_sse_golden(
    tmp_path: Path,
) -> None:
    bridge = OpenMagiEventBridge()
    turn_id = "gate1-turn-tool"
    call = Event(
        id="evt-tool-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-call-synthetic-1",
                        name="SyntheticFetch",
                        args={
                            "query": "gate 1 fixture",
                            "authorization": "Bearer synthetic-call-token",
                            "api_key": "synthetic-call-api-key",
                            "payload": "c" * 450,
                        },
                    )
                )
            ],
        ),
        invocation_id=turn_id,
        timestamp=1_779_000_010,
    )
    result = Event(
        id="evt-tool-result",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-call-synthetic-1",
                        name="SyntheticFetch",
                        response={
                            "summary": "synthetic lookup complete",
                            "token": "synthetic-result-token",
                            "password": "synthetic result password",
                            "payload": "r" * 450,
                        },
                    )
                )
            ],
        ),
        invocation_id=turn_id,
        timestamp=1_779_000_011,
    )
    transcript = TranscriptStore(file_path=tmp_path / "tool_redacted.jsonl")
    writer = InMemorySseWriter()

    writer.start()
    for event in (call, result):
        _write_projection_sse(writer, bridge, event, turn_id=turn_id)
        _append_projection_transcript(transcript, bridge, event, turn_id=turn_id)
    writer.legacy_finish()

    body = writer.body
    assert "synthetic-call-token" not in body
    assert "synthetic-call-api-key" not in body
    assert "synthetic-result-token" not in body
    assert "synthetic result password" not in body
    assert "..." in body
    # The local tool_end carries a real (non-hardcoded) wall-clock duration; the
    # exact value is nondeterministic here, so assert the key is present and then
    # normalize it before the byte-exact golden comparison.
    assert '"durationMs":' in body
    transcript_body = transcript.file_path.read_text(encoding="utf-8")
    assert "synthetic-call-token" in transcript_body
    assert "synthetic-result-token" in transcript_body
    assert _normalize_duration_ms(transcript_body) == _fixture("tool_redacted.jsonl")
    assert _normalize_duration_ms(body) == _fixture("tool_redacted.sse")


def test_adk_error_matches_gate1_redacted_sse_and_private_reason_jsonl_golden(
    tmp_path: Path,
) -> None:
    bridge = OpenMagiEventBridge()
    turn_id = "gate1-turn-error"
    private_reason = (
        "ADK synthetic failure: "
        + ("x" * 360)
        + " api_key=synthetic-private-key refresh_token=synthetic-refresh-token, "
        + ("y" * 420)
    )
    event = Event(
        id="evt-error",
        author="model",
        invocation_id=turn_id,
        error_code="SYNTHETIC_ADK_ERROR",
        error_message=private_reason,
        timestamp=1_779_000_020,
    )
    transcript = TranscriptStore(file_path=tmp_path / "adk_error.jsonl")
    writer = InMemorySseWriter()

    writer.start()
    _write_projection_sse(writer, bridge, event, turn_id=turn_id)
    _append_projection_transcript(transcript, bridge, event, turn_id=turn_id)
    writer.legacy_finish()

    body = writer.body
    assert "synthetic-private-key" not in body
    assert "synthetic-refresh-token" not in body
    # C-11 (#798): kernel `[redacted]` substitution replaces legacy ellipsis truncation; the `not in body` assertions above already guarantee the security invariant.
    transcript_body = transcript.file_path.read_text(encoding="utf-8")
    assert "synthetic-private-key" in transcript_body
    assert transcript_body == _fixture("adk_error.jsonl")
    assert body == _fixture("adk_error.sse")


def test_control_request_pending_matches_gate1_transcript_jsonl_golden(
    tmp_path: Path,
) -> None:
    event = ControlRequestCreatedEvent(
        eventId="evt-control-created",
        seq=1,
        ts=1_779_000_030,
        sessionKey="agent:main:local:gate1",
        turnId="gate1-turn-control",
        idempotencyKey="idem-control-created",
        request={
            "requestId": "ctrl-req-pending",
            "kind": "tool_permission",
            "state": "pending",
            "sessionKey": "agent:main:local:gate1",
            "turnId": "gate1-turn-control",
            "channelName": "local",
            "source": "turn",
            "prompt": "Allow synthetic read-only tool?",
            "proposedInput": {"path": "synthetic/local/file.txt"},
            "createdAt": 1_779_000_030,
            "expiresAt": 1_779_000_090,
        },
    )
    transcript = TranscriptStore(file_path=tmp_path / "control_request_pending.jsonl")

    transcript.append(make_transcript_reference(event))

    assert event.request.state == "pending"
    assert transcript.file_path.read_text(encoding="utf-8") == _fixture(
        "control_request_pending.jsonl"
    )
