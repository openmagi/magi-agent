from __future__ import annotations

from datetime import UTC, datetime

from openmagi_core_agent.runtime.heartbeat_contract import (
    ActivityReceipt,
    HeartbeatReceipt,
    RunLease,
    activity_receipt_digest,
    heartbeat_receipt_digest,
)
from openmagi_core_agent.runtime.heartbeat_store import (
    HeartbeatRunRecord,
    LocalFakeHeartbeatStore,
)
from openmagi_core_agent.runtime.stale_run_detector import (
    StaleRunDetectorConfig,
    evaluate_stale_run,
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _lease(
    *,
    run_id: str = "run:alpha",
    phase: str = "running",
    acquired_at: str = "2026-05-27T12:00:00Z",
    expires_at: str = "2026-05-27T13:00:00Z",
) -> RunLease:
    return RunLease.model_validate(
        {
            "runId": run_id,
            "turnId": "turn:001",
            "sessionKey": "sess:alpha",
            "workerId": "worker:west-1",
            "leaseId": "lease:primary",
            "leaseAcquiredAt": acquired_at,
            "leaseExpiresAt": expires_at,
            "phase": phase,
            "activeBoundary": "turn-controller",
            "authorityScope": "runtime-contract-default-off",
            "generation": 1,
            "fencingToken": _digest("a"),
        }
    )


def _activity(
    *,
    run_id: str = "run:alpha",
    sequence: int = 1,
    emitted_at: str = "2026-05-27T12:00:10Z",
) -> ActivityReceipt:
    payload: dict[str, object] = {
        "activityId": f"activity:{sequence:03d}",
        "runId": run_id,
        "leaseId": "lease:primary",
        "sequence": sequence,
        "emittedAt": emitted_at,
        "activityType": "model_stream_delta",
        "activityRef": f"event:activity-{sequence:03d}",
        "metadata": {"source": "runtime", "count": sequence},
        "publicSafe": True,
    }
    payload["digest"] = activity_receipt_digest(payload)
    return ActivityReceipt.model_validate(payload)


def _heartbeat(
    *,
    run_id: str = "run:alpha",
    sequence: int = 1,
    emitted_at: str = "2026-05-27T12:00:20Z",
    last_activity_at: str = "2026-05-27T12:00:10Z",
    last_activity_digest: str,
    phase: str = "running",
    pending_approval_ids: tuple[str, ...] = (),
) -> HeartbeatReceipt:
    payload: dict[str, object] = {
        "heartbeatId": f"heartbeat:{sequence:03d}",
        "runId": run_id,
        "leaseId": "lease:primary",
        "sequence": sequence,
        "emittedAt": emitted_at,
        "lastActivityAt": last_activity_at,
        "lastActivityReceiptDigest": last_activity_digest,
        "phase": phase,
        "pendingApprovalIds": list(pending_approval_ids),
        "publicSafe": True,
    }
    payload["digest"] = heartbeat_receipt_digest(payload)
    return HeartbeatReceipt.model_validate(payload)


def _record(
    *,
    lease: RunLease | None = None,
    activities: tuple[ActivityReceipt, ...] = (),
    heartbeats: tuple[HeartbeatReceipt, ...] = (),
) -> HeartbeatRunRecord:
    store = LocalFakeHeartbeatStore()
    active_lease = lease or _lease()
    store.acquire_lease(active_lease, now=active_lease.lease_acquired_at)
    for activity in activities:
        store.append_activity(activity, expected_fencing_token=active_lease.fencing_token)
    for heartbeat in heartbeats:
        store.append_heartbeat(heartbeat, expected_fencing_token=active_lease.fencing_token)
    record = store.get_run(active_lease.run_id)
    assert record is not None
    return record


def _config() -> StaleRunDetectorConfig:
    return StaleRunDetectorConfig(
        heartbeat_silence_after_seconds=60,
        inactivity_timeout_seconds=300,
        worker_lost_after_seconds=600,
    )


def test_stale_run_detector_returns_healthy_for_recent_activity_and_heartbeat() -> None:
    activity = _activity(emitted_at="2026-05-27T12:04:00Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:04:30Z",
        last_activity_at="2026-05-27T12:04:00Z",
        last_activity_digest=activity.digest,
    )
    record = _record(activities=(activity,), heartbeats=(heartbeat,))

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:05:00Z"),
        config=_config(),
    )

    assert verdict.verdict == "healthy"
    assert verdict.reason_codes == ("healthy",)
    assert verdict.heartbeat_digest == heartbeat.digest
    assert verdict.activity_digest == activity.digest
    assert verdict.lease_digest is not None
    assert verdict.public_projection()["trustedLeaseAuthority"] is False


