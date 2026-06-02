from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from magi_agent.runtime.query_state import (
    QueryState,
    QueryStateAuthorityFlags,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _state() -> QueryState:
    return QueryState(
        currentTurnId="turn-pr21-current",
        sessionId="session-pr21",
        compactedTranscriptSummaryRef="summary:compact:pr21",
        compactedTranscriptDigest=DIGEST_A,
        restoreProvenanceDigest=DIGEST_C,
        recentEventRefs=("event:recent:1", "event:recent:2"),
        outstandingControlRequestRefs=("control:approval:write-1",),
        latestReadLedgerDigests=(DIGEST_B,),
        pendingToolResultRefs=("tool-result:pending:1",),
        childAgentSummaryRefs=("summary:child:research-1",),
        childAgentEvidenceRefs=("evidence:child:research-1",),
        verificationEvidenceRefs=("evidence:verification:test-run-1",),
        modelContextConfigRefs=("model-config:standard-final",),
        cacheSafeParamRefs=("cache-params:turn-pr21",),
        cacheSafeParamDigests=(DIGEST_C,),
        authorityFlags={
            "liveModelCallAllowed": True,
            "toolExecutionAllowed": True,
            "memoryProviderCallAllowed": True,
            "memoryWriteAllowed": True,
            "productionTranscriptWriteAllowed": True,
            "userVisibleOutputAllowed": True,
            "liveAttachmentAllowed": True,
        },
    )


def test_query_state_preserves_required_refs_and_forces_authority_flags_false() -> None:
    state = _state()

    assert state.current_turn_id == "turn-pr21-current"
    assert state.session_id == "session-pr21"
    assert state.compacted_transcript_summary_ref == "summary:compact:pr21"
    assert state.compacted_transcript_digest == DIGEST_A
    assert state.restore_provenance_digest == DIGEST_C
    assert state.outstanding_control_request_refs == ("control:approval:write-1",)
    assert state.latest_read_ledger_digests == (DIGEST_B,)
    assert state.pending_tool_result_refs == ("tool-result:pending:1",)
    assert state.child_agent_summary_refs == ("summary:child:research-1",)
    assert state.child_agent_evidence_refs == ("evidence:child:research-1",)
    assert state.verification_evidence_refs == ("evidence:verification:test-run-1",)
    assert state.model_context_config_refs == ("model-config:standard-final",)
    assert state.cache_safe_param_refs == ("cache-params:turn-pr21",)
    assert state.cache_safe_param_digests == (DIGEST_C,)
    assert set(state.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_query_state_public_projection_is_ref_and_digest_only() -> None:
    projection = _state().public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["compactedTranscriptSummaryRef"] == "summary:compact:pr21"
    assert projection["recentEventRefs"] == ["event:recent:1", "event:recent:2"]
    assert "raw old transcript" not in encoded.lower()
    assert "rawTranscript" not in encoded
    assert "Authorization" not in encoded
    assert "/Users/" not in encoded


def test_query_state_rejects_raw_transcript_or_unsafe_refs() -> None:
    payload = _state().model_dump(by_alias=True, mode="python")
    payload["rawTranscript"] = "raw old transcript must not be carried"
    with pytest.raises(ValidationError, match="Extra inputs"):
        QueryState.model_validate(payload)

    payload = _state().model_dump(by_alias=True, mode="python")
    payload["pendingToolResultRefs"] = ("tool-result:/Users/kevin/private/log.txt",)
    with pytest.raises(ValidationError, match="safe refs"):
        QueryState.model_validate(payload)


def test_authority_flags_cannot_be_forged_by_construct_validate_or_copy() -> None:
    forged = QueryStateAuthorityFlags.model_construct(liveModelCallAllowed=True)
    assert set(forged.model_dump(by_alias=True).values()) == {False}

    validated = QueryStateAuthorityFlags.model_validate(
        {
            "liveModelCallAllowed": True,
            "toolExecutionAllowed": True,
            "memoryProviderCallAllowed": True,
            "memoryWriteAllowed": True,
            "productionTranscriptWriteAllowed": True,
            "userVisibleOutputAllowed": True,
            "liveAttachmentAllowed": True,
        }
    )
    assert set(validated.model_dump(by_alias=True).values()) == {False}

    copied = validated.model_copy(update={"tool_execution_allowed": True})
    assert set(copied.model_dump(by_alias=True).values()) == {False}
