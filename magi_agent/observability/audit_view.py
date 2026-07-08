"""Per-session policy-enforcement verdict projection for the chat Audit panel.

Pure read/projection over the observability store and (optionally) source-ledger
records. Produces the redacted, grouped data contract the chat Audit tab renders.
No new verdict production, no global state, no I/O beyond the injected ``store``
(the caller wires the real store + source records).

Redaction: free text runs through ``public_projection_safe_text`` and metadata
mappings through ``public_evidence_metadata_report`` (evidence/reports.py). Raw
event payloads are NEVER returned.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from magi_agent.evidence.audit_labels import (
    AUDIT_PASS,
    ENFORCEMENT_EVENT_KINDS,
    classify_verdict_severity,
    is_enforced_kind,
    verdict_to_display_label,
    verify_finding_display_label,
)
from magi_agent.evidence.reports import (
    public_evidence_metadata_report,
    public_projection_safe_text,
)

# Sentinel dict key for the "ungrouped" bucket (events with run_id == None). Using
# the real ``None`` keeps the bucket stable while serializing ``runId`` as null.
_UNGROUPED: Any = None

_VERIFIED_TRUST_TIERS: frozenset[str] = frozenset({"primary", "official"})
_CREDIBILITY_VALUES: frozenset[str] = frozenset({"credible", "unverified", "contradicted"})

# Sentinel value public_projection_safe_text returns when it redacts a string.
_REDACTED = "[redacted]"

# Defensive caps against oversized payload lists (cheap upper bounds).
_MAX_REASON_CODES = 50
_MAX_EVIDENCE_REFS = 50
_MAX_SOURCES = 100


def build_session_audit(
    session_id: str,
    *,
    store: Any,
    source_records: Any = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Build the per-session audit projection.

    Reads enforcement events for ``session_id`` from ``store``, groups them per
    ``run_id`` (newest run first), projects each to a redacted verdict row, and
    projects the optional ``source_records`` to the sources box.
    """
    # Push the enforcement-kind filter down to SQL (kind IN (...)). Reading 200
    # rows and Python-filtering afterward truncated sparse enforcement events out
    # of the window whenever high-volume noise kinds (text_delta, heartbeat, ...)
    # filled it first. store.list_events accepts a comma-separated kind list.
    events = store.list_events(
        session_id=session_id,
        kind=",".join(sorted(ENFORCEMENT_EVENT_KINDS)),
        limit=limit,
    )
    # Cheap defensive filter: the SQL already restricts kinds, but keep this so a
    # future caller passing a pre-read event list still gets the same guarantee.
    enforced = [ev for ev in events if is_enforced_kind(ev.get("kind"))]

    grouped: dict[Any, list[dict[str, Any]]] = {}
    for ev in enforced:
        run_id = ev.get("run_id")
        grouped.setdefault(run_id if run_id is not None else _UNGROUPED, []).append(ev)

    runs: list[dict[str, Any]] = []
    for run_id, rows in grouped.items():
        timestamps = [_as_float(row.get("ts")) for row in rows]
        valid_ts = [ts for ts in timestamps if ts is not None]
        runs.append(
            {
                "runId": run_id,
                "startedAt": min(valid_ts) if valid_ts else None,
                "policyCount": len(rows),
                "verdicts": [_project_verdict(row) for row in rows],
                "_maxTs": max(valid_ts) if valid_ts else 0.0,
            }
        )

    # Newest run first by the latest event timestamp; stable for ties.
    runs.sort(key=lambda group: group["_maxTs"], reverse=True)
    for group in runs:
        group.pop("_maxTs", None)

    return {
        "ok": True,
        "sessionId": session_id,
        "runs": runs,
        "sources": _project_sources(source_records or []),
    }


# ---------------------------------------------------------------------------
# Verdict row projection
# ---------------------------------------------------------------------------


