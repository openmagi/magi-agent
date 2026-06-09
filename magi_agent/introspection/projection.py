"""Shared, pure projection of the session evidence ledger into a lean view.

This module is the SHARED CORE consumed by later PRs:
  - PR2 self-introspection tool (pull): the model reads its own evidence.
  - PR3/PR4 pre-egress verification gate (push): a gate checks the draft answer
    against the same view.

Both surfaces take the *same* ``SessionEvidenceView`` so the truth the model
sees == the truth the gate sees (consistency by construction).

Guarantees:
  - Pure / deterministic / read-only over ``EvidenceLedger.entries`` (and an
    optional ``ReadLedger``). Zero side effects.
  - Never emits raw payloads or transcript text — only the projected summary
    fields below.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.ledger import EvidenceLedger, EvidenceLedgerEntry
from magi_agent.introspection.mapping import tool_call_from_evidence_record
from magi_agent.tools.read_ledger import ReadLedger

# File-read source decision (verified against the real producers):
#   The dedicated ``ReadLedger`` (tools/read_ledger.py) is the ONLY real source
#   of file-read evidence. It is a SEPARATE structure NOT reachable from
#   ``EvidenceLedger``; its entries carry the genuine path/digest/size/turn
#   fields. The EvidenceLedger does NOT carry file-read attributes:
#   ``SourceInspection`` records emit only {sourceId, sourceIds, sourceKind,
#   inspected} (see evidence/source_ledger.py) and ``FileDeliver`` is a deferred
#   metadata-only tool projection. So file reads are projected EXCLUSIVELY from
#   the optional ``read_ledger`` parameter, kept pure / read-only.

_VIEW_NOTE = "projection of session ledger; not raw transcript"

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
)


class SessionScopeView(BaseModel):
    model_config = _MODEL_CONFIG

    session_id: str = Field(alias="sessionId")
    turns_covered: tuple[str, ...] = Field(alias="turnsCovered")


class FileReadView(BaseModel):
    model_config = _MODEL_CONFIG

    path: str
    sha256: str
    turn_id: str = Field(alias="turnId")
    bytes: int


class ToolCallView(BaseModel):
    model_config = _MODEL_CONFIG

    name: str
    status: str
    turn_id: str = Field(alias="turnId")


class PhaseView(BaseModel):
    model_config = _MODEL_CONFIG

    name: str
    reached: bool
    turn_id: str = Field(alias="turnId")


class VerdictView(BaseModel):
    model_config = _MODEL_CONFIG

    stage: str
    result: str
    turn_id: str = Field(alias="turnId")


class SessionEvidenceView(BaseModel):
    """Stable, lean projection of one session's evidence ledger."""

    model_config = _MODEL_CONFIG

    scope: SessionScopeView
    files_read: tuple[FileReadView, ...] = Field(default=(), alias="filesRead")
    tool_calls: tuple[ToolCallView, ...] = Field(default=(), alias="toolCalls")
    phases: tuple[PhaseView, ...] = ()
    verdicts: tuple[VerdictView, ...] = ()
    note: str = _VIEW_NOTE


def project_session_evidence(
    ledger: EvidenceLedger,
    *,
    turn_filter: str | None = None,
    read_ledger: ReadLedger | None = None,
) -> SessionEvidenceView:
    """Project the immutable evidence ledger into a lean ``SessionEvidenceView``.

    Pure, deterministic, read-only — never mutates inputs and never emits raw
    payloads.

    Args:
        ledger: the primary in-memory ``EvidenceLedger`` (its ``entries`` tuple
            is read directly; no accessor methods are required).
        turn_filter: restrict the projection to a single ``turn_id``. ``None``
            (default) projects the whole session.
        read_ledger: optional dedicated read-state primitive — the real source
            of file-read evidence. When ``None``, ``files_read`` is empty.

    Returns:
        A frozen ``SessionEvidenceView``.
    """
    files_read: list[FileReadView] = []
    tool_calls: list[ToolCallView] = []
    phases: list[PhaseView] = []
    verdicts: list[VerdictView] = []
    turns: list[str] = []

    for entry in ledger.entries:
        if turn_filter is not None and entry.turn_id != turn_filter:
            continue
        _categorize_ledger_entry(entry, tool_calls, phases, verdicts, turns)

    if read_ledger is not None:
        _project_read_ledger(read_ledger, ledger.session_id, turn_filter, files_read, turns)

    return SessionEvidenceView(
        scope=SessionScopeView(
            sessionId=ledger.session_id,
            turnsCovered=_distinct_preserving_order(turns),
        ),
        filesRead=tuple(files_read),
        toolCalls=tuple(tool_calls),
        phases=tuple(phases),
        verdicts=tuple(verdicts),
    )


