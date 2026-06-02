from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


EvalGateReasonCode = Literal[
    "selector_accuracy_below_threshold",
    "unsupported_claim_rate_exceeds_threshold",
    "approval_bypass_count_exceeds_threshold",
    "raw_governed_projection_fixture_passed_unexpectedly",
    "plugin_sandbox_overreach_fixture_passed_unexpectedly",
    "required_governed_selector_fixture_resolved_ungoverned",
    "required_hard_invariant_not_enforced",
]
SelectorSelectedKind = Literal["route", "workflow", "recipe"]
HardInvariantConfiguredMode = Literal[
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

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SAFE_RECIPE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
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
    "bearer",
    "credential",
    "auth",
    "oauth",
)
_RAW_MARKERS = (
    "raw:",
    "rawref",
    "rawtoollog",
    "rawchildtranscript",
    "childrawtoollog",
    "rawoutput",
    "rawresult",
    "hiddenreasoning",
    "privatememory",
)
_PROTECTED_COMPACT_MARKERS = tuple(
    "".join(character for character in marker if character.isalnum())
    for marker in _PROTECTED_FRAGMENTS + _RAW_MARKERS
)


class _FrozenNoUpdateModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for eval regression gate contracts")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class EvalGateThresholds(_FrozenNoUpdateModel):
    min_selector_accuracy: float = Field(alias="minSelectorAccuracy", ge=0.0, le=1.0)
    max_unsupported_claim_rate: float = Field(alias="maxUnsupportedClaimRate", ge=0.0, le=1.0)
    max_approval_bypass_count: int = Field(alias="maxApprovalBypassCount", ge=0)
    raw_projection_must_fail: StrictBool = Field(alias="rawProjectionMustFail")
    plugin_sandbox_overreach_must_fail: StrictBool = Field(alias="pluginSandboxOverreachMustFail")


class SelectorFixtureEvaluation(_FrozenNoUpdateModel):
    fixture_id: str = Field(alias="fixtureId")
    selected_ref: str = Field(alias="selectedRef")
    selected_kind: SelectorSelectedKind = Field(alias="selectedKind")
    required: StrictBool = True
    expected_governed: StrictBool = Field(alias="expectedGoverned")
    actual_governed: StrictBool = Field(alias="actualGoverned")

    @field_validator("fixture_id", "selected_ref")
    @classmethod
    def _validate_safe_public_ref(cls, value: str) -> str:
        return _safe_public_ref(value)


class HardInvariantPolicyEvaluation(_FrozenNoUpdateModel):
    invariant_id: str = Field(alias="invariantId")
    required: StrictBool = True
    configured_mode: HardInvariantConfiguredMode = Field(alias="configuredMode")

    @field_validator("invariant_id")
    @classmethod
    def _validate_invariant_id(cls, value: str) -> str:
        return _safe_public_ref(value)


class RecipeEvalMetrics(_FrozenNoUpdateModel):
    recipe_id: str = Field(alias="recipeId")
    selector_accuracy: float = Field(alias="selectorAccuracy", ge=0.0, le=1.0)
    unsupported_claim_rate: float = Field(alias="unsupportedClaimRate", ge=0.0, le=1.0)
    approval_bypass_count: int = Field(alias="approvalBypassCount", ge=0)
    raw_projection_fixture_passed: StrictBool = Field(alias="rawProjectionFixturePassed")
    plugin_sandbox_overreach_fixture_passed: StrictBool = Field(alias="pluginSandboxOverreachFixturePassed")
    selector_fixture_evaluations: tuple[SelectorFixtureEvaluation, ...] = Field(
        default=(),
        alias="selectorFixtureEvaluations",
    )
    hard_invariant_policy_evaluations: tuple[HardInvariantPolicyEvaluation, ...] = Field(
        default=(),
        alias="hardInvariantPolicyEvaluations",
    )

    @model_validator(mode="before")
    @classmethod
    def _sanitize_recipe_id_before_errors(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        raw_recipe_id = data.get("recipeId", data.get("recipe_id"))
        if isinstance(raw_recipe_id, str) and _unsafe_recipe_ref(raw_recipe_id):
            if "recipeId" in data:
                data["recipeId"] = ""
            if "recipe_id" in data:
                data["recipe_id"] = ""
        return data

    @field_validator("recipe_id")
    @classmethod
    def _validate_recipe_id(cls, value: str) -> str:
        clean = value.strip()
        if not clean or not _SAFE_RECIPE_ID_RE.fullmatch(clean):
            raise ValueError("recipeId must be a safe public recipe reference")
        if _unsafe_recipe_ref(clean):
            raise ValueError("recipeId contains protected runtime data marker")
        return clean


class EvalGateVerdict(_FrozenNoUpdateModel):
    ok: StrictBool
    reason_codes: tuple[EvalGateReasonCode, ...] = Field(default=(), alias="reasonCodes")


def evaluate_recipe_promotion_gate(
    metrics: RecipeEvalMetrics,
    thresholds: EvalGateThresholds,
) -> EvalGateVerdict:
    reasons: list[EvalGateReasonCode] = []
    if metrics.selector_accuracy < thresholds.min_selector_accuracy:
        reasons.append("selector_accuracy_below_threshold")
    if metrics.unsupported_claim_rate > thresholds.max_unsupported_claim_rate:
        reasons.append("unsupported_claim_rate_exceeds_threshold")
    if metrics.approval_bypass_count > 0:
        reasons.append("approval_bypass_count_exceeds_threshold")
    if metrics.raw_projection_fixture_passed:
        reasons.append("raw_governed_projection_fixture_passed_unexpectedly")
    if metrics.plugin_sandbox_overreach_fixture_passed:
        reasons.append("plugin_sandbox_overreach_fixture_passed_unexpectedly")
    if any(
        fixture.required and fixture.expected_governed and not fixture.actual_governed
        for fixture in metrics.selector_fixture_evaluations
    ):
        reasons.append("required_governed_selector_fixture_resolved_ungoverned")
    if any(
        invariant.required and invariant.configured_mode in {"log_only", "disabled"}
        for invariant in metrics.hard_invariant_policy_evaluations
    ):
        reasons.append("required_hard_invariant_not_enforced")
    return EvalGateVerdict(ok=not reasons, reasonCodes=tuple(reasons))


def _safe_public_ref(value: str) -> str:
    clean = value.strip()
    if not clean or not _SAFE_RECIPE_ID_RE.fullmatch(clean):
        raise ValueError("reference must be a safe public selector reference")
    lowered = clean.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    if (
        any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS)
        or any(marker in lowered for marker in _RAW_MARKERS)
        or any(marker in compact for marker in _PROTECTED_COMPACT_MARKERS)
        or "/" in clean
        or "\\" in clean
        or clean.startswith(("~", "."))
    ):
        raise ValueError("reference contains protected runtime data marker")
    return clean


def _unsafe_recipe_ref(value: str) -> bool:
    clean = value.strip()
    lowered = clean.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    return (
        ":" in clean
        or any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS)
        or any(marker in lowered for marker in _RAW_MARKERS)
        or any(marker in compact for marker in _PROTECTED_COMPACT_MARKERS)
        or "/" in clean
        or "\\" in clean
        or clean.startswith(("~", "."))
    )
