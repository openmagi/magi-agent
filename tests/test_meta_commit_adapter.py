from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from magi_agent.harness.verifier_bus import VerifierResultMetadata
from magi_agent.meta_orchestration.child_acceptance import ChildAcceptanceVerdict
from magi_agent.meta_orchestration.commit_adapter import (
    MetaBeforeCommitVerdict,
    RuntimeIssuedMetaVerifierResult,
    evaluate_before_commit_for_assembly,
    issue_runtime_verifier_result_for_assembly,
)
from magi_agent.meta_orchestration.final_assembly import (
    MetaFinalAssemblyPlan,
    assemble_final_output_from_inspection,
)
from magi_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    inspect_child_verdicts,
)
from magi_agent.runtime import commit_boundary


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


def _ready_assembly(assembly_id: str = "assembly:before-commit") -> MetaFinalAssemblyPlan:
    inspection = inspect_child_verdicts(
        "loop:before-commit",
        (
            _accepted("task-a", "ledger:a", "receipt:a"),
            _accepted("task-b", "ledger:b"),
        ),
    )
    return assemble_final_output_from_inspection(
        assembly_id,
        inspection,
        required_verifier_refs=("verifier:meta-before-commit",),
        satisfied_verifier_refs=("verifier:meta-before-commit",),
    )


def _result(
    verifier_id: str = "verifier:meta-before-commit",
    *,
    status: str = "pass",
    public_summary: str | None = None,
    retry_message: str | None = None,
    failure_message: str | None = None,
) -> VerifierResultMetadata:
    return VerifierResultMetadata(
        verifierId=verifier_id,
        status=status,
        publicSummary=public_summary,
        retryMessage=retry_message,
        failureMessage=failure_message,
    )


def _issued_result(
    assembly: MetaFinalAssemblyPlan,
    verifier_id: str = "verifier:meta-before-commit",
    *,
    status: str = "pass",
    public_summary: str | None = None,
    retry_message: str | None = None,
    failure_message: str | None = None,
) -> RuntimeIssuedMetaVerifierResult:
    return issue_runtime_verifier_result_for_assembly(
        assembly,
        _result(
            verifier_id,
            status=status,
            public_summary=public_summary,
            retry_message=retry_message,
            failure_message=failure_message,
        ),
        verifier_bus_run_id="verifier-run:local",
        policy_snapshot_id="policy:local",
    )


def test_failed_verifier_blocks_final_projection_even_when_assembly_is_ready() -> None:
    assembly = _ready_assembly()
    verdict = evaluate_before_commit_for_assembly(
        "before-commit:failed",
        assembly,
        verifier_results=(
            _issued_result(
                assembly,
                status="failed",
                retry_message="retry after missing evidence",
                failure_message=(
                    "raw child transcript in /workspace/private with token=sk-test-secret"
                ),
            ),
        ),
    )

    assert verdict.final_projection_eligible is False
    assert verdict.commit_executed is False
    assert verdict.default_off is True
    assert verdict.blocked_reasons == ("verifier_failed:verifier:meta-before-commit",)
    assert verdict.retryable_reasons == ("verifier_retryable:verifier:meta-before-commit",)

    dumped = json.dumps(verdict.public_projection(), sort_keys=True)
    for unsafe in (
        "raw child transcript",
        "/workspace/private",
        "sk-test-secret",
        "ledger:a",
        "receipt:a",
    ):
        assert unsafe not in dumped


def test_final_assembly_cannot_bypass_runtime_verifier_results() -> None:
    assembly = _ready_assembly()
    verdict = evaluate_before_commit_for_assembly(
        "before-commit:missing",
        assembly,
        verifier_results=(),
    )

    assert verdict.final_projection_eligible is False
    assert verdict.blocked_reasons == ("verifier_missing:verifier:meta-before-commit",)
    assert verdict.verifier_chain_result == "blocked"

    with pytest.raises(ValueError):
        evaluate_before_commit_for_assembly(
            "before-commit:forged",
            assembly.public_projection(),
            verifier_results=(_issued_result(assembly),),
        )
    with pytest.raises(ValueError):
        evaluate_before_commit_for_assembly(
            "before-commit:raw-result",
            assembly,
            verifier_results=(_result(),),
        )
    with pytest.raises(TypeError):
        RuntimeIssuedMetaVerifierResult.model_validate(
            {
                "result": _result(),
                "assemblyId": assembly.assembly_id,
                "assemblyDigest": assembly.final_output_digest,
                "verifierBusRunId": "verifier-run:forged",
                "policySnapshotId": "policy:forged",
                "issuer": "openmagi-verifier-bus",
                "metadataOnly": True,
                "trafficAttached": False,
                "executionAttached": False,
            }
        )


