from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from openmagi_core_agent.harness.approval_receipts import (
    ApprovalReceipt,
    verify_approval_receipt_for_action,
)
from openmagi_core_agent.runtime.receipt_utils import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_text,
)
from openmagi_core_agent.self_improvement.review_gate import SelfImprovementAuthorityFlags


RollbackScope: TypeAlias = Literal["recipe", "harness_config", "plugin_config"]
RollbackStatus: TypeAlias = Literal["disabled", "blocked", "rollback_recorded_local_fake"]
AutomaticExecutionDecision: TypeAlias = Literal["denied", "allowed_local_fake"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@=-]{1,191}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class RollbackConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_rollback_enabled: bool = Field(default=False, alias="localFakeRollbackEnabled")
    local_fake_rollback_execution_enabled: bool = Field(
        default=False,
        alias="localFakeRollbackExecutionEnabled",
    )
    production_rollback_enabled: Literal[False] = Field(
        default=False,
        alias="productionRollbackEnabled",
    )
    automatic_rollback_enabled: Literal[False] = Field(
        default=False,
        alias="automaticRollbackEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_live_flags_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionRollbackEnabled"] = False
        payload.pop("production_rollback_enabled", None)
        payload["automaticRollbackEnabled"] = False
        payload.pop("automatic_rollback_enabled", None)
        return payload

    @field_serializer("production_rollback_enabled", "automatic_rollback_enabled")
    def _serialize_false(self, _value: object) -> bool:
        return False

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, deep
        return self.model_copy(update=update)


class RollbackRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    promotion_receipt_digest: str = Field(alias="promotionReceiptDigest")
    promoted_artifact_digest: str = Field(alias="promotedArtifactDigest")
    previous_artifact_digest: str = Field(alias="previousArtifactDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    rollback_scope: RollbackScope = Field(alias="rollbackScope")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    requested_automatic_execution: bool = Field(
        default=False,
        alias="requestedAutomaticExecution",
    )
    now: datetime | None = None
    rollback_approval_receipt: ApprovalReceipt | None = Field(
        default=None,
        alias="rollbackApprovalReceipt",
    )
    rollback_approval_digest: str | None = Field(
        default=None,
        alias="rollbackApprovalDigest",
    )
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True, repr=False)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True, repr=False)
    raw_private_path: str | None = Field(
        default=None,
        alias="rawPrivatePath",
        exclude=True,
        repr=False,
    )
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True, repr=False)
    hidden_reasoning: str | None = Field(
        default=None,
        alias="hiddenReasoning",
        exclude=True,
        repr=False,
    )

    @field_validator("request_id")
    @classmethod
    def _validate_request_id(cls, value: str) -> str:
        return _safe_ref(value, "requestId", prefixes=("self-improvement-rollback:", "ref:"))

    @field_validator(
        "promotion_receipt_digest",
        "promoted_artifact_digest",
        "previous_artifact_digest",
        "policy_snapshot_digest",
        "rollback_approval_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_digest(value, "digest")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "reasonCodes") for item in _string_tuple(value))

    @field_validator("rollback_approval_receipt", mode="before")
    @classmethod
    def _validate_approval_receipt(cls, value: object) -> ApprovalReceipt | None:
        if value is None:
            return None
        if isinstance(value, ApprovalReceipt):
            return ApprovalReceipt.model_validate(value.model_dump(by_alias=True, mode="json"))
        return ApprovalReceipt.model_validate(value)


class RollbackReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementRollbackReceipt.v1"] = Field(
        default="selfImprovementRollbackReceipt.v1",
        alias="schemaVersion",
    )
    rollback_receipt_digest: str = Field(alias="rollbackReceiptDigest")
    promotion_receipt_digest: str = Field(alias="promotionReceiptDigest")
    promoted_artifact_digest: str = Field(alias="promotedArtifactDigest")
    previous_artifact_digest: str = Field(alias="previousArtifactDigest")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    rollback_scope: RollbackScope = Field(alias="rollbackScope")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    rollback_approval_digest: str | None = Field(
        default=None,
        alias="rollbackApprovalDigest",
    )
    automatic_execution_decision: AutomaticExecutionDecision = Field(
        alias="automaticExecutionDecision",
    )
    execution_default: Literal["denied"] = Field(default="denied", alias="executionDefault")
    authority_flags: SelfImprovementAuthorityFlags = Field(
        default_factory=SelfImprovementAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator(
        "rollback_receipt_digest",
        "promotion_receipt_digest",
        "promoted_artifact_digest",
        "previous_artifact_digest",
        "policy_snapshot_digest",
        "rollback_approval_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_digest(value, "digest")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "reasonCodes") for item in _string_tuple(value))

    @model_validator(mode="after")
    def _validate_receipt_digest(self) -> Self:
        if self.rollback_receipt_digest != canonical_digest(_rollback_receipt_payload(self)):
            raise ValueError("rollbackReceiptDigest mismatch")
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        raise ValueError("model_copy is disabled for RollbackReceipt")

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        raise ValueError("copy is disabled for RollbackReceipt")

    @field_serializer("execution_default")
    def _serialize_execution_default(self, _value: object) -> str:
        return "denied"


class RollbackResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementRollbackResult.v1"] = Field(
        default="selfImprovementRollbackResult.v1",
        alias="schemaVersion",
    )
    status: RollbackStatus
    rollback_receipt: RollbackReceipt | None = Field(default=None, alias="rollbackReceipt")
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    authority_flags: SelfImprovementAuthorityFlags = Field(
        default_factory=SelfImprovementAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "rollback_receipt" in payload:
            payload["rollbackReceipt"] = payload.pop("rollback_receipt")
        if "blocked_reason" in payload:
            payload["blockedReason"] = payload.pop("blocked_reason")
        payload["authorityFlags"] = SelfImprovementAuthorityFlags().model_dump(by_alias=True)
        payload.pop("authority_flags", None)
        return payload

    @field_validator("blocked_reason")
    @classmethod
    def _validate_blocked_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_reason_code(value, "blockedReason")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, deep
        return self.model_copy(update=update)


class ReplayPolicySnapshotBinding(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementReplayPolicySnapshotBinding.v1"] = Field(
        default="selfImprovementReplayPolicySnapshotBinding.v1",
        alias="schemaVersion",
    )
    original_run_id: str = Field(alias="originalRunId")
    original_run_receipt_digest: str = Field(alias="originalRunReceiptDigest")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    current_policy_snapshot_digest: str = Field(alias="currentPolicySnapshotDigest")
    replay_creates_side_effects: Literal[False] = Field(
        default=False,
        alias="replayCreatesSideEffects",
    )
    binding_digest: str = Field(alias="bindingDigest")

    @model_validator(mode="after")
    def _validate_binding_digest(self) -> Self:
        if self.binding_digest != canonical_digest(_binding_digest_payload(self)):
            raise ValueError("bindingDigest mismatch")
        return self

    @field_validator(
        "original_run_receipt_digest",
        "effective_policy_snapshot_digest",
        "current_policy_snapshot_digest",
        "binding_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value, "digest")

    @field_validator("original_run_id")
    @classmethod
    def _validate_original_run_id(cls, value: str) -> str:
        return _safe_ref(value, "originalRunId", prefixes=("run:", "ref:"))

    @model_validator(mode="before")
    @classmethod
    def _force_no_side_effects(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "original_run_id" in payload:
            payload["originalRunId"] = payload.pop("original_run_id")
        if "original_run_receipt_digest" in payload:
            payload["originalRunReceiptDigest"] = payload.pop("original_run_receipt_digest")
        if "effective_policy_snapshot_digest" in payload:
            payload["effectivePolicySnapshotDigest"] = payload.pop(
                "effective_policy_snapshot_digest"
            )
        if "current_policy_snapshot_digest" in payload:
            payload["currentPolicySnapshotDigest"] = payload.pop(
                "current_policy_snapshot_digest"
            )
        if "binding_digest" in payload:
            payload["bindingDigest"] = payload.pop("binding_digest")
        payload["replayCreatesSideEffects"] = False
        payload.pop("replay_creates_side_effects", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, deep
        return self.model_copy(update=update)

    @field_serializer("replay_creates_side_effects")
    def _serialize_false(self, _value: object) -> bool:
        return False


class RollbackService:
    def __init__(self, config: RollbackConfig | Mapping[str, object] | None = None) -> None:
        self.config = (
            config
            if isinstance(config, RollbackConfig)
            else RollbackConfig.model_validate(config or {})
        )

    def record(self, request: RollbackRequest | Mapping[str, object]) -> RollbackResult:
        parsed = (
            RollbackRequest.model_validate(request.model_dump(by_alias=True))
            if isinstance(request, RollbackRequest)
            else RollbackRequest.model_validate(request)
        )
        if not self.config.enabled:
            return RollbackResult(
                status="disabled",
                blockedReason="self_improvement_rollback_disabled",
            )
        if not self.config.local_fake_rollback_enabled:
            return RollbackResult(
                status="blocked",
                blockedReason="self_improvement_local_fake_rollback_disabled",
            )
        if parsed.requested_automatic_execution and parsed.rollback_approval_receipt is None:
            return RollbackResult(
                status="blocked",
                blockedReason="rollback_approval_receipt_required",
            )
        if parsed.requested_automatic_execution and parsed.now is None:
            return RollbackResult(
                status="blocked",
                blockedReason="rollback_approval_timestamp_required",
            )
        if parsed.requested_automatic_execution and parsed.rollback_approval_receipt is not None:
            action_digest = compute_self_improvement_rollback_action_digest(
                promotionReceiptDigest=parsed.promotion_receipt_digest,
                promotedArtifactDigest=parsed.promoted_artifact_digest,
                previousArtifactDigest=parsed.previous_artifact_digest,
                rollbackScope=parsed.rollback_scope,
                policySnapshotDigest=parsed.policy_snapshot_digest,
                reasonCodes=parsed.reason_codes,
            )
            approval = verify_approval_receipt_for_action(
                parsed.rollback_approval_receipt,
                actionDigest=action_digest,
                requiredScope="workflow_run",
                now=parsed.now,
            )
            constraint_reasons = _approval_constraint_reasons(
                parsed.rollback_approval_receipt,
                parsed,
            )
            approval_reasons = tuple(dict.fromkeys((*approval.reason_codes, *constraint_reasons)))
            if approval_reasons:
                return RollbackResult(status="blocked", blockedReason=approval_reasons[0])
        if (
            parsed.requested_automatic_execution
            and not self.config.local_fake_rollback_execution_enabled
        ):
            return RollbackResult(
                status="blocked",
                blockedReason="local_fake_rollback_execution_disabled",
            )

        receipt = _build_rollback_receipt(
            parsed,
            automatic_execution_decision=(
                "allowed_local_fake" if parsed.requested_automatic_execution else "denied"
            ),
        )
        return RollbackResult(status="rollback_recorded_local_fake", rollbackReceipt=receipt)


def preserve_replay_policy_snapshot(
    *,
    originalRunId: str,
    originalRunReceiptDigest: str,
    originalPolicySnapshotDigest: str,
    currentPolicySnapshotDigest: str,
) -> ReplayPolicySnapshotBinding:
    payload = {
        "schemaVersion": "selfImprovementReplayPolicySnapshotBinding.v1",
        "originalRunId": _safe_ref(originalRunId, "originalRunId", prefixes=("run:", "ref:")),
        "originalRunReceiptDigest": _safe_digest(
            originalRunReceiptDigest,
            "originalRunReceiptDigest",
        ),
        "effectivePolicySnapshotDigest": _safe_digest(
            originalPolicySnapshotDigest,
            "originalPolicySnapshotDigest",
        ),
        "currentPolicySnapshotDigest": _safe_digest(
            currentPolicySnapshotDigest,
            "currentPolicySnapshotDigest",
        ),
        "replayCreatesSideEffects": False,
    }
    return ReplayPolicySnapshotBinding.model_validate(
        payload | {"bindingDigest": canonical_digest(payload)}
    )


def _build_rollback_receipt(
    request: RollbackRequest,
    *,
    automatic_execution_decision: AutomaticExecutionDecision,
) -> RollbackReceipt:
    payload = {
        "schemaVersion": "selfImprovementRollbackReceipt.v1",
        "promotionReceiptDigest": request.promotion_receipt_digest,
        "promotedArtifactDigest": request.promoted_artifact_digest,
        "previousArtifactDigest": request.previous_artifact_digest,
        "policySnapshotDigest": request.policy_snapshot_digest,
        "rollbackScope": request.rollback_scope,
        "reasonCodes": request.reason_codes,
        "rollbackApprovalDigest": (
            request.rollback_approval_receipt.approval_digest
            if request.rollback_approval_receipt is not None
            else request.rollback_approval_digest
        ),
        "automaticExecutionDecision": automatic_execution_decision,
        "executionDefault": "denied",
        "authorityFlags": SelfImprovementAuthorityFlags().model_dump(by_alias=True),
    }
    return RollbackReceipt.model_validate(
        payload | {"rollbackReceiptDigest": canonical_digest(payload)}
    )


def _rollback_receipt_payload(receipt: RollbackReceipt) -> dict[str, object]:
    return receipt.model_dump(by_alias=True, exclude={"rollback_receipt_digest"})


def _binding_digest_payload(binding: ReplayPolicySnapshotBinding) -> dict[str, object]:
    return binding.model_dump(by_alias=True, exclude={"binding_digest"})


def compute_self_improvement_rollback_action_digest(
    *,
    promotionReceiptDigest: str,
    promotedArtifactDigest: str,
    previousArtifactDigest: str,
    rollbackScope: str,
    policySnapshotDigest: str,
    reasonCodes: Sequence[str],
) -> str:
    if rollbackScope not in {"recipe", "harness_config", "plugin_config"}:
        raise ValueError("rollbackScope must be recipe, harness_config, or plugin_config")
    return canonical_digest(
        {
            "schemaVersion": "selfImprovementRollbackAction.v1",
            "promotionReceiptDigest": _safe_digest(
                promotionReceiptDigest,
                "promotionReceiptDigest",
            ),
            "promotedArtifactDigest": _safe_digest(
                promotedArtifactDigest,
                "promotedArtifactDigest",
            ),
            "previousArtifactDigest": _safe_digest(
                previousArtifactDigest,
                "previousArtifactDigest",
            ),
            "rollbackScope": rollbackScope,
            "policySnapshotDigest": _safe_digest(
                policySnapshotDigest,
                "policySnapshotDigest",
            ),
            "reasonCodes": tuple(
                _safe_reason_code(item, "reasonCodes") for item in reasonCodes
            ),
        }
    )


def _approval_constraint_reasons(
    receipt: ApprovalReceipt,
    request: RollbackRequest,
) -> tuple[str, ...]:
    constraints = receipt.constraints
    expected: dict[str, object] = {
        "promotionReceiptDigest": request.promotion_receipt_digest,
        "promotedArtifactDigest": request.promoted_artifact_digest,
        "previousArtifactDigest": request.previous_artifact_digest,
        "rollbackScope": request.rollback_scope,
        "policySnapshotDigest": request.policy_snapshot_digest,
        "reasonCodes": list(request.reason_codes),
    }
    if any(constraints.get(key) != value for key, value in expected.items()):
        return ("rollback_approval_constraints_mismatch",)
    return ()


def _safe_digest(value: str, field_name: str) -> str:
    raw = str(value).strip()
    if not _SHA256_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be sha256:<64 lowercase hex>")
    return raw


def _safe_ref(value: str, field_name: str, *, prefixes: tuple[str, ...]) -> str:
    raw = str(value).strip()
    if not raw or not raw.startswith(prefixes) or not _SAFE_REF_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be a safe public ref")
    if has_unsafe_marker(raw) or sanitize_public_text(raw) != raw:
        raise ValueError(f"{field_name} contains private or unsafe material")
    return raw


def _safe_reason_code(value: str, field_name: str) -> str:
    token = str(value).strip().lower().replace(" ", "_")
    if not token or not _SAFE_REASON_RE.fullmatch(token):
        raise ValueError(f"{field_name} must be a safe reason code")
    if has_unsafe_marker(token) or sanitize_public_text(token) != token:
        raise ValueError(f"{field_name} contains private or unsafe material")
    return token


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


__all__ = [
    "AutomaticExecutionDecision",
    "ReplayPolicySnapshotBinding",
    "RollbackConfig",
    "RollbackReceipt",
    "RollbackRequest",
    "RollbackResult",
    "RollbackScope",
    "RollbackService",
    "RollbackStatus",
    "compute_self_improvement_rollback_action_digest",
    "preserve_replay_policy_snapshot",
]
