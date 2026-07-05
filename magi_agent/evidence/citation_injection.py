"""Deterministic re-injection of assigned citation ids into tool results.

Wave 2 (L2, design 7.4). After the harness registers a tool result's external
sources (assigning each a stable ``src_N``), this module rewrites the
MODEL-FACING result dict so the model can SEE the ids and cite them:

- a rendered header per source (list shapes) or a prepended header (text
  shapes),
- a per-item ``sourceId`` on raw list entries,
- a top-level ``sources`` mirror of ``{sourceId, url, title}`` for structured
  consumers,
- a ``metadata.citation = {injected, sourceIds}`` marker so audit surfaces can
  tell harness bytes from provider bytes.

Injection is append/prepend of small headers ONLY. It never truncates or
reorders provider content: ``cap_text`` truncation runs upstream (in the tool
handler) BEFORE this, so the prepended header is never cut off. The input dict
is never mutated (a deep copy is returned). Pure: no I/O, no runtime imports
beyond types.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class InjectedSource:
    """One registered source with its assigned id, ready for injection."""

    source_id: str
    kind: str
    uri: str
    title: str | None = None
    snippet: str | None = None


# Source kinds whose tool result is a LIST of entries (per-item injection +
# rendered block). Everything else is treated as a single/multi text body that
# gets header lines prepended.
_LIST_KINDS: frozenset[str] = frozenset({"web_search", "kb"})

# Candidate text fields, in priority order, for the prepend (text shapes). The
# first present string field receives the header block.
_TEXT_OUTPUT_KEYS: tuple[str, ...] = ("markdown", "content", "text", "body")


def inject_citation_headers(
    tool_name: str,
    result: Mapping[str, object],
    sources: Sequence[InjectedSource],
) -> dict[str, object]:
    """Return a NEW result dict with citation ids injected.

    ``result`` is the model-facing dict (``ToolResult.model_dump(by_alias=True)``).
    ``sources`` are the already-registered sources in registration order. When
    ``sources`` is empty the result is returned unchanged (deep-copied). Never
    raises: on any structural surprise the structured mirror + metadata marker
    are still applied so downstream consumers see the mapping.
    """
    injected: dict[str, object] = copy.deepcopy(dict(result))
    if not sources:
        return injected

    try:
        first_kind = sources[0].kind
        if first_kind in _LIST_KINDS:
            _inject_list_shape(injected, sources)
        else:
            _inject_text_shape(injected, sources)
    except Exception:
        # Fail-quiet: fall through to still attach the structured mirror +
        # marker below so the mapping is never wholly lost.
        pass

    _attach_structured_mirror(injected, sources)
    _stamp_citation_metadata(injected, sources)
    return injected


def _inject_list_shape(
    injected: dict[str, object], sources: Sequence[InjectedSource]
) -> None:
    """Add a per-item ``sourceId`` to raw list entries and set ``llmOutput`` to
    a rendered per-source block. Web-search entries render the full
    ``[src_N] <title>\\n<url>\\n<description>`` form; kb (and other list kinds
    without snippets) render the compact ``[src_N] <title> - <uri>`` form."""
    by_uri: dict[str, str] = {s.uri: s.source_id for s in sources}
    items = _list_items(injected.get("output"))
    if items is not None:
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _first_str(item, "url", "link", "href", "uri", "path", "source")
            if url is not None and url in by_uri:
                item["sourceId"] = by_uri[url]

    blocks: list[str] = []
    for src in sources:
        if src.kind == "web_search":
            title = src.title or ""
            desc = src.snippet or ""
            blocks.append(f"[{src.source_id}] {title}\n{src.uri}\n{desc}")
        else:
            blocks.append(_compact_header(src))
    injected["llmOutput"] = "\n\n".join(blocks)


def _inject_text_shape(
    injected: dict[str, object], sources: Sequence[InjectedSource]
) -> None:
    """Prepend one header line per source to the primary text field."""
    header = "\n".join(_compact_source_header(src) for src in sources)
    _prepend_to_text_field(injected, header)


def _compact_source_header(src: InjectedSource) -> str:
    """``[source: src_N] <title> - <url>`` (or ``[source: src_N] <url>``)."""
    if src.title:
        return f"[source: {src.source_id}] {src.title} - {src.uri}"
    return f"[source: {src.source_id}] {src.uri}"


def _compact_header(src: InjectedSource) -> str:
    """``[src_N] <title> - <uri>`` (or ``[src_N] <uri>``) for list kinds."""
    if src.title:
        return f"[{src.source_id}] {src.title} - {src.uri}"
    return f"[{src.source_id}] {src.uri}"


def _prepend_to_text_field(injected: dict[str, object], header: str) -> None:
    output = injected.get("output")
    prefix = f"{header}\n\n"
    if isinstance(output, str):
        injected["output"] = prefix + output
        return
    if isinstance(output, dict):
        for key in _TEXT_OUTPUT_KEYS:
            value = output.get(key)
            if isinstance(value, str):
                output[key] = prefix + value
                return
    llm = injected.get("llmOutput")
    if isinstance(llm, str):
        injected["llmOutput"] = prefix + llm
        return
    # No text field to prepend to: expose the header on llmOutput so the model
    # still sees the ids (structured mirror + metadata still follow).
    injected["llmOutput"] = header


def _attach_structured_mirror(
    injected: dict[str, object], sources: Sequence[InjectedSource]
) -> None:
    injected["sources"] = [
        {"sourceId": src.source_id, "url": src.uri, "title": src.title}
        for src in sources
    ]


def _stamp_citation_metadata(
    injected: dict[str, object], sources: Sequence[InjectedSource]
) -> None:
    metadata = injected.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata["citation"] = {
        "injected": True,
        "sourceIds": [src.source_id for src in sources],
    }
    injected["metadata"] = metadata


def _list_items(output: object) -> list[object] | None:
    if isinstance(output, list):
        return output
    if isinstance(output, dict):
        for key in ("results", "documents", "items", "data", "hits", "organic"):
            value = output.get(key)
            if isinstance(value, list):
                return value
    return None


def _first_str(item: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = ["InjectedSource", "inject_citation_headers"]
