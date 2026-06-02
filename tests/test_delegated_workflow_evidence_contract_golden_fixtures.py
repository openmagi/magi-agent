from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.delegated_workflow_evidence_contract import (
    DelegatedWorkflowAttachmentFlags,
    DelegatedWorkflowEvidenceFixture,
    load_delegated_workflow_evidence_fixture,
    project_delegated_workflow_evidence_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "delegated_workflow_evidence"


def test_delegated_workflow_evidence_fixture_covers_child_scope_and_parent_aggregation() -> None:
    fixture = load_delegated_workflow_evidence_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_delegated_workflow_evidence_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "delegated_workflow_evidence_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "research_child_source_evidence_pass",
        "coding_child_verification_evidence_pass",
        "child_blocking_failure_propagates",
        "natural_language_child_summary_rejected",
        "task_local_policy_snapshot_compatible",
        "aggregate_required_rejects_child_only_evidence",
    )
    assert projection.by_category == {
        "delegated_research_child_source_pass": 1,
        "delegated_coding_child_verification_pass": 1,
        "child_blocking_failure_propagates": 1,
        "natural_language_summary_rejected": 1,
        "task_local_policy_snapshot_compatible": 1,
        "aggregate_required_parent_scope": 1,
    }
    assert projection.by_parent_state == {
        "pass": 5,
        "blocked": 1,
    }
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_execution is True

    research = projection.case_snapshots["research_child_source_evidence_pass"]
    assert research["parentScope"] == {
        "agentRole": "research",
        "runOn": "main",
        "spawnDepth": 0,
    }
    assert research["childScopes"] == (
        {
            "agentRole": "research",
            "runOn": "child",
            "spawnDepth": 1,
            "taskId": "research-task-1",
        },
    )
    assert research["propagatedEvidenceTypes"] == ("WebSearch", "SourceInspection")
    assert research["requirementMatches"]["SourceInspection:delegated_required"][
        "satisfied"
    ] is True
    assert research["naturalLanguageSummaryAcceptedAsEvidence"] is False

    coding = projection.case_snapshots["coding_child_verification_evidence_pass"]
    assert coding["propagatedEvidenceTypes"] == ("GitDiff", "TestRun")
    assert coding["requirementMatches"]["GitDiff:delegated_required"]["satisfied"] is True
    assert coding["requirementMatches"]["GitDiff:delegated_allowed"][
        "satisfied"
    ] is True
    assert coding["requirementMatches"]["GitDiff:delegated_allowed"][
        "matchedEvidenceTypes"
    ] == ("GitDiff",)
    assert coding["requirementMatches"]["TestRun:local_only"]["satisfied"] is True
    assert coding["requirementMatches"]["TestRun:local_only"][
        "matchedEvidenceTypes"
    ] == ("TestRun",)
    assert coding["requirementMatches"]["TestRun:delegated_required"]["satisfied"] is True

    blocked = projection.case_snapshots["child_blocking_failure_propagates"]
    assert blocked["parentState"] == "blocked"
    assert blocked["blockingFailureCount"] == 1
    assert blocked["propagatedEvidenceTypes"] == ()
    assert blocked["requirementMatches"]["TestRun:delegated_required"]["satisfied"] is False
    assert "child aggregation state is blocked" in blocked["requirementMatches"][
        "TestRun:delegated_required"
    ]["reason"]

    summary = projection.case_snapshots["natural_language_child_summary_rejected"]
    assert summary["naturalLanguageSummaryAcceptedAsEvidence"] is False
    assert summary["naturalLanguageRejectionReason"] == (
        "Natural-language subagent summaries are never evidence."
    )
    assert cases["natural_language_child_summary_rejected"].summary_claimed_as_evidence is False

    compatible = projection.case_snapshots["task_local_policy_snapshot_compatible"]
    assert compatible["compatiblePolicySnapshotIds"] == (
        "research-child-task-local-policy",
    )
    assert compatible["requirementMatches"]["SourceInspection:delegated_required"][
        "satisfied"
    ] is True

    aggregate_required = projection.case_snapshots[
        "aggregate_required_rejects_child_only_evidence"
    ]
    assert aggregate_required["requirementMatches"]["TestRun:aggregate_required"][
        "satisfied"
    ] is False
    assert aggregate_required["requirementMatches"]["TestRun:local_only"][
        "satisfied"
    ] is False
    assert aggregate_required["propagatedEvidenceTypes"] == ("TestRun",)

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "Bearer unsafe",
        "ghp_childsecret",
        "sk-child-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "/data/bots",
        "/workspace",
        "raw child transcript",
        "adkRunnerInvoked\": true",
        "childExecutionAttached\": true",
        "liveToolDispatched\": true",
        "workspaceMutated\": true",
        "evidenceBlockEnabled\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(
                {"childExecutionAttached": True}
            ),
            id="fixture-child-execution-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"adkRunnerInvoked": True}
            ),
            id="case-runner-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0].update(
                {"issuedBy": "child_authored_json"}
            ),
            id="child-authored-json",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["boundary"].update(
                {"runOn": "main", "spawnDepth": 0}
            ),
            id="child-boundary-main",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["boundary"].update(
                {"spawnDepth": 0}
            ),
            id="child-spawn-depth-zero",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["evidenceRecords"][
                0
            ]["source"]["metadata"].update({"executionId": "forged-child"}),
            id="forged-record-execution-id",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["contractVerdicts"][
                0
            ]["matchedEvidence"][0]["source"]["metadata"].update(
                {"taskId": "forged-task"}
            ),
            id="forged-verdict-record-task-id",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0][
                "evidenceRecords"
            ][0].update({"preview": "/data/bots/bot-secret/raw child transcript"}),
            id="unsafe-child-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0][
                "evidenceRecords"
            ][0].update({"preview": "Raw Child Transcript"}),
            id="unsafe-child-preview-case-insensitive",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["evidenceRecords"][
                0
            ]["fields"].update({"Raw Child Transcript": "redacted"}),
            id="unsafe-child-field-key",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["evidenceRecords"][
                0
            ]["fields"].update({"apiKey": "redacted"}),
            id="unsafe-api-key-field-key",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["evidenceRecords"][
                0
            ]["source"]["metadata"].update({"authorization": "redacted"}),
            id="unsafe-authorization-metadata-key",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["evidenceRecords"][
                0
            ]["fields"].update({"status": "sk-live-child-secret"}),
            id="unsafe-secret-shaped-field-value",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["childEnvelopes"][0]["evidenceRecords"][
                0
            ]["source"]["metadata"].update({"privateKey": "redacted"}),
            id="unsafe-private-key-metadata-key",
        ),
        pytest.param(
            lambda payload: payload["cases"][3].update(
                {"summaryClaimedAsEvidence": True}
            ),
            id="summary-claimed-as-evidence",
        ),
        pytest.param(
            lambda payload: payload["cases"][4].update(
                {"compatiblePolicySnapshots": []}
            ),
            id="missing-task-local-policy-compatibility",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["requirementExpectations"][0].update(
                {"expectedSatisfied": False}
            ),
            id="expected-match-mismatch",
        ),
    ),
)
def test_delegated_workflow_evidence_fixture_rejects_bad_scope_or_live_claims(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        DelegatedWorkflowEvidenceFixture.model_validate(payload)


def test_delegated_workflow_attachment_flags_remain_false_under_construct_and_copy() -> None:
    constructed = DelegatedWorkflowAttachmentFlags.model_construct(
        adkRunnerInvoked=True,
        childExecutionAttached=True,
        liveToolDispatched=True,
        workspaceMutated=True,
        evidenceBlockEnabled=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"childExecutionAttached": True})


