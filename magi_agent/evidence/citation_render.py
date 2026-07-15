"""Pure render projection for source citations (Wave 3a, design Section 8).

Turns a final answer plus a :class:`SessionSourceRegistry` snapshot into a
display projection: canonical ``src_N`` refs get first-appearance ``[n]``
display indices, resolvable refs become source entries, and cited-but-unknown
refs become dangling refs. The projection is the single source of truth shared
by the terminal SSE frame, the CLI Sources footer, and (Wave 3b) the web
Sources tab, so every surface numbers citations identically.

Pure and deterministic: no env reads, no I/O. Flag gating lives at the call
sites (transport, CLI) where the environment is available. Marker extraction
and dangling detection use the canonical ``src_N`` regex verbatim (shared with
the research final gate) so this projection never diverges from the governance
regex. Tolerant near-miss normalization is exposed separately and is DISPLAY
only: it never feeds markers or dangling detection.

No em-dashes in this module per the citation feature style rule.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

# Reuse the EXACT canonical extraction regex the research final gate uses so
# marker/dangling extraction stays identical to the governance surface.
from magi_agent.evidence.research_final_gate import _FINAL_ANSWER_SOURCE_REF_RE
from magi_agent.evidence.source_ledger import SourceLedgerKind, SourceTrustTier

__all__ = [
    "CitationDisplayEntry",
    "CitationRenderProjection",
    "project_citations",
    "normalize_citation_token",
    "render_verdict",
    "build_citations_payload",
    "citations_payload_for",
    "hosted_citations_payload_for",
    "render_cli_sources_footer",
]

# DISPLAY-ONLY tolerant matcher for malformed near-misses like ``[src3]``,
# ``(src_3)``, ``[SRC_3]``. Never used for marker or dangling extraction.
_TOLERANT_TOKEN_RE = re.compile(
    r"^[\[(]?\s*src_?([1-9][0-9]*)\s*[\])]?$", re.IGNORECASE
)


class CitationDisplayEntry(BaseModel):
    """One resolved, cited source with its per-message display index."""

    model_config = ConfigDict(frozen=True)

    display_index: int
    source_id: str
    uri: str
    title: str | None = None
    kind: SourceLedgerKind
    trust_tier: SourceTrustTier | None = None
    inspected: bool
    turn_id: str


class CitationRenderProjection(BaseModel):
    """Display projection of the citations in one final answer.

    ``markers`` pairs each resolvable ``src_N`` with its ``[n]`` display index
    in first-appearance order. ``sources`` are the matching display entries.
    ``dangling_refs`` are cited ids with no registry entry (fabricated
    attribution); they receive no display index and no source entry.
    """

    model_config = ConfigDict(frozen=True)

    markers: tuple[tuple[str, int], ...] = ()
    sources: tuple[CitationDisplayEntry, ...] = ()
    dangling_refs: tuple[str, ...] = ()


def project_citations(
    final_text: str | None,
    registry: object | None,
) -> CitationRenderProjection:
    """Project canonical ``src_N`` refs in ``final_text`` into display entries.

    Refs are extracted with the canonical governance regex, deduped preserving
    first appearance, and resolved against ``registry.snapshot()``. Resolvable
    refs get sequential ``[n]`` indices and source entries; unresolvable refs go
    to ``dangling_refs`` and consume no index. A ``None`` registry (or one with
    an empty snapshot) resolves nothing, so every ref is dangling.
    """
    text = final_text or ""
    refs = _FINAL_ANSWER_SOURCE_REF_RE.findall(text)

    snapshot: dict[str, object] = {}
    if registry is not None:
        for record in registry.snapshot():
            snapshot[record.source_id] = record

    markers: list[tuple[str, int]] = []
    sources: list[CitationDisplayEntry] = []
    dangling: list[str] = []
    seen: set[str] = set()
    next_index = 1

    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        record = snapshot.get(ref)
        if record is None:
            dangling.append(ref)
            continue
        markers.append((ref, next_index))
        sources.append(
            CitationDisplayEntry(
                display_index=next_index,
                source_id=ref,
                uri=record.uri,
                title=record.title,
                kind=record.kind,
                trust_tier=record.trust_tier,
                inspected=record.inspected,
                turn_id=record.turn_id,
            )
        )
        next_index += 1

    return CitationRenderProjection(
        markers=tuple(markers),
        sources=tuple(sources),
        dangling_refs=tuple(dangling),
    )


def normalize_citation_token(raw: str) -> str | None:
    """Normalize a tolerant near-miss token to canonical ``src_N`` (DISPLAY only).

    Accepts ``[src3]``, ``(src_3)``, ``[SRC_3]``, ``src_3`` and returns
    ``src_3``. Returns ``None`` for anything that is not a src-shaped token.
    This is a rendering convenience for a malformed inline token; it never
    feeds marker extraction or dangling detection (those stay canonical).
    """
    if not isinstance(raw, str):
        return None
    match = _TOLERANT_TOKEN_RE.match(raw.strip())
    if match is None:
        return None
    return f"src_{match.group(1)}"


def render_verdict(
    projection: CitationRenderProjection,
    *,
    has_registry_sources: bool,
) -> str:
    """Deterministic terminal-payload verdict (superseded by the Wave 4 gate).

    Values: ``cited`` (>=1 resolvable marker, no dangling), ``partial`` (>=1
    marker AND >=1 dangling), ``uncited`` (registry has sources this session but
    the text cites none resolvable), ``not_applicable`` (no external-read
    sources at all). This is a render-level status only; Wave 4 replaces it with
    a gate-produced governance verdict.
    """
    cited = len(projection.markers)
    dangling = len(projection.dangling_refs)
    if cited >= 1 and dangling == 0:
        return "cited"
    if cited >= 1 and dangling >= 1:
        return "partial"
    if has_registry_sources:
        return "uncited"
    return "not_applicable"


def build_citations_payload(
    projection: CitationRenderProjection,
    verdict: str,
) -> dict[str, object]:
    """Build the on-the-wire ``citations`` object (design Section 8 shape).

    camelCase keys match the frame convention. ``sources`` entries carry the
    display index as ``n`` and never carry ``turn_id`` (that stays on the
    in-memory display entry, not the wire).
    """
    return {
        "markers": [[source_id, index] for source_id, index in projection.markers],
        "sources": [
            {
                "n": entry.display_index,
                "sourceId": entry.source_id,
                "uri": entry.uri,
                "title": entry.title,
                "kind": entry.kind,
                "trustTier": entry.trust_tier,
                "inspected": entry.inspected,
            }
            for entry in projection.sources
        ],
        "danglingRefs": list(projection.dangling_refs),
        "verdict": verdict,
    }


def citations_payload_for(
    final_text: str | None,
    registry: object | None,
) -> dict[str, object] | None:
    """Compose the terminal ``citations`` payload from text plus a registry.

    Returns ``None`` when ``registry`` is ``None`` (caller has nothing to
    project). Otherwise projects, computes the render verdict, and builds the
    wire payload. Callers gate on ``MAGI_SOURCE_CITATION_ENABLED`` before
    invoking (a disabled session yields a ``None`` registry from the collector).
    """
    if registry is None:
        return None
    projection = project_citations(final_text, registry)
    has_sources = bool(registry.snapshot())
    verdict = render_verdict(projection, has_registry_sources=has_sources)
    return build_citations_payload(projection, verdict)


def hosted_citations_payload_for(
    final_text: str | None,
    collector: object | None,
    session_id: str | None,
) -> dict[str, object] | None:
    """Compose the hosted terminal ``citations`` payload from a per-turn collector.

    Hosted analogue of the LOCAL streaming driver's inline registry lookup plus
    payload build (transport/streaming_driver.py): the LOCAL path reaches the
    live ``SessionSourceRegistry`` through ``engine.local_tool_evidence_collector``
    and calls :func:`citations_payload_for`; the hosted governed serving path has
    no engine-bound collector, so the driver holds a per-turn collector by
    reference and passes it here.

    Fail-soft and byte-identity-when-off: returns ``None`` when the collector is
    absent, exposes no ``source_registry_for`` accessor, or citation is disabled
    (``source_registry_for`` itself gates on ``MAGI_SOURCE_CITATION_ENABLED`` and
    returns ``None`` when off). Any fault in registry lookup or payload build
    collapses to ``None`` so a citation fault never breaks the hosted stream.
    """
    if collector is None or not session_id:
        return None
    try:
        accessor = getattr(collector, "source_registry_for", None)
        if not callable(accessor):
            return None
        registry = accessor(session_id)
        if registry is None:
            return None
        return citations_payload_for(final_text, registry)
    except Exception:
        return None


def _host_of(uri: str) -> str:
    """Best-effort host label for a source uri (falls back to the raw uri)."""
    try:
        netloc = urlparse(uri).netloc
    except Exception:
        return uri
    return netloc or uri


def render_cli_sources_footer(citations: Mapping[str, object] | None) -> str:
    """Render the CLI / headless markdown Sources footer (design 12.2).

    Lists ONLY cited sources (uncited-but-registered sources are a web-panel
    concept, not shown here). Returns an empty string when there are no cited
    sources, so a well-cited turn with nothing to attribute prints nothing
    extra. Each line is ``  [n] <title> - <host> (src_N)`` (title omitted when
    absent). The caller decides whether to append, gated on the citation flag.
    """
    if not citations:
        return ""
    raw_sources = citations.get("sources")
    if not isinstance(raw_sources, (list, tuple)) or not raw_sources:
        return ""
    lines = ["Sources:"]
    for entry in raw_sources:
        if not isinstance(entry, Mapping):
            continue
        index = entry.get("n")
        source_id = entry.get("sourceId")
        uri = entry.get("uri")
        title = entry.get("title")
        host = _host_of(uri) if isinstance(uri, str) else ""
        label = title if isinstance(title, str) and title else host
        if label and host and label != host:
            body = f"{label} - {host}"
        else:
            body = label or host or (uri if isinstance(uri, str) else "")
        lines.append(f"  [{index}] {body} ({source_id})")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
