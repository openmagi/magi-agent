from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openmagi_core_agent.evidence.reports import (
    PublicEvidenceVerdictReport,
    public_evidence_verdict_report,
)
from openmagi_core_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerAttachmentFlags,
    SourceLedgerRecord,
)
from openmagi_core_agent.evidence.types import (
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceFailureCode,
    EvidenceRecord,
    EvidenceRequirement,
    _validate_strict_bool,
)


CitationAuditItemStatus = Literal["pass", "failure", "missing"]
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    validate_default=True,
    extra="forbid",
    arbitrary_types_allowed=True,
)
_SOURCE_ID_RE = re.compile(r"^src_[1-9][0-9]*$")


class CitationAuditRequest(BaseModel):
    model_config = _MODEL_CONFIG

    contract_id: str = Field(alias="contractId")
    turn_id: str = Field(alias="turnId")
    cited_refs: tuple[str, ...] = Field(alias="citedRefs")
    source_ledger: LocalResearchSourceLedger = Field(alias="sourceLedger")

    @field_validator("contract_id", "turn_id")
    @classmethod
    def _reject_empty_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("citation audit identifiers must be non-empty")
        return value

    @field_validator("cited_refs")
    @classmethod
    def _validate_cited_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("citation audit requires at least one cited ref")
        if len(set(value)) != len(value):
            raise ValueError("citation audit cited refs must not contain duplicates")
        if any(_SOURCE_ID_RE.fullmatch(ref) is None for ref in value):
            raise ValueError("citation audit refs must use stable src_N metadata refs")
        return value


class CitationAuditItem(BaseModel):
    model_config = _MODEL_CONFIG

    source_id: str = Field(alias="sourceId")
    status: CitationAuditItemStatus
    inspected: bool = False
    evidence_type: str | None = Field(default=None, alias="evidenceType")
    failure_code: EvidenceFailureCode | None = Field(default=None, alias="failureCode")
    message: str | None = None

    @field_validator("inspected", mode="before")
    @classmethod
    def _validate_inspected_bool(cls, value: object) -> object:
        return _validate_strict_bool(value, "inspected")


class CitationAuditResult(BaseModel):
    model_config = _MODEL_CONFIG

    contract_id: str = Field(alias="contractId")
    turn_id: str = Field(alias="turnId")
    ok: bool
    enforcement: Literal["audit"] = "audit"
    verdict: EvidenceContractVerdict
    audit_items: tuple[CitationAuditItem, ...] = Field(alias="auditItems")
    block_mode: Literal[False] = Field(default=False, alias="blockMode")
    final_answer_mutated: Literal[False] = Field(default=False, alias="finalAnswerMutated")
    user_visible_enforcement_actions: tuple[()] = Field(
        default=(),
        alias="userVisibleEnforcementActions",
    )
    attachment_flags: SourceLedgerAttachmentFlags = Field(
        default_factory=SourceLedgerAttachmentFlags,
        alias="attachmentFlags",
    )

    @field_validator("ok", "block_mode", "final_answer_mutated", mode="before")
    @classmethod
    def _validate_bool(cls, value: object, info: object) -> object:
        field_name = getattr(info, "field_name", "citation audit boolean")
        return _validate_strict_bool(value, field_name)


class PublicCitationAuditItemReport(BaseModel):
    model_config = _MODEL_CONFIG

    source_id: str = Field(alias="sourceId")
    status: CitationAuditItemStatus
    inspected: bool
    evidence_type: str | None = Field(default=None, alias="evidenceType")
    failure_code: EvidenceFailureCode | None = Field(default=None, alias="failureCode")
    message: str | None = None


class PublicCitationAuditReport(BaseModel):
    model_config = _MODEL_CONFIG

    contract_id: str = Field(alias="contractId")
    turn_id: str = Field(alias="turnId")
    ok: bool
    enforcement: Literal["audit"]
    verdict: PublicEvidenceVerdictReport
    audit_items: tuple[PublicCitationAuditItemReport, ...] = Field(alias="auditItems")
    block_mode: Literal[False] = Field(alias="blockMode")
    final_answer_mutated: Literal[False] = Field(alias="finalAnswerMutated")
    user_visible_enforcement_actions: tuple[()] = Field(
        alias="userVisibleEnforcementActions"
    )
    attachment_flags: SourceLedgerAttachmentFlags = Field(alias="attachmentFlags")


