import json
import math

from google.adk.events import Event, EventActions
from google.genai import types

from magi_agent.adk_bridge.event_adapter import OpenMagiEventBridge


def text_event(text: str, *, partial: bool, turn_complete: bool = False) -> Event:
    return Event(
        author="model",
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        partial=partial,
        turn_complete=turn_complete,
        invocation_id="turn-1",
    )


def test_event_bridge_projects_function_call_to_tool_start_and_transcript() -> None:
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
    assert projection.transcript_entries[0].input == {"query": "adk migration", "limit": 3}
    assert projection.legacy_deltas == []


def test_event_bridge_sanitizes_function_call_public_input_preview_only() -> None:
    bridge = OpenMagiEventBridge()
    long_secret = "x" * 450
    event = Event(
        id="event-secret-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-secret-call",
                        name="FetchPrivateData",
                        args={
                            "authorization": "Bearer abc.DEF_123~+/=-",
                            "github": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
                            "openai": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
                            "api_key": "api-key-secret-value",
                            "token": "token-secret-value",
                            "secret": "named-secret-value",
                            "password": "password-secret-value",
                            "payload": long_secret,
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    preview = projection.agent_events[0]["input_preview"]
    assert isinstance(preview, str)
    assert len(preview) == 400
    assert preview.endswith("...")
    assert '"authorization": "[redacted]"' in preview
    assert "Bearer abc.DEF_123~+/=-" not in preview
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in preview
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789" not in preview
    assert "api-key-secret-value" not in preview
    assert "token-secret-value" not in preview
    assert "named-secret-value" not in preview
    assert "password-secret-value" not in preview
    assert projection.transcript_entries[0].input["authorization"] == "Bearer abc.DEF_123~+/=-"
    assert projection.transcript_entries[0].input["github"].startswith("ghp_")
    assert projection.transcript_entries[0].input["openai"].startswith("sk-proj-")
    assert projection.transcript_entries[0].input["api_key"] == "api-key-secret-value"


def test_event_bridge_digests_private_tool_preview_key_classes() -> None:
    bridge = OpenMagiEventBridge()
    call_event = Event(
        id="event-private-preview-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-private-preview-call",
                        name="PrivatePreview",
                        args={
                            "childPrompt": "child private prompt",
                            "payload": json.dumps(
                                {
                                    "childPrompt": "stringified child prompt",
                                    "summary": "safe stringified call summary",
                                },
                                sort_keys=True,
                            ),
                            "rawArgs": {"query": "raw private args"},
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-preview",
    )
    second_call_event = Event(
        id="event-private-preview-second-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-private-preview-second-call",
                        name="PrivatePreview",
                        args={
                            "hiddenReasoning": "private chain of thought",
                            "privateMemory": "private memory body",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-preview",
    )
    response_event = Event(
        id="event-private-preview-response",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-private-preview-response",
                        name="PrivatePreview",
                        response={
                            "childOutput": "child private response",
                            "payload": json.dumps(
                                {
                                    "rawOutput": "stringified raw result",
                                    "summary": "safe stringified result summary",
                                },
                                sort_keys=True,
                            ),
                            "privateMemory": "private memory response",
                            "rawOutput": "raw private result",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-private-preview",
    )

    call_projection = bridge.project_adk_event(call_event, turn_id="turn-private-preview")
    second_call_projection = bridge.project_adk_event(
        second_call_event,
        turn_id="turn-private-preview",
    )
    response_projection = bridge.project_adk_event(
        response_event,
        turn_id="turn-private-preview",
    )
    rendered = json.dumps(
        [
            *call_projection.agent_events,
            *second_call_projection.agent_events,
            *response_projection.agent_events,
        ],
        sort_keys=True,
    )

    for leaked in (
        "child private prompt",
        "private chain of thought",
        "private memory body",
        "raw private args",
        "stringified child prompt",
        "child private response",
        "private memory response",
        "raw private result",
        "stringified raw result",
    ):
        assert leaked not in rendered
    for digest_key in (
        "childPromptDigest",
        "childOutputDigest",
        "hiddenReasoningDigest",
        "privateMemoryDigest",
        "rawArgsDigest",
        "rawOutputDigest",
    ):
        assert digest_key in rendered
    assert "safe stringified call summary" in rendered
    assert "safe stringified result summary" in rendered


def test_event_bridge_coerces_non_finite_numbers_in_public_tool_previews() -> None:
    bridge = OpenMagiEventBridge()
    call_event = Event(
        id="event-non-finite-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-non-finite-call",
                        name="Score",
                        args={"high": math.inf, "low": -math.inf, "score": math.nan},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )
    response_event = Event(
        id="event-non-finite-response",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-non-finite-response",
                        name="Score",
                        response={"high": math.inf, "low": -math.inf, "score": math.nan},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    call_projection = bridge.project_adk_event(call_event, turn_id="turn-1")
    response_projection = bridge.project_adk_event(response_event, turn_id="turn-1")

    assert call_projection.agent_events[0]["input_preview"] == (
        '{"high": null, "low": null, "score": null}'
    )
    assert response_projection.agent_events[0]["output_preview"] == (
        '{"high": null, "low": null, "score": null}'
    )
    assert response_projection.transcript_entries[0].output == (
        '{"high": null, "low": null, "score": null}'
    )


def test_event_bridge_redacts_quoted_secret_values_with_spaces_and_escapes() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-secret-call-edge",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-secret-call-edge",
                        name="FetchPrivateData",
                        args={
                            "password": "alpha beta gamma",
                            "secret": 'alpha "beta" gamma',
                            "github_oauth": "gho_abcdefghijklmnopqrstuvwxyz0123456789",
                            "github_user": "ghu_abcdefghijklmnopqrstuvwxyz0123456789",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    preview = projection.agent_events[0]["input_preview"]
    assert isinstance(preview, str)
    assert "alpha beta gamma" not in preview
    assert 'alpha \\"beta\\" gamma' not in preview
    assert "beta gamma" not in preview
    assert '\\"beta\\" gamma' not in preview
    assert "gho_abcdefghijklmnopqrstuvwxyz0123456789" not in preview
    assert "ghu_abcdefghijklmnopqrstuvwxyz0123456789" not in preview
    assert preview.count("[redacted]") >= 4
    assert projection.transcript_entries[0].input["password"] == "alpha beta gamma"
    assert projection.transcript_entries[0].input["secret"] == 'alpha "beta" gamma'


def test_event_bridge_projects_function_call_without_id_to_stable_fallback() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-1",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="Search",
                        args={"query": "adk migration"},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    first_projection = bridge.project_adk_event(event, turn_id="turn-1")
    second_projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert first_projection.agent_events[0]["id"]
    assert first_projection.agent_events[0]["id"] == second_projection.agent_events[0]["id"]
    assert (
        first_projection.transcript_entries[0].tool_use_id
        == first_projection.agent_events[0]["id"]
    )


def test_event_bridge_preserves_partial_text_delta_in_mixed_function_call_event() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-mixed-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Searching now. "),
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-mixed-call",
                        name="Search",
                        args={"query": "runtime parity"},
                    )
                ),
            ],
        ),
        partial=True,
        invocation_id="turn-mixed-call",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-mixed-call")

    assert projection.agent_events == [
        {"type": "text_delta", "delta": "Searching now. "},
        {
            "type": "tool_start",
            "id": "tool-mixed-call",
            "name": "Search",
            "input_preview": '{"query": "runtime parity"}',
        },
    ]
    assert projection.legacy_deltas == ["Searching now. "]
    assert [entry.kind for entry in projection.transcript_entries] == ["tool_call"]


def test_event_bridge_preserves_non_partial_text_delta_in_mixed_function_call_event() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(
        id="event-mixed-call-non-partial",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text=(
                        "Searching now with Authorization: Bearer nonpartial.SECRET "
                        "path=/workspace/private/search.txt. "
                    )
                ),
                types.Part(
                    function_call=types.FunctionCall(
                        id="tool-mixed-call-non-partial",
                        name="Search",
                        args={"query": "runtime parity"},
                    )
                ),
            ],
        ),
        invocation_id="turn-mixed-call-non-partial",
    )

    assert event.partial is None

    projection = bridge.project_adk_event(event, turn_id="turn-mixed-call-non-partial")

    assert projection.agent_events == [
        {
            "type": "text_delta",
            "delta": (
                "Searching now with Authorization: Bearer [redacted] "
                "path=[redacted-path] "
            ),
        },
        {
            "type": "tool_start",
            "id": "tool-mixed-call-non-partial",
            "name": "Search",
            "input_preview": '{"query": "runtime parity"}', "eventId": "event-mixed-call-non-partial:tool-start-1", "inputDigest": "sha256:83a909b07ae517f3c5cb7a8e8d406819063ad034ed47c9e2141ecb9418a624bb",
        },
    ]
    assert projection.legacy_deltas == []
    assert [entry.kind for entry in projection.transcript_entries] == ["tool_call"]


