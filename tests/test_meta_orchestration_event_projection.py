from __future__ import annotations

import json

import pytest

from openmagi_core_agent.harness.verifier_bus import VerifierResultMetadata
from openmagi_core_agent.meta_orchestration.child_acceptance import ChildAcceptanceVerdict
from openmagi_core_agent.meta_orchestration.commit_adapter import (
    evaluate_before_commit_for_assembly,
    issue_runtime_verifier_result_for_assembly,
)
from openmagi_core_agent.meta_orchestration.event_projection import (
    project_meta_before_commit_event,
    project_meta_child_task_scheduled_event,
    project_meta_final_assembly_events,
    project_meta_parent_inspection_events,
    project_meta_plan_created_events,
)
from openmagi_core_agent.meta_orchestration.final_assembly import (
    assemble_final_output_from_inspection,
)
from openmagi_core_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    inspect_child_verdicts,
)
from openmagi_core_agent.meta_orchestration.projection import (
    meta_projection_assembly_id_for_inspection,
    meta_projection_loop_id_for_plan,
)
from openmagi_core_agent.meta_orchestration.task_plan import (
    MetaChildTaskSpec,
    MetaTaskPlan,
)
from openmagi_core_agent.transport.sse import InMemorySseWriter


def _child(
    task_id: str,
    *,
    role_ref: str = "role:research_searcher",
    delivery_mode: str = "return",
) -> MetaChildTaskSpec:
    return MetaChildTaskSpec.model_validate(
        {
            "taskId": task_id,
            "roleRef": role_ref,
            "scopeRef": f"scope:{task_id}",
            "allowedToolRefs": ("tool:readonly-evidence",),
            "contextBudget": {
                "maxInputTokens": 1000,
                "maxOutputTokens": 500,
                "reservedEvidenceTokens": 100,
            },
            "completionContractRef": "contract:runtime-evidence-envelope",
            "deliveryMode": delivery_mode,
        }
    )


def _plan(*children: MetaChildTaskSpec) -> MetaTaskPlan:
    return MetaTaskPlan.model_validate(
        {
            "planId": "plan:research_searcher",
            "parentExecutionId": "parent:coding_review",
            "objectiveDigest": "sha256:" + "a" * 64,
            "objectivePreview": "Assemble accepted child evidence behind local gates.",
            "acceptanceCriteriaRefs": ("criteria:accepted-evidence-only",),
            "childTaskSpecs": children
            or (
                _child("task:research_searcher"),
                _child("task:code_editor", role_ref="role:code_editor", delivery_mode="background"),
            ),
            "verifierChainRefs": ("verifier:research_verifier",),
            "maxRetryBudget": 1,
        }
    )


def _accepted(task_id: str, *evidence_refs: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 0,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="accepted",
                reason_codes=("accepted",),
                accepted_evidence_refs=evidence_refs,
                missing_evidence_refs=(),
                retryable=False,
                retry_budget_remaining=1,
            ),
        }
    )


def _retry(task_id: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 0,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="retry",
                reason_codes=("missing_required_evidence",),
                accepted_evidence_refs=("ledger:partial",),
                missing_evidence_refs=("audit:missing",),
                retryable=True,
                retry_budget_remaining=1,
            ),
        }
    )


def _rejected(task_id: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 1,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="rejected",
                reason_codes=("missing_required_evidence", "retry_budget_exhausted"),
                accepted_evidence_refs=("ledger:partial",),
                missing_evidence_refs=("audit:missing",),
                retryable=False,
                retry_budget_remaining=0,
            ),
        }
    )


def _blocked(task_id: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 0,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="blocked",
                reason_codes=("child_blocked",),
                accepted_evidence_refs=(),
                missing_evidence_refs=("audit:child-blocked",),
                retryable=False,
                retry_budget_remaining=0,
            ),
        }
    )


