import json
import math

from google.adk.events import Event
from google.genai import types

from openmagi_core_agent.adk_bridge.event_adapter import (
    OpenMagiEventBridge,
    project_runner_end_event,
    project_runner_heartbeat_event,
    project_runner_llm_progress_event,
    project_runner_model_fallback_event,
    project_runner_phase_event,
    project_runner_retry_event,
    project_runner_start_event,
)
from openmagi_core_agent.transport.sse import InMemorySseWriter


def _render(events: list[dict[str, object]]) -> str:
    return json.dumps(events, sort_keys=True)


def _sse_payloads(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


SYNTHETIC_GITHUB_PAT = (
    "github"
    "_pat_"
    "012345678901234567890123456789"
)
SYNTHETIC_GHP_TOKEN = (
    "ghp_"
    "abcdefghijklmnopqrstuvwxyz0123456789"
)
SYNTHETIC_GOOGLE_KEY = (
    "AIza"
    "123456789012345678901234567890123"
)
SYNTHETIC_SLACK_TOKEN = (
    "xox"
    "b-"
    "123456789012345678901234"
)
SYNTHETIC_AWS_KEY = (
    "AKIA"
    "12345678"
    "90ABCDEF"
)
SYNTHETIC_JWT = (
    "eyJhbGciOiJIUzI1NiJ9"
    "."
    "eyJzdWIiOiJzZWNyZXQifQ"
    "."
    "signature00"
)


def test_runner_lifecycle_helpers_project_public_only_agent_events() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    turn_id = "session:/data/bots/private-bot/transcripts/secret-turn"
    receipt_ref = "receipt:sha256:" + ("a" * 64)

    projections = [
        project_runner_start_event(turn_id=turn_id, declared_route="private-route"),
        bridge.project_runner_phase_event(
            turn_id=turn_id,
            phase="model_running",
            label="internal label with Authorization: Bearer phase.SECRET",
        ),
        bridge.project_runner_llm_progress_event(
            turn_id=turn_id,
            stage="waiting",
            label=f"model phase with {SYNTHETIC_GITHUB_PAT}",
            detail=(
                f"raw payload {SYNTHETIC_GOOGLE_KEY} "
                "memory/ROOT.md"
            ),
            iter=1,
            elapsed_ms=250,
        ),
        bridge.project_runner_heartbeat_event(
            turn_id=turn_id,
            iter=2,
            elapsed_ms=1250,
            last_event_at=1710000007,
        ),
        bridge.project_runner_retry_event(
            turn_id=turn_id,
            reason=f"provider failed with token={SYNTHETIC_GITHUB_PAT}",
            retry_no=2,
            tool_use_id="/workspace/private/tool-call.json",
            tool_name=f"Fetch with {SYNTHETIC_SLACK_TOKEN}",
        ),
        project_runner_model_fallback_event(
            turn_id=turn_id,
            from_model="gemini-primary",
            to_model="gemini-fallback",
            reason="provider_rate_limited",
            attempt=3,
        ),
        project_runner_end_event(
            turn_id=turn_id,
            status="committed",
            stop_reason=(
                "private active snapshot sk-proj-secret session/turn-123 "
                f"{SYNTHETIC_AWS_KEY} "
                f"{SYNTHETIC_JWT}"
            ),
            usage={"inputTokens": 12, "outputTokens": 4, "costUsd": 0.25},
            receipt_ref=receipt_ref,
        ),
    ]

    events = [event for projection in projections for event in projection.agent_events]

    assert events == [
        {
            "type": "turn_start",
            "turnId": events[0]["turnId"],
            "declaredRoute": "direct",
        },
        {
            "type": "turn_phase",
            "turnId": events[0]["turnId"],
            "phase": "executing",
        },
        {
            "type": "llm_progress",
            "turnId": events[0]["turnId"],
            "stage": "waiting",
            "label": "model phase with [redacted]",
            "detail": "[redacted-private]",
            "iter": 1,
            "elapsedMs": 250,
        },
        {
            "type": "heartbeat",
            "turnId": events[0]["turnId"],
            "iter": 2,
            "elapsedMs": 1250,
            "lastEventAt": 1710000007,
        },
        {
            "type": "retry",
            "reason": "retry_scheduled",
            "retryNo": 2,
            "toolUseId": events[4]["toolUseId"],
            "toolName": "Fetch with [redacted]",
        },
        {
            "type": "runtime_trace",
            "turnId": events[0]["turnId"],
            "phase": "retry_scheduled",
            "severity": "warning",
            "title": "Model fallback selected",
            "reasonCode": "provider_rate_limited",
            "detail": "gemini-primary -> gemini-fallback",
            "attempt": 3,
        },
        {
            "type": "turn_end",
            "turnId": events[0]["turnId"],
            "status": "committed",
            "stopReason": "end_turn",
            "receiptRef": receipt_ref,
            "usage": {"inputTokens": 12, "outputTokens": 4, "costUsd": 0.25},
        },
    ]
    assert isinstance(events[0]["turnId"], str)
    assert events[0]["turnId"].startswith("turn:")
    rendered = _render(events)
    for leaked in (
        "/data/bots",
        "/workspace",
        "private-bot",
        "secret-turn",
        "Authorization",
        "Bearer phase.SECRET",
        SYNTHETIC_GHP_TOKEN,
        SYNTHETIC_GITHUB_PAT,
        SYNTHETIC_GOOGLE_KEY,
        SYNTHETIC_SLACK_TOKEN,
        SYNTHETIC_AWS_KEY,
        "eyJhbGciOiJIUzI1NiJ9",
        "sk-proj-secret",
        "memory/ROOT.md",
        "session/turn-123",
        "raw payload",
        "private active snapshot",
        "private-route",
        "internal label",
    ):
        assert leaked not in rendered
    for projection in projections:
        assert projection.legacy_deltas == []
        assert projection.transcript_entries == []
        assert projection.normalized_events == []

    writer = InMemorySseWriter()
    for event in events:
        writer.agent(event)

    payloads = _sse_payloads(writer.body)
    assert [payload["type"] for payload in payloads] == [
        "turn_start",
        "turn_phase",
        "llm_progress",
        "heartbeat",
        "retry",
        "runtime_trace",
        "turn_end",
    ]
    assert payloads[5] == {
        "type": "runtime_trace",
        "turnId": events[0]["turnId"],
        "phase": "retry_scheduled",
        "severity": "warning",
        "title": "Model fallback selected",
        "detail": "gemini-primary -> gemini-fallback",
        "reasonCode": "provider_rate_limited",
        "attempt": 3,
    }
    assert payloads[6] == {
        "type": "turn_end",
        "turnId": events[0]["turnId"],
        "status": "committed",
        "receiptRef": receipt_ref,
        "usage": {"inputTokens": 12, "outputTokens": 4, "costUsd": 0.25},
    }
    assert "event: agent" in writer.body
    for leaked in (
        "/data/bots",
        "/workspace",
        SYNTHETIC_GITHUB_PAT,
        SYNTHETIC_GOOGLE_KEY,
        SYNTHETIC_SLACK_TOKEN,
        SYNTHETIC_AWS_KEY,
        "eyJhbGciOiJIUzI1NiJ9",
        "sk-proj-secret",
        "memory/ROOT.md",
        "session/turn-123",
        "raw payload",
        "private active snapshot",
    ):
        assert leaked not in writer.body


def test_runner_lifecycle_private_turn_refs_remain_distinct_without_leaks() -> None:
    private_turn_ids = [
        "session:turn-a",
        "session:turn-b",
        "memory/one",
        "/data/bots/private-bot/transcripts/turn-a.json",
        "/data/bots/private-bot/transcripts/turn-b.json",
    ]

    public_turn_ids = [
        project_runner_start_event(turn_id=turn_id).agent_events[0]["turnId"]
        for turn_id in private_turn_ids
    ]

    assert len(set(public_turn_ids)) == len(private_turn_ids)
    rendered = _render([{"turnId": turn_id} for turn_id in public_turn_ids])
    for leaked in ("session:turn", "memory/one", "/data/bots", "private-bot"):
        assert leaked not in rendered


def test_runner_lifecycle_redacts_raw_payload_variants() -> None:
    for detail in (
        "raw event payload: response body hello",
        "raw provider payload: provider body hello",
        "raw provider response: provider body secret",
        "raw model payload: model body hello",
        "raw child transcript: child transcript secret",
        "rawPayload: compact raw body",
        "rawToolArguments: compact account id",
        "sourceSnapshot: compact proprietary source",
        "hiddenReasoning: compact hidden thought",
        "raw tool arguments: account id customer-123",
        "raw tool input: account id customer-456",
        "raw tool response: account id customer-789",
        "tool result: account id customer-abc",
        "source snapshot: proprietary source text",
        "developer prompt: private instruction",
    ):
        projection = project_runner_llm_progress_event(
            turn_id="turn-public",
            stage="waiting",
            detail=detail,
        )
        event = projection.agent_events[0]

        assert event["detail"] == "[redacted-private]"

        writer = InMemorySseWriter()
        writer.agent(event)
        assert detail not in writer.body
        assert "response body hello" not in writer.body
        assert "provider body hello" not in writer.body
        assert "provider body secret" not in writer.body
        assert "model body hello" not in writer.body
        assert "child transcript secret" not in writer.body
        assert "compact raw body" not in writer.body
        assert "compact account id" not in writer.body
        assert "compact proprietary source" not in writer.body
        assert "compact hidden thought" not in writer.body
        assert "account id customer" not in writer.body
        assert "proprietary source text" not in writer.body
        assert "private instruction" not in writer.body


def test_runner_lifecycle_preview_digests_private_marker_key_variants() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-private-key-variants",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-private-key-variants",
                        name="PrivatePreview",
                        args={
                            "rawToolArguments": "account id customer-123",
                            "toolArgs": "account id customer-args",
                            "toolResult": "account id customer-result",
                            "sourceSnapshot": "proprietary source text",
                            "hiddenReasoning": "compact hidden thought",
                            "summary": "safe summary",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-key-variants",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-private-key-variants")
    preview = projection.agent_events[0]["input_preview"]

    assert isinstance(preview, str)
    assert "rawToolArgumentsDigest" in preview
    assert "toolArgsDigest" in preview
    assert "sourceSnapshotDigest" in preview
    assert "hiddenReasoningDigest" in preview
    assert "safe summary" in preview
    for leaked in (
        "account id customer-123",
        "account id customer-args",
        "account id customer-result",
        "proprietary source text",
        "compact hidden thought",
    ):
        assert leaked not in preview

    call_variant_event = Event(
        id="event-private-call-key-variants",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-private-call-key-variants",
                        name="PrivatePreview",
                        args={
                            "rawToolCallArgs": "account id customer-tool-call-args",
                            "toolCallResult": "account id customer-tool-call-result",
                            "functionCallArgs": "account id customer-function-call-args",
                            "summary": "safe call variant summary",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-key-variants",
    )

    call_variant_projection = bridge.project_adk_event(
        call_variant_event,
        turn_id="turn-private-key-variants",
    )
    call_variant_preview = call_variant_projection.agent_events[0]["input_preview"]

    assert isinstance(call_variant_preview, str)
    assert "rawToolCallArgsDigest" in call_variant_preview
    assert "toolCallResultDigest" in call_variant_preview
    assert "functionCallArgsDigest" in call_variant_preview
    assert "safe call variant summary" in call_variant_preview
    assert "account id customer-tool-call-args" not in call_variant_preview
    assert "account id customer-tool-call-result" not in call_variant_preview
    assert "account id customer-function-call-args" not in call_variant_preview

    tool_use_variant_event = Event(
        id="event-private-tool-use-key-variants",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-private-tool-use-key-variants",
                        name="PrivatePreview",
                        args={
                            "rawToolUseInput": "account id customer-tool-use-input",
                            "toolUseResponse": "account id customer-tool-use-response",
                            "summary": "safe variant summary",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-key-variants",
    )

    tool_use_variant_projection = bridge.project_adk_event(
        tool_use_variant_event,
        turn_id="turn-private-key-variants",
    )
    tool_use_variant_preview = tool_use_variant_projection.agent_events[0][
        "input_preview"
    ]

    assert isinstance(tool_use_variant_preview, str)
    assert "rawToolUseInputDigest" in tool_use_variant_preview
    assert "toolUseResponseDigest" in tool_use_variant_preview
    assert "safe variant summary" in tool_use_variant_preview
    assert "account id customer-tool-use-input" not in tool_use_variant_preview
    assert "account id customer-tool-use-response" not in tool_use_variant_preview

    nested_call_event = Event(
        id="event-private-nested-call-key-variants",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-private-nested-call-key-variants",
                        name="PrivatePreview",
                        args={
                            "toolCall": {"args": "PRIVATE-TOOL-CALL-ARGS"},
                            "toolCalls": [{"args": "PRIVATE-TOOL-CALLS-ARGS"}],
                            "functionCall": {"args": "PRIVATE-FUNCTION-CALL-ARGS"},
                            "functionCalls": [{"args": "PRIVATE-FUNCTION-CALLS-ARGS"}],
                            "summary": "safe nested call summary",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-key-variants",
    )

    nested_call_projection = bridge.project_adk_event(
        nested_call_event,
        turn_id="turn-private-key-variants",
    )
    nested_call_preview = nested_call_projection.agent_events[0]["input_preview"]

    assert isinstance(nested_call_preview, str)
    assert "toolCallDigest" in nested_call_preview
    assert "toolCallsDigest" in nested_call_preview
    assert "functionCallDigest" in nested_call_preview
    assert "functionCallsDigest" in nested_call_preview
    assert "safe nested call summary" in nested_call_preview
    assert "PRIVATE-TOOL-CALL-ARGS" not in nested_call_preview
    assert "PRIVATE-TOOL-CALLS-ARGS" not in nested_call_preview
    assert "PRIVATE-FUNCTION-CALL-ARGS" not in nested_call_preview
    assert "PRIVATE-FUNCTION-CALLS-ARGS" not in nested_call_preview

    nested_tool_use_event = Event(
        id="event-private-nested-tool-use-key-variants",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-private-nested-tool-use-key-variants",
                        name="PrivatePreview",
                        args={
                            "toolUse": {"input": "PRIVATE-TOOL-USE-INPUT"},
                            "toolUses": [{"input": "PRIVATE-TOOL-USES-INPUT"}],
                            "summary": "safe nested variant summary",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-key-variants",
    )

    nested_tool_use_projection = bridge.project_adk_event(
        nested_tool_use_event,
        turn_id="turn-private-key-variants",
    )
    nested_tool_use_preview = nested_tool_use_projection.agent_events[0][
        "input_preview"
    ]

    assert isinstance(nested_tool_use_preview, str)
    assert "toolUseDigest" in nested_tool_use_preview
    assert "toolUsesDigest" in nested_tool_use_preview
    assert "safe nested variant summary" in nested_tool_use_preview
    assert "PRIVATE-TOOL-USE-INPUT" not in nested_tool_use_preview
    assert "PRIVATE-TOOL-USES-INPUT" not in nested_tool_use_preview

    response_event = Event(
        id="event-private-response-key-variants",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-private-response-key-variants",
                        name="PrivatePreview",
                        response={
                            "toolResponse": "account id customer-response",
                            "toolLogs": "account id customer-logs",
                            "summary": "safe response summary",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-key-variants",
    )

    response_projection = bridge.project_adk_event(
        response_event,
        turn_id="turn-private-key-variants",
    )
    response_preview = response_projection.agent_events[0]["output_preview"]

    assert isinstance(response_preview, str)
    assert "toolResponseDigest" in response_preview
    assert "toolLogsDigest" in response_preview
    assert "safe response summary" in response_preview
    assert "account id customer-response" not in response_preview
    assert "account id customer-logs" not in response_preview

    response_variant_event = Event(
        id="event-private-response-variant-key-variants",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-private-response-variant-key-variants",
                        name="PrivatePreview",
                        response={
                            "toolCallResponse": "account id customer-tool-call-response",
                            "toolCallLogs": "account id customer-tool-call-logs",
                            "functionResponse": "account id customer-function-response",
                            "summary": "safe response variant summary",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-key-variants",
    )

    response_variant_projection = bridge.project_adk_event(
        response_variant_event,
        turn_id="turn-private-key-variants",
    )
    response_variant_preview = response_variant_projection.agent_events[0][
        "output_preview"
    ]

    assert isinstance(response_variant_preview, str)
    assert "toolCallResponseDigest" in response_variant_preview
    assert "toolCallLogsDigest" in response_variant_preview
    assert "functionResponseDigest" in response_variant_preview
    assert "safe response variant summary" in response_variant_preview
    assert "account id customer-tool-call-response" not in response_variant_preview
    assert "account id customer-tool-call-logs" not in response_variant_preview
    assert "account id customer-function-response" not in response_variant_preview


def test_adk_text_projection_drops_thought_parts_and_redacts_private_markers() -> None:
    bridge = OpenMagiEventBridge()
    thought_event = Event(
        id="event-thought-text",
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(text="PRIVATE_THOUGHT hiddenReasoning: secret", thought=True)],
        ),
        partial=True,
        invocation_id="turn-thought-text",
    )

    thought_projection = bridge.project_adk_event(
        thought_event,
        turn_id="turn-thought-text",
    )

    assert thought_projection.agent_events == []
    assert thought_projection.legacy_deltas == []
    assert thought_projection.transcript_entries == []
    assert thought_projection.normalized_events == []

    partial_event = Event(
        id="event-private-marker-delta",
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(text="rawPayload: SECRET toolResult: SECRET")],
        ),
        partial=True,
        invocation_id="turn-private-marker-delta",
    )

    partial_projection = bridge.project_adk_event(
        partial_event,
        turn_id="turn-private-marker-delta",
    )

    assert partial_projection.agent_events == [
        {"type": "text_delta", "delta": "[redacted-private]"}
    ]
    assert partial_projection.legacy_deltas == ["[redacted-private]"]
    assert partial_projection.normalized_events[0].payload["textPreview"] == (
        "[redacted-private]"
    )
    rendered_partial = json.dumps(
        [
            partial_projection.agent_events,
            partial_projection.legacy_deltas,
            partial_projection.normalized_events[0].payload,
        ],
        sort_keys=True,
    )
    assert "SECRET" not in rendered_partial
    assert "rawPayload" not in rendered_partial
    assert "toolResult" not in rendered_partial

    final_event = Event(
        id="event-private-marker-final",
        author="model",
        content=types.Content(
            role="model",
            parts=[types.Part(text="hiddenReasoning: final SECRET")],
        ),
        partial=False,
        turn_complete=True,
        invocation_id="turn-private-marker-final",
    )

    final_projection = bridge.project_adk_event(
        final_event,
        turn_id="turn-private-marker-final",
    )

    assert final_projection.transcript_entries[0].text == "[redacted-private]"
    assert final_projection.normalized_events[0].payload["textPreview"] == (
        "[redacted-private]"
    )
    assert "final SECRET" not in json.dumps(
        [
            final_projection.transcript_entries[0].text,
            final_projection.normalized_events[0].payload,
        ],
        sort_keys=True,
    )


def test_adk_normalized_event_ids_hash_private_adk_event_ids() -> None:
    bridge = OpenMagiEventBridge()

    first_partial = Event(
        id="rawPayload-customer-123-internal-body",
        author="model",
        content=types.Content(role="model", parts=[types.Part(text="safe delta")]),
        partial=True,
        invocation_id="turn-private-id",
    )
    second_partial = Event(
        id="rawPayload-customer-456-internal-body",
        author="model",
        content=types.Content(role="model", parts=[types.Part(text="safe delta")]),
        partial=True,
        invocation_id="turn-private-id",
    )
    final = Event(
        id="sourceSnapshot-customer-123-internal-body",
        author="model",
        content=types.Content(role="model", parts=[types.Part(text="safe final")]),
        partial=False,
        turn_complete=True,
        invocation_id="turn-private-id",
    )
    call = Event(
        id="toolLogs-customer-123-internal-body",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-safe-call",
                        name="PrivatePreview",
                        args={"summary": "safe"},
                    )
                )
            ],
        ),
        invocation_id="turn-private-id",
    )
    response = Event(
        id="functionLogs-customer-123-internal-body",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-safe-response",
                        name="PrivatePreview",
                        response={"summary": "safe"},
                    )
                )
            ],
        ),
        invocation_id="turn-private-id",
    )

    projections = [
        bridge.project_adk_event(first_partial, turn_id="turn-private-id"),
        bridge.project_adk_event(second_partial, turn_id="turn-private-id"),
        bridge.project_adk_event(final, turn_id="turn-private-id"),
        bridge.project_adk_event(call, turn_id="turn-private-id"),
        bridge.project_adk_event(response, turn_id="turn-private-id"),
    ]
    event_ids = [
        normalized.event_id
        for projection in projections
        for normalized in projection.normalized_events
    ]

    assert len(event_ids) == 5
    assert len(set(event_ids)) == len(event_ids)
    assert all(event_id.startswith("adk-event-") for event_id in event_ids)
    rendered = json.dumps(event_ids, sort_keys=True)
    for leaked in (
        "rawPayload",
        "sourceSnapshot",
        "toolLogs",
        "functionLogs",
        "customer-123",
        "customer-456",
        "internal-body",
    ):
        assert leaked not in rendered


