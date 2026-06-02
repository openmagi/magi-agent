from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .safety import (
    require_digest,
    require_safe_ref,
    safe_metadata,
    sanitize_validation_error,
)


JobRecordStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "dead_lettered",
    "cancelled",
]
JobQueueReceiptStatus = Literal[
    "disabled",
    "queued",
    "duplicate",
    "blocked",
    "running",
    "completed",
    "failed",
    "dead_lettered",
    "cancelled",
    "timed_out",
]
EnqueueStatus = Literal["disabled", "queued", "duplicate", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


def _digest_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _safe_reason(value: str) -> str:
    return require_safe_ref(value, field_name="reasonCode")


class _JobQueueModel(BaseModel):
    model_config = _MODEL_CONFIG

    def __init__(self, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=type(self).__name__) from None

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *args: object,
        **kwargs: object,
    ) -> Self:
        try:
            return super().model_validate_json(json_data, *args, **kwargs)
        except ValidationError as exc:
            raise sanitize_validation_error(exc, title=cls.__name__) from None

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: object) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))

    def copy(
        self,
        *,
        include: object = None,
        exclude: object = None,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        if update or include is not None or exclude is not None:
            raise ValueError(f"copy update/include/exclude is disabled for {type(self).__name__}")
        return self.model_copy(deep=deep)


class JobQueueAuthorityFlags(_JobQueueModel):
    production_worker_attached: Literal[False] = Field(
        default=False,
        alias="productionWorkerAttached",
    )
    production_write: Literal[False] = Field(default=False, alias="productionWrite")
    live_tool_execution: Literal[False] = Field(default=False, alias="liveToolExecution")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
    database_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="databaseMutationAllowed",
    )
    filesystem_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="filesystemMutationAllowed",
    )
    network_call_allowed: Literal[False] = Field(default=False, alias="networkCallAllowed")
    production_background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="productionBackgroundExecutionEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

    def public_projection(self) -> dict[str, object]:
        return {
            "productionWorkerAttached": False,
            "productionWrite": False,
            "liveToolExecution": False,
            "trafficAttached": False,
            "userVisibleOutputEnabled": False,
            "databaseMutationAllowed": False,
            "filesystemMutationAllowed": False,
            "networkCallAllowed": False,
            "productionBackgroundExecutionEnabled": False,
        }


class JobQueueConfig(_JobQueueModel):
    enabled: bool = False
    kill_switch_enabled: bool = Field(default=False, alias="killSwitchEnabled")
    max_attempts: int = Field(default=3, alias="maxAttempts", ge=1, le=10)
    lease_timeout_ms: int = Field(default=300_000, alias="leaseTimeoutMs", ge=1_000)
    max_pending_jobs: int = Field(default=1_000, alias="maxPendingJobs", ge=1, le=100_000)
    source: Literal["local_in_memory"] = "local_in_memory"
    authority_flags: JobQueueAuthorityFlags = Field(
        default_factory=JobQueueAuthorityFlags,
        alias="authorityFlags",
    )