def _project_verdict(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}

    raw_status = event.get("status")
    # rule_check rows carry no top-level status; their verdict lives in the
    # public payload's ``verdict`` field (ok/violation/pending).
    verdict_hint = payload.get("verdict")
    status = _first_str(raw_status, verdict_hint) or "unknown"

    source_type = _first_str(payload.get("sourceType"), payload.get("source_type"))
    # source_citation.gate rows (Wave 4b): the event ``verdict`` stays a valid
    # RuleVerdict for the generic rule_check machinery, while the raw citation
    # verdict (cited/partial/uncited) rides a dedicated ``citationVerdict``
    # scalar. Prefer it for label selection so the Audit tab renders the
    # citation-governance vocabulary rather than generic pass/violation.
    if source_type == "citation":
        citation_status = _first_str(payload.get("citationVerdict"))
        if citation_status is not None:
            status = citation_status
    # verify_before_replying per-pass rows (B4, GAP-2 fix): per-pass rows carry
    # ``sourceType="verify"`` and a ``verifyKind`` scalar that identifies the
    # row species. Pass rows (verifyKind="pass") and legacy rows emitted by
    # pre-fix images (no verifyKind) must render as AUDIT PASS / info rather
    # than falling through to verdict_to_display_label's verify branch, which
    # only handles turn-level verdicts and returns UNKNOWN for pass verdicts.
    #
    # Branch is structured as an if/elif ladder so that turn and finding species
    # (verifyKind="turn" / "finding", added by the full rich-panel work) can be
    # wired here without restructuring.
    display_label: str
    if source_type == "verify":
        verify_kind = _first_str(payload.get("verifyKind"))
        if verify_kind == "turn":
            # Per-turn verdict row (PR-1, design B4 turn arm). The verifyVerdict
            # scalar carries the four turn-level verdict strings; override the
            # event-level status with it so verdict_to_display_label routes to
            # the correct turn-label arm in audit_labels.py.
            turn_verdict = _first_str(payload.get("verifyVerdict"))
            if turn_verdict is not None:
                status = turn_verdict
            display_label = verdict_to_display_label(status, source_type=source_type)
        elif verify_kind == "pass" or verify_kind is None:
            # Pass row or legacy row (no verifyKind from pre-fix image).
            display_label = AUDIT_PASS
        elif verify_kind == "finding":
            # Per-finding row (PR-2, design B4 finding arm). Use the finding-specific
            # label function keyed on (confidence, resolution); bypass the generic
            # verdict_to_display_label which only handles turn-level verdicts.
            confidence = _first_str(payload.get("confidence")) or ""
            resolution = _first_str(payload.get("resolution")) or ""
            status = resolution or status
            display_label = verify_finding_display_label(confidence, resolution)
        else:
            # Future species: fall through to the generic label function.
            display_label = verdict_to_display_label(status, source_type=source_type)
    else:
        display_label = verdict_to_display_label(status, source_type=source_type)

    subject_raw = _first_str(
        payload.get("ruleId"),
        payload.get("verifier_id"),
        payload.get("verifierId"),
        event.get("tool_name"),
    )

    summary_raw = _first_str(
        event.get("summary"),
        payload.get("detail"),
        payload.get("summary"),
        payload.get("public_summary"),
    )

    # Citation gate affordances (repaired / induced search / fail-open) ride a
    # dedicated ``affordances`` list, glanceable next to the verdict badge rather
    # than buried in the collapsed detail region. Empty for every non-citation
    # row, so generic rows are unaffected.
    affordances = (
        _citation_affordance_codes(payload) if source_type == "citation" else []
    )

    return {
        "id": str(event.get("id")) if event.get("id") is not None else None,
        "kind": event.get("kind"),
        "status": status,
        "displayLabel": display_label,
        "severity": classify_verdict_severity(display_label),
        "subject": public_projection_safe_text(subject_raw) if subject_raw else None,
        "reasonCodes": _reason_codes(payload),
        "affordances": affordances,
        "summary": public_projection_safe_text(summary_raw) if summary_raw else "",
        "evidenceRefs": _evidence_refs(payload),
        "verify": _verify_wire_fields(payload),
    }


