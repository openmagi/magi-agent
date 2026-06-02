from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib

from magi_agent.runtime.heartbeat_contract import StaleRunVerdict
from magi_agent.runtime.heartbeat_store import HeartbeatRunRecord


@dataclass(frozen=True)
class StaleRunDetectorConfig:
    heartbeat_silence_after_seconds: int = 60
    inactivity_timeout_seconds: int = 300
    worker_lost_after_seconds: int = 900
    rollback_phases: tuple[str, ...] = (
        "rollback",
        "rollback_pending",
        "rollback_required",
        "rolling_back",
    )

    def __post_init__(self) -> None:
        _require_positive_seconds(
            self.heartbeat_silence_after_seconds,
            field_name="heartbeat_silence_after_seconds",
        )
        _require_positive_seconds(
            self.inactivity_timeout_seconds,
            field_name="inactivity_timeout_seconds",
        )
        _require_positive_seconds(
            self.worker_lost_after_seconds,
            field_name="worker_lost_after_seconds",
        )
        if self.worker_lost_after_seconds <= self.heartbeat_silence_after_seconds:
            raise ValueError(
                "worker_lost_after_seconds must exceed heartbeat_silence_after_seconds"
            )


def evaluate_stale_run(
    record: HeartbeatRunRecord,
    *,
    checked_at: datetime,
    config: StaleRunDetectorConfig | None = None,
) -> StaleRunVerdict:
    detector_config = config or StaleRunDetectorConfig()
    safe_checked_at = _utc(checked_at)
    latest_heartbeat = record.heartbeats[-1] if record.heartbeats else None
    latest_activity = record.activities[-1] if record.activities else None
    phase = latest_heartbeat.phase if latest_heartbeat is not None else record.lease.phase

    heartbeat_age = _age_seconds(
        safe_checked_at,
        (
            latest_heartbeat.emitted_at
            if latest_heartbeat is not None
            else record.lease.lease_acquired_at
        ),
    )
    activity_age = _age_seconds(
        safe_checked_at,
        (
            latest_activity.emitted_at
            if latest_activity is not None
            else record.lease.lease_acquired_at
        ),
    )

    verdict = "healthy"
    reason_codes: tuple[str, ...] = ("healthy",)

    if record.status == "cancelled":
        verdict = "cancelled"
        reason_codes = ("run_cancelled",)
    elif record.status == "released":
        verdict = "resume_pending"
        reason_codes = ("lease_released",)
    elif safe_checked_at >= record.lease.lease_expires_at:
        verdict = "lease_expired"
        reason_codes = ("lease_expired",)
    elif latest_heartbeat is not None and latest_heartbeat.pending_approval_ids:
        verdict = "blocked_for_operator"
        reason_codes = ("pending_operator_approval",)
    elif phase in detector_config.rollback_phases:
        verdict = "rollback_required"
        reason_codes = ("rollback_phase",)
    elif heartbeat_age >= detector_config.worker_lost_after_seconds:
        verdict = "worker_lost"
        reason_codes = ("worker_lost",)
    elif activity_age >= detector_config.inactivity_timeout_seconds:
        verdict = "inactive_timeout"
        reason_codes = ("activity_timeout",)
    elif heartbeat_age >= detector_config.heartbeat_silence_after_seconds:
        verdict = "silent_but_within_threshold"
        reason_codes = ("heartbeat_silent",)

    return StaleRunVerdict(
        verdict=verdict,
        runId=record.lease.run_id,
        checkedAt=safe_checked_at,
        reasonCodes=reason_codes,
        heartbeatDigest=latest_heartbeat.digest if latest_heartbeat is not None else None,
        activityDigest=latest_activity.digest if latest_activity is not None else None,
        leaseDigest=_digest_text(record.lease.lease_id),
        metadata={
            "activityAgeSeconds": activity_age,
            "heartbeatAgeSeconds": heartbeat_age,
            "phaseName": phase,
            "recordStatus": record.status,
            "source": "stale_detector",
        },
    )


def _require_positive_seconds(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("stale detector timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _age_seconds(checked_at: datetime, previous_at: datetime) -> int:
    return max(0, int((checked_at - previous_at.astimezone(UTC)).total_seconds()))


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
