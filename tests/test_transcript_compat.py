from pathlib import Path

from magi_agent.runtime.transcript import (
    AssistantTextEntry,
    CanonicalMessageEntry,
    ControlEventTranscriptEntry,
    ToolCallEntry,
    ToolResultEntry,
    TranscriptStore,
    TurnCommittedEntry,
    UserMessageEntry,
)


FIXTURES = Path(__file__).parent / "fixtures" / "transcript"


def copy_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.write_text((FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_read_committed_returns_committed_turn_entries(tmp_path: Path) -> None:
    store = TranscriptStore(file_path=copy_fixture(tmp_path, "committed_turn.jsonl"))

    entries = store.read_committed()

    assert [entry.kind for entry in entries] == [
        "turn_started",
        "user_message",
        "assistant_text",
        "turn_committed",
    ]


def test_read_committed_treats_aborted_turn_as_complete(tmp_path: Path) -> None:
    store = TranscriptStore(file_path=copy_fixture(tmp_path, "aborted_turn.jsonl"))

    entries = store.read_committed()

    assert [entry.kind for entry in entries] == ["turn_started", "user_message", "turn_aborted"]


def test_read_committed_ignores_uncommitted_crash_tail(tmp_path: Path) -> None:
    store = TranscriptStore(file_path=copy_fixture(tmp_path, "trailing_partial.jsonl"))

    entries = store.read_committed()

    assert [entry.turn_id for entry in entries] == ["turn-1", "turn-1", "turn-1"]


def test_read_committed_includes_control_and_canonical_tail(tmp_path: Path) -> None:
    store = TranscriptStore(file_path=copy_fixture(tmp_path, "control_tail.jsonl"))

    entries = store.read_committed()

    assert [entry.kind for entry in entries] == [
        "turn_started",
        "user_message",
        "turn_aborted",
        "control_event",
        "canonical_message",
    ]


def test_typescript_repair_fixture_tolerates_malformed_tail_and_structural_entries(
    tmp_path: Path,
) -> None:
    store = TranscriptStore(
        file_path=copy_fixture(tmp_path, "typescript_replay_repair_tail.jsonl")
    )

    entries = store.read_committed()

    assert [entry.kind for entry in entries] == [
        "turn_started",
        "user_message",
        "assistant_text",
        "turn_committed",
        "canonical_message",
        "compaction_boundary",
        "control_event",
    ]
    canonical = next(
        entry for entry in entries if isinstance(entry, CanonicalMessageEntry)
    )
    assert canonical.model_dump(by_alias=True)["content"] == [
        {
            "type": "thinking",
            "thinking": "redacted hidden reasoning fixture only",
            "signature": "sig-redacted",
        },
        {"type": "text", "text": "Visible answer preserved."},
    ]
    boundary = entries[-2].model_dump(by_alias=True)
    assert boundary["boundaryId"] == "compact-post-commit-1"
    control_ref = entries[-1]
    assert isinstance(control_ref, ControlEventTranscriptEntry)
    assert control_ref.seq == 3
    assert control_ref.event_id == "ctrl-timeout-1"
    assert control_ref.event_type == "control_request_timed_out"


def test_typescript_tool_pairing_fixture_records_duplicate_and_orphan_expectations(
    tmp_path: Path,
) -> None:
    store = TranscriptStore(
        file_path=copy_fixture(tmp_path, "typescript_tool_pairing_metadata.jsonl")
    )

    entries = store.read_all()

    tool_entries = [
        entry for entry in entries if isinstance(entry, ToolCallEntry | ToolResultEntry)
    ]
    assert [entry.kind for entry in tool_entries] == [
        "tool_call",
        "tool_result",
        "tool_result",
        "tool_result",
    ]
    first_result, duplicate_result, orphan_result = tool_entries[1:]
    assert isinstance(first_result, ToolResultEntry)
    assert first_result.metadata == {
        "pairing": "matched",
        "callEventId": "tool-call-1",
        "resultEventId": "tool-result-1",
    }
    assert isinstance(duplicate_result, ToolResultEntry)
    assert duplicate_result.metadata == {
        "pairing": "duplicate_result",
        "callEventId": "tool-call-1",
        "resultEventId": "tool-result-1-duplicate",
        "expectedRepair": "keep_first_result",
    }
    assert isinstance(orphan_result, ToolResultEntry)
    assert orphan_result.metadata == {
        "pairing": "orphan_result",
        "resultEventId": "tool-result-orphan-1",
        "expectedRepair": "project_unknown_call",
    }


def test_append_writes_one_json_object_per_line_with_ts_field_names(tmp_path: Path) -> None:
    store = TranscriptStore(file_path=tmp_path / "session.jsonl")

    store.append(UserMessageEntry(ts=1, turn_id="turn-1", text="hello"))
    store.append(AssistantTextEntry(ts=2, turn_id="turn-1", text="hi"))
    store.append(TurnCommittedEntry(ts=3, turn_id="turn-1", input_tokens=1, output_tokens=1))

    lines = store.file_path.read_text(encoding="utf-8").splitlines()
    assert lines == [
        '{"kind":"user_message","ts":1,"turnId":"turn-1","text":"hello"}',
        '{"kind":"assistant_text","ts":2,"turnId":"turn-1","text":"hi"}',
        '{"kind":"turn_committed","ts":3,"turnId":"turn-1","inputTokens":1,"outputTokens":1}',
    ]
