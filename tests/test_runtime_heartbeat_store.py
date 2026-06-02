from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from openmagi_core_agent.runtime.heartbeat_contract import (
    ActivityReceipt,
    HeartbeatReceipt,
    RunLease,
    activity_receipt_digest,
    heartbeat_receipt_digest,
)
from openmagi_core_agent.runtime.heartbeat_store import (
    HeartbeatStoreError,
    LocalFakeHeartbeatStore,
    require_local_fake_heartbeat_store,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _activity_digest(character: str) -> str:
    return "sha256:activity:" + character * 64


def _lease(
    *,
    run_id: str = "run:alpha",
    worker_id: str = "worker:west-1",
    lease_id: str = "lease:primary",
    generation: int = 1,
    fencing_token: str | None = None,
    acquired_at: str = "2026-05-27T12:00:00Z",
    expires_at: str = "2026-05-27T12:05:00Z",
) -> RunLease:
    return RunLease.model_validate(
        {
            "runId": run_id,
            "turnId": "turn:001",
            "sessionKey": "sess:alpha",
            "workerId": worker_id,
            "leaseId": lease_id,
            "leaseAcquiredAt": acquired_at,
            "leaseExpiresAt": expires_at,
            "phase": "running",
            "activeBoundary": "turn-controller",
            "authorityScope": "runtime-contract-default-off",
            "generation": generation,
            "fencingToken": fencing_token or _digest("a"),
        }
    )


def _activity(
    *,
    run_id: str = "run:alpha",
    lease_id: str = "lease:primary",
    sequence: int = 1,
    emitted_at: str = "2026-05-27T12:00:10Z",
    activity_id: str | None = None,
) -> ActivityReceipt:
    payload: dict[str, object] = {
        "activityId": activity_id or f"activity:{sequence:03d}",
        "runId": run_id,
        "leaseId": lease_id,
        "sequence": sequence,
        "emittedAt": emitted_at,
        "activityType": "tool_event",
        "activityRef": f"event:activity-{sequence:03d}",
        "metadata": {"source": "runtime", "count": sequence},
        "publicSafe": True,
    }
    payload["digest"] = activity_receipt_digest(payload)
    return ActivityReceipt.model_validate(payload)


def _heartbeat(
    *,
    run_id: str = "run:alpha",
    lease_id: str = "lease:primary",
    sequence: int = 1,
    last_activity_digest: str | None = None,
    emitted_at: str = "2026-05-27T12:00:20Z",
    last_activity_at: str = "2026-05-27T12:00:10Z",
) -> HeartbeatReceipt:
    payload: dict[str, object] = {
        "heartbeatId": f"heartbeat:{sequence:03d}",
        "runId": run_id,
        "leaseId": lease_id,
        "sequence": sequence,
        "emittedAt": emitted_at,
        "lastActivityAt": last_activity_at,
        "lastActivityReceiptDigest": last_activity_digest or _activity_digest("b"),
        "phase": "running",
        "publicSafe": True,
    }
    payload["digest"] = heartbeat_receipt_digest(payload)
    return HeartbeatReceipt.model_validate(payload)


def test_local_fake_store_acquires_renews_releases_and_records_receipts() -> None:
    store = LocalFakeHeartbeatStore()
    lease = _lease()

    acquired = store.acquire_lease(lease, now=datetime(2026, 5, 27, 12, 0, tzinfo=UTC))
    assert acquired == lease

    activity = store.append_activity(_activity(), expected_fencing_token=lease.fencing_token)
    heartbeat = store.append_heartbeat(
        _heartbeat(last_activity_digest=activity.digest),
        expected_fencing_token=lease.fencing_token,
    )

    renewed = _lease(
        generation=2,
        fencing_token=_digest("b"),
        acquired_at="2026-05-27T12:01:00Z",
        expires_at="2026-05-27T12:06:00Z",
    )
    assert store.renew_lease(
        renewed,
        expected_fencing_token=lease.fencing_token,
        now=datetime(2026, 5, 27, 12, 1, tzinfo=UTC),
    ) == renewed

    snapshot = store.get_run("run:alpha")
    assert snapshot is not None
    assert snapshot.status == "active"
    assert snapshot.lease == renewed
    assert snapshot.activities == (activity,)
    assert snapshot.heartbeats == (heartbeat,)
    assert snapshot.openmagi_local_fake_provider is True
    assert snapshot.public_projection()["trafficAttached"] is False
    assert snapshot.public_projection()["trustedLeaseAuthority"] is False
    assert snapshot.public_projection()["schedulerAttached"] is False
    assert snapshot.public_projection()["modelCallEnabled"] is False
    assert snapshot.public_projection()["channelDeliveryEnabled"] is False
    assert snapshot.public_projection()["workspaceMutationEnabled"] is False
    assert snapshot.public_projection()["memoryWriteEnabled"] is False

    released = store.release_lease(
        run_id="run:alpha",
        lease_id="lease:primary",
        worker_id="worker:west-1",
        expected_fencing_token=renewed.fencing_token,
    )
    assert released.status == "released"

    with pytest.raises(HeartbeatStoreError, match="not active"):
        store.append_activity(
            _activity(sequence=2, emitted_at="2026-05-27T12:02:00Z"),
            expected_fencing_token=renewed.fencing_token,
        )


def test_local_fake_store_marks_completed_and_cancelled_terminal_states() -> None:
    store = LocalFakeHeartbeatStore()
    store.acquire_lease(_lease(run_id="run:complete"))
    completed = store.mark_completed(
        run_id="run:complete",
        lease_id="lease:primary",
        worker_id="worker:west-1",
        expected_fencing_token=_digest("a"),
    )
    assert completed.status == "completed"

    store.acquire_lease(_lease(run_id="run:cancel", lease_id="lease:cancel"))
    cancelled = store.mark_cancelled(
        run_id="run:cancel",
        lease_id="lease:cancel",
        worker_id="worker:west-1",
        expected_fencing_token=_digest("a"),
    )
    assert cancelled.status == "cancelled"

    with pytest.raises(HeartbeatStoreError, match="not active"):
        store.append_activity(
            _activity(run_id="run:complete", sequence=1),
            expected_fencing_token=_digest("a"),
        )


def test_local_fake_store_rejects_untrusted_store_implementations() -> None:
    class UntrustedStore:
        openmagi_local_fake_provider = False

    store = LocalFakeHeartbeatStore()
    assert require_local_fake_heartbeat_store(store) is store

    with pytest.raises(HeartbeatStoreError, match="untrusted"):
        require_local_fake_heartbeat_store(UntrustedStore())
    with pytest.raises(HeartbeatStoreError, match="untrusted"):
        require_local_fake_heartbeat_store(object())


def test_local_fake_store_rejects_duplicate_active_and_expired_leases() -> None:
    store = LocalFakeHeartbeatStore()
    lease = _lease()
    store.acquire_lease(lease)

    with pytest.raises(HeartbeatStoreError, match="duplicate active lease"):
        store.acquire_lease(lease)
    with pytest.raises(HeartbeatStoreError, match="duplicate active lease"):
        store.acquire_lease(_lease(worker_id="worker:east-1", lease_id="lease:other"))

    expired_store = LocalFakeHeartbeatStore()
    with pytest.raises(HeartbeatStoreError, match="expired lease"):
        expired_store.acquire_lease(
            _lease(
                run_id="run:expired",
                acquired_at="2026-05-27T11:00:00Z",
                expires_at="2026-05-27T11:05:00Z",
            ),
            now=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
        )

    with pytest.raises(HeartbeatStoreError, match="expired lease"):
        store.release_lease(
            run_id="run:alpha",
            lease_id="lease:primary",
            worker_id="worker:west-1",
            expected_fencing_token=lease.fencing_token,
            now=datetime(2026, 5, 27, 12, 6, tzinfo=UTC),
        )


def test_local_fake_store_rejects_wrong_worker_and_stale_fencing_renewal() -> None:
    store = LocalFakeHeartbeatStore()
    lease = _lease()
    store.acquire_lease(lease)

    with pytest.raises(HeartbeatStoreError, match="stale fencing token"):
        store.renew_lease(
            _lease(generation=2, fencing_token=_digest("b")),
            expected_fencing_token=_digest("9"),
        )

    with pytest.raises(HeartbeatStoreError, match="lease owner mismatch"):
        store.renew_lease(
            _lease(worker_id="worker:east-1", generation=2, fencing_token=_digest("b")),
            expected_fencing_token=lease.fencing_token,
        )

    with pytest.raises(HeartbeatStoreError, match="generation"):
        store.renew_lease(
            _lease(generation=1, fencing_token=_digest("b")),
            expected_fencing_token=lease.fencing_token,
        )

    with pytest.raises(HeartbeatStoreError, match="fencing token"):
        store.renew_lease(
            _lease(generation=2, fencing_token=lease.fencing_token),
            expected_fencing_token=lease.fencing_token,
        )

    with pytest.raises(HeartbeatStoreError, match="lease owner mismatch"):
        store.release_lease(
            run_id="run:alpha",
            lease_id="lease:primary",
            worker_id="worker:east-1",
            expected_fencing_token=lease.fencing_token,
        )

    renewed = _lease(
        generation=2,
        fencing_token=_digest("b"),
        acquired_at="2026-05-27T12:01:00Z",
        expires_at="2026-05-27T12:06:00Z",
    )
    store.renew_lease(renewed, expected_fencing_token=lease.fencing_token)
    with pytest.raises(HeartbeatStoreError, match="stale fencing token"):
        store.append_activity(
            _activity(sequence=1, emitted_at="2026-05-27T12:01:10Z"),
            expected_fencing_token=lease.fencing_token,
        )
    store.append_activity(
        _activity(sequence=1, emitted_at="2026-05-27T12:01:10Z"),
        expected_fencing_token=renewed.fencing_token,
    )


def test_local_fake_store_enforces_monotonic_receipt_sequences_and_known_activity() -> None:
    store = LocalFakeHeartbeatStore()
    store.acquire_lease(_lease())

    with pytest.raises(HeartbeatStoreError, match="activity receipt required"):
        store.append_heartbeat(_heartbeat(), expected_fencing_token=_digest("a"))

    first_activity = store.append_activity(_activity(sequence=1), expected_fencing_token=_digest("a"))
    store.append_heartbeat(
        _heartbeat(sequence=1, last_activity_digest=first_activity.digest),
        expected_fencing_token=_digest("a"),
    )

    with pytest.raises(HeartbeatStoreError, match="activity sequence"):
        store.append_activity(
            _activity(sequence=1, activity_id="activity:duplicate"),
            expected_fencing_token=_digest("a"),
        )
    with pytest.raises(HeartbeatStoreError, match="heartbeat sequence"):
        store.append_heartbeat(
            _heartbeat(sequence=1, last_activity_digest=first_activity.digest),
            expected_fencing_token=_digest("a"),
        )
    with pytest.raises(HeartbeatStoreError, match="unknown activity receipt"):
        store.append_heartbeat(
            _heartbeat(sequence=2, last_activity_digest=_activity_digest("d")),
            expected_fencing_token=_digest("a"),
        )


def test_local_fake_store_returns_append_only_snapshots() -> None:
    store = LocalFakeHeartbeatStore()
    store.acquire_lease(_lease())
    before = store.get_run("run:alpha")
    assert before is not None

    activity = store.append_activity(_activity(), expected_fencing_token=_digest("a"))
    after = store.get_run("run:alpha")
    assert after is not None

    assert before.activities == ()
    assert after.activities == (activity,)
    with pytest.raises(FrozenInstanceError):
        before.activities += (activity,)

    reread = store.get_run("run:alpha")
    assert reread is not None
    assert reread.activities == (activity,)
