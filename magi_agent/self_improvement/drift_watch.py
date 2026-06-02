from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.runtime.receipt_utils import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_text,
)
from magi_agent.self_improvement.review_gate import SelfImprovementAuthorityFlags


DriftWatchStatus: TypeAlias = Literal["disabled", "unchanged", "drift_detected"]
DriftReasonCode: TypeAlias = Literal[
    "self_improvement_drift_watch_disabled",
    "recipe_digest_changed",
    "harness_config_digest_changed",
    "plugin_config_digest_changed",
    "model_tier_changed",
    "policy_snapshot_changed",
    "eval_threshold_changed",
    "plugin_supply_chain_digest_changed",
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
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@=-]{1,191}$")


class DriftWatchConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_drift_watch_enabled: bool = Field(
        default=False,
        alias="localFakeDriftWatchEnabled",
    )
    automatic_rollback_enabled: Literal[False] = Field(
        default=False,
        alias="automaticRollbackEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_live_flags_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["automaticRollbackEnabled"] = False
        payload.pop("automatic_rollback_enabled", None)
        return payload

    @field_serializer("automatic_rollback_enabled")
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


class DriftWatchRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    baseline_recipe_digest: str = Field(alias="baselineRecipeDigest")
    candidate_recipe_digest: str = Field(alias="candidateRecipeDigest")
    baseline_harness_config_digest: str = Field(alias="baselineHarnessConfigDigest")
    candidate_harness_config_digest: str = Field(alias="candidateHarnessConfigDigest")
    baseline_plugin_config_digest: str = Field(alias="baselinePluginConfigDigest")
    candidate_plugin_config_digest: str = Field(alias="candidatePluginConfigDigest")
    baseline_model_tier_ref: str = Field(alias="baselineModelTierRef")
    candidate_model_tier_ref: str = Field(alias="candidateModelTierRef")
    baseline_policy_snapshot_digest: str = Field(alias="baselinePolicySnapshotDigest")
    candidate_policy_snapshot_digest: str = Field(alias="candidatePolicySnapshotDigest")
    baseline_eval_threshold_digest: str = Field(alias="baselineEvalThresholdDigest")
    candidate_eval_threshold_digest: str = Field(alias="candidateEvalThresholdDigest")
    baseline_plugin_supply_chain_digest: str = Field(
        alias="baselinePluginSupplyChainDigest",
    )
    candidate_plugin_supply_chain_digest: str = Field(
        alias="candidatePluginSupplyChainDigest",
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
        return _safe_ref(value, "requestId", prefixes=("self-improvement-drift:", "ref:"))

    @field_validator("baseline_model_tier_ref", "candidate_model_tier_ref")
    @classmethod
    def _validate_model_tier_ref(cls, value: str) -> str:
        return _safe_ref(value, "modelTierRef", prefixes=("model-tier:", "ref:"))

    @field_validator(
        "baseline_policy_snapshot_digest",
        "candidate_policy_snapshot_digest",
        "baseline_recipe_digest",
        "candidate_recipe_digest",
        "baseline_harness_config_digest",
        "candidate_harness_config_digest",
        "baseline_plugin_config_digest",
        "candidate_plugin_config_digest",
        "baseline_eval_threshold_digest",
        "candidate_eval_threshold_digest",
        "baseline_plugin_supply_chain_digest",
        "candidate_plugin_supply_chain_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value, "digest")


class DriftWatchResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementDriftWatchResult.v1"] = Field(
        default="selfImprovementDriftWatchResult.v1",
        alias="schemaVersion",
    )
    status: DriftWatchStatus
    drift_digest: str = Field(alias="driftDigest")
    reason_codes: tuple[DriftReasonCode, ...] = Field(default=(), alias="reasonCodes")
    baseline_recipe_digest: str = Field(alias="baselineRecipeDigest")
    candidate_recipe_digest: str = Field(alias="candidateRecipeDigest")
    baseline_harness_config_digest: str = Field(alias="baselineHarnessConfigDigest")
    candidate_harness_config_digest: str = Field(alias="candidateHarnessConfigDigest")
    baseline_plugin_config_digest: str = Field(alias="baselinePluginConfigDigest")
    candidate_plugin_config_digest: str = Field(alias="candidatePluginConfigDigest")
    baseline_model_tier_ref: str = Field(alias="baselineModelTierRef")
    candidate_model_tier_ref: str = Field(alias="candidateModelTierRef")
    baseline_policy_snapshot_digest: str = Field(alias="baselinePolicySnapshotDigest")
    candidate_policy_snapshot_digest: str = Field(alias="candidatePolicySnapshotDigest")
    baseline_eval_threshold_digest: str = Field(alias="baselineEvalThresholdDigest")
    candidate_eval_threshold_digest: str = Field(alias="candidateEvalThresholdDigest")
    baseline_plugin_supply_chain_digest: str = Field(
        alias="baselinePluginSupplyChainDigest",
    )
    candidate_plugin_supply_chain_digest: str = Field(
        alias="candidatePluginSupplyChainDigest",
    )
    authority_flags: SelfImprovementAuthorityFlags = Field(
        default_factory=SelfImprovementAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "drift_digest" in payload:
            payload["driftDigest"] = payload.pop("drift_digest")
        if "reason_codes" in payload:
            payload["reasonCodes"] = payload.pop("reason_codes")
        aliases = {
            "baseline_recipe_digest": "baselineRecipeDigest",
            "candidate_recipe_digest": "candidateRecipeDigest",
            "baseline_harness_config_digest": "baselineHarnessConfigDigest",
            "candidate_harness_config_digest": "candidateHarnessConfigDigest",
            "baseline_plugin_config_digest": "baselinePluginConfigDigest",
            "candidate_plugin_config_digest": "candidatePluginConfigDigest",
            "baseline_model_tier_ref": "baselineModelTierRef",
            "candidate_model_tier_ref": "candidateModelTierRef",
            "baseline_policy_snapshot_digest": "baselinePolicySnapshotDigest",
            "candidate_policy_snapshot_digest": "candidatePolicySnapshotDigest",
            "baseline_eval_threshold_digest": "baselineEvalThresholdDigest",
            "candidate_eval_threshold_digest": "candidateEvalThresholdDigest",
            "baseline_plugin_supply_chain_digest": "baselinePluginSupplyChainDigest",
            "candidate_plugin_supply_chain_digest": "candidatePluginSupplyChainDigest",
        }
        for field_name, alias in aliases.items():
            if field_name in payload:
                payload[alias] = payload.pop(field_name)
        payload["authorityFlags"] = SelfImprovementAuthorityFlags().model_dump(by_alias=True)
        payload.pop("authority_flags", None)
        return payload

    @field_validator("drift_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value, "driftDigest")

    @field_validator("baseline_model_tier_ref", "candidate_model_tier_ref")
    @classmethod
    def _validate_model_tier_ref(cls, value: str) -> str:
        return _safe_ref(value, "modelTierRef", prefixes=("model-tier:", "ref:"))

    @field_validator(
        "baseline_policy_snapshot_digest",
        "candidate_policy_snapshot_digest",
        "baseline_recipe_digest",
        "candidate_recipe_digest",
        "baseline_harness_config_digest",
        "candidate_harness_config_digest",
        "baseline_plugin_config_digest",
        "candidate_plugin_config_digest",
        "baseline_eval_threshold_digest",
        "candidate_eval_threshold_digest",
        "baseline_plugin_supply_chain_digest",
        "candidate_plugin_supply_chain_digest",
    )
    @classmethod
    def _validate_basis_digest(cls, value: str) -> str:
        return _safe_digest(value, "digest")

    @model_validator(mode="after")
    def _validate_drift_digest(self) -> Self:
        if self.drift_digest != canonical_digest(_drift_result_payload(self)):
            raise ValueError("driftDigest mismatch")
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
        _ = include, exclude, deep
        return self.model_copy(update=update)


class DriftWatchService:
    def __init__(self, config: DriftWatchConfig | Mapping[str, object] | None = None) -> None:
        self.config = (
            config
            if isinstance(config, DriftWatchConfig)
            else DriftWatchConfig.model_validate(config or {})
        )

    def evaluate(self, request: DriftWatchRequest | Mapping[str, object]) -> DriftWatchResult:
        parsed = (
            DriftWatchRequest.model_validate(request.model_dump(by_alias=True))
            if isinstance(request, DriftWatchRequest)
            else DriftWatchRequest.model_validate(request)
        )
        if not self.config.enabled or not self.config.local_fake_drift_watch_enabled:
            return _drift_result(
                status="disabled",
                reason_codes=("self_improvement_drift_watch_disabled",),
                request=parsed,
            )

        reasons: list[DriftReasonCode] = []
        if parsed.baseline_recipe_digest != parsed.candidate_recipe_digest:
            reasons.append("recipe_digest_changed")
        if parsed.baseline_harness_config_digest != parsed.candidate_harness_config_digest:
            reasons.append("harness_config_digest_changed")
        if parsed.baseline_plugin_config_digest != parsed.candidate_plugin_config_digest:
            reasons.append("plugin_config_digest_changed")
        if parsed.baseline_model_tier_ref != parsed.candidate_model_tier_ref:
            reasons.append("model_tier_changed")
        if parsed.baseline_policy_snapshot_digest != parsed.candidate_policy_snapshot_digest:
            reasons.append("policy_snapshot_changed")
        if parsed.baseline_eval_threshold_digest != parsed.candidate_eval_threshold_digest:
            reasons.append("eval_threshold_changed")
        if (
            parsed.baseline_plugin_supply_chain_digest
            != parsed.candidate_plugin_supply_chain_digest
        ):
            reasons.append("plugin_supply_chain_digest_changed")
        return _drift_result(
            status="drift_detected" if reasons else "unchanged",
            reason_codes=tuple(reasons),
            request=parsed,
        )


def _drift_result(
    *,
    status: DriftWatchStatus,
    reason_codes: tuple[DriftReasonCode, ...],
    request: DriftWatchRequest,
) -> DriftWatchResult:
    payload = {
        "schemaVersion": "selfImprovementDriftWatchResult.v1",
        "status": status,
        "reasonCodes": reason_codes,
        "baselineRecipeDigest": request.baseline_recipe_digest,
        "candidateRecipeDigest": request.candidate_recipe_digest,
        "baselineHarnessConfigDigest": request.baseline_harness_config_digest,
        "candidateHarnessConfigDigest": request.candidate_harness_config_digest,
        "baselinePluginConfigDigest": request.baseline_plugin_config_digest,
        "candidatePluginConfigDigest": request.candidate_plugin_config_digest,
        "baselineModelTierRef": request.baseline_model_tier_ref,
        "candidateModelTierRef": request.candidate_model_tier_ref,
        "baselinePolicySnapshotDigest": request.baseline_policy_snapshot_digest,
        "candidatePolicySnapshotDigest": request.candidate_policy_snapshot_digest,
        "baselineEvalThresholdDigest": request.baseline_eval_threshold_digest,
        "candidateEvalThresholdDigest": request.candidate_eval_threshold_digest,
        "baselinePluginSupplyChainDigest": request.baseline_plugin_supply_chain_digest,
        "candidatePluginSupplyChainDigest": request.candidate_plugin_supply_chain_digest,
        "authorityFlags": SelfImprovementAuthorityFlags().model_dump(by_alias=True),
    }
    return DriftWatchResult.model_validate(payload | {"driftDigest": canonical_digest(payload)})


def _drift_result_payload(result: DriftWatchResult) -> dict[str, object]:
    return result.model_dump(by_alias=True, exclude={"drift_digest"})


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


__all__ = [
    "DriftReasonCode",
    "DriftWatchConfig",
    "DriftWatchRequest",
    "DriftWatchResult",
    "DriftWatchService",
    "DriftWatchStatus",
]