# Phase markers are emitted by the Stage 3 producer
# (``LocalToolEvidenceCollector.record_phase_reached``) as evidence_records of
# this type carrying ``fields.phaseName`` / ``fields.reached`` and NO
# ``source.toolName`` (so the tool-call normalizer skips them). The egress seam
# (transport/chat.py) has no EvidenceLedger, so it still yields ``phases=()``.
_PHASE_REACHED_RECORD_TYPE = "custom:PhaseReached"


def _categorize_ledger_entry(
    entry: EvidenceLedgerEntry,
    tool_calls: list[ToolCallView],
    phases: list[PhaseView],
    verdicts: list[VerdictView],
    turns: list[str],
) -> None:
    turns.append(entry.turn_id)
    if entry.kind == "verifier_verdict":
        verdicts.append(_verdict_view(entry))
        return
    if entry.kind != "evidence_record":
        return
    record = entry.payload.get("record")
    if not isinstance(record, Mapping):
        return

    # Phase markers are evidence_records distinguished by their ``type``. They
    # carry no ``source.toolName``, so categorize them BEFORE the tool-call
    # normalizer (which would return None for them anyway) and short-circuit.
    if record.get("type") == _PHASE_REACHED_RECORD_TYPE:
        phase_view = _phase_view(record, entry.turn_id)
        if phase_view is not None:
            phases.append(phase_view)
        return

    # Tool-call projection is delegated to the SHARED normalization helper
    # (introspection/mapping.py) so this PULL seam and the egress PUSH seam
    # (transport/chat.py) emit an identical ``ToolCallView`` shape + canonical
    # status vocabulary even though they read from different producers.
    tool_view = tool_call_from_evidence_record(record, entry.turn_id)
    if tool_view is not None:
        tool_calls.append(tool_view)


def _phase_view(record: Mapping[str, object], turn_id: str) -> PhaseView | None:
    fields = record.get("fields")
    if not isinstance(fields, Mapping):
        return None
    name = fields.get("phaseName")
    if not isinstance(name, str) or not name:
        return None
    reached = fields.get("reached")
    return PhaseView(
        name=name,
        reached=bool(reached) if isinstance(reached, bool) else True,
        turnId=turn_id,
    )


def _verdict_view(entry: EvidenceLedgerEntry) -> VerdictView:
    payload = entry.payload
    contract_id = payload.get("contractId")
    state = payload.get("state")
    return VerdictView(
        stage=contract_id if isinstance(contract_id, str) and contract_id else "unknown",
        result=state if isinstance(state, str) and state else "unknown",
        turnId=entry.turn_id,
    )


def _project_read_ledger(
    read_ledger: ReadLedger,
    session_id: str,
    turn_filter: str | None,
    files_read: list[FileReadView],
    turns: list[str],
) -> None:
    # Read-only, lock-guarded snapshot of the ReadLedger's entries via its public
    # ``iter_entries()`` accessor (no private ``_entries`` access). Concurrent
    # record_read() cannot mutate the snapshot mid-iteration; we emit only
    # projected summary fields (path / digest / size / turn).
    for entry in read_ledger.iter_entries():
        if entry.session_id != session_id:
            continue
        if turn_filter is not None and entry.turn_id != turn_filter:
            continue
        turns.append(entry.turn_id)
        files_read.append(
            FileReadView(
                path=entry.path,
                sha256=entry.digest,
                turnId=entry.turn_id,
                bytes=entry.size_bytes,
            )
        )


def _distinct_preserving_order(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


__all__ = [
    "FileReadView",
    "PhaseView",
    "SessionEvidenceView",
    "SessionScopeView",
    "ToolCallView",
    "VerdictView",
    "project_session_evidence",
]
