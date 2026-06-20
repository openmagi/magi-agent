from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.evidence.citation_audit import (
    CitationAuditRequest,
    CitationAuditResult,
    audit_citations,
)
from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerRecord,
)
from magi_agent.evidence.types import _validate_strict_bool
from magi_agent.ops.authority import FalseOnlyAuthorityModel


ResearchFinalGateMode = Literal[
    "off",
    "audit",
    "local_block_intent",
    "approval_required_block",
]
ResearchFinalGateStatus = Literal[
    "skipped",
    "passed",
    "audit_failed",
    "local_block_intent",
    "approval_required_block_intent",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    validate_default=True,
    extra="forbid",
    arbitrary_types_allowed=True,
    hide_input_in_errors=True,
)
_SOURCE_ID_RE = re.compile(r"^src_[1-9][0-9]*$")
_SAFE_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SAFE_GATE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.IGNORECASE)
_FINAL_ANSWER_SOURCE_REF_RE = re.compile(r"\bsrc_[1-9][0-9]*\b")
_FINAL_ANSWER_ANY_SOURCE_REF_RE = re.compile(r"\bsrc_[A-Za-z0-9_.:-]+\b")
_FACTUAL_CLAIM_RE = re.compile(
    r"\b(is|are|was|were|has|have|had|supports?|according to|latest|current|today|"
    r"recent|new|released?|production|default-off|ready|will|must|should|launched|"
    r"announced|published|ships?|version|price|costs?|cost|uses?|runs?|deploys?|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\b|[$€£¥]\s*\d+|\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d+(?:\.\d+)+\b",
    re.IGNORECASE,
)
_SHA256_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_PRIVATE_REF_RE = re.compile(
    r"(?:"
    r"/Users/|/home/|/workspace/|/data/bots/|/var/lib/kubelet/|"
    r"Bearer\s+|github_pat_|gh[opusr]_|xox[a-z]-|AKIA|AIza|sk-|"
    r"authorization|cookie|secret|token|password|credential|api[_-]?key|private[_-]?key|"
    r"hidden[_-]?reasoning|chain[_-]?of[_-]?thought"
    r")",
    re.IGNORECASE,
)


class ResearchClaimRef(BaseModel):
    model_config = _MODEL_CONFIG

    claim_id: str = Field(alias="claimId")
    cited_refs: tuple[str, ...] = Field(default=(), alias="citedRefs")
    requires_fresh_source: bool = Field(default=False, alias="requiresFreshSource")
    fresh_source_refs: tuple[str, ...] = Field(default=(), alias="freshSourceRefs")

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        if _SAFE_PUBLIC_REF_RE.fullmatch(value) is None or _PRIVATE_REF_RE.search(value):
            raise ValueError("claimId must be a safe public reference")
        return value

    @field_validator("cited_refs", "fresh_source_refs")
    @classmethod
    def _validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("source refs must not contain duplicates")
        if any(_SOURCE_ID_RE.fullmatch(ref) is None for ref in value):
            raise ValueError("source refs must use stable src_N metadata refs")
        return value

    @field_validator("requires_fresh_source", mode="before")
    @classmethod
    def _validate_requires_fresh_source_bool(cls, value: object) -> object:
        return _validate_strict_bool(value, "requiresFreshSource")


class ResearchFinalGateAuthorityFlags(FalseOnlyAuthorityModel):
    final_answer_blocked: Literal[False] = Field(default=False, alias="finalAnswerBlocked")
    final_answer_blocking_enabled: Literal[False] = Field(
        default=False,
        alias="finalAnswerBlockingEnabled",
    )
    user_visible_output_blocked: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputBlocked",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")


class ResearchFinalGateRequest(BaseModel):
    model_config = _MODEL_CONFIG

    contract_id: str = Field(alias="contractId")
    turn_id: str = Field(alias="turnId")
    mode: ResearchFinalGateMode = "off"
    candidate_final_answer: str = Field(alias="candidateFinalAnswer")
    extracted_claim_refs: tuple[ResearchClaimRef, ...] = Field(
        default=(),
        alias="extractedClaimRefs",
    )
    source_ledger: LocalResearchSourceLedger = Field(alias="sourceLedger")
    cited_refs: tuple[str, ...] = Field(default=(), alias="citedRefs")
    citation_audit_result: CitationAuditResult | None = Field(
        default=None,
        alias="citationAuditResult",
    )

    @field_validator("contract_id", "turn_id")
    @classmethod
    def _reject_empty_ids(cls, value: str) -> str:
        if _SAFE_GATE_ID_RE.fullmatch(value) is None:
            raise ValueError("research final gate identifiers must be safe public refs")
        if _PRIVATE_REF_RE.search(value):
            raise ValueError("research final gate identifiers must be safe public refs")
        return value

    @field_validator("cited_refs")
    @classmethod
    def _validate_cited_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("cited refs must not contain duplicates")
        if any(_SOURCE_ID_RE.fullmatch(ref) is None for ref in value):
            raise ValueError("cited refs must use stable src_N metadata refs")
        return value


