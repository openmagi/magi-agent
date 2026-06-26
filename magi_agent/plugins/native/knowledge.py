from __future__ import annotations

import hashlib

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
    openmagi_local_fake_provider = True

    def execute(self, request: KnowledgeBoundaryRequest) -> dict[str, object]:
        query = request.query or "local knowledge"
        return {
            "records": (
                {
                    "sourceRef": "knowledge:local",
                    "title": f"Local KB result for {query}",
                    "snippet": f"Local first-party KB result for {query}. Configure a KB provider for hosted collections.",
                    "metadata": {"visibility": "public-safe", "publicSafe": True},
                },
            )
        }


def _boundary() -> LocalKnowledgeSourceToolBoundary:
    return LocalKnowledgeSourceToolBoundary(
        boundary=KnowledgeBoundary(
            KnowledgeBoundaryConfig(enabled=True, localFakeProviderEnabled=True)
        ),
        provider=_LocalKnowledgeProvider(),
    )


async def knowledge_search(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    hosted = hosted_egress()
    if hosted is not None:
        return await hosted(arguments, context)
    return await _boundary().execute_tool("KnowledgeSearch", arguments, context)


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
