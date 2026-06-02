from __future__ import annotations

from collections.abc import Mapping
import re
from types import MappingProxyType
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    StrictBool,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)


PromotionStage = Literal["staging", "canary", "production"]
ReleaseGateReasonCode = Literal[
    "raw_projection_leak",
    "selector_fallback",
    "selector_governed_mismatch",
    "approval_bypass",
    "plugin_sandbox_overreach",
    "hard_invariant_downgrade",
    "missing_rollback_ref",
    "missing_owner_approval_ref",
    "missing_canary_proof_ref",
    "cost_threshold_exceeded",
    "tool_threshold_exceeded",
    "eval_threshold_failed",
]
HardInvariantMode = Literal[
    "block",
    "repair",
    "ask_user",
    "require_approval",
    "abstain",
    "fallback",
    "escalate_model",
    "log_only",
    "disabled",
]

ADK_EVALUATION_BOUNDARY = MappingProxyType({
    "adkEvaluationImported": False,
    "boundary": "contract_only_local_evaluation_boundary",
    "rationale": (
        "Release gate contracts validate digest-only promotion evidence locally and do not "
        "replace ADK Evaluation suites."
    ),
    "futureAdkPrimitive": "Evaluation",
})

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SECRET_SHAPED_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:sk|pk|rk|xox[baprs]|gh[pousr]|AKIA|ASIA)[_-]?[a-z0-9][a-z0-9_-]{8,}"
)
_UNSAFE_PUBLIC_FRAGMENTS = (
    "api_key",
    "apikey",
    "auth_header",
    "authheader",
    "authentication",
    "author" + "ization",
    "bearer",
    "coo" + "kie",
    "cred" + "ential",
    "hidden_reasoning",
    "hiddenreasoning",
    "oauth",
    "pass" + "word",
    "priv" + "ate",
    "pro" + "mpt",
    "raw",
    "raw_output",
    "rawoutput",
    "sess" + "ion",
    "se" + "cret",
    "tool_output",
    "tooloutput",
    "to" + "ken",
    ".env",
)
_UNSAFE_PATH_MARKERS = (
    "/users/",
    "/private/",
    "/var/",
    "/workspace/",
    "/data/",
    "\\users\\",
    "\\private\\",
)
_INVALID_METADATA_SENTINEL = "__invalid_release_gate_metadata__"
_INVALID_REF_SENTINEL = "__invalid_release_gate_ref__"
_INVALID_DIGEST_SENTINEL = "invalid-release-gate-digest"
_INVALID_PROJECTION_SENTINEL = {
    "projectionDigest": _INVALID_DIGEST_SENTINEL,
    "policyDigest": _INVALID_DIGEST_SENTINEL,
    "decisionDigest": _INVALID_DIGEST_SENTINEL,
    "sourceSnapshotDigest": _INVALID_DIGEST_SENTINEL,
    "publicMetadata": {_INVALID_METADATA_SENTINEL: ""},
}
_INVALID_ADK_EVALUATION_BOUNDARY_SENTINEL = {
    "adkEvaluationImported": False,
    "boundary": _INVALID_REF_SENTINEL,
    "rationale": _INVALID_REF_SENTINEL,
    "futureAdkPrimitive": _INVALID_REF_SENTINEL,
}
_AUTHORITY_FLAG_FIELDS = (
    "evaluation_attached",
    "adk_evaluation_imported",
    "adk_runner_invoked",
    "model_called",
    "toolhost_dispatched",
    "live_tool_dispatched",
    "traffic_attached",
    "production_authority",
    "route_or_api_attached",
    "runtime_activation_allowed",
)
_MAX_TRUSTED_COST_MICROS = 10_000_000
_MAX_TRUSTED_TOOL_INVOCATIONS = 250
_MIN_TRUSTED_EVAL_SCORE = 0.5
_MAX_TRUSTED_EVAL_FAILURE_RATE = 0.25
_SAFE_INTERNAL_LITERALS = frozenset({
    "raw_projection_leak",
    "selector_fallback",
    "selector_governed_mismatch",
    "approval_bypass",
    "plugin_sandbox_overreach",
    "hard_invariant_downgrade",
    "missing_rollback_ref",
    "missing_owner_approval_ref",
    "missing_canary_proof_ref",
    "cost_threshold_exceeded",
    "tool_threshold_exceeded",
    "eval_threshold_failed",
    "block",
    "repair",
    "ask_user",
    "require_approval",
    "abstain",
    "fallback",
    "escalate_model",
    "log_only",
    "disabled",
    "staging",
    "canary",
    "production",
    "releaseGateProjection.v1",
    "promotionGateRecord.v1",
    "contract_only_local_evaluation_boundary",
    "Evaluation",
})


