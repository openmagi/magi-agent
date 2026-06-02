from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from magi_agent.evidence.coding_verification import (
    CodingVerificationAuditRequest,
    evaluate_coding_verification_audit,
)
from magi_agent.evidence.reports import public_evidence_record_report
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource


def _record(
    evidence_type: str,
    *,
    observed_at: int | float = 20,
    status: str = "ok",
    fields: dict[str, object] | None = None,
    preview: str | None = None,
    source_kind: str = "tool_trace",
) -> EvidenceRecord:
    return EvidenceRecord(
        type=evidence_type,
        status=status,
        observedAt=observed_at,
        source=EvidenceSource(kind=source_kind, toolName=evidence_type),
        fields=fields or {},
        preview=preview,
        metadata={"publicSafeFields": ("command", "exitCode", "status")},
    )


def _request(
    *records: EvidenceRecord,
    last_code_mutation_at: int | float = 10,
    require_commit_checkpoint: bool = False,
) -> CodingVerificationAuditRequest:
    return CodingVerificationAuditRequest(
        evidenceRecords=records,
        lastCodeMutationAt=last_code_mutation_at,
        requireCommitCheckpoint=require_commit_checkpoint,
    )


def test_gitdiff_and_successful_testrun_attach_audit_verifier_result_metadata() -> None:
    result = evaluate_coding_verification_audit(
        _request(
            _record(
                "GitDiff",
                fields={"changedFiles": ("src/app.py",), "status": "changed"},
                preview="diff -- src/app.py token=sk-test-secret",
            ),
            _record("TestRun", fields={"command": "pytest", "exitCode": 0, "status": "passed"}),
        )
    )

    assert result.verdict.ok is True
    assert result.verdict.state == "pass"
    assert result.verdict.enforcement == "audit"
    assert result.verifier_result.status == "pass"
    assert result.verifier_result.metadata_only is True
    assert result.audit_evidence.type == "DeterministicEvidenceVerifier"
    assert result.audit_evidence.status == "ok"
    assert result.audit_evidence.source.kind == "verifier"
    assert result.audit_evidence.source.verifier_name == "dev-coding-verification-audit"
    assert result.audit_evidence.fields["verdictState"] == "pass"
    assert result.audit_evidence.fields["matchedEvidenceTypes"] == ("GitDiff", "TestRun")
    assert result.block_mode_enabled is False
    assert result.final_answer_blocked is False
    assert set(result.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_missing_gitdiff_audit_fails_without_block_mode() -> None:
    result = evaluate_coding_verification_audit(
        _request(_record("TestRun", fields={"command": "pytest", "exitCode": 0}))
    )

    assert result.verdict.ok is False
    assert result.verdict.state == "missing"
    assert result.verifier_result.status == "missing"
    assert tuple(req.type for req in result.verdict.missing_requirements) == ("GitDiff",)
    assert tuple(failure.code for failure in result.verdict.failures) == (
        "EVIDENCE_CONTRACT_MISSING",
    )
    assert result.block_mode_enabled is False
    assert result.final_answer_blocked is False


def test_failed_testrun_audit_fails_without_block_mode() -> None:
    result = evaluate_coding_verification_audit(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", status="failed", fields={"command": "pytest", "exitCode": 1}),
        )
    )

    assert result.verdict.ok is False
    assert result.verdict.state == "failed"
    assert result.verifier_result.status == "failed"
    assert tuple(record.type for record in result.verdict.matched_evidence) == ("GitDiff",)
    assert tuple(failure.code for failure in result.verdict.failures) == (
        "EVIDENCE_CONTRACT_FIELD_MISMATCH",
    )
    assert result.block_mode_enabled is False
    assert result.final_answer_blocked is False


def test_stale_testrun_before_last_code_mutation_is_stale_failure() -> None:
    result = evaluate_coding_verification_audit(
        _request(
            _record("GitDiff", observed_at=45, fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", observed_at=30, fields={"command": "pytest", "exitCode": 0}),
            last_code_mutation_at=40,
        )
    )

    assert result.verdict.ok is False
    assert result.verdict.state == "failed"
    assert result.verifier_result.status == "failed"
    assert tuple(failure.code for failure in result.verdict.failures) == (
        "EVIDENCE_CONTRACT_STALE",
    )
    assert result.verdict.failures[0].metadata["boundary"] == "last_code_mutation"
    assert result.block_mode_enabled is False


def test_optional_commit_checkpoint_requirement_is_evaluated_when_requested() -> None:
    without_checkpoint = evaluate_coding_verification_audit(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", fields={"command": "pytest", "exitCode": 0}),
            require_commit_checkpoint=True,
        )
    )
    with_checkpoint = evaluate_coding_verification_audit(
        _request(
            _record("GitDiff", fields={"changedFiles": ("src/app.py",)}),
            _record("TestRun", fields={"command": "pytest", "exitCode": 0}),
            _record("CommitCheckpoint", fields={"checkpointId": "commit-001"}),
            require_commit_checkpoint=True,
        )
    )

    assert without_checkpoint.verdict.ok is False
    assert without_checkpoint.verdict.state == "missing"
    assert tuple(req.type for req in without_checkpoint.verdict.missing_requirements) == (
        "CommitCheckpoint",
    )
    assert with_checkpoint.verdict.ok is True
    assert tuple(record.type for record in with_checkpoint.verdict.matched_evidence) == (
        "GitDiff",
        "TestRun",
        "CommitCheckpoint",
    )


def test_public_projection_redacts_paths_secrets_and_raw_output() -> None:
    result = evaluate_coding_verification_audit(
        _request(
            _record(
                "GitDiff",
                fields={
                    "changedFiles": ("/workspace/src/app.py",),
                    "secretToken": "sk-test-secret",
                    "status": "changed",
                },
                preview="diff -- /workspace/src/app.py\nAuthorization: Bearer unsafe",
            ),
            _record(
                "TestRun",
                fields={
                    "command": "pytest",
                    "exitCode": 0,
                    "rawOutput": "raw test output with token=ghp_codingsecret",
                },
                preview="raw test output token=ghp_codingsecret",
            ),
        )
    )

    public_record = public_evidence_record_report(result.audit_evidence)
    rendered = json.dumps(public_record.model_dump(by_alias=True), sort_keys=True)

    assert "/workspace" not in rendered
    assert "sk-test-secret" not in rendered
    assert "ghp_codingsecret" not in rendered
    assert "raw test output" not in rendered
    assert public_record.fields["verdictState"] == "pass"
    assert public_record.fields["matchedEvidenceTypes"] == ["GitDiff", "TestRun"]


def test_coding_verification_audit_import_boundary_has_no_runtime_execution_surfaces() -> None:
    source = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "evidence"
        / "coding_verification.py"
    ).read_text(encoding="utf-8")
    forbidden_source_tokens = (
        "subprocess",
        "os.system",
        "asyncio.create_subprocess",
        "ToolDispatcher",
        "ToolHost",
        "git ",
        "pytest ",
        "npm test",
        "npm run lint",
    )
    for token in forbidden_source_tokens:
        assert token not in source

    code = """
import importlib
import sys

module = importlib.import_module("magi_agent.evidence.coding_verification")
assert hasattr(module, "evaluate_coding_verification_audit")

forbidden_prefixes = (
    "subprocess",
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.runtime",
    "magi_agent.routing",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"coding verification audit import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
