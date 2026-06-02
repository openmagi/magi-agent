from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.ops.job_queue import (
    AgentJobQueue,
    JobQueueAuthorityFlags,
    JobQueueConfig,
    JobQueueReceipt,
    enqueue_job,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def test_disabled_queue_records_intent_without_enqueuing() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=False))

    result = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="research",
        idempotency_key="idem-1",
        payload_digest=_digest("1"),
        now_ms=100,
    )

    assert result.status == "disabled"
    assert result.job is None
    assert "job_queue_disabled" in result.reason_codes
    assert queue.jobs() == ()
    assert queue.receipts()[0].status == "disabled"
    assert queue.receipts()[0].production_worker_attached is False
    assert queue.receipts()[0].production_write is False
    assert queue.receipts()[0].live_tool_execution is False
    assert queue.receipts()[0].traffic_attached is False


def test_queue_idempotency_returns_existing_job_without_raw_idempotency_key() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True))

    first = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="coding",
        idempotency_key="idem-1",
        payload_digest=_digest("2"),
        now_ms=100,
    )
    second = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="coding",
        idempotency_key="idem-1",
        payload_digest=_digest("2"),
        now_ms=200,
    )

    assert first.status == "queued"
    assert second.status == "duplicate"
    assert first.job is not None
    assert second.job is not None
    assert second.job.job_id == first.job.job_id
    encoded = json.dumps(second.model_dump(by_alias=True, mode="json"), sort_keys=True)
    assert "idem-1" not in encoded
    assert second.receipt.idempotency_key_digest.startswith("sha256:")


def test_idempotency_conflict_blocks_without_silent_overwrite() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True))

    first = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="automation",
        idempotency_key="idem-1",
        payload_digest=_digest("3"),
        now_ms=100,
    )
    conflict = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="automation",
        idempotency_key="idem-1",
        payload_digest=_digest("4"),
        now_ms=200,
    )

    assert first.job is not None
    assert conflict.status == "blocked"
    assert conflict.job == first.job
    assert "job_queue_idempotency_conflict" in conflict.reason_codes
    assert len(queue.jobs()) == 1


def test_idempotency_retry_is_not_blocked_by_pending_capacity() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True, maxPendingJobs=1))

    first = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="research",
        idempotency_key="idem-1",
        payload_digest=_digest("d"),
        now_ms=100,
    )
    duplicate = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="research",
        idempotency_key="idem-1",
        payload_digest=_digest("d"),
        now_ms=200,
    )
    conflict = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="research",
        idempotency_key="idem-1",
        payload_digest=_digest("e"),
        now_ms=300,
    )

    assert first.job is not None
    assert duplicate.status == "duplicate"
    assert duplicate.job is not None
    assert duplicate.job.job_id == first.job.job_id
    assert conflict.status == "blocked"
    assert "job_queue_idempotency_conflict" in conflict.reason_codes
    assert "job_queue_capacity_exceeded" not in duplicate.reason_codes
    assert "job_queue_capacity_exceeded" not in conflict.reason_codes


def test_lease_ack_and_retry_lifecycle() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True, maxAttempts=2))
    enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="automation",
        idempotency_key="idem-1",
        payload_digest=_digest("5"),
        now_ms=100,
    )

    lease = queue.lease_next(worker_id="worker-1", now_ms=1_000)
    assert lease is not None
    assert lease.job.status == "running"
    assert lease.job.attempt_count == 1

    failed = queue.fail(lease.lease_id, reason_code="provider_timeout", now_ms=2_000)
    assert failed.status == "queued"
    assert failed.attempt_count == 1
    assert "job_retry_queued" in failed.reason_codes

    lease2 = queue.lease_next(worker_id="worker-1", now_ms=3_000)
    assert lease2 is not None
    completed = queue.ack(lease2.lease_id, result_digest=_digest("6"), now_ms=4_000)
    assert completed.status == "completed"
    assert completed.result_digest == _digest("6")
    assert queue.receipts()[-1].status == "completed"


