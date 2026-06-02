from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from magi_agent.adk_bridge.session_service import WorkspaceSessionService
from magi_agent.runtime.context_lifecycle import (
    ContextLifecycleBoundary,
    ContextLifecycleConfig,
    ContextLifecycleEvent,
    ContextCompactionDecision,
    ContextRestoreResult,
    RestoreContextRequest,
    _restore_provenance_digest,
)
from magi_agent.runtime.query_state import QueryState


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _run(coro):
    return asyncio.run(coro)


async def _session() -> tuple[WorkspaceSessionService, object]:
    service = WorkspaceSessionService(app_name="openmagi")
    session = await service.create_session(
        app_name="openmagi",
        user_id="user-pr21",
        session_id="session-pr21",
    )
    return service, session


def _state() -> QueryState:
    return QueryState(
        currentTurnId="turn-current",
        sessionId="session-pr21",
        outstandingControlRequestRefs=("control:approval:pending-write",),
        latestReadLedgerDigests=(DIGEST_B,),
        pendingToolResultRefs=("tool-result:pending:search-1",),
        childAgentSummaryRefs=("summary:child:agent-1",),
        childAgentEvidenceRefs=("evidence:child:agent-1",),
        verificationEvidenceRefs=("evidence:verification:pytest-1",),
        modelContextConfigRefs=("model-config:standard-final",),
        cacheSafeParamRefs=("cache-params:turn-current",),
        cacheSafeParamDigests=(DIGEST_C,),
    )


def _events(count: int) -> tuple[ContextLifecycleEvent, ...]:
    return tuple(
        ContextLifecycleEvent(
            eventRef=f"event:turn:{index}",
            tokenEstimate=100,
            contentRef=f"content:event:{index}",
        )
        for index in range(count)
    )


def test_default_off_context_lifecycle_does_not_compact_or_append_session_events() -> None:
    service, session = _run(_session())
    boundary = ContextLifecycleBoundary()

    decision = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=_state(),
            events=_events(4),
            approvedSummaryRef="summary:compact:pr21",
            approvedSummaryDigest=DIGEST_A,
        )
    )

    assert decision.status == "skipped"
    assert decision.compaction_applied is False
    assert decision.diagnostics.reason_codes == ("context_lifecycle_disabled",)
    assert session.events == []


def test_compaction_trigger_records_token_and_event_breaches_with_truncation() -> None:
    service, session = _run(_session())
    boundary = ContextLifecycleBoundary()

    decision = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=_state(),
            events=_events(5),
            approvedSummaryRef="summary:compact:pr21",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=250,
                eventCountThreshold=3,
                recentEventCount=2,
            ),
        )
    )

    assert decision.status == "compacted"
    assert decision.compaction_applied is True
    assert decision.threshold_breaches == (
        "token_estimate_threshold_breached",
        "event_count_threshold_breached",
    )
    assert decision.diagnostics.reason_codes == (
        "token_estimate_threshold_breached",
        "event_count_threshold_breached",
        "compaction_applied",
        "pre_boundary_truncated",
    )
    assert decision.truncated_event_count == 3
    assert decision.state.compacted_transcript_summary_ref == "summary:compact:pr21"
    assert decision.state.compacted_transcript_digest == DIGEST_A
    assert decision.state.restore_provenance_digest is not None
    assert decision.state.restore_provenance_digest.startswith("sha256:")
    assert decision.state.recent_event_refs == ("event:turn:3", "event:turn:4")
    assert len(session.events) == 1
    assert (
        session.events[0].custom_metadata["openmagi.contextLifecycle"]["kind"]
        == "compacted_state_provenance"
    )


