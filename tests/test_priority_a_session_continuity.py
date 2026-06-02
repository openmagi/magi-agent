from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from google.adk.events import Event
from google.genai import types

from openmagi_core_agent.adk_bridge.session_service import WorkspaceSessionService
from openmagi_core_agent.runtime.transcript import (
    AssistantTextEntry,
    CompactionBoundaryEntry,
    ControlEventTranscriptEntry,
    ToolCallEntry,
    ToolResultEntry,
    TranscriptEntry,
    TranscriptStore,
    TurnCommittedEntry,
    TurnStartedEntry,
    UserMessageEntry,
)

FIXTURES = Path(__file__).parent / "fixtures" / "transcript"


@dataclass
class _FakeTranscriptStore:
    entries: list[TranscriptEntry]
    read_committed_calls: int = 0
    read_all_calls: int = 0
    append_calls: int = 0

    def read_committed(self) -> list[TranscriptEntry]:
        self.read_committed_calls += 1
        return list(self.entries)

    def read_all(self) -> list[TranscriptEntry]:
        self.read_all_calls += 1
        raise AssertionError("session continuity must use read_committed() only")

    def append(self, entry: TranscriptEntry) -> None:
        self.append_calls += 1
        raise AssertionError("session continuity must not write production transcript")


def _run(coro):
    return asyncio.run(coro)


async def _session() -> tuple[WorkspaceSessionService, object]:
    service = WorkspaceSessionService(app_name="openmagi")
    session = await service.create_session(
        app_name="openmagi",
        user_id="user-1",
        session_id="agent:main:app:default",
    )
    return service, session


def _text(event: Event) -> str:
    assert event.content is not None
    assert event.content.parts
    assert event.content.parts[0].text is not None
    return event.content.parts[0].text


def _metadata(event: Event) -> dict[str, object]:
    assert isinstance(event.custom_metadata, dict)
    return event.custom_metadata


def _copy_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.write_text((FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8")
    return target


def test_disabled_default_does_not_read_transcript_or_mutate_session() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [UserMessageEntry(ts=1, turn_id="turn-1", text="hello")]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
        )
    )

    assert result.status == "skipped"
    assert result.reason == "disabled"
    assert result.enabled is False
    assert store.read_committed_calls == 0
    assert store.read_all_calls == 0
    assert store.append_calls == 0
    assert session.events == []


def test_multi_turn_continuity_imports_prior_turn_order_and_role_mapping() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            TurnStartedEntry(
                ts=1,
                turn_id="turn-1",
                declaredRoute="gpt-5.5:anthropic",
                routingMetadata={
                    "providerLabel": "anthropic",
                    "modelLabel": "claude-opus-4-7",
                    "credentialRef": "server-config-main",
                    "unsafe": "Bearer raw-token",
                },
            ),
            UserMessageEntry(ts=2, turn_id="turn-1", text="Plan the launch."),
            AssistantTextEntry(ts=3, turn_id="turn-1", text="Launch plan v1."),
            TurnCommittedEntry(ts=4, turn_id="turn-1", inputTokens=10, outputTokens=5),
            TurnStartedEntry(ts=5, turn_id="turn-2", declaredRoute="gpt-5.5:openai"),
            UserMessageEntry(ts=6, turn_id="turn-2", text="Continue."),
            AssistantTextEntry(ts=7, turn_id="turn-2", text="Continuing."),
            TurnCommittedEntry(ts=8, turn_id="turn-2", inputTokens=3, outputTokens=4),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.status == "imported"
    assert result.imported_event_count == 4
    assert [event.invocation_id for event in session.events] == [
        "turn-1",
        "turn-1",
        "turn-2",
        "turn-2",
    ]
    assert [event.author for event in session.events] == [
        "user",
        "model",
        "user",
        "model",
    ]
    assert [event.content.role for event in session.events if event.content] == [
        "user",
        "model",
        "user",
        "model",
    ]
    assert [_text(event) for event in session.events] == [
        "Plan the launch.",
        "Launch plan v1.",
        "Continue.",
        "Continuing.",
    ]
    assert _metadata(session.events[1])["openmagi.modelRouting"] == {
        "declaredRoute": "gpt-5.5:anthropic",
        "modelLabel": "claude-opus-4-7",
        "providerLabel": "anthropic",
        "credentialRefSource": "server_config",
    }
    assert "credentialRef" not in _metadata(session.events[1])["openmagi.modelRouting"]
    assert store.read_committed_calls == 1
    assert store.read_all_calls == 0


def test_repeated_continuity_import_is_idempotent_for_existing_adk_session() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-1", text="remember alpha"),
            AssistantTextEntry(ts=2, turn_id="turn-1", text="alpha noted"),
            TurnCommittedEntry(ts=3, turn_id="turn-1", inputTokens=2, outputTokens=2),
        ]
    )
    boundary = SessionContinuityBoundary()

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert first.imported_event_count == 2
    assert second.imported_event_count == 0
    assert second.diagnostics.deduplicated_import_count == 2
    assert "committed_history_deduplicated" in second.diagnostics.reason_codes
    assert [_text(event) for event in session.events] == [
        "remember alpha",
        "alpha noted",
    ]


