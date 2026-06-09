from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

from magi_agent.evidence.source_ledger import (
    LocalResearchSourceLedger,
    SourceLedgerRecord,
)
from magi_agent.tools.result import ToolResult
from magi_agent.web_acquisition.live_provider_pack import (
    OPERATION_TO_PROVIDER_NAME,
    LiveWebAcquisitionPackConfig,
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

if TYPE_CHECKING:
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )


ResearchToolName = Literal["WebSearch", "WebFetch", "WebReader"]
_TOOL_OPERATIONS: Mapping[str, str] = {
    "WebSearch": "web.search",
    "WebFetch": "web.fetch",
}
# Bare live operations consumed by LiveWebAcquisitionProviderPack (search/fetch/...).
_TOOL_LIVE_OPERATIONS: Mapping[str, str] = {
    "WebSearch": "search",
    "WebFetch": "fetch",
    "WebReader": "reader",
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
        provider_router: "WebAcquisitionProviderRouter | None" = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.runtime = runtime or LocalWebAcquisitionRuntime(WebAcquisitionConfig())
        self.last_result: WebAcquisitionResult | None = None
        self._live_pack = live_pack
        self._live_provider = live_provider
        self._provider_router = provider_router
        self._env = env
        self.last_live_result: WebAcquisitionProviderResult | None = None

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None = None,
    ) -> ToolResult:
        # WebReader is a live-only tool (no local-runtime equivalent).
        is_live_only = tool_name not in _TOOL_OPERATIONS

        if tool_name not in _TOOL_LIVE_OPERATIONS:
            return _blocked_tool_result(
                tool_name,
                "web_research_tool_not_supported",
                boundary_status="blocked",
            )

        if live_web_acquisition_active(env=self._env):
            # Router path: when a provider router is wired and enabled, prefer it.
            if self._provider_router is not None and self._provider_router.config.enabled:
                return self._execute_tool_via_router(tool_name, arguments, context)
            # Direct live-pack path: legacy wiring without router.
            if not is_live_only and self._live_pack is not None and self._live_provider is not None:
                return self._execute_tool_live(tool_name, arguments, context)

        if is_live_only:
            # WebReader has no local-runtime fallback.
            return _blocked_tool_result(
                tool_name,
                "web_research_live_required_for_reader",
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

    def _execute_tool_live(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None,
    ) -> ToolResult:
        assert self._live_pack is not None  # caller guarantees gate + wiring
        provider_request = _live_request_from_tool(tool_name, arguments, context)
        # PR3b guard: live_pack.run() owns provider-boundary fail-closed behavior.
        # When PR3b wires real network providers, keep connection errors, timeouts,
        # async-provider output, and provider status values contained there so this
        # tool seam can project a blocked/repair_required ToolResult.
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

    def _execute_tool_via_router(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: object | None,
    ) -> ToolResult:
        """Dispatch through the ``WebAcquisitionProviderRouter`` (PR-A / PR-C path)."""
        assert self._provider_router is not None  # caller guarantees gate
        provider_request = _live_request_from_tool(tool_name, arguments, context)
        result = self._provider_router.run(provider_request)
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
    # NOTE: _live_request_from_tool is intentionally separate from _request_from_tool.
    # The legacy and live operation namespaces differ ("web.search" vs "search") and
    # their request/record types differ, so the two helper families are deliberate
    # duplicates rather than a shared abstraction.
    operation = _TOOL_LIVE_OPERATIONS[tool_name]
    turn_id = _context_text(context, "turn_id", "turnId") or "turn-local"
    # requestId must be per-call-distinct, not just per-turn. A single turn may
    # issue both WebSearch and WebFetch (and in future, multiple fetches), which
    # would collide if we based requestId on turn_id alone. Include the tool name
    # and tool_use_id (when present) as discriminators so each call gets a unique
    # correlation handle through the safe-ref / digest path.
    tool_use_id = _context_text(context, "tool_use_id", "toolUseId")
    if tool_use_id is not None:
        raw_request_id = f"{turn_id}:{tool_name}:{tool_use_id}"
    else:
        raw_request_id = f"{turn_id}:{tool_name}"
    base: dict[str, object] = {
        "operation": operation,
        "requestId": _live_safe_ref(raw_request_id, fallback=f"req-{tool_name.lower()}-local"),
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
    elif tool_name in {"WebFetch", "WebReader"}:
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
    # Intentional: errorMessage is omitted here. WebAcquisitionProviderResult
    # exposes no free-text error message (only reason_codes), so we cannot
    # surface a redacted string; the caller receives only the errorCode.
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
    # PR3b guard: safe_metadata() is a key-denylist + URL/secret-regex redactor.
    # It does NOT scrub bare hostnames inside metadata VALUES (e.g. a provider
    # may embed "https://internal.corp/..." in a "snippet" or custom key).
    # Today this is safe because only the canned StubLiveProvider feeds metadata.
    # Before any real httpx provider ships (PR3b), provider-controlled metadata
    # values must go through a host/URL-aware redactor, or the metadata surface
    # must be switched to an allowlist of known-safe keys.
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
    # PR3b guard: public_projection() calls safe_metadata() on diagnosticMetadata
    # (provider-controlled). Same hostname-in-values caveat as _source_output_live
    # applies; see that function's PR3b comment for the full mitigation note.
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


# ---------------------------------------------------------------------------
# build_live_research_boundary — PR-C factory
# ---------------------------------------------------------------------------

MAGI_PLATFORM_BASE_URL_ENV = "MAGI_PLATFORM_BASE_URL"
MAGI_PLATFORM_API_KEY_ENV = "MAGI_PLATFORM_API_KEY"
PROVIDER_ROUTER_ENABLED_ENV = "CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED"

# Names used in the provider allowlist and router providers dict.
PLATFORM_SEARCH_PROVIDER_NAME = "platform.search"
PLATFORM_FETCH_PROVIDER_NAME = "platform.fetch"

# Jina Reader provider — default-OFF, lazily imported.
JINA_READER_PROVIDER_NAME = "jina.reader"
JINA_READER_ENABLED_ENV = "CORE_AGENT_PYTHON_JINA_READER_ENABLED"
MAGI_JINA_API_KEY_ENV = "MAGI_JINA_API_KEY"

# InsaneFetch (curl_cffi WAF-bypass) provider — default-OFF, lazily imported.
INSANE_FETCH_PROVIDER_NAME = "insane.fetch"
INSANE_FETCH_ENABLED_ENV = "CORE_AGENT_PYTHON_INSANE_FETCH_ENABLED"


def build_live_research_boundary(
    env: Mapping[str, str] | None = None,
    *,
    pack_config: "LiveWebAcquisitionPackConfig | None" = None,
    router_config: "ProviderRouterConfig | None" = None,
) -> "LocalWebResearchToolBoundary":
    """Assemble a ``LocalWebResearchToolBoundary`` from environment variables.

    Three levels must all be True to reach real network calls:

    1. ``CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED=1`` (and kill-switch unset)
       — checked by ``live_web_acquisition_active()``.
    2. ``pack_config`` with ``live_network_enabled=True`` and a non-empty
       ``provider_allowlist`` — or auto-built from env when ``pack_config`` is None.
    3. ``router_config`` with ``enabled=True`` and a non-empty ``providers`` tuple
       — or auto-built from env when ``router_config`` is None.

    When ``MAGI_PLATFORM_BASE_URL`` and ``MAGI_PLATFORM_API_KEY`` are present in
    the environment, a ``PlatformEndpointProvider`` is constructed and wired as
    the primary provider.  If neither env var is set, the router's provider list
    is empty and no live network calls are made.

    Parameters
    ----------
    env:
        Environment mapping (defaults to ``os.environ``).
    pack_config:
        Override the automatically-derived ``LiveWebAcquisitionPackConfig``.
    router_config:
        Override the automatically-derived ``ProviderRouterConfig``.

    Returns
    -------
    LocalWebResearchToolBoundary
        A fully-wired boundary.  When the env gate is off or no providers are
        configured, the boundary silently falls back to the local-fixture path
        — identical to current behaviour.
    """
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
        build_provider_router,
    )

    resolved_env: Mapping[str, str] = os.environ if env is None else env

    base_url = resolved_env.get(MAGI_PLATFORM_BASE_URL_ENV, "").strip()
    api_key = resolved_env.get(MAGI_PLATFORM_API_KEY_ENV, "").strip()
    router_enabled = _is_true(resolved_env.get(PROVIDER_ROUTER_ENABLED_ENV, ""))

    # Build providers dict.
    providers: dict[str, object] = {}
    provider_names: list[str] = []
    if base_url and api_key:
        from magi_agent.web_acquisition.providers.platform_endpoint import (
            PlatformEndpointProvider,
        )

        platform_provider = PlatformEndpointProvider(
            base_url=base_url,
            api_key=api_key,
        )
        providers[PLATFORM_SEARCH_PROVIDER_NAME] = platform_provider
        providers[PLATFORM_FETCH_PROVIDER_NAME] = platform_provider
        provider_names.extend([PLATFORM_SEARCH_PROVIDER_NAME, PLATFORM_FETCH_PROVIDER_NAME])

    # InsaneFetch (curl_cffi WAF-bypass) — fallback fetch provider, default-OFF.
    # Ordered AFTER platform so platform remains primary; insane.fetch is the
    # first non-platform fallback for fetch operations.
    if _is_true(resolved_env.get(INSANE_FETCH_ENABLED_ENV)):
        from magi_agent.web_acquisition.providers.insane_fetch import (
            InsaneFetchProvider,
        )

        insane_fetch_provider = InsaneFetchProvider()
        providers[INSANE_FETCH_PROVIDER_NAME] = insane_fetch_provider
        provider_names.append(INSANE_FETCH_PROVIDER_NAME)

    # Jina Reader — fallback reader/fetch provider, default-OFF.
    # Ordered last so platform + insane.fetch are tried first.
    if _is_true(resolved_env.get(JINA_READER_ENABLED_ENV)):
        from magi_agent.web_acquisition.providers.jina_reader import (
            JinaReaderProvider,
        )

        jina_api_key = resolved_env.get(MAGI_JINA_API_KEY_ENV) or None
        jina_reader_provider = JinaReaderProvider(api_key=jina_api_key)
        providers[JINA_READER_PROVIDER_NAME] = jina_reader_provider
        provider_names.append(JINA_READER_PROVIDER_NAME)

    # Auto-derive configs when not supplied.
    if pack_config is None:
        pack_config = LiveWebAcquisitionPackConfig(
            enabled=bool(provider_names),
            liveNetworkEnabled=bool(provider_names),
            providerAllowlist=tuple(set(provider_names)),
        )

    if router_config is None:
        router_config = ProviderRouterConfig(
            enabled=router_enabled and bool(provider_names),
            providers=tuple(provider_names),
        )

    live_pack = LiveWebAcquisitionProviderPack(pack_config)
    router: WebAcquisitionProviderRouter | None = build_provider_router(
        router_config, live_pack, providers
    )

    return LocalWebResearchToolBoundary(
        live_pack=live_pack,
        provider_router=router,
        env=env,
    )


__all__ = [
    "INSANE_FETCH_ENABLED_ENV",
    "INSANE_FETCH_PROVIDER_NAME",
    "JINA_READER_ENABLED_ENV",
    "JINA_READER_PROVIDER_NAME",
    "LIVE_WEB_ACQUISITION_ENABLED_ENV",
    "LIVE_WEB_ACQUISITION_KILL_SWITCH_ENV",
    "MAGI_JINA_API_KEY_ENV",
    "MAGI_PLATFORM_API_KEY_ENV",
    "MAGI_PLATFORM_BASE_URL_ENV",
    "PLATFORM_FETCH_PROVIDER_NAME",
    "PLATFORM_SEARCH_PROVIDER_NAME",
    "PROVIDER_ROUTER_ENABLED_ENV",
    "LocalWebResearchToolBoundary",
    "ResearchToolName",
    "build_live_research_boundary",
    "live_web_acquisition_active",
    "project_live_web_acquisition_result_to_source_ledger",
    "project_web_acquisition_result_to_source_ledger",
]
