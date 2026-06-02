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
from openmagi_core_agent.self_improvement.review_gate import (
    SelfImprovementAuthorityFlags,
    SelfImprovementPromotionScope,
)


SelfImprovementPromotionStatus: TypeAlias = Literal[
    "disabled",
    "blocked",
    "promotion_ready_local_fake",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class SelfImprovementPromotionConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_promotion_enabled: bool = Field(
        default=False,
        alias="localFakePromotionEnabled",
    )
    production_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionMutationEnabled",
    )
    automatic_promotion_enabled: Literal[False] = Field(
        default=False,
        alias="automaticPromotionEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_live_flags_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionMutationEnabled"] = False
        payload.pop("production_mutation_enabled", None)
        payload["automaticPromotionEnabled"] = False
        payload.pop("automatic_promotion_enabled", None)
        return payload

    @field_serializer("production_mutation_enabled", "automatic_promotion_enabled")
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
        _ = include, exclude, update, deep
        return self.model_copy(update=update)


class SelfImprovementPromotionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    proposal_digest: str = Field(alias="proposalDigest")
    affected_digest: str = Field(alias="affectedDigest")
    promotion_scope: SelfImprovementPromotionScope = Field(alias="promotionScope")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    approval_receipt: ApprovalReceipt = Field(alias="approvalReceipt")
    now: datetime
    eval_gate_ok: bool = Field(alias="evalGateOk")
    eval_gate_reason_codes: tuple[str, ...] = Field(
        default=(),
        alias="evalGateReasonCodes",
    )
    selector_fallback_occurred: bool = Field(alias="selectorFallbackOccurred")
    raw_projection_fixture_passed: bool = Field(alias="rawProjectionFixturePassed")
    plugin_sandbox_overreach_fixture_passed: bool = Field(
        alias="pluginSandboxOverreachFixturePassed",
    )
    hard_invariant_downgraded: bool = Field(alias="hardInvariantDowngraded")
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

    @model_validator(mode="before")
    @classmethod
    def _normalize_field_names(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        aliases = {
            "request_id": "requestId",
            "proposal_digest": "proposalDigest",
            "affected_digest": "affectedDigest",
            "promotion_scope": "promotionScope",
            "policy_snapshot_digest": "policySnapshotDigest",
            "approval_receipt": "approvalReceipt",
            "eval_gate_ok": "evalGateOk",
            "eval_gate_reason_codes": "evalGateReasonCodes",
            "selector_fallback_occurred": "selectorFallbackOccurred",
            "raw_projection_fixture_passed": "rawProjectionFixturePassed",
            "plugin_sandbox_overreach_fixture_passed": "pluginSandboxOverreachFixturePassed",
            "hard_invariant_downgraded": "hardInvariantDowngraded",
        }
        for field_name, alias in aliases.items():
            if field_name in payload:
                payload[alias] = payload.pop(field_name)
        return payload

    @field_validator("proposal_digest", "affected_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value, "digest")

    @field_validator("approval_receipt", mode="before")
    @classmethod
    def _validate_approval_receipt(cls, value: object) -> ApprovalReceipt:
        if isinstance(value, ApprovalReceipt):
            return ApprovalReceipt.model_validate(value.model_dump(by_alias=True, mode="json"))
        return ApprovalReceipt.model_validate(value)

    @field_validator("eval_gate_reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "evalGateReasonCodes") for item in _string_tuple(value))


class SelfImprovementPromotionResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementPromotionResult.v1"] = Field(
        default="selfImprovementPromotionResult.v1",
        alias="schemaVersion",
    )
    status: SelfImprovementPromotionStatus
    promotion_action_digest: str = Field(alias="promotionActionDigest")
    promotion_receipt_digest: str = Field(alias="promotionReceiptDigest")
    approval_verification_ok: bool = Field(alias="approvalVerificationOk")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    execution_default: Literal["denied"] = Field(default="denied", alias="executionDefault")
    authority_flags: SelfImprovementAuthorityFlags = Field(
        default_factory=SelfImprovementAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_denied_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "promotion_action_digest" in payload:
            payload["promotionActionDigest"] = payload.pop("promotion_action_digest")
        if "promotion_receipt_digest" in payload:
            payload["promotionReceiptDigest"] = payload.pop("promotion_receipt_digest")
        if "approval_verification_ok" in payload:
            payload["approvalVerificationOk"] = payload.pop("approval_verification_ok")
        if "reason_codes" in payload:
            payload["reasonCodes"] = payload.pop("reason_codes")
        if "blocked_reason" in payload:
            payload["blockedReason"] = payload.pop("blocked_reason")
        payload["executionDefault"] = "denied"
        payload.pop("execution_default", None)
        payload["authorityFlags"] = SelfImprovementAuthorityFlags().model_dump(by_alias=True)
        payload.pop("authority_flags", None)
        return payload

    @field_validator("promotion_action_digest", "promotion_receipt_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value, "digest")

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "reasonCodes") for item in _string_tuple(value))

    @field_validator("blocked_reason")
    @classmethod
    def _validate_blocked_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_reason_code(value, "blockedReason")

    @model_validator(mode="after")
    def _validate_receipt_digest(self) -> Self:
        if self.promotion_receipt_digest != canonical_digest(_promotion_receipt_payload(self)):
            raise ValueError("promotionReceiptDigest mismatch")
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
        _ = include, exclude, update, deep
        return self.model_copy(update=update)

    @field_serializer("execution_default")
    def _serialize_execution_default(self, _value: object) -> str:
        return "denied"


class SelfImprovementPromotionGate:
    def __init__(
        self,
        config: SelfImprovementPromotionConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, SelfImprovementPromotionConfig)
            else SelfImprovementPromotionConfig.model_validate(config or {})
        )

    def evaluate(
        self,
        request: SelfImprovementPromotionRequest | Mapping[str, object],
    ) -> SelfImprovementPromotionResult:
        parsed = (
            SelfImprovementPromotionRequest.model_validate(request.model_dump(by_alias=True))
            if isinstance(request, SelfImprovementPromotionRequest)
            else SelfImprovementPromotionRequest.model_validate(request)
        )
        action_digest = compute_self_improvement_promotion_action_digest(
            proposalDigest=parsed.proposal_digest,
            affectedDigest=parsed.affected_digest,
            promotionScope=parsed.promotion_scope,
            policySnapshotDigest=parsed.policy_snapshot_digest,
        )
        if not self.config.enabled:
            return _promotion_result(
                status="disabled",
                promotion_action_digest=action_digest,
                approval_verification_ok=False,
                reason_codes=("self_improvement_promotion_disabled",),
                blocked_reason="self_improvement_promotion_disabled",
            )
        if not self.config.local_fake_promotion_enabled:
            return _promotion_result(
                status="blocked",
                promotion_action_digest=action_digest,
                approval_verification_ok=False,
                reason_codes=("self_improvement_local_fake_promotion_disabled",),
                blocked_reason="self_improvement_local_fake_promotion_disabled",
            )

        approval = verify_approval_receipt_for_action(
            parsed.approval_receipt,
            actionDigest=action_digest,
            requiredScope="workflow_run",
            requiredActionKind="workflow_run",
            effectivePolicySnapshotDigest=parsed.policy_snapshot_digest,
            now=parsed.now,
        )
        constraint_reasons = _approval_constraint_reasons(parsed.approval_receipt, parsed)
        approval_ok = approval.ok and not constraint_reasons
        reasons: list[str] = [*approval.reason_codes, *constraint_reasons]
        if not parsed.eval_gate_ok or parsed.eval_gate_reason_codes:
            reasons.append("eval_regression_detected")
            reasons.extend(parsed.eval_gate_reason_codes)
        if parsed.selector_fallback_occurred:
            reasons.append("selector_fallback_detected")
        if parsed.raw_projection_fixture_passed:
            reasons.append("raw_projection_detected")
        if parsed.plugin_sandbox_overreach_fixture_passed:
            reasons.append("plugin_sandbox_overreach_detected")
        if parsed.hard_invariant_downgraded:
            reasons.append("hard_invariant_downgrade_detected")

        unique_reasons = tuple(dict.fromkeys(reasons))
        status: SelfImprovementPromotionStatus = (
            "blocked" if unique_reasons else "promotion_ready_local_fake"
        )
        return _promotion_result(
            status=status,
            promotion_action_digest=action_digest,
            approval_verification_ok=approval_ok,
            reason_codes=unique_reasons,
            blocked_reason=unique_reasons[0] if unique_reasons else None,
        )


def compute_self_improvement_promotion_action_digest(
    *,
    proposalDigest: str,
    affectedDigest: str,
    promotionScope: str,
    policySnapshotDigest: str,
) -> str:
    if promotionScope not in {"recipe", "harness_config", "plugin_config", "test_fixture", "docs"}:
        raise ValueError("promotionScope must be an allowed self-improvement promotion scope")
    return canonical_digest(
        {
            "schemaVersion": "selfImprovementPromotionAction.v1",
            "proposalDigest": _safe_digest(proposalDigest, "proposalDigest"),
            "affectedDigest": _safe_digest(affectedDigest, "affectedDigest"),
            "promotionScope": promotionScope,
            "policySnapshotDigest": _safe_digest(policySnapshotDigest, "policySnapshotDigest"),
        }
    )


def _promotion_result(
    *,
    status: SelfImprovementPromotionStatus,
    promotion_action_digest: str,
    approval_verification_ok: bool,
    reason_codes: tuple[str, ...],
    blocked_reason: str | None,
) -> SelfImprovementPromotionResult:
    payload = {
        "schemaVersion": "selfImprovementPromotionResult.v1",
        "status": status,
        "promotionActionDigest": promotion_action_digest,
        "approvalVerificationOk": approval_verification_ok,
        "reasonCodes": reason_codes,
        "blockedReason": blocked_reason,
        "executionDefault": "denied",
        "authorityFlags": SelfImprovementAuthorityFlags().model_dump(by_alias=True),
    }
    return SelfImprovementPromotionResult.model_validate(
        payload | {"promotionReceiptDigest": canonical_digest(payload)}
    )


def _promotion_receipt_payload(result: SelfImprovementPromotionResult) -> dict[str, object]:
    return result.model_dump(by_alias=True, exclude={"promotion_receipt_digest"})


def _approval_constraint_reasons(
    receipt: ApprovalReceipt,
    request: SelfImprovementPromotionRequest,
) -> tuple[str, ...]:
    constraints = receipt.constraints
    expected = {
        "proposalDigest": request.proposal_digest,
        "affectedDigest": request.affected_digest,
        "promotionScope": request.promotion_scope,
        "policySnapshotDigest": request.policy_snapshot_digest,
    }
    if any(constraints.get(key) != value for key, value in expected.items()):
        return ("approval_constraints_mismatch",)
    return ()


def _safe_digest(value: str, field_name: str) -> str:
    raw = str(value).strip()
    if not _SHA256_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be sha256:<64 lowercase hex>")
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
    "SelfImprovementPromotionConfig",
    "SelfImprovementPromotionGate",
    "SelfImprovementPromotionRequest",
    "SelfImprovementPromotionResult",
    "SelfImprovementPromotionStatus",
    "compute_self_improvement_promotion_action_digest",
]