def test_expanded_import_window_replaces_existing_continuity_context_in_order() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-1", text="one"),
            AssistantTextEntry(ts=2, turn_id="turn-1", text="two"),
            UserMessageEntry(ts=3, turn_id="turn-2", text="three"),
            AssistantTextEntry(ts=4, turn_id="turn-2", text="four"),
            TurnCommittedEntry(ts=5, turn_id="turn-2", inputTokens=2, outputTokens=2),
        ]
    )
    boundary = SessionContinuityBoundary()

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True, maxImportedEvents=2),
        )
    )
    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True, maxImportedEvents=4),
        )
    )

    assert first.imported_event_count == 2
    assert second.imported_event_count == 4
    assert second.diagnostics.deduplicated_import_count == 2
    assert second.diagnostics.replaced_import_count == 2
    assert "committed_history_replaced" in second.diagnostics.reason_codes
    assert [_text(event) for event in session.events] == ["one", "two", "three", "four"]


def test_memory_only_overlap_does_not_suppress_new_committed_transcript() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        MemoryRecallProjection,
        SessionContinuityBoundary,
        SessionContinuityConfig,
        SessionContinuityPolicy,
    )

    service, session = _run(_session())
    memory_policy = SessionContinuityPolicy(
        memoryMode="read_only",
        recallProjection=MemoryRecallProjection(
            allowed=True,
            refs=("memory-ref://daily-2026-05-19",),
        ),
    )
    boundary = SessionContinuityBoundary()

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=_FakeTranscriptStore([]),
            config=SessionContinuityConfig(enabled=True),
            policy=memory_policy,
        )
    )
    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=_FakeTranscriptStore(
                [
                    UserMessageEntry(ts=1, turn_id="turn-1", text="new topic"),
                    AssistantTextEntry(ts=2, turn_id="turn-1", text="new answer"),
                    TurnCommittedEntry(
                        ts=3,
                        turn_id="turn-1",
                        inputTokens=2,
                        outputTokens=2,
                    ),
                ]
            ),
            config=SessionContinuityConfig(enabled=True),
            policy=memory_policy,
        )
    )

    assert first.imported_event_count == 1
    assert second.imported_event_count == 3
    assert second.diagnostics.deduplicated_import_count == 1
    assert second.diagnostics.replaced_import_count == 1
    assert [_text(event) for event in session.events] == ["new topic", "new answer", ""]
    assert _metadata(session.events[-1])["openmagi.memoryRecall"] == {
        "mode": "read_only",
        "refs": ["memory-ref://daily-2026-05-19"],
    }


def test_late_compaction_replaces_previously_imported_raw_pre_boundary_context() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    boundary = SessionContinuityBoundary()
    initial_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=3, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=4, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=5, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    compacted_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-late",
                summaryHash="sha256:late-summary",
                summaryText="Approved late compact summary.",
                approved=True,
                summaryRef="summary://late-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=initial_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=compacted_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert first.imported_event_count == 4
    assert second.imported_event_count == 3
    assert second.diagnostics.deduplicated_import_count == 2
    assert second.diagnostics.replaced_import_count == 4
    assert second.compaction_applied is True
    assert [_text(event) for event in session.events] == [
        "Approved late compact summary.",
        "Post-boundary question",
        "Post-boundary answer",
    ]
    dumped = str([event.model_dump() for event in session.events])
    assert "Raw pre-boundary context" not in dumped
    assert "Raw pre-boundary answer" not in dumped


def test_safe_ref_compaction_boundary_does_not_import_unsafe_summary_text() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            CompactionBoundaryEntry(
                ts=2,
                turn_id="turn-compact",
                boundaryId="compact-safe-ref",
                summaryHash="sha256:safe-ref",
                summaryText="Bearer raw-token must not be model visible",
                approved=True,
                summaryRef="summary://safe-ref",
            ),
            UserMessageEntry(ts=3, turn_id="turn-new", text="Post-boundary question"),
            TurnCommittedEntry(ts=4, turn_id="turn-new", inputTokens=1, outputTokens=1),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.compaction_applied is True
    assert _text(session.events[0]) == ""
    assert _metadata(session.events[0])["openmagi.compaction"] == {
        "boundaryId": "compact-safe-ref",
        "summaryHash": "sha256:safe-ref",
        "summaryRef": "summary://safe-ref",
    }
    dumped = str([event.model_dump() for event in session.events])
    assert "Bearer raw-token" not in dumped
    assert "Raw pre-boundary context" not in dumped


def test_stale_raw_transcript_does_not_downgrade_compacted_session_context() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    boundary = SessionContinuityBoundary()
    compacted_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-safe",
                summaryHash="sha256:safe-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://safe-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    stale_raw_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=compacted_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=stale_raw_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert first.compaction_applied is True
    assert second.imported_event_count == 0
    assert second.diagnostics.deduplicated_import_count == 2
    assert second.diagnostics.out_of_order_import_skipped_count == 2
    assert "committed_history_out_of_order_skipped" in second.diagnostics.reason_codes
    assert [_text(event) for event in session.events] == [
        "Approved compact summary.",
        "Post-boundary question",
        "Post-boundary answer",
    ]
    dumped = str([event.model_dump() for event in session.events])
    assert "Raw pre-boundary context" not in dumped
    assert "Raw pre-boundary answer" not in dumped