def test_dead_letter_after_max_attempts() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True, maxAttempts=1))
    enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="eval",
        idempotency_key="idem-1",
        payload_digest=_digest("7"),
        now_ms=100,
    )

    lease = queue.lease_next(worker_id="worker-1", now_ms=1_000)
    assert lease is not None
    dead = queue.fail(lease.lease_id, reason_code="provider_timeout", now_ms=2_000)

    assert dead.status == "dead_lettered"
    assert "job_dead_lettered" in dead.reason_codes
    assert queue.receipts()[-1].status == "dead_lettered"


def test_completed_jobs_do_not_consume_pending_capacity() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True, maxPendingJobs=1))
    enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="coding",
        idempotency_key="idem-1",
        payload_digest=_digest("0"),
        now_ms=100,
    )
    lease = queue.lease_next(worker_id="worker-1", now_ms=200)
    assert lease is not None
    queue.ack(lease.lease_id, result_digest=_digest("1"), now_ms=300)

    next_result = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="coding",
        idempotency_key="idem-2",
        payload_digest=_digest("2"),
        now_ms=400,
    )

    assert next_result.status == "queued"
    assert "job_queue_capacity_exceeded" not in next_result.reason_codes


def test_fail_rejects_expired_lease_without_mutating_timeout_lifecycle() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True, leaseTimeoutMs=1_000))
    enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="automation",
        idempotency_key="idem-1",
        payload_digest=_digest("3"),
        now_ms=100,
    )
    lease = queue.lease_next(worker_id="worker-1", now_ms=1_000)
    assert lease is not None

    with pytest.raises(ValueError, match="job lease expired before failure"):
        queue.fail(lease.lease_id, reason_code="provider_timeout", now_ms=2_001)

    timed_out = queue.timeout_expired_leases(now_ms=2_001)
    assert len(timed_out) == 1
    assert timed_out[0].status == "timed_out"
    assert queue.get(lease.job.job_id).status == "queued"


def test_cancel_cannot_overwrite_terminal_lifecycle_state() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True))
    enqueued = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="delivery",
        idempotency_key="idem-1",
        payload_digest=_digest("4"),
        now_ms=100,
    )
    assert enqueued.job is not None
    lease = queue.lease_next(worker_id="worker-1", now_ms=200)
    assert lease is not None
    completed = queue.ack(lease.lease_id, result_digest=_digest("5"), now_ms=300)
    receipt_count = len(queue.receipts())

    with pytest.raises(ValueError, match="terminal job cannot be cancelled"):
        queue.cancel(completed.job_id, reason_code="user_cancelled", now_ms=400)

    assert queue.get(completed.job_id).status == "completed"
    assert len(queue.receipts()) == receipt_count


def test_kill_switch_blocks_new_enqueues_and_leases() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True))
    enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="research",
        idempotency_key="idem-1",
        payload_digest=_digest("8"),
        now_ms=100,
    )
    queue.kill_switch_enabled = True

    blocked = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="research",
        idempotency_key="idem-2",
        payload_digest=_digest("9"),
        now_ms=200,
    )

    assert blocked.status == "blocked"
    assert "job_queue_kill_switch_enabled" in blocked.reason_codes
    assert queue.lease_next(worker_id="worker-1", now_ms=1_000) is None