def test_restore_continuity_uses_summary_ref_recent_refs_and_excludes_raw_old_transcript() -> None:
    service, session = _run(_session())
    boundary = ContextLifecycleBoundary()
    compacted = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=_state(),
            events=_events(5),
            approvedSummaryRef="summary:compact:pr21",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=250,
                eventCountThreshold=10,
                recentEventCount=2,
            ),
        )
    )

    restored = _run(
        boundary.restore_context(
            session_service=service,
            session=session,
            request=RestoreContextRequest(
                state=compacted.state,
                approvedSummaryRef="summary:compact:pr21",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=compacted.state.recent_event_refs,
            ),
        )
    )

    assert restored.status == "restored"
    assert restored.context_refs[:3] == (
        "summary:compact:pr21",
        "event:turn:3",
        "event:turn:4",
    )
    assert restored.state.outstanding_control_request_refs == (
        "control:approval:pending-write",
    )
    assert restored.state.latest_read_ledger_digests == (DIGEST_B,)
    assert restored.state.pending_tool_result_refs == ("tool-result:pending:search-1",)
    assert restored.state.child_agent_evidence_refs == ("evidence:child:agent-1",)
    assert restored.state.verification_evidence_refs == (
        "evidence:verification:pytest-1",
    )
    assert restored.state.cache_safe_param_digests == (DIGEST_C,)
    assert set(restored.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert len(session.events) == 2
    encoded = json.dumps(
        {
            "restore": restored.public_projection(),
            "session": [event.model_dump() for event in session.events],
        },
        sort_keys=True,
        default=str,
    )
    assert "raw old transcript" not in encoded.lower()
    assert "rawChildTranscript" not in encoded
    assert "tool logs" not in encoded.lower()
    assert "hidden reasoning" not in encoded.lower()


def test_final_answer_context_sees_compacted_evidence_refs_not_raw_transcript() -> None:
    service, session = _run(_session())
    boundary = ContextLifecycleBoundary()
    compacted = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=_state(),
            events=_events(5),
            approvedSummaryRef="summary:compact:pr21",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=1,
                eventCountThreshold=99,
                recentEventCount=1,
            ),
        )
    )
    restored = _run(
        boundary.restore_context(
            session_service=service,
            session=session,
            request=RestoreContextRequest(
                state=compacted.state,
                approvedSummaryRef="summary:compact:pr21",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=compacted.state.recent_event_refs,
            ),
        )
    )

    continuation = restored.final_answer_context()
    encoded = json.dumps(continuation, sort_keys=True)

    assert continuation["compactedTranscriptSummaryRef"] == "summary:compact:pr21"
    assert "evidence:child:agent-1" in encoded
    assert "evidence:verification:pytest-1" in encoded
    assert "raw old transcript" not in encoded.lower()
    assert "raw prompt" not in encoded.lower()


def test_restore_rejects_unapproved_or_mismatched_summary_ref() -> None:
    service, session = _run(_session())
    with pytest.raises(ValidationError, match="approvedSummaryDigest"):
        RestoreContextRequest(
            state=_state().model_copy(
                update={
                    "compacted_transcript_summary_ref": "summary:compact:pr21",
                    "compacted_transcript_digest": DIGEST_A,
                }
            ),
            approvedSummaryRef="summary:compact:pr21",
            approvedSummaryDigest="not-a-digest",
            recentEventRefs=("event:turn:1",),
        )

    request = RestoreContextRequest(
        state=_state().model_copy(
            update={
                "compacted_transcript_summary_ref": "summary:compact:pr21",
                "compacted_transcript_digest": DIGEST_A,
            }
        ),
        approvedSummaryRef="summary:compact:other",
        approvedSummaryDigest=DIGEST_A,
        recentEventRefs=("event:turn:1",),
    )
    restored = _run(
        ContextLifecycleBoundary().restore_context(
            session_service=service,
            session=session,
            request=request,
        )
    )
    assert restored.status == "blocked"
    assert restored.diagnostics.reason_codes == ("approved_summary_ref_mismatch",)


def test_restore_rejects_recent_event_refs_that_do_not_match_query_state() -> None:
    service, session = _run(_session())
    state = _state().model_copy(
        update={
            "compacted_transcript_summary_ref": "summary:compact:pr21",
            "compacted_transcript_digest": DIGEST_A,
            "recent_event_refs": ("event:turn:kept-1",),
        }
    )
    request = RestoreContextRequest(
        state=state,
        approvedSummaryRef="summary:compact:pr21",
        approvedSummaryDigest=DIGEST_A,
        recentEventRefs=("event:turn:forged-2",),
    )

    restored = _run(
        ContextLifecycleBoundary().restore_context(
            session_service=service,
            session=session,
            request=request,
        )
    )

    assert restored.status == "blocked"
    assert restored.diagnostics.reason_codes == ("recent_event_refs_mismatch",)
    assert session.events == []


def test_restore_rejects_self_consistent_forged_state_without_compaction_provenance() -> None:
    service, session = _run(_session())
    forged_state = _state().model_copy(
        update={
            "compacted_transcript_summary_ref": "summary:compact:forged",
            "compacted_transcript_digest": DIGEST_A,
            "recent_event_refs": ("event:forged:injected",),
            "pending_tool_result_refs": ("tool-result:forged:injected",),
        }
    )
    forged_state = forged_state.model_copy(
        update={"restore_provenance_digest": _restore_provenance_digest(forged_state)}
    )

    restored = _run(
        ContextLifecycleBoundary().restore_context(
            session_service=service,
            session=session,
            request=RestoreContextRequest(
                state=forged_state,
                approvedSummaryRef="summary:compact:forged",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=("event:forged:injected",),
            ),
        )
    )

    assert restored.status == "blocked"
    assert restored.diagnostics.reason_codes == ("restore_provenance_missing",)
    assert session.events == []