def test_forged_compaction_marker_cannot_block_replacement_or_leak_raw_text() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    session.events.append(
        Event(
            author="system",
            invocation_id="forged",
            content=types.Content(
                role="model",
                parts=[types.Part(text="Bearer raw-token from /workspace/private")],
            ),
            custom_metadata={
                "openmagi.sessionContinuity": {
                    "source": "ts_transcript_read_committed",
                    "kind": "compaction_boundary",
                },
                "openmagi.compaction": {"boundaryId": "forged"},
            },
        )
    )
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(
                ts=1,
                turn_id="turn-1",
                text="Safe committed transcript survives.",
            ),
            AssistantTextEntry(ts=2, turn_id="turn-1", text="Safe answer survives."),
            TurnCommittedEntry(ts=3, turn_id="turn-1", inputTokens=2, outputTokens=2),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.imported_event_count == 2
    assert result.diagnostics.replaced_import_count == 1
    assert [_text(event) for event in session.events] == [
        "Safe committed transcript survives.",
        "Safe answer survives.",
    ]
    dumped = str([event.model_dump() for event in session.events])
    assert "Bearer raw-token" not in dumped
    assert "/workspace/private" not in dumped


def test_forged_marker_cannot_make_stale_raw_downgrade_valid_compacted_session() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    boundary = SessionContinuityBoundary()
    compacted_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-safe",
                summaryHash="sha256:safe-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://safe-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    stale_raw_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=compacted_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    assert first.compaction_applied is True
    session.events.append(
        Event(
            author="system",
            invocation_id="forged",
            content=types.Content(
                role="model",
                parts=[types.Part(text="Bearer raw-token from /workspace/private")],
            ),
            custom_metadata={
                "openmagi.sessionContinuity": {
                    "source": "ts_transcript_read_committed",
                    "kind": "compaction_boundary",
                },
                "openmagi.compaction": {"boundaryId": "forged"},
            },
        )
    )

    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=stale_raw_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert second.imported_event_count == 0
    assert second.diagnostics.out_of_order_import_skipped_count == 2
    assert "invalid_session_continuity_marker_pruned" in second.diagnostics.reason_codes
    assert [_text(event) for event in session.events] == [
        "Approved compact summary.",
        "Post-boundary question",
        "Post-boundary answer",
    ]
    dumped = str([event.model_dump() for event in session.events])
    assert "Raw pre-boundary context" not in dumped
    assert "Raw pre-boundary answer" not in dumped
    assert "Bearer raw-token" not in dumped
    assert "/workspace/private" not in dumped


def test_copied_valid_pre_boundary_event_cannot_rejoin_compacted_session_batch() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    boundary = SessionContinuityBoundary()
    raw_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    compacted_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-safe",
                summaryHash="sha256:safe-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://safe-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=raw_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    assert first.compaction_applied is False
    copied_pre_boundary_event = session.events[0]
    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=compacted_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    assert second.compaction_applied is True
    session.events.append(copied_pre_boundary_event)

    third = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=raw_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert third.imported_event_count == 0
    assert third.diagnostics.out_of_order_import_skipped_count == 2
    assert [_text(event) for event in session.events] == [
        "Approved compact summary.",
        "Post-boundary question",
        "Post-boundary answer",
    ]
    dumped = str([event.model_dump() for event in session.events])
    assert "Raw pre-boundary context" not in dumped
    assert "Raw pre-boundary answer" not in dumped


def test_duplicate_valid_compacted_batch_event_does_not_reopen_raw_context() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    boundary = SessionContinuityBoundary()
    compacted_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-safe",
                summaryHash="sha256:safe-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://safe-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    stale_raw_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=compacted_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    assert first.compaction_applied is True
    session.events.append(session.events[1])

    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=stale_raw_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert second.imported_event_count == 0
    assert [_text(event) for event in session.events] == [
        "Approved compact summary.",
        "Post-boundary question",
        "Post-boundary answer",
    ]
    dumped = str([event.model_dump() for event in session.events])
    assert "Raw pre-boundary context" not in dumped
    assert "Raw pre-boundary answer" not in dumped


def test_partial_compacted_batch_does_not_reopen_stale_raw_context() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    boundary = SessionContinuityBoundary()
    compacted_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-safe",
                summaryHash="sha256:safe-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://safe-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    stale_raw_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=compacted_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    assert first.compaction_applied is True
    del session.events[1]

    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=stale_raw_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert second.imported_event_count == 0
    assert session.events == []
    assert "invalid_session_continuity_marker_pruned" in second.diagnostics.reason_codes


