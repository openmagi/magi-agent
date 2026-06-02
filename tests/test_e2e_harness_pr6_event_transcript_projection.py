from __future__ import annotations

import json
import subprocess
import sys

from google.adk.events import Event
from google.genai import types

from openmagi_core_agent.adk_bridge.event_adapter import OpenMagiEventBridge
from openmagi_core_agent.memory.projection import project_turn_summary_for_memory
from openmagi_core_agent.runtime.control import (
    ControlRequestCreatedEvent,
    ControlRequestResolvedEvent,
)
from openmagi_core_agent.runtime.events import (
    NormalizedEvent,
    NormalizedProjectionContract,
    normalized_events_to_transcript,
    transcript_entries_to_agent_events,
)
from openmagi_core_agent.runtime.transcript import (
    ControlEventTranscriptEntry,
    ToolCallEntry,
    ToolResultEntry,
)


def _text_event(
    text: str,
    *,
    turn_id: str,
    partial: bool,
    turn_complete: bool = False,
    timestamp: int = 1_779_100_000,
) -> Event:
    return Event(
        id=f"evt-{turn_id}-{timestamp}",
        author="model",
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        partial=partial,
        turn_complete=turn_complete,
        invocation_id=turn_id,
        timestamp=timestamp,
    )


def test_pr6_model_only_turn_projects_normalized_transcript_and_sse_order() -> None:
    bridge = OpenMagiEventBridge()
    turn_id = "pr6-turn-model"

    partial = bridge.project_adk_event(
        _text_event("Hello ", turn_id=turn_id, partial=True, timestamp=1),
        turn_id=turn_id,
    )
    final = bridge.project_adk_event(
        _text_event(
            "Hello world.",
            turn_id=turn_id,
            partial=False,
            turn_complete=True,
            timestamp=2,
        ),
        turn_id=turn_id,
    )

    assert [event.type for event in partial.normalized_events] == ["model.message.delta"]
    assert partial.normalized_events[0].source == "adk"
    assert partial.normalized_events[0].turn_id == turn_id
    assert partial.normalized_events[0].event_id.startswith("evt-")
    assert partial.agent_events == [{"type": "text_delta", "delta": "Hello "}]
    assert partial.legacy_deltas == ["Hello "]

    assert [event.type for event in final.normalized_events] == [
        "model.message.completed"
    ]
    assert final.normalized_events[0].metadata["contentDigest"].startswith("sha256:")
    assert final.transcript_entries == normalized_events_to_transcript(
        final.normalized_events
    )
    assert [entry.kind for entry in final.transcript_entries] == ["assistant_text"]
    assert final.transcript_entries[0].text == "Hello world."

    assert transcript_entries_to_agent_events(final.transcript_entries) == [
        {"type": "text_delta", "delta": "Hello world."}
    ]


def test_pr6_single_tool_pair_uses_stable_call_id_and_public_tool_order() -> None:
    bridge = OpenMagiEventBridge()
    turn_id = "pr6-turn-tool"
    call = Event(
        id="evt-pr6-tool-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="call-pr6-stable",
                        name="Search",
                        args={"query": "event projection", "limit": 2},
                    )
                )
            ],
        ),
        invocation_id=turn_id,
        timestamp=10,
    )
    result = Event(
        id="evt-pr6-tool-result",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call-pr6-stable",
                        name="Search",
                        response={
                            "results": ["alpha", "beta"],
                            "sourceRefs": ["src_1"],
                            "digest": "sha256:" + "a" * 64,
                        },
                    )
                )
            ],
        ),
        invocation_id=turn_id,
        timestamp=11,
    )

    call_projection = bridge.project_adk_event(call, turn_id=turn_id)
    result_projection = bridge.project_adk_event(result, turn_id=turn_id)
    normalized = [
        *call_projection.normalized_events,
        *result_projection.normalized_events,
    ]

    assert [event.type for event in normalized] == [
        "tool.call.started",
        "tool.call.completed",
    ]
    assert normalized[0].call_id == "call-pr6-stable"
    assert normalized[1].call_id == "call-pr6-stable"
    assert normalized[1].metadata["toolResultRefs"] == ["sha256:" + "a" * 64]
    assert normalized[1].metadata["sourceRefs"] == ["src_1"]

    transcript_entries = normalized_events_to_transcript(normalized)
    assert [entry.kind for entry in transcript_entries] == ["tool_call", "tool_result"]
    assert transcript_entries[0].tool_use_id == "call-pr6-stable"
    assert transcript_entries[1].tool_use_id == "call-pr6-stable"

    public_events = transcript_entries_to_agent_events(transcript_entries)
    assert [event["type"] for event in public_events] == ["tool_start", "tool_end"]
    assert public_events[0]["id"] == "call-pr6-stable"
    assert public_events[1]["id"] == "call-pr6-stable"
    assert public_events[1]["status"] == "ok"