def test_stale_run_detector_is_deterministic_for_same_record_and_check_time() -> None:
    activity = _activity(emitted_at="2026-05-27T12:01:00Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:01:30Z",
        last_activity_at="2026-05-27T12:01:00Z",
        last_activity_digest=activity.digest,
    )
    record = _record(activities=(activity,), heartbeats=(heartbeat,))
    checked_at = _dt("2026-05-27T12:02:00Z")

    first = evaluate_stale_run(record, checked_at=checked_at, config=_config())
    second = evaluate_stale_run(record, checked_at=checked_at, config=_config())

    assert first == second


def test_stale_run_detector_returns_silent_when_heartbeat_is_late_but_not_lost() -> None:
    activity = _activity(emitted_at="2026-05-27T12:08:00Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:08:10Z",
        last_activity_at="2026-05-27T12:08:00Z",
        last_activity_digest=activity.digest,
    )
    record = _record(activities=(activity,), heartbeats=(heartbeat,))

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:09:30Z"),
        config=_config(),
    )

    assert verdict.verdict == "silent_but_within_threshold"
    assert verdict.reason_codes == ("heartbeat_silent",)


def test_stale_run_detector_heartbeat_only_activity_does_not_prevent_inactivity_timeout() -> None:
    activity = _activity(emitted_at="2026-05-27T12:00:10Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:07:00Z",
        last_activity_at="2026-05-27T12:00:10Z",
        last_activity_digest=activity.digest,
    )
    record = _record(activities=(activity,), heartbeats=(heartbeat,))

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:07:10Z"),
        config=_config(),
    )

    assert verdict.verdict == "inactive_timeout"
    assert verdict.reason_codes == ("activity_timeout",)
    assert verdict.heartbeat_digest == heartbeat.digest
    assert verdict.activity_digest == activity.digest


def test_stale_run_detector_returns_lease_expired_before_other_stale_reasons() -> None:
    lease = _lease(expires_at="2026-05-27T12:05:00Z")
    activity = _activity(emitted_at="2026-05-27T12:00:10Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:00:20Z",
        last_activity_at="2026-05-27T12:00:10Z",
        last_activity_digest=activity.digest,
    )
    record = _record(lease=lease, activities=(activity,), heartbeats=(heartbeat,))

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:06:00Z"),
        config=_config(),
    )

    assert verdict.verdict == "lease_expired"
    assert verdict.reason_codes == ("lease_expired",)


def test_stale_run_detector_returns_worker_lost_for_missing_recent_heartbeat() -> None:
    activity = _activity(emitted_at="2026-05-27T12:00:10Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:00:20Z",
        last_activity_at="2026-05-27T12:00:10Z",
        last_activity_digest=activity.digest,
    )
    record = _record(activities=(activity,), heartbeats=(heartbeat,))

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:11:00Z"),
        config=_config(),
    )

    assert verdict.verdict == "worker_lost"
    assert verdict.reason_codes == ("worker_lost",)


def test_stale_run_detector_returns_rollback_required_for_rollback_phase() -> None:
    lease = _lease(phase="rollback_required")
    activity = _activity(emitted_at="2026-05-27T12:01:00Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:01:10Z",
        last_activity_at="2026-05-27T12:01:00Z",
        last_activity_digest=activity.digest,
        phase="rollback_required",
    )
    record = _record(lease=lease, activities=(activity,), heartbeats=(heartbeat,))

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:01:30Z"),
        config=_config(),
    )

    assert verdict.verdict == "rollback_required"
    assert verdict.reason_codes == ("rollback_phase",)


def test_stale_run_detector_returns_blocked_for_operator_for_pending_approvals() -> None:
    activity = _activity(emitted_at="2026-05-27T12:01:00Z")
    heartbeat = _heartbeat(
        emitted_at="2026-05-27T12:01:10Z",
        last_activity_at="2026-05-27T12:01:00Z",
        last_activity_digest=activity.digest,
        pending_approval_ids=("approval:manual-check",),
    )
    record = _record(activities=(activity,), heartbeats=(heartbeat,))

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:02:00Z"),
        config=_config(),
    )

    assert verdict.verdict == "blocked_for_operator"
    assert verdict.reason_codes == ("pending_operator_approval",)


def test_stale_run_detector_returns_cancelled_for_cancelled_record() -> None:
    lease = _lease()
    store = LocalFakeHeartbeatStore()
    store.acquire_lease(lease, now=lease.lease_acquired_at)
    record = store.mark_cancelled(
        run_id="run:alpha",
        lease_id="lease:primary",
        worker_id="worker:west-1",
        expected_fencing_token=lease.fencing_token,
    )

    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:01:00Z"),
        config=_config(),
    )

    assert verdict.verdict == "cancelled"
    assert verdict.reason_codes == ("run_cancelled",)