def _verify_wire_fields(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build the optional verify wire object for the Audit panel (PR-1, design B4).

    Returns None for non-verify rows and for legacy rows (no verifyKind). For
    pass rows returns {kind: 'pass'}. For turn rows returns the full scalar
    inventory (int/bool coerced, absent keys omitted). Free text never enters
    this object; all fields are numbers, bools, or enum strings. findingsOmitted
    and context are added only when present in the payload.
    """
    verify_kind = _first_str(payload.get("verifyKind"))
    if verify_kind is None:
        # Legacy row (no verifyKind): return None to preserve old behavior.
        return None
    if verify_kind == "pass":
        return {"kind": "pass"}
    if verify_kind == "turn":
        obj: dict[str, Any] = {"kind": "turn"}
        verify_verdict = _first_str(payload.get("verifyVerdict"))
        if verify_verdict is not None:
            obj["verdict"] = verify_verdict
        for int_key in (
            "passes",
            "highTotal",
            "highResolved",
            "highAcknowledged",
            "highIgnored",
            "advisoryTotal",
            "advisoryIgnored",
            "loopBackToolCalls",
            "corpusRecordCount",
        ):
            val = payload.get(int_key)
            if val is not None:
                try:
                    obj[int_key] = int(val)
                except (TypeError, ValueError):
                    pass
        ship = payload.get("shipMarkerUsed")
        if ship is not None:
            obj["shipMarkerUsed"] = bool(ship)
        skeptic = payload.get("skepticRan")
        if skeptic is not None:
            obj["skepticRan"] = bool(skeptic)
        # findingsOmitted and context are optional (PR-2 adds findingsOmitted).
        findings_omitted = payload.get("findingsOmitted")
        if findings_omitted is not None:
            try:
                obj["findingsOmitted"] = int(findings_omitted)
            except (TypeError, ValueError):
                pass
        context = _first_str(payload.get("context"))
        if context is not None:
            obj["context"] = context
        return obj
    if verify_kind == "finding":
        # Per-finding wire object (PR-2, design B4 finding arm). Free text fields
        # (claimText, expected, observed) run through public_projection_safe_text
        # as a backstop -- the emitter already applied display_span, but the
        # projection layer is the final redaction frontier (mirrors how summary
        # is handled at :193).
        fobj: dict[str, Any] = {"kind": "finding"}
        finding_id = _first_str(payload.get("findingId"))
        if finding_id is not None:
            fobj["findingId"] = finding_id
        confidence = _first_str(payload.get("confidence"))
        if confidence is not None:
            fobj["confidence"] = confidence
        claim_class = _first_str(payload.get("claimClass"))
        if claim_class is not None:
            fobj["claimClass"] = claim_class
        resolution = _first_str(payload.get("resolution"))
        if resolution is not None:
            fobj["resolution"] = resolution
        claim_text_raw = _first_str(payload.get("claimText"))
        if claim_text_raw is not None:
            fobj["claimText"] = public_projection_safe_text(claim_text_raw)
        expected_raw = _first_str(payload.get("expectedValue"))
        if expected_raw is not None:
            fobj["expected"] = public_projection_safe_text(expected_raw)
        observed_raw = _first_str(payload.get("observedValue"))
        if observed_raw is not None:
            fobj["observed"] = public_projection_safe_text(observed_raw)
        suggested_action = _first_str(payload.get("suggestedAction"))
        if suggested_action is not None:
            fobj["suggestedAction"] = suggested_action
        return fobj
    # Future species: no wire object.
    return None


def _citation_affordance_codes(payload: Mapping[str, Any]) -> list[str]:
    """Human-readable chips for the citation gate's scalar affordances.

    Reads only the flat scalar fields the observability projector preserves
    (``project_public_event`` drops nested structures), so ``violations`` (a
    list of dicts) is intentionally NOT surfaced here.
    """
    codes: list[str] = []
    attempts = payload.get("repairAttempts")
    if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 0:
        codes.append(f"repaired ({attempts})" if attempts > 1 else "repaired")
    if payload.get("inducedSearch") is True:
        codes.append("induced search")
    if payload.get("failOpen") is True:
        codes.append("fail-open")
    return codes


def _reason_codes(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("reasonCodes")
    if raw is None:
        raw = payload.get("reason_codes")
    # If reason codes arrived nested inside a metadata mapping, route that mapping
    # through the canonical evidence-metadata redactor first.
    if raw is None:
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            safe_meta = public_evidence_metadata_report(metadata)
            raw = safe_meta.get("reasonCodes") or safe_meta.get("reason_codes")
    if not isinstance(raw, (list, tuple)):
        return []
    codes = [
        public_projection_safe_text(item)
        for item in raw
        if isinstance(item, str) and item.strip()
    ]
    # Defensive bound against an oversized reasonCodes payload list.
    return codes[:_MAX_REASON_CODES]


def _evidence_refs(payload: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("evidenceRef", "evidenceRefs"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            refs.append(value.strip())
        elif isinstance(value, (list, tuple)):
            refs.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    # Redact each ref: public_projection_safe_text preserves hash-shaped refs
    # (verifier:sha256:.../evidence:sha256:...) but redacts true locators
    # (ref:..., urls, file paths) to "[redacted]". Drop the redacted/empties,
    # then dedupe preserving first-seen order, then cap.
    redacted = [public_projection_safe_text(ref) for ref in refs]
    kept = [r for r in redacted if r and r != _REDACTED]
    return list(dict.fromkeys(kept))[:_MAX_EVIDENCE_REFS]


# ---------------------------------------------------------------------------
# Sources box projection
# ---------------------------------------------------------------------------


def _project_sources(records: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        raw_uri = _field(record, "uri", default="") or ""
        dedupe_key = raw_uri or _field(record, "source_id", "sourceId", default="") or ""
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        label_raw = (
            _field(record, "title", "label")
            or _field(record, "source_id", "sourceId")
            or "source"
        )
        inspected = bool(_field(record, "inspected", default=False))
        trust_tier = _field(record, "trust_tier", "trustTier")
        verified = inspected and trust_tier in _VERIFIED_TRUST_TIERS

        out.append(
            {
                "label": public_projection_safe_text(str(label_raw)),
                "uri": _safe_source_uri(str(raw_uri)) if raw_uri else "",
                "verified": verified,
                "credibility": _credibility(record, verified),
            }
        )
        if len(out) >= _MAX_SOURCES:
            break
    return out


def _safe_source_uri(raw: str) -> str:
    """Sanitize a source URI for display, preserving the host of real web URLs.

    The Sources box must show a recognizable host (e.g. ``sec.gov``,
    ``cnbc.com``) with a truncated path, so blanket
    ``public_projection_safe_text`` (which redacts every http(s) URL to
    "[redacted]") is too aggressive here. For http/https URLs we keep the host
    and only the FIRST path segment, eliding the rest, and DROP the query and
    fragment entirely (they can carry tokens). Userinfo (``user:pass@``) is
    stripped. Non-http locators (``ref:``, file paths, ``s3://``, ``git@``, ...)
    are not user-facing hosts and are redacted via public_projection_safe_text.
    """
    try:
        parts = urlsplit(raw)
    except ValueError:
        return public_projection_safe_text(raw)

    scheme = parts.scheme.lower()
    if scheme in {"http", "https"} and parts.netloc:
        # Strip any userinfo (everything before the last '@' in netloc), keep
        # host[:port].
        netloc = parts.netloc.rsplit("@", 1)[-1]
        if not netloc:
            return public_projection_safe_text(raw)
        segments = [seg for seg in parts.path.split("/") if seg]
        if not segments:
            return netloc
        if len(segments) == 1:
            return f"{netloc}/{segments[0]}"
        return f"{netloc}/{segments[0]}/…"

    # Non-http locator: not a user-facing host, redact it.
    return public_projection_safe_text(raw)


def _credibility(record: Any, verified: bool) -> str:
    explicit = _field(record, "credibility")
    if explicit is None:
        metadata = _field(record, "metadata")
        if isinstance(metadata, Mapping):
            explicit = metadata.get("credibility")
    if isinstance(explicit, str) and explicit in _CREDIBILITY_VALUES:
        return explicit
    return "credible" if verified else "unverified"


# ---------------------------------------------------------------------------
# Small accessors (dict OR pydantic-model records)
# ---------------------------------------------------------------------------


def _field(record: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(record, Mapping):
            if name in record and record[name] is not None:
                return record[name]
        else:
            value = getattr(record, name, None)
            if value is not None:
                return value
    return default


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


__all__ = ["build_session_audit"]