def test_stale_verifier_result_bound_to_other_assembly_cannot_unblock_projection() -> None:
    current = _ready_assembly("assembly:current")
    stale = _ready_assembly("assembly:stale")
    stale_result = _issued_result(stale)

    with pytest.raises(ValueError):
        evaluate_before_commit_for_assembly(
            "before-commit:stale",
            current,
            verifier_results=(stale_result,),
        )


def test_mutated_runtime_verifier_result_cannot_be_upgraded_after_issuance() -> None:
    assembly = _ready_assembly()
    issued = _issued_result(assembly, status="failed")
    object.__setattr__(issued, "result", _result(status="pass"))

    with pytest.raises(ValueError):
        evaluate_before_commit_for_assembly(
            "before-commit:mutated-wrapper",
            assembly,
            verifier_results=(issued,),
        )


@pytest.mark.parametrize("status", ("missing", "approval_required", "audit"))
def test_non_pass_verifier_statuses_block_projection(status: str) -> None:
    assembly = _ready_assembly()
    verdict = evaluate_before_commit_for_assembly(
        f"before-commit:{status}",
        assembly,
        verifier_results=(_issued_result(assembly, status=status),),
    )

    assert verdict.final_projection_eligible is False
    assert verdict.blocked_reasons == (f"verifier_{status}:verifier:meta-before-commit",)


def test_passed_verifiers_allow_metadata_only_projection_without_commit_writes() -> None:
    assembly = _ready_assembly()
    verdict = evaluate_before_commit_for_assembly(
        "before-commit:pass",
        assembly,
        verifier_results=(_issued_result(assembly, public_summary="verifier passed"),),
    )

    assert verdict.final_projection_eligible is True
    assert verdict.verifier_chain_result == "passed"
    assert verdict.commit_executed is False
    assert verdict.transcript_written is False
    assert verdict.sse_written is False
    assert verdict.control_written is False
    assert verdict.tool_execution_attached is False
    assert verdict.commit_intent_refs == ()
    assert verdict.default_off is True

    projection = verdict.public_projection()
    assert projection["finalProjectionEligible"] is True
    assert projection["verifierResultRefCount"] == 1
    assert "acceptedChildEvidenceRefs" not in projection


def test_public_projection_is_digest_safe_and_excludes_raw_verifier_messages() -> None:
    assembly = _ready_assembly()
    verdict = evaluate_before_commit_for_assembly(
        "before-commit:redacted",
        assembly,
        verifier_results=(
            _issued_result(
                assembly,
                status="failed",
                public_summary=(
                    "raw tool result from /Users/kevin/private with Authorization: Bearer secret"
                ),
                failure_message="source raw text includes Cookie: session=secret",
            ),
        ),
    )

    dumped = json.dumps(verdict.public_projection(), sort_keys=True)
    for unsafe in (
        "raw tool result",
        "/Users/kevin/private",
        "Bearer secret",
        "Cookie: session",
        "source raw text",
    ):
        assert unsafe not in dumped
    assert "sha256:" in dumped


def test_before_commit_verdict_cannot_be_forged_or_runtime_enabled() -> None:
    with pytest.raises(TypeError):
        MetaBeforeCommitVerdict.model_validate(
            {
                "verdictId": "before-commit:forged",
                "assemblyId": "assembly:before-commit",
                "assemblyDigest": "sha256:" + "a" * 64,
                "verifierChainResult": "passed",
                "verifierResultRefs": ("verifier-result:forged",),
                "blockedReasons": (),
                "retryableReasons": (),
                "finalProjectionEligible": True,
                "commitExecuted": False,
                "defaultOff": True,
            }
        )
    with pytest.raises(TypeError):
        MetaBeforeCommitVerdict.model_construct(
            verdictId="before-commit:forged",
            assemblyId="assembly:before-commit",
        )


def test_generic_commit_boundary_remains_domain_neutral() -> None:
    source = inspect.getsource(commit_boundary)
    package_root = Path(commit_boundary.__file__).resolve().parents[1]
    generic_sources = (
        path
        for dirname in ("runtime", "harness", "evidence")
        for path in (package_root / dirname).rglob("*.py")
    )

    assert "meta_orchestration" not in source
    for path in generic_sources:
        module_source = path.read_text()
        assert "magi_agent.meta_orchestration" not in module_source, path
        assert "from magi_agent import meta_orchestration" not in module_source, path
        assert "import magi_agent.meta_orchestration" not in module_source, path
    for forbidden in (
        "research_searcher",
        "source_inspector",
        "claim_mapper",
        "research_verifier",
        "code_editor",
        "test_runner",
        "citation_policy",
    ):
        assert forbidden not in source
