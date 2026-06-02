from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openmagi_core_agent.meta_orchestration.child_acceptance import ChildAcceptanceVerdict
from openmagi_core_agent.meta_orchestration.final_assembly import (
    MetaFinalAssemblyPlan,
    assemble_final_output_from_inspection,
)
from openmagi_core_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
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


def _rejected(task_id: str, *partial_refs: str) -> MetaInspectedChildVerdict:
    return MetaInspectedChildVerdict.model_validate(
        {
            "taskId": task_id,
            "required": True,
            "attempt": 1,
            "verdict": ChildAcceptanceVerdict._from_evaluation(
                status="rejected",
                reason_codes=("missing_required_evidence", "retry_budget_exhausted"),
                accepted_evidence_refs=partial_refs,
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


def _complete_inspection() -> object:
    return inspect_child_verdicts(
        "loop:assembly",
        (
            _accepted("task-a", "ledger:a", "receipt:a"),
            _accepted("task-b", "ledger:b"),
        ),
    )


def test_child_text_without_accepted_evidence_cannot_enter_assembly() -> None:
    with pytest.raises(TypeError):
        MetaFinalAssemblyPlan.model_validate(
            {
                "assemblyId": "assembly:raw-text",
                "acceptedChildEvidenceRefs": (),
                "excludedChildRefs": (),
                "requiredVerifierRefs": (),
                "finalOutputDigest": "summary:child says tests passed",
                "projectionMode": "ready_for_projection",
                "rawChildTranscriptUsed": True,
            }
        )
    with pytest.raises(TypeError):
        MetaFinalAssemblyPlan.model_validate(
            {
                "assemblyId": "assembly:forged",
                "acceptedChildEvidenceRefs": ("ledger:forged",),
                "excludedChildRefs": (),
                "requiredVerifierRefs": (),
                "finalOutputDigest": "sha256:" + "a" * 64,
                "projectionMode": "ready_for_projection",
                "rawChildTranscriptUsed": False,
            }
        )
    with pytest.raises(TypeError):
        MetaFinalAssemblyPlan._from_inspection(
            inspection=_complete_inspection(),
            assembly_id="assembly:forged-private",
            accepted_child_evidence_refs=("ledger:forged",),
            excluded_child_refs=(),
            required_verifier_refs=(),
            final_output_digest="sha256:" + "a" * 64,
            projection_mode="ready_for_projection",
        )


def test_blocked_and_rejected_child_refs_are_excluded_from_assembly() -> None:
    inspection = inspect_child_verdicts(
        "loop:blocked",
        (
            _accepted("task-a", "ledger:a"),
            _rejected("task-b", "ledger:partial"),
            _blocked("task-c"),
        ),
    )
    plan = assemble_final_output_from_inspection(
        "assembly:blocked",
        inspection,
        required_verifier_refs=("verifier:meta-before-commit",),
    )

    assert plan.projection_mode == "blocked"
    assert plan.accepted_child_evidence_refs == ("ledger:a",)
    assert plan.excluded_child_refs == ("task-b", "task-c")
    assert "ledger:partial" not in plan.accepted_child_evidence_refs


def test_mutated_inspection_cannot_smuggle_rejected_evidence_into_assembly() -> None:
    inspection = inspect_child_verdicts(
        "loop:mutated",
        (
            _accepted("task-a", "ledger:a"),
            _rejected("task-b", "ledger:partial"),
        ),
    )
    object.__setattr__(
        inspection,
        "accepted_child_evidence_refs_for_assembly",
        ("ledger:a", "ledger:partial"),
    )
    object.__setattr__(inspection, "aggregate_status", "complete")

    plan = assemble_final_output_from_inspection(
        "assembly:mutated-inspection",
        inspection,
        required_verifier_refs=(),
    )

    assert plan.projection_mode == "blocked"
    assert plan.accepted_child_evidence_refs == ("ledger:a",)
    assert plan.excluded_child_refs == ("task-b",)


def test_missing_required_verifier_keeps_projection_blocked() -> None:
    plan = assemble_final_output_from_inspection(
        "assembly:verifier",
        _complete_inspection(),
        required_verifier_refs=("verifier:meta-before-commit",),
        satisfied_verifier_refs=(),
    )
    ready = assemble_final_output_from_inspection(
        "assembly:verifier-ready",
        _complete_inspection(),
        required_verifier_refs=("verifier:meta-before-commit",),
        satisfied_verifier_refs=("verifier:meta-before-commit",),
    )

    assert plan.projection_mode == "blocked"
    assert ready.projection_mode == "ready_for_projection"


def test_accepted_evidence_refs_produce_stable_digest() -> None:
    plan_a = assemble_final_output_from_inspection(
        "assembly:stable",
        _complete_inspection(),
        required_verifier_refs=(),
    )
    plan_b = assemble_final_output_from_inspection(
        "assembly:stable",
        _complete_inspection(),
        required_verifier_refs=(),
    )

    assert plan_a.final_output_digest == plan_b.final_output_digest
    assert plan_a.final_output_digest.startswith("sha256:")
    assert plan_a.public_projection()["acceptedChildEvidenceRefCount"] == 3


def test_raw_transcript_and_private_values_are_rejected_or_redacted() -> None:
    with pytest.raises(TypeError):
        MetaFinalAssemblyPlan.model_validate(
            {
                "assemblyId": "assembly:private",
                "acceptedChildEvidenceRefs": ("ledger:a",),
                "excludedChildRefs": (),
                "requiredVerifierRefs": (),
                "finalOutputDigest": "sha256:" + "a" * 64,
                "projectionMode": "ready_for_projection",
                "rawChildTranscriptUsed": False,
                "privateNotes": ("raw child transcript with Bearer unsafe-token",),
            }
        )

    plan = assemble_final_output_from_inspection(
        "assembly:redacted",
        _complete_inspection(),
        required_verifier_refs=("verifier:meta-before-commit",),
        private_notes=("private-note-redacted",),
    )
    dumped = json.dumps(plan.public_projection(), sort_keys=True)

    for unsafe in (
        "raw child transcript",
        "sk-child-secret",
        "/workspace",
        "ledger:a",
        "receipt:a",
    ):
        assert unsafe not in dumped
    assert plan.raw_child_transcript_used is False


def test_public_projection_revalidates_mutated_plan_fields() -> None:
    plan = assemble_final_output_from_inspection(
        "assembly:mutated",
        _complete_inspection(),
        required_verifier_refs=(),
    )
    object.__setattr__(plan, "final_output_digest", "raw child transcript")
    object.__setattr__(plan, "excluded_child_refs", ("/workspace/private/source.txt",))
    object.__setattr__(plan, "default_off", False)

    with pytest.raises(ValueError):
        plan.public_projection()


def test_public_projection_rejects_valid_looking_mutated_plan_fields() -> None:
    plan = assemble_final_output_from_inspection(
        "assembly:valid-looking-mutation",
        _complete_inspection(),
        required_verifier_refs=(),
    )
    object.__setattr__(plan, "accepted_child_evidence_refs", ("ledger:forged",))
    object.__setattr__(plan, "final_output_digest", "sha256:" + "b" * 64)

    with pytest.raises(ValueError):
        plan.public_projection()