def test_event_bridge_marks_no_id_tool_response_unmatched_across_events() -> None:
    bridge = OpenMagiEventBridge()
    call_event = Event(
        id="event-call-uuid",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="Search",
                        args={"query": "adk migration"},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )
    response_event = Event(
        id="event-response-uuid",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="Search",
                        response={"results": ["alpha", "beta"]},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    call_projection = bridge.project_adk_event(call_event, turn_id="turn-1")
    response_projection = bridge.project_adk_event(response_event, turn_id="turn-1")

    call_id = call_projection.agent_events[0]["id"]
    response_id = response_projection.agent_events[0]["id"]
    assert call_id.startswith("adk-tool-call-")
    assert response_id.startswith("adk-tool-response-")
    assert call_id != response_id
    assert call_projection.transcript_entries[0].tool_use_id == call_id
    assert response_projection.transcript_entries[0].tool_use_id == response_id


def test_event_bridge_no_id_replay_is_stable_and_repeated_calls_do_not_collide() -> None:
    bridge = OpenMagiEventBridge()
    first = Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(function_call=types.FunctionCall(name="Search", args={"query": "a"}))
            ],
        ),
        invocation_id="turn-replay",
    )
    second = Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(function_call=types.FunctionCall(name="Search", args={"query": "b"}))
            ],
        ),
        invocation_id="turn-replay",
    )

    first_projection = bridge.project_adk_event(first, turn_id="turn-replay")
    first_replay = bridge.project_adk_event(first, turn_id="turn-replay")
    second_projection = bridge.project_adk_event(second, turn_id="turn-replay")

    assert first_projection.agent_events[0]["id"] == first_replay.agent_events[0]["id"]
    assert (
        first_projection.normalized_events[0].event_id
        == first_replay.normalized_events[0].event_id
    )
    assert first_projection.agent_events[0]["id"] != second_projection.agent_events[0]["id"]


