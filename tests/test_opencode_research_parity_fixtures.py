from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_DIR = REPO_ROOT / "docs/notes/research-parity/fixtures"
README_PATH = REPO_ROOT / "docs/notes/research-parity/README.md"
FIXTURE_FILENAMES = (
    "opencode-external-repo-scout.json",
    "opencode-websearch-provider-router.json",
    "opencode-child-task-lifecycle.json",
)
REQUIRED_FAILURE_CASES = {
    "url_only_citation",
    "missing_repo_overview",
    "child_summary_without_evidence_envelope",
    "unmanaged_external_path_read",
    "live_network_attempt",
}
FORBIDDEN_TEXT = (
    "http://",
    "https://",
    "/Users/",
    "/home/",
    "/workspace/",
    "/data/bots/",
    "Authorization",
    "Cookie",
    "Bearer ",
    "api_key",
    "token=",
)


def _load_fixture(filename: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _all_fixtures() -> list[dict[str, object]]:
    return [_load_fixture(filename) for filename in FIXTURE_FILENAMES]


def test_opencode_research_parity_readme_links_all_fixtures() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    for filename in FIXTURE_FILENAMES:
        assert f"fixtures/{filename}" in readme
    assert "OpenCode-derived benchmark fixtures" in readme
    assert "fixture-only" in readme


def test_opencode_research_parity_fixtures_are_default_off_and_local_only() -> None:
    for fixture in _all_fixtures():
        rendered = json.dumps(fixture, sort_keys=True)

        assert fixture["version"] == 1
        assert fixture["defaultOff"] is True
        assert fixture["localOnly"] is True
        assert fixture["fixtureOnly"] is True
        assert fixture["activationGate"] == "opencode-research-benchmark-fixtures-only"
        assert fixture["liveAuthorityAllowed"] is False
        assert fixture["liveNetworkAllowed"] is False
        assert fixture["liveGitCloneAllowed"] is False
        assert fixture["browserExecutionAllowed"] is False
        assert fixture["modelCallsAllowed"] is False
        assert fixture["toolExecutionAllowed"] is False
        assert fixture["memoryWritesAllowed"] is False
        assert fixture["channelDeliveryAllowed"] is False
        for forbidden in FORBIDDEN_TEXT:
            assert forbidden not in rendered


def test_opencode_research_parity_fixtures_require_source_proof_not_urls() -> None:
    for fixture in _all_fixtures():
        source_requirements = fixture["sourceRequirements"]
        claim_requirements = fixture["claimRequirements"]

        assert source_requirements
        for source in source_requirements:
            assert source["sourceRefId"].startswith("src:")
            assert source["openedSnapshotReceiptId"].startswith("receipt:")
            assert source["contentDigest"].startswith("sha256:")
            assert source["inspectedAt"].endswith("Z")
            assert source["supportSpans"]
            assert "url" not in source

        assert claim_requirements
        for claim in claim_requirements:
            assert claim["claimId"].startswith("claim:")
            assert claim["supportVerdict"] in {"supported", "qualified", "contradicted"}
            assert claim["sourceRefIds"]
            assert claim["spanRefs"]
            assert "url" not in claim


def test_opencode_research_parity_fixtures_cover_required_failure_cases() -> None:
    seen: set[str] = set()
    for fixture in _all_fixtures():
        for failure in fixture["failureCases"]:
            seen.add(failure["caseId"])
            assert failure["expectedVerdict"] == "fail"
            assert failure["repairAction"] in {
                "inspect_source",
                "add_repo_overview",
                "require_child_evidence_envelope",
                "block_unmanaged_path",
                "block_live_network",
            }
            assert failure["rendersAsFact"] is False

    assert REQUIRED_FAILURE_CASES.issubset(seen)


def test_external_repo_scout_fixture_requires_repo_tools_and_exact_spans() -> None:
    fixture = _load_fixture("opencode-external-repo-scout.json")

    assert fixture["scenarioId"] == "opencode.external_repo_scout"
    assert fixture["owningLayer"] == "Tests/docs only"
    assert fixture["requiredToolAdapters"] == [
        "RepoClone",
        "RepoOverview",
        "Grep",
        "Read",
    ]
    assert fixture["sourceInspectionMode"] == "external_repo"
    assert fixture["managedCacheOnly"] is True
    assert fixture["requiredPathLineRefs"] == [
        {
            "sourceRefId": "src:repo-opencode-agent",
            "pathRef": "repo-cache:packages/opencode/src/agent/agent.ts",
            "lineRange": [106, 205],
        }
    ]


def test_websearch_router_fixture_is_fake_provider_parser_only() -> None:
    fixture = _load_fixture("opencode-websearch-provider-router.json")
    router = fixture["providerRouter"]

    assert fixture["scenarioId"] == "opencode.websearch_provider_router"
    assert fixture["fakeProviderOnly"] is True
    assert router["sessionStableProvider"] is True
    assert router["overrideBehavior"] == "fixture_override_metadata_only"
    assert router["providerCandidates"] == ["exa_fixture", "parallel_fixture"]
    assert router["parserFixtures"] == ["json_result_fixture", "sse_result_fixture"]
    assert router["liveProviderRoutingAllowed"] is False


def test_child_task_lifecycle_fixture_requires_runtime_child_evidence_envelopes() -> None:
    fixture = _load_fixture("opencode-child-task-lifecycle.json")

    assert fixture["scenarioId"] == "opencode.child_task_lifecycle_benchmark"
    assert fixture["parentCreatesChildTasks"] is True
    assert fixture["acceptsRawChildSummary"] is False
    assert fixture["requiresRuntimeIssuedChildEvidenceEnvelope"] is True
    assert fixture["childTasks"]
    for child in fixture["childTasks"]:
        assert child["runtimeIssuedEnvelopeRequired"] is True
        assert child["acceptedEvidenceMetadataOnly"] is True
        assert child["rawTranscriptProjectionAllowed"] is False
        assert child["requiredSourceRefs"]
