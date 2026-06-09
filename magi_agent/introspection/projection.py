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
from magi_agent.tools.read_ledger import ReadLedger

# File-read source decision (verified against the real code):
#   The dedicated ``ReadLedger`` (tools/read_ledger.py) is a SEPARATE structure
#   NOT reachable from ``EvidenceLedger``. File reads MAY also surface in the
#   EvidenceLedger as evidence records (e.g. SourceInspection / FileDeliver
#   carrying path/sha256/sizeBytes in ``fields``). So PR1 projects file reads
#   from the EvidenceLedger when present AND accepts an OPTIONAL ``read_ledger``
#   to project the dedicated read-state primitive. Both kept pure / read-only.

_VIEW_NOTE = "projection of session ledger; not raw transcript"

# Evidence-record ``type`` values whose ``fields`` describe a file read.
_FILE_READ_EVIDENCE_TYPES = frozenset({"SourceInspection", "FileDeliver"})
# ``fields`` keys (and reasonable aliases) carrying file-read attributes.
_FILE_PATH_KEYS = ("path", "filePath", "file_path")
_FILE_SHA_KEYS = ("sha256", "digest", "sha")
_FILE_BYTES_KEYS = ("sizeBytes", "size_bytes", "bytes")
# ``fields`` keys that name a workflow phase reached this turn.
_PHASE_KEYS = ("phase", "phaseName", "phase_name")

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
        read_ledger: optional dedicated read-state primitive. File reads that
            live only in the ``ReadLedger`` are projected when it is provided.

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
        _categorize_ledger_entry(entry, files_read, tool_calls, phases, verdicts, turns)

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


def _categorize_ledger_entry(
    entry: EvidenceLedgerEntry,
    files_read: list[FileReadView],
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
    fields = record.get("fields")
    fields = fields if isinstance(fields, Mapping) else {}
    record_type = record.get("type")

    file_view = _file_read_view(record_type, fields, entry.turn_id)
    if file_view is not None:
        files_read.append(file_view)

    phase_view = _phase_view(fields, entry.turn_id)
    if phase_view is not None:
        phases.append(phase_view)

    # Categorization is exclusive: a record already projected as a file read or
    # a phase marker is not double-counted as a generic tool call.
    if file_view is None and phase_view is None:
        tool_view = _tool_call_view(record, entry.turn_id)
        if tool_view is not None:
            tool_calls.append(tool_view)


def _file_read_view(
    record_type: object,
    fields: Mapping[str, object],
    turn_id: str,
) -> FileReadView | None:
    if record_type not in _FILE_READ_EVIDENCE_TYPES:
        return None
    path = _first_str(fields, _FILE_PATH_KEYS)
    sha = _first_str(fields, _FILE_SHA_KEYS)
    if path is None or sha is None:
        return None
    return FileReadView(
        path=path,
        sha256=sha,
        turnId=turn_id,
        bytes=_first_int(fields, _FILE_BYTES_KEYS) or 0,
    )


def _phase_view(fields: Mapping[str, object], turn_id: str) -> PhaseView | None:
    name = _first_str(fields, _PHASE_KEYS)
    if name is None:
        return None
    # Presence of a phase marker in the immutable ledger == the phase was reached.
    return PhaseView(name=name, reached=True, turnId=turn_id)


def _tool_call_view(record: Mapping[str, object], turn_id: str) -> ToolCallView | None:
    source = record.get("source")
    if not isinstance(source, Mapping):
        return None
    tool_name = source.get("toolName")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    status = record.get("status")
    return ToolCallView(
        name=tool_name,
        status=status if isinstance(status, str) else "unknown",
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
    # Read-only snapshot of the ReadLedger's private entries. We avoid mutation
    # and emit only projected summary fields (path / digest / size / turn).
    for entry in read_ledger._entries:  # noqa: SLF001 — read-only projection seam
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


def _first_str(fields: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = fields.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_int(fields: Mapping[str, object], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = fields.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


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
