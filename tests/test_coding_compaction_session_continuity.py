from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.runtime.context_lifecycle import (
    ContextLifecycleBoundary,
    ContextLifecycleConfig,
    ContextLifecycleEvent,
    RestoreContextRequest,
)
from magi_agent.runtime.context_packet import (
    ContextContinuityConfig,
    build_context_packet_from_transcript,
    render_context_packet_for_model,
)
from magi_agent.runtime.query_state import QueryState
from magi_agent.runtime.transcript import (
    CompactionBoundaryEntry,
    ControlEventTranscriptEntry,
    ToolCallEntry,
    ToolResultEntry,
    TranscriptEntry,
    TurnCommittedEntry,
    UserMessageEntry,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


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
        raise AssertionError("coding continuity must use committed diagnostic input only")

    def append(self, entry: TranscriptEntry) -> None:
        _ = entry
        self.append_calls += 1
        raise AssertionError("coding continuity gap tests must not write transcripts")


@dataclass
class _SessionStub:
    id: str
    events: list[object]


class _RejectingLiveSessionService:
    openmagi_local_fake_provider = False

    def __init__(self) -> None:
        self.append_calls = 0

    async def append_event(self, *_args: object, **_kwargs: object) -> None:
        self.append_calls += 1
        raise AssertionError("non-local SessionService must not receive coding compaction writes")


def _run(coro):
    return asyncio.run(coro)


async def _session(
    *,
    event_sink: list[object] | None = None,
) -> tuple[WorkspaceSessionService, object]:
    service = WorkspaceSessionService(
        app_name="openmagi",
        event_sink=event_sink.append if event_sink is not None else None,
    )
    session = await service.create_session(
        app_name="openmagi",
        user_id="user-coding-pr6",
        session_id="session-coding-pr6",
    )
    return service, session


def test_coding_context_packet_imports_safe_refs_and_rejects_raw_private_data() -> None:
    store = _FakeTranscriptStore(
        [
            UserMessageEntry(
                ts=1,
                turn_id="turn-old",
                text="Pre-boundary coding detail with sk-live-REDACT_ME_SECRET_SENTINEL",
            ),
            CompactionBoundaryEntry(
                ts=2,
                turn_id="turn-compact",
                boundaryId="compact-coding-pr6",
                summaryHash=DIGEST_B,
                summaryText=(
                    "summary:compact:coding-pr6 task-plan:coding-pr6 "
                    "file-ref:src-app-py read-ledger:coding-pr6 diff:coding-pr6 "
                    "test:pytest:pass control:blocker:stale-read"
                ),
                approved=True,
                summaryRef="summary:compact:coding-pr6",
            ),
            ToolCallEntry(
                ts=3,
                turn_id="turn-coding",
                toolUseId="bash-raw",
                name="Bash",
                input={
                    "cmd": (
                        "cat /workspace/private/secret && "
                        "echo Authorization: Bearer token"
                    ),
                    "hiddenReasoning": "raw hidden coding chain",
                },
            ),
            ToolResultEntry(
                ts=4,
                turn_id="turn-coding",
                toolUseId="coding-evidence",
                status="ok",
                output="raw tool output from /workspace/private/secret",
                metadata={
                    "evidenceRefs": [
                        "evidence:diff:coding-pr6",
                        "evidence:test:pytest:pass",
                    ],
                    "controlRefs": ["control:blocker:stale-read"],
                    "approvalPayload": {"cookie": "REDACT_ME_COOKIE_SENTINEL"},
                },
            ),
            ControlEventTranscriptEntry(
                ts=5,
                turn_id="turn-coding",
                seq=1,
                eventId="control-coding-pr6",
                eventType="approval_requested",
                approvalPayload={"sessionKey": "REDACT_ME_SESSION_KEY"},
                controlRef="control:blocker:stale-read",
            ),
            UserMessageEntry(
                ts=6,
                turn_id="turn-coding",
                text="Continue from the coding PR6 safe refs.",
            ),
            TurnCommittedEntry(
                ts=7,
                turn_id="turn-coding",
                inputTokens=10,
                outputTokens=5,
            ),
        ]
    )

    packet = build_context_packet_from_transcript(
        store,
        config=ContextContinuityConfig(enabled=True, maxImportedEvents=8),
    )
    rendered = render_context_packet_for_model(packet)
    packet_projection = packet.model_dump(by_alias=True, mode="json")

    assert packet.enabled is True
    assert packet.local_only is True
    assert packet.diagnostic_only is True
    assert packet.response_authority == "none"
    assert set(packet.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert store.read_committed_calls == 1
    assert store.read_all_calls == 0
    assert store.append_calls == 0

    assert packet.diagnostics.imported_event_count == 3
    assert packet.diagnostics.rejected_entry_count == 3
    assert packet.diagnostics.compaction_applied is True
    assert packet.diagnostics.dropped_pre_boundary_count == 1
    assert "raw_tool_payload_rejected" in packet.diagnostics.reason_codes
    assert "raw_control_payload_rejected" in packet.diagnostics.reason_codes
    assert packet.projection_digest is not None
    assert packet.model_visible_digest is not None
    assert packet.source_transcript_head_digest is not None
    assert _DIGEST_RE.fullmatch(packet.projection_digest)
    assert _DIGEST_RE.fullmatch(packet.model_visible_digest)
    assert _DIGEST_RE.fullmatch(packet.source_transcript_head_digest)

    for safe_ref in (
        "summary:compact:coding-pr6",
        "task-plan:coding-pr6",
        "file-ref:src-app-py",
        "read-ledger:coding-pr6",
        "diff:coding-pr6",
        "test:pytest:pass",
        "control:blocker:stale-read",
        "evidence:diff:coding-pr6",
        "evidence:test:pytest:pass",
    ):
        assert safe_ref in rendered
        assert safe_ref in str(packet_projection)

    for unsafe_text in (
        "sk-live-REDACT_ME_SECRET_SENTINEL",
        "/workspace/private",
        "Authorization: Bearer token",
        "raw hidden coding chain",
        "REDACT_ME_COOKIE_SENTINEL",
        "REDACT_ME_SESSION_KEY",
    ):
        assert unsafe_text not in rendered
        assert unsafe_text not in str(packet_projection)


def test_unsafe_coding_compaction_summary_is_not_model_visible_context() -> None:
    packet = build_context_packet_from_transcript(
        _FakeTranscriptStore(
            [
                UserMessageEntry(
                    ts=1,
                    turn_id="turn-old",
                    text="Raw old coding context that should be behind the boundary.",
                ),
                CompactionBoundaryEntry(
                    ts=2,
                    turn_id="turn-compact",
                    boundaryId="compact-coding-pr6-ref-only",
                    summaryHash="sha256:coding-pr6-ref-only",
                    summaryText=(
                        "Unsafe compact text includes /workspace/private/path and "
                        "REDACT_ME_SECRET_SENTINEL"
                    ),
                    approved=True,
                    summaryRef="summary:compact:coding-pr6",
                ),
                UserMessageEntry(
                    ts=3,
                    turn_id="turn-new",
                    text="Post-boundary coding context survives.",
                ),
                TurnCommittedEntry(
                    ts=4,
                    turn_id="turn-new",
                    inputTokens=4,
                    outputTokens=2,
                ),
            ]
        ),
        config=ContextContinuityConfig(enabled=True),
    )
    rendered = render_context_packet_for_model(packet)
    packet_projection = packet.model_dump(by_alias=True, mode="json")

    assert packet.diagnostics.compaction_applied is True
    assert packet.diagnostics.dropped_pre_boundary_count == 1
    assert "Post-boundary coding context survives." in rendered
    assert "Raw old coding context" not in rendered
    assert "/workspace/private/path" not in rendered
    assert "REDACT_ME_SECRET_SENTINEL" not in rendered
    assert "Raw old coding context" not in str(packet_projection)
    assert "/workspace/private/path" not in str(packet_projection)
    assert "REDACT_ME_SECRET_SENTINEL" not in str(packet_projection)


def test_coding_lifecycle_projection_preserves_refs_with_local_fake_session_only() -> None:
    local_events: list[object] = []
    service, session = _run(_session(event_sink=local_events))
    state = QueryState(
        currentTurnId="turn-coding-pr6",
        sessionId="session-coding-pr6",
        outstandingControlRequestRefs=("control:blocker:stale-read",),
        pendingToolResultRefs=("diff:coding-pr6",),
        verificationEvidenceRefs=("test:pytest:pass",),
        modelContextConfigRefs=("task-plan:coding-pr6",),
    )
    events = (
        ContextLifecycleEvent(
            eventRef="event:coding-pr6:pre-boundary",
            tokenEstimate=200,
            contentRef="content:coding-pr6:pre-boundary",
        ),
        ContextLifecycleEvent(
            eventRef="task-plan:coding-pr6",
            tokenEstimate=200,
        ),
        ContextLifecycleEvent(
            eventRef="file-ref:src-app-py",
            tokenEstimate=200,
        ),
        ContextLifecycleEvent(
            eventRef="read-ledger:coding-pr6",
            tokenEstimate=200,
        ),
        ContextLifecycleEvent(
            eventRef="diff:coding-pr6",
            tokenEstimate=200,
        ),
        ContextLifecycleEvent(
            eventRef="test:pytest:pass",
            tokenEstimate=200,
        ),
    )
    boundary = ContextLifecycleBoundary()

    compacted = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=state,
            events=events,
            approvedSummaryRef="summary:compact:coding-pr6",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=300,
                eventCountThreshold=3,
                recentEventCount=5,
            ),
        )
    )
    restored = _run(
        boundary.restore_context(
            session_service=service,
            session=session,
            request=RestoreContextRequest(
                state=compacted.state,
                approvedSummaryRef="summary:compact:coding-pr6",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=compacted.state.recent_event_refs,
            ),
        )
    )

    assert service.openmagi_local_fake_provider is True
    assert len(local_events) == 2
    assert compacted.status == "compacted"
    assert compacted.truncated_event_count == 1
    assert "pre_boundary_truncated" in compacted.diagnostics.reason_codes
    assert restored.status == "restored"
    assert restored.context_refs == (
        "summary:compact:coding-pr6",
        "task-plan:coding-pr6",
        "file-ref:src-app-py",
        "read-ledger:coding-pr6",
        "diff:coding-pr6",
        "test:pytest:pass",
        "control:blocker:stale-read",
    )
    assert restored.final_answer_context()["contextRefs"] == list(restored.context_refs)
    assert restored.state.compacted_transcript_summary_ref == "summary:compact:coding-pr6"
    assert restored.state.compacted_transcript_digest == DIGEST_A
    assert restored.state.restore_provenance_digest is not None
    assert _DIGEST_RE.fullmatch(restored.state.restore_provenance_digest)
    assert set(compacted.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert set(restored.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert set(restored.state.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert "raw" not in str(restored.final_answer_context()).lower()


def test_coding_lifecycle_blocks_non_local_session_service_before_any_write() -> None:
    service = _RejectingLiveSessionService()
    session = _SessionStub(id="session-coding-pr6", events=[])
    boundary = ContextLifecycleBoundary()
    state = QueryState(
        currentTurnId="turn-coding-pr6",
        sessionId="session-coding-pr6",
        outstandingControlRequestRefs=("control:blocker:stale-read",),
    )

    decision = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=state,
            events=(
                ContextLifecycleEvent(eventRef="task-plan:coding-pr6", tokenEstimate=200),
                ContextLifecycleEvent(eventRef="diff:coding-pr6", tokenEstimate=200),
            ),
            approvedSummaryRef="summary:compact:coding-pr6",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=1,
                eventCountThreshold=1,
                recentEventCount=1,
            ),
        )
    )

    assert decision.status == "blocked"
    assert decision.compaction_applied is False
    assert decision.diagnostics.reason_codes == ("local_fake_session_service_required",)
    assert service.append_calls == 0
    assert session.events == []
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}
