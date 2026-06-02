from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


GuardrailStage = Literal[
    "before_input_acceptance",
    "before_recipe_selection",
    "after_recipe_selection",
    "before_context_projection",
    "before_model_call",
    "after_model_call",
    "before_tool_call",
    "after_tool_call",
    "before_repair",
    "before_approval_request",
    "after_approval",
    "before_output_projection",
    "after_output_projection",
    "before_delivery",
    "after_delivery",
]
GuardrailFailureMode = Literal[
    "block",
    "repair",
    "ask_user",
    "require_approval",
    "abstain",
    "fallback",
    "escalate_model",
    "log_only",
]
GuardrailStatus = Literal["pass", "failed", "missing", "skipped"]
ValidatorTrustClass = Literal["deterministic", "llm_assisted", "human_review"]
RedactionStatus = Literal["redacted", "not_required"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
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
_PATHLIKE_COMPACT_MARKERS = (
    "users",
    "home",
    "ssh",
    "idrsa",
    "env",
    "kube",
    "kubeconfig",
    "varlib",
    "databots",
    "etcpasswd",
    "passwd",
)


class _FrozenNoUpdateModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for guardrail matrix contracts")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class GuardrailDefinition(_FrozenNoUpdateModel):
    guardrail_id: str = Field(alias="guardrailId")
    stage: GuardrailStage
    failure_mode: GuardrailFailureMode = Field(alias="failureMode")
    hard_invariant: StrictBool = Field(default=False, alias="hardInvariant")
    validator_trust_class: ValidatorTrustClass = Field(alias="validatorTrustClass")

    @field_validator("guardrail_id")
    @classmethod
    def _validate_guardrail_id(cls, value: str) -> str:
        return _safe_ref(value, field_name="guardrailId")

    @model_validator(mode="after")
    def _hard_invariant_cannot_log_only(self) -> Self:
        if self.hard_invariant and self.failure_mode == "log_only":
            raise ValueError("hard invariant guardrails cannot use log_only")
        return self


class GuardrailResult(_FrozenNoUpdateModel):
    guardrail_id: str = Field(alias="guardrailId")
    stage: GuardrailStage
    status: GuardrailStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    policy_decision_id: str = Field(alias="policyDecisionId")
    validator_trust_class: ValidatorTrustClass = Field(alias="validatorTrustClass")
    recommended_transition: GuardrailFailureMode = Field(alias="recommendedTransition")
    redaction_status: RedactionStatus = Field(alias="redactionStatus")

    @field_validator("guardrail_id", "policy_decision_id")
    @classmethod
    def _validate_safe_ref(cls, value: str) -> str:
        return _safe_ref(value, field_name="guardrail result ref")

    @field_validator("reason_codes", "evidence_refs", mode="before")
    @classmethod
    def _normalize_ref_tuple(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("guardrail refs must be arrays of safe strings")
        return tuple(value)  # type: ignore[arg-type]

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(reason_code, field_name="reasonCodes") for reason_code in value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(evidence_ref, field_name="evidenceRefs") for evidence_ref in value)

    @model_validator(mode="after")
    def _validate_transition_for_status(self) -> Self:
        if self.status == "pass" and self.recommended_transition != "log_only":
            raise ValueError("recommendedTransition must be log_only when guardrail passes")
        if self.status in {"failed", "missing"} and self.recommended_transition == "log_only":
            raise ValueError("recommendedTransition must be actionable for failed or missing guardrails")
        return self


def _safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not clean or not _SAFE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a safe public reference")
    lowered = clean.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    if (
        any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS)
        or any(marker in lowered for marker in _RAW_MARKERS)
        or any(marker in compact for marker in _PROTECTED_COMPACT_MARKERS)
        or _looks_path_like(clean, compact)
        or "/" in clean
        or "\\" in clean
        or clean.startswith(("~", "."))
    ):
        raise ValueError(f"{field_name} contains protected runtime data marker")
    return clean


def _looks_path_like(value: str, compact: str) -> bool:
    if not any(sep in value for sep in (":", ".", "-")):
        return False
    if "users" in compact or "home" in compact:
        return True
    return any(
        marker in compact
        for marker in ("ssh", "idrsa", "kube", "kubeconfig", "varlib", "databots", "etcpasswd")
    ) or ("passwd" in compact and "etc" in compact) or (
        "env" in compact and any(marker in compact for marker in _PATHLIKE_COMPACT_MARKERS)
    )