def test_reordered_compacted_batch_does_not_reopen_stale_raw_context() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    boundary = SessionContinuityBoundary()
    compacted_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-safe",
                summaryHash="sha256:safe-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://safe-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )
    stale_raw_store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )

    first = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=compacted_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )
    assert first.compaction_applied is True
    session.events[:] = [session.events[1], session.events[0], session.events[2]]

    second = _run(
        boundary.import_committed_transcript(
            service,
            session,
            transcript_store=stale_raw_store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert second.imported_event_count == 0
    assert session.events == []
    assert "invalid_session_continuity_marker_pruned" in second.diagnostics.reason_codes


def test_latest_approved_compaction_survives_subsequent_rejected_boundary() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-approved context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-approved answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact-good",
                boundaryId="compact-good",
                summaryHash="sha256:good-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://good-compact",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-approved question"),
            CompactionBoundaryEntry(
                ts=5,
                turn_id="turn-compact-bad",
                boundaryId="compact-bad",
                summaryHash="sha256:bad-summary",
                summaryText="Rejected subsequent summary must not cancel the approved boundary.",
                summaryRef="summary://bad-compact",
            ),
            AssistantTextEntry(ts=6, turn_id="turn-new", text="Post-approved answer"),
            TurnCommittedEntry(ts=7, turn_id="turn-new", inputTokens=2, outputTokens=2),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.compaction_applied is True
    assert result.dropped_pre_boundary_count == 2
    assert result.rejected_entry_count == 1
    assert "unapproved_compaction_boundary_rejected" in result.diagnostics.reason_codes
    assert [_text(event) for event in session.events] == [
        "Approved compact summary.",
        "Post-approved question",
        "Post-approved answer",
    ]
    dumped = str([event.model_dump() for event in session.events])
    assert "Raw pre-approved context" not in dumped
    assert "Raw pre-approved answer" not in dumped
    assert "Rejected subsequent summary" not in dumped


def test_late_compaction_replacement_persists_through_adk_in_memory_service() -> None:
    from google.adk.sessions import InMemorySessionService

    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    async def exercise() -> object:
        service = InMemorySessionService()
        session = await service.create_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="agent:main:app:default",
        )
        boundary = SessionContinuityBoundary()
        await boundary.import_committed_transcript(
            service,
            session,
            transcript_store=_FakeTranscriptStore(
                [
                    UserMessageEntry(
                        ts=1,
                        turn_id="turn-old",
                        text="Raw pre-boundary context",
                    ),
                    AssistantTextEntry(
                        ts=2,
                        turn_id="turn-old",
                        text="Raw pre-boundary answer",
                    ),
                    UserMessageEntry(ts=3, turn_id="turn-new", text="Post question"),
                    AssistantTextEntry(ts=4, turn_id="turn-new", text="Post answer"),
                    TurnCommittedEntry(
                        ts=5,
                        turn_id="turn-new",
                        inputTokens=2,
                        outputTokens=2,
                    ),
                ]
            ),
            config=SessionContinuityConfig(enabled=True),
        )
        await boundary.import_committed_transcript(
            service,
            session,
            transcript_store=_FakeTranscriptStore(
                [
                    UserMessageEntry(
                        ts=1,
                        turn_id="turn-old",
                        text="Raw pre-boundary context",
                    ),
                    AssistantTextEntry(
                        ts=2,
                        turn_id="turn-old",
                        text="Raw pre-boundary answer",
                    ),
                    CompactionBoundaryEntry(
                        ts=3,
                        turn_id="turn-compact",
                        boundaryId="compact-late",
                        summaryHash="sha256:late-summary",
                        summaryText="Approved late compact summary.",
                        approved=True,
                        summaryRef="summary://late-compact",
                    ),
                    UserMessageEntry(ts=4, turn_id="turn-new", text="Post question"),
                    AssistantTextEntry(ts=5, turn_id="turn-new", text="Post answer"),
                    TurnCommittedEntry(
                        ts=6,
                        turn_id="turn-new",
                        inputTokens=2,
                        outputTokens=2,
                    ),
                ]
            ),
            config=SessionContinuityConfig(enabled=True),
        )
        persisted = await service.get_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="agent:main:app:default",
        )
        assert persisted is not None
        return persisted

    persisted = _run(exercise())

    assert [_text(event) for event in persisted.events] == [
        "Approved late compact summary.",
        "Post question",
        "Post answer",
    ]
    dumped = str([event.model_dump() for event in persisted.events])
    assert "Raw pre-boundary context" not in dumped
    assert "Raw pre-boundary answer" not in dumped


def test_compaction_boundary_suppresses_pre_boundary_raw_and_imports_summary_ref() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary secret"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw old answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-1",
                summaryHash="sha256:abc123",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://compact-1",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=3),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.compaction_applied is True
    assert result.dropped_pre_boundary_count == 2
    assert [_text(event) for event in session.events] == [
        "Approved compact summary.",
        "Post-boundary question",
        "Post-boundary answer",
    ]
    assert _metadata(session.events[0])["openmagi.compaction"] == {
        "boundaryId": "compact-1",
        "summaryHash": "sha256:abc123",
        "summaryRef": "summary://compact-1",
    }


def test_compaction_boundary_with_ref_does_not_import_unsafe_summary_text() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary detail"),
            CompactionBoundaryEntry(
                ts=2,
                turn_id="turn-compact",
                boundaryId="compact-ref-only-1",
                summaryHash="sha256:ref-only-summary",
                summaryText="Approved summary includes /workspace/private/raw-path",
                approved=True,
                summaryRef="summary://compact-ref-only-1",
            ),
            UserMessageEntry(ts=3, turn_id="turn-new", text="Post-boundary question"),
            TurnCommittedEntry(ts=4, turn_id="turn-new", inputTokens=2, outputTokens=3),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.compaction_applied is True
    assert result.dropped_pre_boundary_count == 1
    assert [_text(event) for event in session.events] == [
        "",
        "Post-boundary question",
    ]
    assert _metadata(session.events[0])["openmagi.compaction"] == {
        "boundaryId": "compact-ref-only-1",
        "summaryHash": "sha256:ref-only-summary",
        "summaryRef": "summary://compact-ref-only-1",
    }
    dumped = str([event.model_dump() for event in session.events])
    assert "/workspace/private/raw-path" not in dumped
    assert "Raw pre-boundary detail" not in dumped