def test_event_bridge_projects_function_response_to_tool_end_and_transcript() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-2",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-1",
                        name="Search",
                        response={"results": ["alpha", "beta"]},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert projection.agent_events == [
        {
            "type": "tool_end",
            "id": "tool-1",
            "status": "ok",
            "output_preview": '{"results": ["alpha", "beta"]}',
            "durationMs": 0,
        }
    ]
    assert projection.transcript_entries[0].kind == "tool_result"
    assert projection.transcript_entries[0].tool_use_id == "tool-1"
    assert projection.transcript_entries[0].status == "ok"
    assert projection.transcript_entries[0].output == '{"results": ["alpha", "beta"]}'
    assert projection.transcript_entries[0].is_error is False
    assert projection.legacy_deltas == []


def test_event_bridge_preserves_final_text_transcript_in_mixed_function_response_event() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-mixed-response",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-mixed-response",
                        name="Search",
                        response={"results": ["alpha"]},
                    )
                ),
                types.Part(text="Found one result."),
            ],
        ),
        partial=False,
        turn_complete=True,
        invocation_id="turn-mixed-response",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-mixed-response")

    assert projection.agent_events == [
        {
            "type": "tool_end",
            "id": "tool-mixed-response",
            "status": "ok",
            "output_preview": '{"results": ["alpha"]}',
            "durationMs": 0,
        },
        {
            "type": "text_delta",
            "delta": "Found one result.",
        },
    ]
    assert [entry.kind for entry in projection.transcript_entries] == [
        "tool_result",
        "assistant_text",
    ]
    assert projection.transcript_entries[0].tool_use_id == "tool-mixed-response"
    assert projection.transcript_entries[1].text == "Found one result."
    assert projection.legacy_deltas == []


