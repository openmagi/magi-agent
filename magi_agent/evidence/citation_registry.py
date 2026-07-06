"""Session-scoped source registry for citation id allocation and dedup.

Wave 1 of the source-citation substrate. Wraps a single long-lived
LocalResearchSourceLedger (turn_id="session" sentinel) so ids are stable
across the whole session. Dedup is by (kind, canonical_uri); same URL twice
returns the same src_N. Content-hash changes on the same URL keep the id and
append a revision entry to metadata.

NOT thread-safe: the CLI serve path is single-threaded per session.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerKind,
    SourceLedgerRecord,
    SourceTrustTier,
)

_SESSION_LEDGER_ID_PREFIX = "citation-registry"
_SESSION_TURN_SENTINEL = "session"
_SESSION_SOURCE_CAP = 500
_TRACKER_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_creative_format", "utm_marketing_tactic",
    "fbclid", "gclid", "msclkid", "dclid", "twclid",
    "_ga", "_gl", "mc_cid", "mc_eid",
})
_DEFAULT_PORTS: Mapping[str, str] = {"http": "80", "https": "443", "ftp": "21"}


def _canonical_uri(uri: str) -> str:
    """Normalize a URI for dedup keying.

    Lowercases scheme and host, strips fragment, removes default ports,
    and removes known tracker query params. Non-HTTP URIs (kb://, file://)
    are returned with only scheme+host lowercased and fragment stripped.
    """
    try:
        parsed = urlparse(uri)
    except Exception:
        return uri.lower()

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Strip default port from netloc
    if ":" in netloc:
        host, port = netloc.rsplit(":", 1)
        if _DEFAULT_PORTS.get(scheme) == port:
            netloc = host

    # Strip tracker params from query string (HTTP/HTTPS only)
    query = parsed.query
    if scheme in ("http", "https") and query:
        try:
            params = parse_qs(query, keep_blank_values=True)
            cleaned = {k: v for k, v in params.items() if k not in _TRACKER_PARAMS}
            query = urlencode(cleaned, doseq=True)
        except Exception:
            pass

    # Rebuild without fragment
    canonical = urlunparse((scheme, netloc, parsed.path, parsed.params, query, ""))
    return canonical


class SessionSourceRegistry:
    """Session-scoped source registry for citation id allocation and dedup.

    One instance per session, held by the LocalToolEvidenceCollector.
    Wraps a single LocalResearchSourceLedger with turn_id="session" so
    _next_source_id computes max+1 over all session records.
    """

    def __init__(self, *, session_id: str) -> None:
        self.session_id = session_id
        self._ledger = LocalResearchSourceLedger.model_validate({
            "ledgerId": f"{_SESSION_LEDGER_ID_PREFIX}:{session_id}",
            "sessionId": session_id,
            "turnId": _SESSION_TURN_SENTINEL,
        })
        # (kind, canonical_uri) -> source_id
        self._by_key: dict[tuple[str, str], str] = {}
        # source_id -> list of revision entries (content-hash changes on a
        # re-read of the same URI). Kept as registry side metadata because
        # SourceLedgerRecord is frozen; folded into snapshot() output.
        self._revisions: dict[str, list[dict[str, object]]] = {}
        self._saturated: bool = False
        self._saturation_logged: bool = False

    def register(
        self,
        kind: SourceLedgerKind,
        uri: str,
        *,
        turn_id: str,
        tool_name: str,
        tool_use_id: str | None = None,
        title: str | None = None,
        content_hash: str | None = None,
        trust_tier: SourceTrustTier | None = None,
        snippets: tuple[str, ...] = (),
        metadata: Mapping[str, object] | None = None,
        inspected: bool = False,
    ) -> SourceLedgerRecord | None:
        """Register a source, returning a SourceLedgerRecord or None if saturated.

        Dedup: same (kind, canonical_uri) returns the SAME src_N for the life
        of the session. Content-hash change on the same URI returns the same id.
        New registrations beyond the 500-source cap return None (saturation).
        Dedup hits always return the existing record even after cap.
        """
        canonical = _canonical_uri(uri)
        key = (kind, canonical)

        existing_id = self._by_key.get(key)
        if existing_id is not None:
            existing = self._ledger.source_by_id(existing_id)
            if existing is None:
                return None
            # Design 7.2: a dedup hit whose content_hash differs from the
            # recorded one keeps the SAME id and appends a revision entry (the
            # id names the source, not the snapshot).
            self._maybe_record_revision(existing, content_hash, turn_id)
            return existing

        # Cap check for new registrations
        if len(self._by_key) >= _SESSION_SOURCE_CAP:
            if not self._saturation_logged:
                self._saturated = True
                self._saturation_logged = True
            return None

        # New registration
        meta: dict[str, object] = dict(metadata) if metadata else {}
        payload: dict[str, object] = {
            "turnId": turn_id,
            "toolName": tool_name,
            "evidenceType": _kind_to_evidence_type(kind),
            "kind": kind,
            "uri": uri,
            "inspectedAt": time.time(),
            "inspected": inspected,
        }
        if tool_use_id is not None:
            payload["toolUseId"] = tool_use_id
        if title is not None:
            payload["title"] = title
        if content_hash is not None:
            payload["contentHash"] = content_hash
        if trust_tier is not None:
            payload["trustTier"] = trust_tier
        if snippets:
            payload["snippets"] = snippets
        if meta:
            payload["metadata"] = meta

        try:
            record = self._ledger.record_source(payload)
        except Exception:
            return None

        self._by_key[key] = record.source_id
        return record

    def lookup(self, kind: SourceLedgerKind, uri: str) -> SourceLedgerRecord | None:
        """Look up a registered source by kind and URI (canonical dedup key)."""
        canonical = _canonical_uri(uri)
        source_id = self._by_key.get((kind, canonical))
        if source_id is None:
            return None
        return self._ledger.source_by_id(source_id)

    def _maybe_record_revision(
        self,
        existing: SourceLedgerRecord,
        content_hash: str | None,
        turn_id: str,
    ) -> None:
        """Append a revision entry when a re-read carries a new content hash.

        No-op when ``content_hash`` is absent or matches the latest known hash
        for this source (the original record hash, or the most recent revision).
        Records stay immutable: the revision list lives in registry side
        metadata and is surfaced by ``snapshot()``.
        """
        if not content_hash:
            return
        revisions = self._revisions.get(existing.source_id, [])
        latest = revisions[-1]["contentHash"] if revisions else existing.content_hash
        if content_hash == latest:
            return
        revisions.append({
            "contentHash": content_hash,
            "turnId": turn_id,
            "recordedAt": time.time(),
        })
        self._revisions[existing.source_id] = revisions

    def snapshot(self) -> tuple[SourceLedgerRecord, ...]:
        """Return an immutable snapshot of all registered sources.

        Records that accrued content-hash revisions (7.2) carry a ``revisions``
        entry in their ``metadata``, folded in here as an immutable copy so the
        stored records stay untouched.
        """
        records = self._ledger.snapshot()
        if not self._revisions:
            return records
        folded: list[SourceLedgerRecord] = []
        for record in records:
            revisions = self._revisions.get(record.source_id)
            if not revisions:
                folded.append(record)
                continue
            new_metadata = {**dict(record.metadata), "revisions": tuple(revisions)}
            folded.append(record.model_copy(update={"metadata": new_metadata}))
        return tuple(folded)

    @property
    def is_saturated(self) -> bool:
        return self._saturated


def _kind_to_evidence_type(kind: SourceLedgerKind) -> str:
    """Map SourceLedgerKind to BUILTIN evidence type for the EvidenceRecord."""
    if kind == "web_search":
        return "WebSearch"
    if kind == "kb":
        return "KnowledgeSearch"
    # web_fetch, browser, file, external_repo, external_doc, subagent_result, clock
    return "SourceInspection"


__all__ = [
    "SessionSourceRegistry",
    "_canonical_uri",
    "_SESSION_SOURCE_CAP",
]