def test_cancel_and_timeout_emit_digest_only_receipts() -> None:
    queue = AgentJobQueue(config=JobQueueConfig(enabled=True, leaseTimeoutMs=1_000))
    enqueued = enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="delivery",
        idempotency_key="idem-1",
        payload_digest=_digest("a"),
        now_ms=100,
    )
    assert enqueued.job is not None
    cancelled = queue.cancel(enqueued.job.job_id, reason_code="user_cancelled", now_ms=200)
    assert cancelled.status == "cancelled"
    assert queue.receipts()[-1].status == "cancelled"

    enqueue_job(
        queue,
        tenant_id="tenant-1",
        bot_id="bot-1",
        job_kind="delivery",
        idempotency_key="idem-2",
        payload_digest=_digest("b"),
        now_ms=300,
    )
    lease = queue.lease_next(worker_id="worker-1", now_ms=1_000)
    assert lease is not None
    timed_out = queue.timeout_expired_leases(now_ms=2_001)

    assert len(timed_out) == 1
    assert timed_out[0].status == "timed_out"
    encoded_receipts = json.dumps(
        [receipt.model_dump(by_alias=True, mode="json") for receipt in queue.receipts()],
        sort_keys=True,
    )
    assert "worker-1" not in encoded_receipts
    assert "idem-1" not in encoded_receipts
    assert "idem-2" not in encoded_receipts
    assert "user_cancelled" in encoded_receipts


def test_forced_authority_flags_remain_false() -> None:
    flags = JobQueueAuthorityFlags(
        productionWorkerAttached=True,
        productionWrite=True,
        liveToolExecution=True,
        trafficAttached=True,
        userVisibleOutputEnabled=True,
        databaseMutationAllowed=True,
        networkCallAllowed=True,
    )

    assert flags.production_worker_attached is False
    assert flags.production_write is False
    assert flags.live_tool_execution is False
    assert flags.traffic_attached is False
    assert flags.user_visible_output_enabled is False
    assert flags.database_mutation_allowed is False
    assert flags.network_call_allowed is False
    with pytest.raises(ValueError, match="model_construct"):
        JobQueueAuthorityFlags.model_construct(productionWorkerAttached=True)
    with pytest.raises(ValueError, match="model_copy update"):
        flags.model_copy(update={"productionWorkerAttached": True})
    with pytest.raises(ValueError, match="copy update"):
        flags.copy(update={"productionWorkerAttached": True})


@pytest.mark.parametrize(
    "rejected",
    (
        "Authorization: Bearer live-token",
        "Cookie: session=secret",
        "/Users/example/.ssh/id_rsa",
        "raw prompt payload",
        "hidden reasoning payload",
        "private.path.ref",
        "connector_token=value",
    ),
)
def test_validation_errors_do_not_echo_private_inputs(rejected: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        enqueue_job(
            AgentJobQueue(config=JobQueueConfig(enabled=True)),
            tenant_id="tenant-1",
            bot_id="bot-1",
            job_kind="research",
            idempotency_key=rejected,
            payload_digest=_digest("c"),
            now_ms=100,
        )

    encoded_error = json.dumps(exc_info.value.errors(), default=str)
    assert rejected not in str(exc_info.value)
    assert rejected not in encoded_error


def test_receipt_validation_errors_do_not_echo_bad_digest_or_private_extra_keys() -> None:
    with pytest.raises(ValidationError) as exc_info:
        JobQueueReceipt(status="queued", requestDigest="private.ref", **{"private.ref": "x"})

    encoded_error = json.dumps(exc_info.value.errors(), default=str)
    assert "private.ref" not in str(exc_info.value)
    assert "private.ref" not in encoded_error


def test_job_queue_import_does_not_attach_live_runtime_surfaces() -> None:
    script = """
import importlib
import json
import sys

before = set(sys.modules)
importlib.import_module("magi_agent.ops.job_queue")
imported = set(sys.modules) - before
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.browser",
    "magi_agent.channels",
    "magi_agent.memory",
    "magi_agent.plugins",
    "magi_agent.tools",
    "magi_agent.transport",
    "magi_agent.web_acquisition",
    "magi_agent.workspace",
    "magi_agent.shadow",
    "kubernetes",
)
blocked = sorted(
    module
    for module in imported
    if any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in forbidden_prefixes
    )
)
print(json.dumps(blocked))
"""
    output = subprocess.check_output([sys.executable, "-c", script], text=True)
    assert json.loads(output) == []
