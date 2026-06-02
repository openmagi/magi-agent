from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.harness.guardrail_matrix import (
    GuardrailDefinition,
    GuardrailFailureMode,
    GuardrailResult,
    GuardrailStage,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def test_guardrail_declares_stage_failure_mode_and_structured_result() -> None:
    guardrail = GuardrailDefinition(
        guardrailId="guardrail-before-tool-denylist",
        stage="before_tool_call",
        failureMode="block",
        hardInvariant=True,
        validatorTrustClass="deterministic",
    )
    result = GuardrailResult(
        guardrailId=guardrail.guardrail_id,
        stage=guardrail.stage,
        status="failed",
        reasonCodes=("tool_denied",),
        evidenceRefs=("evidence:tool-policy",),
        policyDecisionId="decision-tool-denied",
        validatorTrustClass="deterministic",
        recommendedTransition="block",
        redactionStatus="redacted",
    )

    assert result.recommended_transition == "block"
    assert result.validator_trust_class == "deterministic"


def test_hard_invariant_cannot_use_log_only() -> None:
    with pytest.raises(ValidationError, match="log_only"):
        GuardrailDefinition(
            guardrailId="guardrail-hard-bad",
            stage="before_output_projection",
            failureMode="log_only",
            hardInvariant=True,
            validatorTrustClass="deterministic",
        )


def test_stage_and_failure_mode_values_are_closed() -> None:
    assert "before_model_call" in set(GuardrailStage.__args__)
    assert "after_delivery" in set(GuardrailStage.__args__)
    assert set(GuardrailFailureMode.__args__) == {
        "block",
        "repair",
        "ask_user",
        "require_approval",
        "abstain",
        "fallback",
        "escalate_model",
        "log_only",
    }


def test_guardrail_contract_rejects_coerced_boolean_and_protected_refs() -> None:
    with pytest.raises(ValidationError, match="hardInvariant"):
        GuardrailDefinition(
            guardrailId="guardrail-coerced-bool",
            stage="before_model_call",
            failureMode="block",
            hardInvariant="true",
            validatorTrustClass="deterministic",
        )
    with pytest.raises(ValidationError, match="guardrailId"):
        GuardrailDefinition(
            guardrailId="guardrail-to-ken",
            stage="before_model_call",
            failureMode="block",
            hardInvariant=True,
            validatorTrustClass="deterministic",
        )
    with pytest.raises(ValidationError, match="evidenceRefs"):
        GuardrailResult(
            guardrailId="guardrail-before-model",
            stage="before_model_call",
            status="failed",
            reasonCodes=("model_blocked",),
            evidenceRefs=("evidence:/Users/example/.env",),
            policyDecisionId="decision-model-blocked",
            validatorTrustClass="deterministic",
            recommendedTransition="block",
            redactionStatus="redacted",
        )


def test_guardrail_contract_rejects_auth_markers_and_system_path_refs() -> None:
    for ref in ("auth_header", "oauth:callback", "evidence:etc-passwd"):
        with pytest.raises(ValidationError, match="guardrailId"):
            GuardrailDefinition(
                guardrailId=ref,
                stage="before_model_call",
                failureMode="block",
                hardInvariant=True,
                validatorTrustClass="deterministic",
            )
        with pytest.raises(ValidationError, match="evidenceRefs"):
            GuardrailResult(
                guardrailId="guardrail-before-model",
                stage="before_model_call",
                status="failed",
                reasonCodes=("model_blocked",),
                evidenceRefs=(ref,),
                policyDecisionId="decision-model-blocked",
                validatorTrustClass="deterministic",
                recommendedTransition="block",
                redactionStatus="redacted",
            )


def test_guardrail_result_rejects_mismatched_transition_for_status() -> None:
    with pytest.raises(ValidationError, match="recommendedTransition"):
        GuardrailResult(
            guardrailId="guardrail-before-output",
            stage="before_output_projection",
            status="pass",
            reasonCodes=("output_clean",),
            evidenceRefs=("evidence:output-policy",),
            policyDecisionId="decision-output-clean",
            validatorTrustClass="deterministic",
            recommendedTransition="block",
            redactionStatus="redacted",
        )


def test_guardrail_model_copy_update_is_disabled() -> None:
    guardrail = GuardrailDefinition(
        guardrailId="guardrail-before-delivery",
        stage="before_delivery",
        failureMode="require_approval",
        hardInvariant=False,
        validatorTrustClass="deterministic",
    )
    result = GuardrailResult(
        guardrailId="guardrail-before-delivery",
        stage="before_delivery",
        status="failed",
        reasonCodes=("delivery_requires_approval",),
        evidenceRefs=("evidence:delivery-policy",),
        policyDecisionId="decision-delivery-approval",
        validatorTrustClass="deterministic",
        recommendedTransition="require_approval",
        redactionStatus="redacted",
    )

    with pytest.raises(ValueError, match="model_copy update"):
        guardrail.model_copy(update={"failureMode": "log_only"})
    with pytest.raises(ValueError, match="model_copy update"):
        result.model_copy(update={"recommendedTransition": "log_only"})


def test_guardrail_fixture_validates_without_raw_payloads() -> None:
    payload = json.loads((FIXTURE_DIR / "guardrail_matrix.json").read_text())
    guardrail = GuardrailDefinition.model_validate(payload["definition"])
    result = GuardrailResult.model_validate(payload["result"])

    assert result.guardrail_id == guardrail.guardrail_id
    assert result.stage == guardrail.stage
    assert result.recommended_transition == guardrail.failure_mode
    encoded_values = " ".join(_string_values(payload)).lower()
    forbidden_fragments = (
        "pro" + "mpt",
        "author" + "ization",
        "coo" + "kie",
        "to" + "ken",
        "sess" + "ion",
        "priv" + "ate",
        "/users/",
        ".env",
    )
    assert all(fragment not in encoded_values for fragment in forbidden_fragments)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []
