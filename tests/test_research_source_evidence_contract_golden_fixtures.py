from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.research_source_evidence_contract import (
    ResearchSourceAttachmentFlags,
    ResearchSourceEvidenceFixture,
    load_research_source_evidence_fixture,
    project_research_source_evidence_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "research_source_evidence"


def _forbidden_bearer_fragment() -> str:
    return "Bear" + "er unsafe"


def _forbidden_github_token_fragment() -> str:
    return "ghp" + "_researchsecret"


def _forbidden_openai_key_fragment() -> str:
    return "sk" + "-research-secret"


def _forbidden_service_role_fragment() -> str:
    return "SUPABASE" + "_SERVICE_ROLE_KEY"


def test_research_source_evidence_fixture_covers_source_ledger_and_citation_gates() -> None:
    fixture = load_research_source_evidence_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_research_source_evidence_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "research_source_evidence_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "web_search_and_source_inspection_pass",
        "missing_source_inspection_blocks_claim",
        "citation_to_uninspected_source_blocks",
        "knowledge_search_source_ledger_pass",
        "temporal_research_clock_pass",
        "audit_only_missing_source_does_not_block",
        "child_research_source_scoped_child",
        "browser_source_schema_pass",
        "external_repo_source_schema_pass",
        "external_doc_source_schema_pass",
    )
    assert projection.by_verdict_state == {
        "pass": 7,
        "block_ready": 2,
        "missing": 1,
    }
    assert projection.by_category == {
        "web_search_source_inspection_pass": 1,
        "missing_source_inspection": 1,
        "citation_uninspected_source": 1,
        "knowledge_search_source_ledger_pass": 1,
        "temporal_context_clock_pass": 1,
        "audit_only_missing_source": 1,
        "child_research_source_scoped_child": 1,
        "browser_source_schema_pass": 1,
        "external_repo_source_schema_pass": 1,
        "external_doc_source_schema_pass": 1,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    passing = cases["web_search_and_source_inspection_pass"]
    assert passing.contract.requirements[0].type == "WebSearch"
    assert passing.contract.requirements[1].type == "SourceInspection"
    assert passing.citation_refs == ("src_1",)
    assert passing.expected_ok is True
    assert passing.expected_verdict_state == "pass"
    assert projection.case_snapshots["web_search_and_source_inspection_pass"][
        "matchedEvidenceTypes"
    ] == ("WebSearch", "SourceInspection")
    assert projection.case_snapshots["web_search_and_source_inspection_pass"][
        "sourceIds"
    ] == ("src_1",)
    assert projection.public_previews["web_search_and_source_inspection_pass"] == (
        "WebSearch and SourceInspection recorded src_1 for docs.example"
    )

    missing_inspection = projection.case_snapshots[
        "missing_source_inspection_blocks_claim"
    ]
    assert missing_inspection["verdictState"] == "block_ready"
    assert missing_inspection["missingRequirementTypes"] == ("SourceInspection",)
    assert missing_inspection["failureCodes"] == ("EVIDENCE_CONTRACT_MISSING",)
    assert missing_inspection["sourceSensitive"] is True

    uninspected = projection.case_snapshots["citation_to_uninspected_source_blocks"]
    assert uninspected["verdictState"] == "block_ready"
    assert uninspected["failureCodes"] == ("EVIDENCE_CONTRACT_FIELD_MISMATCH",)
    assert uninspected["citationRefs"] == ("src_2",)
    assert uninspected["sourceIds"] == ("src_1",)

    kb = projection.case_snapshots["knowledge_search_source_ledger_pass"]
    assert kb["matchedEvidenceTypes"] == ("KnowledgeSearch", "SourceInspection")
    assert kb["sourceKinds"] == ("kb",)
    assert kb["verdictState"] == "pass"

    temporal = projection.case_snapshots["temporal_research_clock_pass"]
    assert temporal["matchedEvidenceTypes"] == ("WebSearch", "SourceInspection", "Clock")
    assert temporal["sourceKinds"] == ("web_search", "web_fetch", "clock")
    assert temporal["verdictState"] == "pass"

    audit_only = projection.case_snapshots["audit_only_missing_source_does_not_block"]
    assert audit_only["enforcement"] == "audit"
    assert audit_only["verdictState"] == "missing"
    assert audit_only["authority"] == "audit_only_no_block"

    child = cases["child_research_source_scoped_child"]
    assert child.agent_role == "research"
    assert child.run_on == "child"
    assert child.spawn_depth == 1
    assert projection.case_snapshots["child_research_source_scoped_child"][
        "scope"
    ] == {
        "agentRole": "research",
        "runOn": "child",
        "spawnDepth": 1,
    }
    assert projection.case_snapshots["child_research_source_scoped_child"][
        "sourceIds"
    ] == ("src_7",)

    browser = projection.case_snapshots["browser_source_schema_pass"]
    assert browser["matchedEvidenceTypes"] == ("WebSearch", "SourceInspection")
    assert browser["sourceKinds"] == ("browser",)
    assert browser["sourceIds"] == ("src_8",)
    assert browser["verdictState"] == "pass"

    external_repo = projection.case_snapshots["external_repo_source_schema_pass"]
    assert external_repo["matchedEvidenceTypes"] == (
        "KnowledgeSearch",
        "SourceInspection",
    )
    assert external_repo["sourceKinds"] == ("external_repo",)
    assert external_repo["sourceIds"] == ("src_9",)
    assert external_repo["verdictState"] == "pass"

    external_doc = projection.case_snapshots["external_doc_source_schema_pass"]
    assert external_doc["matchedEvidenceTypes"] == ("WebSearch", "SourceInspection")
    assert external_doc["sourceKinds"] == ("external_doc",)
    assert external_doc["sourceIds"] == ("src_10",)
    assert external_doc["verdictState"] == "pass"

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        _forbidden_bearer_fragment(),
        _forbidden_github_token_fragment(),
        _forbidden_openai_key_fragment(),
        _forbidden_service_role_fragment(),
        "/data/bots",
        "/workspace",
        "private raw page",
        "adkRunnerInvoked\": true",
        "webSearchExecuted\": true",
        "browserExecuted\": true",
        "sourceFetched\": true",
        "liveToolDispatched\": true",
        "evidenceBlockEnabled\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"webSearchExecuted": True}),
            id="fixture-web-search-executed-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"adkRunnerInvoked": True}
            ),
            id="case-runner-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0].update(
                {"type": "GitDiff"}
            ),
            id="non-research-evidence-type",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0].update(
                {"preview": "/data/bots/bot-secret/workspace/private raw page"}
            ),
            id="unsafe-production-path-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][1]["fields"].update(
                {"sourceIds": ["src_9"]}
            ),
            id="source-id-does-not-cover-citation",
        ),
        pytest.param(
            lambda payload: (
                payload["cases"][0]["contract"]["requirements"][1]["fields"][
                    "sourceIds"
                ].update({"equals": ["src_9"]}),
                payload["cases"][0]["records"][1]["fields"].update(
                    {"sourceIds": ["src_9"]}
                ),
            ),
            id="citation-ref-only-appears-in-search-record",
        ),
        pytest.param(
            lambda payload: payload["cases"][6].update({"runOn": "main"}),
            id="child-scope-runon-mismatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"browserExecuted": True}
            ),
            id="nested-browser-executed-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"sourceFetched": True}
            ),
            id="nested-source-fetched-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"toolHostDispatched": True}
            ),
            id="nested-toolhost-dispatch-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"agentMemoryImported": True}
            ),
            id="nested-agentmemory-import-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][6]["records"][0]["source"].update(
                {"kind": "tool_trace"}
            ),
            id="child-record-tool-trace-source",
        ),
        pytest.param(
            lambda payload: payload["cases"][6]["records"][0]["source"]["metadata"].update(
                {"executionBoundary": "main"}
            ),
            id="child-record-main-boundary",
        ),
        pytest.param(
            lambda payload: payload["cases"][6]["contract"]["when"].pop(
                "executionBoundary"
            ),
            id="child-contract-missing-boundary",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["contract"]["when"].update(
                {"executionBoundary": "child"}
            ),
            id="main-contract-child-boundary",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["contract"]["when"].update(
                {"executionBoundary": "sidecar"}
            ),
            id="main-contract-unknown-boundary",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"][
                "metadata"
            ].update({"executionBoundary": "sidecar"}),
            id="main-record-unknown-boundary",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["contract"]["when"].update(
                {"executionBoundary": None}
            ),
            id="main-contract-null-boundary",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"][
                "metadata"
            ].update({"executionBoundary": None}),
            id="main-record-null-boundary",
        ),
        pytest.param(
            lambda payload: payload["cases"][1].update({"authority": "audit_only_no_block"}),
            id="blocking-case-audit-only-authority",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update({"citationRefs": ["summary only"]}),
            id="natural-language-citation-ref",
        ),
    ),
)
def test_research_source_evidence_fixture_rejects_live_flags_and_bad_contracts(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        ResearchSourceEvidenceFixture.model_validate(payload)


def test_research_source_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = ResearchSourceAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        webSearchExecuted=True,
        browserExecuted=True,
        sourceFetched=True,
        evidenceBlockEnabled=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"browserExecuted": True})


def test_research_source_evidence_import_boundary_stays_runtime_free() -> None:
    forbidden = (
        "google.adk.runners",
        "magi_agent.adk_bridge.local_runner",
        "magi_agent.adk_bridge.runner_adapter",
        "magi_agent.adk_bridge.tool_adapter",
        "magi_agent.tools.dispatcher",
        "magi_agent.tools.registry",
        "magi_agent.plugins.agentmemory",
        "magi_agent.memory",
        "magi_agent.services.memory",
        "magi_agent.hipocampus",
        "magi_agent.qmd",
        "magi_agent.app",
        "magi_agent.transport.chat",
        "magi_agent.routes",
    )
    loaded_before = set(sys.modules)

    fixture = load_research_source_evidence_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )
    project_research_source_evidence_fixture(fixture)

    loaded_after = set(sys.modules)
    newly_loaded = loaded_after - loaded_before
    loaded = [
        module_name
        for module_name in sorted(newly_loaded)
        for forbidden_name in forbidden
        if module_name == forbidden_name or module_name.startswith(f"{forbidden_name}.")
    ]
    assert loaded == []
