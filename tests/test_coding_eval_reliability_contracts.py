from __future__ import annotations

import json
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any


PYTHON_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PYTHON_ROOT / "tests/fixtures/coding_eval/reliability_matrix.json"
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/coding_harness_consolidated_matrix.json"
PR8_CHANGED_FILES = {
    "tests/fixtures/coding_eval/reliability_matrix.json",
    "tests/test_coding_eval_reliability_contracts.py",
    "tests/fixtures/parity/coding_harness_consolidated_matrix.json",
}

SCORING_CATEGORIES = {"pass", "fail", "manual-review", "infra-unavailable"}
REQUIRED_BENCHMARK_EVIDENCE = {
    "changedFiles",
    "GitDiff",
    "TestRun",
    "Checkpoint",
    "DeliveryAck",
}
FORBIDDEN_TEXT_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer ",
    "bot_request",
    "browser_session",
    "live_benchmark",
    "model_call",
    "model_request",
    "mcp_session",
    "provider_request",
    "raw_private",
    "secret",
    "shell_session",
)
FORBIDDEN_COMPACT_MARKERS = (
    "apikey",
    "authorization",
    "bearer",
    "botrequest",
    "browsersession",
    "cookie",
    "livebenchmarkrequest",
    "livebenchmarkrun",
    "mcpsession",
    "modelcall",
    "modelrequest",
    "privatepath",
    "providerrequest",
    "rawdiff",
    "rawhunk",
    "rawprivate",
    "secret",
    "shellsession",
    "token",
)
RAW_HUNK_MARKER_PREFIXES = ("<<<<<<<", "=======", ">>>>>>>", "@@ ")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def test_coding_eval_reliability_fixture_is_local_adk_eval_contract() -> None:
    fixture = _load_json(FIXTURE_PATH)
    rows = _rows_by_id(fixture["rows"])

    assert fixture["schemaVersion"] == "codingEvalReliabilityMatrix.v1"
    assert fixture["fixtureId"] == "coding_eval_reliability_matrix_0001"
    assert fixture["adkPrimitive"] == "ADK Evaluation vocabulary and artifact refs"
    assert fixture["authority"] == {
        "defaultOff": True,
        "localOnly": True,
        "liveAuthorityAllowed": False,
        "coreTouchAllowed": False,
        "workspaceMutationAllowed": False,
        "providerModelToolMcpBrowserShellActivationAllowed": False,
    }
    assert fixture["captureMode"] == "fixture_only_no_live_capture"
    assert fixture["artifactRefPrefix"] == "adk-eval-artifact://coding-eval-reliability/"
    assert set(fixture["scoringCategories"]) == SCORING_CATEGORIES
    assert set(fixture["benchmarkEvidenceRequirements"]) == REQUIRED_BENCHMARK_EVIDENCE
    assert len(fixture["rows"]) == 5
    assert set(rows) == {
        "scoring_categories",
        "benchmark_evidence_requirements",
        "worktree_apply_conflict_metadata",
        "repo_task_lock_contract",
        "commit_unit_boundary_contract",
    }

    serialized = json.dumps(fixture, sort_keys=True).lower()
    assert not any(marker in serialized for marker in FORBIDDEN_TEXT_MARKERS)
    compact_serialized = "".join(char for char in serialized if char.isalnum())
    assert not any(marker in compact_serialized for marker in FORBIDDEN_COMPACT_MARKERS)


