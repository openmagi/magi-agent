from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
from math import isfinite
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.ops.safety import reject_private_text, require_safe_ref
from openmagi_core_agent.runtime.heartbeat_contract import (
    HeartbeatReceipt,
    ResumeDecision,
    RunLease,
    StaleRunVerdict,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_CHECKPOINT_PREFIX = "checkpoint:"
_AUTHORITY_METADATA_PREFIXES = (
    "activation",
    "authority",
    "browser",
    "capability",
    "channel",
    "child",
    "db",
    "env",
    "execution",
    "gate2",
    "gate8",
    "k8s",
    "kubernetes",
    "live",
    "memory",
    "missionruntime",
    "model",
    "permission",
    "production",
    "provider",
    "route",
    "scheduler",
    "tool",
    "traffic",
    "wakeagent",
    "workspace",
)


class ResumeDecisionConfig(BaseModel):
    model_config = _MODEL_CONFIG

    stuck_loop_threshold: int = Field(default=3, alias="stuckLoopThreshold", ge=1, le=20)

    @field_validator("stuck_loop_threshold", mode="before")
    @classmethod
    def _reject_bool_threshold(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("stuckLoopThreshold must be an integer")
        return value


class ResumeDecisionContext(BaseModel):
    model_config = _MODEL_CONFIG

    run_id: str = Field(alias="runId")
    decided_at: datetime = Field(alias="decidedAt")
    lease: RunLease
    last_heartbeat: HeartbeatReceipt | None = Field(default=None, alias="lastHeartbeat")
    stale_verdict: StaleRunVerdict = Field(alias="staleVerdict")
    completed: bool = False
    cancelled: bool = False
    checkpoint_ref: str | None = Field(default=None, alias="checkpointRef")
    same_session_available: bool = Field(default=False, alias="sameSessionAvailable")
    restart_interrupted: bool = Field(default=False, alias="restartInterrupted")
    stuck_loop_count: int = Field(default=0, alias="stuckLoopCount", ge=0, le=10_000)
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        return _safe_prefixed_ref(value, field_name="runId", prefix="run:")

    @field_validator("checkpoint_ref")
    @classmethod
    def _validate_checkpoint_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_prefixed_ref(
            value,
            field_name="checkpointRef",
            prefix=_CHECKPOINT_PREFIX,
        )

    @field_validator("decided_at")
    @classmethod
    def _validate_decided_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decidedAt must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("stuck_loop_count", mode="before")
    @classmethod
    def _reject_bool_count(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("stuckLoopCount must be an integer")
        return value

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        return _safe_metadata(value)

    @model_validator(mode="after")
    def _validate_record_consistency(self) -> Self:
        if self.lease.run_id != self.run_id:
            raise ValueError("lease runId must match resume decision runId")
        if self.last_heartbeat is not None:
            if self.last_heartbeat.run_id != self.run_id:
                raise ValueError("lastHeartbeat runId must match resume decision runId")
            if self.last_heartbeat.lease_id != self.lease.lease_id:
                raise ValueError("lastHeartbeat leaseId must match lease leaseId")
            if self.stale_verdict.heartbeat_digest != self.last_heartbeat.digest:
                raise ValueError("staleVerdict heartbeatDigest must match lastHeartbeat")
        if self.stale_verdict.run_id != self.run_id:
            raise ValueError("staleVerdict runId must match resume decision runId")
        if self.last_heartbeat is None and self.stale_verdict.heartbeat_digest is not None:
            raise ValueError("staleVerdict heartbeatDigest requires lastHeartbeat")
        expected_lease_digest = _digest_text(self.lease.lease_id)
        if self.stale_verdict.lease_digest is None:
            raise ValueError("staleVerdict leaseDigest must match lease")
        if (
            self.stale_verdict.lease_digest != expected_lease_digest
        ):
            raise ValueError("staleVerdict leaseDigest must match lease")
        return self


def evaluate_resume_decision(
    context: ResumeDecisionContext,
    *,
    config: ResumeDecisionConfig | None = None,
) -> ResumeDecision:
    decision_config = config or ResumeDecisionConfig()
    checkpoint_digest = _digest_text(context.checkpoint_ref) if context.checkpoint_ref else None
    verdict_digest = _digest_mapping(context.stale_verdict.public_projection())
    base_metadata = _metadata(context, decision_config)

    if context.completed:
        return _decision(
            context,
            "ignore_completed",
            reason_codes=("run_completed",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata={**base_metadata, "completed": True},
        )

    if context.cancelled or context.stale_verdict.verdict == "cancelled":
        return _decision(
            context,
            "cancel_and_project_failure",
            reason_codes=("run_cancelled",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata={**base_metadata, "cancelled": True},
        )

    if context.stale_verdict.verdict == "blocked_for_operator":
        return _decision(
            context,
            "block_for_operator",
            reason_codes=("blocked_for_operator",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata=base_metadata,
        )

    if context.stuck_loop_count >= decision_config.stuck_loop_threshold:
        return _decision(
            context,
            "block_for_operator",
            reason_codes=("stuck_loop_threshold_exceeded",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata=base_metadata,
        )

    if context.stale_verdict.verdict == "rollback_required":
        if checkpoint_digest is not None:
            return _decision(
                context,
                "retry_from_checkpoint",
                reason_codes=("rollback_checkpoint_available",),
                checkpoint_digest=checkpoint_digest,
                verdict_digest=verdict_digest,
                metadata=base_metadata,
            )
        return _decision(
            context,
            "block_for_operator",
            reason_codes=("rollback_without_checkpoint",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata=base_metadata,
        )

    if context.stale_verdict.verdict in {
        "inactive_timeout",
        "lease_expired",
        "worker_lost",
    }:
        if checkpoint_digest is not None:
            return _decision(
                context,
                "retry_from_checkpoint",
                reason_codes=("checkpoint_available_for_stale_run",),
                checkpoint_digest=checkpoint_digest,
                verdict_digest=verdict_digest,
                metadata=base_metadata,
            )
        return _decision(
            context,
            "cancel_and_project_failure",
            reason_codes=("stale_run_without_checkpoint",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata=base_metadata,
        )

    if context.restart_interrupted or context.stale_verdict.verdict == "resume_pending":
        if context.same_session_available and not context.restart_interrupted:
            return _decision(
                context,
                "resume_same_session",
                reason_codes=("same_session_available",),
                checkpoint_digest=None,
                verdict_digest=verdict_digest,
                metadata={**base_metadata, "resumePending": True},
            )
        return _decision(
            context,
            "resume_with_system_note",
            reason_codes=("restart_interrupted_resume_pending",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata={**base_metadata, "resumePending": True},
        )

    if context.same_session_available:
        return _decision(
            context,
            "resume_same_session",
            reason_codes=("same_session_available",),
            checkpoint_digest=None,
            verdict_digest=verdict_digest,
            metadata=base_metadata,
        )

    return _decision(
        context,
        "resume_with_system_note",
        reason_codes=("system_note_required",),
        checkpoint_digest=None,
        verdict_digest=verdict_digest,
        metadata=base_metadata,
    )


def _decision(
    context: ResumeDecisionContext,
    decision: Literal[
        "resume_same_session",
        "resume_with_system_note",
        "retry_from_checkpoint",
        "cancel_and_project_failure",
        "block_for_operator",
        "ignore_completed",
    ],
    *,
    reason_codes: tuple[str, ...],
    checkpoint_digest: str | None,
    verdict_digest: str,
    metadata: Mapping[str, object],
) -> ResumeDecision:
    return ResumeDecision(
        decision=decision,
        runId=context.run_id,
        decidedAt=context.decided_at,
        reasonCodes=reason_codes,
        checkpointDigest=checkpoint_digest,
        verdictDigest=verdict_digest,
        metadata=metadata,
    )


def _metadata(
    context: ResumeDecisionContext,
    config: ResumeDecisionConfig,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source": "resume_decision",
        "staleVerdict": context.stale_verdict.verdict,
        "sameSessionAvailable": context.same_session_available,
        "restartInterrupted": context.restart_interrupted,
        "stuckLoopCount": context.stuck_loop_count,
        "stuckLoopThreshold": config.stuck_loop_threshold,
        "resumeExecutionAllowed": False,
        "runnerInvoked": False,
        "leaseGeneration": context.lease.generation,
        "leasePhase": context.lease.phase,
    }
    if context.last_heartbeat is not None:
        metadata["heartbeatSequence"] = context.last_heartbeat.sequence
        metadata["heartbeatPhase"] = context.last_heartbeat.phase
    metadata.update(_unreserved_metadata(context.metadata))
    return _safe_metadata(metadata)


def _safe_prefixed_ref(value: str, *, field_name: str, prefix: str) -> str:
    clean = value.strip()
    reject_private_text(clean, field_name=field_name)
    if not clean.startswith(prefix) or len(clean) == len(prefix):
        raise ValueError(f"{field_name} must use {prefix} public ref")
    if _is_authority_shaped(clean.removeprefix(prefix)):
        raise ValueError(f"{field_name} must not imply live authority")
    return require_safe_ref(clean, field_name=field_name)


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in sorted(metadata.items(), key=lambda pair: str(pair[0])):
        if not isinstance(key, str):
            raise ValueError("metadata keys must be strings")
        if _is_authority_shaped(key):
            raise ValueError("metadata keys must not imply live authority")
        safe[key] = _safe_metadata_value(value)
    return safe


def _unreserved_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    reserved = {
        "checkpointRef",
        "checkpoint_ref",
        "completed",
        "cancelled",
        "heartbeatPhase",
        "heartbeat_phase",
        "heartbeatSequence",
        "heartbeat_sequence",
        "leaseGeneration",
        "lease_generation",
        "leasePhase",
        "lease_phase",
        "resumeExecutionAllowed",
        "resume_execution_allowed",
        "resumePending",
        "resume_pending",
        "restartInterrupted",
        "restart_interrupted",
        "runnerInvoked",
        "runner_invoked",
        "sameSessionAvailable",
        "same_session_available",
        "source",
        "staleVerdict",
        "stale_verdict",
        "stuckLoopCount",
        "stuck_loop_count",
        "stuckLoopThreshold",
        "stuck_loop_threshold",
    }
    normalized_reserved = {_normalize_key(item) for item in reserved}
    return {
        str(key): value
        for key, value in metadata.items()
        if _normalize_key(str(key)) not in normalized_reserved
    }


def _safe_metadata_value(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("metadata numeric values must be finite")
        return value
    if isinstance(value, str):
        clean = value.strip()
        reject_private_text(clean, field_name="metadata")
        if _is_authority_shaped(clean):
            raise ValueError("metadata values must not imply live authority")
        return require_safe_ref(clean, field_name="metadata")
    raise ValueError("metadata must contain only safe primitive values")


def _is_authority_shaped(value: str) -> bool:
    normalized = _normalize_key(value)
    return any(normalized.startswith(prefix) for prefix in _AUTHORITY_METADATA_PREFIXES)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_mapping(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        {str(key): _json_safe(item) for key, item in sorted(value.items())},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    if isinstance(value, str) or value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("resume decision digest requires finite numeric values")
        return value
    raise ValueError("resume decision digest requires JSON-safe values")


__all__ = [
    "ResumeDecisionConfig",
    "ResumeDecisionContext",
    "evaluate_resume_decision",
]
