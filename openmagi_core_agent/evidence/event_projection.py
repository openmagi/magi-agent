from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from itertools import islice
from typing import Literal, Sequence, cast

from openmagi_core_agent.evidence.reports import public_evidence_verdict_report
from openmagi_core_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)
from openmagi_core_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerRecord,
)
from openmagi_core_agent.evidence.types import EvidenceContractVerdict
from openmagi_core_agent.harness.verifier_bus import VerifierResultMetadata
from openmagi_core_agent.runtime.public_events import (
    PublicEvent,
    RuleVerdict,
    SourceKind,
    authorize_rule_check_event,
    source_inspected_event,
    rule_check_event,
)


_PUBLIC_SOURCE_KINDS = frozenset(
    {
        "web_search",
        "web_fetch",
        "browser",
        "kb",
        "file",
        "external_repo",
        "external_doc",
        "subagent_result",
    }
)
_SOURCE_REF_PREFIX = "ref:"
_MAX_PROJECTED_EVENTS = 50


def project_source_ledger_events(
    ledger_or_records: LocalResearchSourceLedger | Iterable[SourceLedgerRecord],
) -> tuple[PublicEvent, ...]:
    records = (
        ledger_or_records.snapshot()
        if isinstance(ledger_or_records, LocalResearchSourceLedger)
        else tuple(islice(ledger_or_records, _MAX_PROJECTED_EVENTS))
    )
    events: list[PublicEvent] = []
    for record in records[:_MAX_PROJECTED_EVENTS]:
        event = project_source_ledger_record_event(record)
        if event is not None:
            events.append(event)
        if len(events) >= _MAX_PROJECTED_EVENTS:
            break
    return tuple(events)


def project_source_ledger_record_event(record: SourceLedgerRecord) -> PublicEvent | None:
    if not record.inspected or record.evidence_type != "SourceInspection":
        return None
    if record.kind not in _PUBLIC_SOURCE_KINDS:
        return None
    evidence_ref = _source_evidence_ref(record)
    if evidence_ref is None:
        return None
    return source_inspected_event(
        source_id=record.source_id,
        kind=cast(SourceKind, record.kind),
        uri=f"{_SOURCE_REF_PREFIX}{record.source_id}",
        content_hash=evidence_ref,
        content_type=record.content_type,
        trust_tier=record.trust_tier or "unknown",
        turn_id=record.turn_id,
        tool_name=record.tool_name,
        tool_use_id=record.tool_use_id,
        inspected_at=record.inspected_at,
    )


def project_evidence_verdict_rule_event(
    verdict: EvidenceContractVerdict,
    *,
    rule_prefix: str = "evidence",
) -> PublicEvent:
    report = public_evidence_verdict_report(verdict)
    detail = (
        f"evidence verdict state={report.state}: "
        f"matched={len(report.matched_evidence)} "
        f"missing={len(report.missing_requirements)} "
        f"failures={len(report.failures)} "
        f"enforcement={report.enforcement}"
    )
    return rule_check_event(
        rule_id=_digest_rule_id(rule_prefix, report.contract_id),
        verdict=_evidence_rule_verdict(verdict),
        detail=detail,
    )


def project_verifier_result_rule_event(
    result: VerifierResultMetadata,
    *,
    evidence_refs: Sequence[object] = (),
    runtime_authority: RuntimeIssueAuthority | None = None,
) -> PublicEvent:
    safe_ref_count = sum(1 for value in evidence_refs if _is_safe_event_ref(value))
    event = rule_check_event(
        rule_id=_digest_rule_id("verifier", result.verifier_id),
        verdict=_verifier_rule_verdict(result.status, has_evidence=safe_ref_count > 0),
        detail=f"verifier status={result.status}",
    )
    evidence_ref = _first_safe_event_ref(evidence_refs)
    if evidence_ref is not None:
        event["evidenceRef"] = evidence_ref
    if (
        event["verdict"] != "pending"
        and evidence_ref is not None
        and runtime_authority is not None
    ):
        require_runtime_issue_authority(
            runtime_authority,
            scope="verifier_result_rule_check",
        )
        authorize_rule_check_event(event)
    return event


def _source_evidence_ref(record: SourceLedgerRecord) -> str | None:
    if _is_safe_digest_ref(record.content_hash):
        return record.content_hash
    return None


def _is_safe_digest_ref(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return len(value) == 71 and value.startswith("sha256:") and all(
        char in "0123456789abcdefABCDEF" for char in value.removeprefix("sha256:")
    )


def _evidence_rule_verdict(verdict: EvidenceContractVerdict) -> RuleVerdict:
    if _evidence_success_is_receipt_backed(verdict):
        return "ok"
    if verdict.state == "audit":
        return "pending"
    return "violation"


def _evidence_success_is_receipt_backed(verdict: EvidenceContractVerdict) -> bool:
    if (
        not verdict.ok
        or verdict.state != "pass"
        or not verdict.matched_evidence
        or verdict.failures
        or verdict.missing_requirements
        or not verdict.requirement_coverage
    ):
        return False
    ok_types = {
        record.type
        for record in verdict.matched_evidence
        if record.status == "ok"
    }
    return set(verdict.requirement_coverage).issubset(ok_types)


def _verifier_rule_verdict(
    status: Literal["pass", "failed", "missing", "approval_required", "audit"],
    *,
    has_evidence: bool = False,
) -> RuleVerdict:
    if status == "pass":
        return "ok" if has_evidence else "pending"
    if status == "audit":
        return "pending"
    return "violation"


def _is_safe_event_ref(value: object) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if _is_safe_digest_ref(candidate):
        return True
    if candidate.startswith("receipt:") and _is_safe_digest_ref(candidate.removeprefix("receipt:")):
        return True
    if candidate.startswith("result:") and _is_safe_digest_ref(candidate.removeprefix("result:")):
        return True
    return False


def _first_safe_event_ref(values: Sequence[object]) -> str | None:
    for value in values:
        if isinstance(value, str) and _is_safe_event_ref(value):
            return value.strip()
    return None


def _digest_rule_id(prefix: str, value: str) -> str:
    return f"{prefix}:sha256:{sha256(value.encode('utf-8')).hexdigest()}"


__all__ = [
    "project_evidence_verdict_rule_event",
    "project_source_ledger_events",
    "project_source_ledger_record_event",
    "project_verifier_result_rule_event",
]
