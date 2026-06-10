from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.regression_gates import (
    EvalGateThresholds,
    EvalGateVerdict,
    HardInvariantPolicyEvaluation,
    RecipeEvalMetrics,
    SelectorFixtureEvaluation,
    evaluate_recipe_promotion_gate,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def test_recipe_promotion_blocks_unsupported_claim_regression() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.research.cited-market-brief",
        selectorAccuracy=0.97,
        unsupportedClaimRate=0.03,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
    )

    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)
    assert verdict.ok is False
    assert "unsupported_claim_rate_exceeds_threshold" in verdict.reason_codes


def test_recipe_promotion_blocks_raw_projection_or_plugin_overreach() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.backoffice.numeric-audit",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=True,
        pluginSandboxOverreachFixturePassed=True,
    )

    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)
    assert verdict.reason_codes == (
        "raw_governed_projection_fixture_passed_unexpectedly",
        "plugin_sandbox_overreach_fixture_passed_unexpectedly",
    )


def test_recipe_promotion_blocks_raw_projection_and_plugin_overreach_even_if_thresholds_are_misconfigured() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=False,
        pluginSandboxOverreachMustFail=False,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.operations.invoice-review",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=True,
        pluginSandboxOverreachFixturePassed=True,
    )

    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)
    assert verdict.reason_codes == (
        "raw_governed_projection_fixture_passed_unexpectedly",
        "plugin_sandbox_overreach_fixture_passed_unexpectedly",
    )


def test_recipe_promotion_blocks_any_approval_bypass_even_if_threshold_is_misconfigured() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=5,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.operations.invoice-review",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=1,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
    )

    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)
    assert verdict.ok is False
    assert verdict.reason_codes == ("approval_bypass_count_exceeds_threshold",)


def test_recipe_promotion_blocks_required_governed_selector_fixture_falling_to_ungoverned_route() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.operations.invoice-review",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
        selectorFixtureEvaluations=(
            SelectorFixtureEvaluation(
                fixtureId="selector-fixture-invoice-review",
                selectedRef="route:lightweight-summary",
                selectedKind="route",
                required=True,
                expectedGoverned=True,
                actualGoverned=False,
            ),
        ),
    )

    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)
    assert verdict.ok is False
    assert verdict.reason_codes == ("required_governed_selector_fixture_resolved_ungoverned",)


def test_selector_governedness_hard_block_is_metadata_driven_not_category_name_driven() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    passing_general_named_metrics = RecipeEvalMetrics(
        recipeId="openmagi.general.custom-governed-workflow",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
        selectorFixtureEvaluations=(
            SelectorFixtureEvaluation(
                fixtureId="selector-fixture-general-governed",
                selectedRef="workflow:custom-governed-general",
                selectedKind="workflow",
                required=True,
                expectedGoverned=True,
                actualGoverned=True,
            ),
        ),
    )
    failing_non_general_metrics = RecipeEvalMetrics(
        recipeId="openmagi.billing.statement-check",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
        selectorFixtureEvaluations=(
            SelectorFixtureEvaluation(
                fixtureId="selector-fixture-billing-ungoverned",
                selectedRef="recipe:statement-fast-path",
                selectedKind="recipe",
                required=True,
                expectedGoverned=True,
                actualGoverned=False,
            ),
        ),
    )

    assert evaluate_recipe_promotion_gate(passing_general_named_metrics, thresholds).ok is True
    failing_verdict = evaluate_recipe_promotion_gate(failing_non_general_metrics, thresholds)
    assert failing_verdict.ok is False
    assert failing_verdict.reason_codes == ("required_governed_selector_fixture_resolved_ungoverned",)


def test_recipe_promotion_blocks_required_hard_invariant_configured_log_only_or_disabled() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.operations.invoice-review",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
        hardInvariantPolicyEvaluations=(
            HardInvariantPolicyEvaluation(
                invariantId="invariant-selector-governed-route",
                required=True,
                configuredMode="log_only",
            ),
            HardInvariantPolicyEvaluation(
                invariantId="invariant-plugin-sandbox-deny",
                required=True,
                configuredMode="disabled",
            ),
        ),
    )

    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)
    assert verdict.ok is False
    assert verdict.reason_codes == ("required_hard_invariant_not_enforced",)


def test_recipe_promotion_passes_when_all_thresholds_satisfied() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.research.cited-market-brief",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
    )

    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)
    assert verdict.ok is True
    assert verdict.reason_codes == ()


