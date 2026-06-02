from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from magi_agent.recipes.composition import (
    AdmittedRecipeSnapshot,
    RecipeStackInput,
)
from magi_agent.recipes.effective_contract import (
    EffectiveRecipeContract,
    build_effective_recipe_contract,
)
from magi_agent.recipes.hook_composition import (
    EffectiveRecipeHookContract,
    HookContribution,
    compose_hook_contributions,
)
from magi_agent.recipes.projection import (
    RecipeCompositionProjection,
    project_effective_recipe_contract,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "recipe_composition"
REGRESSION_FIXTURE_PATHS = sorted(FIXTURE_ROOT.glob("regression_*.json"))

REQUIRED_CASE_IDS = {
    "approval_bypass_attempt",
    "duplicate_non_idempotent_hook",
    "evidence_weakening_attempt",
    "explicit_recipe_omitted",
    "governed_selector_auto_fallback_with_required_ref_present",
    "governed_selector_general_chat_fallback",
    "grant_deny_collision",
    "hard_invariant_log_only",
    "raw_private_config_projection",
    "unbounded_retry_composition",
    "unsafe_context_widening",
}


def _load_fixture(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["schemaVersion"] == "recipeCompositionRegressionFixture.v1"
    return fixture


FIXTURES = tuple(_load_fixture(path) for path in REGRESSION_FIXTURE_PATHS)


def _snapshot(data: dict[str, Any]) -> AdmittedRecipeSnapshot:
    payload: dict[str, object] = {
        "recipeRef": data["recipeRef"],
        "snapshotDigest": "sha256:" + "0" * 64,
        "version": data.get("version", "v1"),
        "source": data.get("source", "fixture"),
        "governed": data.get("governed", True),
        "hardSafety": data.get("hardSafety", False),
        "toolGrants": tuple(data.get("toolGrants", ())),
        "toolDenials": tuple(data.get("toolDenials", ())),
        "evidenceRequirements": tuple(data.get("evidenceRequirements", ())),
        "approvalRequirements": tuple(data.get("approvalRequirements", ())),
        "contextRequirements": tuple(data.get("contextRequirements", ())),
        "hookContributions": tuple(data.get("hookContributions", ())),
        "retryPolicy": data.get("retryPolicy", "none"),
        "projectionRules": tuple(data.get("projectionRules", ())),
    }
    payload["snapshotDigest"] = AdmittedRecipeSnapshot.compute_snapshot_digest(payload)
    return AdmittedRecipeSnapshot._from_registry_snapshot(payload)


def _hook_contribution(data: dict[str, Any]) -> HookContribution:
    payload: dict[str, object] = {
        "recipeRef": data["recipeRef"],
        "hookId": data["hookId"],
        "stage": data.get("stage", "beforeToolUse"),
        "priority": data.get("priority", 100),
        "scope": tuple(data.get("scope", ("all",))),
        "idempotencyKey": data.get("idempotencyKey"),
        "blocking": data.get("blocking", False),
        "failureMode": data.get("failureMode", "fail_open"),
        "sideEffectful": data.get("sideEffectful", False),
        "securityCritical": data.get("securityCritical", False),
        "privateConfig": data.get("privateConfig", {}),
    }
    payload["contributionDigest"] = HookContribution.compute_contribution_digest(payload)
    return HookContribution._from_registry_contribution(payload)


def _build_fixture_contract(
    fixture: dict[str, Any],
) -> tuple[
    EffectiveRecipeContract,
    EffectiveRecipeHookContract | None,
    tuple[HookContribution, ...],
    RecipeCompositionProjection,
]:
    hook_contributions = tuple(
        _hook_contribution(hook) for hook in fixture.get("hookContributions", ())
    )
    hook_contract = (
        compose_hook_contributions(hook_contributions)
        if hook_contributions
        else None
    )
    contract = build_effective_recipe_contract(
        stack=RecipeStackInput.model_validate(fixture["stack"]),
        admitted_snapshots=tuple(_snapshot(item) for item in fixture["snapshots"]),
        hook_contributions=hook_contributions if hook_contributions else None,
        auto_conflict_policy=fixture.get("autoConflictPolicy", "exclude"),
        global_retry_cap=fixture.get("globalRetryCap", 3),
        required_governed_recipe_refs=tuple(
            fixture.get("requiredGovernedRecipeRefs", ())
        ),
    )
    return (
        contract,
        hook_contract,
        hook_contributions,
        project_effective_recipe_contract(contract),
    )


def _assert_authority_flags(
    fixture: dict[str, Any],
    contract: EffectiveRecipeContract,
    hook_contract: EffectiveRecipeHookContract | None,
    projection: RecipeCompositionProjection,
) -> None:
    assert fixture["activationMode"] == {
        "defaultOff": True,
        "trafficAttached": False,
        "executionAttached": False,
        "liveActivation": False,
    }
    assert contract.default_off is True
    assert contract.traffic_attached is False
    assert contract.execution_attached is False
    assert contract.live_activation is False
    assert projection.default_off is True
    assert projection.traffic_attached is False
    assert projection.execution_attached is False
    assert projection.live_activation is False
    if hook_contract is not None:
        assert hook_contract.default_off is True
        assert hook_contract.traffic_attached is False
        assert hook_contract.execution_attached is False
        assert hook_contract.live_activation is False


def _assert_contract_expectations(
    fixture: dict[str, Any],
    contract: EffectiveRecipeContract,
    hook_contract: EffectiveRecipeHookContract | None,
) -> None:
    expected = fixture["expected"]
    assert contract.blocked is expected["blocked"]

    field_map = {
        "effectiveRecipeRefs": "effective_recipe_refs",
        "includedExplicitRefs": "included_explicit_refs",
        "includedAutoRefs": "included_auto_refs",
        "effectiveToolGrants": "effective_tool_grants",
        "effectiveToolDenials": "effective_tool_denials",
        "effectiveEvidenceRequirements": "effective_evidence_requirements",
        "effectiveApprovalRequirements": "effective_approval_requirements",
        "effectiveContextPolicy": "effective_context_policy",
    }
    for expected_field, attribute in field_map.items():
        if expected_field in expected:
            assert getattr(contract, attribute) == tuple(expected[expected_field])

    if "effectiveRetryPolicy" in expected:
        retry = expected["effectiveRetryPolicy"]
        assert contract.effective_retry_policy.max_attempts == retry["maxAttempts"]
        assert (
            contract.effective_retry_policy.repair_attempts
            == retry["repairAttempts"]
        )
        assert contract.effective_retry_policy.global_cap == retry["globalCap"]

    if "excludedRefs" in expected:
        assert tuple(
            exclusion.public_projection() for exclusion in contract.excluded_refs
        ) == tuple(expected["excludedRefs"])

    if "conflictCodes" in expected:
        assert {conflict.code for conflict in contract.conflicts} == set(
            expected["conflictCodes"]
        )

    if "hookConflictCodes" in expected:
        assert hook_contract is not None
        assert {conflict.code for conflict in hook_contract.conflicts} == set(
            expected["hookConflictCodes"]
        )
        assert hook_contract.blocked is expected.get("hookContractBlocked", False)
    if "hookContractHookCount" in expected:
        assert hook_contract is not None
        assert len(hook_contract.hooks) == expected["hookContractHookCount"]


def _assert_projection_expectations(
    fixture: dict[str, Any],
    projection: RecipeCompositionProjection,
) -> None:
    expected = fixture["expected"].get("projection", {})
    if "conflictStatus" in expected:
        assert projection.conflict_status == expected["conflictStatus"]
    if "hardSafetyStatus" in expected:
        assert projection.hard_safety_status == expected["hardSafetyStatus"]
    if "excludedReasonCodes" in expected:
        assert projection.excluded_reason_codes == tuple(
            expected["excludedReasonCodes"]
        )

    count_map = {
        "toolGrantCount": "tool_grant_count",
        "toolDenialCount": "tool_denial_count",
        "hookCount": "hook_count",
        "evidenceRequirementCount": "evidence_requirement_count",
        "approvalRequirementCount": "approval_requirement_count",
        "contextPolicyCount": "context_policy_count",
    }
    for expected_field, attribute in count_map.items():
        if expected_field in expected.get("publicSafeCounts", {}):
            assert getattr(projection.public_safe_counts, attribute) == expected[
                "publicSafeCounts"
            ][expected_field]


def _serialized_public_surfaces(
    contract: EffectiveRecipeContract,
    hook_contract: EffectiveRecipeHookContract | None,
    hook_contributions: tuple[HookContribution, ...],
    projection: RecipeCompositionProjection,
) -> str:
    serialized = (
        json.dumps(contract.public_projection(), sort_keys=True)
        + contract.model_dump_json(by_alias=True)
        + json.dumps(projection.public_projection(), sort_keys=True)
        + projection.model_dump_json(by_alias=True)
    )
    if hook_contract is not None:
        serialized += (
            json.dumps(hook_contract.public_projection(), sort_keys=True)
            + hook_contract.model_dump_json(by_alias=True)
        )
    for contribution in hook_contributions:
        serialized += (
            json.dumps(contribution.public_projection(), sort_keys=True)
            + json.dumps(contribution.model_dump(by_alias=True, mode="json"))
            + contribution.model_dump_json(by_alias=True)
        )
    return serialized


def test_required_regression_fixture_set_is_present() -> None:
    assert {fixture["caseId"] for fixture in FIXTURES} == REQUIRED_CASE_IDS


@pytest.mark.parametrize(
    "fixture",
    FIXTURES,
    ids=lambda fixture: fixture["caseId"],
)
def test_recipe_composition_regression_fixture(fixture: dict[str, Any]) -> None:
    contract, hook_contract, hook_contributions, projection = _build_fixture_contract(
        fixture
    )

    _assert_authority_flags(fixture, contract, hook_contract, projection)
    _assert_contract_expectations(fixture, contract, hook_contract)
    _assert_projection_expectations(fixture, projection)

    assert contract.effective_digest.startswith("sha256:")
    assert projection.effective_digest == contract.effective_digest
    assert projection.audit_digest.startswith("sha256:")

    serialized = _serialized_public_surfaces(
        contract,
        hook_contract,
        hook_contributions,
        projection,
    )
    for forbidden in fixture.get("projectionForbidden", ()):
        assert forbidden not in serialized
