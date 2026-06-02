from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class RuntimeInvariantSet(BaseModel):
    model_config = _MODEL_CONFIG

    governed_raw_draft_streaming_forbidden: bool = Field(
        default=True,
        alias="governedRawDraftStreamingForbidden",
    )
    tool_host_only_execution: bool = Field(default=True, alias="toolHostOnlyExecution")
    minimum_receipt_schema_required: bool = Field(
        default=True,
        alias="minimumReceiptSchemaRequired",
    )
    source_snapshot_digest_span_required: bool = Field(
        default=True,
        alias="sourceSnapshotDigestSpanRequired",
    )
    authority_anti_forgery_required: bool = Field(
        default=True,
        alias="authorityAntiForgeryRequired",
    )
    secret_redaction_required: bool = Field(default=True, alias="secretRedactionRequired")
    validator_before_projection_required: bool = Field(
        default=True,
        alias="validatorBeforeProjectionRequired",
    )

    @classmethod
    def strict(cls) -> RuntimeInvariantSet:
        return cls()

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self).strict()


class CustomerConfigurablePolicy(BaseModel):
    model_config = _MODEL_CONFIG

    require_citations: bool = Field(default=False, alias="requireCitations")
    require_calculation_refs: bool = Field(default=False, alias="requireCalculationRefs")
    max_repair_attempts: int = Field(default=0, alias="maxRepairAttempts", ge=0, le=5)
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")


class DeterministicPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_id: str = Field(alias="policyId")
    customer_configurable: CustomerConfigurablePolicy = Field(
        default_factory=CustomerConfigurablePolicy,
        alias="customerConfigurable",
    )
    runtime_invariants: RuntimeInvariantSet = Field(
        default_factory=RuntimeInvariantSet,
        alias="runtimeInvariants",
    )


class RuntimeInvariantDecision(BaseModel):
    model_config = _MODEL_CONFIG

    allowed: bool
    policy_id: str = Field(alias="policyId")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    effective_invariants: RuntimeInvariantSet = Field(alias="effectiveInvariants")
    forged_invariant_refs: tuple[str, ...] = Field(default=(), alias="forgedInvariantRefs")


def evaluate_runtime_invariants(
    policy: DeterministicPolicy | Mapping[str, Any],
) -> RuntimeInvariantDecision:
    parsed = policy if isinstance(policy, DeterministicPolicy) else DeterministicPolicy.model_validate(policy)
    strict = RuntimeInvariantSet.strict()
    incoming = parsed.runtime_invariants
    forged = tuple(
        name
        for name in RuntimeInvariantSet.model_fields
        if getattr(incoming, name) != getattr(strict, name)
    )
    return RuntimeInvariantDecision(
        allowed=not forged,
        policyId=parsed.policy_id,
        reasonCodes=("runtime_invariant_forgery",) if forged else ("runtime_invariants_strict",),
        effectiveInvariants=strict,
        forgedInvariantRefs=forged,
    )


__all__ = [
    "CustomerConfigurablePolicy",
    "DeterministicPolicy",
    "RuntimeInvariantDecision",
    "RuntimeInvariantSet",
    "evaluate_runtime_invariants",
]