def test_adk_tool_ids_and_names_hash_or_redact_private_markers() -> None:
    bridge = OpenMagiEventBridge()

    first_call = Event(
        id="event-private-tool-call-1",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="rawPayload-customer-123-internal-body",
                        name="toolLogs-customer-123-internal-body",
                        args={"summary": "safe first call"},
                    )
                )
            ],
        ),
        invocation_id="turn-private-tools",
    )
    second_call = Event(
        id="event-private-tool-call-2",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="rawPayload-customer-456-internal-body",
                        name="functionLogs-customer-456-internal-body",
                        args={"summary": "safe second call"},
                    )
                )
            ],
        ),
        invocation_id="turn-private-tools",
    )
    response = Event(
        id="event-private-tool-response",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="sourceSnapshot-customer-123-internal-body",
                        name="toolLogs-customer-123-internal-body",
                        response={"summary": "safe response"},
                    )
                )
            ],
        ),
        invocation_id="turn-private-tools",
    )

    first_projection = bridge.project_adk_event(first_call, turn_id="turn-private-tools")
    second_projection = bridge.project_adk_event(second_call, turn_id="turn-private-tools")
    response_projection = bridge.project_adk_event(response, turn_id="turn-private-tools")

    first_tool_id = first_projection.agent_events[0]["id"]
    second_tool_id = second_projection.agent_events[0]["id"]
    response_tool_id = response_projection.agent_events[0]["id"]
    assert isinstance(first_tool_id, str)
    assert isinstance(second_tool_id, str)
    assert isinstance(response_tool_id, str)
    assert first_tool_id.startswith("adk-tool-call:")
    assert second_tool_id.startswith("adk-tool-call:")
    assert response_tool_id.startswith("adk-tool-response:")
    assert len({first_tool_id, second_tool_id, response_tool_id}) == 3

    assert first_projection.agent_events[0]["name"] == "[redacted-private]"
    assert second_projection.agent_events[0]["name"] == "[redacted-private]"
    assert first_projection.transcript_entries[0].name == "[redacted-private]"
    assert second_projection.transcript_entries[0].name == "[redacted-private]"
    assert first_projection.normalized_events[0].tool_name == "[redacted-private]"
    assert second_projection.normalized_events[0].tool_name == "[redacted-private]"
    assert response_projection.normalized_events[0].tool_name == "[redacted-private]"
    assert first_projection.normalized_events[0].call_id == first_tool_id
    assert second_projection.normalized_events[0].call_id == second_tool_id
    assert response_projection.normalized_events[0].call_id == response_tool_id

    rendered = json.dumps(
        {
            "agentEvents": [
                *first_projection.agent_events,
                *second_projection.agent_events,
                *response_projection.agent_events,
            ],
            "transcript": [
                entry.model_dump(by_alias=True)
                for projection in (
                    first_projection,
                    second_projection,
                    response_projection,
                )
                for entry in projection.transcript_entries
            ],
            "normalized": [
                normalized.model_dump(by_alias=True)
                for projection in (
                    first_projection,
                    second_projection,
                    response_projection,
                )
                for normalized in projection.normalized_events
            ],
        },
        sort_keys=True,
    )
    for leaked in (
        "rawPayload",
        "sourceSnapshot",
        "toolLogs",
        "functionLogs",
        "customer-123",
        "customer-456",
        "internal-body",
    ):
        assert leaked not in rendered


