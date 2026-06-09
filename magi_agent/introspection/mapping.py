"""Shared, pure normalization of tool-call evidence into ``ToolCallView``.

THE TWO-PRODUCER REALITY (verified against the real runtime seams):

``tool_calls`` in a ``SessionEvidenceView`` are produced at TWO distinct points
in the runtime, from TWO different data structures, and the two seams CANNOT be
collapsed into a single producer:

  1. Mid-turn introspection (PULL): ``InspectSelfEvidence`` projects the live
     ``EvidenceLedger`` evidence_records (see ``projection.py``). Each record
     carries ``source.toolName`` + an ``EvidenceStatus`` ("ok"|"failed"|
     "unknown").

  2. Pre-egress critic gate (PUSH): ``_build_egress_evidence_view`` in
     ``transport/chat.py`` projects gate5b ``Gate5BFullToolReceipt`` objects,
     which carry ``tool_name`` + a ``Gate5BFullToolOutcomeStatus`` ("ok"|
     "blocked"|"error"|"duplicate").

There is NO ``EvidenceLedger`` reachable at the egress seam, and NO gate5b
receipts reachable inside ``ToolContext`` at the mid-turn seam — the two
producers live at genuinely different runtime points. So we do NOT attempt to
unify the SOURCES. Instead this module guarantees a consistent OUTPUT: both
helpers emit the same ``ToolCallView`` shape and run their raw status through
the SAME ``normalize_tool_status`` vocabulary, so a "success" (or any other
outcome) reported via either seam projects to the same canonical token.

Pure / deterministic / read-only. No side effects.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.introspection.projection import ToolCallView

# Canonical, source-independent status vocabulary. Both the EvidenceLedger
# ``EvidenceStatus`` and the gate5b ``Gate5BFullToolOutcomeStatus`` are mapped
# onto exactly these tokens; anything unmapped collapses to "unknown".
ToolCallStatus = str  # one of _CANONICAL_STATUSES (kept as ``str`` for the view)

_UNKNOWN_STATUS = "unknown"

_CANONICAL_STATUSES: frozenset[str] = frozenset(
    {"ok", "error", "blocked", "duplicate", _UNKNOWN_STATUS}
)

# Total mapping from every known raw status (EvidenceStatus ∪
# Gate5BFullToolOutcomeStatus) onto the canonical vocabulary. Built explicitly
# so a new raw status added to either producer fails loudly in tests rather than
# silently diverging.
#
#   EvidenceStatus          -> canonical
#     "ok"                  -> "ok"
#     "failed"              -> "error"
#     "unknown"             -> "unknown"
#   Gate5BFullToolOutcomeStatus -> canonical
#     "ok"                  -> "ok"
#     "error"               -> "error"
#     "blocked"             -> "blocked"
#     "duplicate"           -> "duplicate"
_RAW_STATUS_MAP: Mapping[str, str] = {
    # EvidenceStatus (magi_agent/evidence/types.py)
    "ok": "ok",
    "failed": "error",
    "unknown": _UNKNOWN_STATUS,
    # Gate5BFullToolOutcomeStatus (magi_agent/gates/gate5b_full_toolhost.py)
    "error": "error",
    "blocked": "blocked",
    "duplicate": "duplicate",
}


def normalize_tool_status(raw: str) -> str:
    """Map a raw producer status onto the canonical tool-call vocabulary.

    Total over both producers' status sets; any unmapped/empty value collapses
    to ``"unknown"``. Pure and deterministic.
    """
    return _RAW_STATUS_MAP.get(raw, _UNKNOWN_STATUS)


def tool_call_from_evidence_record(
    record: Mapping[str, object],
    turn_id: str,
) -> ToolCallView | None:
    """Build a ``ToolCallView`` from a serialized EvidenceLedger record.

    Reads ``source.toolName`` + ``status`` exactly as the projection seam did,
    then normalizes the status. Returns ``None`` when the record has no usable
    tool name (mirrors prior projection behavior).
    """
    from magi_agent.introspection.projection import ToolCallView

    source = record.get("source")
    if not isinstance(source, Mapping):
        return None
    tool_name = source.get("toolName")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    status = record.get("status")
    raw_status = status if isinstance(status, str) else _UNKNOWN_STATUS
    return ToolCallView(
        name=tool_name,
        status=normalize_tool_status(raw_status),
        turnId=turn_id,
    )


def tool_call_from_gate5b_receipt(receipt: object, turn_id: str) -> ToolCallView:
    """Build a ``ToolCallView`` from a gate5b ``Gate5BFullToolReceipt``.

    Reads ``tool_name`` + ``status`` and normalizes the status onto the same
    canonical vocabulary used for EvidenceLedger records.
    """
    from magi_agent.introspection.projection import ToolCallView

    raw_status = getattr(receipt, "status", _UNKNOWN_STATUS)
    return ToolCallView(
        name=receipt.tool_name,
        status=normalize_tool_status(
            raw_status if isinstance(raw_status, str) else _UNKNOWN_STATUS
        ),
        turnId=turn_id,
    )


__all__ = [
    "ToolCallStatus",
    "normalize_tool_status",
    "tool_call_from_evidence_record",
    "tool_call_from_gate5b_receipt",
]
