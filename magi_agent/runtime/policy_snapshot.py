from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PolicyDecisionVerdict = Literal["allow", "deny", "approval_required", "repair", "block"]
_DIGEST_PREFIX = "sha256:"
_PRIVATE_POLICY_FRAGMENTS = ("authorization", "cookie", "token", "secret", "api_key", "password", "prompt")


class PolicySourceRef(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    source_name: str = Field(alias="sourceName")
    source_version: str = Field(alias="sourceVersion")
    source_digest: str = Field(alias="sourceDigest")
    authoritative: bool

    @field_validator("source_name", "source_version")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy source identifiers must be non-empty")
        if any(fragment in value.lower() for fragment in _PRIVATE_POLICY_FRAGMENTS):
            raise ValueError("policy source refs must not expose private policy internals")
        return value

    @field_validator("source_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "sourceDigest")


class EffectivePolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    policy_id: str = Field(alias="policyId")
    policy_version: str = Field(alias="policyVersion")
    sources: tuple[PolicySourceRef, ...]
    recipe_refs: tuple[str, ...] = Field(alias="recipeRefs")
    validator_refs: tuple[str, ...] = Field(alias="validatorRefs")
    tool_allowlist: tuple[str, ...] = Field(alias="toolAllowlist")
    projection_policy_ref: str = Field(alias="projectionPolicyRef")
    repair_policy_ref: str = Field(alias="repairPolicyRef")
    approval_policy_ref: str = Field(alias="approvalPolicyRef")
    model_tier_policy_ref: str = Field(alias="modelTierPolicyRef")
    gate_refs: tuple[str, ...] = Field(alias="gateRefs")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")

    @field_validator("policy_id", "policy_version")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy identifiers must be non-empty")
        return value

    @field_validator("sources")
    @classmethod
    def _validate_sources(cls, value: tuple[PolicySourceRef, ...]) -> tuple[PolicySourceRef, ...]:
        if not value:
            raise ValueError("sources must contain at least one source ref")
        return value

    @field_validator(
        "recipe_refs",
        "validator_refs",
        "tool_allowlist",
        "gate_refs",
        mode="before",
    )
    @classmethod
    def _normalize_tuple(cls, value: object) -> tuple[str, ...]:
        values = tuple(value or ())  # type: ignore[arg-type]
        if not values or any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("policy ref tuples must contain non-empty strings")
        return values

    @field_validator(
        "projection_policy_ref",
        "repair_policy_ref",
        "approval_policy_ref",
        "model_tier_policy_ref",
    )
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if not value.strip() or ":" not in value:
            raise ValueError("policy refs must be namespaced")
        if any(fragment in value.lower() for fragment in _PRIVATE_POLICY_FRAGMENTS):
            raise ValueError("policy refs must not expose private policy internals")
        return value

    @field_validator("effective_policy_snapshot_digest")
    @classmethod
    def _validate_snapshot_digest(cls, value: str) -> str:
        return _require_digest(value, "effectivePolicySnapshotDigest")

    @model_validator(mode="after")
    def _verify_digest(self) -> Self:
        expected = digest_policy_snapshot_payload(self)
        if self.effective_policy_snapshot_digest != expected:
            raise ValueError("effectivePolicySnapshotDigest does not match snapshot content")
        return self


class PolicyDecisionBinding(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    decision_id: str = Field(alias="decisionId")
    effective_policy_snapshot_digest: str = Field(alias="effectivePolicySnapshotDigest")
    selected_action_digest: str = Field(alias="selectedActionDigest")
    verdict: PolicyDecisionVerdict
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("decision_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("decisionId must be non-empty")
        return value

    @field_validator("effective_policy_snapshot_digest", "selected_action_digest")
    @classmethod
    def _validate_digest(cls, value: str, info: object) -> str:
        return _require_digest(value, getattr(info, "field_name", "digest"))

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _normalize_reasons(cls, value: object) -> tuple[str, ...]:
        values = tuple(value or ())  # type: ignore[arg-type]
        if any(not isinstance(item, str) or not item.strip() for item in values):
            raise ValueError("reasonCodes must contain non-empty strings")
        return values


def build_effective_policy_snapshot(
    *,
    policyId: str,
    policyVersion: str,
    sources: Iterable[PolicySourceRef],
    recipeRefs: Iterable[str],
    validatorRefs: Iterable[str],
    toolAllowlist: Iterable[str],
    projectionPolicyRef: str,
    repairPolicyRef: str,
    approvalPolicyRef: str,
    modelTierPolicyRef: str,
    gateRefs: Iterable[str],
) -> EffectivePolicySnapshot:
    source_tuple = tuple(sources)
    payload = {
        "policyId": policyId,
        "policyVersion": policyVersion,
        "sources": [source.model_dump(by_alias=True, mode="json") for source in source_tuple],
        "recipeRefs": tuple(recipeRefs),
        "validatorRefs": tuple(validatorRefs),
        "toolAllowlist": tuple(toolAllowlist),
        "projectionPolicyRef": projectionPolicyRef,
        "repairPolicyRef": repairPolicyRef,
        "approvalPolicyRef": approvalPolicyRef,
        "modelTierPolicyRef": modelTierPolicyRef,
        "gateRefs": tuple(gateRefs),
    }
    return EffectivePolicySnapshot(
        **payload,
        effectivePolicySnapshotDigest=_digest_json(payload),
    )


def digest_policy_snapshot_payload(snapshot: EffectivePolicySnapshot) -> str:
    payload = snapshot.model_dump(by_alias=True, mode="json")
    payload.pop("effectivePolicySnapshotDigest", None)
    return _digest_json(payload)


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


def _digest_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()
