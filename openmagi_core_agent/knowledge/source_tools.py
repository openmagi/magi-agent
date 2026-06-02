from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from openmagi_core_agent.knowledge.provider_boundary import (
    KnowledgeBoundary,
    KnowledgeBoundaryConfig,
    KnowledgeBoundaryDecision,
    KnowledgeBoundaryRequest,
    KnowledgeProviderPort,
    KnowledgeSourceRecord,
)
from openmagi_core_agent.web_acquisition.policy import redact_public_text

if TYPE_CHECKING:
    from openmagi_core_agent.evidence.source_ledger import (
        LocalResearchSourceLedger,
        SourceLedgerRecord,
    )
    from openmagi_core_agent.tools.result import ToolResult


class LocalKnowledgeSourceToolBoundary:
    """Tool-style facade over the default-off local knowledge boundary."""

    def __init__(
        self,
        *,
        boundary: KnowledgeBoundary | None = None,
        provider: KnowledgeProviderPort | None = None,
    ) -> None:
        self.boundary = boundary or KnowledgeBoundary(KnowledgeBoundaryConfig())
        self.provider = provider
        self.last_decision: KnowledgeBoundaryDecision | None = None

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None = None,
    ) -> "ToolResult":
        if tool_name != "KnowledgeSearch":
            return _blocked_tool_result(tool_name, "knowledge_source_tool_not_supported")

        request = KnowledgeBoundaryRequest(
            operation="knowledge.search",
            query=_string_arg(arguments, "query", "q"),
            metadata={"turnId": _context_text(context, "turn_id", "turnId") or "turn-local"},
        )
        decision = await self.boundary.execute(request, provider=self.provider)
        self.last_decision = decision
        if decision.status != "ok":
            return _tool_result_from_non_ok(tool_name, decision)

        output = {
            "toolName": tool_name,
            "query": redact_public_text(request.query or "", max_chars=512),
            "resultRefs": tuple(_projected_ref(record, "sourceRef") for record in decision.records),
            "evidenceRefs": tuple(_projected_ref(record, "evidenceRef") for record in decision.records),
            "sources": tuple(_source_output(record) for record in decision.records),
        }
        return _tool_result(
            status="ok",
            output=output,
            llmOutput=output,
            transcriptOutput={
                "toolName": tool_name,
                "resultRefs": tuple(_projected_ref(record, "sourceRef") for record in decision.records),
                "evidenceRefs": tuple(_projected_ref(record, "evidenceRef") for record in decision.records),
            },
            metadata=_safe_tool_metadata(tool_name, decision),
        )


def project_knowledge_result_to_source_ledger(
    decision: KnowledgeBoundaryDecision | None,
    ledger: LocalResearchSourceLedger,
    *,
    context: object | None = None,
    tool_name: str = "KnowledgeSearch",
) -> tuple[SourceLedgerRecord, ...]:
    if decision is None or decision.status != "ok" or decision.operation != "knowledge.search":
        return ()

    turn_id = _context_text(context, "turn_id", "turnId") or ledger.turn_id
    tool_use_id = _context_text(context, "tool_use_id", "toolUseId")
    records: list[SourceLedgerRecord] = []
    for record in decision.records:
        projected = record.public_projection()
        source_ref = str(projected["sourceRef"])
        evidence_ref = str(projected["evidenceRef"])
        metadata: dict[str, object] = {
            "providerId": redact_public_text(record.provider, max_chars=120),
            "knowledgeSourceRef": source_ref,
            "evidenceId": evidence_ref,
            "operation": record.operation,
            "visibility": _visibility(record),
            "sourcePrecedence": "below_current_turn_user_sources",
            "currentTurnUserSourcePriority": "higher",
            "priorityClass": "background_knowledge",
        }
        topic = record.metadata.get("topic") if _is_public_safe(record) else None
        if isinstance(topic, str) and topic.strip():
            metadata["topic"] = redact_public_text(topic, max_chars=120).strip()

        payload: dict[str, object] = {
            "turnId": turn_id,
            "toolName": tool_name,
            "evidenceType": "KnowledgeSearch",
            "kind": "kb",
            "uri": f"knowledge-ref:{source_ref}",
            "inspected": True,
            "contentHash": record.content_digest,
            "metadata": metadata,
            "trustTier": "unknown",
        }
        if tool_use_id is not None:
            payload["toolUseId"] = tool_use_id
        if _is_public_safe(record) and record.title is not None:
            payload["title"] = redact_public_text(record.title, max_chars=160).strip()
        if _is_public_safe(record) and record.preview:
            payload["snippets"] = (record.preview,)
        records.append(ledger.record_source(payload))
    return tuple(records)


def _tool_result_from_non_ok(
    tool_name: str,
    decision: KnowledgeBoundaryDecision,
) -> "ToolResult":
    status = "error" if decision.status == "error" else "blocked"
    return _tool_result(
        status=status,
        errorCode=decision.reason_codes[0] if decision.reason_codes else decision.status,
        metadata=_safe_tool_metadata(tool_name, decision),
    )


def _blocked_tool_result(tool_name: str, error_code: str) -> "ToolResult":
    return _tool_result(
        status="blocked",
        errorCode=error_code,
        metadata={"toolName": tool_name, "boundaryStatus": "blocked"},
    )


def _source_output(record: KnowledgeSourceRecord) -> dict[str, object]:
    projected = record.public_projection()
    output: dict[str, object] = {
        "sourceRef": projected["sourceRef"],
        "evidenceRef": projected["evidenceRef"],
        "title": projected["title"],
        "contentDigest": record.content_digest,
        "visibility": _visibility(record),
    }
    if projected["preview"] is not None:
        output["publicPreview"] = projected["preview"]
    return output


def _safe_tool_metadata(
    tool_name: str,
    decision: KnowledgeBoundaryDecision,
) -> dict[str, object]:
    projection = decision.public_projection()
    return {
        "toolName": tool_name,
        "boundaryStatus": decision.status,
        "parentOutputRefs": projection["parentOutputRefs"],
        "reasonCodes": projection["reasonCodes"],
        "sourcePrecedence": "below_current_turn_user_sources",
    }


def _visibility(record: KnowledgeSourceRecord) -> str:
    value = record.metadata.get("visibility")
    if isinstance(value, str) and value.strip():
        return redact_public_text(value, max_chars=80).strip()
    if record.metadata.get("publicSafe") is True:
        return "public-safe"
    return "private"


def _is_public_safe(record: KnowledgeSourceRecord) -> bool:
    visibility = _visibility(record).casefold().replace("_", "-")
    return visibility in {"public-safe", "public"} or record.metadata.get(
        "publicSafe"
    ) is True


def _projected_ref(record: KnowledgeSourceRecord, key: str) -> str:
    value = record.public_projection()[key]
    return str(value)


def _string_arg(arguments: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    return None


def _context_text(context: object | None, *names: str) -> str | None:
    if context is None:
        return None
    for name in names:
        value = getattr(context, name, None)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(context, Mapping):
        for name in names:
            value = context.get(name)
            if isinstance(value, str) and value.strip():
                return value
    return None


def _tool_result(**kwargs: Any) -> "ToolResult":
    from openmagi_core_agent.tools.result import ToolResult

    return ToolResult(**kwargs)


__all__ = [
    "LocalKnowledgeSourceToolBoundary",
    "project_knowledge_result_to_source_ledger",
]
