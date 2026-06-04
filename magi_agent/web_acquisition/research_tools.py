from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from typing import Literal

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerRecord,
)
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.live_provider_pack import (
    OPERATION_TO_PROVIDER_NAME,
    LiveWebAcquisitionProviderPack,
    WebAcquisitionLiveSourceRecord,
    WebAcquisitionProviderRequest,
    WebAcquisitionProviderResult,
)
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
# Bare live operations consumed by LiveWebAcquisitionProviderPack (search/fetch/...).
_TOOL_LIVE_OPERATIONS: Mapping[str, str] = {
    "WebSearch": "search",
    "WebFetch": "fetch",
}

LIVE_WEB_ACQUISITION_ENABLED_ENV = "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED"
LIVE_WEB_ACQUISITION_KILL_SWITCH_ENV = "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_KILL_SWITCH"
# Duplicated deliberately to match the harness/canary env-gate convention
# (see research_first_canary.py) rather than importing a shared helper.
_TRUE_VALUES = frozenset({"1", "on", "true", "yes"})
# Mirrors the live-pack ref grammar so digest refs we mint stay valid public ids.
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")


def _is_true(value: object) -> bool:
    return str(value or "").strip().casefold() in _TRUE_VALUES


def live_web_acquisition_active(*, env: Mapping[str, str] | None = None) -> bool:
    resolved_env = os.environ if env is None else env
    return _is_true(
        resolved_env.get(LIVE_WEB_ACQUISITION_ENABLED_ENV)
    ) and not _is_true(resolved_env.get(LIVE_WEB_ACQUISITION_KILL_SWITCH_ENV))


class LocalWebResearchToolBoundary:
    """Tool-style facade over the local web acquisition provider boundary."""

    # These sealed class attrs stay UNCHANGED: PR3a ships only StubLiveProvider
    # (canned, zero network), so the boundary remains fixture-only in practice.
    # Real-provider injection + a seal revisit is PR3b.
    fixture_only: Literal[True] = True
    tool_host_execution_allowed: Literal[False] = False
    live_authority_allowed: Literal[False] = False

    def __init__(
        self,
        *,
        runtime: LocalWebAcquisitionRuntime | None = None,
        live_pack: LiveWebAcquisitionProviderPack | None = None,
        live_provider: object | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.runtime = runtime or LocalWebAcquisitionRuntime(WebAcquisitionConfig())
        self.last_result: WebAcquisitionResult | None = None
        self._live_pack = live_pack
        self._live_provider = live_provider
        self._env = env
        self.last_live_result: WebAcquisitionProviderResult | None = None

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

        if (
            live_web_acquisition_active(env=self._env)
            and self._live_pack is not None
            and self._live_provider is not None
        ):
            return self._execute_tool_live(tool_name, arguments, context)

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

    def _execute_tool_live(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None,
    ) -> ToolResult:
        assert self._live_pack is not None  # caller guarantees gate + wiring
        provider_request = _live_request_from_tool(tool_name, arguments, context)
        result = self._live_pack.run(provider_request, provider=self._live_provider)
        self.last_live_result = result
        if result.status != "ok":
            return _tool_result_from_non_ok_live(tool_name, result)

        output = _tool_output_live(tool_name, provider_request, result)
        return ToolResult(
            status="ok",
            output=output,
            llmOutput=output,
            transcriptOutput={
                "toolName": tool_name,
                "resultRefs": [record.source_ref for record in result.source_records],
            },
            metadata=_safe_tool_metadata_live(tool_name, result),
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


def project_live_web_acquisition_result_to_source_ledger(
    result: WebAcquisitionProviderResult | None,
    ledger: LocalResearchSourceLedger,
    *,
    context: object | None = None,
    tool_name: str | None = None,
) -> tuple[SourceLedgerRecord, ...]:
    """Live-record parity of ``project_web_acquisition_result_to_source_ledger``.

    The live record (``WebAcquisitionLiveSourceRecord``) already carries a
    redacted ``url_ref`` (``url:<digest>``) instead of a raw URL, so projection
    is leak-safe by construction.
    """
    if result is None or result.status != "ok":
        return ()

    resolved_tool_name = tool_name or _tool_name_for_live_operation(result.operation)
    turn_id = _context_text(context, "turn_id", "turnId") or ledger.turn_id
    tool_use_id = _context_text(context, "tool_use_id", "toolUseId")
    records: list[SourceLedgerRecord] = []
    for record in result.source_records:
        payload: dict[str, object] = {
            "turnId": turn_id,
            "toolName": resolved_tool_name,
            "evidenceType": _ledger_evidence_type(result.operation),
            "kind": _ledger_kind_live(result.operation),
            "uri": record.url_ref,
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


def _live_request_from_tool(
    tool_name: str,
    arguments: Mapping[str, object],
    context: object | None,
) -> WebAcquisitionProviderRequest:
    operation = _TOOL_LIVE_OPERATIONS[tool_name]
    turn_id = _context_text(context, "turn_id", "turnId") or "turn-local"
    base: dict[str, object] = {
        "operation": operation,
        "requestId": _live_safe_ref(turn_id, fallback="req-local"),
        "providerName": OPERATION_TO_PROVIDER_NAME[operation],
        "botIdDigest": _live_digest_ref(context, ("bot_id", "botId"), fallback="bot-local"),
        "ownerIdDigest": _live_digest_ref(context, ("user_id", "userId"), fallback="owner-local"),
        "sessionKeyDigest": _live_digest_ref(
            context, ("session_key", "sessionKey", "session_id", "sessionId"), fallback="session-local"
        ),
    }
    if tool_name == "WebSearch":
        query = _string_arg(arguments, "query", "q")
        if query is not None:
            base["query"] = query
    elif tool_name == "WebFetch":
        url = _string_arg(arguments, "url")
        if url is not None:
            base["url"] = url
    return WebAcquisitionProviderRequest.model_validate(base)


def _tool_result_from_non_ok_live(
    tool_name: str,
    result: WebAcquisitionProviderResult,
) -> ToolResult:
    if result.status == "approval_required":
        status = "needs_approval"
    elif result.status in {"repair_required", "no_answer"}:
        status = "error"
    else:
        status = "blocked"
    error_code = result.reason_codes[0] if result.reason_codes else None
    return ToolResult(
        status=status,
        errorCode=error_code,
        metadata=_safe_tool_metadata_live(tool_name, result),
    )


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


def _tool_output_live(
    tool_name: str,
    request: WebAcquisitionProviderRequest,
    result: WebAcquisitionProviderResult,
) -> dict[str, object]:
    provider_id = _provider_id_live(result.source_records)
    sources = [_source_output_live(record) for record in result.source_records]
    if tool_name == "WebSearch":
        output: dict[str, object] = {
            "toolName": tool_name,
            "query": normalize_query(request.query or ""),
            "providerId": provider_id,
            "resultRefs": [record.source_ref for record in result.source_records],
            "sources": sources,
        }
    else:
        first = result.source_records[0] if result.source_records else None
        metadata = dict(first.metadata) if first is not None else {}
        output = {
            "toolName": tool_name,
            "url": first.url_ref if first is not None else "[redacted]",
            "providerId": provider_id,
            "inspectedSourceRefs": [record.source_ref for record in result.source_records],
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


def _source_output_live(record: WebAcquisitionLiveSourceRecord) -> dict[str, object]:
    return {
        "sourceRef": record.source_ref,
        "evidenceRef": record.evidence_ref,
        "title": _clean_optional_text(record.title, max_chars=160),
        # Live records carry a pre-redacted urlRef (url:<digest>); never raw URLs.
        "urlRef": record.url_ref,
        "contentDigest": record.content_digest,
        "proofType": record.proof_type,
        "metadata": safe_metadata(dict(record.metadata)),
    }


def _safe_tool_metadata_live(
    tool_name: str,
    result: WebAcquisitionProviderResult,
) -> dict[str, object]:
    projection = result.public_projection()
    return {
        "toolName": tool_name,
        "boundaryStatus": result.status,
        "errorCode": result.reason_codes[0] if result.reason_codes else None,
        "parentOutputRefs": projection["parentOutputRefs"],
        "attachmentFlags": projection["authorityFlags"],
    }


def _provider_id_live(records: tuple[WebAcquisitionLiveSourceRecord, ...]) -> str:
    if not records:
        return "openmagi.web-acquisition.system"
    return redact_public_text(records[0].provider, max_chars=120)


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


def _tool_name_for_live_operation(operation: str) -> str:
    if operation == "search":
        return "WebSearch"
    if operation == "fetch":
        return "WebFetch"
    return "WebFetch"


def _ledger_kind(operation: str) -> str:
    if operation == "web.search":
        return "web_search"
    return "web_fetch"


def _ledger_kind_live(operation: str) -> str:
    if operation == "search":
        return "web_search"
    return "web_fetch"


def _ledger_evidence_type(operation: str) -> str:
    _ = operation
    return "SourceInspection"


def _live_safe_ref(value: str, *, fallback: str) -> str:
    """Coerce a context string into a public ref accepted by the live request.

    The live request validators only admit ``^[A-Za-z][A-Za-z0-9_.:-]{1,180}$``
    public identifiers (never raw secrets/PII). Anything that does not match is
    hashed to a stable, non-reversing digest ref so we still get a deterministic
    correlation handle without leaking the source value.
    """
    cleaned = redact_public_text(value.strip(), max_chars=180)
    if cleaned and _REF_RE.fullmatch(cleaned):
        return cleaned
    return fallback


def _live_digest_ref(context: object | None, names: tuple[str, ...], *, fallback: str) -> str:
    raw = _context_text(context, *names)
    if raw is None:
        return f"{fallback}:{_short_digest(fallback)}"
    safe = _live_safe_ref(raw, fallback="")
    if safe:
        return safe
    # Never embed the raw value; emit a stable digest ref instead.
    return f"digest:{_short_digest(raw)}"


def _short_digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "LIVE_WEB_ACQUISITION_ENABLED_ENV",
    "LIVE_WEB_ACQUISITION_KILL_SWITCH_ENV",
    "LocalWebResearchToolBoundary",
    "ResearchToolName",
    "live_web_acquisition_active",
    "project_live_web_acquisition_result_to_source_ledger",
    "project_web_acquisition_result_to_source_ledger",
]