class _ReleaseGateModel(BaseModel):
    model_config = _MODEL_CONFIG

    def __init__(self, **data: Any) -> None:
        super().__init__(**_normalize_release_gate_contract_input(data, type(self)))

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        return super().model_validate(
            _normalize_release_gate_contract_input(obj, cls),
            *args,
            **kwargs,
        )

    @model_validator(mode="before")
    @classmethod
    def _normalize_subclass_input_before_errors(cls, value: object) -> object:
        return _normalize_release_gate_contract_input(value, cls)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        contract_type = _contract_model_type(cls)
        return contract_type.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(_canonical_update_aliases(type(self), update))
        _ = deep
        contract_type = _contract_model_type(type(self))
        return contract_type.model_validate(data)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        _ = args
        by_alias = bool(kwargs.get("by_alias", False))
        mode = kwargs.get("mode", "python")
        return _safe_release_gate_serialized_data(self, by_alias=by_alias, mode=mode)

    def __repr_args__(self) -> Any:
        try:
            data = _release_gate_contract_field_data(self, _contract_model_type(type(self)), by_alias=False)
        except Exception:
            return ()
        return tuple(data.items())

    def __repr__(self) -> str:
        return _safe_release_gate_repr(self)

    def __str__(self) -> str:
        return _safe_release_gate_repr(self)

    @model_serializer(mode="plain")
    def _serialize_with_validation(self, info: SerializationInfo) -> Mapping[str, Any]:
        return _safe_release_gate_serialized_data(
            self,
            by_alias=bool(info.by_alias),
            mode=info.mode,
        )


