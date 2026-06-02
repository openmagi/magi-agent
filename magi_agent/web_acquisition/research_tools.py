from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerRecord,
)
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.policy import (
    normalize_query,
    redact_public_text,
    safe_metadata,
)
from magi_agent.web_acquisition.provider_boundary import (
    LocalWebAcquisitionRuntime,
    WebAcquisitionConfig,
    WebAcquisitionRequest,
    WebAcquisitionResult,
    WebAcquisitionSourceRecord,
)


ResearchToolName = Literal["WebSearch", "WebFetch"]
_TOOL_OPERATIONS: Mapping[str, str] = {
    "WebSearch": "web.search",
    "WebFetch": "web.fetch",
}


class LocalWebResearchToolBoundary:
    """Tool-style facade over the local web acquisition provider boundary."""

    fixture_only: Literal[True] = True
    tool_host_execution_allowed: Literal[False] = False
    live_authority_allowed: Literal[False] = False

    def __init__(self, *, runtime: LocalWebAcquisitionRuntime | None = None) -> None:
        self.runtime = runtime or LocalWebAcquisitionRuntime(WebAcquisitionConfig())
        self.last_result: WebAcquisitionResult | None = None

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None = None,
    ) -> ToolResult:
        if tool_name not in _TOOL_OPERATIONS:
            return _blocked_tool_result(
                tool_name,
                "web_research_tool_not_supported",
                boundary_status="blocked",
            )

        request = _request_from_tool(tool_name, arguments, context)
        result = await self.runtime.run(request)
        self.last_result = result
        if result.status != "ok":
            return _tool_result_from_non_ok(tool_name, result)

        output = _tool_output(tool_name, request, result)
        return ToolResult(
            status="ok",
            output=output,
            llmOutput=output,
            transcriptOutput={
                "toolName": tool_name,
                "resultRefs": [record.source_ref for record in result.records],
            },
            metadata=_safe_tool_metadata(tool_name, result),
        )


def project_web_acquisition_result_to_source_ledger(
    result: WebAcquisitionResult | None,
    ledger: LocalResearchSourceLedger,
    *,
    context: object | None = None,
    tool_name: str | None = None,
) -> tuple[SourceLedgerRecord, ...]:
    if result is None or result.status != "ok":
        return ()

    resolved_tool_name = tool_name or _tool_name_for_operation(result.operation)
    turn_id = _context_text(context, "turn_id", "turnId") or ledger.turn_id
    tool_use_id = _context_text(context, "tool_use_id", "toolUseId")
    records: list[SourceLedgerRecord] = []
    for record in result.records:
        payload: dict[str, object] = {
            "turnId": turn_id,
            "toolName": resolved_tool_name,
            "evidenceType": _ledger_evidence_type(result.operation),
            "kind": _ledger_kind(result.operation),
            "uri": _safe_record_url_ref(record),
            "inspected": True,
            "contentHash": record.content_digest,
            "metadata": {
                "providerId": redact_public_text(record.provider, max_chars=120),
                "webAcquisitionSourceRef": record.source_ref,
                "evidenceId": record.evidence_ref,
                "method": record.method,
                "proofType": record.proof_type,
            },
        }
        if tool_use_id is not None:
            payload["toolUseId"] = tool_use_id
        title = _clean_optional_text(record.title, max_chars=160)
        if title is not None:
            payload["title"] = title
        content_type = record.metadata.get("contentType")
        if isinstance(content_type, str) and content_type.strip():
            payload["contentType"] = redact_public_text(content_type, max_chars=120).strip()
        records.append(ledger.record_source(payload))
    return tuple(records)


def _request_from_tool(
    tool_name: str,
    arguments: Mapping[str, object],
    context: object | None,
) -> WebAcquisitionRequest:
    operation = _TOOL_OPERATIONS[tool_name]
    base: dict[str, object] = {
        "operation": operation,
        "turnId": _context_text(context, "turn_id", "turnId") or "turn-local",
    }
    if tool_name == "WebSearch":
        query = _string_arg(arguments, "query", "q")
        if query is not None:
            base["query"] = query
    elif tool_name == "WebFetch":
        url = _string_arg(arguments, "url")
        if url is not None:
            base["url"] = url
    return WebAcquisitionRequest.model_validate(base)


