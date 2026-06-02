"""PR5: Diff And Test Evidence Hard Gates.

Tests that:
- "I changed X" requires GitDiff or sandbox diff evidence.
- "Tests passed" requires TestRun(exit_code=0) with matching command digest.
- Failed or stale test evidence cannot prove completion.
- Audit-only mode can report missing evidence but cannot project verified success.
- Public projections use digest-only (no raw paths, contents, auth tokens).
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest

from openmagi_core_agent.evidence.coding_verification import (
    CodingVerificationAuditRequest,
    build_coding_verification_hard_gate_contract,
    evaluate_coding_verification_audit,
    evaluate_coding_verification_hard_gate,
)
from openmagi_core_agent.evidence.contracts import (
    evaluate_evidence_contract,
    evidence_command_digest,
)
from openmagi_core_agent.evidence.types import (
    EvidenceContract,
    EvidenceContractVerdict,
    EvidenceFieldMatcher,
    EvidenceRecord,
    EvidenceRequirement,
    EvidenceSource,
)
from openmagi_core_agent.recipes.coding_evidence_gate import (
    CodingEvidenceGate,
    CodingEvidenceGateConfig,
    CodingEvidenceGateRequest,
)
from openmagi_core_agent.shadow.coding_verification_evidence_contract import (
    CodingVerificationEvidenceCase,
    CodingVerificationEvidenceFixture,
    CodingVerificationEvidenceProjection,
    project_coding_verification_evidence_fixture,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _gate_request(
    *records: EvidenceRecord,
    completion_claimed: bool = True,
    claim_text: str = "I changed X and tests passed",
    last_code_mutation_at: int | float = 10,
) -> CodingEvidenceGateRequest:
    return CodingEvidenceGateRequest(
        evidenceRecords=records,
        completionClaimed=completion_claimed,
        claimText=claim_text,
        lastCodeMutationAt=last_code_mutation_at,
    )


def _command_digest(command: str) -> str:
    return "sha256:" + hashlib.sha256(command.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1. Missing diff evidence — "I changed X" without GitDiff
# ---------------------------------------------------------------------------

class TestMissingDiffEvidence:
    """Claiming code changes without GitDiff evidence must fail."""

    def test_missing_gitdiff_returns_missing_state(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _test_run(),
            )
        )
        assert result.verdict.ok is False
        assert result.verdict.state == "missing"
        assert "GitDiff" in tuple(
            req.type for req in result.verdict.missing_requirements
        )

    def test_missing_gitdiff_failure_code_is_evidence_contract_missing(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _test_run(),
            )
        )
        failure_codes = tuple(f.code for f in result.verdict.failures)
        assert "EVIDENCE_CONTRACT_MISSING" in failure_codes

    def test_missing_gitdiff_in_gate_blocks_completion(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
                enforcement="local_block",
            )
        )
        decision = gate.evaluate(
            _gate_request(_test_run()),
        )
        assert decision.status == "blocked_local"
        assert "GitDiff" in decision.missing_evidence_types

    def test_missing_gitdiff_audit_mode_reports_missing(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
                enforcement="audit",
            )
        )
        decision = gate.evaluate(
            _gate_request(_test_run()),
        )
        assert decision.status == "audit_required"
        assert "GitDiff" in decision.missing_evidence_types


# ---------------------------------------------------------------------------
# 2. Stale diff evidence
# ---------------------------------------------------------------------------

class TestStaleDiffEvidence:
    """GitDiff observed before lastCodeMutation must be rejected."""

    def test_stale_gitdiff_returns_failed(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(observed_at=5),  # before lastCodeMutation=10
                _test_run(),
                last_code_mutation_at=10,
            )
        )
        assert result.verdict.ok is False
        failure_codes = tuple(f.code for f in result.verdict.failures)
        assert "EVIDENCE_CONTRACT_STALE" in failure_codes


# ---------------------------------------------------------------------------
# 3. Failed test evidence
# ---------------------------------------------------------------------------

class TestFailedTestEvidence:
    """TestRun with non-zero exit code must not prove completion."""

    def test_failed_testrun_returns_verdict_not_ok(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(),
                _test_run(exit_code=1, status="failed"),
            )
        )
        assert result.verdict.ok is False

    def test_failed_testrun_failure_code_is_field_mismatch(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(),
                _test_run(exit_code=1, status="failed"),
            )
        )
        failure_codes = tuple(f.code for f in result.verdict.failures)
        assert "EVIDENCE_CONTRACT_FIELD_MISMATCH" in failure_codes

    def test_failed_testrun_gate_blocks(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
                enforcement="local_block",
            )
        )
        decision = gate.evaluate(
            _gate_request(
                _diff(),
                _test_run(exit_code=1, status="failed"),
            ),
        )
        assert decision.status == "blocked_local"
        assert decision.authority_flags.local_claim_blocked is True


# ---------------------------------------------------------------------------
# 4. Stale test evidence
# ---------------------------------------------------------------------------

class TestStaleTestEvidence:
    """TestRun observed before lastCodeMutation must be rejected."""

    def test_stale_testrun_returns_failed(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(),
                _test_run(observed_at=5),  # before lastCodeMutation=10
                last_code_mutation_at=10,
            )
        )
        assert result.verdict.ok is False
        failure_codes = tuple(f.code for f in result.verdict.failures)
        assert "EVIDENCE_CONTRACT_STALE" in failure_codes


# ---------------------------------------------------------------------------
# 5. Valid diff + test — happy path
# ---------------------------------------------------------------------------

class TestValidDiffAndTest:
    """Fresh GitDiff + passing TestRun should pass."""

    def test_fresh_diff_and_passing_test_ok(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(),
                _test_run(),
            )
        )
        assert result.verdict.ok is True
        assert result.verdict.state == "pass"

    def test_matched_evidence_types(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(),
                _test_run(),
            )
        )
        matched = tuple(r.type for r in result.verdict.matched_evidence)
        assert "GitDiff" in matched
        assert "TestRun" in matched

    def test_gate_passes_with_valid_evidence(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
                enforcement="local_block",
            )
        )
        decision = gate.evaluate(
            _gate_request(
                _diff(),
                _test_run(),
            ),
        )
        assert decision.status == "passed"
        assert decision.authority_flags.local_claim_blocked is False


# ---------------------------------------------------------------------------
# 6. TestRun command digest matching
# ---------------------------------------------------------------------------

class TestCommandDigestMatching:
    """TestRun command must match the required commandPattern when specified."""

    def test_testrun_requires_command_field(self) -> None:
        """TestRun without command field should fail."""
        contract = EvidenceContract(
            id="test-command-digest",
            triggers=("beforeCommit",),
            when={"lastCodeMutation": 10},
            requirements=(
                EvidenceRequirement(
                    type="TestRun",
                    after="last_code_mutation",
                    exitCode=0,
                    fields={"command": EvidenceFieldMatcher(exists=True)},
                ),
            ),
            onMissing="audit",
        )
        record_no_cmd = _record(
            "TestRun",
            fields={"exitCode": 0},
        )
        verdict = evaluate_evidence_contract(contract, (record_no_cmd,))
        assert verdict.ok is False

    def test_testrun_with_matching_command_passes(self) -> None:
        """TestRun with command that exists should pass."""
        contract = EvidenceContract(
            id="test-command-digest",
            triggers=("beforeCommit",),
            when={"lastCodeMutation": 10},
            requirements=(
                EvidenceRequirement(
                    type="TestRun",
                    after="last_code_mutation",
                    exitCode=0,
                    fields={"command": EvidenceFieldMatcher(exists=True)},
                ),
            ),
            onMissing="audit",
        )
        record = _test_run()
        verdict = evaluate_evidence_contract(contract, (record,))
        assert verdict.ok is True


# ---------------------------------------------------------------------------
# 7. Audit-only mode cannot project verified success
# ---------------------------------------------------------------------------

class TestAuditOnlyCannotProjectSuccess:
    """Audit-only results must not claim block-level verification."""

    def test_audit_result_is_audit_only(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(),
                _test_run(),
            )
        )
        assert result.audit_only is True
        assert result.block_mode_enabled is False

    def test_audit_result_never_blocks_final_answer(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _test_run(),  # missing GitDiff
            )
        )
        assert result.final_answer_blocked is False
        assert result.audit_only is True

    def test_audit_contract_on_missing_is_audit(self) -> None:
        from openmagi_core_agent.evidence.coding_verification import (
            build_coding_verification_audit_contract,
        )
        contract = build_coding_verification_audit_contract(
            _request(_diff(), _test_run())
        )
        assert contract.on_missing == "audit"

    def test_audit_only_gate_disabled_cannot_claim_pass(self) -> None:
        """Gate disabled mode must report disabled, not passed."""
        gate = CodingEvidenceGate(CodingEvidenceGateConfig(enabled=False))
        decision = gate.evaluate(_gate_request(_diff(), _test_run()))
        assert decision.status == "disabled"


# ---------------------------------------------------------------------------
# 8. Digest-only public reports (no raw paths/contents/tokens)
# ---------------------------------------------------------------------------

class TestDigestOnlyPublicReports:
    """Public projections must use sha256 digests, no raw file paths."""

    def test_gate_decision_public_projection_v2_uses_counts_not_types(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
                enforcement="audit",
            )
        )
        decision = gate.evaluate(
            _gate_request(_diff(), _test_run()),
        )
        projection = decision.public_projection(schema_version="v2")
        # v2 uses counts, not raw type lists
        assert "requiredEvidenceTypeCount" in projection
        assert "matchedEvidenceTypeCount" in projection
        assert "requiredEvidenceTypes" not in projection

    def test_gate_decision_claim_digest_is_sha256(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
            )
        )
        decision = gate.evaluate(
            _gate_request(_diff(), _test_run()),
        )
        assert decision.claim_digest.startswith("sha256:")
        assert len(decision.claim_digest) == len("sha256:") + 64

    def test_gate_decision_receipt_ref_is_deterministic(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
            )
        )
        d1 = gate.evaluate(_gate_request(_diff(), _test_run()))
        d2 = gate.evaluate(_gate_request(_diff(), _test_run()))
        assert d1.receipt_ref == d2.receipt_ref
        assert d1.receipt_ref.startswith("coding-evidence-gate-receipt:")

    def test_public_projection_has_no_raw_paths(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
            )
        )
        decision = gate.evaluate(
            _gate_request(_diff(), _test_run()),
        )
        projection = decision.public_projection()
        import json
        rendered = json.dumps(projection)
        assert "/Users/" not in rendered
        assert "/home/" not in rendered
        assert "/workspace/" not in rendered
        assert "/data/bots/" not in rendered

    def test_audit_evidence_fields_use_digests(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(
                _diff(),
                _test_run(),
            )
        )
        # audit_evidence should not contain raw file paths
        import json
        rendered = json.dumps(
            result.audit_evidence.model_dump(by_alias=True, mode="json")
        )
        assert "/Users/" not in rendered
        assert "/data/bots/" not in rendered


# ---------------------------------------------------------------------------
# 9. Both missing — no diff AND no test
# ---------------------------------------------------------------------------

class TestBothMissing:
    """No evidence at all must fail with both missing."""

    def test_no_evidence_at_all_fails(self) -> None:
        result = evaluate_coding_verification_audit(
            _request()
        )
        assert result.verdict.ok is False
        missing_types = tuple(r.type for r in result.verdict.missing_requirements)
        assert "GitDiff" in missing_types
        assert "TestRun" in missing_types

    def test_no_evidence_failure_codes(self) -> None:
        result = evaluate_coding_verification_audit(
            _request()
        )
        failure_codes = tuple(f.code for f in result.verdict.failures)
        assert all(c == "EVIDENCE_CONTRACT_MISSING" for c in failure_codes)
        assert len(failure_codes) == 2


# ---------------------------------------------------------------------------
# 10. productionWorkspaceMutationAllowed must always be False
# ---------------------------------------------------------------------------

class TestProductionWorkspaceMutationAlwaysFalse:
    """Safety invariant: production workspace mutation is never allowed."""

    def test_gate_authority_flags_production_write_always_false(self) -> None:
        gate = CodingEvidenceGate(
            CodingEvidenceGateConfig(
                enabled=True,
                localEvaluationEnabled=True,
                enforcement="local_block",
            )
        )
        decision = gate.evaluate(
            _gate_request(_diff(), _test_run()),
        )
        assert decision.authority_flags.production_write_allowed is False

    def test_gate_production_block_config_is_always_false(self) -> None:
        config = CodingEvidenceGateConfig(
            enabled=True,
            localEvaluationEnabled=True,
        )
        assert config.production_block_enabled is False

    def test_audit_attachment_flags_all_false(self) -> None:
        result = evaluate_coding_verification_audit(
            _request(_diff(), _test_run())
        )
        flags = result.attachment_flags.model_dump(by_alias=True)
        assert all(v is False for v in flags.values())


# ---------------------------------------------------------------------------
# 11. Hard gate contract (block_final_answer enforcement)
# ---------------------------------------------------------------------------

class TestHardGateContract:
    """Hard gate uses block_final_answer enforcement."""

    def test_hard_gate_contract_uses_block_final_answer(self) -> None:
        contract = build_coding_verification_hard_gate_contract(
            _request(_diff(), _test_run())
        )
        assert contract.on_missing == "block_final_answer"

    def test_hard_gate_missing_diff_returns_block_ready(self) -> None:
        result = evaluate_coding_verification_hard_gate(
            _request(_test_run())
        )
        assert result.verdict.ok is False
        assert result.verdict.state == "block_ready"
        assert result.verdict.enforcement == "block_final_answer"

    def test_hard_gate_missing_test_returns_block_ready(self) -> None:
        result = evaluate_coding_verification_hard_gate(
            _request(_diff())
        )
        assert result.verdict.ok is False
        assert result.verdict.state == "block_ready"

    def test_hard_gate_stale_diff_returns_block_ready(self) -> None:
        result = evaluate_coding_verification_hard_gate(
            _request(
                _diff(observed_at=5),
                _test_run(),
                last_code_mutation_at=10,
            )
        )
        assert result.verdict.ok is False
        assert result.verdict.state == "block_ready"

    def test_hard_gate_failed_test_returns_block_ready(self) -> None:
        result = evaluate_coding_verification_hard_gate(
            _request(
                _diff(),
                _test_run(exit_code=1, status="failed"),
            )
        )
        assert result.verdict.ok is False
        assert result.verdict.state == "block_ready"

    def test_hard_gate_stale_test_returns_block_ready(self) -> None:
        result = evaluate_coding_verification_hard_gate(
            _request(
                _diff(),
                _test_run(observed_at=5),
                last_code_mutation_at=10,
            )
        )
        assert result.verdict.ok is False
        assert result.verdict.state == "block_ready"

    def test_hard_gate_valid_evidence_passes(self) -> None:
        result = evaluate_coding_verification_hard_gate(
            _request(_diff(), _test_run())
        )
        assert result.verdict.ok is True
        assert result.verdict.state == "pass"

    def test_hard_gate_result_stays_audit_only(self) -> None:
        """Even hard gate results have auditOnly=True (scaffold never blocks)."""
        result = evaluate_coding_verification_hard_gate(
            _request(_test_run())
        )
        assert result.audit_only is True
        assert result.block_mode_enabled is False
        assert result.final_answer_blocked is False

    def test_hard_gate_no_evidence_both_block_ready(self) -> None:
        result = evaluate_coding_verification_hard_gate(_request())
        assert result.verdict.ok is False
        assert result.verdict.state == "block_ready"
        missing = tuple(r.type for r in result.verdict.missing_requirements)
        assert "GitDiff" in missing
        assert "TestRun" in missing


# ---------------------------------------------------------------------------
# 12. Command digest helper
# ---------------------------------------------------------------------------

class TestCommandDigestHelper:
    """evidence_command_digest produces stable sha256 digests."""

    def test_command_digest_format(self) -> None:
        digest = evidence_command_digest("pytest tests/ -q")
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_command_digest_deterministic(self) -> None:
        d1 = evidence_command_digest("npm test")
        d2 = evidence_command_digest("npm test")
        assert d1 == d2

    def test_different_commands_different_digests(self) -> None:
        d1 = evidence_command_digest("npm test")
        d2 = evidence_command_digest("npm run lint")
        assert d1 != d2

    def test_command_digest_matches_manual_sha256(self) -> None:
        cmd = "uv run pytest tests/test_app.py -q"
        expected = "sha256:" + hashlib.sha256(cmd.encode("utf-8")).hexdigest()
        assert evidence_command_digest(cmd) == expected
