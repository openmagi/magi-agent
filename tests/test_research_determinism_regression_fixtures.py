from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, get_args

from openmagi_core_agent.research.final_projection_gate import (
    ResearchFinalProjectionRepairAction,
)


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "research_determinism_harness"
    / "blocked_regressions.json"
)

REQUIRED_CASE_IDS = (
    "agent_claims_searched_without_receipt",
    "read_pricing_page_from_search_url_only",
    "competitor_comparison_single_source_only",
    "citation_to_unopened_source",
    "citation_span_mismatch",
    "numeric_claim_mismatch",
    "weak_inference_rendered_as_fact",
    "stale_source_accepted_as_current",
    "child_summary_without_envelope",
    "missing_requested_criterion_accepted",
    "private_projection_leakage",
    "final_answer_bypasses_failed_intermediate_boundary",
)

REQUIRED_ROWS = (
    "action_proof_matrix",
    "source_proof_matrix",
    "url_only_citation_rejection",
    "unopened_source_rejection",
    "stale_source_rejection",
    "claim_graph_support_mapping",
    "weak_claim_downgrade",
    "unsupported_claim_block",
    "task_completion_proof",
    "child_raw_summary_rejection",
    "intermediate_synthesis_boundary",
    "final_projection_boundary",
)

TOP_LEVEL_FIELDS = {
    "fixtureId",
    "scope",
    "track",
    "defaultOff",
    "noLiveExecution",
    "liveAuthorityAllowed",
    "trafficAttached",
    "cases",
}
CASE_FIELDS = {
    "id",
    "title",
    "regressionSignal",
    "researchDeterminismRows",
    "owningLayer",
    "selector",
    "enforcement",
    "executionPosture",
    "authorityFlags",
    "expectedOutcome",
    "publicProjection",
    "adkUsageNotes",
}
SELECTOR_FIELDS = {
    "route",
    "governedResearchHarness",
    "fallbackRoute",
    "fallbackToGeneralChatAllowed",
}
ENFORCEMENT_FIELDS = {
    "hardInvariant",
    "mode",
    "defaultOff",
    "localOnly",
    "fakeProviderOnly",
    "liveAuthorityAllowed",
}
EXECUTION_POSTURE_FIELDS = {
    "defaultOff",
    "localOnly",
    "fakeProviderOnly",
    "liveExecutionAllowed",
    "providerCallsAllowed",
    "browserExecutionAllowed",
    "toolExecutionAllowed",
    "modelCallsAllowed",
    "memoryWritesAllowed",
    "channelDeliveryAllowed",
    "productionRoutingAllowed",
    "routeActivationAllowed",
    "userVisiblePythonActivationAllowed",
}
AUTHORITY_FLAGS_FIELDS = {
    "liveToolDispatched",
    "providerCalled",
    "browserOpened",
    "modelCalled",
    "memoryWritten",
    "channelDeliveryPerformed",
    "productionRouteActivated",
    "routeActivated",
    "userVisiblePythonActivated",
    "liveAuthorityExercised",
}
EXPECTED_FIELDS = {
    "mustBlock",
    "acceptableProjection",
    "allowedStatuses",
    "repairAction",
    "boundaryAction",
    "boundaryStatus",
}
PUBLIC_PROJECTION_FIELDS = {
    "metadataOnly",
    "digestSafe",
    "rawGovernedProjection",
    "rawSourceProjected",
    "privateDataProjected",
    "outputContainsFactsWithoutSupport",
}
CANONICAL_REPAIR_ACTIONS = set(get_args(ResearchFinalProjectionRepairAction))
MUST_BLOCK_STATUSES = {"repair_required", "blocked"}
UNSAFE_VALUE_FRAGMENTS = (
    "authorization",
    "authorization:",
    "auth=",
    "bearer ",
    "cookie",
    "cookie:",
    "set-cookie",
    "api key",
    "api-key",
    "api_key",
    "credential",
    "credentials",
    "secret=",
    "secret:",
    "token=",
    "token:",
    "raw source",
    "raw_source",
    "raw-source",
    "raw tool",
    "raw_tool",
    "tool output",
    "tool_output",
    "source text",
    "source_text",
    "source body",
    "source_body",
    "private path",
    "private_path",
    "production route",
    "prod route",
    "/users/",
    "/home/",
    "/workspace/",
    "/data/bots/",
    "c:\\",
    "c:/",
    "\\\\host\\",
)
UNSAFE_KEY_FRAGMENTS = (
    "authorization",
    "auth",
    "credential",
    "credentials",
    "apikey",
    "secret",
    "token",
    "cookie",
    "rawsource",
    "rawtool",
    "tooloutput",
    "sourcetext",
    "sourcebody",
    "sourcecontent",
    "privatepath",
    "privatefilepath",
    "password",
    "prod",
)
SAFE_POLICY_KEYS = {
    "authorityFlags",
    "liveAuthorityAllowed",
    "liveAuthorityExercised",
    "rawGovernedProjection",
    "rawSourceProjected",
    "privateDataProjected",
    "productionRoutingAllowed",
    "productionRouteActivated",
}
FORBIDDEN_SELECTOR_FRAGMENTS = ("general", "chat_proxy", "non_governed")


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text())


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for nested in value.values():
            result.extend(_strings(nested))
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            result.extend(_strings(nested))
        return result
    return []