def audit_citations(request: CitationAuditRequest) -> CitationAuditResult:
    records_by_id = {
        record.source_id: record for record in request.source_ledger.sources_for_turn(request.turn_id)
    }
    items: list[CitationAuditItem] = []
    matched: list[EvidenceRecord] = []
    failures: list[EvidenceContractFailure] = []
    missing_requirements: list[EvidenceRequirement] = []

    for cited_ref in request.cited_refs:
        record = records_by_id.get(cited_ref)
        if record is None:
            item, failure = _missing_item(request.contract_id, cited_ref)
            items.append(item)
            failures.append(failure)
            missing_requirements.append(EvidenceRequirement(type="SourceInspection"))
            continue
        if not _is_inspected_citable_source(record):
            item, failure = _uninspected_item(request.contract_id, record)
            items.append(item)
            failures.append(failure)
            continue
        items.append(
            CitationAuditItem(
                sourceId=record.source_id,
                status="pass",
                inspected=True,
                evidenceType=record.evidence_type,
            )
        )
        matched.append(record.to_evidence_record())

    ok = all(item.status == "pass" for item in items)
    state: Literal["pass", "missing", "failed"] = "pass"
    if not ok:
        state = "missing" if any(item.status == "missing" for item in items) else "failed"

    verdict = EvidenceContractVerdict(
        contractId=request.contract_id,
        ok=ok,
        state=state,
        enforcement="audit",
        missingRequirements=tuple(missing_requirements),
        matchedEvidence=tuple(matched),
        failures=tuple(failures),
        requirementCoverage=("SourceInspection",) if matched else (),
    )
    return CitationAuditResult(
        contractId=request.contract_id,
        turnId=request.turn_id,
        ok=ok,
        verdict=verdict,
        auditItems=tuple(items),
        blockMode=False,
        finalAnswerMutated=False,
        userVisibleEnforcementActions=(),
        attachmentFlags=SourceLedgerAttachmentFlags(),
    )


def public_citation_audit_report(result: CitationAuditResult) -> PublicCitationAuditReport:
    return PublicCitationAuditReport(
        contractId=result.contract_id,
        turnId=result.turn_id,
        ok=result.ok,
        enforcement=result.enforcement,
        verdict=public_evidence_verdict_report(result.verdict),
        auditItems=tuple(
            PublicCitationAuditItemReport(
                sourceId=item.source_id,
                status=item.status,
                inspected=item.inspected,
                evidenceType=item.evidence_type,
                failureCode=item.failure_code,
                message=item.message,
            )
            for item in result.audit_items
        ),
        blockMode=False,
        finalAnswerMutated=False,
        userVisibleEnforcementActions=(),
        attachmentFlags=result.attachment_flags,
    )


def _is_inspected_citable_source(record: SourceLedgerRecord) -> bool:
    return record.inspected and record.evidence_type == "SourceInspection"


def _missing_item(
    contract_id: str,
    source_id: str,
) -> tuple[CitationAuditItem, EvidenceContractFailure]:
    message = f"Citation ref {source_id} was not present in the recorded local source ledger."
    return (
        CitationAuditItem(
            sourceId=source_id,
            status="missing",
            inspected=False,
            failureCode="EVIDENCE_CONTRACT_MISSING",
            message=message,
        ),
        EvidenceContractFailure(
            code="EVIDENCE_CONTRACT_MISSING",
            contractId=contract_id,
            requirementType="SourceInspection",
            message=message,
            metadata={"sourceId": source_id},
        ),
    )


def _uninspected_item(
    contract_id: str,
    record: SourceLedgerRecord,
) -> tuple[CitationAuditItem, EvidenceContractFailure]:
    message = f"Citation ref {record.source_id} did not point to an inspected source."
    return (
        CitationAuditItem(
            sourceId=record.source_id,
            status="failure",
            inspected=record.inspected,
            evidenceType=record.evidence_type,
            failureCode="EVIDENCE_CONTRACT_FIELD_MISMATCH",
            message=message,
        ),
        EvidenceContractFailure(
            code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
            contractId=contract_id,
            requirementType="SourceInspection",
            message=message,
            metadata={
                "sourceId": record.source_id,
                "inspected": record.inspected,
                "evidenceType": record.evidence_type,
            },
        ),
    )


__all__ = [
    "CitationAuditItem",
    "CitationAuditItemStatus",
    "CitationAuditRequest",
    "CitationAuditResult",
    "PublicCitationAuditItemReport",
    "PublicCitationAuditReport",
    "audit_citations",
    "public_citation_audit_report",
]
