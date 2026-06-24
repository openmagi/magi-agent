"""D-13 — single source of truth for transcript rendering.

Both ``context/auto_compact.AutoCompactionEngine._format_conversation``
(dict-message transcripts, the legacy pre-ADK shape) and
``adk_bridge/context_compaction._render_dropped_transcript`` (ADK
``types.Content`` transcripts, the live compaction stack) emit
``"\\n\\n"``-joined ``[role]: content`` lines built from per-message
pieces. REVIEW-A engine L1 (D-13) flagged the duplication: the
``_render_dropped_transcript`` docstring even cited
``_format_conversation`` as its model. Per-piece caps and per-piece
formatting differ between the two paths, so the shared renderer only
collapses the common skeleton: role bracketing, line joining, total-
length cap, truncation marker.

This module exposes:

- :class:`NormalizedSegment` — frozen ``(role, pieces)`` view. The
  adapter packs already-formatted and already-capped strings into
  ``pieces`` in the order they should appear in the rendered line.
  Pieces may include plain text, ``"[tool_call <name> <args>]"``,
  ``"[tool_result <name>]: <payload>"`` — the renderer does not
  distinguish kinds, so the adapter retains its provider-specific
  ordering (matters for ADK ``Content.parts`` where text and
  function_call/function_response parts can interleave).
- :func:`render_transcript` — render an iterable of segments to a
  ``"\\n\\n"``-joined ``[role]: content`` block, optionally dropping
  segments past a ``total_cap`` and appending a ``truncation_marker``.

Import-clean leaf: stdlib only, no project imports, so both context
and adk_bridge call sites can pull it without disturbing their cold-
start discipline.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedSegment:
    """One transcript segment.

    ``pieces`` carries the already-formatted strings that make up the
    line's content, in the order they should appear. The adapter is
    responsible for any per-piece length caps and for formatting tool-
    call / tool-result markers.

    Every segment passed to :func:`render_transcript` is rendered
    unconditionally as ``[role]: <pieces joined by ' '>`` — including
    a segment whose pieces tuple is empty (in which case the line is
    ``"[role]: "``, a deliberate marker the legacy auto-compact format
    emits for empty-content messages). Adapters that want to suppress
    empty segments must filter them BEFORE constructing the segment.
    """

    role: str
    pieces: tuple[str, ...] = ()


def render_transcript(
    segments: Iterable[NormalizedSegment],
    *,
    total_cap: int | None = None,
    truncation_marker: str = "",
) -> str:
    """Render an iterable of normalized segments.

    Output shape: ``"\\n\\n"``-joined ``[role]: <pieces joined by ' '>``
    lines. EVERY segment in the iterable is rendered — adapters filter
    empty ones BEFORE building segments. When ``total_cap`` is set, the
    renderer stops appending lines once the cumulative character length
    would exceed it; if any segment was dropped, ``truncation_marker``
    is appended verbatim to the output.
    """

    lines: list[str] = []
    used = 0
    truncated = False
    for segment in segments:
        line = f"[{segment.role}]: {' '.join(segment.pieces)}"
        if total_cap is not None and used + len(line) > total_cap:
            truncated = True
            break
        lines.append(line)
        used += len(line)
    rendered = "\n\n".join(lines)
    if truncated and truncation_marker:
        rendered += truncation_marker
    return rendered


__all__ = ["NormalizedSegment", "render_transcript"]