def test_pr6_tool_failure_permission_and_control_lifecycle_project() -> None:
    needs_approval = NormalizedEvent(
        type="tool.call.needs_approval",
        eventId="norm-needs-approval",
        ts=20,
        turnId="pr6-turn-control",
        callId="call-needs-approval",
        source="tool_kernel",
        toolName="Bash",
        metadata={"controlRefs": ["ctrl:req-1"], "inputDigest": "sha256:" + "1" * 64},
    )
    denied = needs_approval.model_copy(
        update={
            "type": "tool.call.denied",
            "event_id": "norm-denied",
            "metadata": {
                "controlRefs": ["ctrl:req-1"],
                "reasonDigest": "sha256:" + "2" * 64,
            },
        }
    )
    failed = needs_approval.model_copy(
        update={
            "type": "tool.call.failed",
            "event_id": "norm-failed",
            "metadata": {"errorDigest": "sha256:" + "3" * 64},
        }
    )
    requested = NormalizedEvent.from_control_event(
        ControlRequestCreatedEvent(
            eventId="ctrl-created",
            seq=1,
            ts=21,
            sessionKey="agent:main:local:pr6",
            turnId="pr6-turn-control",
            idempotencyKey="idem-created",
            request={
                "requestId": "req-1",
                "kind": "tool_permission",
                "state": "pending",
                "sessionKey": "agent:main:local:pr6",
                "turnId": "pr6-turn-control",
                "channelName": "local",
                "source": "turn",
                "prompt": "Allow tool?",
                "proposedInput": {"command": "echo safe"},
                "createdAt": 21,
                "expiresAt": 81,
            },
        )
    )
    resumed = NormalizedEvent.from_control_event(
        ControlRequestResolvedEvent(
            eventId="ctrl-resolved",
            seq=2,
            ts=22,
            sessionKey="agent:main:local:pr6",
            turnId="pr6-turn-control",
            idempotencyKey="idem-resolved",
            requestId="req-1",
            decision="approved",
        )
    )

    assert [requested.type, resumed.type] == ["control.requested", "control.resumed"]
    transcript_entries = normalized_events_to_transcript(
        [needs_approval, denied, failed, requested, resumed]
    )

    tool_results = [
        entry for entry in transcript_entries if isinstance(entry, ToolResultEntry)
    ]
    assert [entry.status for entry in tool_results] == [
        "needs_approval",
        "blocked",
        "error",
    ]
    assert [entry.kind for entry in transcript_entries[-2:]] == [
        "control_event",
        "control_event",
    ]
    assert transcript_entries[-2].event_type == "control_requested"
    assert transcript_entries[-1].event_type == "control_resumed"


def test_pr6_error_event_becomes_normalized_turn_failed_and_aborted_transcript() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "pr6-turn-error"
    projection = bridge.project_adk_event(
        Event(
            id="evt-pr6-error",
            author="model",
            invocation_id=turn_id,
            error_code="SYNTHETIC_FAILURE",
            error_message="failure at /Users/kevin/private.txt token=unsafe-secret",
            timestamp=30,
        ),
        turn_id=turn_id,
    )

    assert [event.type for event in projection.normalized_events] == ["turn.failed"]
    assert projection.normalized_events[0].source == "adk"
    assert projection.normalized_events[0].metadata["errorDigest"].startswith("sha256:")
    assert [entry.kind for entry in projection.transcript_entries] == ["turn_aborted"]
    public_transcript = normalized_events_to_transcript(projection.normalized_events)
    assert public_transcript[0].kind == "turn_aborted"
    assert public_transcript[0].reason != projection.transcript_entries[0].reason
    assert "[redacted-path]" in public_transcript[0].reason
    assert "/Users/kevin" not in public_transcript[0].reason
    assert "unsafe-secret" not in public_transcript[0].reason
    assert [event["type"] for event in projection.agent_events] == [
        "runtime_trace",
        "error",
        "turn_end",
    ]
    public_dumped = json.dumps(projection.agent_events, sort_keys=True)
    assert "/Users/kevin" not in public_dumped
    assert "unsafe-secret" not in public_dumped


