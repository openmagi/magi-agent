from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, Self

from pydantic import Field, field_serializer, field_validator

from magi_agent.coding.edit_matching import EditMatchResult
from magi_agent.evidence.contracts import evaluate_evidence_contract
from magi_agent.evidence.reports import (
    PublicEvidenceRecordReport,
    PublicEvidenceVerdictReport,
    public_evidence_record_report,
    public_evidence_verdict_report,
)
from magi_agent.evidence.types import (
    EvidenceContract,
    EvidenceContractVerdict,
    EvidenceEnforcement,
    EvidenceFieldMatcher,
    EvidenceMetadataModel,
    EvidenceRecord,
    EvidenceRequirement,
    EvidenceSource,
    _validate_observed_at,
)
from magi_agent.harness.verifier_bus import VerifierResultMetadata

# ---------------------------------------------------------------------------
# Low-confidence tier names (require post-edit verification when gating is on)
# ---------------------------------------------------------------------------
_LOW_CONFIDENCE_TIERS = frozenset({"block_anchor", "context_aware"})
_HIGH_CONFIDENCE_FLOOR = 0.80


CODING_VERIFICATION_AUDIT_VERIFIER_ID = "dev-coding-verification-audit"
CODING_VERIFICATION_AUDIT_CONTRACT_ID = "dev-coding-verification-audit"