def _tool_result_from_non_ok(tool_name: str, result: WebAcquisitionResult) -> ToolResult:
    if result.status == "approval_required":
        status = "needs_approval"
    elif result.status == "error":
        status = "error"
    else:
        status = "blocked"
    return ToolResult(
        status=status,
        errorCode=result.error_code,
        errorMessage=redact_public_text(result.error_message or "", max_chars=240) or None,
        metadata=_safe_tool_metadata(tool_name, result),
    )


def _blocked_tool_result(
    tool_name: str,
    error_code: str,
    *,
    boundary_status: str,
) -> ToolResult:
    return ToolResult(
        status="blocked",
        errorCode=error_code,
        metadata={
            "toolName": tool_name,
            "boundaryStatus": boundary_status,
            "attachmentFlags": _default_attachment_flags(),
        },
    )


def _tool_output(
    tool_name: str,
    request: WebAcquisitionRequest,
    result: WebAcquisitionResult,
) -> dict[str, object]:
    provider_id = _provider_id(result.records)
    sources = [_source_output(record) for record in result.records]
    if tool_name == "WebSearch":
        output: dict[str, object] = {
            "toolName": tool_name,
            "query": normalize_query(request.query or ""),
            "providerId": provider_id,
            "resultRefs": [record.source_ref for record in result.records],
            "sources": sources,
        }
    else:
        first = result.records[0] if result.records else None
        metadata = dict(first.metadata) if first is not None else {}
        output = {
            "toolName": tool_name,
            "url": _safe_record_url_ref(first) if first is not None else "[redacted]",
            "providerId": provider_id,
            "inspectedSourceRefs": [record.source_ref for record in result.records],
            "sources": sources,
        }
        status = _status_code(metadata)
        if status is not None:
            output["status"] = status
            output["statusClass"] = f"{status // 100}xx"
        content_type = _clean_optional_text(metadata.get("contentType"), max_chars=120)
        if content_type is not None:
            output["contentType"] = content_type
    preview = _clean_optional_text(result.public_preview, max_chars=1_024)
    if preview is not None:
        output["publicPreview"] = preview
    return output


def _source_output(record: WebAcquisitionSourceRecord) -> dict[str, object]:
    return {
        "sourceRef": record.source_ref,
        "evidenceRef": record.evidence_ref,
        "title": _clean_optional_text(record.title, max_chars=160),
        "urlRef": _safe_record_url_ref(record),
        "contentDigest": record.content_digest,
        "proofType": record.proof_type,
        "metadata": safe_metadata(dict(record.metadata)),
    }


def _safe_tool_metadata(tool_name: str, result: WebAcquisitionResult) -> dict[str, object]:
    projection = result.public_projection()
    return {
        "toolName": tool_name,
        "boundaryStatus": result.status,
        "errorCode": result.error_code,
        "parentOutputRefs": projection["parentOutputRefs"],
        "attachmentFlags": projection["attachmentFlags"],
    }


def _default_attachment_flags() -> dict[str, bool]:
    return {
        "adkRunnerInvoked": False,
        "liveToolDispatched": False,
        "networkFetched": False,
        "browserExecuted": False,
        "rawContentInjected": False,
        "parentContextInjected": False,
        "productionAuthority": False,
    }


def _provider_id(records: tuple[WebAcquisitionSourceRecord, ...]) -> str:
    if not records:
        return "openmagi.web-acquisition.system"
    return redact_public_text(records[0].provider, max_chars=120)


def _safe_record_url_ref(record: WebAcquisitionSourceRecord | None) -> str:
    if record is None:
        return "[redacted]"
    value = record.normalized_url or record.url
    if value.startswith(("http://", "https://", "search:", "source:", "blocked-source:")):
        return redact_public_text(value, max_chars=512)
    return "[redacted]"


def _status_code(metadata: Mapping[str, object]) -> int | None:
    value = metadata.get("status") or metadata.get("statusCode")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    return None


def _clean_optional_text(value: object, *, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = redact_public_text(value, max_chars=max_chars).strip()
    return text or None


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


def _tool_name_for_operation(operation: str) -> str:
    if operation == "web.search":
        return "WebSearch"
    if operation == "web.fetch":
        return "WebFetch"
    return "WebFetch"


def _ledger_kind(operation: str) -> str:
    if operation == "web.search":
        return "web_search"
    return "web_fetch"


def _ledger_evidence_type(operation: str) -> str:
    _ = operation
    return "SourceInspection"


__all__ = [
    "LocalWebResearchToolBoundary",
    "ResearchToolName",
    "project_web_acquisition_result_to_source_ledger",
]