def test_pr6_memory_projection_is_digest_only_redacted_and_default_off() -> None:
    normalized = [
        NormalizedEvent(
            type="model.message.completed",
            eventId="norm-secret-model",
            ts=40,
            turnId="pr6-turn-memory",
            source="adk",
            payload={
                "text": "raw answer with sk-test-secret and /Users/kevin/private.txt"
            },
            metadata={"contentDigest": "sha256:" + "4" * 64},
        ),
        NormalizedEvent(
            type="tool.call.completed",
            eventId="norm-secret-tool",
            ts=41,
            turnId="pr6-turn-memory",
            callId="call-secret",
            source="tool_kernel",
            toolName="FileRead",
            payload={"output": "raw tool output Authorization: Bearer unsafe"},
            metadata={
                "toolResultRefs": ["result:abcd1234"],
                "sourceRefs": ["src_1"],
                "outputDigest": "sha256:" + "5" * 64,
            },
        ),
    ]
    normalized_dump = json.dumps(
        [event.model_dump(by_alias=True) for event in normalized],
        sort_keys=True,
    )
    assert "sk-test-secret" not in normalized_dump
    assert "unsafe" not in normalized_dump
    assert "/Users/kevin" not in normalized_dump
    assert "raw tool output" not in normalized_dump

    transcript_entries = normalized_events_to_transcript(normalized)

    summary = project_turn_summary_for_memory(
        turn_id="pr6-turn-memory",
        normalized_events=normalized,
        transcript_entries=transcript_entries,
    )
    dumped = json.dumps(summary.model_dump(by_alias=True), sort_keys=True)

    assert summary.memory_writes_enabled is False
    assert summary.production_writes_enabled is False
    assert summary.turn_digest.startswith("sha256:")
    assert summary.event_digest.startswith("sha256:")
    assert summary.tool_result_refs == ("result:abcd1234",)
    assert summary.source_refs == ("src_1",)
    assert "sk-test-secret" not in dumped
    assert "unsafe" not in dumped
    assert "/Users/kevin" not in dumped
    assert "raw tool output" not in dumped

    public_events = transcript_entries_to_agent_events(transcript_entries)
    public_dumped = json.dumps(public_events, sort_keys=True)
    assert "Authorization: Bearer unsafe" not in public_dumped
    assert "/Users/kevin" not in public_dumped
    assert "sk-test-secret" not in public_dumped
    assert "result:abcd1234" in public_dumped
    assert "toolResultRefs" in public_dumped


def test_pr6_transcript_to_agent_events_redacts_private_paths_and_session_keys() -> None:
    normalized = [
        NormalizedEvent(
            type="tool.call.completed",
            eventId="norm-private-tool",
            ts=50,
            turnId="/Users/kevin/private-turn",
            callId="call-private",
            source="tool_kernel",
            toolName="FileRead",
            payload={
                "output": {
                    "text": "sessionKey=unsafe-session-key",
                    "path": "/Users/kevin/private.txt",
                }
            },
            metadata={"outputDigest": "sha256:" + "6" * 64},
        )
    ]
    entries = normalized_events_to_transcript(normalized)

    public_events = transcript_entries_to_agent_events(entries)
    dumped = json.dumps(public_events, sort_keys=True)

    assert "unsafe-session-key" not in dumped
    assert "/Users/kevin" not in dumped
    assert "sha256:6666666666666666666666666666666666666666666666666666666666666666" in dumped


def test_pr6_normalized_events_are_raw_free_by_construction() -> None:
    event = NormalizedEvent(
        type="tool.call.started",
        eventId="norm-raw-construction",
        ts=60,
        turnId="turn-raw-construction",
        callId="call-raw-construction",
        source="tool_kernel",
        toolName="FileRead",
        payload={
            "input": {
                "path": "/Users/kevin/private.txt",
                "authorization": "Bearer unsafe-token",
                "patch": "*** Begin Patch\n+SECRET\n*** End Patch",
            }
        },
        metadata={
            "apiKey": "plain-api-key-value",
            "authHeader": "Authorization: Bearer plain-auth-value",
            "hiddenReasoning": "non-secret private chain of thought",
            "privateMemory": "non-secret private memory content",
            "rawArgs": {"authorization": "Bearer unsafe-token"},
            "rawOutput": "non-secret raw tool output body",
            "sessionId": "plain-session-id",
        },
    )
    dumped = json.dumps(event.model_dump(by_alias=True), sort_keys=True)

    assert "plain-api-key-value" not in dumped
    assert "plain-auth-value" not in dumped
    assert "plain-session-id" not in dumped
    assert "unsafe-token" not in dumped
    assert "/Users/kevin" not in dumped
    assert "*** Begin Patch" not in dumped
    assert "private chain of thought" not in dumped
    assert "private memory content" not in dumped
    assert "raw tool output body" not in dumped
    assert "inputDigest" in dumped
    assert "hiddenReasoningDigest" in dumped
    assert "privateMemoryDigest" in dumped
    assert "rawArgsDigest" in dumped
    assert "rawOutputDigest" in dumped
    assert "apiKeyDigest" in dumped
    assert "authHeaderDigest" in dumped
    assert "sessionIdDigest" in dumped