def test_subsequent_unapproved_compaction_boundary_cannot_reopen_pre_boundary_raw() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary secret"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw old answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-approved-1",
                summaryHash="sha256:approved-summary",
                summaryText="Approved compact summary.",
                approved=True,
                summaryRef="summary://compact-approved-1",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Safe post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Safe post-boundary answer"),
            CompactionBoundaryEntry(
                ts=6,
                turn_id="turn-bad-compact",
                boundaryId="compact-unapproved-2",
                summaryHash="sha256:unapproved-summary",
                summaryText="Unapproved subsequent summary must not reopen raw history.",
                summaryRef="summary://compact-unapproved-2",
            ),
            UserMessageEntry(ts=7, turn_id="turn-latest", text="Latest safe question"),
            TurnCommittedEntry(ts=8, turn_id="turn-latest", inputTokens=2, outputTokens=3),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.compaction_applied is True
    assert result.dropped_pre_boundary_count == 2
    assert result.rejected_entry_count == 1
    assert "unapproved_compaction_boundary_rejected" in result.diagnostics.reason_codes
    imported_text = "\n".join(_text(event) for event in session.events)
    assert "Raw pre-boundary secret" not in imported_text
    assert "Raw old answer" not in imported_text
    assert [_text(event) for event in session.events] == [
        "Approved compact summary.",
        "Safe post-boundary question",
        "Safe post-boundary answer",
        "Latest safe question",
    ]


def test_typescript_compaction_fixture_imports_summary_and_drops_pre_boundary_raw(
    tmp_path: Path,
) -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = TranscriptStore(
        file_path=_copy_fixture(tmp_path, "typescript_replay_repair_tail.jsonl")
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.compaction_applied is True
    assert result.dropped_pre_boundary_count == 5
    assert result.imported_event_count == 1
    assert [_text(event) for event in session.events] == [
        "Fixture-only structural post-commit summary.",
    ]
    assert _metadata(session.events[0])["openmagi.compaction"] == {
        "boundaryId": "compact-post-commit-1",
        "summaryHash": "sha256:redacted-tail-summary",
    }
    dumped = str([event.model_dump() for event in session.events])
    assert "Replay the committed TypeScript-compatible tail" not in dumped
    assert "Visible answer preserved" not in dumped
    assert "redacted hidden reasoning fixture only" not in dumped


def test_unapproved_noncanonical_compaction_boundary_is_rejected_without_suppression() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-old", text="Raw pre-boundary context"),
            AssistantTextEntry(ts=2, turn_id="turn-old", text="Raw pre-boundary answer"),
            CompactionBoundaryEntry(
                ts=3,
                turn_id="turn-compact",
                boundaryId="compact-synthetic-1",
                summaryHash="sha256:synthetic-summary",
                summaryText="Synthetic summary must not import.",
                summaryRef="summary://synthetic-1",
            ),
            UserMessageEntry(ts=4, turn_id="turn-new", text="Post-boundary question"),
            AssistantTextEntry(ts=5, turn_id="turn-new", text="Post-boundary answer"),
            TurnCommittedEntry(ts=6, turn_id="turn-new", inputTokens=2, outputTokens=3),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.compaction_applied is False
    assert result.dropped_pre_boundary_count == 0
    assert result.rejected_entry_count == 1
    assert "unapproved_compaction_boundary_rejected" in result.diagnostics.reason_codes
    assert [_text(event) for event in session.events] == [
        "Raw pre-boundary context",
        "Raw pre-boundary answer",
        "Post-boundary question",
        "Post-boundary answer",
    ]
    assert all("openmagi.compaction" not in _metadata(event) for event in session.events)


def test_incognito_mode_does_not_inject_long_term_or_private_memory() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        MemoryRecallProjection,
        SessionContinuityBoundary,
        SessionContinuityConfig,
        SessionContinuityPolicy,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [UserMessageEntry(ts=1, turn_id="turn-1", text="Current session only")]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
            policy=SessionContinuityPolicy(
                memoryMode="incognito",
                recallProjection=MemoryRecallProjection(
                    allowed=True,
                    refs=("memory://private/root",),
                    privatePayload="do not import",
                ),
            ),
        )
    )

    assert result.memory.recall_imported is False
    assert "incognito_blocks_recall" in result.memory.reason_codes
    assert len(session.events) == 1
    assert _text(session.events[0]) == "Current session only"
    assert all("private/root" not in str(event.model_dump()) for event in session.events)
    assert all("do not import" not in str(event.model_dump()) for event in session.events)


def test_read_only_memory_mode_may_represent_recall_refs_without_write_intent() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        MemoryRecallProjection,
        SessionContinuityBoundary,
        SessionContinuityConfig,
        SessionContinuityPolicy,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore([])

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
            policy=SessionContinuityPolicy(
                memoryMode="read_only",
                recallProjection=MemoryRecallProjection(
                    allowed=True,
                    refs=("memory-ref://daily-2026-05-19",),
                ),
            ),
        )
    )

    assert result.memory.recall_imported is True
    assert result.memory.write_intent_produced is False
    assert result.authority_flags.memory_write_allowed is False
    assert len(session.events) == 1
    assert _metadata(session.events[0])["openmagi.memoryRecall"] == {
        "mode": "read_only",
        "refs": ["memory-ref://daily-2026-05-19"],
    }
    assert _text(session.events[0]) == ""


