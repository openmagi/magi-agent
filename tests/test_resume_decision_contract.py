from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from magi_agent.runtime.heartbeat_contract import (
    HeartbeatReceipt,
    RunLease,
    StaleRunVerdict,
    heartbeat_receipt_digest,
)
from magi_agent.runtime.resume_decision import (
    ResumeDecisionConfig,
    ResumeDecisionContext,
    evaluate_resume_decision,
)


NOW = datetime(2026, 5, 28, 19, 45, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
ACTIVITY_DIGEST = "sha256:activity:" + "b" * 64


def _digest_text(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _lease() -> RunLease:
    return RunLease(
        runId="run:alpha",
        turnId="turn:alpha",
        sessionKey="sess:alpha",
        workerId="worker:runtime",
        leaseId="lease:alpha",
        leaseAcquiredAt=NOW - timedelta(minutes=10),
        leaseExpiresAt=NOW + timedelta(minutes=5),
        phase="running",
        activeBoundary="turn-controller",
        authorityScope="runtime-contract-default-off",
        generation=1,
        fencingToken=DIGEST_A,
    )


def _heartbeat(*, phase: str = "running") -> HeartbeatReceipt:
    payload: dict[str, object] = {
        "schemaVersion": "openmagi.runtime.heartbeat.receipt.v1",
        "heartbeatId": "heartbeat:alpha",
        "runId": "run:alpha",
        "leaseId": "lease:alpha",
        "sequence": 3,
        "emittedAt": NOW.isoformat().replace("+00:00", "Z"),
        "lastActivityAt": (NOW - timedelta(seconds=30)).isoformat().replace(
            "+00:00",
            "Z",
        ),
        "lastActivityReceiptDigest": ACTIVITY_DIGEST,
        "lastEventId": "event:alpha",
        "lastReceiptId": "activity:alpha",
        "phase": phase,
        "publicSafe": True,
    }
    payload["digest"] = heartbeat_receipt_digest(payload)
    return HeartbeatReceipt.model_validate(payload)


def _verdict(
    verdict: str,
    *reason_codes: str,
    heartbeat_digest: str | None = None,
    include_heartbeat_digest: bool = True,
    lease_digest: str | None = None,
    include_lease_digest: bool = True,
) -> StaleRunVerdict:
    return StaleRunVerdict(
        verdict=verdict,
        runId="run:alpha",
        checkedAt=NOW,
        reasonCodes=reason_codes or (verdict,),
        heartbeatDigest=(
            heartbeat_digest
            if heartbeat_digest is not None
            else (_heartbeat().digest if include_heartbeat_digest else None)
        ),
        leaseDigest=(
            lease_digest
            if lease_digest is not None
            else (_digest_text("lease:alpha") if include_lease_digest else None)
        ),
        metadata={"source": "stale_detector"},
    )


def _context(
    stale_verdict: StaleRunVerdict,
    **overrides: object,
) -> ResumeDecisionContext:
    payload: dict[str, object] = {
        "runId": "run:alpha",
        "decidedAt": NOW,
        "lease": _lease(),
        "lastHeartbeat": _heartbeat(),
        "staleVerdict": stale_verdict,
    }
    payload.update(overrides)
    return ResumeDecisionContext(**payload)


def test_healthy_run_resumes_same_session_without_live_runner_authority() -> None:
    decision = evaluate_resume_decision(
        _context(_verdict("healthy", "healthy"), sameSessionAvailable=True)
    )

    projection = decision.public_projection()

    assert decision.decision == "resume_same_session"
    assert decision.reason_codes == ("same_session_available",)
    assert decision.metadata["resumeExecutionAllowed"] is False
    assert decision.metadata["runnerInvoked"] is False
    assert "modelCallEnabled" not in decision.metadata
    assert "providerCallEnabled" not in decision.metadata
    assert "toolExecutionEnabled" not in decision.metadata
    assert "channelDeliveryEnabled" not in decision.metadata
    assert projection["liveAuthority"] is False
    assert projection["trafficAttached"] is False


def test_restart_interrupted_run_is_resume_pending_metadata_only() -> None:
    decision = evaluate_resume_decision(
        _context(
            _verdict("resume_pending", "lease_released"),
            restartInterrupted=True,
            sameSessionAvailable=False,
            checkpointRef="checkpoint:restart-safe",
        )
    )

    assert decision.decision == "resume_with_system_note"
    assert decision.reason_codes == ("restart_interrupted_resume_pending",)
    assert decision.checkpoint_digest is None
    assert decision.metadata["resumePending"] is True
    assert decision.metadata["restartInterrupted"] is True
    assert decision.metadata["resumeExecutionAllowed"] is False
    assert decision.public_projection()["liveAuthority"] is False


def test_lease_expired_with_checkpoint_retries_from_checkpoint_digest_only() -> None:
    decision = evaluate_resume_decision(
        _context(
            _verdict("lease_expired", "lease_expired"),
            checkpointRef="checkpoint:turn-17",
        )
    )
    projection = decision.public_projection()

    assert decision.decision == "retry_from_checkpoint"
    assert decision.reason_codes == ("checkpoint_available_for_stale_run",)
    assert decision.checkpoint_digest is not None
    assert decision.checkpoint_digest.startswith("sha256:")
    assert projection["checkpointDigest"] == decision.checkpoint_digest
    assert "checkpoint:turn-17" not in str(projection)
    assert projection["liveAuthority"] is False


def test_cancelled_state_cancels_and_projects_failure_without_runner() -> None:
    decision = evaluate_resume_decision(
        _context(_verdict("healthy", "healthy"), cancelled=True)
    )

    assert decision.decision == "cancel_and_project_failure"
    assert decision.reason_codes == ("run_cancelled",)
    assert decision.metadata["cancelled"] is True
    assert decision.metadata["resumeExecutionAllowed"] is False
    assert decision.public_projection()["liveAuthority"] is False


def test_completed_state_ignores_completed_even_if_stale() -> None:
    decision = evaluate_resume_decision(
        _context(
            _verdict("worker_lost", "worker_lost"),
            completed=True,
            checkpointRef="checkpoint:old",
        )
    )

    assert decision.decision == "ignore_completed"
    assert decision.reason_codes == ("run_completed",)
    assert decision.checkpoint_digest is None
    assert decision.metadata["completed"] is True


def test_operator_blocked_and_stuck_loop_threshold_block_without_execution() -> None:
    operator_blocked = evaluate_resume_decision(
        _context(_verdict("blocked_for_operator", "pending_operator_approval"))
    )
    stuck_loop = evaluate_resume_decision(
        _context(
            _verdict("healthy", "healthy"),
            stuckLoopCount=3,
        ),
        config=ResumeDecisionConfig(stuckLoopThreshold=3),
    )

    assert operator_blocked.decision == "block_for_operator"
    assert operator_blocked.reason_codes == ("blocked_for_operator",)
    assert stuck_loop.decision == "block_for_operator"
    assert stuck_loop.reason_codes == ("stuck_loop_threshold_exceeded",)
    assert stuck_loop.metadata["stuckLoopCount"] == 3
    assert stuck_loop.metadata["stuckLoopThreshold"] == 3
    assert stuck_loop.metadata["resumeExecutionAllowed"] is False


def test_resume_decision_rejects_mismatched_or_unsafe_inputs() -> None:
    bad_verdict = StaleRunVerdict(
        verdict="healthy",
        runId="run:other",
        checkedAt=NOW,
    )
    with pytest.raises(ValidationError):
        _context(bad_verdict)

    with pytest.raises(ValidationError):
        _context(_verdict("healthy"), checkpointRef="/Users/kevin/private")

    with pytest.raises(ValidationError):
        _context(_verdict("healthy"), metadata={"modelCallEnabled": True})

    with pytest.raises(ValidationError):
        _context(
            _verdict("healthy"),
            lastHeartbeat=HeartbeatReceipt.model_validate(
                {
                    **_heartbeat().model_dump(by_alias=True, mode="json"),
                    "leaseId": "lease:other",
                    "digest": heartbeat_receipt_digest(
                        {
                            **_heartbeat().model_dump(by_alias=True, mode="json"),
                            "leaseId": "lease:other",
                        }
                    ),
                }
            ),
        )

    with pytest.raises(ValidationError):
        _context(_verdict("healthy", lease_digest="sha256:" + "c" * 64))

    with pytest.raises(ValidationError):
        _context(_verdict("healthy", include_lease_digest=False))

    with pytest.raises(ValidationError):
        _context(_verdict("healthy", heartbeat_digest="sha256:heartbeat:" + "d" * 64))

    with pytest.raises(ValidationError):
        _context(_verdict("healthy", include_heartbeat_digest=False))

    with pytest.raises(ValidationError):
        _context(_verdict("healthy"), lastHeartbeat=None)


def test_resume_decision_metadata_cannot_override_no_live_execution_markers() -> None:
    decision = evaluate_resume_decision(
        _context(
            _verdict("healthy", "healthy"),
            sameSessionAvailable=True,
            metadata={
                "resumeExecutionAllowed": True,
                "resume_execution_allowed": True,
                "runnerInvoked": True,
                "runner_invoked": True,
                "note": "operator-check",
            },
        )
    )

    assert decision.decision == "resume_same_session"
    assert decision.metadata["resumeExecutionAllowed"] is False
    assert decision.metadata["runnerInvoked"] is False
    assert "resume_execution_allowed" not in decision.metadata
    assert "runner_invoked" not in decision.metadata
    assert decision.metadata["note"] == "operator-check"