def _keys(value: object) -> list[str]:
    if isinstance(value, dict):
        result = [str(key) for key in value]
        for nested in value.values():
            result.extend(_keys(nested))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for nested in value:
            result.extend(_keys(nested))
        return result
    return []


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", key.lower())


def _has_unsafe_key_fragment(key: str) -> bool:
    if key in SAFE_POLICY_KEYS:
        return False
    normalized = _normalized_key(key)
    return any(fragment in normalized for fragment in UNSAFE_KEY_FRAGMENTS)


def test_required_research_regression_fixture_cases_exist_once() -> None:
    fixture = _load_fixture()
    case_ids = [case["id"] for case in fixture["cases"]]

    assert set(fixture) == TOP_LEVEL_FIELDS
    assert fixture["fixtureId"] == "research_determinism_blocked_regressions_20260527"
    assert fixture["scope"] == "tests-fixtures-only"
    assert fixture["defaultOff"] is True
    assert fixture["noLiveExecution"] is True
    assert fixture["liveAuthorityAllowed"] is False
    assert fixture["trafficAttached"] is False
    assert tuple(case_ids) == REQUIRED_CASE_IDS
    assert len(case_ids) == len(set(case_ids))


def test_regression_fixtures_cover_required_matrix_rows() -> None:
    rows_by_case = {
        case["id"]: set(case["researchDeterminismRows"])
        for case in _load_fixture()["cases"]
    }
    covered_rows = set().union(*rows_by_case.values())

    for row in REQUIRED_ROWS:
        assert row in covered_rows
    for case_id, rows in rows_by_case.items():
        assert rows, case_id


def test_must_block_fixtures_cannot_be_marked_acceptable() -> None:
    for case in _load_fixture()["cases"]:
        assert set(case) == CASE_FIELDS
        expected = case["expectedOutcome"]
        assert set(expected) == EXPECTED_FIELDS
        assert expected["mustBlock"] is True
        assert expected["acceptableProjection"] is False
        assert set(expected["allowedStatuses"]) <= MUST_BLOCK_STATUSES
        assert expected["repairAction"] in CANONICAL_REPAIR_ACTIONS
        assert expected["boundaryAction"] == "block"
        assert expected["boundaryStatus"] == "blocked"


def test_hard_invariants_are_not_log_only() -> None:
    for case in _load_fixture()["cases"]:
        enforcement = case["enforcement"]
        assert set(enforcement) == ENFORCEMENT_FIELDS
        assert enforcement["hardInvariant"] is True
        assert enforcement["mode"] == "block"
        assert enforcement["mode"] != "log_only"
        assert enforcement["defaultOff"] is True
        assert enforcement["localOnly"] is True
        assert enforcement["fakeProviderOnly"] is True
        assert enforcement["liveAuthorityAllowed"] is False


def test_execution_posture_disallows_live_authority_and_activation() -> None:
    for case in _load_fixture()["cases"]:
        posture = case["executionPosture"]
        assert set(posture) == EXECUTION_POSTURE_FIELDS
        assert posture["defaultOff"] is True
        assert posture["localOnly"] is True
        assert posture["fakeProviderOnly"] is True
        for field in EXECUTION_POSTURE_FIELDS - {
            "defaultOff",
            "localOnly",
            "fakeProviderOnly",
        }:
            assert posture[field] is False, (case["id"], field)

        authority_flags = case["authorityFlags"]
        assert set(authority_flags) == AUTHORITY_FLAGS_FIELDS
        for field in AUTHORITY_FLAGS_FIELDS:
            assert authority_flags[field] is False, (case["id"], field)


def test_governed_research_fixtures_cannot_fallback_to_general_chat() -> None:
    for case in _load_fixture()["cases"]:
        selector = case["selector"]
        assert set(selector) == SELECTOR_FIELDS
        assert selector["route"] == "research_determinism_harness"
        assert selector["governedResearchHarness"] is True
        assert selector["fallbackRoute"] is None
        assert selector["fallbackToGeneralChatAllowed"] is False
        selector_text = " ".join(_strings(selector)).lower()
        assert not any(fragment in selector_text for fragment in FORBIDDEN_SELECTOR_FRAGMENTS)


def test_public_projection_fixtures_are_digest_safe_and_not_raw() -> None:
    fixture = _load_fixture()
    for case in fixture["cases"]:
        public_projection = case["publicProjection"]
        assert set(public_projection) == PUBLIC_PROJECTION_FIELDS
        assert public_projection["metadataOnly"] is True
        assert public_projection["digestSafe"] is True
        assert public_projection["rawGovernedProjection"] is False
        assert public_projection["rawSourceProjected"] is False
        assert public_projection["privateDataProjected"] is False
        assert public_projection["outputContainsFactsWithoutSupport"] is False

    encoded_values = " ".join(_strings(fixture)).lower()
    assert not any(fragment in encoded_values for fragment in UNSAFE_VALUE_FRAGMENTS)
    unsafe_keys = [key for key in _keys(fixture) if _has_unsafe_key_fragment(key)]
    assert unsafe_keys == []