def test_pr6_structured_reason_payloads_digest_private_fields() -> None:
    events = [
        NormalizedEvent(
            type="tool.call.needs_approval",
            eventId="norm-private-approval-reason",
            ts=67,
            turnId="turn-private-reason",
            callId="call-private-reason",
            source="tool_kernel",
            toolName="FileRead",
            payload={
                "reason": {
                    "hiddenReasoning": "private chain of thought",
                    "summary": "safe approval summary",
                }
            },
        ),
        NormalizedEvent(
            type="turn.failed",
            eventId="norm-private-turn-reason",
            ts=68,
            turnId="turn-private-reason",
            source="adk",
            payload={
                "reasonPreview": {
                    "childOutput": "child private output",
                    "summary": "safe failure summary",
                }
            },
        ),
        NormalizedEvent(
            type="tool.call.denied",
            eventId="norm-private-string-reason",
            ts=69,
            turnId="turn-private-reason",
            callId="call-private-string-reason",
            source="tool_kernel",
            toolName="FileRead",
            payload={
                "reason": json.dumps(
                    {
                        "hiddenReasoning": "stringified private reasoning",
                        "summary": "safe stringified denial summary",
                    },
                    sort_keys=True,
                )
            },
        ),
    ]
    dumped = json.dumps(
        [event.model_dump(by_alias=True) for event in events],
        sort_keys=True,
    )

    assert "private chain of thought" not in dumped
    assert "child private output" not in dumped
    assert "stringified private reasoning" not in dumped
    assert "hiddenReasoningDigest" in dumped
    assert "childOutputDigest" in dumped
    assert "safe approval summary" in dumped
    assert "safe failure summary" in dumped
    assert "safe stringified denial summary" in dumped


def test_pr6_normalized_event_refs_digest_session_auth_cookie_key_shapes() -> None:
    event = NormalizedEvent(
        type="tool.call.completed",
        eventId="event-sessionKey-abc",
        ts=61,
        turnId="turn-auth-cookie",
        callId="call-api-key-123",
        source="tool_kernel",
        toolName="FileRead",
        payload={"output": "ok"},
        metadata={"outputDigest": "sha256:" + "7" * 64},
    )
    dumped = json.dumps(event.model_dump(by_alias=True), sort_keys=True)

    assert "event-sessionKey-abc" not in dumped
    assert "turn-auth-cookie" not in dumped
    assert "call-api-key-123" not in dumped
    assert event.event_id.startswith("event:")
    assert event.turn_id.startswith("turn:")
    assert event.call_id is not None
    assert event.call_id.startswith("call:")


def test_pr6_public_agent_events_digest_private_transcript_ids() -> None:
    entries = [
        ToolCallEntry(
            ts=62,
            turnId="turn-private-transcript",
            toolUseId="authCookieSessionRaw123",
            name="FileRead",
            input={"inputDigest": "sha256:" + "8" * 64},
        ),
        ToolResultEntry(
            ts=63,
            turnId="turn-private-transcript",
            toolUseId="authCookieSessionRaw123",
            status="ok",
            output='{"digest":"sha256:' + "9" * 64 + '"}',
        ),
        ControlEventTranscriptEntry(
            ts=64,
            turnId="turn-private-transcript",
            seq=1,
            eventId="cookieAuthSessionRaw789",
            eventType="control_requested",
        ),
    ]

    public_events = transcript_entries_to_agent_events(entries)
    dumped = json.dumps(public_events, sort_keys=True)

    assert "authCookieSessionRaw123" not in dumped
    assert "cookieAuthSessionRaw789" not in dumped
    assert public_events[0]["id"].startswith("call:")
    assert public_events[1]["id"].startswith("call:")
    assert public_events[2]["eventId"].startswith("event:")


