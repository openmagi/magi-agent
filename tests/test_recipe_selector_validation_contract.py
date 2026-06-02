from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

import magi_agent.recipes.selector_validation as selector_validation
from magi_agent.recipes.selector_validation import (
    RecipeSelectorFixture,
    evaluate_recipe_selector_fixture,
)


def test_research_prompt_requires_cited_research_recipe() -> None:
    fixture = RecipeSelectorFixture.model_validate(
        {
            "fixtureId": "research-market-brief",
            "inputText": "시장 조사해서 출처와 함께 요약해줘",
            "selectedRecipeIds": ["openmagi.research.cited-market-brief.v1"],
            "expectedRecipeIds": ["openmagi.research.cited-market-brief.v1"],
            "expectedGoverned": True,
            "actualGoverned": True,
            "expectedRouteRef": "workflow:cited-market-brief",
            "actualRouteRef": "workflow:cited-market-brief",
            "mustEnable": ["sourceLedger", "citationValidators"],
            "enabledRefs": ["sourceLedger", "citationValidators"],
            "mustDeny": ["uncitedFinalAnswer"],
            "deniedRefs": ["uncitedFinalAnswer"],
        }
    )

    verdict = evaluate_recipe_selector_fixture(fixture)

    assert verdict.status == "pass"


def test_backoffice_prompt_requires_numeric_recipe() -> None:
    fixture = RecipeSelectorFixture.model_validate(
        {
            "fixtureId": "backoffice-spreadsheet",
            "inputText": "이 엑셀 합계와 증감률 계산해줘",
            "selectedRecipeIds": ["openmagi.backoffice.finance-summary.v1"],
            "expectedRecipeIds": ["openmagi.backoffice.finance-summary.v1"],
            "expectedGoverned": True,
            "actualGoverned": True,
            "expectedRouteRef": "workflow:numeric-finance-summary",
            "actualRouteRef": "workflow:numeric-finance-summary",
            "mustEnable": ["calculationReceipts", "noBareNumbers"],
            "enabledRefs": ["calculationReceipts", "noBareNumbers"],
            "mustDeny": ["llmDirectArithmetic"],
            "deniedRefs": ["llmDirectArithmetic"],
        }
    )

    verdict = evaluate_recipe_selector_fixture(fixture)

    assert verdict.status == "pass"


def test_governed_task_fails_if_selected_route_is_not_governed() -> None:
    fixture = RecipeSelectorFixture.model_validate(
        {
            "fixtureId": "tenant-ledger-fell-through",
            "inputText": "계약 원장 검토해서 증거와 함께 요약해줘",
            "selectedRecipeIds": ["openmagi.tenant-lite-summary.v1"],
            "expectedRecipeIds": ["openmagi.tenant-ledger-review.v1"],
            "expectedGoverned": True,
            "actualGoverned": False,
            "expectedRouteRef": "workflow:tenant-ledger-review",
            "actualRouteRef": "route:tenant-safe-summary",
            "mustEnable": ["sourceLedger"],
            "enabledRefs": [],
            "mustDeny": ["rawFinalAnswer"],
            "deniedRefs": [],
        }
    )

    verdict = evaluate_recipe_selector_fixture(fixture)

    assert verdict.status == "fail"
    assert "expected_recipe_missing" in verdict.reason_codes
    assert "required_enable_ref_missing" in verdict.reason_codes
    assert "governed_selector_resolved_non_governed" in verdict.reason_codes


def test_selector_validation_reports_missing_deny_ref() -> None:
    fixture = RecipeSelectorFixture.model_validate(
        {
            "fixtureId": "research-fell-through-deny",
            "inputText": "시장 조사해줘",
            "selectedRecipeIds": ["openmagi.research.cited-market-brief.v1"],
            "expectedRecipeIds": ["openmagi.research.cited-market-brief.v1"],
            "expectedGoverned": True,
            "actualGoverned": True,
            "mustDeny": ["uncitedFinalAnswer"],
            "deniedRefs": [],
        }
    )

    verdict = evaluate_recipe_selector_fixture(fixture)

    assert verdict.status == "fail"
    assert verdict.missing_deny_refs == ("uncitedFinalAnswer",)
    assert "required_deny_ref_missing" in verdict.reason_codes


def test_governed_task_fails_if_required_recipe_resolves_to_non_governed_route() -> None:
    fixture = RecipeSelectorFixture.model_validate(
        {
            "fixtureId": "tenant-ledger-route-downgrade",
            "inputText": "증빙 필요한 원장 검토",
            "selectedRecipeIds": ["openmagi.tenant-ledger-review.v1"],
            "expectedRecipeIds": ["openmagi.tenant-ledger-review.v1"],
            "expectedGoverned": True,
            "actualGoverned": False,
            "expectedRouteRef": "workflow:tenant-ledger-review",
            "actualRouteRef": "route:tenant-lite-answer",
            "mustEnable": ["sourceLedger", "citationValidators"],
            "enabledRefs": ["sourceLedger", "citationValidators"],
            "mustDeny": ["uncitedFinalAnswer"],
            "deniedRefs": ["uncitedFinalAnswer"],
        }
    )

    verdict = evaluate_recipe_selector_fixture(fixture)

    assert verdict.status == "fail"
    assert verdict.governance_mismatch is True
    assert "governed_selector_resolved_non_governed" in verdict.reason_codes


def test_selector_governance_is_metadata_driven_not_category_name_hardcoded() -> None:
    fixture = RecipeSelectorFixture.model_validate(
        {
            "fixtureId": "ungoverned-lite-answer",
            "inputText": "짧게 안내해줘",
            "selectedRecipeIds": ["openmagi.freeform-lite-answer.v1"],
            "expectedRecipeIds": ["openmagi.freeform-lite-answer.v1"],
            "expectedGoverned": False,
            "actualGoverned": False,
            "expectedRouteRef": "route:tenant-lite-answer",
            "actualRouteRef": "route:tenant-lite-answer",
        }
    )

    verdict = evaluate_recipe_selector_fixture(fixture)

    assert verdict.status == "pass"
    assert verdict.reason_codes == ("selector_fixture_passed",)
    assert "general-chat" not in inspect.getsource(selector_validation.evaluate_recipe_selector_fixture)


def test_legacy_general_chat_fallback_knob_is_not_accepted() -> None:
    with pytest.raises(ValidationError):
        RecipeSelectorFixture.model_validate(
            {
                "fixtureId": "tenant-route-downgrade",
                "inputText": "시장 조사해서 출처와 함께 요약해줘",
                "selectedRecipeIds": ["openmagi.tenant-ledger-review.v1"],
                "expectedRecipeIds": ["openmagi.research.cited-market-brief.v1"],
                "expectedGoverned": True,
                "actualGoverned": False,
                "forbidGeneralChatFallback": False,
            }
        )


def test_selector_route_refs_reject_protected_or_pathlike_metadata() -> None:
    with pytest.raises(ValidationError, match="expectedRouteRef"):
        RecipeSelectorFixture.model_validate(
            {
                "fixtureId": "tenant-route-downgrade",
                "inputText": "증빙 필요한 원장 검토",
                "selectedRecipeIds": ["openmagi.tenant-ledger-review.v1"],
                "expectedRecipeIds": ["openmagi.tenant-ledger-review.v1"],
                "expectedGoverned": True,
                "actualGoverned": False,
                "expectedRouteRef": "../session-token-route",
                "actualRouteRef": "route:tenant-lite-answer",
            }
        )
