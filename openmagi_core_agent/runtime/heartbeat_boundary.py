from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from typing import Literal, Protocol

from openmagi_core_agent.runtime.heartbeat_contract import (
    ActivityReceipt,
    HeartbeatReceipt,
    RunLease,
    activity_receipt_digest,
    heartbeat_receipt_digest,
)
from openmagi_core_agent.runtime.heartbeat_store import (
    HeartbeatRunRecord,
    HeartbeatStorePort,
    require_local_fake_heartbeat_store,
)


RuntimeHeartbeatBoundaryStatus = Literal[
    "disabled",
    "not_started",
    "ignored",
    "ignored_heartbeat",
    "lease_acquired",
    "activity_appended",
    "heartbeat_appended",
    "heartbeat_without_activity",
]

_PUBLIC_HEARTBEAT_TYPES = {"heartbeat", "runtime.heartbeat"}
_ACTIVITY_TYPES_BY_EVENT_TYPE = {
    "text_delta": "model_event",
    "token": "model_event",
    "model.message.delta": "model_event",
    "model.message.completed": "model_event",
    "tool_result": "tool_event",
    "tool_end": "tool_event",
    "tool.call.started": "tool_event",
    "tool.call.progress": "tool_event",
    "tool.call.needs_approval": "tool_event",
    "tool.call.completed": "tool_event",
    "tool.call.denied": "tool_event",
    "tool.call.failed": "tool_event",
    "source.inspected": "source_inspected",
    "source_inspected": "source_inspected",
    "child.started": "child_event",
    "child.progress": "child_event",
    "child.completed": "child_event",
    "child.cancelled": "child_event",
    "child.failed": "child_event",
    "runtime.activity": "runtime_activity",
    "activity": "runtime_activity",
}
_SAFE_EVENT_REF_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


class RuntimeHeartbeatBoundaryStore(HeartbeatStorePort, Protocol):
    def get_run(self, run_id: str) -> HeartbeatRunRecord | None: ...


@dataclass(frozen=True)
class RuntimeHeartbeatBoundaryConfig:
    enabled: bool = False
    default_phase: str = "running"

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a strict boolean")
        if not self.default_phase:
            raise ValueError("default_phase is required")


@dataclass(frozen=True)
class RuntimeHeartbeatBoundaryResult:
    status: RuntimeHeartbeatBoundaryStatus
    activity_receipt: ActivityReceipt | None = None
    heartbeat_receipt: HeartbeatReceipt | None = None

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.runtime.heartbeat.boundary.result.public.v1",
            "status": self.status,
            "activityDigest": self.activity_receipt.digest
            if self.activity_receipt is not None
            else None,
            "heartbeatDigest": self.heartbeat_receipt.digest
            if self.heartbeat_receipt is not None
            else None,
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