def test_event_bridge_preserves_non_final_text_delta_in_mixed_function_response_event() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(
        id="event-mixed-response-non-final",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-mixed-response-non-final",
                        name="Search",
                        response={"results": ["alpha"]},
                    )
                ),
                types.Part(
                    text=(
                        "Found one result with token=ghp_nonfinalsecret "
                        "path=/data/bots/bot/private/result.txt."
                    )
                ),
            ],
        ),
        invocation_id="turn-mixed-response-non-final",
    )

    assert event.partial is None

    projection = bridge.project_adk_event(event, turn_id="turn-mixed-response-non-final")

    assert projection.agent_events == [
        {
            "type": "tool_end",
            "id": "tool-mixed-response-non-final",
            "status": "ok",
            "output_preview": '{"results": ["alpha"]}',
            "durationMs": 0, "eventId": "event-mixed-response-non-final:tool-end-0", "outputDigest": "sha256:463caa0802abac5d085326333a2e64baf3f285a4cbd1e0d94e5c8d88fbe26b73",
        },
        {
            "type": "text_delta",
            "delta": "Found one result with token=[redacted] path=[redacted-path]",
        },
    ]
    assert projection.legacy_deltas == []
    assert [entry.kind for entry in projection.transcript_entries] == ["tool_result"]