def _inspection(
    plan: MetaTaskPlan,
    *children: MetaInspectedChildVerdict,
    private_notes: tuple[str, ...] = (),
) -> object:
    return inspect_child_verdicts(
        meta_projection_loop_id_for_plan(plan),
        children
        or (
            _accepted("task:research_searcher", "ledger:source", "receipt:source"),
            _accepted("task:code_editor", "ledger:diff"),
        ),
        private_notes=private_notes,
    )


def _assembly(plan: MetaTaskPlan, inspection: object, *, satisfied: bool = True) -> object:
    return assemble_final_output_from_inspection(
        meta_projection_assembly_id_for_inspection(inspection),
        inspection,
        required_verifier_refs=plan.verifier_chain_refs,
        satisfied_verifier_refs=plan.verifier_chain_refs if satisfied else (),
    )


def _before_commit(assembly: object, *, status: str = "pass") -> object:
    verifier_result = issue_runtime_verifier_result_for_assembly(
        assembly,
        VerifierResultMetadata(
            verifierId="verifier:research_verifier",
            status=status,
            publicSummary="raw tool result from /Users/kevin/private with token=sk-test-secret",
            failureMessage="Authorization: Bearer unsafe-token",
        ),
        verifier_bus_run_id="verifier-run:meta",
        policy_snapshot_id="policy:meta",
    )
    return evaluate_before_commit_for_assembly(
        "before-commit:meta",
        assembly,
        verifier_results=(verifier_result,),
    )


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _json_safe(item)
            for key, item in value.items()
            if not str(key).startswith("_openmagi")
        }
    if isinstance(value, tuple):
        return tuple(_json_safe(item) for item in value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _dump(events: object) -> str:
    return json.dumps(_json_safe(events), sort_keys=True)


def _sse_payloads(events: tuple[dict[str, object], ...]) -> list[dict[str, object]]:
    writer = InMemorySseWriter()
    for event in events:
        writer.agent(event)
    return [
        json.loads(line.removeprefix("data: "))
        for line in writer.body.splitlines()
        if line.startswith("data: ")
    ]


def test_plan_created_projects_task_board_and_planning_phase_without_raw_refs() -> None:
    plan = _plan()

    events = project_meta_plan_created_events(plan)

    assert [event["type"] for event in events] == ["turn_phase", "task_board"]
    assert events[0]["phase"] == "planning"
    task_board = events[1]
    assert len(task_board["tasks"]) == 2
    assert {task["status"] for task in task_board["tasks"]} == {"pending"}

    dumped = _dump(events)
    for unsafe in (
        "research_searcher",
        "code_editor",
        "coding_review",
        "role:",
        "scope:",
        "tool:readonly-evidence",
        "verifier:research_verifier",
        "ledger:",
        "receipt:",
    ):
        assert unsafe not in dumped


def test_child_schedule_projection_requires_receipt_and_never_claims_execution() -> None:
    plan = _plan()

    event = project_meta_child_task_scheduled_event(
        plan,
        task_index=1,
        receipt_ref="receipt:sha256:" + "b" * 64,
    )

    assert event["type"] == "spawn_started"
    assert event["persona"] == "meta_child"
    assert event["deliver"] == "background"
    assert event["detail"] == f"scheduled receipt=receipt:sha256:{'b' * 64}"
    assert "task:code_editor" not in _dump(event)
    assert "role:code_editor" not in _dump(event)
    assert "toolCallCount" not in event

    with pytest.raises(ValueError):
        project_meta_child_task_scheduled_event(plan, task_index=0, receipt_ref="ref:meta-schedule-1")


def test_parent_inspection_projects_rule_checks_and_public_safe_retry_trace() -> None:
    plan = _plan()
    inspection = _inspection(
        plan,
        _accepted("task:research_searcher", "ledger:source"),
        _retry("task:code_editor"),
        private_notes=(
            "raw child transcript with hidden reasoning",
            "toolArgs={'authorization':'Bearer unsafe-token'}",
        ),
    )

    events = project_meta_parent_inspection_events(inspection)

    assert events[0]["type"] == "rule_check"
    assert events[0]["verdict"] == "pending"
    retry_events = [event for event in events if event["type"] == "runtime_trace"]
    assert len(retry_events) == 1
    assert retry_events[0]["phase"] == "retry_scheduled"
    assert "missing_required_evidence" in retry_events[0]["detail"]

    sse_payloads = _sse_payloads(events)
    assert [payload["type"] for payload in sse_payloads] == [
        event["type"] for event in events
    ]
    assert not any(
        payload.get("title") == "Public event omitted" for payload in sse_payloads
    )
    for payload in sse_payloads:
        if payload["type"] == "rule_check" and payload["verdict"] != "pending":
            assert str(payload["evidenceRef"]).startswith("sha256:")

    dumped = _dump(events)
    for unsafe in (
        "task:research_searcher",
        "task:code_editor",
        "ledger:source",
        "ledger:partial",
        "audit:missing",
        "raw child transcript",
        "hidden reasoning",
        "toolArgs",
        "Bearer unsafe-token",
    ):
        assert unsafe not in dumped


def test_rejected_child_cannot_project_completed_or_accepted_work() -> None:
    plan = _plan()
    inspection = _inspection(
        plan,
        _accepted("task:research_searcher", "ledger:source"),
        _rejected("task:code_editor"),
    )

    events = project_meta_parent_inspection_events(inspection)

    assert not any(event["type"] == "child_completed" for event in events)
    assert not any(
        event["type"] == "spawn_result" and event.get("status") == "ok"
        for event in events
    )
    assert not any(
        task.get("status") == "completed"
        for event in events
        for task in event.get("tasks", ())
    )
    dumped = _dump(events)
    assert "ledger:partial" not in dumped
    assert "retry_budget_exhausted" in dumped


def test_final_assembly_and_before_commit_project_blocked_without_authority() -> None:
    plan = _plan()
    inspection = _inspection(
        plan,
        _accepted("task:research_searcher", "ledger:source"),
        _blocked("task:code_editor"),
    )
    assembly = _assembly(plan, inspection, satisfied=False)
    before_commit = _before_commit(assembly, status="failed")

    assembly_events = project_meta_final_assembly_events(assembly)
    before_commit_event = project_meta_before_commit_event(before_commit)

    assert assembly_events[0]["type"] == "turn_phase"
    assert assembly_events[0]["phase"] == "aborted"
    assert assembly_events[1]["type"] == "runtime_trace"
    assert assembly_events[1]["turnId"] == assembly_events[0]["turnId"]
    assert "status=blocked" in assembly_events[1]["detail"]
    assert before_commit_event["type"] == "rule_check"
    assert before_commit_event["verdict"] == "violation"

    sse_payloads = _sse_payloads((*assembly_events, before_commit_event))
    assert [payload["type"] for payload in sse_payloads] == [
        "turn_phase",
        "runtime_trace",
        "rule_check",
    ]
    assert sse_payloads[1]["turnId"] == assembly_events[0]["turnId"]
    assert sse_payloads[2]["evidenceRef"].startswith("sha256:")

    dumped = _dump((*assembly_events, before_commit_event))
    for unsafe in (
        "ledger:source",
        "task:code_editor",
        "raw tool result",
        "/Users/kevin/private",
        "sk-test-secret",
        "Authorization",
        "Bearer unsafe-token",
        "commitExecuted=True",
        "toolExecutionAttached=True",
        "sseWritten=True",
    ):
        assert unsafe not in dumped


def test_projectors_reject_raw_mappings_and_public_projection_dicts() -> None:
    plan = _plan()
    inspection = _inspection(plan)
    assembly = _assembly(plan, inspection)
    before_commit = _before_commit(assembly)

    with pytest.raises(ValueError):
        project_meta_plan_created_events(plan.model_dump(by_alias=True))
    with pytest.raises(ValueError):
        project_meta_parent_inspection_events(inspection.public_projection())
    with pytest.raises(ValueError):
        project_meta_final_assembly_events(assembly.public_projection())
    with pytest.raises(ValueError):
        project_meta_before_commit_event(before_commit.public_projection())