class RuntimeHeartbeatBoundary:
    """Default-off bridge from turn-visible activity events to local fake receipts."""

    def __init__(
        self,
        *,
        store: RuntimeHeartbeatBoundaryStore,
        lease: RunLease,
        config: RuntimeHeartbeatBoundaryConfig | None = None,
    ) -> None:
        self._store = require_local_fake_heartbeat_store(store)
        self._lease = RunLease.model_validate(lease.model_dump(by_alias=True, mode="json"))
        self._config = config or RuntimeHeartbeatBoundaryConfig()
        self._started = False

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def start(self, *, now: datetime | None = None) -> RuntimeHeartbeatBoundaryResult:
        if not self.enabled:
            return RuntimeHeartbeatBoundaryResult(status="disabled")
        if not self._started:
            self._store.acquire_lease(self._lease, now=now or self._lease.lease_acquired_at)
            self._started = True
        return RuntimeHeartbeatBoundaryResult(status="lease_acquired")

    def consume_event(
        self,
        event: Mapping[str, object],
        *,
        emitted_at: datetime | None = None,
    ) -> RuntimeHeartbeatBoundaryResult:
        if not self.enabled:
            return RuntimeHeartbeatBoundaryResult(status="disabled")
        if not self._started:
            return RuntimeHeartbeatBoundaryResult(status="not_started")

        event_type = _event_type(event)
        if _is_public_heartbeat_event(event_type):
            return RuntimeHeartbeatBoundaryResult(status="ignored_heartbeat")

        activity_type = _activity_type_for_event(event_type)
        if activity_type is None:
            return RuntimeHeartbeatBoundaryResult(status="ignored")

        record = self._store.get_run(self._lease.run_id)
        sequence = len(record.activities) + 1 if record is not None else 1
        safe_emitted_at = _event_emitted_at(event, emitted_at=emitted_at)
        payload: dict[str, object] = {
            "activityId": f"activity:{sequence:06d}",
            "runId": self._lease.run_id,
            "leaseId": self._lease.lease_id,
            "sequence": sequence,
            "emittedAt": _json_datetime(safe_emitted_at),
            "activityType": activity_type,
            "activityRef": _activity_ref(event_type, sequence),
            "metadata": {
                "count": sequence,
                "eventKind": activity_type,
                "source": "runtime_boundary",
            },
            "publicSafe": True,
        }
        payload["digest"] = activity_receipt_digest(payload)
        receipt = ActivityReceipt.model_validate(payload)
        appended = self._store.append_activity(
            receipt,
            expected_fencing_token=self._lease.fencing_token,
        )
        return RuntimeHeartbeatBoundaryResult(
            status="activity_appended",
            activity_receipt=appended,
        )

    def emit_runtime_heartbeat(
        self,
        *,
        emitted_at: datetime | None = None,
        phase: str | None = None,
        pending_approval_ids: tuple[str, ...] = (),
    ) -> RuntimeHeartbeatBoundaryResult:
        if not self.enabled:
            return RuntimeHeartbeatBoundaryResult(status="disabled")
        if not self._started:
            return RuntimeHeartbeatBoundaryResult(status="not_started")

        record = self._store.get_run(self._lease.run_id)
        if record is None or not record.activities:
            return RuntimeHeartbeatBoundaryResult(status="heartbeat_without_activity")

        latest_activity = record.activities[-1]
        sequence = len(record.heartbeats) + 1
        safe_emitted_at = _utc(emitted_at or datetime.now(UTC))
        if safe_emitted_at <= latest_activity.emitted_at:
            safe_emitted_at = latest_activity.emitted_at + timedelta(milliseconds=1)

        payload: dict[str, object] = {
            "heartbeatId": f"heartbeat:{sequence:06d}",
            "runId": self._lease.run_id,
            "leaseId": self._lease.lease_id,
            "sequence": sequence,
            "emittedAt": _json_datetime(safe_emitted_at),
            "lastActivityAt": _json_datetime(latest_activity.emitted_at),
            "lastActivityReceiptDigest": latest_activity.digest,
            "phase": phase or self._config.default_phase,
            "pendingApprovalIds": list(pending_approval_ids),
            "publicSafe": True,
        }
        payload["digest"] = heartbeat_receipt_digest(payload)
        receipt = HeartbeatReceipt.model_validate(payload)
        appended = self._store.append_heartbeat(
            receipt,
            expected_fencing_token=self._lease.fencing_token,
        )
        return RuntimeHeartbeatBoundaryResult(
            status="heartbeat_appended",
            heartbeat_receipt=appended,
        )


def _event_type(event: Mapping[str, object]) -> str:
    value = event.get("type")
    return str(value).strip() if isinstance(value, str) else ""


def _is_public_heartbeat_event(event_type: str) -> bool:
    normalized = event_type.strip().lower()
    return normalized in _PUBLIC_HEARTBEAT_TYPES


def _activity_type_for_event(event_type: str) -> str | None:
    normalized = event_type.strip().lower()
    return _ACTIVITY_TYPES_BY_EVENT_TYPE.get(normalized)


def _activity_ref(event_type: str, sequence: int) -> str:
    safe_type = _SAFE_EVENT_REF_RE.sub("-", event_type.strip().lower()).strip("-.:")
    if not safe_type:
        safe_type = "event"
    return f"event:{safe_type}-{sequence:06d}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("runtime heartbeat boundary timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _event_emitted_at(
    event: Mapping[str, object],
    *,
    emitted_at: datetime | None,
) -> datetime:
    if emitted_at is not None:
        return _utc(emitted_at)
    for key in ("emittedAt", "createdAt"):
        value = event.get(key)
        if isinstance(value, datetime):
            return _utc(value)
        if isinstance(value, str):
            return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    return datetime.now(UTC)


def _json_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