def test_event_bridge_sanitizes_function_response_public_output_preview_only() -> None:
    bridge = OpenMagiEventBridge()
    long_secret = "y" * 450
    event = Event(
        id="event-secret-response",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-secret-response",
                        name="FetchPrivateData",
                        response={
                            "authorization": "Bearer response.SECRET_123",
                            "github": "ghs_abcdefghijklmnopqrstuvwxyz0123456789",
                            "openai": "sk-response-secret",
                            "api_key": "response-api-key-secret",
                            "token": "response-token-secret",
                            "secret": "response-named-secret",
                            "password": "response-password-secret",
                            "payload": long_secret,
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    preview = projection.agent_events[0]["output_preview"]
    assert isinstance(preview, str)
    assert len(preview) == 400
    assert preview.endswith("...")
    assert '"authorization": "[redacted]"' in preview
    assert "Bearer response.SECRET_123" not in preview
    assert "ghs_abcdefghijklmnopqrstuvwxyz0123456789" not in preview
    assert "sk-response-secret" not in preview
    assert "response-api-key-secret" not in preview
    assert "response-token-secret" not in preview
    assert "response-named-secret" not in preview
    assert "response-password-secret" not in preview
    transcript_output = projection.transcript_entries[0].output
    assert transcript_output is not None
    assert "Bearer response.SECRET_123" in transcript_output
    assert "ghs_abcdefghijklmnopqrstuvwxyz0123456789" in transcript_output
    assert "sk-response-secret" in transcript_output
    assert "response-api-key-secret" in transcript_output
    assert len(transcript_output) > 400


def test_event_bridge_redacts_quoted_secret_response_values_with_spaces_and_escapes() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-secret-response-edge",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-secret-response-edge",
                        name="FetchPrivateData",
                        response={
                            "password": "delta epsilon zeta",
                            "token": 'delta "epsilon" zeta',
                            "github_oauth": "gho_zyxwvutsrqponmlkjihgfedcba9876543210",
                            "github_user": "ghu_zyxwvutsrqponmlkjihgfedcba9876543210",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    preview = projection.agent_events[0]["output_preview"]
    assert isinstance(preview, str)
    assert "delta epsilon zeta" not in preview
    assert 'delta \\"epsilon\\" zeta' not in preview
    assert "epsilon zeta" not in preview
    assert '\\"epsilon\\" zeta' not in preview
    assert "gho_zyxwvutsrqponmlkjihgfedcba9876543210" not in preview
    assert "ghu_zyxwvutsrqponmlkjihgfedcba9876543210" not in preview
    assert preview.count("[redacted]") >= 4
    transcript_output = projection.transcript_entries[0].output
    assert transcript_output is not None
    assert "delta epsilon zeta" in transcript_output
    assert 'delta \\"epsilon\\" zeta' in transcript_output


def test_event_bridge_marks_error_function_response_as_error() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-3",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-2",
                        name="Search",
                        response={"isError": True, "error": "permission denied"},
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert projection.agent_events[0]["status"] == "error"
    assert projection.transcript_entries[0].status == "error"
    assert projection.transcript_entries[0].is_error is True


def test_event_bridge_marks_blocked_tool_result_response_as_error() -> None:
    bridge = OpenMagiEventBridge()
    event = Event(
        id="event-blocked-tool-result",
        author="tool",
        content=types.Content(
            role="tool",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="tool-blocked",
                        name="SpawnAgent",
                        response={
                            "status": "blocked",
                            "output": {
                                "status": "blocked",
                                "liveChildRunnerAttached": False,
                                "summary": "child_provider_key_missing",
                            },
                            "errorCode": "child_provider_key_missing",
                            "errorMessage": "child_provider_key_missing",
                        },
                    )
                )
            ],
        ),
        invocation_id="turn-1",
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert projection.agent_events[0]["status"] == "error"
    assert projection.transcript_entries[0].status == "error"
    assert projection.transcript_entries[0].is_error is True
    assert projection.normalized_events[0].type == "tool.call.failed"


def test_event_bridge_projects_partial_text_to_agent_and_legacy_deltas() -> None:
    bridge = OpenMagiEventBridge()

    projection = bridge.project_adk_event(text_event("hi", partial=True), turn_id="turn-1")

    assert projection.agent_events == [{"type": "text_delta", "delta": "hi"}]
    assert projection.legacy_deltas == ["hi"]
    assert projection.transcript_entries == []


def test_event_bridge_projects_final_text_to_transcript_entry() -> None:
    bridge = OpenMagiEventBridge()

    projection = bridge.project_adk_event(
        text_event("final", partial=False, turn_complete=True),
        turn_id="turn-1",
    )

    assert projection.transcript_entries[0].kind == "assistant_text"
    assert projection.transcript_entries[0].text == "final"


def test_event_bridge_projects_final_text_to_public_delta_before_turn_end() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    final_text = (
        "final transcript text with Authorization: Bearer final.SECRET "
        "path=/workspace/private/final.txt"
    )

    projection = bridge.project_adk_event(
        text_event(final_text, partial=False, turn_complete=True),
        turn_id="turn-final",
    )

    assert len(projection.agent_events) == 2
    text_delta = projection.agent_events[0]
    assert text_delta == {
        "type": "text_delta",
        "delta": "final transcript text with Authorization: Bearer [redacted] path=[redacted-path]",
    }
    turn_end = projection.agent_events[1]
    assert turn_end == {
        "type": "turn_end",
        "turnId": "turn-final",
        "status": "aborted",
        "reason": "missing_runtime_receipt",
    }
    assert projection.legacy_deltas == []
    rendered_agent_events = json.dumps(projection.agent_events)
    assert "final.SECRET" not in rendered_agent_events
    assert "/workspace/private/final.txt" not in rendered_agent_events
    assert projection.transcript_entries[0].kind == "assistant_text"
    assert projection.transcript_entries[0].text == (
        "final transcript text with Authorization: Bearer [redacted] path=[redacted-path]"
    )


def test_event_bridge_live_compatible_final_empty_events_emit_turn_end() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    events = [
        Event(
            id="event-final-empty-content",
            author="model",
            content=types.Content(role="model", parts=[types.Part(text="")]),
            partial=False,
            turn_complete=True,
            invocation_id="turn-final-empty-content",
        ),
        Event(
            id="event-final-no-content",
            author="model",
            partial=False,
            turn_complete=True,
            invocation_id="turn-final-no-content",
        ),
    ]

    for event in events:
        projection = bridge.project_adk_event(event, turn_id=event.invocation_id)

        assert len(projection.agent_events) == 1
        turn_end = projection.agent_events[0]
        assert turn_end == {
            "type": "turn_end",
            "turnId": event.invocation_id,
            "status": "aborted",
            "reason": "missing_runtime_receipt",
        }
        assert projection.legacy_deltas == []
        assert projection.transcript_entries == []


def test_event_bridge_projects_adk_final_text_without_turn_complete_to_transcript() -> None:
    bridge = OpenMagiEventBridge()
    event = text_event("official final", partial=False)

    assert event.is_final_response() is True

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert projection.transcript_entries[0].kind == "assistant_text"
    assert projection.transcript_entries[0].text == "official final"


def test_event_bridge_projects_error_to_abort_and_runtime_events() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(author="model", invocation_id="turn-1", error_code="bad", error_message="failed")

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    assert [agent_event["type"] for agent_event in projection.agent_events] == [
        "runtime_trace",
        "error",
        "turn_end",
    ]
    assert projection.agent_events[1] == {"type": "error", "code": "bad", "message": "failed"}
    assert projection.agent_events[2] == {
        "type": "turn_end",
        "turnId": "turn-1",
        "status": "aborted",
        "reason": "failed",
    }
    assert projection.transcript_entries[0].kind == "turn_aborted"
    assert projection.transcript_entries[0].reason == "failed"


def test_event_bridge_redacts_error_public_events_without_mutating_transcript_reason() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    message = (
        "ADK failed with STRIPE_SECRET_KEY=stripe-live-secret "
        "SUPABASE_SERVICE_ROLE_KEY=supabase-service-role "
        "ANTHROPIC_API_KEY=anthropic-live-secret "
        "refresh_token=refresh-token-value, "
        + ("x" * 500)
    )
    event = Event(
        author="model",
        invocation_id="turn-1",
        error_code="bad",
        error_message=message,
    )

    projection = bridge.project_adk_event(event, turn_id="turn-1")

    runtime_trace = projection.agent_events[0]
    error = projection.agent_events[1]
    assert runtime_trace["type"] == "runtime_trace"
    assert error["type"] == "error"
    assert isinstance(runtime_trace["detail"], str)
    assert isinstance(error["message"], str)
    assert len(runtime_trace["detail"]) == 400
    assert len(error["message"]) == 400
    assert runtime_trace["detail"].endswith("...")
    assert error["message"].endswith("...")
    for leaked in (
        "stripe-live-secret",
        "supabase-service-role",
        "anthropic-live-secret",
        "refresh-token-value",
    ):
        assert leaked not in runtime_trace["detail"]
        assert leaked not in error["message"]
        assert leaked in projection.transcript_entries[0].reason

    turn_end = projection.agent_events[2]
    assert turn_end["type"] == "turn_end"
    assert turn_end["status"] == "aborted"
    assert isinstance(turn_end["reason"], str)
    assert len(turn_end["reason"]) == 400
    assert turn_end["reason"].endswith("...")
    for leaked in (
        "stripe-live-secret",
        "supabase-service-role",
        "anthropic-live-secret",
        "refresh-token-value",
    ):
        assert leaked not in turn_end["reason"]


def test_event_bridge_public_turn_ids_digest_sensitive_shapes() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    sensitive_turn_id = "turn-sessionKey-auth-cookie-raw"
    error_projection = bridge.project_adk_event(
        Event(
            author="model",
            invocation_id=sensitive_turn_id,
            error_code="bad",
            error_message="failed",
        ),
        turn_id=sensitive_turn_id,
    )
    final_projection = bridge.project_adk_event(
        Event(
            id="event-sensitive-turn-final",
            author="model",
            content=types.Content(role="model", parts=[types.Part(text="done")]),
            partial=False,
            turn_complete=True,
            invocation_id=sensitive_turn_id,
        ),
        turn_id=sensitive_turn_id,
    )
    clear_projection = bridge.project_adk_event(
        Event(
            id="event-sensitive-turn-clear",
            author="model",
            partial=True,
            invocation_id=sensitive_turn_id,
            actions=EventActions(rewind_before_invocation_id="before"),
        ),
        turn_id=sensitive_turn_id,
    )

    public_events = [
        *error_projection.agent_events,
        *final_projection.agent_events,
        *clear_projection.agent_events,
    ]
    rendered = json.dumps(public_events)

    assert sensitive_turn_id not in rendered
    for agent_event in public_events:
        if "turnId" in agent_event:
            assert str(agent_event["turnId"]).startswith("turn:")


def test_event_bridge_malformed_blank_public_refs_do_not_crash() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    blank_turn_id = "   "
    call_event = Event(
        id="event-blank-tool-call",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="   ",
                        name="Search",
                        args={"query": "safe"},
                    )
                )
            ],
        ),
        invocation_id=blank_turn_id,
    )
    clear_event = Event(
        id="event-blank-turn-clear",
        author="model",
        partial=True,
        invocation_id=blank_turn_id,
        actions=EventActions(rewind_before_invocation_id="before"),
    )
    error_event = Event(
        author="model",
        invocation_id=blank_turn_id,
        error_code="bad",
        error_message="failed",
    )

    first = bridge.project_adk_event(call_event, turn_id=blank_turn_id)
    second = bridge.project_adk_event(call_event, turn_id=blank_turn_id)
    clear_projection = bridge.project_adk_event(clear_event, turn_id=blank_turn_id)
    error_projection = bridge.project_adk_event(error_event, turn_id=blank_turn_id)

    assert first.agent_events[0]["id"].startswith("adk-tool-call-")
    assert first.agent_events[0]["id"] == second.agent_events[0]["id"]
    assert clear_projection.agent_events[0]["turnId"].startswith("turn:")
    assert error_projection.agent_events[0]["turnId"].startswith("turn:")
    assert error_projection.agent_events[2]["turnId"].startswith("turn:")


