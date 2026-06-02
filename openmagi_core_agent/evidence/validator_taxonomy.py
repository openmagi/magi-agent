from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


ValidatorTrustClass = Literal["deterministic", "llm_assisted"]
ValidatorSupportStatus = Literal["supported", "weak", "unverifiable", "contradicted", "failed"]
ValidatorAction = Literal["pass", "repair", "ask_user", "abstain", "block"]
LlmAssistedPolicyAction = Literal["repair", "ask_user", "abstain", "block"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,180}$")
_JWT_LIKE_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?:$|[^A-Za-z0-9_-])"
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:|set-cookie\s*:|\bcookie\b|\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"token\s*[:=]|password\s*[:=]|secret\s*[:=]|api[_-]?key\s*[:=]|"
    r"/Users(?:/|\b)|/home(?:/|\b)|/workspace(?:/|\b)|/data/bots(?:/|\b)|"
    r"/var/lib/kubelet(?:/|\b)|pvc-[A-Za-z0-9-]+|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)


class ValidatorResult(BaseModel):
    model_config = _MODEL_CONFIG

    validator_id: str = Field(alias="validatorId")
    trust_class: ValidatorTrustClass = Field(alias="trustClass")
    status: ValidatorSupportStatus
    claim_ref: str = Field(alias="claimRef")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")

    @field_validator("validator_id", "claim_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("evidence_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(ref) for ref in value)


class ValidatorPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_id: str = Field(alias="policyId")
    weak_llm_assisted_action: LlmAssistedPolicyAction = Field(
        default="repair",
        alias="weakLlmAssistedAction",
    )
    unverifiable_llm_assisted_action: LlmAssistedPolicyAction = Field(
        default="abstain",
        alias="unverifiableLlmAssistedAction",
    )

    @field_validator("policy_id")
    @classmethod
    def _validate_policy_id(cls, value: str) -> str:
        return _safe_ref(value)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set
        policy_id = values.get("policy_id", values.get("policyId", "validator_policy"))
        try:
            safe_policy_id = _safe_ref(str(policy_id))
        except ValueError:
            safe_policy_id = "validator_policy"
        return cls(
            policyId=safe_policy_id,
            weakLlmAssistedAction=_safe_llm_action(
                values.get("weak_llm_assisted_action", values.get("weakLlmAssistedAction")),
                default="repair",
            ),
            unverifiableLlmAssistedAction=_safe_llm_action(
                values.get(
                    "unverifiable_llm_assisted_action",
                    values.get("unverifiableLlmAssistedAction"),
                ),
                default="abstain",
            ),
        )


class ValidatorPolicyDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ValidatorAction
    policy_id: str = Field(alias="policyId")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")


def apply_validator_policy(
    policy: ValidatorPolicy,
    results: Sequence[ValidatorResult],
) -> ValidatorPolicyDecision:
    safe_policy = ValidatorPolicy.model_construct(
        policy_id=getattr(policy, "policy_id", "validator_policy"),
        weak_llm_assisted_action=getattr(policy, "weak_llm_assisted_action", "repair"),
        unverifiable_llm_assisted_action=getattr(
            policy,
            "unverifiable_llm_assisted_action",
            "abstain",
        ),
    )
    parsed_results = tuple(
        result if isinstance(result, ValidatorResult) else ValidatorResult.model_validate(result)
        for result in results
    )
    validator_refs = tuple(result.validator_id for result in parsed_results)
    if not parsed_results:
            return ValidatorPolicyDecision(
                status="block",
                policyId=safe_policy.policy_id,
                reasonCodes=("validator_result_missing",),
                validatorRefs=(),
            )
    reason_codes: list[str] = []
    selected_action: ValidatorAction = "pass"
    for result in parsed_results:
        if result.status in {"failed", "contradicted"}:
            reason = "claim_contradicted" if result.status == "contradicted" else "validator_failed"
            return ValidatorPolicyDecision(
                status="block",
                policyId=safe_policy.policy_id,
                reasonCodes=(reason,),
                validatorRefs=validator_refs,
            )
        if result.trust_class == "deterministic":
            if result.status != "supported":
                return ValidatorPolicyDecision(
                    status="block",
                    policyId=safe_policy.policy_id,
                    reasonCodes=("deterministic_validator_not_supported",),
                    validatorRefs=validator_refs,
                )
            continue
        if result.status == "weak":
            selected_action = _most_restrictive(
                selected_action,
                safe_policy.weak_llm_assisted_action,
            )
            reason_codes.append("llm_assisted_weak_support")
        elif result.status == "unverifiable":
            selected_action = _most_restrictive(
                selected_action,
                safe_policy.unverifiable_llm_assisted_action,
            )
            reason_codes.append("llm_assisted_unverifiable")
        elif result.status == "supported":
            reason_codes.append("llm_assisted_supported_not_hard_authoritative")
    return ValidatorPolicyDecision(
        status=selected_action,
        policyId=safe_policy.policy_id,
        reasonCodes=tuple(dict.fromkeys(reason_codes)) or ("validator_policy_passed",),
        validatorRefs=validator_refs,
    )


def _safe_ref(value: str) -> str:
    text = value.strip()
    if _PRIVATE_TEXT_RE.search(text) or _JWT_LIKE_RE.search(text) or _SAFE_REF_RE.fullmatch(text) is None:
        raise ValueError("validator refs must be sanitized public refs")
    return text


def _safe_llm_action(value: object, *, default: LlmAssistedPolicyAction) -> LlmAssistedPolicyAction:
    if value in {"repair", "ask_user", "abstain", "block"}:
        return value  # type: ignore[return-value]
    return default


def _most_restrictive(left: ValidatorAction, right: ValidatorAction) -> ValidatorAction:
    order = {
        "pass": 0,
        "repair": 1,
        "ask_user": 2,
        "abstain": 3,
        "block": 4,
    }
    return left if order[left] >= order[right] else right


__all__ = [
    "LlmAssistedPolicyAction",
    "ValidatorAction",
    "ValidatorPolicy",
    "ValidatorPolicyDecision",
    "ValidatorResult",
    "ValidatorSupportStatus",
    "ValidatorTrustClass",
    "apply_validator_policy",
]
