from __future__ import annotations

import json
from pathlib import Path

import pytest

from openmagi_core_agent.evidence.coding_verification import (
    CodingVerificationAuditRequest,
    evaluate_coding_verification_audit,
)
from openmagi_core_agent.evidence.types import EvidenceRecord, EvidenceSource
from openmagi_core_agent.recipes.coding_evidence_gate import (
    CodingEvidenceGate,
    CodingEvidenceGateConfig,
    CodingEvidenceGateHarnessBinding,
    CodingEvidenceGateRequest,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PYTHON_ROOT / "tests/fixtures/parity/coding_harness_consolidated_matrix.json"


def _record(
    evidence_type: str,
    *,
    observed_at: int | float = 20,
    status: str = "ok",
    fields: dict[str, object] | None = None,
    preview: str | None = None,
    source_kind: str = "tool_trace",
    metadata: dict[str, object] | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        type=evidence_type,
        status=status,
        observedAt=observed_at,
        source=EvidenceSource(kind=source_kind, toolName=evidence_type),
        fields=fields or {},
        preview=preview,
        metadata=metadata or {"publicSafeFields": ("command", "exitCode", "status")},
    )


def _diff(*, observed_at: int | float = 20) -> EvidenceRecord:
    return _record(
        "GitDiff",
        observed_at=observed_at,
        fields={"changedFiles": ("src/app.py",), "status": "changed"},
        preview="diff --git a/src/app.py b/src/app.py",
    )


def _test_run(
    *,
    observed_at: int | float = 20,
    status: str = "ok",
    exit_code: int = 0,
    command: str = "uv run pytest tests/test_app.py -q",
) -> EvidenceRecord:
    return _record(
        "TestRun",
        observed_at=observed_at,
        status=status,
        fields={"command": command, "exitCode": exit_code, "status": "passed"},
        preview="pytest output omitted from public projection",
    )


def _checkpoint(*, observed_at: int | float = 20) -> EvidenceRecord:
    return _record(
        "CommitCheckpoint",
        observed_at=observed_at,
        fields={"checkpointId": "checkpoint:local-001"},
    )


def _delivery(*, observed_at: int | float = 20) -> EvidenceRecord:
    return _record(
        "custom:DeliveryReceipt",
        observed_at=observed_at,
        fields={
            "artifactRef": "artifact:patch-bundle",
            "command": "uv run pytest tests/test_app.py -q",
            "exitCode": 0,
            "status": "delivered",
        },
    )


def _request(
    *records: EvidenceRecord,
    claim_text: str = "Patch apply finished.",
    last_code_mutation_at: int | float = 10,
    require_commit_checkpoint: bool = False,
) -> CodingEvidenceGateRequest:
    return CodingEvidenceGateRequest(
        evidenceRecords=records,
        completionClaimed=True,
        claimText=claim_text,
        claimRef="claim:patch-apply-diff-test",
        lastCodeMutationAt=last_code_mutation_at,
        requireCommitCheckpoint=require_commit_checkpoint,
    )


def _gate(enforcement: str = "local_block") -> CodingEvidenceGate:
    return CodingEvidenceGate(
        CodingEvidenceGateConfig(
            enabled=True,
            localEvaluationEnabled=True,
            enforcement=enforcement,
        )
    )


@pytest.mark.parametrize(
    "claim_text",
    (
        "Patch applied and verified.",
        "Cherry-pick applied and verified.",
        "Apply diff completed with verification.",
    ),
)
def test_patch_apply_and_cherry_pick_claims_require_fresh_diff_and_testrun(
    claim_text: str,
) -> None:
    decision = _gate().evaluate(_request(_diff(), _test_run(), claim_text=claim_text))

    assert decision.status == "passed"
    assert decision.matched_evidence_types == ("GitDiff", "TestRun")
    projection = decision.public_projection()
    assert projection["claimDigest"].startswith("sha256:")
    assert projection["receiptRef"].startswith("coding-evidence-gate-receipt:")
    assert projection["authorityFlags"]["localClaimBlocked"] is False
    assert projection["authorityFlags"]["finalAnswerBlocked"] is False


@pytest.mark.parametrize(
    ("records", "expected_status", "expected_failure_codes", "expected_missing"),
    (
        (
            (_diff(), _test_run(status="failed", exit_code=1)),
            "blocked_local",
            ("EVIDENCE_CONTRACT_FIELD_MISMATCH",),
            (),
        ),
        ((_diff(observed_at=5), _test_run()), "blocked_local", ("EVIDENCE_CONTRACT_STALE",), ()),
        ((_test_run(),), "blocked_local", ("EVIDENCE_CONTRACT_MISSING",), ("GitDiff",)),
        ((_diff(), _test_run(observed_at=5)), "blocked_local", ("EVIDENCE_CONTRACT_STALE",), ()),
        (
            (_diff(), _test_run()),
            "blocked_local",
            ("EVIDENCE_CONTRACT_MISSING",),
            ("CommitCheckpoint",),
        ),
    ),
)
def test_failed_stale_missing_or_pre_apply_evidence_blocks_success_projection(
    records: tuple[EvidenceRecord, ...],
    expected_status: str,
    expected_failure_codes: tuple[str, ...],
    expected_missing: tuple[str, ...],
) -> None:
    require_checkpoint = expected_missing == ("CommitCheckpoint",)

    decision = _gate().evaluate(
        _request(*records, require_commit_checkpoint=require_checkpoint),
    )

    assert decision.status == expected_status
    assert decision.status != "passed"
    assert decision.failure_codes == expected_failure_codes
    assert decision.missing_evidence_types == expected_missing
    assert decision.public_projection()["authorityFlags"]["localClaimBlocked"] is True


def test_testrun_without_command_cannot_satisfy_configured_verification_evidence() -> None:
    decision = _gate().evaluate(
        _request(
            _diff(),
            _record("TestRun", fields={"exitCode": 0, "status": "passed"}),
        )
    )

    assert decision.status == "blocked_local"
    assert decision.matched_evidence_types == ("GitDiff",)
    assert decision.failure_codes == ("EVIDENCE_CONTRACT_FIELD_MISMATCH",)


def test_delivery_evidence_cannot_satisfy_testrun_verification() -> None:
    decision = _gate().evaluate(_request(_diff(), _delivery()))

    assert decision.status == "blocked_local"
    assert decision.missing_evidence_types == ("TestRun",)
    assert decision.matched_evidence_types == ("GitDiff",)
    assert "custom:DeliveryReceipt" not in decision.matched_evidence_types


def test_commit_checkpoint_claims_require_explicit_checkpoint_evidence() -> None:
    missing_checkpoint = _gate().evaluate(
        _request(_diff(), _test_run(), _delivery(), require_commit_checkpoint=True),
    )
    with_checkpoint = _gate().evaluate(
        _request(_diff(), _test_run(), _checkpoint(), require_commit_checkpoint=True),
    )

    assert missing_checkpoint.status == "blocked_local"
    assert missing_checkpoint.missing_evidence_types == ("CommitCheckpoint",)
    assert with_checkpoint.status == "passed"
    assert with_checkpoint.matched_evidence_types == ("GitDiff", "TestRun", "CommitCheckpoint")


def test_audit_path_is_local_only_and_does_not_execute_vcs_or_verification_commands() -> None:
    result = evaluate_coding_verification_audit(
        CodingVerificationAuditRequest(
            evidenceRecords=(_diff(), _test_run()),
            lastCodeMutationAt=10,
            requireCommitCheckpoint=False,
        )
    )

    assert result.verdict.ok is True
    assert result.audit_only is True
    assert result.block_mode_enabled is False
    assert result.final_answer_blocked is False
    assert result.traffic_attached is False
    assert result.execution_attached is False
    assert result.runner_attached is False
    assert result.route_attached is False
    assert result.canary_attached is False
    flags = result.attachment_flags.model_dump(by_alias=True)
    assert flags["vcsExecuted"] is False
    assert flags["verificationCommandExecuted"] is False
    assert flags["shellOrCodeExecuted"] is False
    assert flags["liveToolDispatched"] is False
    assert flags["adkRunnerInvoked"] is False


def test_public_projection_excludes_raw_diff_source_test_output_private_paths_and_secrets() -> None:
    decision = _gate("audit").evaluate(
        _request(
            _record(
                "GitDiff",
                fields={
                    "changedFiles": ("/Users/kevin/private/app.py",),
                    "rawDiff": "diff --git token=sk-test-secret",
                },
                preview="diff --git a/private/app.py b/private/app.py token=sk-test-secret",
            ),
            _record(
                "TestRun",
                fields={
                    "command": "uv run pytest tests/test_app.py -q",
                    "exitCode": 0,
                    "rawOutput": "failed output with ghp_private_secret",
                },
                preview="raw pytest output ghp_private_secret",
            ),
            claim_text="Done: /Users/kevin/private/app.py sk-test-secret ghp_private_secret",
        ),
    )

    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert decision.status == "passed"
    assert set(projection) == {
        "status",
        "reasonCodes",
        "claimRef",
        "claimDigest",
        "receiptRef",
        "verdictState",
        "verifierStatus",
        "requiredEvidenceTypeCount",
        "matchedEvidenceTypeCount",
        "missingEvidenceTypeCount",
        "failureCount",
        "authorityFlags",
    }
    assert projection["requiredEvidenceTypeCount"] == 2
    assert projection["matchedEvidenceTypeCount"] == 2
    assert projection["missingEvidenceTypeCount"] == 0
    assert projection["failureCount"] == 0
    assert "/Users/kevin" not in rendered
    assert "private/app.py" not in rendered
    assert "sk-test-secret" not in rendered
    assert "ghp_private_secret" not in rendered
    assert "diff --git" not in rendered
    assert "raw pytest output" not in rendered
    assert "rawOutput" not in rendered
    assert "rawDiff" not in rendered


def test_projection_keeps_explicit_v1_compatibility_without_defaulting_to_arrays() -> None:
    decision = _gate().evaluate(_request(_diff(), _test_run()))

    default_projection = decision.public_projection()
    compat_projection = decision.public_projection(schema_version="v1")

    assert "requiredEvidenceTypes" not in default_projection
    assert "failureCodes" not in default_projection
    assert compat_projection["requiredEvidenceTypes"] == ["GitDiff", "TestRun"]
    assert compat_projection["matchedEvidenceTypes"] == ["GitDiff", "TestRun"]
    assert compat_projection["missingEvidenceTypes"] == []
    assert compat_projection["failureCodes"] == []
    assert compat_projection["claimDigest"] == default_projection["claimDigest"]
    assert compat_projection["receiptRef"] == default_projection["receiptRef"]


def test_harness_binding_is_default_off_local_only_and_does_not_attach_live_surfaces() -> None:
    materialized = CodingEvidenceGateHarnessBinding().materialize().public_projection()

    assert materialized["recipeId"] == "openmagi.dev-coding.evidence-gate"
    assert materialized["requiredEvidenceTypes"] == ["GitDiff", "TestRun"]
    assert materialized["optionalEvidenceTypes"] == ["CommitCheckpoint"]
    assert set(materialized["attachmentFlags"].values()) == {False}

    for relative_path in (
        "openmagi_core_agent/evidence/coding_verification.py",
        "openmagi_core_agent/recipes/coding_evidence_gate.py",
    ):
        source = (PYTHON_ROOT / relative_path).read_text(encoding="utf-8")
        for forbidden in (
            "git commit",
            "git push",
            "subprocess",
            "ToolDispatcher",
            "ToolHost",
            "google.adk.runners",
            "mcp",
            "browser",
        ):
            assert forbidden not in source


def test_pr3_matrix_row_is_complete_default_off_and_forbids_live_core_authority() -> None:
    data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in data["rows"]}
    row = rows["patch_apply_diff_test_checkpoint_evidence"]

    assert row["alreadyCovered"] is True
    assert row["missingImplementation"] == ["complete"]
    assert row["defaultOff"] is True
    assert row["liveAuthorityAllowed"] is False
    assert row["coreTouchAllowed"] is False
    assert row["activationGate"] == "PR3-coding-evidence-gate-local-only"
    assert row["coveredByTests"] == [
        "tests/recipes/test_coding_evidence_gate_recipe.py",
        "tests/test_coding_verification_evidence_audit_path.py",
        "tests/test_coding_meta_adapter.py",
        "tests/test_coding_patch_diff_test_checkpoint_evidence.py",
    ]