class ReleaseGateAuthorityFlags(_ReleaseGateModel):
    evaluation_attached: Literal[False] = Field(default=False, alias="evaluationAttached")
    adk_evaluation_imported: Literal[False] = Field(default=False, alias="adkEvaluationImported")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    runtime_activation_allowed: Literal[False] = Field(default=False, alias="runtimeActivationAllowed")

    def __getattribute__(self, name: str) -> object:
        if name in _AUTHORITY_FLAG_FIELDS:
            return False
        return super().__getattribute__(name)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = (_fields_set, values)
        return cls(**{field.alias or name: False for name, field in cls.model_fields.items()})

    @field_serializer(
        "evaluation_attached",
        "adk_evaluation_imported",
        "adk_runner_invoked",
        "model_called",
        "toolhost_dispatched",
        "live_tool_dispatched",
        "traffic_attached",
        "production_authority",
        "route_or_api_attached",
        "runtime_activation_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class EvalThresholds(_ReleaseGateModel):
    max_cost_micros: int = Field(alias="maxCostMicros", ge=0)
    max_tool_invocations: int = Field(alias="maxToolInvocations", ge=0)
    min_eval_score: float = Field(alias="minEvalScore", ge=0.0, le=1.0)
    max_eval_failure_rate: float = Field(alias="maxEvalFailureRate", ge=0.0, le=1.0)
    threshold_policy_digest: str = Field(alias="thresholdPolicyDigest")
    verified: StrictBool

    @field_validator("threshold_policy_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("threshold_policy_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)

    @model_validator(mode="after")
    def _validate_not_disabled(self) -> Self:
        if not self.verified:
            raise ValueError("eval threshold policy must be verified")
        if self.max_cost_micros > _MAX_TRUSTED_COST_MICROS:
            raise ValueError("maxCostMicros disables release gate cost guard")
        if self.max_tool_invocations > _MAX_TRUSTED_TOOL_INVOCATIONS:
            raise ValueError("maxToolInvocations disables release gate tool guard")
        if self.min_eval_score < _MIN_TRUSTED_EVAL_SCORE:
            raise ValueError("minEvalScore disables release gate quality guard")
        if self.max_eval_failure_rate > _MAX_TRUSTED_EVAL_FAILURE_RATE:
            raise ValueError("maxEvalFailureRate disables release gate failure guard")
        return self


class EvalObservationSet(_ReleaseGateModel):
    cost_micros: int = Field(alias="costMicros", ge=0)
    tool_invocations: int = Field(alias="toolInvocations", ge=0)
    eval_score: float = Field(alias="evalScore", ge=0.0, le=1.0)
    eval_failure_rate: float = Field(alias="evalFailureRate", ge=0.0, le=1.0)


class DigestOnlyProjection(_ReleaseGateModel):
    schema_version: Literal["releaseGateProjection.v1"] = Field(
        default="releaseGateProjection.v1",
        alias="schemaVersion",
    )
    projection_digest: str = Field(alias="projectionDigest")
    policy_digest: str = Field(alias="policyDigest")
    decision_digest: str = Field(alias="decisionDigest")
    source_snapshot_digest: str = Field(alias="sourceSnapshotDigest")
    public_metadata: Mapping[str, str] = Field(default_factory=dict, alias="publicMetadata")

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_metadata_before_errors(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = _redact_public_mapping_before_errors(
            value,
            digest_fields=(
                "projectionDigest",
                "projection_digest",
                "policyDigest",
                "policy_digest",
                "decisionDigest",
                "decision_digest",
                "sourceSnapshotDigest",
                "source_snapshot_digest",
            ),
        )
        metadata = data.get("publicMetadata", data.get("public_metadata", {}))
        if (
            isinstance(metadata, Mapping)
            and any(_contains_unsafe_public_string(key) or _contains_unsafe_public_string(item) for key, item in metadata.items())
        ) or (not isinstance(metadata, Mapping) and _contains_unsafe_public_string(metadata)):
            if "publicMetadata" in data:
                data["publicMetadata"] = {_INVALID_METADATA_SENTINEL: ""}
            if "public_metadata" in data:
                data["public_metadata"] = {_INVALID_METADATA_SENTINEL: ""}
        return data

    @field_validator(
        "projection_digest",
        "policy_digest",
        "decision_digest",
        "source_snapshot_digest",
        mode="before",
    )
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("public_metadata")
    @classmethod
    def _validate_metadata(cls, metadata: Mapping[str, str]) -> Mapping[str, str]:
        for key, value in metadata.items():
            if key == _INVALID_METADATA_SENTINEL or not _safe_metadata_pair(key, value):
                raise ValueError("publicMetadata must contain public-safe metadata only")
        return metadata

    @field_validator(
        "projection_digest",
        "policy_digest",
        "decision_digest",
        "source_snapshot_digest",
    )
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)


class SelectorGateDecision(_ReleaseGateModel):
    selector_ref: str = Field(alias="selectorRef")
    selected_ref: str = Field(alias="selectedRef")
    expected_governed: StrictBool = Field(alias="expectedGoverned")
    actual_governed: StrictBool = Field(alias="actualGoverned")
    used_fallback: StrictBool = Field(default=False, alias="usedFallback")
    governed_policy_digest: str = Field(alias="governedPolicyDigest")

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            ref_fields=("selectorRef", "selector_ref", "selectedRef", "selected_ref"),
            digest_fields=("governedPolicyDigest", "governed_policy_digest"),
        )

    @field_validator("selector_ref", "selected_ref", mode="before")
    @classmethod
    def _sanitize_ref_before_errors(cls, value: object) -> object:
        return _redact_unsafe_ref_before_errors(value)

    @field_validator("governed_policy_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("selector_ref", "selected_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _validate_public_ref(value)

    @field_validator("governed_policy_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)


class CanaryProofRef(_ReleaseGateModel):
    proof_ref: str = Field(alias="proofRef")
    proof_digest: str = Field(alias="proofDigest")
    verified: StrictBool

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            ref_fields=("proofRef", "proof_ref"),
            digest_fields=("proofDigest", "proof_digest"),
        )

    @field_validator("proof_ref", mode="before")
    @classmethod
    def _sanitize_ref_before_errors(cls, value: object) -> object:
        return _redact_unsafe_ref_before_errors(value)

    @field_validator("proof_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("proof_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _validate_public_ref(value)

    @field_validator("proof_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)


class RollbackRef(_ReleaseGateModel):
    rollback_ref: str = Field(alias="rollbackRef")
    rollback_plan_digest: str = Field(alias="rollbackPlanDigest")
    previous_snapshot_digest: str = Field(alias="previousSnapshotDigest")
    verified: StrictBool

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            ref_fields=("rollbackRef", "rollback_ref"),
            digest_fields=(
                "rollbackPlanDigest",
                "rollback_plan_digest",
                "previousSnapshotDigest",
                "previous_snapshot_digest",
            ),
        )

    @field_validator("rollback_ref", mode="before")
    @classmethod
    def _sanitize_ref_before_errors(cls, value: object) -> object:
        return _redact_unsafe_ref_before_errors(value)

    @field_validator("rollback_plan_digest", "previous_snapshot_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("rollback_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _validate_public_ref(value)

    @field_validator("rollback_plan_digest", "previous_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)


class OwnerApprovalRef(_ReleaseGateModel):
    approval_ref: str = Field(alias="approvalRef")
    owner_ref: str = Field(alias="ownerRef")
    approval_digest: str = Field(alias="approvalDigest")
    approved: StrictBool
    verified: StrictBool
    bypass_detected: StrictBool = Field(default=False, alias="bypassDetected")

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            ref_fields=("approvalRef", "approval_ref", "ownerRef", "owner_ref"),
            digest_fields=("approvalDigest", "approval_digest"),
        )

    @field_validator("approval_ref", "owner_ref", mode="before")
    @classmethod
    def _sanitize_ref_before_errors(cls, value: object) -> object:
        return _redact_unsafe_ref_before_errors(value)

    @field_validator("approval_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("approval_ref", "owner_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _validate_public_ref(value)

    @field_validator("approval_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)


class PluginSandboxObservation(_ReleaseGateModel):
    plugin_ref: str = Field(alias="pluginRef")
    sandbox_policy_digest: str = Field(alias="sandboxPolicyDigest")
    overreach_detected: StrictBool = Field(default=False, alias="overreachDetected")

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            ref_fields=("pluginRef", "plugin_ref"),
            digest_fields=("sandboxPolicyDigest", "sandbox_policy_digest"),
        )

    @field_validator("plugin_ref", mode="before")
    @classmethod
    def _sanitize_ref_before_errors(cls, value: object) -> object:
        return _redact_unsafe_ref_before_errors(value)

    @field_validator("sandbox_policy_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("plugin_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _validate_public_ref(value)

    @field_validator("sandbox_policy_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)


class HardInvariantEvaluation(_ReleaseGateModel):
    invariant_ref: str = Field(alias="invariantRef")
    configured_mode: HardInvariantMode = Field(alias="configuredMode")
    downgraded: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            ref_fields=("invariantRef", "invariant_ref"),
        )

    @field_validator("invariant_ref", mode="before")
    @classmethod
    def _sanitize_ref_before_errors(cls, value: object) -> object:
        return _redact_unsafe_ref_before_errors(value)

    @field_validator("invariant_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _validate_public_ref(value)


class PromotionRequest(_ReleaseGateModel):
    promotion_id: str = Field(alias="promotionId")
    candidate_snapshot_digest: str = Field(alias="candidateSnapshotDigest")
    target_stage: PromotionStage = Field(alias="targetStage")
    thresholds: EvalThresholds
    observations: EvalObservationSet
    projection: DigestOnlyProjection
    selector_decision: SelectorGateDecision = Field(alias="selectorDecision")
    canary_proof_refs: tuple[CanaryProofRef, ...] = Field(
        default=(),
        alias="canaryProofRefs",
    )
    rollback_ref: RollbackRef | None = Field(default=None, alias="rollbackRef")
    owner_approval_refs: tuple[OwnerApprovalRef, ...] = Field(
        default=(),
        alias="ownerApprovalRefs",
    )
    plugin_sandbox_observations: tuple[PluginSandboxObservation, ...] = Field(
        default=(),
        alias="pluginSandboxObservations",
    )
    hard_invariant_evaluations: tuple[HardInvariantEvaluation, ...] = Field(
        default=(),
        alias="hardInvariantEvaluations",
    )
    raw_projection_leak_detected: StrictBool = Field(
        default=False,
        alias="rawProjectionLeakDetected",
    )
    authority_flags: ReleaseGateAuthorityFlags = Field(
        default_factory=ReleaseGateAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            ref_fields=("promotionId", "promotion_id"),
            digest_fields=("candidateSnapshotDigest", "candidate_snapshot_digest"),
        )

    @field_validator("projection", mode="before")
    @classmethod
    def _reject_projection_subclasses_before_errors(cls, value: object) -> object:
        if isinstance(value, DigestOnlyProjection) and type(value) is not DigestOnlyProjection:
            return dict(_INVALID_PROJECTION_SENTINEL)
        return value

    @field_validator(
        "thresholds",
        "observations",
        "selector_decision",
        "rollback_ref",
        "authority_flags",
        mode="before",
    )
    @classmethod
    def _normalize_single_nested_contract_before_errors(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        expected_types: dict[str, type[_ReleaseGateModel]] = {
            "thresholds": EvalThresholds,
            "observations": EvalObservationSet,
            "selector_decision": SelectorGateDecision,
            "rollback_ref": RollbackRef,
            "authority_flags": ReleaseGateAuthorityFlags,
        }
        if value is None:
            return value
        return _normalize_release_gate_contract_input(value, expected_types[info.field_name])

    @field_validator(
        "canary_proof_refs",
        "owner_approval_refs",
        "plugin_sandbox_observations",
        "hard_invariant_evaluations",
        mode="before",
    )
    @classmethod
    def _normalize_sequence_nested_contracts_before_errors(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        expected_types: dict[str, type[_ReleaseGateModel]] = {
            "canary_proof_refs": CanaryProofRef,
            "owner_approval_refs": OwnerApprovalRef,
            "plugin_sandbox_observations": PluginSandboxObservation,
            "hard_invariant_evaluations": HardInvariantEvaluation,
        }
        return _normalize_release_gate_contract_sequence(value, expected_types[info.field_name])

    @field_validator("promotion_id", mode="before")
    @classmethod
    def _sanitize_ref_before_errors(cls, value: object) -> object:
        return _redact_unsafe_ref_before_errors(value)

    @field_validator("candidate_snapshot_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("promotion_id")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _validate_public_ref(value)

    @field_validator("candidate_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)


class PromotionResult(_ReleaseGateModel):
    promotion_id: str = Field(alias="promotionId")
    allowed: StrictBool
    reason_codes: tuple[ReleaseGateReasonCode, ...] = Field(alias="reasonCodes")
    projection: DigestOnlyProjection
    authority_flags: ReleaseGateAuthorityFlags = Field(alias="authorityFlags")
    adk_evaluation_boundary: Mapping[str, object] = Field(alias="adkEvaluationBoundary")

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = _redact_public_mapping_before_errors(
            value,
            ref_fields=("promotionId", "promotion_id"),
        )
        boundary_key = (
            "adkEvaluationBoundary"
            if "adkEvaluationBoundary" in data
            else "adk_evaluation_boundary"
        )
        if boundary_key in data and isinstance(data[boundary_key], Mapping):
            boundary = data[boundary_key]
            if dict(boundary) != dict(ADK_EVALUATION_BOUNDARY):
                data[boundary_key] = dict(_INVALID_ADK_EVALUATION_BOUNDARY_SENTINEL)
        return data

    @field_validator("projection", mode="before")
    @classmethod
    def _reject_projection_subclasses_before_errors(cls, value: object) -> object:
        if isinstance(value, DigestOnlyProjection) and type(value) is not DigestOnlyProjection:
            return dict(_INVALID_PROJECTION_SENTINEL)
        return value

    @field_validator("authority_flags", mode="before")
    @classmethod
    def _normalize_authority_flags_before_errors(cls, value: object) -> object:
        return _normalize_release_gate_contract_input(value, ReleaseGateAuthorityFlags)

    @field_validator("adk_evaluation_boundary", mode="before")
    @classmethod
    def _sanitize_adk_evaluation_boundary_before_errors(cls, value: object) -> object:
        if not isinstance(value, Mapping) or dict(value) != dict(ADK_EVALUATION_BOUNDARY):
            return dict(_INVALID_ADK_EVALUATION_BOUNDARY_SENTINEL)
        return value

    @field_validator("adk_evaluation_boundary")
    @classmethod
    def _validate_adk_evaluation_boundary(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        if dict(value) != dict(ADK_EVALUATION_BOUNDARY):
            raise ValueError("adkEvaluationBoundary must match default-off release gate boundary")
        return value

    @field_serializer("adk_evaluation_boundary")
    def _serialize_adk_evaluation_boundary(self, _value: object) -> Mapping[str, object]:
        return dict(ADK_EVALUATION_BOUNDARY)


class PromotionGateRecord(_ReleaseGateModel):
    schema_version: Literal["promotionGateRecord.v1"] = Field(
        default="promotionGateRecord.v1",
        alias="schemaVersion",
    )
    request: PromotionRequest
    result: PromotionResult
    record_digest: str = Field(alias="recordDigest")

    @model_validator(mode="before")
    @classmethod
    def _redact_unsafe_inputs_before_errors(cls, value: object) -> object:
        return _redact_public_mapping_before_errors(
            value,
            digest_fields=("recordDigest", "record_digest"),
        )

    @field_validator("record_digest", mode="before")
    @classmethod
    def _sanitize_digest_before_errors(cls, value: object) -> object:
        return _redact_unsafe_digest_before_errors(value)

    @field_validator("request", mode="before")
    @classmethod
    def _normalize_request_before_errors(cls, value: object) -> object:
        return _normalize_release_gate_contract_input(value, PromotionRequest)

    @field_validator("result", mode="before")
    @classmethod
    def _normalize_result_before_errors(cls, value: object) -> object:
        return _normalize_release_gate_contract_input(value, PromotionResult)

    @field_validator("record_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _validate_digest(value)

    @model_validator(mode="after")
    def _validate_record(self) -> Self:
        if self.request.promotion_id != self.result.promotion_id:
            raise ValueError("promotion gate record request/result ids must match")
        expected_result = evaluate_promotion_request(self.request)
        if self.result.model_dump(by_alias=True, mode="json") != expected_result.model_dump(by_alias=True, mode="json"):
            raise ValueError("promotion gate record result must match evaluated request")
        return self


_CONTRACT_MODEL_TYPES: tuple[type[_ReleaseGateModel], ...] = (
    ReleaseGateAuthorityFlags,
    EvalThresholds,
    EvalObservationSet,
    DigestOnlyProjection,
    SelectorGateDecision,
    CanaryProofRef,
    RollbackRef,
    OwnerApprovalRef,
    PluginSandboxObservation,
    HardInvariantEvaluation,
    PromotionRequest,
    PromotionResult,
    PromotionGateRecord,
)


def evaluate_promotion_request(request: PromotionRequest) -> PromotionResult:
    request = PromotionRequest.model_validate(
        _normalize_release_gate_contract_input(request, PromotionRequest),
    )
    reasons: list[ReleaseGateReasonCode] = []
    if request.raw_projection_leak_detected:
        reasons.append("raw_projection_leak")
    if request.selector_decision.used_fallback:
        reasons.append("selector_fallback")
    if (
        request.selector_decision.expected_governed != request.selector_decision.actual_governed
        or not request.selector_decision.actual_governed
    ):
        reasons.append("selector_governed_mismatch")
    if not request.rollback_ref or not request.rollback_ref.verified:
        reasons.append("missing_rollback_ref")
    if not any(approval.approved and approval.verified for approval in request.owner_approval_refs):
        reasons.append("missing_owner_approval_ref")
    if any(approval.bypass_detected for approval in request.owner_approval_refs):
        reasons.append("approval_bypass")
    if not any(proof.verified for proof in request.canary_proof_refs):
        reasons.append("missing_canary_proof_ref")
    if any(
        observation.overreach_detected
        for observation in request.plugin_sandbox_observations
    ):
        reasons.append("plugin_sandbox_overreach")
    if any(
        invariant.downgraded or invariant.configured_mode in {"log_only", "disabled"}
        for invariant in request.hard_invariant_evaluations
    ):
        reasons.append("hard_invariant_downgrade")
    if request.observations.cost_micros > request.thresholds.max_cost_micros:
        reasons.append("cost_threshold_exceeded")
    if request.observations.tool_invocations > request.thresholds.max_tool_invocations:
        reasons.append("tool_threshold_exceeded")
    if (
        request.observations.eval_score < request.thresholds.min_eval_score
        or request.observations.eval_failure_rate > request.thresholds.max_eval_failure_rate
    ):
        reasons.append("eval_threshold_failed")
    return PromotionResult(
        promotionId=request.promotion_id,
        allowed=not reasons,
        reasonCodes=tuple(dict.fromkeys(reasons)),
        projection=request.projection,
        authorityFlags=ReleaseGateAuthorityFlags(),
        adkEvaluationBoundary=dict(ADK_EVALUATION_BOUNDARY),
    )


def _validate_digest(value: str) -> str:
    clean = value.strip()
    if not _DIGEST_RE.fullmatch(clean):
        raise ValueError("digest must be a sha256 digest reference")
    return clean


def _contract_model_type(model_type: type[object]) -> type[BaseModel]:
    if not issubclass(model_type, _ReleaseGateModel):
        raise ValueError("release gate contract serializer received unsupported model")
    for candidate in model_type.__mro__:
        if candidate is _ReleaseGateModel:
            break
        if candidate in _CONTRACT_MODEL_TYPES:
            return candidate
    raise ValueError("release gate contract serializer received unsupported subclass")


def _contract_model_public_keys(model_type: type[BaseModel]) -> set[str]:
    keys: set[str] = set()
    for name, field in model_type.model_fields.items():
        keys.add(name)
        if field.alias is not None:
            keys.add(field.alias)
    return keys


def _safe_schema_keys() -> frozenset[str]:
    return frozenset().union(
        *(_contract_model_public_keys(model_type) for model_type in _CONTRACT_MODEL_TYPES),
        set(ADK_EVALUATION_BOUNDARY),
    )


def _canonical_update_aliases(
    model_type: type[object],
    update: Mapping[str, Any],
) -> dict[str, Any]:
    contract_type = _contract_model_type(model_type)
    canonical: dict[str, Any] = {}
    for key, value in update.items():
        field = contract_type.model_fields.get(key)
        canonical[field.alias or key if field is not None else key] = value
    return canonical


def _normalize_release_gate_contract_input(
    value: object,
    expected_type: type[BaseModel],
) -> object:
    if not issubclass(expected_type, _ReleaseGateModel):
        return value
    try:
        contract_type = _contract_model_type(expected_type)
    except ValueError:
        return value
    if isinstance(value, contract_type):
        return _release_gate_contract_field_data(value, contract_type)
    if isinstance(value, Mapping):
        return _sanitize_contract_mapping_extras(value, contract_type)
    return {_INVALID_REF_SENTINEL: ""}


def _normalize_release_gate_contract_sequence(
    value: object,
    expected_type: type[BaseModel],
) -> object:
    if value is None:
        return value
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(
            _normalize_release_gate_contract_input(item, expected_type)
            for item in value
        )
    return _sanitize_contract_value_before_errors(value)


def _release_gate_contract_field_data(
    value: object,
    expected_type: type[BaseModel],
    *,
    by_alias: bool = True,
) -> dict[str, object]:
    data: dict[str, object] = {}
    for name, field in expected_type.model_fields.items():
        try:
            raw_value = getattr(value, name)
        except Exception:
            raw_value = _INVALID_REF_SENTINEL
        key = field.alias if by_alias and field.alias is not None else name
        data[key] = _sanitize_contract_field_value(expected_type, name, raw_value)
    return data


def _sanitize_contract_field_value(
    expected_type: type[BaseModel],
    name: str,
    value: object,
) -> object:
    if expected_type is PromotionResult and name == "adk_evaluation_boundary":
        if isinstance(value, Mapping) and dict(value) == dict(ADK_EVALUATION_BOUNDARY):
            return dict(ADK_EVALUATION_BOUNDARY)
        return dict(_INVALID_ADK_EVALUATION_BOUNDARY_SENTINEL)
    if (
        expected_type in {PromotionRequest, PromotionResult}
        and name == "authority_flags"
    ):
        return ReleaseGateAuthorityFlags().model_dump(by_alias=True, mode="python")
    return _sanitize_contract_value_before_errors(value)


def _safe_release_gate_serialized_data(
    value: object,
    *,
    by_alias: bool,
    mode: str,
) -> dict[str, object]:
    contract_type = _contract_model_type(type(value))
    alias_data = _release_gate_contract_field_data(value, contract_type, by_alias=True)
    validated = contract_type.model_validate(alias_data)
    data = _release_gate_contract_field_data(validated, contract_type, by_alias=by_alias)
    serialized = _release_gate_json_safe_data(data) if mode == "json" else data
    _validate_contract_serialized_data(type(value), serialized)
    return serialized


def _safe_release_gate_repr(value: object) -> str:
    try:
        contract_type = _contract_model_type(type(value))
        data = _release_gate_contract_field_data(value, contract_type, by_alias=False)
    except Exception:
        return f"{type(value).__name__}()"
    rendered = ", ".join(f"{key}={item!r}" for key, item in data.items())
    return f"{contract_type.__name__}({rendered})"


def _release_gate_json_safe_data(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _release_gate_json_safe_data(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(_release_gate_json_safe_data(item) for item in value)
    return value


def _sanitize_contract_mapping_extras(
    value: Mapping[object, object],
    expected_type: type[BaseModel],
) -> dict[object, object]:
    allowed_keys = _contract_model_public_keys(expected_type)
    canonical_keys = {
        name: field.alias or name
        for name, field in expected_type.model_fields.items()
    }
    canonical_keys.update({
        field.alias: field.alias
        for field in expected_type.model_fields.values()
        if field.alias is not None
    })
    data: dict[object, object] = {}
    for key, item in value.items():
        if isinstance(key, str) and key in allowed_keys:
            canonical_key = canonical_keys.get(key, key)
            sanitized_item = _sanitize_contract_value_before_errors(item)
            if canonical_key in data and data[canonical_key] != sanitized_item:
                data[canonical_key] = _duplicate_field_sentinel(expected_type, canonical_key)
            else:
                data[canonical_key] = sanitized_item
    return data


def _duplicate_field_sentinel(
    expected_type: type[BaseModel],
    canonical_key: str,
) -> object:
    if expected_type is PromotionResult and canonical_key == "adkEvaluationBoundary":
        return dict(_INVALID_ADK_EVALUATION_BOUNDARY_SENTINEL)
    if (
        expected_type in {PromotionRequest, PromotionResult}
        and canonical_key == "authorityFlags"
    ):
        return ReleaseGateAuthorityFlags().model_dump(by_alias=True, mode="python")
    return _INVALID_REF_SENTINEL


def _sanitize_contract_value_before_errors(value: object) -> object:
    if isinstance(value, _ReleaseGateModel):
        try:
            contract_type = _contract_model_type(type(value))
        except ValueError:
            return _INVALID_REF_SENTINEL
        return _release_gate_contract_field_data(value, contract_type)
    if isinstance(value, Mapping):
        return {
            _sanitize_contract_mapping_key_before_errors(key): _sanitize_contract_value_before_errors(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return tuple(_sanitize_contract_value_before_errors(item) for item in value)
    if type(value) is str:
        if value in _SAFE_INTERNAL_LITERALS:
            return value
        if _contains_unsafe_public_string(value):
            return _INVALID_REF_SENTINEL
        return value
    if _safe_exact_scalar(value):
        return value
    return _INVALID_REF_SENTINEL


def _safe_exact_scalar(value: object) -> bool:
    return type(value) in {bool, int, float, str} or value is None


def _sanitize_contract_mapping_key_before_errors(value: object) -> object:
    if type(value) is str and (
        value in _safe_schema_keys() or not _contains_unsafe_public_string(value)
    ):
        return value
    return _INVALID_REF_SENTINEL


def _validate_contract_serialized_data(
    model_type: type[object],
    data: object,
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError("release gate serialization must produce contract mapping")
    contract_type = _contract_model_type(model_type)
    allowed_keys = _contract_model_public_keys(contract_type)
    if not set(data) <= allowed_keys:
        raise ValueError("release gate serialization contains non-contract fields")
    contract_type.model_validate(dict(data))


def _redact_unsafe_digest_before_errors(value: object) -> object:
    if _contains_unsafe_public_string(value):
        return _INVALID_DIGEST_SENTINEL
    return value


def _redact_unsafe_ref_before_errors(value: object) -> object:
    if _contains_unsafe_public_string(value):
        return _INVALID_REF_SENTINEL
    return value


def _contains_unsafe_public_string(value: object) -> bool:
    if _unsafe_marker_type(value):
        return True
    if isinstance(value, str):
        if type(value) is not str:
            return True
        return _unsafe_public_string(value)
    if isinstance(value, Mapping):
        return any(
            _contains_unsafe_public_string(key) or _contains_unsafe_public_string(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple | set | frozenset):
        return any(_contains_unsafe_public_string(item) for item in value)
    return False


def _redact_public_mapping_before_errors(
    value: object,
    *,
    ref_fields: tuple[str, ...] = (),
    digest_fields: tuple[str, ...] = (),
) -> object:
    if not isinstance(value, Mapping):
        return value
    data = dict(value)
    for field in ref_fields:
        if field in data:
            data[field] = _redact_unsafe_ref_before_errors(data[field])
    for field in digest_fields:
        if field in data:
            data[field] = _redact_unsafe_digest_before_errors(data[field])
    return data


def _validate_public_ref(value: str) -> str:
    if type(value) is not str:
        raise ValueError("reference must be a public-safe release gate reference")
    clean = value.strip()
    if not _PUBLIC_REF_RE.fullmatch(clean) or _unsafe_public_string(clean):
        raise ValueError("reference must be a public-safe release gate reference")
    return clean


def _safe_metadata_pair(key: str, value: str) -> bool:
    if type(key) is not str or type(value) is not str:
        return False
    if not key.strip() or not value.strip():
        return False
    if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.:-]{0,80}$", key):
        return False
    if not re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.:-]{0,180}$", value):
        return False
    return not (_unsafe_public_string(key) or _unsafe_public_string(value))


def _unsafe_public_string(value: str) -> bool:
    clean = value.strip()
    lowered = clean.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    return (
        _SECRET_SHAPED_RE.search(clean) is not None
        or any(fragment in lowered for fragment in _UNSAFE_PUBLIC_FRAGMENTS)
        or any(marker in lowered for marker in _UNSAFE_PATH_MARKERS)
        or any(fragment.replace("_", "") in compact for fragment in _UNSAFE_PUBLIC_FRAGMENTS)
        or "/" in clean
        or "\\" in clean
        or clean.startswith(("~", "."))
    )


def _unsafe_marker_type(value: object) -> bool:
    marker = " ".join(type_.__name__ for type_ in type(value).__mro__)
    lowered = marker.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    return any(fragment in lowered for fragment in _UNSAFE_PUBLIC_FRAGMENTS) or any(
        fragment.replace("_", "") in compact for fragment in _UNSAFE_PUBLIC_FRAGMENTS
    )
