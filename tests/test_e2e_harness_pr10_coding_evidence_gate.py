from __future__ import annotations

from openmagi_core_agent.evidence.types import EvidenceRecord, EvidenceSource
from openmagi_core_agent.recipes.coding_evidence_gate import (
    CodingEvidenceGate,
    CodingEvidenceGateConfig,
    CodingEvidenceGateHarnessBinding,
    CodingEvidenceGateRequest,
)


def _record(evidence_type: str, **fields: object) -> EvidenceRecord:
    return EvidenceRecord(
        type=evidence_type,
        status="ok",
        observedAt=20,
        source=EvidenceSource(kind="tool_trace", toolName=evidence_type),
        fields=fields,
        preview=f"{evidence_type} observed",
        metadata={"publicSafeFields": tuple(fields)},
    )


def test_pr10_e2e_coding_completion_claim_gate_uses_recipe_owned_policy() -> None:
    binding = CodingEvidenceGateHarnessBinding(
        CodingEvidenceGateConfig(
            enabled=True,
            localEvaluationEnabled=True,
            enforcement="local_block",
        )
    )
    materialization = binding.materialize()

    assert materialization.recipe_id == "openmagi.dev-coding.evidence-gate"
    assert materialization.validator_callback_refs == (
        "validator:dev-coding-verification-audit",
        "validator:completion-evidence-local",
    )
    assert materialization.required_evidence_types == ("GitDiff", "TestRun")
    assert set(materialization.attachment_flags.values()) == {False}

    missing_tests = binding.evaluate_completion_claim(
        CodingEvidenceGateRequest(
            completionClaimed=True,
            claimText="The coding task is complete.",
            claimRef="claim:e2e-1",
            lastCodeMutationAt=10,
            evidenceRecords=(_record("GitDiff", changedFiles=("src/app.py",)),),
        )
    )
    passed = binding.evaluate_completion_claim(
        CodingEvidenceGateRequest(
            completionClaimed=True,
            claimText="The coding task is complete.",
            claimRef="claim:e2e-2",
            lastCodeMutationAt=10,
            evidenceRecords=(
                _record("GitDiff", changedFiles=("src/app.py",)),
                _record("TestRun", command="pytest", exitCode=0),
            ),
        )
    )

    assert missing_tests.status == "blocked_local"
    assert missing_tests.missing_evidence_types == ("TestRun",)
    assert passed.status == "passed"
    assert passed.matched_evidence_types == ("GitDiff", "TestRun")
    assert passed.public_projection()["authorityFlags"] == {
        "localEvaluationOnly": True,
        "localClaimBlocked": False,
        "finalAnswerBlocked": False,
        "userVisibleOutputAllowed": False,
        "trafficAttached": False,
        "runnerAttached": False,
        "liveToolAttached": False,
        "productionWriteAllowed": False,
    }


def test_pr10_package_export_is_lazy_but_available_for_recipe_binding() -> None:
    import openmagi_core_agent.recipes as recipes

    assert recipes.CodingEvidenceGate is CodingEvidenceGate
    assert recipes.CodingEvidenceGateHarnessBinding is CodingEvidenceGateHarnessBinding