def test_event_bridge_live_compatible_partial_text_uses_agent_channel_only_after_clear() -> None:
    bridge = OpenMagiEventBridge(live_compatible=True)
    event = Event(
        id="event-clear-and-text",
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text=(
                        "안녕 Authorization: Bearer partial.SECRET "
                        "Set-Cookie: sid=unsafe; HttpOnly "
                        "token=ghp_partialsecret path=/data/bots/bot/private.txt"
                    )
                )
            ],
        ),
        partial=True,
        invocation_id="turn-clear",
        actions=EventActions(rewind_before_invocation_id="turn-clear-before-retry"),
        custom_metadata={
            "hiddenReasoning": "private chain of thought",
            "memoryProviderPayload": "private memory",
            "rawArguments": {"authorization": "Bearer raw-arg-secret"},
            "rawOutput": {"result": "raw tool output"},
            "childPrompt": "private child prompt",
            "childOutput": "private child output",
        },
    )

    projection = bridge.project_adk_event(event, turn_id="turn-clear")

    assert [agent_event["type"] for agent_event in projection.agent_events] == [
        "response_clear",
        "text_delta",
    ]
    assert projection.legacy_deltas == []
    assert projection.agent_events[0] == {
        "type": "response_clear",
        "turnId": "turn-clear",
        "reason": "adk_rewind",
    }
    delta = projection.agent_events[1]["delta"]
    assert isinstance(delta, str)
    assert "안녕" in delta
    unsafe_rendered = json.dumps(projection.agent_events, ensure_ascii=False)
    for leaked in (
        "partial.SECRET",
        "sid=unsafe",
        "ghp_partialsecret",
        "/data/bots/bot/private.txt",
        "private chain of thought",
        "private memory",
        "raw-arg-secret",
        "raw tool output",
        "private child prompt",
        "private child output",
    ):
        assert leaked not in unsafe_rendered


