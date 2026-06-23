"""Serialize a session's durable evidence into ONE stable per-run view.

This is the read contract a run-share page renders. ``build_run_view`` is pure
over the rows ``EvidenceLedgerReader.read`` returns (one dict per JSONL line,
shape ``{sessionId, turnId, toolCallId?, toolName?, status?, record}``). It
discriminates each row by the inner ``record``:

  - ``openmagi.runBookend.v1``                -> ``summary`` (goal/result/model/usage/cost)
  - ``custom:FirstPartyToolCall``             -> a ``trace`` step (+ ``governance`` if not ok)
  - ``openmagi.localToolEvidenceReceipt.v1``  -> a digest-only receipt (skipped, counted)

The ``record`` field is sometimes stored as a python-repr string (single quotes,
``True``/``False``) rather than JSON, so parsing tolerates both. Everything is
best-effort: an unparseable row is skipped, never raised.

SECURITY: trace free-text (``argsSummary``/``resultSummary``) is surfaced AS
STORED. The producer already redacts long values, but short commands/paths are
not scrubbed here. This view is therefore NOT yet safe for a PUBLIC link; the
dedicated redaction phase hardens the serialization path before any public
exposure.
"""
from __future__ import annotations

import ast
import json
from collections.abc import Mapping, Sequence

from magi_agent.evidence.run_bookend import RUN_BOOKEND_SCHEMA_VERSION

__all__ = [
    "RUN_VIEW_SCHEMA_VERSION",
    "build_run_view",
    "read_run_view",
]

RUN_VIEW_SCHEMA_VERSION = "openmagi.runView.v1"

_RECEIPT_SCHEMA = "openmagi.localToolEvidenceReceipt.v1"
# The first-party activity family the collector persists to the SAME JSONL:
# ToolCall, SkillLoad, SubagentSpawn. All are run steps a share page wants
# (a SubagentSpawn / delegation is one of the most interesting steps), so we
# match the whole family by prefix, not just ToolCall.
_FIRST_PARTY_PREFIX = "custom:FirstParty"
# Non-ok tool statuses split into a tool failure vs a policy/governance decision.
_ERROR_STATUSES = frozenset({"error"})


def _coerce_record(obj: object) -> dict:
    """Return the inner record as a dict from a dict / JSON str / python-repr str."""
    if isinstance(obj, Mapping):
        return dict(obj)
    if isinstance(obj, str):
        text = obj.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _bookend_summary(record: Mapping[str, object]) -> dict:
    # Allowlist the published keys (the record is already allowlisted at write
    # time, but stay explicit so a future producer change cannot widen this).
    summary: dict[str, object] = {}
    for key in ("goal", "result", "status", "model", "usage", "costUsd"):
        if key in record:
            summary[key] = record[key]
    return summary


def _activity_type(record: Mapping[str, object], fields: Mapping[str, object]) -> str | None:
    # ``evidenceType`` is the producer's authoritative label (ToolCall /
    # SkillLoad / SubagentSpawn); fall back to the record ``type`` suffix.
    label = fields.get("evidenceType")
    if isinstance(label, str) and label:
        return label
    rec_type = record.get("type")
    if isinstance(rec_type, str) and rec_type.startswith(_FIRST_PARTY_PREFIX):
        return rec_type[len(_FIRST_PARTY_PREFIX) :] or None
    return None


def _trace_step(row: Mapping[str, object], record: Mapping[str, object]) -> dict:
    fields = record.get("fields")
    fields = fields if isinstance(fields, Mapping) else {}
    detail = fields.get("detail")
    detail = detail if isinstance(detail, Mapping) else {}
    name = fields.get("name") or row.get("toolName")
    status = fields.get("status") or row.get("status")
    step: dict[str, object] = {
        "turnId": row.get("turnId"),
        "toolCallId": row.get("toolCallId"),
        "activityType": _activity_type(record, fields),
        "name": name,
        "status": status,
        "reason": fields.get("reason"),
        "durationMs": fields.get("durationMs"),
        "actor": fields.get("actor"),
        "spawnDepth": fields.get("spawnDepth"),
        "argsSummary": detail.get("argsSummary"),
        "resultSummary": detail.get("resultSummary"),
    }
    return step


def build_run_view(
    rows: Sequence[Mapping[str, object]],
    *,
    session_id: str | None = None,
) -> dict:
    """Build the per-run view from a session's durable evidence rows."""
    summary: dict | None = None
    trace: list[dict] = []
    governance: list[dict] = []
    receipt_count = 0
    resolved_session = session_id
    turn_ids: set[object] = set()

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if resolved_session is None:
            sid = row.get("sessionId")
            if isinstance(sid, str) and sid:
                resolved_session = sid
        # Count the turn from EVERY row, so a turn whose only contribution is a
        # bookend or a receipt (no tool call) still counts toward turnCount.
        turn = row.get("turnId")
        if turn is not None:
            turn_ids.add(turn)
        record = _coerce_record(row.get("record"))
        schema = record.get("schemaVersion")
        rec_type = record.get("type")

        if schema == RUN_BOOKEND_SCHEMA_VERSION:
            # Latest bookend wins: the final turn's bookend is the run result.
            # A bookend is written per turn, so in a multi-objective session the
            # earlier goals are intentionally dropped from the summary here.
            summary = _bookend_summary(record)
        elif schema == _RECEIPT_SCHEMA:
            receipt_count += 1
        elif isinstance(rec_type, str) and rec_type.startswith(_FIRST_PARTY_PREFIX):
            step = _trace_step(row, record)
            trace.append(step)
            if step["status"] not in (None, "ok"):
                governance.append(
                    {
                        "turnId": step["turnId"],
                        "name": step["name"],
                        "status": step["status"],
                        "reason": step["reason"],
                        # "error" is a tool failure; blocked/needs_approval are
                        # policy decisions. The renderer can split on this.
                        "kind": "error" if step["status"] in _ERROR_STATUSES else "policy",
                    }
                )
    return {
        "schemaVersion": RUN_VIEW_SCHEMA_VERSION,
        "sessionId": resolved_session,
        "summary": summary,
        "trace": trace,
        "governance": governance,
        "counts": {
            "stepCount": len(trace),
            "turnCount": len(turn_ids),
            "receiptCount": receipt_count,
            "governanceCount": len(governance),
        },
    }


def read_run_view(
    session_id: str, *, env: Mapping[str, str] | None = None
) -> dict | None:
    """Read a session's durable evidence and build its per-run view.

    Returns ``None`` when the durable sink is disabled or the session has no
    rows. Fail-open: any read error yields ``None`` rather than raising.

    This is a pure read with no authority, so it carries no feature flag. The
    public-exposure gate (and the redaction pass) belong at the future
    share-link caller, not here.
    """
    from magi_agent.evidence.ledger_store import (
        EvidenceLedgerReader,
        resolve_evidence_ledger_dir,
    )

    try:
        base_dir = resolve_evidence_ledger_dir(env)
        if base_dir is None:
            return None
        rows = EvidenceLedgerReader(base_dir).read(session_id)
        if not rows:
            return None
        return build_run_view(rows, session_id=session_id)
    except Exception:
        return None