def test_pr6_public_agent_events_digest_private_transcript_tool_fields() -> None:
    entries = [
        ToolCallEntry(
            ts=65,
            turnId="turn-private-tool-fields",
            toolUseId="call-private-tool-fields",
            name="FileRead",
            input={
                "path": "/Users/kevin/private.txt",
                "hiddenReasoning": "private chain of thought",
                "payload": json.dumps(
                    {
                        "rawArgs": "stringified raw args",
                        "summary": "safe stringified input summary",
                    },
                    sort_keys=True,
                ),
                "privateMemory": "private memory body",
                "rawArgs": {"query": "raw private args"},
            },
        ),
        ToolResultEntry(
            ts=66,
            turnId="turn-private-tool-fields",
            toolUseId="call-private-tool-fields",
            status="ok",
            output=json.dumps(
                {
                    "childOutput": "child private output",
                    "payload": json.dumps(
                        {
                            "rawOutput": "stringified raw output",
                            "summary": "safe stringified output summary",
                        },
                        sort_keys=True,
                    ),
                    "rawOutput": "raw private result",
                    "summary": "safe public summary",
                },
                sort_keys=True,
            ),
        ),
    ]

    public_events = transcript_entries_to_agent_events(entries)
    dumped = json.dumps(public_events, sort_keys=True)

    for leaked in (
        "/Users/kevin",
        "private chain of thought",
        "private memory body",
        "raw private args",
        "stringified raw args",
        "child private output",
        "raw private result",
        "stringified raw output",
    ):
        assert leaked not in dumped
    for digest_key in (
        "hiddenReasoningDigest",
        "privateMemoryDigest",
        "rawArgsDigest",
        "childOutputDigest",
        "rawOutputDigest",
    ):
        assert digest_key in dumped
    assert "safe public summary" in dumped
    assert "safe stringified input summary" in dumped
    assert "safe stringified output summary" in dumped


def test_pr6_event_bridge_sanitizes_sensitive_adk_tool_ids_publicly() -> None:
    bridge = OpenMagiEventBridge()
    turn_id = "pr6-turn-sensitive-tool-id"
    call = Event(
        id="evt-pr6-sensitive-tool-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="/Users/kevin/.ssh/id_rsa?sessionKey=abc",
                        name="FileRead",
                        args={"path": "/Users/kevin/private.txt"},
                    )
                )
            ],
        ),
        invocation_id=turn_id,
        timestamp=62,
    )
    result = Event(
        id="evt-pr6-sensitive-tool-result",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="cookie-auth-key",
                        name="FileRead",
                        response={"digest": "sha256:" + "8" * 64},
                    )
                )
            ],
        ),
        invocation_id=turn_id,
        timestamp=63,
    )

    call_projection = bridge.project_adk_event(call, turn_id=turn_id)
    result_projection = bridge.project_adk_event(result, turn_id=turn_id)
    dumped = json.dumps(
        [*call_projection.agent_events, *result_projection.agent_events],
        sort_keys=True,
    )

    assert "/Users/kevin" not in dumped
    assert "sessionKey" not in dumped
    assert "cookie-auth-key" not in dumped
    assert call_projection.agent_events[0]["id"].startswith("adk-tool-call:")
    assert result_projection.agent_events[0]["id"].startswith("adk-tool-response:")


def test_pr6_projection_import_boundary_has_no_runner_network_or_product_imports() -> None:
    forbidden_fragments = (
        "google.adk.runners",
        "google.adk.models",
        "google.adk.clients",
        "network",
        "provider_execution",
        "chat_proxy",
        "supabase",
        "vercel",
        "k8s",
        "frontend",
        "deploy",
    )
    probe = """
import importlib
import json
import sys

importlib.import_module("openmagi_core_agent.runtime.events")
importlib.import_module("openmagi_core_agent.memory.projection")

print(json.dumps(sorted(sys.modules)))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        text=True,
        capture_output=True,
    )
    loaded = "\n".join(json.loads(result.stdout))
    for fragment in forbidden_fragments:
        assert fragment not in loaded


def test_pr6_projection_contract_remains_local_default_off() -> None:
    contract = NormalizedProjectionContract()

    assert contract.schema_version == "normalizedProjectionContract.v1"
    assert contract.network_enabled is False
    assert contract.model_calls_enabled is False
    assert contract.production_writes_enabled is False
    assert contract.transcript_writes_enabled is False
    assert contract.sse_writes_enabled is False
    assert contract.memory_writes_enabled is False