class JobQueueEnqueueRequest(_JobQueueModel):
    tenant_id: str = Field(alias="tenantId")
    bot_id: str = Field(alias="botId")
    job_kind: str = Field(alias="jobKind")
    idempotency_key: str = Field(alias="idempotencyKey")
    payload_digest: str = Field(alias="payloadDigest")
    policy_snapshot_digest: str | None = Field(default=None, alias="policySnapshotDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("tenant_id", "bot_id", "job_kind", "idempotency_key")
    @classmethod
    def _validate_refs(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("payload_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digests(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return safe_metadata(value)

    @property
    def idempotency_key_digest(self) -> str:
        return _digest_payload(
            {
                "tenantId": self.tenant_id,
                "botId": self.bot_id,
                "jobKind": self.job_kind,
                "idempotencyKey": self.idempotency_key,
            }
        )

    @property
    def request_digest(self) -> str:
        return _digest_payload(
            {
                "tenantId": self.tenant_id,
                "botId": self.bot_id,
                "jobKind": self.job_kind,
                "idempotencyKeyDigest": self.idempotency_key_digest,
                "payloadDigest": self.payload_digest,
                "policySnapshotDigest": self.policy_snapshot_digest,
                "metadata": dict(sorted(self.metadata.items())),
            }
        )

    @property
    def job_id(self) -> str:
        return "job:" + self.request_digest.removeprefix("sha256:")


class AgentJob(_JobQueueModel):
    schema_version: Literal["openmagi.ops.job.v1"] = Field(
        default="openmagi.ops.job.v1",
        alias="schemaVersion",
    )
    job_id: str = Field(alias="jobId")
    tenant_id: str = Field(alias="tenantId")
    bot_id: str = Field(alias="botId")
    job_kind: str = Field(alias="jobKind")
    idempotency_key_digest: str = Field(alias="idempotencyKeyDigest")
    payload_digest: str = Field(alias="payloadDigest")
    request_digest: str = Field(alias="requestDigest")
    status: JobRecordStatus = "queued"
    attempt_count: int = Field(default=0, alias="attemptCount", ge=0)
    result_digest: str | None = Field(default=None, alias="resultDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    created_at_ms: int = Field(alias="createdAtMs", ge=0)
    updated_at_ms: int = Field(alias="updatedAtMs", ge=0)
    lease_expires_at_ms: int | None = Field(default=None, alias="leaseExpiresAtMs")
    authority_flags: JobQueueAuthorityFlags = Field(
        default_factory=JobQueueAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("job_id", "tenant_id", "bot_id", "job_kind")
    @classmethod
    def _validate_refs(cls, value: str, info: object) -> str:
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator(
        "idempotency_key_digest",
        "payload_digest",
        "request_digest",
        "result_digest",
    )
    @classmethod
    def _validate_digests(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_reason(reason) for reason in value)

    @property
    def job_digest(self) -> str:
        return _digest_payload(self.model_dump(by_alias=True, mode="json"))

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.ops.job.public.v1",
            "jobId": self.job_id,
            "jobDigest": self.job_digest,
            "jobKind": self.job_kind,
            "status": self.status,
            "attemptCount": self.attempt_count,
            "payloadDigest": self.payload_digest,
            "requestDigest": self.request_digest,
            "resultDigest": self.result_digest,
            "reasonCodes": list(self.reason_codes),
            "createdAtMs": self.created_at_ms,
            "updatedAtMs": self.updated_at_ms,
            "leaseExpiresAtMs": self.lease_expires_at_ms,
            "authorityFlags": self.authority_flags.public_projection(),
        }


class JobLease(_JobQueueModel):
    schema_version: Literal["openmagi.ops.job_lease.v1"] = Field(
        default="openmagi.ops.job_lease.v1",
        alias="schemaVersion",
    )
    lease_id: str = Field(alias="leaseId")
    worker_digest: str = Field(alias="workerDigest")
    job: AgentJob
    leased_at_ms: int = Field(alias="leasedAtMs", ge=0)
    lease_expires_at_ms: int = Field(alias="leaseExpiresAtMs", ge=0)
    authority_flags: JobQueueAuthorityFlags = Field(
        default_factory=JobQueueAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("lease_id")
    @classmethod
    def _validate_lease_id(cls, value: str) -> str:
        return require_safe_ref(value, field_name="leaseId")

    @field_validator("worker_digest")
    @classmethod
    def _validate_worker_digest(cls, value: str) -> str:
        return require_digest(value)

    @property
    def lease_digest(self) -> str:
        return _digest_payload(self.model_dump(by_alias=True, mode="json"))


class JobQueueReceipt(_JobQueueModel):
    schema_version: Literal["openmagi.ops.job_queue.receipt.v1"] = Field(
        default="openmagi.ops.job_queue.receipt.v1",
        alias="schemaVersion",
    )
    status: JobQueueReceiptStatus
    request_digest: str = Field(alias="requestDigest")
    job_id: str | None = Field(default=None, alias="jobId")
    job_digest: str | None = Field(default=None, alias="jobDigest")
    lease_id: str | None = Field(default=None, alias="leaseId")
    lease_digest: str | None = Field(default=None, alias="leaseDigest")
    idempotency_key_digest: str | None = Field(default=None, alias="idempotencyKeyDigest")
    payload_digest: str | None = Field(default=None, alias="payloadDigest")
    result_digest: str | None = Field(default=None, alias="resultDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    occurred_at_ms: int = Field(default=0, alias="occurredAtMs", ge=0)
    source: Literal["local_in_memory"] = "local_in_memory"
    production_worker_attached: Literal[False] = Field(
        default=False,
        alias="productionWorkerAttached",
    )
    production_write: Literal[False] = Field(default=False, alias="productionWrite")
    live_tool_execution: Literal[False] = Field(default=False, alias="liveToolExecution")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )
    public_projection_allowed: Literal[False] = Field(
        default=False,
        alias="publicProjectionAllowed",
    )
    authority_flags: JobQueueAuthorityFlags = Field(
        default_factory=JobQueueAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for alias in (
            "productionWorkerAttached",
            "productionWrite",
            "liveToolExecution",
            "trafficAttached",
            "userVisibleOutputEnabled",
            "publicProjectionAllowed",
        ):
            payload[alias] = False
        return payload

    @field_validator(
        "request_digest",
        "job_digest",
        "lease_digest",
        "idempotency_key_digest",
        "payload_digest",
        "result_digest",
    )
    @classmethod
    def _validate_digests(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_digest(value)

    @field_validator("job_id", "lease_id")
    @classmethod
    def _validate_refs(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return require_safe_ref(value, field_name=getattr(info, "field_name", "ref"))

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_reason(reason) for reason in value)

    @property
    def receipt_digest(self) -> str:
        return _digest_payload(self.model_dump(by_alias=True, mode="json"))

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "openmagi.ops.job_queue.receipt.public.v1",
            "status": self.status,
            "receiptDigest": self.receipt_digest,
            "requestDigest": self.request_digest,
            "jobId": self.job_id,
            "jobDigest": self.job_digest,
            "leaseId": self.lease_id,
            "leaseDigest": self.lease_digest,
            "idempotencyKeyDigest": self.idempotency_key_digest,
            "payloadDigest": self.payload_digest,
            "resultDigest": self.result_digest,
            "reasonCodes": list(self.reason_codes),
            "occurredAtMs": self.occurred_at_ms,
            "source": self.source,
            "productionWorkerAttached": False,
            "productionWrite": False,
            "liveToolExecution": False,
            "trafficAttached": False,
            "userVisibleOutputEnabled": False,
            "publicProjectionAllowed": False,
            "authorityFlags": self.authority_flags.public_projection(),
        }


class EnqueueResult(_JobQueueModel):
    status: EnqueueStatus
    job: AgentJob | None = None
    receipt: JobQueueReceipt
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_reason(reason) for reason in value)


class AgentJobQueue:
    """Local in-memory product-plane job lifecycle contract.

    The queue records deterministic job lifecycle metadata only. It does not
    start workers, attach ADK LongRunningFunctionTool, dispatch ToolHost,
    persist to production storage, open network connections, or project
    user-visible output.
    """

    def __init__(self, *, config: JobQueueConfig | Mapping[str, Any] | None = None) -> None:
        self.config = (
            config
            if isinstance(config, JobQueueConfig)
            else JobQueueConfig.model_validate(config or {})
        )
        self._kill_switch_enabled = self.config.kill_switch_enabled
        self._jobs: dict[str, AgentJob] = {}
        self._idempotency_index: dict[str, tuple[str, str]] = {}
        self._leases: dict[str, JobLease] = {}
        self._receipts: list[JobQueueReceipt] = []

    @property
    def kill_switch_enabled(self) -> bool:
        return self._kill_switch_enabled

    @kill_switch_enabled.setter
    def kill_switch_enabled(self, value: bool) -> None:
        self._kill_switch_enabled = bool(value)

    def enqueue(self, request: JobQueueEnqueueRequest | Mapping[str, Any], *, now_ms: int = 0) -> EnqueueResult:
        safe_request = (
            request
            if isinstance(request, JobQueueEnqueueRequest)
            else JobQueueEnqueueRequest.model_validate(request)
        )
        if not self.config.enabled:
            receipt = self._record_receipt(
                status="disabled",
                request=safe_request,
                reason_codes=("job_queue_disabled",),
                now_ms=now_ms,
            )
            return EnqueueResult(
                status="disabled",
                job=None,
                receipt=receipt,
                reasonCodes=("job_queue_disabled",),
            )

        if self.kill_switch_enabled:
            receipt = self._record_receipt(
                status="blocked",
                request=safe_request,
                reason_codes=("job_queue_kill_switch_enabled",),
                now_ms=now_ms,
            )
            return EnqueueResult(
                status="blocked",
                job=None,
                receipt=receipt,
                reasonCodes=("job_queue_kill_switch_enabled",),
            )

        idempotency_scope = self._idempotency_scope(safe_request)
        previous = self._idempotency_index.get(idempotency_scope)
        if previous is not None:
            previous_request_digest, previous_job_id = previous
            previous_job = self._jobs.get(previous_job_id)
            if previous_request_digest == safe_request.request_digest and previous_job is not None:
                receipt = self._record_receipt(
                    status="duplicate",
                    request=safe_request,
                    job=previous_job,
                    reason_codes=("job_queue_idempotency_duplicate",),
                    now_ms=now_ms,
                )
                return EnqueueResult(
                    status="duplicate",
                    job=previous_job,
                    receipt=receipt,
                    reasonCodes=("job_queue_idempotency_duplicate",),
                )
            receipt = self._record_receipt(
                status="blocked",
                request=safe_request,
                job=previous_job,
                reason_codes=("job_queue_idempotency_conflict",),
                now_ms=now_ms,
            )
            return EnqueueResult(
                status="blocked",
                job=previous_job,
                receipt=receipt,
                reasonCodes=("job_queue_idempotency_conflict",),
            )

        if self._pending_job_count() >= self.config.max_pending_jobs:
            receipt = self._record_receipt(
                status="blocked",
                request=safe_request,
                reason_codes=("job_queue_capacity_exceeded",),
                now_ms=now_ms,
            )
            return EnqueueResult(
                status="blocked",
                job=None,
                receipt=receipt,
                reasonCodes=("job_queue_capacity_exceeded",),
            )

        job = AgentJob(
            jobId=safe_request.job_id,
            tenantId=safe_request.tenant_id,
            botId=safe_request.bot_id,
            jobKind=safe_request.job_kind,
            idempotencyKeyDigest=safe_request.idempotency_key_digest,
            payloadDigest=safe_request.payload_digest,
            requestDigest=safe_request.request_digest,
            status="queued",
            attemptCount=0,
            reasonCodes=("job_queued",),
            createdAtMs=now_ms,
            updatedAtMs=now_ms,
        )
        self._jobs[job.job_id] = job
        self._idempotency_index[idempotency_scope] = (safe_request.request_digest, job.job_id)
        receipt = self._record_receipt(
            status="queued",
            request=safe_request,
            job=job,
            reason_codes=("job_queued",),
            now_ms=now_ms,
        )
        return EnqueueResult(status="queued", job=job, receipt=receipt, reasonCodes=("job_queued",))

    def lease_next(self, *, worker_id: str, now_ms: int = 0) -> JobLease | None:
        if not self.config.enabled or self.kill_switch_enabled:
            return None
        safe_worker_id = require_safe_ref(worker_id, field_name="workerId")
        worker_digest = _digest_text(safe_worker_id)
        for job in self._jobs.values():
            if job.status != "queued":
                continue
            updated = self._replace_job(
                job,
                status="running",
                attemptCount=job.attempt_count + 1,
                updatedAtMs=now_ms,
                leaseExpiresAtMs=now_ms + self.config.lease_timeout_ms,
                reasonCodes=("job_lease_acquired",),
            )
            lease_id = "lease:" + _digest_payload(
                {
                    "jobId": updated.job_id,
                    "workerDigest": worker_digest,
                    "attemptCount": updated.attempt_count,
                    "leasedAtMs": now_ms,
                }
            ).removeprefix("sha256:")
            lease = JobLease(
                leaseId=lease_id,
                workerDigest=worker_digest,
                job=updated,
                leasedAtMs=now_ms,
                leaseExpiresAtMs=now_ms + self.config.lease_timeout_ms,
            )
            self._leases[lease_id] = lease
            self._record_receipt(
                status="running",
                request_digest=updated.request_digest,
                job=updated,
                lease=lease,
                reason_codes=("job_lease_acquired",),
                now_ms=now_ms,
            )
            return lease
        return None

    def ack(self, lease_id: str, *, result_digest: str, now_ms: int = 0) -> AgentJob:
        safe_lease_id = require_safe_ref(lease_id, field_name="leaseId")
        safe_result_digest = require_digest(result_digest)
        lease = self._leases.pop(safe_lease_id, None)
        if lease is None:
            raise ValueError("job lease is not active")
        if now_ms > lease.lease_expires_at_ms:
            self._leases[safe_lease_id] = lease
            raise ValueError("job lease expired before acknowledgement")
        job = self._jobs.get(lease.job.job_id)
        if job is None or job.status != "running":
            raise ValueError("job lease does not match a running job")
        updated = self._replace_job(
            job,
            status="completed",
            resultDigest=safe_result_digest,
            updatedAtMs=now_ms,
            leaseExpiresAtMs=None,
            reasonCodes=("job_completed",),
        )
        self._record_receipt(
            status="completed",
            request_digest=updated.request_digest,
            job=updated,
            lease=lease,
            result_digest=safe_result_digest,
            reason_codes=("job_completed",),
            now_ms=now_ms,
        )
        return updated

    def fail(self, lease_id: str, *, reason_code: str, now_ms: int = 0) -> AgentJob:
        safe_lease_id = require_safe_ref(lease_id, field_name="leaseId")
        safe_reason = _safe_reason(reason_code)
        lease = self._leases.pop(safe_lease_id, None)
        if lease is None:
            raise ValueError("job lease is not active")
        if now_ms > lease.lease_expires_at_ms:
            self._leases[safe_lease_id] = lease
            raise ValueError("job lease expired before failure")
        job = self._jobs.get(lease.job.job_id)
        if job is None or job.status != "running":
            raise ValueError("job lease does not match a running job")
        if job.attempt_count >= self.config.max_attempts:
            updated = self._replace_job(
                job,
                status="dead_lettered",
                updatedAtMs=now_ms,
                leaseExpiresAtMs=None,
                reasonCodes=(safe_reason, "job_dead_lettered"),
            )
            self._record_receipt(
                status="dead_lettered",
                request_digest=updated.request_digest,
                job=updated,
                lease=lease,
                reason_codes=(safe_reason, "job_dead_lettered"),
                now_ms=now_ms,
            )
            return updated

        updated = self._replace_job(
            job,
            status="queued",
            updatedAtMs=now_ms,
            leaseExpiresAtMs=None,
            reasonCodes=(safe_reason, "job_retry_queued"),
        )
        self._record_receipt(
            status="failed",
            request_digest=updated.request_digest,
            job=updated,
            lease=lease,
            reason_codes=(safe_reason, "job_retry_queued"),
            now_ms=now_ms,
        )
        return updated

    def cancel(self, job_id: str, *, reason_code: str, now_ms: int = 0) -> AgentJob:
        safe_job_id = require_safe_ref(job_id, field_name="jobId")
        safe_reason = _safe_reason(reason_code)
        job = self._jobs.get(safe_job_id)
        if job is None:
            raise ValueError("job is not known")
        if job.status in {"completed", "dead_lettered", "cancelled"}:
            raise ValueError("terminal job cannot be cancelled")
        updated = self._replace_job(
            job,
            status="cancelled",
            updatedAtMs=now_ms,
            leaseExpiresAtMs=None,
            reasonCodes=(safe_reason, "job_cancelled"),
        )
        self._leases = {
            lease_id: lease
            for lease_id, lease in self._leases.items()
            if lease.job.job_id != safe_job_id
        }
        self._record_receipt(
            status="cancelled",
            request_digest=updated.request_digest,
            job=updated,
            reason_codes=(safe_reason, "job_cancelled"),
            now_ms=now_ms,
        )
        return updated

    def timeout_expired_leases(self, *, now_ms: int) -> tuple[JobQueueReceipt, ...]:
        timed_out: list[JobQueueReceipt] = []
        for lease_id, lease in tuple(self._leases.items()):
            if now_ms <= lease.lease_expires_at_ms:
                continue
            self._leases.pop(lease_id, None)
            job = self._jobs.get(lease.job.job_id)
            if job is None or job.status != "running":
                continue
            terminal = job.attempt_count >= self.config.max_attempts
            updated = self._replace_job(
                job,
                status="dead_lettered" if terminal else "queued",
                updatedAtMs=now_ms,
                leaseExpiresAtMs=None,
                reasonCodes=(
                    "job_lease_timed_out",
                    "job_dead_lettered" if terminal else "job_retry_queued",
                ),
            )
            receipt = self._record_receipt(
                status="timed_out",
                request_digest=updated.request_digest,
                job=updated,
                lease=lease,
                reason_codes=(
                    "job_lease_timed_out",
                    "job_dead_lettered" if terminal else "job_retry_queued",
                ),
                now_ms=now_ms,
            )
            timed_out.append(receipt)
        return tuple(timed_out)

    def get(self, job_id: str) -> AgentJob | None:
        return self._jobs.get(require_safe_ref(job_id, field_name="jobId"))

    def jobs(self) -> tuple[AgentJob, ...]:
        return tuple(self._jobs.values())

    def receipts(self) -> tuple[JobQueueReceipt, ...]:
        return tuple(self._receipts)

    def _pending_job_count(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status in {"queued", "running"})

    def _idempotency_scope(self, request: JobQueueEnqueueRequest) -> str:
        return _digest_payload(
            {
                "tenantId": request.tenant_id,
                "botId": request.bot_id,
                "jobKind": request.job_kind,
                "idempotencyKeyDigest": request.idempotency_key_digest,
            }
        )

    def _replace_job(self, job: AgentJob, **updates: object) -> AgentJob:
        payload = job.model_dump(by_alias=True, mode="json")
        payload.update(updates)
        updated = AgentJob.model_validate(payload)
        self._jobs[updated.job_id] = updated
        return updated

    def _record_receipt(
        self,
        *,
        status: JobQueueReceiptStatus,
        reason_codes: tuple[str, ...],
        now_ms: int,
        request: JobQueueEnqueueRequest | None = None,
        request_digest: str | None = None,
        job: AgentJob | None = None,
        lease: JobLease | None = None,
        result_digest: str | None = None,
    ) -> JobQueueReceipt:
        resolved_request_digest = (
            request.request_digest
            if request is not None
            else request_digest
            if request_digest is not None
            else job.request_digest
            if job is not None
            else _digest_text(f"job-queue:{status}:{now_ms}")
        )
        receipt = JobQueueReceipt(
            status=status,
            requestDigest=resolved_request_digest,
            jobId=job.job_id if job is not None else None,
            jobDigest=job.job_digest if job is not None else None,
            leaseId=lease.lease_id if lease is not None else None,
            leaseDigest=lease.lease_digest if lease is not None else None,
            idempotencyKeyDigest=(
                request.idempotency_key_digest
                if request is not None
                else job.idempotency_key_digest
                if job is not None
                else None
            ),
            payloadDigest=(
                request.payload_digest
                if request is not None
                else job.payload_digest
                if job is not None
                else None
            ),
            resultDigest=result_digest if result_digest is not None else job.result_digest if job else None,
            reasonCodes=reason_codes,
            occurredAtMs=now_ms,
        )
        self._receipts.append(receipt)
        return receipt


def enqueue_job(
    queue: AgentJobQueue,
    *,
    tenant_id: str,
    bot_id: str,
    job_kind: str,
    idempotency_key: str,
    payload_digest: str,
    policy_snapshot_digest: str | None = None,
    metadata: Mapping[str, object] | None = None,
    now_ms: int = 0,
) -> EnqueueResult:
    return queue.enqueue(
        {
            "tenantId": tenant_id,
            "botId": bot_id,
            "jobKind": job_kind,
            "idempotencyKey": idempotency_key,
            "payloadDigest": payload_digest,
            "policySnapshotDigest": policy_snapshot_digest,
            "metadata": metadata or {},
        },
        now_ms=now_ms,
    )


__all__ = [
    "AgentJob",
    "AgentJobQueue",
    "EnqueueResult",
    "JobLease",
    "JobQueueAuthorityFlags",
    "JobQueueConfig",
    "JobQueueEnqueueRequest",
    "JobQueueReceipt",
    "enqueue_job",
]