def test_runner_lifecycle_helpers_bound_invalid_fields() -> None:
    start = project_runner_start_event(
        turn_id="turn-public",
        declared_route="pipeline",
    )
    invalid_start = project_runner_start_event(
        turn_id="turn-public",
        declared_route="provider-private-route",
    )
    phase = project_runner_phase_event(
        turn_id="turn-public",
        phase="provider-private-phase",
    )
    aliased_phase = project_runner_phase_event(
        turn_id="turn-public",
        phase="model_finalizing",
    )
    llm_progress = project_runner_llm_progress_event(
        turn_id="turn-public",
        stage="provider-private-stage",
        label="label " + ("l" * 500),
        detail="detail " + ("d" * 500),
        iter=100_001,
        elapsed_ms=86_400_001,
    )
    heartbeat = project_runner_heartbeat_event(
        turn_id="turn-public",
        iter=100_001,
        elapsed_ms=math.inf,
        last_event_at=math.nan,
    )
    retry = project_runner_retry_event(
        turn_id="turn-public",
        reason="raw retry text with sk-proj-secret",
        retry_no=11,
        tool_use_id="raw:/Users/kevin/private/tool-call",
        tool_name="tool " + ("t" * 500),
    )
    fallback = project_runner_model_fallback_event(
        turn_id="turn-public",
        from_model="from-" + ("x" * 300),
        to_model="to-" + ("y" * 300),
        reason="reason " + ("z" * 500),
        attempt=11,
    )
    end = project_runner_end_event(
        turn_id="turn-public",
        status="provider-private-status",
        reason="aborted because password=very-secret " + ("q" * 500),
        usage={"inputTokens": -5, "outputTokens": math.inf, "costUsd": math.nan},
    )
    unreceipted_committed = project_runner_end_event(
        turn_id="turn-public",
        status="committed",
        stop_reason="end_turn",
    )
    overflow_heartbeat = project_runner_heartbeat_event(
        turn_id="turn-public",
        iter=10**400,
        elapsed_ms=10**400,
        last_event_at=10**400,
    )
    overflow_llm_progress = project_runner_llm_progress_event(
        turn_id="turn-public",
        stage="waiting",
        iter=10**400,
        elapsed_ms=10**400,
    )
    overflow_committed = project_runner_end_event(
        turn_id="turn-public",
        status="committed",
        stop_reason="end_turn",
        usage={"inputTokens": 10**400, "outputTokens": 1, "costUsd": 0.1},
        receipt_ref="receipt:sha256:" + ("f" * 64),
    )

    assert start.agent_events == [
        {"type": "turn_start", "turnId": "turn-public", "declaredRoute": "pipeline"}
    ]
    assert invalid_start.agent_events == [
        {"type": "turn_start", "turnId": "turn-public", "declaredRoute": "direct"}
    ]
    assert phase.agent_events == [
        {"type": "turn_phase", "turnId": "turn-public", "phase": "pending"}
    ]
    assert aliased_phase.agent_events == [
        {"type": "turn_phase", "turnId": "turn-public", "phase": "committing"}
    ]
    llm_progress_event = llm_progress.agent_events[0]
    assert llm_progress_event["type"] == "llm_progress"
    assert llm_progress_event["turnId"] == "turn-public"
    assert llm_progress_event["stage"] == "waiting"
    assert len(str(llm_progress_event["label"])) == 240
    assert len(str(llm_progress_event["detail"])) == 240
    assert "iter" not in llm_progress_event
    assert "elapsedMs" not in llm_progress_event
    assert heartbeat.agent_events == [{"type": "heartbeat", "turnId": "turn-public"}]
    retry_event = retry.agent_events[0]
    assert retry_event["type"] == "retry"
    assert retry_event["reason"] == "retry_scheduled"
    assert "retryNo" not in retry_event
    assert retry_event["toolUseId"].startswith("tool:")
    assert len(str(retry_event["toolName"])) == 240
    assert "sk-proj-secret" not in _render([retry_event])
    assert "/Users/kevin" not in _render([retry_event])
    fallback_event = fallback.agent_events[0]
    assert fallback_event["type"] == "runtime_trace"
    assert fallback_event["turnId"] == "turn-public"
    assert fallback_event["phase"] == "retry_scheduled"
    assert fallback_event["severity"] == "warning"
    assert fallback_event["reasonCode"] == "provider_fallback"
    assert len(str(fallback_event["detail"])) == 240
    assert "attempt" not in fallback_event
    assert end.agent_events == [
        {
            "type": "turn_end",
            "turnId": "turn-public",
            "status": "aborted",
            "reason": "aborted",
        }
    ]
    assert "very-secret" not in str(end.agent_events[0]["reason"])
    assert "usage" not in end.agent_events[0]
    assert unreceipted_committed.agent_events == [
        {
            "type": "turn_end",
            "turnId": "turn-public",
            "status": "aborted",
            "reason": "missing_runtime_receipt",
        }
    ]
    assert overflow_heartbeat.agent_events == [
        {"type": "heartbeat", "turnId": "turn-public"}
    ]
    assert overflow_llm_progress.agent_events == [
        {
            "type": "llm_progress",
            "turnId": "turn-public",
            "stage": "waiting",
        }
    ]
    assert overflow_committed.agent_events == [
        {
            "type": "turn_end",
            "turnId": "turn-public",
            "status": "committed",
            "stopReason": "end_turn",
            "receiptRef": "receipt:sha256:" + ("f" * 64),
        }
    ]
