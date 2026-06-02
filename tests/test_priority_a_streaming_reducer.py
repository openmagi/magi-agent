from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from openmagi_core_agent.runtime.streaming import reduce_streaming_events


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, tuple):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def test_default_off_reducer_records_no_authority_and_no_executed_writes() -> None:
    result = reduce_streaming_events(
        [
            {"type": "text_delta", "delta": "Hello "},
            {"type": "text_delta", "delta": "world."},
            {"type": "turn_end", "turnId": "turn-1", "status": "committed"},
            "[DONE]",
        ],
        turn_id="turn-1",
    )
    dumped = _dump(result)

    assert dumped["enabled"] is False
    assert dumped["defaultOff"] is True
    assert dumped["productionWritesAuthorized"] is False
    assert dumped["snapshotAuthority"] is False
    assert dumped["snapshot"] == {
        "turnId": "turn-1",
        "active": False,
        "text": "Hello world.",
        "status": "committed",
        "error": None,
        "final": True,
        "done": True,
        "progressEvents": [],
    }
    assert [intent["operation"] for intent in dumped["writeIntents"]] == [
        "snapshot_update",
        "transcript_assistant_text",
        "turn_end",
    ]
    for intent in dumped["writeIntents"]:
        assert intent["executed"] is False
        assert intent["enabled"] is False
        assert intent["defaultOff"] is True


def test_reducer_applies_response_clear_utf8_error_and_non_rendering_progress() -> None:
    result = reduce_streaming_events(
        [
            {"type": "text_delta", "delta": "draft"},
            {"type": "tool_start", "id": "tool-1", "name": "Search"},
            {"type": "tool_progress", "id": "tool-1", "message": "Working"},
            {"type": "response_clear", "turnId": "turn-utf8", "reason": "retry"},
            {"type": "text_delta", "delta": "안녕, stream 🌊"},
            {"type": "error", "message": "model failed"},
        ],
        turn_id="turn-utf8",
    )
    dumped = _dump(result)

    assert dumped["snapshot"]["text"] == "안녕, stream 🌊"
    assert dumped["snapshot"]["active"] is False
    assert dumped["snapshot"]["status"] == "error"
    assert dumped["snapshot"]["error"] == "model failed"
    assert [event["type"] for event in dumped["snapshot"]["progressEvents"]] == [
        "tool_start",
        "tool_progress",
    ]
    assert all(
        '"type":"tool_' not in chunk
        for chunk in dumped["renderedChunks"]
    )
    assert any("안녕, stream 🌊" in chunk for chunk in dumped["renderedChunks"])