def test_coding_eval_reliability_rows_encode_required_contract_shapes() -> None:
    rows = _rows_by_id(_load_json(FIXTURE_PATH)["rows"])

    scoring = rows["scoring_categories"]
    assert scoring["kind"] == "adk_evaluation_scoring_contract"
    assert set(scoring["categories"]) == SCORING_CATEGORIES
    assert scoring["manualReview"]["requiresReviewerReason"] is True
    assert scoring["infraUnavailable"]["countsAsQualityFailure"] is False
    assert all(ref.startswith("adk-eval-artifact://") for ref in scoring["artifactRefs"])

    benchmark = rows["benchmark_evidence_requirements"]
    assert benchmark["kind"] == "adk_evaluation_benchmark_contract"
    evidence = {item["type"]: item for item in benchmark["requiredEvidence"]}
    assert set(evidence) == REQUIRED_BENCHMARK_EVIDENCE
    assert evidence["changedFiles"]["fields"] == ["paths", "count"]
    assert evidence["GitDiff"]["fields"] == ["summary", "files", "stat"]
    assert evidence["TestRun"]["fields"] == ["command", "exitCode", "passed"]
    assert evidence["Checkpoint"]["fields"] == ["checkpointId", "afterEvidenceTypes"]
    assert evidence["DeliveryAck"]["fields"] == ["messageId", "deliveredAt", "channel"]
    assert benchmark["capturePolicy"] == "local_fixture_only"
    assert benchmark["liveBenchmarkCapture"] is False

    conflict = rows["worktree_apply_conflict_metadata"]
    assert conflict["kind"] == "worktree_apply_conflict_contract"
    conflict_metadata = conflict["conflictMetadata"]
    assert set(conflict_metadata) == {
        "baseSha",
        "worktreePath",
        "targetBranch",
        "conflictedFiles",
        "hunks",
        "resolutionState",
        "retryable",
    }
    assert conflict_metadata["conflictedFiles"] == ["src/app.py", "tests/test_app.py"]
    assert conflict_metadata["resolutionState"] == "manual-review"
    assert conflict["scoreCategory"] == "manual-review"
    assert not PurePosixPath(conflict_metadata["worktreePath"]).is_absolute()
    for hunk in conflict_metadata["hunks"]:
        assert set(hunk) == {"file", "startLine", "endLine", "reason"}
        for value in hunk.values():
            if isinstance(value, str):
                assert "\n" not in value
                assert not value.startswith(RAW_HUNK_MARKER_PREFIXES)

    repo_lock = rows["repo_task_lock_contract"]
    assert repo_lock["kind"] == "repo_task_lock_contract"
    assert repo_lock["localFakeContract"] is True
    assert repo_lock["authority"] == "local_lock_fixture_only"
    assert set(repo_lock["lockFields"]) == {
        "repo",
        "taskId",
        "owner",
        "expiresAt",
        "scope",
        "conflictPolicy",
    }

    commit_unit = rows["commit_unit_boundary_contract"]
    assert commit_unit["kind"] == "commit_unit_boundary_contract"
    assert commit_unit["localFakeContract"] is True
    assert commit_unit["boundaryFields"] == [
        "changedFiles",
        "evidenceRefs",
        "checkpointRef",
        "deliveryAckRef",
        "commitMessageSubject",
    ]
    assert commit_unit["requiresEvidenceTypes"] == [
        "changedFiles",
        "GitDiff",
        "TestRun",
        "Checkpoint",
        "DeliveryAck",
    ]
    assert set(commit_unit["sample"]["changedFiles"]) == PR8_CHANGED_FILES


def test_pr8_matrix_row_points_to_gap_fixture_and_stays_default_off() -> None:
    row = _rows_by_id(_load_json(MATRIX_PATH)["rows"])[
        "coding_measurement_eval_and_reliability_train"
    ]

    assert row["alreadyCovered"] is True
    assert row["missingImplementation"] == ["complete"]
    assert row["prSlice"] == "PR8"
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False
    assert row["coreTouchAllowed"] is False
    assert row["activationGate"] == "PR8-gap-tests-docs-only"
    assert row["adkPrimitive"] == "ADK Evaluation vocabulary and artifact refs"
    assert (
        "tests/fixtures/coding_eval/reliability_matrix.json"
        in row["coveredByFiles"]
    )
    assert "tests/test_coding_eval_reliability_contracts.py" in row["coveredByTests"]
    assert "evals/regression_gates.py" in row["coveredByFiles"]
    assert "tests/test_self_improvement_eval_capture.py" in row["coveredByTests"]