def test_eval_gate_contract_rejects_invalid_ranges_and_coerced_booleans() -> None:
    with pytest.raises(ValidationError, match="minSelectorAccuracy"):
        EvalGateThresholds(
            minSelectorAccuracy=1.1,
            maxUnsupportedClaimRate=0.01,
            maxApprovalBypassCount=0,
            rawProjectionMustFail=True,
            pluginSandboxOverreachMustFail=True,
        )
    with pytest.raises(ValidationError, match="rawProjectionMustFail"):
        EvalGateThresholds(
            minSelectorAccuracy=0.95,
            maxUnsupportedClaimRate=0.01,
            maxApprovalBypassCount=0,
            rawProjectionMustFail="true",
            pluginSandboxOverreachMustFail=True,
        )
    with pytest.raises(ValidationError, match="selectorAccuracy"):
        RecipeEvalMetrics(
            recipeId="openmagi.research.cited-market-brief",
            selectorAccuracy=-0.01,
            unsupportedClaimRate=0.0,
            approvalBypassCount=0,
            rawProjectionFixturePassed=False,
            pluginSandboxOverreachFixturePassed=False,
        )
    with pytest.raises(ValidationError, match="expectedGoverned"):
        SelectorFixtureEvaluation(
            fixtureId="selector-fixture-coerced",
            selectedRef="route:coerced",
            selectedKind="route",
            required=True,
            expectedGoverned="true",
            actualGoverned=False,
        )
    with pytest.raises(ValidationError, match="configuredMode"):
        HardInvariantPolicyEvaluation(
            invariantId="invariant-disabled-string",
            required=True,
            configuredMode="observe_only",
        )


def test_eval_gate_rejects_protected_recipe_ids_and_reason_codes() -> None:
    with pytest.raises(ValidationError, match="recipeId"):
        RecipeEvalMetrics(
            recipeId="openmagi.to-ken.recipe",
            selectorAccuracy=0.99,
            unsupportedClaimRate=0.0,
            approvalBypassCount=0,
            rawProjectionFixturePassed=False,
            pluginSandboxOverreachFixturePassed=False,
        )
    with pytest.raises(ValidationError, match="reasonCodes"):
        EvalGateVerdict(ok=False, reasonCodes=("raw-projection-log",))


def test_eval_gate_rejects_pathlike_recipe_ids_without_error_payload_leakage() -> None:
    unsafe_ids = (
        "openmagi.to-ken.recipe",
        "C:Users.kevin.data",
        "C:workspace.data",
        "http:example.test.path",
    )
    for recipe_id in unsafe_ids:
        with pytest.raises(ValidationError, match="recipeId") as exc_info:
            RecipeEvalMetrics(
                recipeId=recipe_id,
                selectorAccuracy=0.99,
                unsupportedClaimRate=0.0,
                approvalBypassCount=0,
                rawProjectionFixturePassed=False,
                pluginSandboxOverreachFixturePassed=False,
            )
        serialized_errors = json.dumps(exc_info.value.errors(include_input=True), default=str, sort_keys=True).lower()
        serialized_json = exc_info.value.json().lower()
        assert recipe_id.lower() not in serialized_errors
        assert recipe_id.lower() not in serialized_json


def test_eval_gate_model_copy_update_is_disabled() -> None:
    thresholds = EvalGateThresholds(
        minSelectorAccuracy=0.95,
        maxUnsupportedClaimRate=0.01,
        maxApprovalBypassCount=0,
        rawProjectionMustFail=True,
        pluginSandboxOverreachMustFail=True,
    )
    metrics = RecipeEvalMetrics(
        recipeId="openmagi.research.cited-market-brief",
        selectorAccuracy=0.99,
        unsupportedClaimRate=0.0,
        approvalBypassCount=0,
        rawProjectionFixturePassed=False,
        pluginSandboxOverreachFixturePassed=False,
    )

    with pytest.raises(ValueError, match="model_copy update"):
        thresholds.model_copy(update={"rawProjectionMustFail": False})
    with pytest.raises(ValueError, match="model_copy update"):
        metrics.model_copy(update={"approvalBypassCount": 1})


def test_eval_gate_fixture_validates_without_raw_payloads() -> None:
    payload = json.loads((FIXTURE_DIR / "eval_gate_thresholds.json").read_text())
    thresholds = EvalGateThresholds.model_validate(payload["thresholds"])
    metrics = RecipeEvalMetrics.model_validate(payload["metrics"])
    verdict = evaluate_recipe_promotion_gate(metrics, thresholds)

    assert verdict.model_dump(by_alias=True, mode="json") == payload["expectedVerdict"]
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
