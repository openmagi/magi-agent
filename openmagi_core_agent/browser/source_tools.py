from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from openmagi_core_agent.browser.provider_boundary import (
    BrowserProviderConfig,
    BrowserProviderResult,
    BrowserRequest,
    BrowserSourceRecord,
    LocalBrowserProviderRuntime,
)
from openmagi_core_agent.web_acquisition.policy import redact_public_text

if TYPE_CHECKING:
    from openmagi_core_agent.evidence.source_ledger import (
        LocalResearchSourceLedger,
        SourceLedgerRecord,
    )
    from openmagi_core_agent.tools.result import ToolResult


_TOOL_ACTIONS: Mapping[str, str] = {
    "BrowserOpen": "browser.open",
    "BrowserSnapshot": "browser.snapshot",
    "BrowserScrape": "browser.scrape",
    "BrowserClick": "browser.click",
    "BrowserFill": "browser.fill",
    "BrowserScroll": "browser.scroll",
    "BrowserScreenshot": "browser.screenshot",
}


class LocalBrowserSourceToolBoundary:
    """Tool-style facade over the default-off local browser provider runtime."""

    def __init__(self, *, runtime: LocalBrowserProviderRuntime | None = None) -> None:
        self.runtime = runtime or LocalBrowserProviderRuntime(BrowserProviderConfig())
        self.last_result: BrowserProviderResult | None = None

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None = None,
    ) -> "ToolResult":
        action = _TOOL_ACTIONS.get(
            tool_name,
            tool_name if tool_name in _TOOL_ACTIONS.values() else None,
        )
        if action is None:
            return _blocked_tool_result(tool_name, "browser_source_tool_not_supported")

        request = _request_from_tool(action, arguments, context)
        result = await self.runtime.run(request)
        self.last_result = result
        if result.status != "ok":
            return _tool_result_from_non_ok(tool_name, result)

        output = _tool_output(tool_name, result)
        artifact_refs = tuple(
            ref
            for ref in (
                record.artifact_ref for record in result.records if record.artifact_ref is not None
            )
        )
        return _tool_result(
            status="ok",
            output=output,
            llmOutput=output,
            transcriptOutput={
                "toolName": tool_name,
                "parentOutputRefs": output["parentOutputRefs"],
                "artifactRefs": artifact_refs,
            },
            artifactRefs=artifact_refs,
            metadata=_safe_tool_metadata(tool_name, result),
        )


def project_browser_result_to_source_ledger(
    result: BrowserProviderResult | None,
    ledger: LocalResearchSourceLedger,
    *,
    context: object | None = None,
    tool_name: str | None = None,
) -> tuple[SourceLedgerRecord, ...]:
    if result is None or result.status != "ok":
        return ()

    resolved_tool_name = tool_name or _tool_name_for_action(result.action)
    turn_id = _context_text(context, "turn_id", "turnId") or ledger.turn_id
    tool_use_id = _context_text(context, "tool_use_id", "toolUseId")
    records: list[SourceLedgerRecord] = []
    for record in result.records:
        metadata: dict[str, object] = {
            "providerId": redact_public_text(record.provider, max_chars=120),
            "browserSourceRef": record.source_ref,
            "evidenceId": record.evidence_ref,
            "method": record.method,
            "proofType": record.proof_type,
        }
        if record.artifact_ref is not None:
            metadata["artifactRef"] = record.artifact_ref
        payload: dict[str, object] = {
            "turnId": turn_id,
            "toolName": resolved_tool_name,
            "evidenceType": "SourceInspection",
            "kind": "browser",
            "uri": _safe_record_uri(record),
            "inspected": True,
            "contentHash": record.content_digest,
            "metadata": metadata,
        }
        if tool_use_id is not None:
            payload["toolUseId"] = tool_use_id
        if record.title is not None:
            payload["title"] = redact_public_text(record.title, max_chars=160).strip()
        records.append(ledger.record_source(payload))
    return tuple(records)


def _request_from_tool(
    action: str,
    arguments: Mapping[str, object],
    context: object | None,
) -> BrowserRequest:
    payload: dict[str, object] = {
        "action": action,
        "turnId": _context_text(context, "turn_id", "turnId") or "turn-local",
    }
    for arg_key, payload_key in (
        ("sessionId", "sessionId"),
        ("url", "url"),
        ("selector", "selector"),
        ("text", "text"),
        ("direction", "direction"),
        ("screenshotPath", "screenshotPath"),
    ):
        if arg_key in arguments:
            payload[payload_key] = arguments[arg_key]
    if _context_bool(context, "approval_granted", "approvalGranted"):
        payload["approvalGranted"] = True
    return BrowserRequest.model_validate(payload)


def _tool_result_from_non_ok(tool_name: str, result: BrowserProviderResult) -> "ToolResult":
    if result.status == "approval_required":
        status = "needs_approval"
    elif result.status == "error":
        status = "error"
    else:
        status = "blocked"
    return _tool_result(
        status=status,
        errorCode=result.error_code,
        errorMessage=result.error_message,
        metadata=_safe_tool_metadata(tool_name, result),
    )


def _blocked_tool_result(tool_name: str, error_code: str) -> "ToolResult":
    return _tool_result(
        status="blocked",
        errorCode=error_code,
        metadata={"toolName": tool_name, "boundaryStatus": "blocked"},
    )


def _tool_output(tool_name: str, result: BrowserProviderResult) -> dict[str, object]:
    projection = result.public_projection()
    return {
        "toolName": tool_name,
        "action": result.action,
        "sourceRecords": tuple(projection["sourceRecords"]),
        "parentOutputRefs": tuple(projection["parentOutputRefs"]),
        "browserFrame": projection["browserFrame"],
        "attachmentFlags": projection["attachmentFlags"],
    }


def _safe_tool_metadata(
    tool_name: str,
    result: BrowserProviderResult,
) -> dict[str, object]:
    projection = result.public_projection()
    return {
        "toolName": tool_name,
        "boundaryStatus": result.status,
        "errorCode": result.error_code,
        "parentOutputRefs": projection["parentOutputRefs"],
        "attachmentFlags": projection["attachmentFlags"],
    }


def _safe_record_uri(record: BrowserSourceRecord) -> str:
    value = record.normalized_url or record.url
    if value.startswith(("http://", "https://", "browser:")):
        return value
    return "browser:session"


def _tool_name_for_action(action: str) -> str:
    for tool_name, mapped_action in _TOOL_ACTIONS.items():
        if mapped_action == action:
            return tool_name
    return "BrowserSnapshot"


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


def _context_bool(context: object | None, *names: str) -> bool:
    if context is None:
        return False
    for name in names:
        if getattr(context, name, None) is True:
            return True
    if isinstance(context, Mapping):
        return any(context.get(name) is True for name in names)
    return False


def _tool_result(**kwargs: Any) -> "ToolResult":
    from openmagi_core_agent.tools.result import ToolResult

    return ToolResult(**kwargs)


__all__ = [
    "LocalBrowserSourceToolBoundary",
    "project_browser_result_to_source_ledger",
]