def test_delegated_workflow_evidence_import_boundary_stays_runtime_free() -> None:
    module_name = "magi_agent.shadow.delegated_workflow_evidence_contract"
    forbidden = (
        "google.adk",
        "magi_agent.adk_bridge",
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
    removed_modules: dict[str, object] = {}
    for loaded_name in tuple(sys.modules):
        if (
            loaded_name == "magi_agent"
            or loaded_name.startswith("magi_agent.")
            or loaded_name == "google.adk"
            or loaded_name.startswith("google.adk.")
        ):
            removed = sys.modules.pop(loaded_name, None)
            if removed is not None:
                removed_modules[loaded_name] = removed

    try:
        module = importlib.import_module(module_name)
        fixture = module.load_delegated_workflow_evidence_fixture(
            "policy_matrix.json",
            fixture_root=FIXTURES,
        )
        module.project_delegated_workflow_evidence_fixture(fixture)

        loaded = [
            loaded_name
            for loaded_name in sorted(sys.modules)
            for forbidden_name in forbidden
            if loaded_name == forbidden_name
            or loaded_name.startswith(f"{forbidden_name}.")
        ]
        assert loaded == []
    finally:
        for loaded_name in tuple(sys.modules):
            if (
                loaded_name == "magi_agent"
                or loaded_name.startswith("magi_agent.")
                or loaded_name == "google.adk"
                or loaded_name.startswith("google.adk.")
            ):
                sys.modules.pop(loaded_name, None)
        sys.modules.update(removed_modules)
