from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.composio.config import ComposioConfig
from magi_agent.composio.redaction import redact_composio_text
from magi_agent.plugins.mcp_resilience import (
    CircuitBreakerRegistry,
    McpResiliencePolicy,
    McpServerUnreachable,
    REASON_CIRCUIT_OPEN,
    REASON_NEEDS_REAUTH,
    REASON_SERVER_UNREACHABLE,
    async_call_with_resilience,
)

_MISSING_PACKAGE_PREVIEW = "install the composio optional extra to enable integrations"
_ERROR_PREVIEW_LIMIT = 240


class _ComposioClient(Protocol):
    def create(self, **kwargs: object) -> object: ...


class ComposioToolsetBundle(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    active: bool = False
    status: str = "inactive"
    reason: str | None = None
    toolsets: tuple[Any, ...] = Field(default=(), exclude=True, repr=False)
    mcp_server_label: str = Field(default="composio", alias="mcpServerLabel")
    # Per-ENDPOINT breaker key: a stable ``sha256(mcp_url)`` digest (never the raw
    # URL, which carries the per-user secret). Load-bearing: ``mcp_server_label``
    # defaults to ``"composio"`` for every server, so keying the breaker on it
    # would collapse all composio endpoints to one shared breaker. Defaults to
    # ``None`` (unset) so OFF callers are unchanged.
    server_ref: str | None = Field(default=None, alias="serverRef")
    last_error_class: str | None = Field(default=None, alias="lastErrorClass")
    last_error_preview: str | None = Field(default=None, alias="lastErrorPreview")


def build_composio_toolset_bundle(
    config: ComposioConfig,
    composio_client_factory: Callable[[str], _ComposioClient] | None = None,
    toolset_cls: Callable[..., Any] | None = None,
    connection_params_cls: Callable[..., Any] | None = None,
    platform_session_fetcher: Callable[[ComposioConfig], dict[str, Any]] | None = None,
) -> ComposioToolsetBundle:
    if not config.active:
        return ComposioToolsetBundle(
            active=False,
            status="inactive",
            reason=config.disabled_reason or "not_configured",
        )

    is_platform = config.credential_source == "platform"
    api_key = config.api_key
    # The secret to scrub from any error preview: the broker token in platform
    # mode (no Composio key is present), else the Composio api key.
    redaction_secret = config.platform_token if is_platform else api_key
    if not is_platform and not api_key:
        return ComposioToolsetBundle(
            active=False,
            status="inactive",
            reason="missing_api_key",
        )

    try:
        if is_platform:
            # Approach A: ask the broker to mint a Composio session for our
            # entity, then connect the toolset DIRECTLY to Composio. The broker
            # holds the master key and steps out of the tool-call path after
            # minting; no local Composio key/client is used here.
            fetch = platform_session_fetcher or _default_platform_session_fetcher
            session = fetch(config)
            mcp_url = session["mcp_url"]
            headers = session.get("headers") or {}
            resolved_toolset_cls, resolved_params_cls = _resolve_adk_classes(
                toolset_cls,
                connection_params_cls,
            )
        elif config.mcp_url_override:
            mcp_url = config.mcp_url_override
            headers = {"Authorization": f"Bearer {api_key}"}
            resolved_toolset_cls, resolved_params_cls = _resolve_adk_classes(
                toolset_cls,
                connection_params_cls,
            )
        else:
            client_factory = composio_client_factory or _default_composio_client
            client = client_factory(api_key)
            create_kwargs: dict[str, object] = {"user_id": config.entity_id or "default"}
            if config.toolkits:
                create_kwargs["toolkits"] = list(config.toolkits)
            session = client.create(**create_kwargs)
            mcp = getattr(session, "mcp")
            mcp_url = getattr(mcp, "url")
            headers = getattr(mcp, "headers")
            resolved_toolset_cls, resolved_params_cls = _resolve_adk_classes(
                toolset_cls,
                connection_params_cls,
            )

        params = resolved_params_cls(url=mcp_url, headers=headers)
        toolset = resolved_toolset_cls(
            connection_params=params,
            tool_name_prefix="composio",
            require_confirmation=False,
        )
        # Per-ENDPOINT breaker key. The raw URL carries the per-user secret, so
        # we carry only a stable one-way digest (see §1.2 CORRECTION 2 of the
        # WS9 design): never mcp_server_label, which is the constant "composio".
        server_ref = _server_ref_for_mcp_url(mcp_url)
        return ComposioToolsetBundle(
            active=True,
            status="ready",
            toolsets=(toolset,),
            serverRef=server_ref,
        )
    except ImportError as exc:
        return ComposioToolsetBundle(
            active=False,
            status="missing_package",
            reason="missing_python_package",
            lastErrorClass=type(exc).__name__,
            lastErrorPreview=_MISSING_PACKAGE_PREVIEW,
        )
    except Exception as exc:
        return ComposioToolsetBundle(
            active=False,
            status="error",
            reason="toolset_build_failed",
            lastErrorClass=type(exc).__name__,
            lastErrorPreview=_sanitize_error_preview(exc, redaction_secret),
        )


def attach_composio_toolsets_to_runner(
    runner: object | None,
    bundle: ComposioToolsetBundle,
) -> bool:
    if runner is None or not bundle.active or not bundle.toolsets:
        return False

    agent = getattr(runner, "agent", None)
    if agent is None:
        return False

    existing_tools = getattr(agent, "tools", None)
    if existing_tools is None:
        agent.tools = list(bundle.toolsets)
        return True
    if isinstance(existing_tools, list):
        for toolset in bundle.toolsets:
            if not _toolset_already_attached(existing_tools, toolset):
                existing_tools.append(toolset)
        return True

    try:
        updated_tools = list(existing_tools)
    except TypeError:
        return False
    for toolset in bundle.toolsets:
        if not _toolset_already_attached(updated_tools, toolset):
            updated_tools.append(toolset)
    agent.tools = updated_tools
    return True


@dataclass(frozen=True)
class ComposioCallReceipt:
    """Append-only record of one dispatcher-guarded composio tool call.

    composio tools execute via the ADK MCP path and never reach
    ``ToolDispatcher.dispatch`` (where native tools have their receipts
    appended), so without this seam a hard-safety-guarded composio call leaves
    no audit trail. Each guarded call records the tool name, the
    :class:`RuntimePermissionArbiter` action / reason, and a redacted
    ``sha256`` digest of the (redacted) arguments — never the raw argument
    values, so secret / path payloads cannot leak through the receipt.
    """

    tool: str
    action: str
    reason: str
    payload_digest: str
    recorded_at_ns: int


class ComposioReceiptLedger:
    """In-memory append-only ledger for dispatcher-guarded composio calls.

    Mirrors the role of ``GeneralAutomationReceiptLedgerStore`` for the MCP
    path: it gives the guard somewhere to retain a per-call receipt without
    granting any execution / route / production-write authority. Single-process
    CLI / local-dashboard scoped, intentionally small and ephemeral.
    """

    def __init__(self) -> None:
        self._receipts: list[ComposioCallReceipt] = []

    def append(self, receipt: ComposioCallReceipt) -> ComposioCallReceipt:
        self._receipts.append(receipt)
        return receipt

    def receipts(self) -> tuple[ComposioCallReceipt, ...]:
        return tuple(self._receipts)


def _composio_payload_digest(tool_name: str, arguments: dict[str, object]) -> str:
    """sha256 over the redacted argument view — no raw secret/path leaks.

    Each value is first run through :func:`redact_composio_text` so any secret
    material the redactor recognises is stripped before it ever reaches the
    digest input. The digest is deterministic for the same (redacted) payload so
    receipts are comparable, but it is one-way — the raw argument cannot be
    recovered from it.
    """
    parts: list[str] = [str(tool_name)]
    for key in sorted(arguments):
        value = arguments[key]
        redacted = redact_composio_text(value) if isinstance(value, str) else repr(value)
        parts.append(f"{key}={redacted}")
    joined = "\x1f".join(parts).encode("utf-8", "replace")
    return f"sha256:{sha256(joined).hexdigest()}"


def guarded_toolset_receipt_ledger(toolset: object) -> ComposioReceiptLedger | None:
    """Return the receipt ledger backing a dispatcher-guarded *toolset*, if any."""
    ledger = getattr(toolset, "receipt_ledger", None)
    return ledger if isinstance(ledger, ComposioReceiptLedger) else None


def _server_ref_for_mcp_url(mcp_url: str) -> str:
    """Stable per-endpoint breaker key derived from the live ``mcp_url``.

    A one-way ``sha256`` digest (truncated), NEVER the raw URL: the URL carries
    the per-user secret. Two distinct endpoints yield distinct digests so their
    circuit breakers are isolated (see §1.2 CORRECTION 2 of the WS9 design).
    """
    return sha256(mcp_url.encode("utf-8")).hexdigest()[:16]


# Conservative auth-signal regex (case-insensitive). Anything that does NOT match
# (and has no 401/403 status) is treated as retryable transport, so a flaky
# network never shows a misleading "reconnect your account" message.
_AUTH_MESSAGE_RE = re.compile(
    r"oauth|unauthor|forbidden|invalid_grant|token",
    re.IGNORECASE,
)


class _ComposioAuthError(Exception):
    """Internal: a guarded composio call failed with an auth-class error.

    Raised inside the resilience wrapper so the (typed) non-retryable auth path
    of :func:`async_call_with_resilience` fires; the wrapper then maps it to a
    ``mcp_needs_reauth`` structured result for the model.
    """

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


def _classify_mcp_exception(exc: BaseException) -> Literal["auth", "transport"]:
    """Classify an ADK/composio exception as ``auth`` or ``transport``.

    ``auth`` only for a clear signal (HTTP 401/403, or an auth-message regex
    hit); everything ambiguous is conservatively ``transport`` (retryable).
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status in (401, 403):
        return "auth"
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) in (401, 403):
        return "auth"
    if _AUTH_MESSAGE_RE.search(str(exc)):
        return "auth"
    return "transport"


class _DispatcherGuardedTool:
    """Wraps an ADK MCP tool so each call first clears the hard-safety arbiter.

    composio tools carry no runtime manifest, so the arbiter's manifest-keyed
    file branches never fire for them on the raw ADK path. This proxy runs
    :meth:`RuntimePermissionArbiter.decide_external_mcp_call` over the call
    arguments BEFORE delegating to the wrapped tool's ``run_async`` — a deny
    short-circuits with a ``blocked`` result so secret / sealed /
    workspace-escape arguments never reach the MCP body. All other tool
    attributes are proxied through to the underlying ADK tool unchanged.
    """

    def __init__(
        self,
        inner: Any,
        *,
        arbiter: Any,
        mode: str,
        context_factory: Callable[..., Any],
        receipt_ledger: ComposioReceiptLedger,
        resilience: McpResiliencePolicy | None = None,
        server_ref: str | None = None,
        registry: CircuitBreakerRegistry | None = None,
    ) -> None:
        self._inner = inner
        self._arbiter = arbiter
        self._mode = mode
        self._context_factory = context_factory
        self._receipt_ledger = receipt_ledger
        # Resilience (timeout / bounded reconnect / per-endpoint breaker) is
        # default-OFF: when ``resilience`` is None or disabled the allow path is
        # the exact byte-identical inner await.
        self._resilience = resilience
        self._server_ref = server_ref
        self._registry = registry
        self.name = getattr(inner, "name", "composio-tool")

    def __getattr__(self, item: str) -> Any:
        # Proxy everything we don't explicitly override to the wrapped tool so
        # ADK introspection (declaration, is_long_running, ...) keeps working.
        return getattr(self._inner, item)

    def _record_receipt(self, args: dict[str, object], decision: Any) -> None:
        self._receipt_ledger.append(
            ComposioCallReceipt(
                tool=self.name,
                action=str(decision.action),
                reason=str(decision.reason),
                payload_digest=_composio_payload_digest(self.name, args),
                recorded_at_ns=time.monotonic_ns(),
            )
        )

    async def run_async(self, *, args: dict[str, object], tool_context: object = None):
        context = self._context_factory(
            tool_name=self.name,
            arguments=args,
            adk_tool_context=tool_context,
        )
        decision = self._arbiter.decide_external_mcp_call(
            self.name,
            dict(args),
            context,
            mode=self._mode,
        )
        # receipt 적재 — every guarded call is recorded (deny AND allow), the
        # MCP-path analogue of ToolDispatcher.dispatch appending receipts for
        # native tools. Append BEFORE delegating so a deny short-circuit still
        # leaves an audit trail.
        self._record_receipt(dict(args), decision)
        if decision.action == "deny":
            return {
                "status": "blocked",
                "error": "permission_denied",
                "tool": self.name,
                "reason": decision.reason,
                "metadata": dict(decision.metadata),
            }
        resilience = self._resilience
        if resilience is None or not resilience.enabled:
            # Byte-identical OFF path: the exact same inner await as before.
            return await self._inner.run_async(args=args, tool_context=tool_context)
        return await self._run_with_resilience(resilience, args, tool_context)

    async def _run_with_resilience(
        self,
        resilience: McpResiliencePolicy,
        args: dict[str, object],
        tool_context: object,
    ) -> Any:
        """Allow path wrapped in timeout / bounded reconnect / breaker (ON path).

        On ``McpServerUnreachable`` (attempts exhausted or breaker-open) or an
        auth-class failure we RETURN a structured error dict (same shape as the
        deny short-circuit) so the model sees an actionable result instead of a
        crashed turn. The receipt was already appended before this call, so the
        audit trail is preserved on failure.
        """
        registry = self._registry or CircuitBreakerRegistry.default()
        server_ref = self._server_ref or self.name

        async def _guarded_inner() -> Any:
            try:
                return await self._inner.run_async(
                    args=args, tool_context=tool_context
                )
            except _ComposioAuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - classify auth vs transport
                if _classify_mcp_exception(exc) == "auth":
                    raise _ComposioAuthError(exc) from exc
                raise

        try:
            return await async_call_with_resilience(
                resilience,
                registry,
                server_ref,
                _guarded_inner,
                auth_error_types=(_ComposioAuthError,),
            )
        except _ComposioAuthError:
            return self._resilience_error("error", REASON_NEEDS_REAUTH)
        except McpServerUnreachable as exc:
            reason = exc.reason_code or REASON_SERVER_UNREACHABLE
            return self._resilience_error("error", reason)

    def _resilience_error(self, status: str, reason: str) -> dict[str, object]:
        return {
            "status": status,
            "error": reason,
            "reason": reason,
            "tool": self.name,
        }


class _DispatcherGuardedToolset:
    """Wraps an ADK MCP toolset so its tools are dispatcher-guarded.

    Proxies ``get_tools`` (the ADK toolset contract) and returns each tool
    wrapped in :class:`_DispatcherGuardedTool`. Every other attribute is proxied
    to the underlying toolset so the bundle / runner continue to see a normal
    toolset (prefix, close, ...).
    """

    def __init__(
        self,
        inner: Any,
        *,
        arbiter: Any,
        mode: str,
        context_factory: Callable[..., Any],
        receipt_ledger: ComposioReceiptLedger,
        resilience: McpResiliencePolicy | None = None,
        server_ref: str | None = None,
        registry: CircuitBreakerRegistry | None = None,
    ) -> None:
        self._inner = inner
        self._arbiter = arbiter
        self._mode = mode
        self._context_factory = context_factory
        # Public so the runner / callers can audit guarded composio calls via
        # ``guarded_toolset_receipt_ledger``.
        self.receipt_ledger = receipt_ledger
        self._resilience = resilience
        self._server_ref = server_ref
        self._registry = registry

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    async def get_tools(self, readonly_context: object = None) -> list[Any]:
        tools = await self._inner.get_tools(readonly_context)
        return [
            _DispatcherGuardedTool(
                tool,
                arbiter=self._arbiter,
                mode=self._mode,
                context_factory=self._context_factory,
                receipt_ledger=self.receipt_ledger,
                resilience=self._resilience,
                server_ref=self._server_ref,
                registry=self._registry,
            )
            for tool in tools
        ]


def attach_composio_toolsets_through_dispatcher(
    runner: object | None,
    bundle: ComposioToolsetBundle,
    *,
    arbiter: Any,
    mode: str,
    context_factory: Callable[..., Any],
    receipt_ledger: ComposioReceiptLedger | None = None,
    resilience: McpResiliencePolicy | None = None,
    registry: CircuitBreakerRegistry | None = None,
) -> bool:
    """Attach composio toolsets to *runner* with dispatcher hard-safety guards.

    Behaves like :func:`attach_composio_toolsets_to_runner` (idempotent agent
    tools append) but each attached toolset is wrapped in
    :class:`_DispatcherGuardedToolset` so its tool calls pass through the
    :class:`RuntimePermissionArbiter` (secret / sealed / workspace-escape
    invariants) before the MCP body executes AND each guarded call appends a
    :class:`ComposioCallReceipt` to *receipt_ledger* (the MCP-path analogue of
    ``ToolDispatcher.dispatch`` recording receipts for native tools). When
    *receipt_ledger* is ``None`` a fresh ledger is created and exposed on the
    guarded toolset (see :func:`guarded_toolset_receipt_ledger`). Inactive /
    empty bundles are a no-op (returns ``False``), identical to the legacy
    attach.
    """
    if runner is None or not bundle.active or not bundle.toolsets:
        return False

    ledger = receipt_ledger if receipt_ledger is not None else ComposioReceiptLedger()

    # Per-ENDPOINT breaker key off the bundle (a sha256(mcp_url) digest), NOT the
    # mcp_server_label constant. Only materialise a shared registry when
    # resilience is actually ON so the OFF path is byte-identical.
    resilience_registry: CircuitBreakerRegistry | None = None
    if resilience is not None and resilience.enabled:
        resilience_registry = registry or CircuitBreakerRegistry.default()

    guarded = ComposioToolsetBundle(
        active=bundle.active,
        status=bundle.status,
        reason=bundle.reason,
        toolsets=tuple(
            _DispatcherGuardedToolset(
                toolset,
                arbiter=arbiter,
                mode=mode,
                context_factory=context_factory,
                receipt_ledger=ledger,
                resilience=resilience,
                server_ref=bundle.server_ref,
                registry=resilience_registry,
            )
            for toolset in bundle.toolsets
        ),
        mcpServerLabel=bundle.mcp_server_label,
        serverRef=bundle.server_ref,
    )
    return attach_composio_toolsets_to_runner(runner, guarded)


def _default_composio_client(api_key: str) -> _ComposioClient:
    from composio import Composio

    return Composio(api_key=api_key)


def _default_platform_session_fetcher(config: ComposioConfig) -> dict[str, Any]:
    """Mint a Composio session via the platform broker (approach A).

    Returns Composio's own ``{"mcp_url", "headers"}`` so the toolset connects
    directly to Composio. Raises if the broker isn't configured (the caller
    surfaces it as a toolset_build_failed bundle with the token redacted).
    """
    from magi_agent.composio.broker import build_broker_client

    client = build_broker_client(config)
    if client is None:
        raise RuntimeError("platform broker not configured")
    return client.session(toolkits=config.toolkits)


def _resolve_adk_classes(
    toolset_cls: Callable[..., Any] | None,
    connection_params_cls: Callable[..., Any] | None,
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    if toolset_cls is not None and connection_params_cls is not None:
        return toolset_cls, connection_params_cls

    from google.adk.tools.mcp_tool.mcp_toolset import (
        McpToolset,
        StreamableHTTPConnectionParams,
    )

    return (
        toolset_cls or McpToolset,
        connection_params_cls or StreamableHTTPConnectionParams,
    )


def _sanitize_error_preview(exc: Exception, secret: str | None) -> str:
    preview = redact_composio_text(str(exc))
    if secret:
        preview = preview.replace(secret, "[redacted-composio-secret]")
    return preview[:_ERROR_PREVIEW_LIMIT]


def _toolset_already_attached(existing_toolsets: list[Any], candidate: Any) -> bool:
    candidate_prefix = _tool_name_prefix(candidate)
    candidate_name = getattr(candidate, "name", None)
    for existing in existing_toolsets:
        if existing is candidate:
            return True
        if candidate_prefix and _tool_name_prefix(existing) == candidate_prefix:
            return True
        if candidate_name and getattr(existing, "name", None) == candidate_name:
            return True
    return False


def _tool_name_prefix(toolset: Any) -> str | None:
    prefix = getattr(toolset, "tool_name_prefix", None)
    if isinstance(prefix, str) and prefix:
        return prefix

    private_prefix = getattr(toolset, "_tool_name_prefix", None)
    if isinstance(private_prefix, str) and private_prefix:
        return private_prefix

    kwargs = getattr(toolset, "kwargs", None)
    if isinstance(kwargs, dict):
        kwargs_prefix = kwargs.get("tool_name_prefix")
        if isinstance(kwargs_prefix, str) and kwargs_prefix:
            return kwargs_prefix
    return None