class ResearchFinalGateResult(FalseOnlyAuthorityModel):
    contract_id: str = Field(alias="contractId")
    turn_id: str = Field(alias="turnId")
    mode: ResearchFinalGateMode
    status: ResearchFinalGateStatus
    ok: bool
    block_intent: bool = Field(alias="blockIntent")
    approval_required_block_intent: bool = Field(alias="approvalRequiredBlockIntent")
    final_answer_blocking_enabled: Literal[False] = Field(
        default=False,
        alias="finalAnswerBlockingEnabled",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    cited_refs: tuple[str, ...] = Field(default=(), alias="citedRefs")
    output_link_digests: tuple[str, ...] = Field(default=(), alias="outputLinkDigests")
    citation_audit_result: CitationAuditResult | None = Field(
        default=None,
        alias="citationAuditResult",
    )
    extracted_claim_refs: tuple[ResearchClaimRef, ...] = Field(
        default=(),
        alias="extractedClaimRefs",
    )
    source_ledger: LocalResearchSourceLedger = Field(alias="sourceLedger")
    final_answer_digest: str = Field(alias="finalAnswerDigest")
    authority_flags: ResearchFinalGateAuthorityFlags = Field(
        default_factory=ResearchFinalGateAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("ok", "block_intent", "approval_required_block_intent", mode="before")
    @classmethod
    def _validate_bool(cls, value: object, info: object) -> object:
        field_name = getattr(info, "field_name", "research final gate boolean")
        return _validate_strict_bool(value, field_name)

    def public_projection(self) -> dict[str, object]:
        return {
            "contractId": _safe_public_ref(self.contract_id),
            "turnId": _safe_public_ref(self.turn_id),
            "mode": self.mode,
            "status": self.status,
            "ok": self.ok,
            "blockIntent": self.block_intent,
            "approvalRequiredBlockIntent": self.approval_required_block_intent,
            "finalAnswerBlockingEnabled": False,
            "reasonCodes": [_safe_public_ref(reason) for reason in self.reason_codes],
            "citedRefs": [_safe_source_ref(ref) for ref in self.cited_refs],
            "claimRefs": [_public_claim_ref(claim) for claim in self.extracted_claim_refs],
            "citationAudit": _public_citation_audit(self.citation_audit_result),
            "sourceRefs": [_public_source_ref(record) for record in self.source_ledger.snapshot()],
            "finalAnswerDigest": _public_digest(self.final_answer_digest),
            "outputLinkDigests": [_public_digest(value) for value in self.output_link_digests],
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }



def evaluate_research_final_gate(request: ResearchFinalGateRequest) -> ResearchFinalGateResult:
    effective_cited_refs = _dedupe(
        (
            *request.cited_refs,
            *_claim_cited_refs(request.extracted_claim_refs),
            *_final_answer_cited_refs(request.candidate_final_answer),
        )
    )
    audit_cited_refs = _dedupe(
        (
            *request.cited_refs,
            *_claim_cited_refs(request.extracted_claim_refs),
            *_final_answer_cited_refs(request.candidate_final_answer),
        )
    )
    final_answer_digest = _digest_text(request.candidate_final_answer)

    if request.mode == "off":
        return _result(
            request,
            status="skipped",
            ok=True,
            block_intent=False,
            approval_required_block_intent=False,
            reason_codes=("research_final_gate_off",),
            cited_refs=effective_cited_refs,
            output_link_digests=(),
            citation_audit_result=None,
            final_answer_digest=final_answer_digest,
        )

    citation_result: CitationAuditResult | None = None
    if audit_cited_refs:
        citation_result = audit_citations(
            CitationAuditRequest(
                contractId=request.contract_id,
                turnId=request.turn_id,
                citedRefs=audit_cited_refs,
                sourceLedger=request.source_ledger,
            )
        )

    reasons: list[str] = []
    reasons.extend(_citation_reason_codes(citation_result))
    reasons.extend(_malformed_final_answer_source_reason_codes(request.candidate_final_answer))
    reasons.extend(_unsupported_claim_reason_codes(request.extracted_claim_refs))
    reasons.extend(_fresh_source_reason_codes(request))
    if not effective_cited_refs and _looks_like_factual_claim(request.candidate_final_answer):
        reasons.append("factual_claim_missing_source_ref")
    unrepresented_links = _unrepresented_output_links(
        request.candidate_final_answer,
        request.source_ledger,
        turn_id=request.turn_id,
    )
    if unrepresented_links:
        reasons.append("output_link_not_in_source_ledger")

    reason_codes = tuple(sorted(dict.fromkeys(reasons)))
    output_link_digests = tuple(_digest_text(link) for link in unrepresented_links)
    if not reason_codes:
        return _result(
            request,
            status="passed",
            ok=True,
            block_intent=False,
            approval_required_block_intent=False,
            reason_codes=("research_final_gate_passed",),
            cited_refs=effective_cited_refs,
            output_link_digests=output_link_digests,
            citation_audit_result=citation_result,
            final_answer_digest=final_answer_digest,
        )

    if request.mode == "audit":
        return _result(
            request,
            status="audit_failed",
            ok=False,
            block_intent=False,
            approval_required_block_intent=False,
            reason_codes=reason_codes,
            cited_refs=effective_cited_refs,
            output_link_digests=output_link_digests,
            citation_audit_result=citation_result,
            final_answer_digest=final_answer_digest,
        )

    approval_required = request.mode == "approval_required_block"
    return _result(
        request,
        status="approval_required_block_intent" if approval_required else "local_block_intent",
        ok=False,
        block_intent=True,
        approval_required_block_intent=approval_required,
        reason_codes=reason_codes,
        cited_refs=effective_cited_refs,
        output_link_digests=output_link_digests,
        citation_audit_result=citation_result,
        final_answer_digest=final_answer_digest,
    )


def _result(
    request: ResearchFinalGateRequest,
    *,
    status: ResearchFinalGateStatus,
    ok: bool,
    block_intent: bool,
    approval_required_block_intent: bool,
    reason_codes: tuple[str, ...],
    cited_refs: tuple[str, ...],
    output_link_digests: tuple[str, ...],
    citation_audit_result: CitationAuditResult | None,
    final_answer_digest: str,
) -> ResearchFinalGateResult:
    return ResearchFinalGateResult(
        contractId=request.contract_id,
        turnId=request.turn_id,
        mode=request.mode,
        status=status,
        ok=ok,
        blockIntent=block_intent,
        approvalRequiredBlockIntent=approval_required_block_intent,
        finalAnswerBlockingEnabled=False,
        reasonCodes=reason_codes,
        citedRefs=cited_refs,
        outputLinkDigests=output_link_digests,
        citationAuditResult=citation_audit_result,
        extractedClaimRefs=request.extracted_claim_refs,
        sourceLedger=request.source_ledger,
        finalAnswerDigest=final_answer_digest,
        authorityFlags=ResearchFinalGateAuthorityFlags(),
    )


def _claim_cited_refs(claims: tuple[ResearchClaimRef, ...]) -> tuple[str, ...]:
    return tuple(ref for claim in claims for ref in claim.cited_refs)


def _final_answer_cited_refs(candidate_final_answer: str) -> tuple[str, ...]:
    return _dedupe(tuple(_FINAL_ANSWER_SOURCE_REF_RE.findall(candidate_final_answer)))


def _malformed_final_answer_source_reason_codes(candidate_final_answer: str) -> tuple[str, ...]:
    malformed_refs = tuple(
        ref
        for ref in _dedupe(tuple(_FINAL_ANSWER_ANY_SOURCE_REF_RE.findall(candidate_final_answer)))
        if _SOURCE_ID_RE.fullmatch(ref) is None
    )
    if not malformed_refs:
        return ()
    return ("malformed_source_ref",)


def _citation_reason_codes(result: CitationAuditResult | None) -> tuple[str, ...]:
    if result is None:
        return ()
    reasons: list[str] = []
    for item in result.audit_items:
        if item.status == "missing":
            reasons.append("missing_source_ref")
        elif item.status == "failure":
            reasons.append("uninspected_source_ref")
    return tuple(reasons)


def _unsupported_claim_reason_codes(claims: tuple[ResearchClaimRef, ...]) -> tuple[str, ...]:
    if any(not claim.cited_refs for claim in claims):
        return ("unsupported_claim_missing_citation_ref",)
    return ()


def _fresh_source_reason_codes(request: ResearchFinalGateRequest) -> tuple[str, ...]:
    reasons: list[str] = []
    records_by_turn_source_id = {
        record.source_id: record for record in request.source_ledger.sources_for_turn(request.turn_id)
    }
    for claim in request.extracted_claim_refs:
        if not claim.requires_fresh_source:
            continue
        fresh_records = tuple(
            record
            for ref in claim.fresh_source_refs
            if (record := records_by_turn_source_id.get(ref)) is not None
        )
        if not any(_is_fresh_inspected_source(record) for record in fresh_records):
            reasons.append("volatile_claim_missing_fresh_source")
    return tuple(reasons)


def _is_fresh_inspected_source(record: SourceLedgerRecord) -> bool:
    return record.inspected and record.evidence_type in {"SourceInspection", "Clock"}


def _unrepresented_output_links(
    candidate_final_answer: str,
    source_ledger: LocalResearchSourceLedger,
    *,
    turn_id: str,
) -> tuple[str, ...]:
    ledger_uris = {
        _normalize_url(record.uri)
        for record in source_ledger.sources_for_turn(turn_id)
        if record.uri.casefold().startswith(("http://", "https://"))
    }
    return tuple(
        link
        for link in _dedupe(tuple(_normalize_url(match.group(0)) for match in _URL_RE.finditer(candidate_final_answer)))
        if link not in ledger_uris
    )


def _normalize_url(value: str) -> str:
    return value.strip().rstrip(".,;:!?")


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _public_claim_ref(claim: ResearchClaimRef) -> dict[str, object]:
    claim_payload = "|".join(
        (
            claim.claim_id,
            ",".join(claim.cited_refs),
            str(claim.requires_fresh_source),
            ",".join(claim.fresh_source_refs),
        )
    )
    return {
        "claimId": _safe_public_ref(claim.claim_id),
        "citedRefs": [_safe_source_ref(ref) for ref in claim.cited_refs],
        "requiresFreshSource": _public_bool(claim.requires_fresh_source),
        "freshSourceRefs": [_safe_source_ref(ref) for ref in claim.fresh_source_refs],
        "claimDigest": _digest_text(claim_payload),
    }


def _public_citation_audit(result: CitationAuditResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    return {
        "ok": _public_bool(result.ok),
        "enforcement": "audit",
        "auditItems": [
            {
                "sourceId": _safe_audit_source_ref(getattr(item, "source_id", "unknown")),
                "status": _public_audit_item_status(getattr(item, "status", "failure")),
                "inspected": _public_bool(getattr(item, "inspected", False)),
                "evidenceType": _safe_optional_public_ref(getattr(item, "evidence_type", None)),
                "failureCode": _safe_optional_public_ref(getattr(item, "failure_code", None)),
            }
            for item in _public_audit_items(result)
        ],
        "blockMode": False,
        "finalAnswerMutated": False,
        "userVisibleEnforcementActions": [],
    }


def _looks_like_factual_claim(candidate_final_answer: str) -> bool:
    text = candidate_final_answer.strip()
    if not text:
        return False
    if _URL_RE.search(text):
        return True
    return _FACTUAL_CLAIM_RE.search(text) is not None


def _safe_public_ref(value: str) -> str:
    safe_value = str(value)
    if _SAFE_PUBLIC_REF_RE.fullmatch(safe_value) is None or _PRIVATE_REF_RE.search(safe_value):
        return f"ref:{_digest_text(safe_value).removeprefix('sha256:')[:24]}"
    return safe_value


def _safe_optional_public_ref(value: object) -> str | None:
    if value is None:
        return None
    return _safe_public_ref(str(value))


def _safe_source_ref(value: str) -> str:
    safe_value = str(value)
    if _SOURCE_ID_RE.fullmatch(safe_value) is not None:
        return safe_value
    return _safe_public_ref(safe_value)


def _safe_audit_source_ref(value: object) -> str:
    safe_value = str(value)
    if _SOURCE_ID_RE.fullmatch(safe_value) is not None:
        return safe_value
    return f"ref:{_digest_text(safe_value).removeprefix('sha256:')[:24]}"


def _public_digest(value: str) -> str:
    safe_value = str(value)
    if _SHA256_DIGEST_RE.fullmatch(safe_value) is not None:
        return safe_value
    return _digest_text(safe_value)


def _public_bool(value: object) -> bool:
    return value is True


def _public_audit_item_status(value: object) -> str:
    status = str(value)
    if status in {"pass", "failure", "missing"}:
        return status
    return "failure"


def _public_audit_items(result: CitationAuditResult) -> tuple[object, ...]:
    items = result.audit_items
    if isinstance(items, tuple):
        return items
    if isinstance(items, list):
        return tuple(items)
    return ()


def _public_source_ref(record: SourceLedgerRecord) -> dict[str, object]:
    return {
        "sourceId": _safe_source_ref(record.source_id),
        "status": "inspected" if record.inspected else "discovered",
        "inspected": record.inspected,
        "kind": record.kind,
        "evidenceType": record.evidence_type,
        "sourceDigest": _digest_text(
            "|".join(
                (
                    record.source_id,
                    record.kind,
                    record.evidence_type,
                    record.uri,
                    record.content_hash or "",
                )
            )
        ),
    }


__all__ = [
    "ResearchClaimRef",
    "ResearchFinalGateAuthorityFlags",
    "ResearchFinalGateMode",
    "ResearchFinalGateRequest",
    "ResearchFinalGateResult",
    "ResearchFinalGateStatus",
    "evaluate_research_final_gate",
]
