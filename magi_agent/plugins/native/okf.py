"""The redaction-free ``OkfLookup`` native tool (PR2).

Lets the agent look up trusted OKF (Open Knowledge Format) documents.  Unlike
``knowledge_search`` this tool MUST NOT route through ``KnowledgeBoundary`` /
``LocalKnowledgeSourceToolBoundary`` — that scaffold aggressively redacts output
(sha-substitutes ``source_ref``, drops ``path``/``resource`` meta, exposes only
``public-safe`` previews).  OKF is human-curated *trusted* content, so this tool
returns :class:`~magi_agent.knowledge.okf.bundle_loader.OkfDoc` fields VERBATIM
per design §4.2.

Default-OFF: gated by the ``MAGI_KNOWLEDGE_OKF_*`` env switches resolved by
:func:`~magi_agent.knowledge.okf.config.resolve_okf_config`.  When the master or
lookup flag is OFF the tool returns ``blocked_result("OkfLookup", "okf_disabled")``.

The handler is ``async def`` to match the native-tool protocol but does no
awaiting — the bundle loader is synchronous (mirrors ``knowledge_search`` which
also wraps sync work).
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.knowledge.okf.bundle_loader import OkfDoc, load_bundles
from magi_agent.knowledge.okf.config import resolve_okf_config
from magi_agent.plugins.native._common import blocked_result, ok_result, workspace_root
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

_TOOL_NAME = "OkfLookup"

#: Auto-discovery subdirectory under the workspace root when no explicit
#: ``MAGI_OKF_BUNDLE_PATHS`` is configured.  Kept OUT of ``memory/`` so it never
#: intersects the memory subsystem's globs (design §8.5 invariant 1).
_WORKSPACE_BUNDLE_SUBDIR = ("knowledge", "okf")


def _resolve_bundle_roots(config_paths: tuple[str, ...], context: ToolContext) -> list[Path]:
    """Resolve bundle roots from config, else the workspace ``knowledge/okf`` dir.

    Explicit ``MAGI_OKF_BUNDLE_PATHS`` wins.  Otherwise we fall back to
    ``<workspace_root>/knowledge/okf`` *only if it exists* (no fabrication of an
    empty path).  Workspace root reuses the shared ``_common.workspace_root``
    helper — the lowest-coupling resolver already used by every other native tool
    (it reads ``context.workspace_root`` and resolves it).
    """
    if config_paths:
        return [Path(p) for p in config_paths]
    fallback = workspace_root(context).joinpath(*_WORKSPACE_BUNDLE_SUBDIR)
    return [fallback] if fallback.is_dir() else []


def _record_from_doc(doc: OkfDoc, *, max_preview_chars: int) -> dict[str, object]:
    """Map an :class:`OkfDoc` to a plain dict VERBATIM (no redaction)."""
    return {
        "path": doc.rel_path,
        "title": doc.title,
        "preview": doc.body[:max_preview_chars],
        "resource": doc.resource,
        "tags": list(doc.tags),
        "type": doc.doc_type,
        "frontmatter": dict(doc.frontmatter),
    }


async def okf_lookup(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    config = resolve_okf_config()

    # Double-gate: the catalog advertises the surface, but the env switch is the
    # real default-OFF control.
    if not config.master_enabled or not config.lookup_enabled:
        return blocked_result(_TOOL_NAME, "okf_disabled")

    query_raw = arguments.get("query")
    path_raw = arguments.get("path")
    query = str(query_raw).strip() if query_raw is not None else ""
    path = str(path_raw).strip() if path_raw is not None else ""
    if not query and not path:
        return blocked_result(_TOOL_NAME, "query_required")

    bundle_roots = _resolve_bundle_roots(config.bundle_paths, context)
    if not bundle_roots:
        return ok_result(
            _TOOL_NAME,
            {
                "records": [],
                "count": 0,
                "truncatedDocs": 0,
                "note": "no_okf_bundles_configured",
            },
        )

    index = load_bundles(bundle_roots, config=config)

    if path:
        matches = [doc for doc in index.docs if doc.rel_path == path]
    else:
        matches = index.search(query, max_records=config.max_records)

    records = [
        _record_from_doc(doc, max_preview_chars=config.max_preview_chars)
        for doc in matches
    ]
    truncated_docs = sum(1 for doc in matches if doc.truncated)

    return ok_result(
        _TOOL_NAME,
        {
            "records": records,
            "count": len(records),
            "truncatedDocs": truncated_docs,
        },
    )
