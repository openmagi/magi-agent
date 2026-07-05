"""Deterministic citation capture classifier.

Wave 1: classifies tool results into zero or more source registration specs
(kind, uri, metadata). Positive allowlist keyed on tool name + result shape.
Memory tools never register. Unknown tools fail-quiet (return empty list).
Authored paths (files created/edited by the agent this session) are excluded
for the file kind.

Pure function: no I/O, no imports from the rest of the runtime beyond types.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from magi_agent.evidence.source_ledger import SourceLedgerKind, SourceTrustTier

# Memory tool names that must NEVER register sources.
_MEMORY_TOOL_NAMES: frozenset[str] = frozenset({
    "MemoryRead", "MemoryWrite", "MemorySearch", "MemoryCompact",
    "MemoryList", "MemoryDelete", "AgentMemoryImport", "MemoryBankRead",
    "MemoryBankWrite", "MemoryBankList",
    "memory_read", "memory_write", "memory_search", "memory_compact",
})

_WEB_SEARCH_TOOL_NAMES: frozenset[str] = frozenset({
    "web_search", "WebSearch", "search_web", "SearchWeb",
})

_WEB_FETCH_TOOL_NAMES: frozenset[str] = frozenset({
    "web_fetch", "WebFetch", "fetch_url", "FetchUrl",
    "research_fact", "ResearchFact",
})

# Design 7.3: the citable ``file`` kind is FileRead/DocumentRead only. Glob and
# Grep are NOT citable external reads (a pattern match is not a source), so they
# are deliberately absent here and register zero citation sources. GitDiff is a
# repository read (external_repo).
_FILE_READ_TOOL_NAMES: frozenset[str] = frozenset({
    "FileRead", "file_read", "DocumentRead", "document_read",
    "GitDiff", "git_diff",
})

_BROWSER_TOOL_NAMES: frozenset[str] = frozenset({
    "browser_navigate", "browser_read", "BrowserNavigate", "BrowserRead",
    "browser_screenshot", "BrowserScreenshot",
})

_KB_TOOL_NAMES: frozenset[str] = frozenset({
    "KnowledgeSearch", "knowledge_search", "kb_search", "KbSearch",
})

_OFFICIAL_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".gov", ".gov.uk", ".mil", ".edu", ".int",
    "ietf.org", "w3.org", "iso.org", "nist.gov",
    "sec.gov", "federalregister.gov",
)


@dataclass
class CaptureSpec:
    """A single source registration spec emitted by the classifier."""

    kind: SourceLedgerKind
    uri: str
    title: str | None = None
    snippets: tuple[str, ...] = field(default_factory=tuple)
    content_hash: str | None = None
    trust_tier: SourceTrustTier | None = None
    inspected: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


def classify_tool_result_for_citation(
    tool_name: str,
    result: object,
    arguments: Mapping[str, object] | None = None,
    *,
    authored_paths: frozenset[str] | None = None,
) -> list[CaptureSpec]:
    """Classify a tool result into zero or more source registration specs.

    Returns an empty list for memory tools, unknown tools, or results with
    no extractable source information. Never raises (fail-quiet).
    """
    if not tool_name or tool_name in _MEMORY_TOOL_NAMES:
        return []

    normalized = "".join(c for c in tool_name.casefold() if c.isalnum())

    try:
        args = arguments or {}
        metadata = _extract_metadata(result)
        output = _extract_output(result)

        if tool_name in _WEB_SEARCH_TOOL_NAMES or normalized == "websearch":
            return _classify_web_search(result, args, output, metadata)

        if tool_name in _WEB_FETCH_TOOL_NAMES or normalized in ("webfetch", "researchfact"):
            return _classify_web_fetch(result, args, output, metadata)

        if tool_name in _FILE_READ_TOOL_NAMES or normalized in (
            "fileread", "documentread", "gitdiff"
        ):
            return _classify_file_read(tool_name, args, output, authored_paths)

        if tool_name in _BROWSER_TOOL_NAMES or normalized.startswith("browser"):
            return _classify_browser(args, output, metadata)

        if tool_name in _KB_TOOL_NAMES or normalized in ("knowledgesearch", "kbsearch"):
            return _classify_kb(result, args, output, metadata)
    except Exception:
        return []

    return []


def _classify_web_search(
    result: object,
    args: Mapping[str, object],
    output: object,
    metadata: dict[str, object],
) -> list[CaptureSpec]:
    specs = []
    items: list[object] = []
    if isinstance(output, list):
        items = output
    elif isinstance(output, Mapping):
        for key in ("results", "items", "data", "hits", "organic"):
            v = output.get(key)
            if isinstance(v, list):
                items = v
                break
        if not items:
            items = [output]
    elif isinstance(result, Mapping):
        for key in ("results", "items", "data", "hits", "organic"):
            v = result.get(key)
            if isinstance(v, list):
                items = v
                break

    for item in items[:20]:
        if not isinstance(item, Mapping):
            continue
        url = _first_string(item, "url", "link", "href", "uri")
        if not url or not url.startswith("http"):
            continue
        title = _first_string(item, "title", "name")
        snippet = _first_string(item, "snippet", "description", "body", "text")
        specs.append(CaptureSpec(
            kind="web_search",
            uri=url,
            title=title,
            snippets=(snippet,) if snippet else (),
            trust_tier=_web_trust_tier(url),
            inspected=False,
        ))
    return specs


def _classify_web_fetch(
    result: object,
    args: Mapping[str, object],
    output: object,
    metadata: dict[str, object],
) -> list[CaptureSpec]:
    url = _first_string(args, "url", "uri", "href", "link")
    if not url:
        url = _first_string(metadata, "url", "uri")
    if not url:
        if isinstance(output, Mapping):
            url = _first_string(output, "url", "uri")
    if not url or not url.startswith("http"):
        return []

    title = None
    if isinstance(output, Mapping):
        title = _first_string(output, "title", "pageTitle")
    if not title:
        title = _first_string(metadata, "title")

    return [CaptureSpec(
        kind="web_fetch",
        uri=url,
        title=title,
        trust_tier=_web_trust_tier(url),
        inspected=True,
    )]


def _classify_file_read(
    tool_name: str,
    args: Mapping[str, object],
    output: object,
    authored_paths: frozenset[str] | None,
) -> list[CaptureSpec]:
    path = _first_string(args, "path", "file", "filepath", "file_path")
    if not path:
        return []

    if authored_paths and path in authored_paths:
        return []

    if not path.startswith(("file://", "http")):
        uri = f"file://{path}" if path.startswith("/") else f"file://{path}"
    else:
        uri = path

    kind_map: dict[str, SourceLedgerKind] = {
        "FileRead": "file", "file_read": "file", "DocumentRead": "file",
        "document_read": "file",
        "GitDiff": "external_repo", "git_diff": "external_repo",
    }
    kind: SourceLedgerKind = kind_map.get(tool_name, "file")

    return [CaptureSpec(
        kind=kind,
        uri=uri,
        trust_tier="primary",
        inspected=True,
    )]


def _classify_browser(
    args: Mapping[str, object],
    output: object,
    metadata: dict[str, object],
) -> list[CaptureSpec]:
    url = _first_string(args, "url", "uri")
    if not url:
        if isinstance(output, Mapping):
            url = _first_string(output, "url", "currentUrl", "current_url")
    if not url or not url.startswith("http"):
        return []

    title = None
    if isinstance(output, Mapping):
        title = _first_string(output, "title", "pageTitle")

    return [CaptureSpec(
        kind="browser",
        uri=url,
        title=title,
        trust_tier=_web_trust_tier(url),
        inspected=True,
    )]


def _classify_kb(
    result: object,
    args: Mapping[str, object],
    output: object,
    metadata: dict[str, object],
) -> list[CaptureSpec]:
    specs = []
    items: list[object] = []
    if isinstance(output, list):
        items = output
    elif isinstance(output, Mapping):
        for key in ("results", "documents", "items"):
            v = output.get(key)
            if isinstance(v, list):
                items = v
                break

    for item in items[:10]:
        if not isinstance(item, Mapping):
            continue
        doc_id = _first_string(item, "id", "docId", "doc_id", "document_id")
        collection = _first_string(item, "collection", "collectionId", "collection_id")
        if doc_id:
            uri = f"kb://{collection or 'default'}/{doc_id}"
        else:
            path = _first_string(item, "path", "source", "uri")
            if not path:
                continue
            uri = path if "://" in path else f"kb://default/{path}"

        title = _first_string(item, "title", "name", "filename")
        specs.append(CaptureSpec(
            kind="kb",
            uri=uri,
            title=title,
            trust_tier="primary",
            inspected=True,
        ))
    return specs


def _web_trust_tier(url: str) -> SourceTrustTier:
    url_lower = url.lower()
    for suffix in _OFFICIAL_DOMAIN_SUFFIXES:
        if suffix in url_lower:
            return "official"
    return "secondary"


def _first_string(d: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_metadata(result: object) -> dict[str, object]:
    if isinstance(result, Mapping):
        m = result.get("metadata")
        if isinstance(m, Mapping):
            return dict(m)
    m = getattr(result, "metadata", None)
    if isinstance(m, Mapping):
        return dict(m)
    return {}


def _extract_output(result: object) -> object:
    if isinstance(result, Mapping):
        return result.get("output") or result.get("content") or result.get("data")
    return getattr(result, "output", None)


__all__ = [
    "CaptureSpec",
    "classify_tool_result_for_citation",
]
