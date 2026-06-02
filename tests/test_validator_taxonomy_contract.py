from __future__ import annotations

from pydantic import ValidationError
import pytest

from openmagi_core_agent.evidence.validator_taxonomy import (
    ValidatorPolicy,
    ValidatorResult,
    apply_validator_policy,
)


def test_deterministic_validator_can_hard_pass_or_fail() -> None:
    result = ValidatorResult.model_validate(
        {
            "validatorId": "quoteExactMatch",
            "trustClass": "deterministic",
            "status": "supported",
            "claimRef": "claim_1",
            "evidenceRefs": ["source_1_span_1"],
        }
    )

    decision = apply_validator_policy(
        ValidatorPolicy(policyId="research.strict"),
        [result],
    )

    assert decision.status == "pass"


def test_llm_assisted_weak_support_requires_policy_action() -> None:
    result = ValidatorResult.model_validate(
        {
            "validatorId": "paraphraseSupportCheck",
            "trustClass": "llm_assisted",
            "status": "weak",
            "claimRef": "claim_1",
            "evidenceRefs": ["source_1_span_1"],
        }
    )

    decision = apply_validator_policy(
        ValidatorPolicy.model_validate(
            {
                "policyId": "research.strict",
                "weakLlmAssistedAction": "abstain",
            }
        ),
        [result],
    )

    assert decision.status == "abstain"
    assert "llm_assisted_weak_support" in decision.reason_codes


def test_contradicted_llm_assisted_result_blocks() -> None:
    result = ValidatorResult.model_validate(
        {
            "validatorId": "contradictionCheck",
            "trustClass": "llm_assisted",
            "status": "contradicted",
            "claimRef": "claim_1",
            "evidenceRefs": ["source_1_span_1", "source_2_span_3"],
        }
    )

    decision = apply_validator_policy(
        ValidatorPolicy(policyId="research.strict"),
        [result],
    )

    assert decision.status == "block"
    assert "claim_contradicted" in decision.reason_codes


def test_llm_assisted_weak_support_does_not_hard_pass_by_default() -> None:
    result = ValidatorResult.model_validate(
        {
            "validatorId": "paraphraseSupportCheck",
            "trustClass": "llm_assisted",
            "status": "weak",
            "claimRef": "claim_1",
            "evidenceRefs": ["source_1_span_1"],
        }
    )

    decision = apply_validator_policy(ValidatorPolicy(policyId="research.strict"), [result])

    assert decision.status == "repair"
    assert decision.status != "pass"


def test_llm_assisted_policy_action_cannot_be_configured_to_pass() -> None:
    with pytest.raises(ValidationError):
        ValidatorPolicy.model_validate(
            {
                "policyId": "research.strict",
                "weakLlmAssistedAction": "pass",
            }
        )
    with pytest.raises(ValidationError):
        ValidatorPolicy.model_validate(
            {
                "policyId": "research.strict",
                "unverifiableLlmAssistedAction": "pass",
            }
        )


def test_constructed_llm_assisted_policy_action_cannot_force_pass() -> None:
    result = ValidatorResult.model_validate(
        {
            "validatorId": "paraphraseSupportCheck",
            "trustClass": "llm_assisted",
            "status": "weak",
            "claimRef": "claim_1",
            "evidenceRefs": ["source_1_span_1"],
        }
    )
    policy = ValidatorPolicy.model_construct(
        policy_id="research.strict",
        weak_llm_assisted_action="pass",
        unverifiable_llm_assisted_action="pass",
    )

    decision = apply_validator_policy(policy, [result])

    assert decision.status == "repair"
    assert decision.status != "pass"


def test_validator_result_rejects_private_refs() -> None:
    with pytest.raises(ValidationError):
        ValidatorResult.model_validate(
            {
                "validatorId": "quoteExactMatch",
                "trustClass": "deterministic",
                "status": "supported",
                "claimRef": "/Users/kevin/private/claim",
                "evidenceRefs": ["source_1_span_1"],
            }
        )


def test_validator_result_rejects_jwt_like_refs() -> None:
    jwt_like = "aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc"
    with pytest.raises(ValidationError):
        ValidatorResult.model_validate(
            {
                "validatorId": "quoteExactMatch",
                "trustClass": "deterministic",
                "status": "supported",
                "claimRef": jwt_like,
                "evidenceRefs": ["source_1_span_1"],
            }
        )
