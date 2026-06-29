"""Live-path construction of a research final-gate request (WS6 PR6a).

Design: WS6 deterministic-verification activation, PR6a (research-governance
promote to soft ``local_block_intent``).

The live CLI engine never constructed a :class:`ResearchFinalGateRequest`; the
only production caller was the read-only canary, which carefully built a
letter-prefixed ``turn_id`` and ``src_N``-shaped cited refs. A live turn does
NOT carry those shapes: its ``turn_id`` is commonly UUID/hex (frequently
digit-starting, which FAILS ``_SAFE_GATE_ID_RE``) and its raw source references
are URLs / content hashes (which FAIL ``_SOURCE_ID_RE``). Passing those raw
values to the strict frozen-model validators RAISES ``ValueError``.

This module normalizes BEFORE construction, never after: it sanitizes the
``turn_id``/``contract_id`` via ``_safe_public_ref`` and records every
source-like evidence record through ``LocalResearchSourceLedger.record_source``
(which auto-assigns ``src_N``) so the cited refs are stable metadata refs. The
SAME sanitized ``turn_id`` is used both for the request and for the ledger
records, so ``evaluate_research_final_gate`` (which filters the ledger via
``sources_for_turn(request.turn_id)``) does not silently drop every source.

A construction ``ValueError`` here would be a wiring bug, not a runtime
condition. Callers MUST NOT rely on a fail-open ``try/except`` to swallow such a
raise (that path degrades to the hard refuse WS6 exists to remove).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from magi_agent.evidence.research_final_gate import (
    ResearchFinalGateRequest,
    ResearchFinalGateResult,
    _digest_text,
    _safe_public_ref,
    evaluate_research_final_gate,
)
from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceEvidenceType,
    SourceLedgerKind,
)

_RESEARCH_SOURCE_EVIDENCE_TYPES: frozenset[str] = frozenset(
    {"WebSearch", "KnowledgeSearch", "SourceInspection", "Clock"}
)
_SOURCE_EVIDENCE_ALIASES: Mapping[str, SourceEvidenceType] = {
    "sourceinspection": "SourceInspection",
    "source_inspected": "SourceInspection",
    "sourceinspected": "SourceInspection",
    "websearch": "WebSearch",
    "knowledgesearch": "KnowledgeSearch",
    "clock": "Clock",
}
_EVIDENCE_TYPE_TO_KIND: Mapping[SourceEvidenceType, SourceLedgerKind] = {
    "WebSearch": "web_search",
    "KnowledgeSearch": "kb",
    "SourceInspection": "external_doc",
    "Clock": "clock",
}


def _short_digest(value: str) -> str:
    return _digest_text(value).removeprefix("sha256:")[:16]


def _record_field(record: object, *keys: str) -> object:
    for key in keys:
        if isinstance(record, Mapping):
            if key in record and record[key] is not None:
                return record[key]
        else:
            value = getattr(record, key, None)
            if value is not None:
                return value
    return None


def _evidence_type_of(record: object) -> SourceEvidenceType | None:
    raw = _record_field(record, "type", "evidence_type", "evidenceType")
    if not isinstance(raw, str):
        return None
    if raw in _RESEARCH_SOURCE_EVIDENCE_TYPES:
        return raw  # type: ignore[return-value]
    return _SOURCE_EVIDENCE_ALIASES.get(raw.strip().lower())


def _tool_name_of(record: object) -> str:
    source = _record_field(record, "source")
    if isinstance(source, Mapping):
        for key in ("toolName", "tool_name"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return "ResearchSourceInspect"


def _source_payload(record: object, *, turn_id: str, index: int) -> dict[str, object] | None:
    evidence_type = _evidence_type_of(record)
    if evidence_type is None:
        return None
    payload: dict[str, object] = {
        "turnId": turn_id,
        "toolName": _tool_name_of(record),
        "evidenceType": evidence_type,
        "kind": _EVIDENCE_TYPE_TO_KIND[evidence_type],
        # A safe synthetic locator: never the raw source URL, so the answer's
        # actual cited URLs stay correctly unrepresented unless they were truly
        # inspected. The ledger only needs a valid non-empty uri to assign src_N.
        "uri": f"ref:live-source:{_short_digest(f'{turn_id}:{index}')}",
        "inspected": True,
    }
    observed_at = _record_field(record, "observedAt", "observed_at", "inspectedAt", "inspected_at")
    if isinstance(observed_at, (int, float)) and not isinstance(observed_at, bool):
        payload["inspectedAt"] = observed_at
    return payload


def build_live_research_final_gate_request(
    *,
    contract_id: str,
    turn_id: str,
    session_id: str,
    final_text: str,
    evidence_records: Sequence[object] | None,
) -> ResearchFinalGateRequest:
    """Build a normalized ``ResearchFinalGateRequest`` for the live engine path.

    Sanitizes ``turn_id``/``contract_id`` to gate-safe public refs and records
    each source-like evidence record into a fresh source ledger so the cited
    refs are ``src_N``-shaped. The request is constructed only from normalized
    values, so the strict field validators never see raw live data.
    """
    safe_turn_id = _safe_public_ref(turn_id)
    safe_contract_id = _safe_public_ref(contract_id)
    ledger = LocalResearchSourceLedger(
        ledgerId=f"ledger-live-{_short_digest(turn_id)}",
        sessionId=f"session-live-{_short_digest(session_id)}",
        turnId=safe_turn_id,
        agentRole="research",
    )
    cited_refs: list[str] = []
    for index, record in enumerate(evidence_records or ()):
        payload = _source_payload(record, turn_id=safe_turn_id, index=index)
        if payload is None:
            continue
        recorded = ledger.record_source(payload)
        cited_refs.append(recorded.source_id)
    return ResearchFinalGateRequest(
        contractId=safe_contract_id,
        turnId=safe_turn_id,
        mode="local_block_intent",
        candidateFinalAnswer=final_text,
        citedRefs=tuple(dict.fromkeys(cited_refs)),
        sourceLedger=ledger,
    )


def evaluate_live_research_final_gate(
    *,
    contract_id: str,
    turn_id: str,
    session_id: str,
    final_text: str,
    evidence_records: Sequence[object] | None,
) -> ResearchFinalGateResult:
    """Construct (normalized) and evaluate the live research final gate."""
    request = build_live_research_final_gate_request(
        contract_id=contract_id,
        turn_id=turn_id,
        session_id=session_id,
        final_text=final_text,
        evidence_records=evidence_records,
    )
    return evaluate_research_final_gate(request)


__all__ = [
    "build_live_research_final_gate_request",
    "evaluate_live_research_final_gate",
]