def test_child_isolation_rejects_raw_child_payloads_and_allows_sanitized_refs() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            ToolResultEntry(
                ts=1,
                turn_id="turn-1",
                toolUseId="spawn-raw",
                status="ok",
                output="child hidden reasoning and raw child output",
                metadata={
                    "childTranscript": [{"role": "assistant", "content": "raw child"}],
                    "rawToolLogs": ["curl -H Authorization: Bearer secret"],
                    "hiddenReasoning": "private chain",
                    "intermediateOutput": "draft child answer",
                },
            ),
            ToolResultEntry(
                ts=2,
                turn_id="turn-1",
                toolUseId="spawn-safe",
                status="ok",
                output=None,
                metadata={
                    "childEnvelopeRef": "child-envelope://spawn-safe",
                    "evidenceRefs": ["evidence://child/report-1"],
                },
            ),
            TurnCommittedEntry(ts=3, turn_id="turn-1", inputTokens=1, outputTokens=1),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.rejected_entry_count == 1
    assert "raw_child_payload_rejected" in result.diagnostics.reason_codes
    assert len(session.events) == 1
    assert _metadata(session.events[0])["openmagi.childEnvelopeRef"] == (
        "child-envelope://spawn-safe"
    )
    assert _metadata(session.events[0])["openmagi.evidenceRefs"] == [
        "evidence://child/report-1"
    ]
    dumped = str(session.events[0].model_dump())
    assert "raw child" not in dumped
    assert "hidden reasoning" not in dumped
    assert "draft child answer" not in dumped


def test_sanitized_ref_validation_is_field_specific_and_rejects_unsafe_schemes() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        MemoryRecallProjection,
        SessionContinuityBoundary,
        SessionContinuityConfig,
        SessionContinuityPolicy,
    )

    rejected_refs = [
        "file://tmp/local-file",
        "http://example.com/source",
        "https://example.com/source",
        "https://api.telegram.org/file/bot123456:REDACTEDFIXTURE/report.pdf",
        "/workspace/private/source",
        "memory://private/root",
        "unknown://ref-1",
    ]
    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            CompactionBoundaryEntry(
                ts=1,
                turn_id="turn-summary",
                boundaryId="compact-ref-validation",
                summaryHash="sha256:summary-ref-validation",
                summaryText="Summary ref accepted.",
                approved=True,
                summaryRef="summary://safe/summary-1",
            ),
            ToolResultEntry(
                ts=2,
                turn_id="turn-refs",
                toolUseId="child-valid",
                status="ok",
                output=None,
                metadata={"childEnvelopeRef": "child-envelope://safe/child-1"},
            ),
            ToolResultEntry(
                ts=3,
                turn_id="turn-refs",
                toolUseId="child-invalid",
                status="ok",
                output=None,
                metadata={"childEnvelopeRef": "http://example.com/child-envelope"},
            ),
            ToolResultEntry(
                ts=4,
                turn_id="turn-refs",
                toolUseId="evidence-refs",
                status="ok",
                output=None,
                metadata={
                    "evidenceRefs": [
                        "evidence://safe/tool-1",
                        "evidence:web:src_1",
                        *rejected_refs,
                    ],
                },
            ),
            ToolResultEntry(
                ts=5,
                turn_id="turn-refs",
                toolUseId="control-refs",
                status="ok",
                output=None,
                metadata={
                    "controlRefs": [
                        "control://approval-1",
                        "control:policy-state",
                        *rejected_refs,
                    ],
                },
            ),
            TurnCommittedEntry(ts=6, turn_id="turn-refs", inputTokens=1, outputTokens=1),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
            policy=SessionContinuityPolicy(
                memoryMode="read_only",
                recallProjection=MemoryRecallProjection(
                    allowed=True,
                    refs=(
                        "memory-ref://daily-2026-05-19",
                        "memory:daily:2026-05-19",
                        *rejected_refs,
                    ),
                ),
            ),
        )
    )

    assert result.memory.recall_imported is True
    summary, child, evidence, control, memory = session.events
    assert _metadata(summary)["openmagi.compaction"] == {
        "boundaryId": "compact-ref-validation",
        "summaryHash": "sha256:summary-ref-validation",
        "summaryRef": "summary://safe/summary-1",
    }
    assert _metadata(child)["openmagi.childEnvelopeRef"] == (
        "child-envelope://safe/child-1"
    )
    assert _metadata(evidence)["openmagi.evidenceRefs"] == [
        "evidence://safe/tool-1",
        "evidence:web:src_1",
    ]
    assert _metadata(control)["openmagi.controlRefs"] == [
        "control://approval-1",
        "control:policy-state",
    ]
    assert _metadata(memory)["openmagi.memoryRecall"] == {
        "mode": "read_only",
        "refs": ["memory-ref://daily-2026-05-19", "memory:daily:2026-05-19"],
    }
    dumped = str([event.model_dump() for event in session.events])
    for rejected in rejected_refs:
        assert rejected not in dumped


