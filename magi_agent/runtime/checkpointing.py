from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


_DIGEST_PREFIX = "sha256:"
_PROTECTED_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "pro" + "mpt",
    "sess" + "ion",
    "priv" + "ate",
)
_RAW_DIAGNOSTIC_MARKERS = (
    "raw ",
    "raw:",
    "raw-",
    "raw_",
    "rawref",
    "tool log",
    "child log",
    "traceback",
    "stdout",
    "stderr",
)
_RAW_DIAGNOSTIC_COMPACT_MARKERS = (
    "rawtoollog",
    "rawchildtranscript",
    "childrawtoollog",
    "toollog",
    "childlog",
)
_REASON_CODES = (
    "ledger_head_digest_mismatch",
    "effective_policy_snapshot_unavailable",
    "effective_policy_snapshot_digest_mismatch",
    "authority_scope_would_expand",
    "pending_approval_expired",
    "required_evidence_missing",
    "checkpoint_not_resumable",
)


class _FrozenNoUpdateModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> "_FrozenNoUpdateModel":
        if update:
            raise ValueError("model_copy update is disabled for checkpoint contracts")
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class ExecutionCheckpoint(_FrozenNoUpdateModel):
    run_id: str = Field(alias="runId")
    checkpoint_id: str = Field(alias="checkpointId")
    step_id: str = Field(alias="stepId")
    workflow_version: str = Field(alias="workflowVersion")
    state_digest: str = Field(alias="stateDigest")
    ledger_head_digest: str = Field(alias="ledgerHeadDigest")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    context_projection_digest: str = Field(alias="contextProjectionDigest")
    pending_approval_refs: tuple[str, ...] = Field(default=(), alias="pendingApprovalRefs")
    resumable: StrictBool
    created_at: datetime = Field(alias="createdAt")

    @field_validator("run_id", "checkpoint_id", "step_id", "workflow_version")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        _validate_identifier(value, "checkpoint identifier")
        return value

    @field_validator(
        "state_digest",
        "ledger_head_digest",
        "effective_policy_snapshot_digest",
        "context_projection_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value)

    @field_validator("pending_approval_refs", mode="before")
    @classmethod
    def _normalize_approval_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("pendingApprovalRefs must be an array of non-empty strings")
        values = tuple(value)  # type: ignore[arg-type]
        for ref in values:
            _validate_identifier(ref, "pendingApprovalRefs")
        return values


class ResumeVerificationReport(_FrozenNoUpdateModel):
    ok: StrictBool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for reason_code in value:
            if reason_code not in _REASON_CODES:
                raise ValueError("reasonCodes must be canonical resume verification reason codes")
        return value


class ResumeVerificationRequest(_FrozenNoUpdateModel):
    ledger_head_digest: str = Field(alias="ledgerHeadDigest")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    effective_policy_snapshot_available: StrictBool = Field(alias="effectivePolicySnapshotAvailable")
    authority_scope_would_expand: StrictBool = Field(alias="authorityScopeWouldExpand")
    pending_approval_expired: StrictBool = Field(alias="pendingApprovalExpired")
    required_evidence_missing: StrictBool = Field(alias="requiredEvidenceMissing")

    @field_validator("ledger_head_digest", "effective_policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value)


class ReplayModeDecision(_FrozenNoUpdateModel):
    mode: Literal["replay"]
    allow_side_effects: StrictBool = Field(alias="allowSideEffects")
    append_replay_observation: StrictBool = Field(alias="appendReplayObservation")

    @field_validator("allow_side_effects")
    @classmethod
    def _validate_side_effects_false(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError("allowSideEffects must be false for replay")
        return value

    @field_validator("append_replay_observation")
    @classmethod
    def _validate_replay_observation_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("appendReplayObservation must be true for replay")
        return value


class ForkedRunLineage(_FrozenNoUpdateModel):
    parent_run_id: str = Field(alias="parentRunId")
    parent_checkpoint_id: str = Field(alias="parentCheckpointId")
    parent_ledger_head_digest: str = Field(alias="parentLedgerHeadDigest")
    fork_reason: str = Field(alias="forkReason")
    new_run_id: str = Field(alias="newRunId")
    new_effective_policy_snapshot_digest: str = Field(alias="newEffectivePolicySnapshotDigest")

    @field_validator("parent_run_id", "parent_checkpoint_id", "fork_reason", "new_run_id")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        _validate_identifier(value, "lineage identifier")
        return value

    @field_validator("parent_ledger_head_digest", "new_effective_policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value)


def verify_resume_request(
    checkpoint: ExecutionCheckpoint,
    *,
    ledgerHeadDigest: str,
    effectivePolicySnapshotDigest: str,
    effectivePolicySnapshotAvailable: bool,
    authorityScopeWouldExpand: bool,
    pendingApprovalExpired: bool,
    requiredEvidenceMissing: bool,
) -> ResumeVerificationReport:
    request = ResumeVerificationRequest(
        ledgerHeadDigest=ledgerHeadDigest,
        effectivePolicySnapshotDigest=effectivePolicySnapshotDigest,
        effectivePolicySnapshotAvailable=effectivePolicySnapshotAvailable,
        authorityScopeWouldExpand=authorityScopeWouldExpand,
        pendingApprovalExpired=pendingApprovalExpired,
        requiredEvidenceMissing=requiredEvidenceMissing,
    )
    reasons: list[str] = []
    if request.ledger_head_digest != checkpoint.ledger_head_digest:
        reasons.append("ledger_head_digest_mismatch")
    if not request.effective_policy_snapshot_available:
        reasons.append("effective_policy_snapshot_unavailable")
    if request.effective_policy_snapshot_digest != checkpoint.effective_policy_snapshot_digest:
        reasons.append("effective_policy_snapshot_digest_mismatch")
    if request.authority_scope_would_expand:
        reasons.append("authority_scope_would_expand")
    if request.pending_approval_expired:
        reasons.append("pending_approval_expired")
    if request.required_evidence_missing:
        reasons.append("required_evidence_missing")
    if not checkpoint.resumable:
        reasons.append("checkpoint_not_resumable")
    return ResumeVerificationReport(ok=not reasons, reasonCodes=tuple(reasons))


def _require_digest(value: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError("digest fields must be sha256 digests")
    return value


def _validate_identifier(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    lowered = value.lower()
    if any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS):
        raise ValueError(f"{field_name} contains protected runtime data marker")
    compact = "".join(character for character in lowered if character.isalnum())
    if any(marker in lowered for marker in _RAW_DIAGNOSTIC_MARKERS) or any(
        marker in compact for marker in _RAW_DIAGNOSTIC_COMPACT_MARKERS
    ):
        raise ValueError(f"{field_name} contains protected runtime data marker")
    if "/" in value or "\\" in value or value.startswith(("~", ".")):
        raise ValueError(f"{field_name} must not be path-like")