class CodingVerificationAuditAttachmentFlags(EvidenceMetadataModel):
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    vcs_executed: Literal[False] = Field(default=False, alias="vcsExecuted")
    verification_command_executed: Literal[False] = Field(
        default=False,
        alias="verificationCommandExecuted",
    )
    file_mutated: Literal[False] = Field(default=False, alias="fileMutated")
    workspace_written: Literal[False] = Field(default=False, alias="workspaceWritten")
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")
    final_answer_blocked: Literal[False] = Field(default=False, alias="finalAnswerBlocked")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")
    production_attached: Literal[False] = Field(default=False, alias="productionAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls()

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "shell_or_code_executed",
        "vcs_executed",
        "verification_command_executed",
        "file_mutated",
        "workspace_written",
        "evidence_block_enabled",
        "final_answer_blocked",
        "traffic_attached",
        "execution_attached",
        "runner_attached",
        "route_attached",
        "canary_attached",
        "production_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class CodingVerificationAuditRequest(EvidenceMetadataModel):
    evidence_records: tuple[EvidenceRecord, ...] = Field(alias="evidenceRecords")
    last_code_mutation_at: int | float = Field(alias="lastCodeMutationAt")
    require_commit_checkpoint: bool = Field(default=False, alias="requireCommitCheckpoint")
    verifier_id: str = Field(
        default=CODING_VERIFICATION_AUDIT_VERIFIER_ID,
        alias="verifierId",
    )
    contract_id: str = Field(
        default=CODING_VERIFICATION_AUDIT_CONTRACT_ID,
        alias="contractId",
    )

    @field_validator("evidence_records")
    @classmethod
    def _revalidate_records(
        cls,
        value: tuple[EvidenceRecord, ...],
    ) -> tuple[EvidenceRecord, ...]:
        return tuple(
            EvidenceRecord.model_validate(
                record.model_dump(by_alias=False, mode="python", warnings=False)
            )
            if isinstance(record, EvidenceRecord)
            else EvidenceRecord.model_validate(record)
            for record in value
        )

    @field_validator("last_code_mutation_at", mode="before")
    @classmethod
    def _validate_last_code_mutation_at(cls, value: object) -> int | float:
        return _validate_observed_at(value)

    @field_validator("require_commit_checkpoint", mode="before")
    @classmethod
    def _validate_bool(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("requireCommitCheckpoint must be a boolean")
        return value

    @field_validator("verifier_id", "contract_id")
    @classmethod
    def _reject_empty_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("coding verification ids must be non-empty")
        return value


class CodingVerificationAuditResult(EvidenceMetadataModel):
    contract: EvidenceContract
    verdict: EvidenceContractVerdict
    verifier_result: VerifierResultMetadata = Field(alias="verifierResult")
    audit_evidence: EvidenceRecord = Field(alias="auditEvidence")
    public_verdict_report: PublicEvidenceVerdictReport = Field(alias="publicVerdictReport")
    public_audit_evidence_report: PublicEvidenceRecordReport = Field(
        alias="publicAuditEvidenceReport",
    )
    attachment_flags: CodingVerificationAuditAttachmentFlags = Field(
        default_factory=CodingVerificationAuditAttachmentFlags,
        alias="attachmentFlags",
    )
    audit_only: Literal[True] = Field(default=True, alias="auditOnly")
    block_mode_enabled: Literal[False] = Field(default=False, alias="blockModeEnabled")
    final_answer_blocked: Literal[False] = Field(default=False, alias="finalAnswerBlocked")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator("contract")
    @classmethod
    def _revalidate_contract(cls, value: EvidenceContract) -> EvidenceContract:
        return EvidenceContract.model_validate(
            value.model_dump(by_alias=False, mode="python", warnings=False)
        )

    @field_validator("verdict")
    @classmethod
    def _revalidate_verdict(cls, value: EvidenceContractVerdict) -> EvidenceContractVerdict:
        return EvidenceContractVerdict.model_validate(
            value.model_dump(by_alias=False, mode="python", warnings=False)
        )

    @field_validator("verifier_result")
    @classmethod
    def _revalidate_verifier_result(
        cls,
        value: VerifierResultMetadata,
    ) -> VerifierResultMetadata:
        return VerifierResultMetadata.model_validate(
            value.model_dump(by_alias=False, mode="python", warnings=False)
        )

    @field_validator("audit_evidence")
    @classmethod
    def _revalidate_audit_evidence(cls, value: EvidenceRecord) -> EvidenceRecord:
        return EvidenceRecord.model_validate(
            value.model_dump(by_alias=False, mode="python", warnings=False)
        )


def build_coding_verification_audit_contract(
    request: CodingVerificationAuditRequest | Mapping[str, object],
) -> EvidenceContract:
    parsed = _parse_request(request)
    requirements: list[EvidenceRequirement] = [
        EvidenceRequirement(
            type="GitDiff",
            after="last_code_mutation",
            fields={"changedFiles": EvidenceFieldMatcher(exists=True)},
        ),
        EvidenceRequirement(
            type="TestRun",
            after="last_code_mutation",
            exitCode=0,
            fields={"command": EvidenceFieldMatcher(exists=True)},
        ),
    ]
    if parsed.require_commit_checkpoint:
        requirements.append(
            EvidenceRequirement(
                type="CommitCheckpoint",
                after="last_code_mutation",
                fields={"checkpointId": EvidenceFieldMatcher(exists=True)},
            )
        )
    return EvidenceContract(
        id=parsed.contract_id,
        description="Audit-only coding verification evidence contract.",
        triggers=("beforeCommit",),
        when={"lastCodeMutation": parsed.last_code_mutation_at},
        requirements=tuple(requirements),
        onMissing="audit",
        retryMessage=(
            "Record post-mutation GitDiff and TestRun evidence before claiming coding "
            "verification."
        ),
    )


def build_coding_verification_hard_gate_contract(
    request: CodingVerificationAuditRequest | Mapping[str, object],
) -> EvidenceContract:
    """Build a block_final_answer contract for coding diff and test evidence.

    Unlike the audit contract, this uses ``onMissing="block_final_answer"`` so
    that missing or failed evidence prevents the model from claiming completion.

    - "I changed X" requires a fresh ``GitDiff`` record.
    - "Tests passed" requires a fresh ``TestRun`` with ``exit_code=0``.
    - Stale or failed evidence cannot satisfy the contract.
    """
    parsed = _parse_request(request)
    requirements: list[EvidenceRequirement] = [
        EvidenceRequirement(
            type="GitDiff",
            after="last_code_mutation",
            fields={"changedFiles": EvidenceFieldMatcher(exists=True)},
        ),
        EvidenceRequirement(
            type="TestRun",
            after="last_code_mutation",
            exitCode=0,
            fields={"command": EvidenceFieldMatcher(exists=True)},
        ),
    ]
    if parsed.require_commit_checkpoint:
        requirements.append(
            EvidenceRequirement(
                type="CommitCheckpoint",
                after="last_code_mutation",
                fields={"checkpointId": EvidenceFieldMatcher(exists=True)},
            )
        )
    return EvidenceContract(
        id=parsed.contract_id,
        description="Hard-gate coding verification: diff and test evidence required.",
        triggers=("beforeCommit",),
        when={"lastCodeMutation": parsed.last_code_mutation_at},
        requirements=tuple(requirements),
        onMissing="block_final_answer",
        retryMessage=(
            "Missing diff or test evidence. Run GitDiff and TestRun(exit_code=0) "
            "after the last code mutation before claiming completion."
        ),
    )


def evaluate_coding_verification_audit(
    request: CodingVerificationAuditRequest | Mapping[str, object],
    evidence_records: Iterable[EvidenceRecord] | None = None,
) -> CodingVerificationAuditResult:
    parsed = _parse_request(request, evidence_records=evidence_records)
    contract = build_coding_verification_audit_contract(parsed)
    verdict = evaluate_evidence_contract(contract, parsed.evidence_records)
    verifier_result = _verifier_result(parsed, verdict)
    audit_evidence = _audit_evidence(parsed, verdict, verifier_result)
    return CodingVerificationAuditResult(
        contract=contract,
        verdict=verdict,
        verifierResult=verifier_result,
        auditEvidence=audit_evidence,
        publicVerdictReport=public_evidence_verdict_report(verdict),
        publicAuditEvidenceReport=public_evidence_record_report(audit_evidence),
        attachmentFlags=CodingVerificationAuditAttachmentFlags(),
        auditOnly=True,
        blockModeEnabled=False,
        finalAnswerBlocked=False,
        trafficAttached=False,
        executionAttached=False,
        runnerAttached=False,
        routeAttached=False,
        canaryAttached=False,
    )


def evaluate_coding_verification_hard_gate(
    request: CodingVerificationAuditRequest | Mapping[str, object],
    evidence_records: Iterable[EvidenceRecord] | None = None,
) -> CodingVerificationAuditResult:
    """Evaluate coding evidence using block_final_answer enforcement.

    This is the hard-gate variant.  When evidence is missing or stale the
    verdict state will be ``block_ready`` instead of ``missing``, signalling
    that the final answer should be blocked.

    The result still carries ``auditOnly=True`` and ``blockModeEnabled=False``
    because this Python scaffold never attaches live blocking side-effects;
    the ``block_final_answer`` policy is expressed only inside the contract /
    verdict and consumed by the harness.
    """
    parsed = _parse_request(request, evidence_records=evidence_records)
    contract = build_coding_verification_hard_gate_contract(parsed)
    verdict = evaluate_evidence_contract(contract, parsed.evidence_records)
    verifier_result = _verifier_result(parsed, verdict)
    audit_evidence = _audit_evidence(parsed, verdict, verifier_result)
    return CodingVerificationAuditResult(
        contract=contract,
        verdict=verdict,
        verifierResult=verifier_result,
        auditEvidence=audit_evidence,
        publicVerdictReport=public_evidence_verdict_report(verdict),
        publicAuditEvidenceReport=public_evidence_record_report(audit_evidence),
        attachmentFlags=CodingVerificationAuditAttachmentFlags(),
        auditOnly=True,
        blockModeEnabled=False,
        finalAnswerBlocked=False,
        trafficAttached=False,
        executionAttached=False,
        runnerAttached=False,
        routeAttached=False,
        canaryAttached=False,
    )


def _parse_request(
    request: CodingVerificationAuditRequest | Mapping[str, object],
    *,
    evidence_records: Iterable[EvidenceRecord] | None = None,
) -> CodingVerificationAuditRequest:
    if isinstance(request, CodingVerificationAuditRequest):
        data = request.model_dump(by_alias=True, mode="python", warnings=False)
    else:
        data = dict(request)
    if evidence_records is not None:
        data["evidenceRecords"] = tuple(evidence_records)
    return CodingVerificationAuditRequest.model_validate(data)


def _verifier_result(
    request: CodingVerificationAuditRequest,
    verdict: EvidenceContractVerdict,
) -> VerifierResultMetadata:
    status = _verifier_status(verdict)
    summary = _verifier_summary(verdict)
    failure_message = summary if not verdict.ok else None
    return VerifierResultMetadata(
        verifierId=request.verifier_id,
        status=status,
        publicSummary=summary,
        failureMessage=failure_message,
    )


def _verifier_status(verdict: EvidenceContractVerdict) -> str:
    if verdict.state == "pass":
        return "pass"
    if verdict.state == "missing":
        return "missing"
    if verdict.state == "audit":
        return "audit"
    return "failed"


def _verifier_summary(verdict: EvidenceContractVerdict) -> str:
    matched = ", ".join(record.type for record in verdict.matched_evidence) or "none"
    missing = ", ".join(requirement.type for requirement in verdict.missing_requirements)
    failures = ", ".join(failure.code for failure in verdict.failures)
    parts = [
        f"coding verification audit {verdict.state}",
        f"matched: {matched}",
    ]
    if missing:
        parts.append(f"missing: {missing}")
    if failures:
        parts.append(f"failures: {failures}")
    return "; ".join(parts)


def build_edit_confidence_contract(
    match: EditMatchResult,
    *,
    last_code_mutation_at: int | float,
    enforcement: EvidenceEnforcement = "off",
    contract_id: str = "dev-edit-confidence",
) -> EvidenceContract:
    """Build an evidence contract for a fuzzy-edit match result.

    - HIGH-confidence tiers (conf >= 0.80, and not ambiguous, and not a
      low-confidence tier name): ``onMissing="audit"`` — never blocks.
    - LOW tiers (``block_anchor`` or ``context_aware``) or ``ambiguous=True``
      tiers: ``onMissing="block_final_answer"`` when ``enforcement`` is
      ``block_final_answer``; ``onMissing="audit"`` otherwise.

    When ``enforcement="off"`` (the default), the returned contract always
    uses ``onMissing="audit"`` regardless of tier — receipts are emitted but
    nothing blocks.
    """
    is_low = match.tier in _LOW_CONFIDENCE_TIERS or match.ambiguous
    if is_low and enforcement == "block_final_answer":
        on_missing = "block_final_answer"
        description = (
            f"Low-confidence fuzzy edit (tier={match.tier!r}, "
            f"confidence={match.confidence:.2f}) requires post-edit "
            "GitDiff and TestRun(exit_code=0) before final answer."
        )
        retry_msg = (
            f"Low-confidence edit (tier={match.tier!r}). "
            "Run GitDiff and TestRun(exit_code=0) after the edit before claiming completion."
        )
    else:
        on_missing = "audit"
        description = (
            f"EditMatch evidence audit (tier={match.tier!r}, "
            f"confidence={match.confidence:.2f})."
        )
        retry_msg = (
            "Record post-edit GitDiff and TestRun evidence before claiming completion."
        )

    requirements: list[EvidenceRequirement] = [
        EvidenceRequirement(
            type="EditMatch",
            after="last_code_mutation",
            fields={
                "tier": EvidenceFieldMatcher(equals=match.tier),
            },
        ),
        EvidenceRequirement(
            type="GitDiff",
            after="last_code_mutation",
            fields={"changedFiles": EvidenceFieldMatcher(exists=True)},
        ),
        EvidenceRequirement(
            type="TestRun",
            after="last_code_mutation",
            exitCode=0,
            fields={"command": EvidenceFieldMatcher(exists=True)},
        ),
    ]

    return EvidenceContract(
        id=contract_id,
        description=description,
        triggers=("beforeCommit",),
        when={"lastCodeMutation": last_code_mutation_at},
        requirements=tuple(requirements),
        onMissing=on_missing,
        retryMessage=retry_msg,
    )


def _audit_evidence(
    request: CodingVerificationAuditRequest,
    verdict: EvidenceContractVerdict,
    verifier_result: VerifierResultMetadata,
) -> EvidenceRecord:
    fields: dict[str, object] = {
        "verdictOk": verdict.ok,
        "verdictState": verdict.state,
        "enforcement": verdict.enforcement,
        "matchedEvidenceTypes": tuple(record.type for record in verdict.matched_evidence),
        "missingRequirementTypes": tuple(
            requirement.type for requirement in verdict.missing_requirements
        ),
        "failureCodes": tuple(failure.code for failure in verdict.failures),
        "requiredEvidenceTypes": verdict.requirement_coverage,
        "blockModeEnabled": False,
        "finalAnswerBlocked": False,
    }
    return EvidenceRecord(
        type="DeterministicEvidenceVerifier",
        status="ok" if verdict.ok else "failed",
        observedAt=request.last_code_mutation_at,
        source=EvidenceSource(
            kind="verifier",
            verifierName=request.verifier_id,
            contractId=verdict.contract_id,
            metadata={"auditOnly": True},
        ),
        fields=fields,
        preview=_verifier_summary(verdict),
        metadata={
            "verifierResult": verifier_result.model_dump(by_alias=True, mode="python"),
            "publicSafeFields": tuple(fields),
            "auditOnly": True,
            "blockModeEnabled": False,
            "finalAnswerBlocked": False,
        },
    )


__all__ = [
    "CODING_VERIFICATION_AUDIT_CONTRACT_ID",
    "CODING_VERIFICATION_AUDIT_VERIFIER_ID",
    "CodingVerificationAuditAttachmentFlags",
    "CodingVerificationAuditRequest",
    "CodingVerificationAuditResult",
    "build_coding_verification_audit_contract",
    "build_coding_verification_hard_gate_contract",
    "build_edit_confidence_contract",
    "evaluate_coding_verification_audit",
    "evaluate_coding_verification_hard_gate",
]
