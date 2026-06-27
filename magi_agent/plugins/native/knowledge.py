from __future__ import annotations

import hashlib
from pathlib import Path

from magi_agent.knowledge.local_index import search_local_knowledge
from magi_agent.knowledge.qmd_index import search_knowledge_via_qmd
from magi_agent.knowledge.provider_boundary import (
    KnowledgeBoundary,
    KnowledgeBoundaryConfig,
    KnowledgeBoundaryRequest,
)
from magi_agent.knowledge.source_tools import LocalKnowledgeSourceToolBoundary
from magi_agent.plugins.native._common import blocked_result, ok_result
from magi_agent.plugins.native._hosted_knowledge import hosted_egress
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import redact_public_text


class _LocalKnowledgeProvider:
    # In-process, non-network local provider. The boundary trusts only providers
    # carrying this marker (it gates network/production providers off); despite
    # the historical name it now reads the operator's real on-disk knowledge base
    # under ``<workspace>/knowledge/`` rather than returning a placeholder.
    openmagi_local_fake_provider = True

    def __init__(self, workspace_root: str | None = None) -> None:
        self._workspace_root = workspace_root

    def execute(self, request: KnowledgeBoundaryRequest) -> dict[str, object]:
        query = request.query or ""
        if not self._workspace_root:
            return {"records": ()}
        roots = [Path(self._workspace_root)]
        # Prefer the qmd-indexed path (BM25 ranking + scale) when a KB collection
        # is registered; otherwise fall back to the dependency-free linear scan.
        via_qmd = search_knowledge_via_qmd(
            roots, query, k=5, auto_register=_prefer_qmd_auto_register()
        )
        records = (
            via_qmd
            if via_qmd is not None
            else search_local_knowledge(roots, query, limit=5)
        )
        return {"records": tuple(records)}


def _prefer_qmd_auto_register() -> bool:
    """Whether the search path may lazily register a NEW global qmd collection.

    Mirrors the memory subsystem's multi-tenant-safe opt-in
    (``MAGI_MEMORY_PREFER_QMD_AUTO_REGISTER``). Default False: an explicit
    ``magi knowledge init`` is the normal way to register. Fail-soft.
    """
    try:
        from magi_agent.memory.config import resolve_memory_config  # noqa: PLC0415

        return bool(resolve_memory_config().prefer_qmd_auto_register)
    except Exception:  # noqa: BLE001
        return False


def _boundary(context: ToolContext | None = None) -> LocalKnowledgeSourceToolBoundary:
    workspace_root = getattr(context, "workspace_root", None)
    return LocalKnowledgeSourceToolBoundary(
        boundary=KnowledgeBoundary(
            KnowledgeBoundaryConfig(enabled=True, localFakeProviderEnabled=True)
        ),
        provider=_LocalKnowledgeProvider(workspace_root=workspace_root),
    )


async def knowledge_search(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    hosted = hosted_egress()
    if hosted is not None:
        return await hosted(arguments, context)
    return await _boundary(context).execute_tool("KnowledgeSearch", arguments, context)


def knowledge_write(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    content = redact_public_text(str(arguments.get("content") or arguments.get("text") or ""), max_chars=2000).strip()
    if not content:
        return blocked_result("KnowledgeWrite", "content_required")
    collection = redact_public_text(str(arguments.get("collection") or "local"), max_chars=80).strip() or "local"
    return ok_result(
        "KnowledgeWrite",
        {
            "collection": collection,
            "contentDigest": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "localOnly": True,
        },
    )