def test_restore_provenance_survives_boundary_recreation_but_binds_session_id() -> None:
    service, session_a = _run(_session())
    session_b = _run(
        service.create_session(
            app_name="openmagi",
            user_id="user-pr21",
            session_id="session-pr21-other",
        )
    )
    compacted = _run(
        ContextLifecycleBoundary().compact_if_needed(
            session_service=service,
            session=session_a,
            state=_state(),
            events=_events(5),
            approvedSummaryRef="summary:compact:pr21",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=250,
                eventCountThreshold=10,
                recentEventCount=2,
            ),
        )
    )

    recreated_boundary = ContextLifecycleBoundary()
    restored = _run(
        recreated_boundary.restore_context(
            session_service=service,
            session=session_a,
            request=RestoreContextRequest(
                state=compacted.state,
                approvedSummaryRef="summary:compact:pr21",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=compacted.state.recent_event_refs,
            ),
        )
    )
    cross_session = _run(
        recreated_boundary.restore_context(
            session_service=service,
            session=session_b,
            request=RestoreContextRequest(
                state=compacted.state,
                approvedSummaryRef="summary:compact:pr21",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=compacted.state.recent_event_refs,
            ),
        )
    )

    assert restored.status == "restored"
    assert cross_session.status == "blocked"
    assert cross_session.diagnostics.reason_codes == ("session_id_mismatch",)
    assert len(session_a.events) == 2
    assert session_b.events == []


def test_restore_requires_local_fake_session_service_before_appending() -> None:
    class UntrustedSessionService:
        openmagi_local_fake_provider = "yes"

        def __init__(self) -> None:
            self.appended = False

        async def append_event(self, session: object, event: object) -> object:
            self.appended = True
            return event

    service, session = _run(_session())
    boundary = ContextLifecycleBoundary()
    compacted = _run(
        boundary.compact_if_needed(
            session_service=service,
            session=session,
            state=_state(),
            events=_events(5),
            approvedSummaryRef="summary:compact:pr21",
            approvedSummaryDigest=DIGEST_A,
            config=ContextLifecycleConfig(
                enabled=True,
                localFakeCompactionEnabled=True,
                tokenEstimateThreshold=250,
                eventCountThreshold=10,
                recentEventCount=2,
            ),
        )
    )
    untrusted = UntrustedSessionService()
    fake_session = type("FakeSession", (), {"id": compacted.state.session_id})()

    restored = _run(
        boundary.restore_context(
            session_service=untrusted,
            session=fake_session,
            request=RestoreContextRequest(
                state=compacted.state,
                approvedSummaryRef="summary:compact:pr21",
                approvedSummaryDigest=DIGEST_A,
                recentEventRefs=compacted.state.recent_event_refs,
            ),
        )
    )

    assert restored.status == "blocked"
    assert restored.diagnostics.reason_codes == ("local_fake_session_service_required",)
    assert untrusted.appended is False


def test_context_lifecycle_result_authority_flags_cannot_be_forged_with_model_copy() -> None:
    state = _state()
    diagnostics = ContextLifecycleBoundary()
    _ = diagnostics
    decision = ContextCompactionDecision(
        status="skipped",
        compactionApplied=False,
        state=state,
        diagnostics={
            "reasonCodes": ("context_lifecycle_disabled",),
            "tokenEstimate": 0,
            "tokenEstimateThreshold": 0,
            "eventCount": 0,
            "eventCountThreshold": 0,
        },
    )
    copied_decision = decision.model_copy(
        update={"authorityFlags": {"sideEffectsAllowed": True, "liveModelCallAllowed": True}}
    )
    assert set(copied_decision.public_projection()["authorityFlags"].values()) == {False}

    restored = ContextRestoreResult(
        status="blocked",
        state=state,
        diagnostics={
            "reasonCodes": ("restore_provenance_missing",),
            "tokenEstimate": 0,
            "tokenEstimateThreshold": 0,
            "eventCount": 0,
            "eventCountThreshold": 0,
        },
    )
    copied_restored = restored.model_copy(
        update={"authorityFlags": {"sideEffectsAllowed": True, "liveModelCallAllowed": True}}
    )
    assert set(copied_restored.public_projection()["authorityFlags"].values()) == {False}


def test_context_lifecycle_result_authority_flags_cannot_be_forged_with_model_construct() -> None:
    state = _state()
    decision = ContextCompactionDecision.model_construct(
        status="skipped",
        compactionApplied=False,
        state=state,
        diagnostics={
            "reasonCodes": ("context_lifecycle_disabled",),
            "tokenEstimate": 0,
            "tokenEstimateThreshold": 0,
            "eventCount": 0,
            "eventCountThreshold": 0,
        },
        authorityFlags={"sideEffectsAllowed": True, "liveModelCallAllowed": True},
    )
    assert set(decision.public_projection()["authorityFlags"].values()) == {False}

    restored = ContextRestoreResult.model_construct(
        status="blocked",
        state=state,
        diagnostics={
            "reasonCodes": ("restore_provenance_missing",),
            "tokenEstimate": 0,
            "tokenEstimateThreshold": 0,
            "eventCount": 0,
            "eventCountThreshold": 0,
        },
        authorityFlags={"sideEffectsAllowed": True, "liveModelCallAllowed": True},
    )
    assert set(restored.public_projection()["authorityFlags"].values()) == {False}
