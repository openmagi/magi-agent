from __future__ import annotations

import importlib
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.transport.tool_preview import sanitize_tool_preview


def _plan_gate_module():
    return importlib.import_module("magi_agent.harness.plan_gate")


def _build_snapshot(**overrides: object):
    plan_gate = _plan_gate_module()
    payload: dict[str, object] = {
        "decision_id": "pg_decision_1",
        "session_key": "bot:session:1",
        "turn_id": "turn_1",
        "lane": "plan",
        "decision": "needs_interview",
        "reason": "request is missing acceptance criteria",
    }
    payload.update(overrides)
    return plan_gate.build_plan_gate_decision_snapshot(**payload)


def test_snapshot_represents_session_transcript_artifact_and_control_request_impact() -> None:
    plan_gate = _plan_gate_module()
    raw_preview = (
        "Need clarification before implementation. "
        "Authorization: Bearer very-secret-token "
        "token=abc123 "
        + ("x" * 480)
    )

    snapshot = _build_snapshot(
        public_signal_preview=raw_preview,
        artifact_ref="artifact_plan_interview_1",
        artifact_kind="interview",
        control_request_ref=plan_gate.PlanGateControlRequestRef(
            request_id="control_req_1",
            kind="plan_approval",
            state="pending",
            turn_id="turn_1",
        ),
    )

    assert snapshot.session_impact.session_service_owner == "adk-session-service"
    assert snapshot.session_impact.plan_state_owner == "session-service"
    assert snapshot.session_impact.session_write_attached is False

    assert snapshot.transcript_impact.transcript_owner == "openmagi-transcript"
    assert snapshot.transcript_impact.entry_kind == "plan_gate_decision"
    assert snapshot.transcript_impact.lane == "plan"
    assert snapshot.transcript_impact.decision == "needs_interview"
    assert snapshot.transcript_impact.records_lane is True
    assert snapshot.transcript_impact.records_decision is True

    assert snapshot.artifact_impact.artifact_service_owner == "adk-artifact-service"
    assert snapshot.artifact_impact.openmagi_index_owner == "openmagi-artifact-index"
    assert snapshot.artifact_impact.artifact_ref == "artifact_plan_interview_1"
    assert snapshot.artifact_impact.artifact_kind == "interview"
    assert snapshot.artifact_impact.openmagi_index_records_ref is True
    assert snapshot.artifact_impact.artifact_write_attached is False

    assert snapshot.control_request_ref is not None
    assert snapshot.control_request_ref.request_id == "control_req_1"
    assert snapshot.control_request_ref.kind == "plan_approval"
    assert snapshot.control_request_ref.state == "pending"
    assert snapshot.public_signal_preview == sanitize_tool_preview(raw_preview)
    assert "very-secret-token" not in snapshot.public_signal_preview
    assert "abc123" not in snapshot.public_signal_preview
    assert len(snapshot.public_signal_preview) <= 400


@pytest.mark.parametrize("artifact_kind", ("plan", "interview", "consensus"))
def test_artifact_impact_records_plan_interview_and_consensus_refs(artifact_kind: str) -> None:
    snapshot = _build_snapshot(
        artifact_ref=f"artifact_{artifact_kind}_1",
        artifact_kind=artifact_kind,
    )

    dumped = snapshot.model_dump(by_alias=True)
    assert dumped["artifactImpact"]["artifactRef"] == f"artifact_{artifact_kind}_1"
    assert dumped["artifactImpact"]["artifactKind"] == artifact_kind
    assert dumped["artifactImpact"]["openmagiIndexRecordsRef"] is True


def test_artifact_impact_allows_no_artifact_ref_when_not_applicable() -> None:
    snapshot = _build_snapshot()

    dumped = snapshot.model_dump(by_alias=True)
    assert dumped["artifactImpact"]["artifactRef"] is None
    assert dumped["artifactImpact"]["artifactKind"] is None
    assert dumped["artifactImpact"]["openmagiIndexRecordsRef"] is False


def test_camel_case_load_and_dump_aliases_are_compatible() -> None:
    plan_gate = _plan_gate_module()

    snapshot = plan_gate.PlanGateDecisionSnapshot.model_validate(
        {
            "decisionId": "pg_decision_alias",
            "sessionKey": "bot:session:alias",
            "turnId": "turn_alias",
            "lane": "plan",
            "decision": "well_specified",
            "reason": "request includes goal, constraints, and acceptance criteria",
            "publicSignalPreview": "token=abc123",
            "sessionImpact": {
                "sessionServiceOwner": "adk-session-service",
                "planStateOwner": "session-service",
                "storesPlanStateLater": True,
                "sessionWriteAttached": False,
            },
            "transcriptImpact": {
                "transcriptOwner": "openmagi-transcript",
                "entryKind": "plan_gate_decision",
                "lane": "plan",
                "decision": "well_specified",
                "recordsLane": True,
                "recordsDecision": True,
                "transcriptWriteAttached": False,
            },
            "artifactImpact": {
                "artifactServiceOwner": "adk-artifact-service",
                "openmagiIndexOwner": "openmagi-artifact-index",
                "artifactRef": "artifact_consensus_1",
                "artifactKind": "consensus",
                "openmagiIndexRecordsRef": True,
                "artifactWriteAttached": False,
            },
            "controlRequestRef": {
                "requestId": "control_req_alias",
                "kind": "plan_approval",
                "state": "approved",
                "turnId": "turn_alias",
            },
            "routeAttached": False,
            "trafficAttached": False,
            "executionAttached": False,
        }
    )

    dumped = snapshot.model_dump(by_alias=True)
    assert dumped["decisionId"] == "pg_decision_alias"
    assert dumped["sessionKey"] == "bot:session:alias"
    assert dumped["turnId"] == "turn_alias"
    assert dumped["publicSignalPreview"] == "token=[redacted]"
    assert dumped["sessionImpact"]["sessionServiceOwner"] == "adk-session-service"
    assert dumped["transcriptImpact"]["recordsDecision"] is True
    assert dumped["artifactImpact"]["artifactKind"] == "consensus"
    assert dumped["controlRequestRef"]["requestId"] == "control_req_alias"
    assert dumped["routeAttached"] is False
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


