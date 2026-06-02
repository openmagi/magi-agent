from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol, TypeVar

from openmagi_core_agent.runtime.heartbeat_contract import (
    ActivityReceipt,
    HeartbeatReceipt,
    RunLease,
)


HeartbeatRunStatus = Literal["active", "released", "completed", "cancelled"]
_StoreT = TypeVar("_StoreT", bound=object)


class HeartbeatStoreError(ValueError):
    """Raised when the local fake heartbeat store rejects a runtime mutation."""


class HeartbeatStorePort(Protocol):
    openmagi_local_fake_provider: bool

    def acquire_lease(self, lease: RunLease, *, now: datetime | None = None) -> RunLease: ...

    def renew_lease(
        self,
        lease: RunLease,
        *,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> RunLease: ...

    def append_heartbeat(
        self,
        receipt: HeartbeatReceipt,
        *,
        expected_fencing_token: str,
    ) -> HeartbeatReceipt: ...

    def append_activity(
        self,
        receipt: ActivityReceipt,
        *,
        expected_fencing_token: str,
    ) -> ActivityReceipt: ...

    def release_lease(
        self,
        *,
        run_id: str,
        lease_id: str,
        worker_id: str,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> HeartbeatRunRecord: ...

    def mark_completed(
        self,
        *,
        run_id: str,
        lease_id: str,
        worker_id: str,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> HeartbeatRunRecord: ...

    def mark_cancelled(
        self,
        *,
        run_id: str,
        lease_id: str,
        worker_id: str,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> HeartbeatRunRecord: ...


@dataclass(frozen=True)
class HeartbeatRunRecord:
    lease: RunLease
    status: HeartbeatRunStatus
    heartbeats: tuple[HeartbeatReceipt, ...] = ()
    activities: tuple[ActivityReceipt, ...] = ()
    openmagi_local_fake_provider: bool = True

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.runtime.heartbeat.store.run.public.v1",
            "runId": self.lease.run_id,
            "status": self.status,
            "lease": self.lease.public_projection(),
            "heartbeatDigests": [heartbeat.digest for heartbeat in self.heartbeats],
            "activityDigests": [activity.digest for activity in self.activities],
            "heartbeatCount": len(self.heartbeats),
            "activityCount": len(self.activities),
            "openmagiLocalFakeProvider": True,
            "liveAuthority": False,
            "trafficAttached": False,
            "trustedLeaseAuthority": False,
            "schedulerAttached": False,
            "modelCallEnabled": False,
            "channelDeliveryEnabled": False,
            "workspaceMutationEnabled": False,
            "memoryWriteEnabled": False,
            "authorityProof": "requires_trusted_lease_store",
        }


class LocalFakeHeartbeatStore:
    """Process-local fake store for default-off heartbeat/lease contract tests."""

    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self._records: dict[str, HeartbeatRunRecord] = {}

    def acquire_lease(self, lease: RunLease, *, now: datetime | None = None) -> RunLease:
        safe_lease = RunLease.model_validate(lease.model_dump(by_alias=True, mode="json"))
        self._reject_expired_lease(safe_lease, now=now)
        existing = self._records.get(safe_lease.run_id)
        if existing is not None and existing.status == "active":
            raise HeartbeatStoreError("duplicate active lease")
        if existing is not None:
            raise HeartbeatStoreError("run already has terminal heartbeat store state")
        self._records[safe_lease.run_id] = HeartbeatRunRecord(
            lease=safe_lease,
            status="active",
        )
        return safe_lease

    def renew_lease(
        self,
        lease: RunLease,
        *,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> RunLease:
        safe_lease = RunLease.model_validate(lease.model_dump(by_alias=True, mode="json"))
        record = self._require_active(safe_lease.run_id)
        self._require_same_owner(
            record,
            lease_id=safe_lease.lease_id,
            worker_id=safe_lease.worker_id,
        )
        if expected_fencing_token != record.lease.fencing_token:
            raise HeartbeatStoreError("stale fencing token")
        if safe_lease.turn_id != record.lease.turn_id or safe_lease.session_key != record.lease.session_key:
            raise HeartbeatStoreError("lease owner mismatch")
        if safe_lease.generation <= record.lease.generation:
            raise HeartbeatStoreError("lease generation must increase")
        if safe_lease.fencing_token == record.lease.fencing_token:
            raise HeartbeatStoreError("fencing token must rotate on renewal")
        self._reject_expired_lease(safe_lease, now=now)
        if safe_lease.lease_expires_at <= record.lease.lease_expires_at:
            raise HeartbeatStoreError("leaseExpiresAt must move forward")
        self._records[safe_lease.run_id] = replace(record, lease=safe_lease)
        return safe_lease

    def append_heartbeat(
        self,
        receipt: HeartbeatReceipt,
        *,
        expected_fencing_token: str,
    ) -> HeartbeatReceipt:
        safe_receipt = HeartbeatReceipt.model_validate(receipt.model_dump(by_alias=True, mode="json"))
        record = self._require_active(safe_receipt.run_id)
        self._require_receipt_lease(record, lease_id=safe_receipt.lease_id)
        self._require_current_fencing(record, expected_fencing_token=expected_fencing_token)
        self._reject_expired_receipt(record, emitted_at=safe_receipt.emitted_at)
        if not record.activities:
            raise HeartbeatStoreError("activity receipt required before heartbeat")
        if safe_receipt.last_activity_receipt_digest not in {
            activity.digest for activity in record.activities
        }:
            raise HeartbeatStoreError("unknown activity receipt digest")
        if record.heartbeats and safe_receipt.sequence <= record.heartbeats[-1].sequence:
            raise HeartbeatStoreError("heartbeat sequence must increase")
        if any(heartbeat.digest == safe_receipt.digest for heartbeat in record.heartbeats):
            raise HeartbeatStoreError("duplicate heartbeat receipt")
        self._records[safe_receipt.run_id] = replace(
            record,
            heartbeats=(*record.heartbeats, safe_receipt),
        )
        return safe_receipt

    def append_activity(
        self,
        receipt: ActivityReceipt,
        *,
        expected_fencing_token: str,
    ) -> ActivityReceipt:
        safe_receipt = ActivityReceipt.model_validate(receipt.model_dump(by_alias=True, mode="json"))
        record = self._require_active(safe_receipt.run_id)
        self._require_receipt_lease(record, lease_id=safe_receipt.lease_id)
        self._require_current_fencing(record, expected_fencing_token=expected_fencing_token)
        self._reject_expired_receipt(record, emitted_at=safe_receipt.emitted_at)
        if record.activities and safe_receipt.sequence <= record.activities[-1].sequence:
            raise HeartbeatStoreError("activity sequence must increase")
        if any(activity.digest == safe_receipt.digest for activity in record.activities):
            raise HeartbeatStoreError("duplicate activity receipt")
        self._records[safe_receipt.run_id] = replace(
            record,
            activities=(*record.activities, safe_receipt),
        )
        return safe_receipt

    def release_lease(
        self,
        *,
        run_id: str,
        lease_id: str,
        worker_id: str,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> HeartbeatRunRecord:
        return self._transition(
            run_id=run_id,
            lease_id=lease_id,
            worker_id=worker_id,
            expected_fencing_token=expected_fencing_token,
            now=now,
            status="released",
        )

    def mark_completed(
        self,
        *,
        run_id: str,
        lease_id: str,
        worker_id: str,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> HeartbeatRunRecord:
        return self._transition(
            run_id=run_id,
            lease_id=lease_id,
            worker_id=worker_id,
            expected_fencing_token=expected_fencing_token,
            now=now,
            status="completed",
        )

    def mark_cancelled(
        self,
        *,
        run_id: str,
        lease_id: str,
        worker_id: str,
        expected_fencing_token: str,
        now: datetime | None = None,
    ) -> HeartbeatRunRecord:
        return self._transition(
            run_id=run_id,
            lease_id=lease_id,
            worker_id=worker_id,
            expected_fencing_token=expected_fencing_token,
            now=now,
            status="cancelled",
        )

    def get_run(self, run_id: str) -> HeartbeatRunRecord | None:
        return self._records.get(run_id)

    def list_runs(self) -> tuple[HeartbeatRunRecord, ...]:
        return tuple(self._records.values())

    def _transition(
        self,
        *,
        run_id: str,
        lease_id: str,
        worker_id: str,
        expected_fencing_token: str,
        now: datetime | None,
        status: HeartbeatRunStatus,
    ) -> HeartbeatRunRecord:
        record = self._require_active(run_id)
        self._require_same_owner(record, lease_id=lease_id, worker_id=worker_id)
        self._require_current_fencing(record, expected_fencing_token=expected_fencing_token)
        if now is not None:
            self._reject_expired_lease(record.lease, now=now)
        next_record = replace(record, status=status)
        self._records[run_id] = next_record
        return next_record

    def _require_active(self, run_id: str) -> HeartbeatRunRecord:
        record = self._records.get(run_id)
        if record is None:
            raise HeartbeatStoreError("run lease not found")
        if record.status != "active":
            raise HeartbeatStoreError("run is not active")
        return record

    @staticmethod
    def _reject_expired_lease(lease: RunLease, *, now: datetime | None) -> None:
        checked_at = _utc(now) if now is not None else lease.lease_acquired_at
        if lease.lease_expires_at <= checked_at:
            raise HeartbeatStoreError("expired lease")

    @staticmethod
    def _reject_expired_receipt(record: HeartbeatRunRecord, *, emitted_at: datetime) -> None:
        if record.lease.lease_expires_at <= emitted_at:
            raise HeartbeatStoreError("expired lease")

    @staticmethod
    def _require_same_owner(record: HeartbeatRunRecord, *, lease_id: str, worker_id: str) -> None:
        if record.lease.lease_id != lease_id or record.lease.worker_id != worker_id:
            raise HeartbeatStoreError("lease owner mismatch")

    @staticmethod
    def _require_receipt_lease(record: HeartbeatRunRecord, *, lease_id: str) -> None:
        if record.lease.lease_id != lease_id:
            raise HeartbeatStoreError("lease owner mismatch")

    @staticmethod
    def _require_current_fencing(
        record: HeartbeatRunRecord,
        *,
        expected_fencing_token: str,
    ) -> None:
        if expected_fencing_token != record.lease.fencing_token:
            raise HeartbeatStoreError("stale fencing token")


def require_local_fake_heartbeat_store(store: _StoreT) -> _StoreT:
    if getattr(store, "openmagi_local_fake_provider", False) is not True:
        raise HeartbeatStoreError("untrusted heartbeat store provider")
    return store


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HeartbeatStoreError("store timestamps must be timezone-aware")
    return value.astimezone(UTC)