def test_sanitized_ref_validation_rejects_nested_blocked_refs_inside_allowed_wrappers() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        MemoryRecallProjection,
        SessionContinuityBoundary,
        SessionContinuityConfig,
        SessionContinuityPolicy,
    )

    nested_blocked_refs = [
        "https://example.com/source",
        "file://tmp/local-file",
        "memory://private/root",
    ]
    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            CompactionBoundaryEntry(
                ts=1,
                turn_id="turn-summary",
                boundaryId="compact-nested-ref-validation",
                summaryHash="sha256:nested-ref-validation",
                summaryText="Nested summary ref should not leak.",
                approved=True,
                summaryRef="summary://memory://private/root",
            ),
            ToolResultEntry(
                ts=2,
                turn_id="turn-refs",
                toolUseId="child-invalid",
                status="ok",
                output=None,
                metadata={
                    "childEnvelopeRef": "child-envelope://memory://private/root",
                },
            ),
            ToolResultEntry(
                ts=3,
                turn_id="turn-refs",
                toolUseId="refs-mixed",
                status="ok",
                output=None,
                metadata={
                    "childEnvelopeRef": "child-envelope://safe/child-1",
                    "evidenceRefs": [
                        "evidence://safe/tool-1",
                        "evidence:web:src_1",
                        "evidence://https://example.com/source",
                        "evidence://file://tmp/local-file",
                        "evidence://memory://private/root",
                    ],
                    "controlRefs": [
                        "control://safe/approval-1",
                        "control://https://example.com/source",
                        "control://file://tmp/local-file",
                        "control://memory://private/root",
                    ],
                },
            ),
            ControlEventTranscriptEntry(
                ts=4,
                turn_id="turn-refs",
                seq=7,
                eventId="ctrl-nested",
                eventType="approval_resolved",
                controlRef="control://memory://private/root",
            ),
            TurnCommittedEntry(ts=5, turn_id="turn-refs", inputTokens=1, outputTokens=1),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
            policy=SessionContinuityPolicy(
                memoryMode="read_only",
                recallProjection=MemoryRecallProjection(
                    allowed=True,
                    refs=(
                        "memory-ref://safe/memory-1",
                        "memory-ref://https://example.com/source",
                        "memory-ref://file://tmp/local-file",
                        "memory-ref://memory://private/root",
                    ),
                ),
            ),
        )
    )

    assert result.memory.recall_imported is True
    dumped = str([event.model_dump() for event in session.events])
    for nested in nested_blocked_refs:
        assert nested not in dumped

    summary, refs, *_control_events, memory = session.events
    assert _metadata(summary)["openmagi.compaction"] == {
        "boundaryId": "compact-nested-ref-validation",
        "summaryHash": "sha256:nested-ref-validation",
    }
    assert _metadata(refs)["openmagi.childEnvelopeRef"] == (
        "child-envelope://safe/child-1"
    )
    assert _metadata(refs)["openmagi.evidenceRefs"] == [
        "evidence://safe/tool-1",
        "evidence:web:src_1",
    ]
    assert _metadata(refs)["openmagi.controlRefs"] == [
        "control://safe/approval-1",
    ]
    assert _metadata(memory)["openmagi.memoryRecall"] == {
        "mode": "read_only",
        "refs": ["memory-ref://safe/memory-1"],
    }


def test_sanitized_ref_validation_rejects_scoped_private_memory_refs_inside_allowed_wrappers() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        MemoryRecallProjection,
        SessionContinuityBoundary,
        SessionContinuityConfig,
        SessionContinuityPolicy,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            CompactionBoundaryEntry(
                ts=1,
                turn_id="turn-summary",
                boundaryId="compact-scoped-private-ref-validation",
                summaryHash="sha256:scoped-private-ref-validation",
                summaryText="Scoped private summary ref should not leak.",
                approved=True,
                summaryRef="summary:memory:private:root",
            ),
            ToolResultEntry(
                ts=2,
                turn_id="turn-refs",
                toolUseId="refs-mixed",
                status="ok",
                output=None,
                metadata={
                    "childEnvelopeRef": "child-envelope:memory:private:root",
                    "evidenceRefs": [
                        "evidence://safe/tool-1",
                        "evidence:web:src_1",
                        "evidence:memory:private:root",
                        "evidence://memory:private:root",
                    ],
                    "controlRefs": [
                        "control://safe/approval-1",
                        "control:memory:private:root",
                        "control://memory:private:root",
                    ],
                },
            ),
            ToolResultEntry(
                ts=3,
                turn_id="turn-refs",
                toolUseId="child-valid",
                status="ok",
                output=None,
                metadata={"childEnvelopeRef": "child-envelope://safe/child-1"},
            ),
            ControlEventTranscriptEntry(
                ts=4,
                turn_id="turn-refs",
                seq=8,
                eventId="ctrl-scoped-private",
                eventType="approval_resolved",
                controlRef="control:memory:private:root",
            ),
            TurnCommittedEntry(ts=5, turn_id="turn-refs", inputTokens=1, outputTokens=1),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
            policy=SessionContinuityPolicy(
                memoryMode="read_only",
                recallProjection=MemoryRecallProjection(
                    allowed=True,
                    refs=(
                        "memory-ref://safe/memory-1",
                        "memory:private:root",
                        "memory-ref:memory:private:root",
                        "memory-ref://memory:private:root",
                    ),
                ),
            ),
        )
    )

    assert result.memory.recall_imported is True
    summary, refs, child, *_control_events, memory = session.events
    assert _metadata(summary)["openmagi.compaction"] == {
        "boundaryId": "compact-scoped-private-ref-validation",
        "summaryHash": "sha256:scoped-private-ref-validation",
    }
    assert "openmagi.childEnvelopeRef" not in _metadata(refs)
    assert _metadata(refs)["openmagi.evidenceRefs"] == [
        "evidence://safe/tool-1",
        "evidence:web:src_1",
    ]
    assert _metadata(refs)["openmagi.controlRefs"] == [
        "control://safe/approval-1",
    ]
    assert _metadata(child)["openmagi.childEnvelopeRef"] == (
        "child-envelope://safe/child-1"
    )
    assert _metadata(memory)["openmagi.memoryRecall"] == {
        "mode": "read_only",
        "refs": ["memory-ref://safe/memory-1"],
    }
    dumped = str([event.model_dump() for event in session.events])
    assert "memory:private:root" not in dumped


