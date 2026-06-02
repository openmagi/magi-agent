from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from magi_agent.meta_orchestration.child_acceptance import ChildAcceptanceVerdict
from magi_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    MetaInspectionLoopResult,
    inspect_child_verdicts,
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


def _retry(task_id: str, *, remaining: int = 1) -> MetaInspectedChildVerdict:
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
                retry_budget_remaining=remaining,
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


def test_retry_schedule_is_bounded_and_deterministic() -> None:
    result = inspect_child_verdicts("loop:1", (_accepted("task-a", "ledger:a"), _retry("task-b")))

    assert result.aggregate_status == "needs_retry"
    assert result.retry_schedule_refs == (
        "retry:loop:1:task-b:attempt-1:missing_required_evidence",
    )
    assert result.exhausted_retry_reasons == ()
    assert result.parent_executed_child_tools is False

    with pytest.raises(ValidationError):
        _retry("task-c", remaining=0)
    with pytest.raises(ValidationError):
        _retry("task-d").model_copy(update={"attempt": 10})


def test_rejected_child_result_cannot_be_assembled() -> None:
    result = inspect_child_verdicts("loop:2", (_accepted("task-a", "ledger:a"), _rejected("task-b")))

    assert result.aggregate_status == "blocked"
    assert result.accepted_child_evidence_refs_for_assembly == ("ledger:a",)
    assert result.exhausted_retry_reasons == (
        "exhausted:task-b:missing_required_evidence:retry_budget_exhausted",
    )


def test_direct_loop_result_validation_recomputes_derived_fields() -> None:
    forged = MetaInspectionLoopResult.model_validate(
        {
            "loopId": "loop:forged",
            "childVerdicts": (_accepted("task-a", "ledger:a"), _rejected("task-b")),
            "aggregateStatus": "complete",
            "retryScheduleRefs": ("retry:forged:unsafe",),
            "exhaustedRetryReasons": (),
            "acceptedChildEvidenceRefsForAssembly": ("ledger:partial", "ledger:forged"),
            "parentExecutedChildTools": False,
            "defaultOff": True,
        }
    )

    assert forged.aggregate_status == "blocked"
    assert forged.retry_schedule_refs == ()
    assert forged.exhausted_retry_reasons == (
        "exhausted:task-b:missing_required_evidence:retry_budget_exhausted",
    )
    assert forged.accepted_child_evidence_refs_for_assembly == ("ledger:a",)


def test_parent_cannot_execute_child_tools_directly() -> None:
    with pytest.raises(ValueError):
        inspect_child_verdicts(
            "loop:3",
            (_accepted("task-a", "ledger:a"),),
            parent_executed_child_tools=True,
        )

    with pytest.raises(ValidationError):
        MetaInspectionLoopResult.model_validate(
            {
                "loopId": "loop:3",
                "childVerdicts": (),
                "aggregateStatus": "complete",
                "retryScheduleRefs": (),
                "exhaustedRetryReasons": (),
                "acceptedChildEvidenceRefsForAssembly": (),
                "parentExecutedChildTools": True,
            }
        )


def test_loop_rejects_raw_verdict_dictionaries() -> None:
    raw_retry_verdict = {
        "status": "retry",
        "reasonCodes": ("missing_required_evidence",),
        "acceptedEvidenceRefs": ("ledger:partial",),
        "missingEvidenceRefs": ("audit:missing",),
        "retryable": True,
        "retryBudgetRemaining": 1,
    }

    with pytest.raises(ValidationError):
        MetaInspectedChildVerdict.model_validate(
            {
                "taskId": "task-a",
                "required": True,
                "attempt": 0,
                "verdict": raw_retry_verdict,
            }
        )
    with pytest.raises(ValueError):
        inspect_child_verdicts(
            "loop:raw",
            (
                {
                    "taskId": "task-a",
                    "required": True,
                    "attempt": 0,
                    "verdict": raw_retry_verdict,
                },
            ),
        )


def test_complete_requires_all_required_children_accepted() -> None:
    complete = inspect_child_verdicts(
        "loop:4",
        (_accepted("task-a", "ledger:a"), _accepted("task-b", "ledger:b")),
    )
    partial = inspect_child_verdicts(
        "loop:5",
        (
            _accepted("task-a", "ledger:a"),
            _rejected("task-extra").model_copy(update={"required": False}),
        ),
    )
    blocked = inspect_child_verdicts("loop:6", (_accepted("task-a", "ledger:a"), _blocked("task-b")))

    assert complete.aggregate_status == "complete"
    assert partial.aggregate_status == "partial"
    assert blocked.aggregate_status == "blocked"


def test_loop_public_projection_redacts_raw_child_data() -> None:
    result = inspect_child_verdicts(
        "loop:7",
        (_accepted("task-a", "ledger:a"), _retry("task-b")),
        private_notes=(
            "raw child transcript with Bearer unsafe-token",
            "/workspace/private/source.txt",
            "toolArgs={'authorization':'sk-child-secret'}",
        ),
    )

    projection = result.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    for unsafe in (
        "raw child transcript",
        "Bearer unsafe-token",
        "sk-child-secret",
        "/workspace",
        "toolArgs",
        "ledger:a",
        "audit:missing",
    ):
        assert unsafe not in dumped
    assert projection["aggregateStatus"] == "needs_retry"
    assert projection["acceptedChildEvidenceRefCountForAssembly"] == 1
    assert projection["childVerdicts"] == (
        {
            "taskId": "task-a",
            "status": "accepted",
            "reasonCodes": ("accepted",),
            "acceptedEvidenceRefCount": 1,
            "missingEvidenceRefCount": 0,
            "retryable": False,
            "retryBudgetRemaining": 1,
            "required": True,
        },
        {
            "taskId": "task-b",
            "status": "retry",
            "reasonCodes": ("missing_required_evidence",),
            "acceptedEvidenceRefCount": 1,
            "missingEvidenceRefCount": 1,
            "retryable": True,
            "retryBudgetRemaining": 1,
            "required": True,
        },
    )


def test_child_public_projection_revalidates_mutated_fields() -> None:
    child = _accepted("task-a", "ledger:a")
    object.__setattr__(child, "task_id", "/workspace/private/source.txt")
    object.__setattr__(child, "required", "raw child transcript with sk-child-secret")

    with pytest.raises(ValidationError):
        child.public_projection()


def test_loop_public_projection_revalidates_constructed_results() -> None:
    forged = inspect_child_verdicts(
        "loop:projection",
        (_accepted("task-a", "ledger:a"), _rejected("task-b")),
    )
    object.__setattr__(forged, "aggregate_status", "complete")
    object.__setattr__(forged, "retry_schedule_refs", ("retry:/workspace/private:Bearer-token",))
    object.__setattr__(forged, "exhausted_retry_reasons", ("exhausted:raw child transcript",))
    object.__setattr__(
        forged,
        "accepted_child_evidence_refs_for_assembly",
        ("ledger:partial", "ledger:forged"),
    )
    object.__setattr__(forged, "parent_executed_child_tools", True)
    object.__setattr__(forged, "default_off", False)
    object.__setattr__(forged, "private_notes", ("raw child transcript with sk-child-secret",))

    projection = forged.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert projection["aggregateStatus"] == "blocked"
    assert projection["parentExecutedChildTools"] is False
    assert projection["defaultOff"] is True
    assert projection["acceptedChildEvidenceRefCountForAssembly"] == 1
    assert "Bearer-token" not in dumped
    assert "/workspace" not in dumped
    assert "raw child transcript" not in dumped
    assert "ledger:partial" not in dumped
