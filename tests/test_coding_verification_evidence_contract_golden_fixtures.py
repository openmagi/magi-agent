from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.coding_verification_evidence_contract import (
    CodingVerificationAttachmentFlags,
    CodingVerificationEvidenceFixture,
    load_coding_verification_evidence_fixture,
    project_coding_verification_evidence_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "coding_verification_evidence"


def _case_payload(payload: dict[str, object], case_id: str) -> dict[str, object]:
    cases = payload["cases"]
    assert isinstance(cases, list)
    for case in cases:
        assert isinstance(case, dict)
        if case.get("caseId") == case_id:
            return case
    raise AssertionError(f"missing fixture case {case_id}")


def _set_first_planner_command_runner(payload: dict[str, object], runner: str) -> None:
    planner_case = _case_payload(payload, "planner_recommends_test_command_then_testrun_passes")
    commands = planner_case["records"][0]["fields"]["commands"]
    if isinstance(commands[0], dict):
        commands[0].update({"runner": runner})
        return
    commands[0] = {
        "kind": "test",
        "command": "python -m pytest",
        "cwd": ".",
        "runner": runner,
        "confidence": "high",
        "reason": "Python project metadata references pytest",
    }


def test_coding_verification_evidence_fixture_covers_gitdiff_testrun_and_claim_gates() -> None:
    fixture = load_coding_verification_evidence_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_coding_verification_evidence_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "coding_verification_evidence_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "post_edit_gitdiff_and_testrun_pass",
        "post_edit_gitdiff_and_diagnostics_pass",
        "missing_gitdiff_blocks_coding_claim",
        "failed_testrun_blocks_completion_claim",
        "stale_testrun_after_last_edit_blocks",
        "audit_only_unverified_claim_does_not_block",
        "child_coding_evidence_scoped_child",
        "stale_reviewer_before_latest_child_mutation_blocks",
        "equal_reviewer_at_latest_child_mutation_blocks",
        "fresh_reviewer_after_latest_child_mutation_passes",
        "equal_then_fresh_reviewer_after_latest_child_mutation_passes",
        "commit_checkpoint_requires_diff_and_tests",
        "planner_recommends_test_command_then_testrun_passes",
    )
    assert projection.by_verdict_state == {
        "pass": 7,
        "block_ready": 5,
        "missing": 1,
    }
    assert projection.by_category == {
        "post_edit_verification_pass": 1,
        "diagnostics_verification_pass": 1,
        "missing_gitdiff": 1,
        "failed_testrun": 1,
        "stale_verification": 1,
        "audit_only_unverified_claim": 1,
        "child_scoped_verification": 1,
        "coding_child_review_freshness": 4,
        "commit_checkpoint_verification": 1,
        "planner_recommended_verification": 1,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    passing = cases["post_edit_gitdiff_and_testrun_pass"]
    assert passing.contract.requirements[0].type == "GitDiff"
    assert passing.contract.requirements[1].type == "TestRun"
    assert passing.expected_ok is True
    assert passing.expected_verdict_state == "pass"
    assert projection.case_snapshots["post_edit_gitdiff_and_testrun_pass"][
        "matchedEvidenceTypes"
    ] == ("GitDiff", "TestRun")
    assert projection.case_snapshots["post_edit_gitdiff_and_testrun_pass"][
        "verificationCommands"
    ] == ("pytest",)
    assert projection.public_previews["post_edit_gitdiff_and_testrun_pass"] == (
        "GitDiff captured src/app.ts; TestRun pytest exited 0"
    )

    diagnostics = cases["post_edit_gitdiff_and_diagnostics_pass"]
    assert diagnostics.contract.requirements[0].type == "GitDiff"
    assert diagnostics.contract.requirements[1].type == "CodeDiagnostics"
    assert diagnostics.expected_ok is True
    diagnostics_snapshot = projection.case_snapshots[
        "post_edit_gitdiff_and_diagnostics_pass"
    ]
    assert diagnostics_snapshot["matchedEvidenceTypes"] == ("GitDiff", "CodeDiagnostics")
    assert diagnostics_snapshot["verificationCommands"] == ()
    assert diagnostics_snapshot["recordedEvidenceTypes"] == ("GitDiff", "CodeDiagnostics")
    diagnostics_record = diagnostics.records[1]
    assert diagnostics_record.source.tool_name == "CodeDiagnostics"
    assert diagnostics_record.source.metadata["evidenceKind"] == "diagnostics"
    assert diagnostics_record.source.metadata["action"] == "diagnostics"
    assert diagnostics_record.fields["checker"] == "typescript"
    assert diagnostics_record.fields["passed"] is True
    assert diagnostics_record.fields["exitCode"] == 0
    assert diagnostics_record.fields["diagnosticCount"] == 0
    assert diagnostics_record.observed_at > diagnostics.last_code_mutation_at
    assert "tests pass" not in diagnostics_snapshot["publicPreview"].lower()
    assert "build passes" not in diagnostics_snapshot["publicPreview"].lower()

    missing_gitdiff = projection.case_snapshots["missing_gitdiff_blocks_coding_claim"]
    assert missing_gitdiff["verdictState"] == "block_ready"
    assert missing_gitdiff["missingRequirementTypes"] == ("GitDiff",)
    assert missing_gitdiff["failureCodes"] == ("EVIDENCE_CONTRACT_MISSING",)
    assert missing_gitdiff["completionClaim"] == "implemented and tests pass"

    failed_test = projection.case_snapshots["failed_testrun_blocks_completion_claim"]
    assert failed_test["verdictState"] == "block_ready"
    assert failed_test["failureCodes"] == ("EVIDENCE_CONTRACT_FIELD_MISMATCH",)
    assert failed_test["verificationCommands"] == ("pytest",)
    assert failed_test["matchedEvidenceTypes"] == ("GitDiff",)

    stale = projection.case_snapshots["stale_testrun_after_last_edit_blocks"]
    assert stale["failureCodes"] == ("EVIDENCE_CONTRACT_STALE",)
    assert stale["lastCodeMutationAt"] == 40
    assert stale["recordedEvidenceTypes"] == ("GitDiff", "TestRun")

    audit_only = projection.case_snapshots["audit_only_unverified_claim_does_not_block"]
    assert audit_only["enforcement"] == "audit"
    assert audit_only["verdictState"] == "missing"
    assert audit_only["authority"] == "audit_only_no_block"

    child = cases["child_coding_evidence_scoped_child"]
    assert child.agent_role == "coding"
    assert child.run_on == "child"
    assert child.spawn_depth == 1
    assert projection.case_snapshots["child_coding_evidence_scoped_child"][
        "scope"
    ] == {
        "agentRole": "coding",
        "runOn": "child",
        "spawnDepth": 1,
    }

    stale_review = projection.case_snapshots[
        "stale_reviewer_before_latest_child_mutation_blocks"
    ]
    assert stale_review["verdictState"] == "block_ready"
    assert stale_review["matchedEvidenceTypes"] == ("GitDiff", "TestRun")
    assert stale_review["failureCodes"] == ("EVIDENCE_CONTRACT_STALE",)
    assert stale_review["recordedEvidenceTypes"] == (
        "GitDiff",
        "TestRun",
        "custom:CodingChildReview",
    )
    assert stale_review["lastCodeMutationAt"] == 40

    equal_review = projection.case_snapshots[
        "equal_reviewer_at_latest_child_mutation_blocks"
    ]
    assert equal_review["verdictState"] == "block_ready"
    assert equal_review["matchedEvidenceTypes"] == ("GitDiff", "TestRun")
    assert equal_review["failureCodes"] == ("EVIDENCE_CONTRACT_STALE",)
    assert equal_review["lastCodeMutationAt"] == 40

    fresh_review = projection.case_snapshots[
        "fresh_reviewer_after_latest_child_mutation_passes"
    ]
    assert fresh_review["verdictState"] == "pass"
    assert fresh_review["matchedEvidenceTypes"] == (
        "GitDiff",
        "TestRun",
        "custom:CodingChildReview",
    )
    assert fresh_review["verificationCommands"] == ("pytest",)
    assert projection.public_previews[
        "fresh_reviewer_after_latest_child_mutation_passes"
    ] == "Reviewer SpawnAgent inspected the latest child mutation metadata"

    equal_then_fresh_review = projection.case_snapshots[
        "equal_then_fresh_reviewer_after_latest_child_mutation_passes"
    ]
    assert equal_then_fresh_review["verdictState"] == "pass"
    assert equal_then_fresh_review["matchedEvidenceTypes"] == (
        "GitDiff",
        "TestRun",
        "custom:CodingChildReview",
    )
    assert equal_then_fresh_review["failureCodes"] == ()
    assert projection.public_previews[
        "equal_then_fresh_reviewer_after_latest_child_mutation_passes"
    ] == "Later reviewer SpawnAgent inspected the latest child mutation metadata"

    checkpoint = projection.case_snapshots["commit_checkpoint_requires_diff_and_tests"]
    assert checkpoint["matchedEvidenceTypes"] == ("GitDiff", "TestRun", "CommitCheckpoint")
    assert checkpoint["verificationCommands"] == ("npm run lint",)
    assert checkpoint["verdictState"] == "pass"

    planner = projection.case_snapshots[
        "planner_recommends_test_command_then_testrun_passes"
    ]
    assert planner["matchedEvidenceTypes"] == (
        "custom:ProjectVerificationPlanner",
        "GitDiff",
        "TestRun",
    )
    assert planner["recordedEvidenceTypes"] == (
        "custom:ProjectVerificationPlanner",
        "GitDiff",
        "TestRun",
    )
    planner_record = cases["planner_recommends_test_command_then_testrun_passes"].records[0]
    planner_commands = planner_record.fields["commands"]
    assert isinstance(planner_commands, tuple)
    assert planner_commands[0] == {
        "kind": "test",
        "command": "python -m pytest",
        "cwd": ".",
        "runner": "TestRun",
        "confidence": "high",
        "reason": "Python project metadata references pytest",
    }
    assert planner_commands[1] == {
        "kind": "compile",
        "command": "python -m compileall .",
        "cwd": ".",
        "runner": "TestRun",
        "confidence": "medium",
        "reason": "Python project metadata exists",
    }
    assert planner["verificationCommands"] == ("python -m pytest",)
    assert planner["plannerRecommendedCommands"] == (
        "python -m pytest",
        "python -m compileall .",
    )
    assert projection.public_previews[
        "planner_recommends_test_command_then_testrun_passes"
    ] == (
        "ProjectVerificationPlanner recommended python -m pytest and "
        "python -m compileall .; TestRun passed"
    )

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "Bearer unsafe",
        "ghp_codingsecret",
        "sk-coding-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        "raw test output",
        "rawOutput",
        "adkRunnerInvoked\": true",
        "gitExecuted\": true",
        "testExecuted\": true",
        "liveToolDispatched\": true",
        "evidenceBlockEnabled\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update({"gitExecuted": True}),
            id="fixture-git-executed-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"adkRunnerInvoked": True}
            ),
            id="case-runner-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0].update(
                {"type": "FileEdit"}
            ),
            id="non-evidence-type",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0].update(
                {"preview": "/data/bots/bot-secret/workspace/src/app.ts"}
            ),
            id="unsafe-production-path-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][1]["fields"].update(
                {"exitCode": 1}
            ),
            id="unexpected-test-exit-code",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "child_coding_evidence_scoped_child",
            ).update({"runOn": "main"}),
            id="child-scope-runon-mismatch",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"adkRunnerInvoked": True}
            ),
            id="nested-camelcase-live-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"agentMemoryImported": True}
            ),
            id="nested-agentmemory-import-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"hipocampusQmdLiveCalled": True}
            ),
            id="nested-hipocampus-qmd-live-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"toolHostDispatched": True}
            ),
            id="nested-toolhost-dispatch-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["records"][0]["source"]["metadata"].update(
                {"codeExecuted": True}
            ),
            id="nested-code-executed-flag",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "child_coding_evidence_scoped_child",
            )["records"][0]["source"].update({"kind": "tool_trace"}),
            id="child-record-tool-trace-source",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "child_coding_evidence_scoped_child",
            )["records"][0]["source"]["metadata"].update({"executionBoundary": "main"}),
            id="child-record-main-boundary",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "missing_gitdiff_blocks_coding_claim",
            ).update({"authority": "audit_only_no_block"}),
            id="blocking-case-audit-only-authority",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "planner_recommends_test_command_then_testrun_passes",
            )["records"][0]["source"]["metadata"].update({"evidenceKind": "diagnostics"}),
            id="planner-evidence-kind-mismatch",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "planner_recommends_test_command_then_testrun_passes",
            )["records"][0]["source"].update({"toolName": "TestRun"}),
            id="planner-tool-name-mismatch",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "planner_recommends_test_command_then_testrun_passes",
            )["records"][0]["fields"].update({"commands": ["python -m pytest"]}),
            id="planner-flat-string-command",
        ),
        pytest.param(
            lambda payload: _set_first_planner_command_runner(payload, "Bash"),
            id="planner-command-runner-mismatch",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "planner_recommends_test_command_then_testrun_passes",
            )["records"][2]["fields"].update({"command": "python -m unittest"}),
            id="planner-command-does-not-feed-testrun",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "post_edit_gitdiff_and_diagnostics_pass",
            )["records"][1]["source"].update({"toolName": "Diagnostics"}),
            id="diagnostics-tool-name-alias-drift",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "post_edit_gitdiff_and_diagnostics_pass",
            )["records"][1]["source"]["metadata"].update({"evidenceKind": "lint"}),
            id="diagnostics-evidence-kind-mismatch",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "post_edit_gitdiff_and_diagnostics_pass",
            )["records"][1]["fields"].update({"checker": "rust"}),
            id="diagnostics-checker-mismatch",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "post_edit_gitdiff_and_diagnostics_pass",
            )["records"][1]["fields"].update({"diagnosticCount": 1}),
            id="diagnostics-nonzero-count",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "post_edit_gitdiff_and_diagnostics_pass",
            )["records"][1]["fields"].update({"rawOutput": "diagnostic output"}),
            id="diagnostics-raw-output-field",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "post_edit_gitdiff_and_diagnostics_pass",
            )["records"][1]["metadata"].update(
                {"diagnostics": {"rawOutput": "diagnostic output"}}
            ),
            id="diagnostics-raw-output-record-metadata",
        ),
        pytest.param(
            lambda payload: _case_payload(
                payload,
                "post_edit_gitdiff_and_diagnostics_pass",
            )["records"][1]["source"]["metadata"].update(
                {"diagnostics": {"stdout": "diagnostic output"}}
            ),
            id="diagnostics-raw-output-source-metadata",
        ),
    ),
)
def test_coding_verification_evidence_fixture_rejects_live_flags_and_bad_contracts(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        CodingVerificationEvidenceFixture.model_validate(payload)


def test_coding_verification_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = CodingVerificationAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        gitExecuted=True,
        testExecuted=True,
        evidenceBlockEnabled=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"testExecuted": True})


def test_coding_verification_evidence_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from magi_agent.shadow.coding_verification_evidence_contract import (
    load_coding_verification_evidence_fixture,
    project_coding_verification_evidence_fixture,
)

fixture_root = Path('tests/fixtures/coding_verification_evidence')
fixture = load_coding_verification_evidence_fixture('policy_matrix.json', fixture_root=fixture_root)
project_coding_verification_evidence_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'magi_agent.adk_bridge.local_runner',
    'magi_agent.adk_bridge.runner_adapter',
    'magi_agent.adk_bridge.tool_adapter',
    'magi_agent.tools.dispatcher',
    'magi_agent.tools.registry',
    'magi_agent.plugins.agentmemory',
    'magi_agent.memory',
    'magi_agent.services.memory',
    'magi_agent.hipocampus',
    'magi_agent.qmd',
    'magi_agent.app',
    'magi_agent.transport.chat',
    'magi_agent.routes',
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f'forbidden modules loaded: {loaded}')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