def test_tool_and_control_boundary_rejects_raw_payloads_and_allows_sanitized_refs() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            ToolCallEntry(
                ts=1,
                turn_id="turn-1",
                toolUseId="tool-1",
                name="Bash",
                input={"cmd": "cat /workspace/secret && echo token=abc"},
            ),
            ToolResultEntry(
                ts=2,
                turn_id="turn-1",
                toolUseId="tool-1",
                status="ok",
                output="raw tool result with /workspace/path",
                metadata={
                    "approvalPayload": {"cookie": "raw-cookie"},
                    "evidenceRefs": ["evidence://safe/tool-1"],
                    "controlRefs": ["control://approval-1"],
                },
            ),
            ControlEventTranscriptEntry(
                ts=3,
                turn_id="turn-1",
                seq=7,
                eventId="ctrl-1",
                eventType="approval_resolved",
                approvalPayload={"sessionKey": "raw-session-key"},
                controlRef="control://approval-1",
            ),
            TurnCommittedEntry(ts=4, turn_id="turn-1", inputTokens=1, outputTokens=1),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )
    )

    assert result.rejected_entry_count == 3
    assert "raw_tool_payload_rejected" in result.diagnostics.reason_codes
    assert "raw_control_payload_rejected" in result.diagnostics.reason_codes
    assert len(session.events) == 1
    assert _metadata(session.events[0])["openmagi.evidenceRefs"] == [
        "evidence://safe/tool-1"
    ]
    assert _metadata(session.events[0])["openmagi.controlRefs"] == [
        "control://approval-1"
    ]
    dumped = str(session.events[0].model_dump())
    assert "/workspace" not in dumped
    assert "raw-cookie" not in dumped
    assert "raw-session-key" not in dumped


def test_oversized_history_is_bounded_with_diagnostic_metadata() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    service, session = _run(_session())
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(ts=1, turn_id="turn-1", text="one"),
            AssistantTextEntry(ts=2, turn_id="turn-1", text="two"),
            UserMessageEntry(ts=3, turn_id="turn-2", text="three"),
            AssistantTextEntry(ts=4, turn_id="turn-2", text="four"),
            TurnCommittedEntry(ts=5, turn_id="turn-2", inputTokens=1, outputTokens=1),
        ]
    )

    result = _run(
        SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True, maxImportedEvents=2),
        )
    )

    assert result.imported_event_count == 2
    assert result.budget_truncated is True
    assert result.diagnostics.budget_policy == "keep_latest"
    assert result.diagnostics.dropped_for_budget_count == 2
    assert result.diagnostics.total_candidate_event_count == 4
    assert [_text(event) for event in session.events] == ["three", "four"]


def test_import_boundary_has_no_production_writes_or_live_runtime_activation() -> None:
    from openmagi_core_agent.runtime.session_continuity import (
        SessionContinuityBoundary,
        SessionContinuityConfig,
    )

    seen: list[Event] = []

    async def exercise():
        service = WorkspaceSessionService(app_name="openmagi", event_sink=seen.append)
        session = await service.create_session(
            app_name="openmagi",
            user_id="user-1",
            session_id="agent:main:app:default",
        )
        store = _FakeTranscriptStore(
            [UserMessageEntry(ts=1, turn_id="turn-1", text="local only")]
        )
        return await SessionContinuityBoundary().import_committed_transcript(
            service,
            session,
            transcript_store=store,
            config=SessionContinuityConfig(enabled=True),
        )

    result = _run(exercise())

    assert len(seen) == 1
    assert result.local_only is True
    assert result.diagnostic_only is True
    assert result.response_authority == "none"
    assert result.authority_flags.transcript_write_allowed is False
    assert result.authority_flags.sse_write_allowed is False
    assert result.authority_flags.db_write_allowed is False
    assert result.authority_flags.control_write_allowed is False
    assert result.authority_flags.tool_host_active is False
    assert result.authority_flags.memory_provider_active is False
    assert result.authority_flags.child_execution_allowed is False
    assert result.authority_flags.workspace_mutation_allowed is False
    assert result.authority_flags.mission_runtime_allowed is False
    assert result.authority_flags.routing_activation_allowed is False
    assert result.authority_flags.live_runner_activation_allowed is False