def test_event_bridge_drops_duplicate_aggregate_text_after_streaming_partials() -> None:
    """A non-partial aggregate that repeats already-streamed partial text (delivered
    alongside a trailing tool call) must NOT be re-emitted, or the client renders the
    segment twice (e.g. "…subagent.…subagent." right before a tool call)."""
    bridge = OpenMagiEventBridge(live_compatible=True)

    # 1) The model streams the text token-by-token as partial events.
    p1 = bridge.project_adk_event(text_event("Writing state and ", partial=True), turn_id="t1")
    p2 = bridge.project_adk_event(text_event("dispatching subagent.", partial=True), turn_id="t1")
    assert [e["delta"] for e in p1.agent_events] == ["Writing state and "]
    assert [e["delta"] for e in p2.agent_events] == ["dispatching subagent."]

    # 2) The aggregated NON-partial event repeats the full text + a tool call.
    mixed = Event(
        author="model",
        content=types.Content(
            role="model",
            parts=[
                types.Part(text="Writing state and dispatching subagent."),
                types.Part(
                    function_call=types.FunctionCall(id="tool-1", name="FileWrite", args={})
                ),
            ],
        ),
        invocation_id="t1",
    )
    projection = bridge.project_adk_event(mixed, turn_id="t1")

    # Only the tool_start survives — the duplicate text aggregate is dropped.
    assert [e["type"] for e in projection.agent_events] == ["tool_start"]
