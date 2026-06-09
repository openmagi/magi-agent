"""Self-introspection tool (pull) — ``InspectSelfEvidence``.

PR2 consumer of the PR1 shared projection core
(:func:`magi_agent.introspection.projection.project_session_evidence`). It lets
the model read its OWN recorded runtime evidence for the current session so it
can truthfully answer questions like "did you really read X.pdf just now?" or
"did you follow the workflow?" instead of guessing.

Design
------
- The tool reuses PR1's projection — it does NOT re-categorize ledger entries.
- An ``EvidenceLedger`` is single-turn (all its entries share one ``turn_id``),
  so to cover a whole session the handler iterates EVERY ``EvidenceLedger`` in
  ``ToolContext.source_ledger`` and merges the per-ledger views into one
  ``SessionEvidenceView``. File-read evidence comes from the dedicated
  ``ReadLedger`` reachable via ``ToolContext.read_ledger`` (the clean seam used
  by ``tools/safety.py``).
- Pure / read-only w.r.t. agent state: it only reads evidence, never mutates it.
- Raw transcript is never emitted — only the compact projected summary.

Contract
--------
Input (``query_type`` is required; ``turn`` / ``ref`` optional):
    query_type: "files_read" | "tools_called" | "phases"
              | "verifier_verdicts" | "summary"
    turn:  str | None   # specific turn_id; None = whole session
    ref:   str | None   # post-filter on the relevant slice's identifier

Return — a compact dict, never raw transcript. ``scope`` and ``note`` are
ALWAYS present. For ``query_type != "summary"`` only the single requested slice
is included; for ``"summary"`` all four slices are included::

    {
      "scope": {"session_id": "...", "turns_covered": ["turn-4", "turn-5"]},
      "files_read": [{"path": "X.pdf", "sha256": "sha256:..", "turn": "turn-4", "bytes": 1234}],
      "tool_calls": [{"name": "Grep", "status": "ok", "turn": "turn-5"}],
      "phases":     [{"name": "B", "reached": true, "turn": "turn-5"}],
      "verdicts":   [{"stage": "tool_evidence_contract", "result": "pass", "turn": "turn-5"}],
      "note": "projection of session ledger; not raw transcript"
    }

``ref`` post-filter (case-insensitive substring) per slice:
  - files_read       → matched against ``path``
  - tools_called     → matched against ``name``
  - phases           → matched against ``name``
  - verifier_verdicts→ matched against ``stage``
  - summary          → applied to EVERY slice that has a natural identifier
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.introspection.projection import (
    FileReadView,
    PhaseView,
    SessionEvidenceView,
    SessionScopeView,
    ToolCallView,
    VerdictView,
    project_session_evidence,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.read_ledger import ReadLedger
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


QueryType = Literal[
    "files_read",
    "tools_called",
    "phases",
    "verifier_verdicts",
    "summary",
]

_VALID_QUERY_TYPES: frozenset[str] = frozenset(
    {"files_read", "tools_called", "phases", "verifier_verdicts", "summary"}
)
_VIEW_NOTE = "projection of session ledger; not raw transcript"

INSPECT_SELF_EVIDENCE_DESCRIPTION = (
    "Inspect your own recorded runtime evidence for this session — which files "
    "you actually read, which tools you called (and their status), which "
    "workflow phases you reached, and verifier verdicts. Use this to truthfully "
    "answer questions about your own prior actions (\"did you really read X?\", "
    "\"did you follow the workflow?\") instead of guessing. Returns a compact "
    "projection of the evidence ledger, never raw transcript."
)

INSPECT_SELF_EVIDENCE_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query_type"],
    "properties": {
        "query_type": {
            "type": "string",
            "enum": [
                "files_read",
                "tools_called",
                "phases",
                "verifier_verdicts",
                "summary",
            ],
            "description": (
                "Which evidence slice to return. 'summary' returns all slices; "
                "the others return just that slice (plus scope+note)."
            ),
        },
        "turn": {
            "type": ["string", "null"],
            "description": (
                "Optional turn_id to scope the projection to a single turn. "
                "Omit (or null) for the whole session."
            ),
        },
        "ref": {
            "type": ["string", "null"],
            "description": (
                "Optional case-insensitive substring filter applied to the "
                "relevant slice's identifier (file path / tool name / phase "
                "name / verdict stage)."
            ),
        },
    },
}


def project_context_session_evidence(context: ToolContext) -> SessionEvidenceView:
    """Merge every ``EvidenceLedger`` in ``context.source_ledger`` into one view.

    An ``EvidenceLedger`` is single-turn, so a whole-session view requires
    projecting across MULTIPLE ledgers. Non-``EvidenceLedger`` members of
    ``source_ledger`` (e.g. research source ledgers) are ignored. The dedicated
    ``ReadLedger`` on ``context.read_ledger`` (when present) supplies file-read
    evidence; it is paired with each EvidenceLedger projection but de-duplicated
    so files_read is not multiplied across ledgers.
    """
    read_ledger = _coerce_read_ledger(context.read_ledger)
    evidence_ledgers = _evidence_ledgers(context.source_ledger)

    if not evidence_ledgers:
        # No evidence ledgers reachable. Still surface file reads if a ReadLedger
        # is present, using a synthetic empty ledger to anchor the session id.
        session_id = context.session_id or "unknown-session"
        return _empty_view_with_optional_reads(session_id, read_ledger)

    merged_files: list[FileReadView] = []
    merged_tools: list[ToolCallView] = []
    merged_phases: list[PhaseView] = []
    merged_verdicts: list[VerdictView] = []
    merged_turns: list[str] = []
    session_id = ""

    # The ReadLedger is session-scoped and shared across the session's ledgers,
    # so attach it to only the FIRST projection to avoid duplicating file reads
    # once per EvidenceLedger.
    for index, ledger in enumerate(evidence_ledgers):
        view = project_session_evidence(
            ledger,
            read_ledger=read_ledger if index == 0 else None,
        )
        session_id = session_id or view.scope.session_id
        merged_turns.extend(view.scope.turns_covered)
        merged_files.extend(view.files_read)
        merged_tools.extend(view.tool_calls)
        merged_phases.extend(view.phases)
        merged_verdicts.extend(view.verdicts)

    return SessionEvidenceView(
        scope=SessionScopeView(
            sessionId=session_id or (context.session_id or "unknown-session"),
            turnsCovered=_distinct_preserving_order(merged_turns),
        ),
        filesRead=tuple(merged_files),
        toolCalls=tuple(merged_tools),
        phases=tuple(merged_phases),
        verdicts=tuple(merged_verdicts),
    )


def inspect_self_evidence(
    *,
    query_type: str,
    context: ToolContext,
    turn: str | None = None,
    ref: str | None = None,
) -> dict[str, object]:
    """Project the session evidence and return the requested compact slice(s).

    Pure / read-only. See module docstring for the exact contract.
    """
    view = project_context_session_evidence(context)

    if turn is not None:
        view = _restrict_view_to_turn(view, turn)

    result: dict[str, object] = {
        "scope": {
            "session_id": view.scope.session_id,
            "turns_covered": list(view.scope.turns_covered),
        },
        "note": _VIEW_NOTE,
    }

    want_all = query_type == "summary"
    if want_all or query_type == "files_read":
        result["files_read"] = _files_read_slice(view, ref)
    if want_all or query_type == "tools_called":
        result["tool_calls"] = _tool_calls_slice(view, ref)
    if want_all or query_type == "phases":
        result["phases"] = _phases_slice(view, ref)
    if want_all or query_type == "verifier_verdicts":
        result["verdicts"] = _verdicts_slice(view, ref)

    return result


class InspectSelfEvidenceToolHost:
    """Bind the ``InspectSelfEvidence`` tool handler to a ToolRegistry.

    Mirrors ``MemoryWriteToolHost``: the handler is ALWAYS bound (so an
    execution-time dispatch returns a structured ``blocked`` result rather than a
    KeyError), but the tool is only ADVERTISED to the model (registry
    ``enabled=True``) when ``config.enabled`` is True. When disabled the tool is
    bound-but-not-advertised — ``is_enabled``/``list_available`` omit it.
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = bool(enabled)

    def bind(self, registry: ToolRegistry) -> None:
        registration = registry.resolve_registration("InspectSelfEvidence")
        if registration is None:
            return  # manifest not registered — nothing to bind
        if registration.handler is not None:
            return  # already bound

        host = self  # capture for closure

        async def _handler(
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            return host._handle(arguments, context)

        registry.bind_handler(
            "InspectSelfEvidence",
            _handler,
            enabled_by_registry_policy=host.enabled,
        )

    def _handle(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if not self.enabled:
            return ToolResult(
                status="blocked",
                error_code="self_introspection_disabled",
                error_message="InspectSelfEvidence tool is not enabled (gate-off).",
                metadata={"toolName": "InspectSelfEvidence", "reason": "gate_off"},
            )

        query_type = arguments.get("query_type")
        if not isinstance(query_type, str) or query_type not in _VALID_QUERY_TYPES:
            return ToolResult(
                status="blocked",
                error_code="self_introspection_invalid_query_type",
                error_message=(
                    "query_type must be one of: files_read, tools_called, "
                    "phases, verifier_verdicts, summary."
                ),
                metadata={
                    "toolName": "InspectSelfEvidence",
                    "reason": "invalid_query_type",
                },
            )

        turn = _optional_str(arguments.get("turn"))
        ref = _optional_str(arguments.get("ref"))

        projection = inspect_self_evidence(
            query_type=query_type,
            context=context,
            turn=turn,
            ref=ref,
        )
        return ToolResult(
            status="ok",
            output=projection,
            llmOutput=projection,
            metadata={
                "toolName": "InspectSelfEvidence",
                "queryType": query_type,
            },
        )


def bind_inspect_self_evidence_handler(
    registry: ToolRegistry,
    *,
    enabled: bool | None = None,
) -> None:
    """Convenience binder resolving the env gate when ``enabled`` is None.

    ``enabled=None`` (default) reads ``MAGI_SELF_INTROSPECTION_ENABLED`` via the
    single env source of truth (default OFF). Pass an explicit bool to override
    (used by tests).
    """
    if enabled is None:
        from magi_agent.config.env import is_self_introspection_enabled

        enabled = is_self_introspection_enabled()
    InspectSelfEvidenceToolHost(enabled=enabled).bind(registry)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_read_ledger(value: object | None) -> ReadLedger | None:
    return value if isinstance(value, ReadLedger) else None


def _evidence_ledgers(source_ledger: Sequence[object]) -> tuple[EvidenceLedger, ...]:
    return tuple(item for item in source_ledger if isinstance(item, EvidenceLedger))


def _empty_view_with_optional_reads(
    session_id: str,
    read_ledger: ReadLedger | None,
) -> SessionEvidenceView:
    files_read: list[FileReadView] = []
    turns: list[str] = []
    if read_ledger is not None:
        for entry in read_ledger.iter_entries():
            if entry.session_id != session_id:
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
    return SessionEvidenceView(
        scope=SessionScopeView(
            sessionId=session_id,
            turnsCovered=_distinct_preserving_order(turns),
        ),
        filesRead=tuple(files_read),
    )


def _restrict_view_to_turn(view: SessionEvidenceView, turn: str) -> SessionEvidenceView:
    files = tuple(f for f in view.files_read if f.turn_id == turn)
    tools = tuple(t for t in view.tool_calls if t.turn_id == turn)
    phases = tuple(p for p in view.phases if p.turn_id == turn)
    verdicts = tuple(v for v in view.verdicts if v.turn_id == turn)
    turns_covered = tuple(t for t in view.scope.turns_covered if t == turn)
    return SessionEvidenceView(
        scope=SessionScopeView(
            sessionId=view.scope.session_id,
            turnsCovered=turns_covered,
        ),
        filesRead=files,
        toolCalls=tools,
        phases=phases,
        verdicts=verdicts,
    )


def _files_read_slice(view: SessionEvidenceView, ref: str | None) -> list[dict[str, object]]:
    return [
        {
            "path": entry.path,
            "sha256": entry.sha256,
            "turn": entry.turn_id,
            "bytes": entry.bytes,
        }
        for entry in view.files_read
        if _ref_matches(entry.path, ref)
    ]


def _tool_calls_slice(view: SessionEvidenceView, ref: str | None) -> list[dict[str, object]]:
    return [
        {"name": entry.name, "status": entry.status, "turn": entry.turn_id}
        for entry in view.tool_calls
        if _ref_matches(entry.name, ref)
    ]


def _phases_slice(view: SessionEvidenceView, ref: str | None) -> list[dict[str, object]]:
    return [
        {"name": entry.name, "reached": entry.reached, "turn": entry.turn_id}
        for entry in view.phases
        if _ref_matches(entry.name, ref)
    ]


def _verdicts_slice(view: SessionEvidenceView, ref: str | None) -> list[dict[str, object]]:
    return [
        {"stage": entry.stage, "result": entry.result, "turn": entry.turn_id}
        for entry in view.verdicts
        if _ref_matches(entry.stage, ref)
    ]


def _distinct_preserving_order(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _ref_matches(value: str, ref: str | None) -> bool:
    if ref is None:
        return True
    return ref.casefold() in value.casefold()


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


__all__ = [
    "INSPECT_SELF_EVIDENCE_DESCRIPTION",
    "INSPECT_SELF_EVIDENCE_INPUT_SCHEMA",
    "InspectSelfEvidenceToolHost",
    "QueryType",
    "bind_inspect_self_evidence_handler",
    "inspect_self_evidence",
    "project_context_session_evidence",
]
