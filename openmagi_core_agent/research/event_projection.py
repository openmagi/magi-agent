from __future__ import annotations

from collections.abc import Iterable, Sequence
from hashlib import sha256
from itertools import islice

from openmagi_core_agent.evidence.citation_audit import CitationAuditResult
from openmagi_core_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)
from openmagi_core_agent.runtime.public_events import (
    PublicEvent,
    authorize_rule_check_event,
    rule_check_event,
)

from .source_proof import (
    ResearchSourceProofVerdict,
    project_research_source_proof_verdicts,
)


_CITATION_STATUSES = ("pass", "failure", "missing")
_CITATION_GATE_RULE_ID = "claim-citation-gate"
_MAX_PROJECTED_EVENTS = 50


def project_citation_audit_rule_events(
    result: CitationAuditResult,
    *,
    evidence_refs: Sequence[object] = (),
    runtime_authority: RuntimeIssueAuthority | None = None,
) -> tuple[PublicEvent, ...]:
    counts = _citation_counts(result)
    consistent = _citation_result_is_receipt_backed(result)
    evidence_ref = _first_safe_event_ref(evidence_refs)
    detail = (
        f"citation audit status={'ok' if consistent else result.verdict.state}: "
        f"checked={len(result.audit_items)} "
        f"passed={counts['pass']} "
        f"failed={counts['failure']} "
        f"missing={counts['missing']}"
    )
    if result.ok and not consistent:
        detail = f"{detail} consistency=inconsistent"
    event = rule_check_event(
        rule_id=_CITATION_GATE_RULE_ID,
        verdict=_citation_rule_verdict(result, consistent=consistent, evidence_ref=evidence_ref),
        detail=detail,
        event_family="citation_gate_alias",
    )
    if evidence_ref is not None:
        event["evidenceRef"] = evidence_ref
    if (
        event["verdict"] != "pending"
        and evidence_ref is not None
        and runtime_authority is not None
    ):
        require_runtime_issue_authority(
            runtime_authority,
            scope="citation_rule_check",
        )
        authorize_rule_check_event(event)
    return (event,)


def project_source_proof_rule_events(
    verdicts: Iterable[ResearchSourceProofVerdict],
) -> tuple[PublicEvent, ...]:
    events: list[PublicEvent] = []
    limited_verdicts = tuple(islice(verdicts, _MAX_PROJECTED_EVENTS))
    for projection in project_research_source_proof_verdicts(limited_verdicts):
        source_ref_id = _string(projection.get("sourceRefId")) or "source"
        source_verdict = _string(projection.get("verdict"))
        reason_code = _string(projection.get("reasonCode")) or "unknown"
        freshness = _string(projection.get("freshnessVerdict")) or "not_checked"
        span_count = _span_count(projection.get("spanRefs"))
        rule_id = _digest_rule_id(
            "source",
            f"{source_ref_id}:{reason_code}:{freshness}",
        )
        detail = (
            f"source proof status={source_verdict or 'unknown'}: "
            f"source={source_ref_id} "
            f"reason={reason_code} "
            f"freshness={freshness} "
            f"spans={span_count}"
        )
        event = rule_check_event(
            rule_id=rule_id,
            verdict="ok" if source_verdict == "allowed" else "violation",
            detail=detail,
        )
        events.append(event)
    return tuple(events)


def _citation_rule_verdict(
    result: CitationAuditResult,
    *,
    consistent: bool,
    evidence_ref: str | None,
) -> str:
    if evidence_ref is None:
        return "pending"
    if consistent:
        return "ok"
    if not result.ok or not result.verdict.ok or result.verdict.state in {"failed", "missing"}:
        return "violation"
    return "pending"


def _citation_counts(result: CitationAuditResult) -> dict[str, int]:
    return {
        status: sum(1 for item in result.audit_items if item.status == status)
        for status in _CITATION_STATUSES
    }


def _citation_result_is_receipt_backed(result: CitationAuditResult) -> bool:
    pass_source_ids = tuple(
        item.source_id for item in result.audit_items if item.status == "pass"
    )
    return (
        result.ok
        and result.verdict.ok
        and result.verdict.state == "pass"
        and bool(result.audit_items)
        and all(item.status == "pass" for item in result.audit_items)
        and all(
            _evidence_record_matches_inspected_source(record, source_id)
            for source_id in pass_source_ids
            for record in result.verdict.matched_evidence
            if _evidence_record_source_ids(record) and source_id in _evidence_record_source_ids(record)
        )
        and all(
            any(
                _evidence_record_matches_inspected_source(record, source_id)
                for record in result.verdict.matched_evidence
            )
            for source_id in pass_source_ids
        )
        and not result.verdict.failures
        and not result.verdict.missing_requirements
    )


def _evidence_record_matches_inspected_source(
    record: object,
    source_id: str,
) -> bool:
    return (
        hasattr(record, "type")
        and getattr(record, "type") == "SourceInspection"
        and getattr(record, "status") == "ok"
        and source_id in _evidence_record_source_ids(record)
        and _evidence_record_inspected(record)
    )


def _evidence_record_source_ids(record: object) -> tuple[str, ...]:
    fields = getattr(record, "fields", {})
    if not isinstance(fields, dict):
        try:
            fields = dict(fields)
        except (TypeError, ValueError):
            return ()
    source_ids = fields.get("sourceIds")
    if isinstance(source_ids, list | tuple):
        ids = tuple(item for item in source_ids if isinstance(item, str) and item.strip())
        if ids:
            return ids
    source_id = fields.get("sourceId")
    return (source_id,) if isinstance(source_id, str) and source_id.strip() else ()


def _evidence_record_inspected(record: object) -> bool:
    fields = getattr(record, "fields", {})
    if not isinstance(fields, dict):
        try:
            fields = dict(fields)
        except (TypeError, ValueError):
            return False
    return fields.get("inspected") is True


def _digest_rule_id(prefix: str, value: str) -> str:
    return f"{prefix}:sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def _first_safe_event_ref(values: Sequence[object]) -> str | None:
    for value in values:
        if isinstance(value, str) and _is_safe_event_ref(value):
            return value
    return None


def _is_safe_event_ref(value: str) -> bool:
    candidate = value.strip()
    if candidate.startswith("receipt:"):
        candidate = candidate.removeprefix("receipt:")
    return (
        len(candidate) == 71
        and candidate.startswith("sha256:")
        and all(char in "0123456789abcdefABCDEF" for char in candidate.removeprefix("sha256:"))
    )


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _span_count(value: object) -> int:
    if not isinstance(value, list | tuple):
        return 0
    return len(value)


__all__ = [
    "project_citation_audit_rule_events",
    "project_source_proof_rule_events",
]