def test_reducer_prevents_legacy_delta_duplicate_rendering_after_agent_text() -> None:
    result = reduce_streaming_events(
        [
            {"type": "text_delta", "delta": "Hello "},
            {"choices": [{"delta": {"content": "Hello "}}]},
            {"type": "text_delta", "delta": "world."},
            {"choices": [{"delta": {"content": "world."}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            "[DONE]",
        ],
        turn_id="turn-legacy",
    )
    dumped = _dump(result)

    assert dumped["snapshot"]["text"] == "Hello world."
    assert dumped["snapshot"]["done"] is True
    assert dumped["snapshot"]["final"] is True
    assert [chunk for chunk in dumped["renderedChunks"] if "Hello " in chunk] == [
        "Hello "
    ]
    assert [chunk for chunk in dumped["renderedChunks"] if "world." in chunk] == [
        "world."
    ]
    assert dumped["suppressedLegacyDeltas"] == ["Hello ", "world."]


def test_reducer_prevents_legacy_first_duplicates_and_resets_after_clear() -> None:
    duplicate = reduce_streaming_events(
        [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"type": "text_delta", "delta": "Hello"},
            "[DONE]",
        ],
        turn_id="turn-legacy-first",
    )
    cleared = reduce_streaming_events(
        [
            {"type": "text_delta", "delta": "draft"},
            {"type": "response_clear"},
            {"choices": [{"delta": {"content": "replacement"}}]},
            "[DONE]",
        ],
        turn_id="turn-clear",
    )

    duplicate_dump = _dump(duplicate)
    cleared_dump = _dump(cleared)

    assert duplicate_dump["snapshot"]["text"] == "Hello"
    assert duplicate_dump["renderedChunks"] == ["Hello"]
    assert duplicate_dump["suppressedLegacyDeltas"] == ["Hello"]
    assert cleared_dump["snapshot"]["text"] == "replacement"
    assert cleared_dump["renderedChunks"] == ["replacement"]


def test_reducer_prevents_multichunk_legacy_first_duplicate_replay() -> None:
    result = reduce_streaming_events(
        [
            {"choices": [{"delta": {"content": "Hello "}}]},
            {"choices": [{"delta": {"content": "world"}}]},
            {"type": "text_delta", "delta": "Hello "},
            {"type": "text_delta", "delta": "world"},
            "[DONE]",
        ],
        turn_id="turn-legacy-multi",
    )
    dumped = _dump(result)

    assert dumped["snapshot"]["text"] == "Hello world"
    assert dumped["renderedChunks"] == ["Hello ", "world"]
    assert dumped["suppressedLegacyDeltas"] == ["Hello ", "world"]


def test_reducer_redacts_private_text_and_does_not_emit_raw_paths_or_secrets() -> None:
    result = reduce_streaming_events(
        [
            {
                "type": "text_delta",
                "delta": "read /workspace/private with Authorization: Bearer live-token",
            },
            {
                "type": "runtime_trace",
                "message": "cookie sid=opaque from /data/bots/bot-1/workspace",
            },
            {
                "type": "error",
                "message": "failed on /Users/kevin/private sk-live-secret",
            },
        ],
        turn_id="turn-redact",
    )
    dumped = _dump(result)
    serialized = str(dumped)

    for forbidden in (
        "/workspace/private",
        "/data/bots",
        "/Users/kevin",
        "Authorization",
        "Bearer live-token",
        "sid=opaque",
        "sk-live-secret",
    ):
        assert forbidden not in serialized
    assert "[redacted" in serialized
    assert dumped["snapshot"]["status"] == "error"
    assert dumped["productionWritesAuthorized"] is False


def test_reducer_redacts_hidden_reasoning_tool_logs_and_child_outputs() -> None:
    result = reduce_streaming_events(
        [
            {
                "type": "text_delta",
                "delta": (
                    "Visible answer. hidden reasoning: private chain "
                    "hidden_reasoning github_pat_unsafeToken12345"
                ),
            },
            {
                "type": "runtime_trace",
                "message": (
                    "raw_tool_log stdout contained raw child output "
                    "xoxb-unsafeToken12345 AKIAUNSAFEKEY12345"
                ),
            },
            {
                "type": "child_progress",
                "summary": (
                    "chain of thought and raw child transcript should not flow "
                    "AIzaUnsafeGoogleToken12345"
                ),
            },
        ],
        turn_id="turn-hidden",
    )
    dumped = _dump(result)
    serialized = str(dumped).casefold()

    for forbidden in (
        "hidden reasoning",
        "chain of thought",
        "raw_tool_log",
        "raw child output",
        "raw child transcript",
        "github_pat_unsafe",
        "xoxb-unsafe",
        "AKIAUNSAFE",
        "AIzaUnsafe",
    ):
        assert forbidden not in serialized
    assert "[redacted" in serialized
    assert dumped["productionWritesAuthorized"] is False


def test_reducer_redacts_private_progress_event_keys() -> None:
    result = reduce_streaming_events(
        [
            {
                "type": "runtime_trace",
                "Authorization: Bearer unsafe-token": "value",
                "/Users/kevin/private-key": "value",
                "safeKey": "safe",
            },
        ],
        turn_id="turn-key-redact",
    )
    dumped = _dump(result)
    serialized = str(dumped)

    assert "safeKey" in serialized
    assert "Authorization" not in serialized
    assert "Bearer unsafe-token" not in serialized
    assert "/Users/kevin" not in serialized


def test_reducer_redacts_event_turn_id_before_snapshot_and_write_intents() -> None:
    result = reduce_streaming_events(
        [
            {
                "type": "text_delta",
                "turnId": "Authorization: Bearer leak-token-123",
                "delta": "safe",
            },
        ],
    )
    dumped = _dump(result)
    serialized = str(dumped)

    assert dumped["snapshot"]["turnId"].startswith("turn:")
    assert "Authorization" not in serialized
    assert "leak-token" not in serialized


def test_reducer_redacts_explicit_turn_id_before_snapshot_and_write_intents() -> None:
    result = reduce_streaming_events(
        [{"type": "text_delta", "delta": "safe"}],
        turn_id="Authorization: Bearer explicit-leak-token",
    )
    dumped = _dump(result)
    serialized = str(dumped)

    assert dumped["snapshot"]["turnId"].startswith("turn:")
    assert "Authorization" not in serialized
    assert "explicit-leak-token" not in serialized


def test_reducer_redacts_generic_credential_turn_ids_and_progress_keys() -> None:
    event_derived = reduce_streaming_events(
        [
            {
                "type": "runtime_trace",
                "turnId": "api_key:supersecret123",
                "api_key": "supersecret123",
                "secretToken": "unsafe",
                "safeKey": "safe",
            },
        ],
    )
    explicit = reduce_streaming_events(
        [{"type": "text_delta", "delta": "safe"}],
        turn_id="api_key:supersecret123",
    )
    serialized = f"{_dump(event_derived)} {_dump(explicit)}"

    assert "safeKey" in serialized
    assert "api_key" not in serialized
    assert "secretToken" not in serialized
    assert "supersecret" not in serialized


def test_reducer_redacts_home_and_kubelet_paths_from_snapshot_and_write_intents() -> None:
    result = reduce_streaming_events(
        [
            {
                "type": "text_delta",
                "delta": "read /home/kevin/.ssh/id_rsa and /var/lib/kubelet/pods/x/token",
            },
            {
                "type": "runtime_trace",
                "message": "trace /home/kevin/.config/secret and /var/lib/kubelet/pods/y",
            },
        ],
        turn_id="turn-home-path",
    )
    serialized = str(_dump(result))

    assert "/home/kevin" not in serialized
    assert "/var/lib/kubelet" not in serialized
    assert "id_rsa" not in serialized
    assert "[redacted" in serialized