@pytest.mark.parametrize("extra_field", ("runnerAttached", "route"))
def test_snapshot_rejects_unexpected_runtime_fields(extra_field: str) -> None:
    plan_gate = _plan_gate_module()
    payload: dict[str, object] = {
        "decisionId": "pg_decision_extra",
        "sessionKey": "bot:session:extra",
        "turnId": "turn_extra",
        "lane": "plan",
        "decision": "well_specified",
        "reason": "no runtime fields should be accepted",
        "sessionImpact": {
            "sessionServiceOwner": "adk-session-service",
            "planStateOwner": "session-service",
            "storesPlanStateLater": True,
            "sessionWriteAttached": False,
        },
        "transcriptImpact": {
            "transcriptOwner": "openmagi-transcript",
            "entryKind": "plan_gate_decision",
            "lane": "plan",
            "decision": "well_specified",
            "recordsLane": True,
            "recordsDecision": True,
            "transcriptWriteAttached": False,
        },
        "artifactImpact": {
            "artifactServiceOwner": "adk-artifact-service",
            "openmagiIndexOwner": "openmagi-artifact-index",
            "artifactRef": None,
            "artifactKind": None,
            "openmagiIndexRecordsRef": False,
            "artifactWriteAttached": False,
        },
        extra_field: False,
    }

    with pytest.raises(ValidationError):
        plan_gate.PlanGateDecisionSnapshot.model_validate(payload)

    with pytest.raises(ValidationError):
        plan_gate.PlanGateControlRequestRef.model_validate(
            {
                "requestId": "control_req_extra",
                "kind": "plan_approval",
                "state": "pending",
                extra_field: False,
            }
        )


@pytest.mark.parametrize("attached_flag", ("routeAttached", "trafficAttached", "executionAttached"))
def test_direct_snapshot_construction_rejects_route_traffic_or_execution_attachment(
    attached_flag: str,
) -> None:
    plan_gate = _plan_gate_module()
    payload: dict[str, object] = {
        "decisionId": "pg_decision_attached",
        "sessionKey": "bot:session:attached",
        "turnId": "turn_attached",
        "lane": "plan",
        "decision": "needs_interview",
        "reason": "attachment flags must stay false",
        "sessionImpact": {
            "sessionServiceOwner": "adk-session-service",
            "planStateOwner": "session-service",
            "storesPlanStateLater": True,
            "sessionWriteAttached": False,
        },
        "transcriptImpact": {
            "transcriptOwner": "openmagi-transcript",
            "entryKind": "plan_gate_decision",
            "lane": "plan",
            "decision": "needs_interview",
            "recordsLane": True,
            "recordsDecision": True,
            "transcriptWriteAttached": False,
        },
        "artifactImpact": {
            "artifactServiceOwner": "adk-artifact-service",
            "openmagiIndexOwner": "openmagi-artifact-index",
            "artifactRef": None,
            "artifactKind": None,
            "openmagiIndexRecordsRef": False,
            "artifactWriteAttached": False,
        },
        attached_flag: True,
    }

    with pytest.raises(ValidationError):
        plan_gate.PlanGateDecisionSnapshot.model_validate(payload)


def test_snapshot_rejects_mismatched_control_request_turn_id() -> None:
    plan_gate = _plan_gate_module()

    with pytest.raises(ValidationError, match="controlRequestRef turnId must match snapshot turnId"):
        _build_snapshot(
            turn_id="turn_snapshot",
            control_request_ref={
                "requestId": "control_req_mismatch",
                "kind": "plan_approval",
                "state": "pending",
                "turnId": "turn_other",
            },
        )

    snapshot = _build_snapshot(
        turn_id="turn_snapshot",
        control_request_ref={
            "requestId": "control_req_unbound",
            "kind": "plan_approval",
            "state": "pending",
        },
    )
    assert snapshot.control_request_ref is not None
    assert snapshot.control_request_ref.turn_id is None


def test_build_snapshot_revalidates_copied_control_request_ref() -> None:
    plan_gate = _plan_gate_module()
    valid_ref = plan_gate.PlanGateControlRequestRef(
        request_id="control_req_copy",
        kind="plan_approval",
        state="pending",
        turn_id="turn_1",
    )
    copied_invalid_ref = valid_ref.model_copy(update={"kind": " "})

    with pytest.raises(ValidationError, match="control request reference"):
        _build_snapshot(control_request_ref=copied_invalid_ref)


def test_plan_gate_contract_import_stays_traffic_free_in_fresh_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.plan_gate")
forbidden_prefixes = (
    "google.adk",
)
forbidden_modules = (
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
)
loaded = [
    module
    for module in sys.modules
    if module.startswith(forbidden_prefixes) or module in forbidden_modules
]
if loaded:
    raise AssertionError(f"plan_gate import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
