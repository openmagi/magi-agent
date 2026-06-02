from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MATRIX_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "research_determinism_harness"
    / "matrix.json"
)

REQUIRED_ROW_IDS = (
    "action_proof_matrix",
    "source_proof_matrix",
    "url_only_citation_rejection",
    "unopened_source_rejection",
    "stale_source_rejection",
    "claim_graph_support_mapping",
    "weak_claim_downgrade",
    "unsupported_claim_block",
    "acceptance_criteria_extraction",
    "task_completion_proof",
    "child_evidence_envelope_acceptance",
    "child_raw_summary_rejection",
    "intermediate_synthesis_boundary",
    "final_projection_boundary",
    "repair_action_result",
    "default_off_authority_flags",
)
REQUIRED_ACTION_PROOF_VERBS = (
    "searched",
    "read",
    "reviewed",
    "compared",
    "confirmed",
    "analyzed",
    "summarized",
)
RESERVED_ACTION_CLAIM_VERBS = ("checked", "verified")
REQUIRED_SOURCE_PROOF_FIELDS = ("opened", "snapshot", "digest", "timestamp", "span")
REQUIRED_FIELDS = {
    "id",
    "requirement",
    "owningLayer",
    "behaviorOwner",
    "genericCoreRole",
    "testOwner",
    "status",
    "liveCapable",
    "activationGate",
    "defaultOff",
    "trafficAttached",
    "localOnly",
    "fakeProviderOnly",
    "adkPrimitives",
    "notes",
    "publicProjection",
}
ALLOWED_ROW_FIELDS = REQUIRED_FIELDS | {
    "actionProofVerbs",
    "reservedActionClaimVerbs",
    "sourceProofFields",
}
TOP_LEVEL_FIELDS = {
    "fixtureId",
    "scope",
    "track",
    "noLiveExecution",
    "defaultOff",
    "trafficAttached",
    "rows",
}
RESEARCH_OWNING_LAYERS = {
    "Research recipe/harness/plugin",
    "Tests/docs only",
}
TEST_STATUSES = {"planned", "explicit_missing"}
FORBIDDEN_GENERIC_CORE_BEHAVIOR = (
    "generic core enforces",
    "generic core owns",
    "core owns research",
    "core hard-codes research",
    "runtime hard-codes research",
)
UNSAFE_FRAGMENTS = (
    "authorization",
    "bearer ",
    "cookie",
    "token",
    "api_key",
    "api-key",
    "secret",
    "/users/",
    "/workspace",
    "/data/bots",
    "raw source",
    "private path",
)
UNSAFE_KEY_FRAGMENTS = (
    "authorization",
    "auth",
    "cookie",
    "credential",
    "token",
    "api_key",
    "apikey",
    "secret",
    "raw",
    "private",
    "path",
    "transcript",
    "prompt",
    "toollog",
    "tool_log",
    "tooloutput",
    "tool_output",
    "sourcebody",
    "source_body",
    "sourcetext",
    "source_text",
)
ADK_TOOL_PRIMITIVES = ("FunctionTool", "LongRunningFunctionTool")


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text())


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
        result: list[str] = []
        for key, nested in value.items():
            result.append(str(key))
            result.extend(_keys(nested))
        return result
    if isinstance(value, list):
        result = []
        for nested in value:
            result.extend(_keys(nested))
        return result
    return []


def test_required_rows_exist_exactly_once_in_plan_order() -> None:
    matrix = _load_matrix()
    row_ids = [row["id"] for row in matrix["rows"]]

    assert row_ids == list(REQUIRED_ROW_IDS)
    assert len(row_ids) == len(set(row_ids))


def test_rows_have_owner_test_owner_or_explicit_missing_status() -> None:
    matrix = _load_matrix()

    assert set(matrix) == TOP_LEVEL_FIELDS
    for row in matrix["rows"]:
        assert set(row) >= REQUIRED_FIELDS
        assert set(row) <= ALLOWED_ROW_FIELDS
        assert row["owningLayer"] in RESEARCH_OWNING_LAYERS
        assert row["behaviorOwner"] == "research_harness_recipe_plugin"
        assert row["genericCoreRole"] in {
            "reused substrate only",
            "none",
            "planned substrate only",
        }
        assert row["status"] in TEST_STATUSES
        assert row["testOwner"] or row["status"] == "explicit_missing"


def test_action_and_source_proof_rows_cover_required_subrequirements() -> None:
    rows = {row["id"]: row for row in _load_matrix()["rows"]}

    assert tuple(rows["action_proof_matrix"]["actionProofVerbs"]) == (
        REQUIRED_ACTION_PROOF_VERBS
    )
    assert tuple(rows["action_proof_matrix"]["reservedActionClaimVerbs"]) == (
        RESERVED_ACTION_CLAIM_VERBS
    )
    assert tuple(rows["source_proof_matrix"]["sourceProofFields"]) == (
        REQUIRED_SOURCE_PROOF_FIELDS
    )


def test_no_row_assigns_research_specific_behavior_to_generic_core() -> None:
    for row in _load_matrix()["rows"]:
        row_text = " ".join(_strings(row)).lower()
        assert row["owningLayer"] != "Generic core"
        assert row["behaviorOwner"] != "generic_core"
        assert not any(phrase in row_text for phrase in FORBIDDEN_GENERIC_CORE_BEHAVIOR), (
            row["id"],
            row_text,
        )


def test_every_required_row_has_default_off_activation_gate() -> None:
    for row in _load_matrix()["rows"]:
        assert row["liveCapable"] is True
        assert row["activationGate"]
        assert "default-off" in row["activationGate"]
        assert "local-only" in row["activationGate"]
        assert "fake-provider" in row["activationGate"]
        assert row["defaultOff"] is True
        assert row["trafficAttached"] is False
        assert row["localOnly"] is True
        assert row["fakeProviderOnly"] is True


def test_adk_tool_exposure_requires_toolhost_mediation() -> None:
    for row in _load_matrix()["rows"]:
        row_text = " ".join(_strings(row))
        if any(primitive in row_text for primitive in ADK_TOOL_PRIMITIVES):
            assert "ToolHost-mediated" in row_text
            assert "direct ADK tool execution rejected" in row_text


def test_matrix_is_local_fake_digest_safe_and_documents_adk_usage() -> None:
    matrix = _load_matrix()

    assert matrix["fixtureId"] == "research_determinism_harness_pr0_matrix_20260526"
    assert matrix["scope"] == "docs-tests-only"
    assert matrix["noLiveExecution"] is True
    assert matrix["defaultOff"] is True
    assert matrix["trafficAttached"] is False

    for row in matrix["rows"]:
        assert row["adkPrimitives"]
        assert row["publicProjection"] == "digest-safe"

    encoded_values = " ".join(_strings(matrix)).lower()
    assert not any(fragment in encoded_values for fragment in UNSAFE_FRAGMENTS)
    encoded_keys = " ".join(key.lower() for key in _keys(matrix))
    assert not any(fragment in encoded_keys for fragment in UNSAFE_KEY_FRAGMENTS)
