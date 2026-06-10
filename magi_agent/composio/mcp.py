from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.composio.config import ComposioConfig
from magi_agent.composio.redaction import redact_composio_text

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
    last_error_class: str | None = Field(default=None, alias="lastErrorClass")
    last_error_preview: str | None = Field(default=None, alias="lastErrorPreview")


def build_composio_toolset_bundle(
    config: ComposioConfig,
    composio_client_factory: Callable[[str], _ComposioClient] | None = None,
    toolset_cls: Callable[..., Any] | None = None,
    connection_params_cls: Callable[..., Any] | None = None,
) -> ComposioToolsetBundle:
    if not config.active:
        return ComposioToolsetBundle(
            active=False,
            status="inactive",
            reason=config.disabled_reason or "not_configured",
        )

    api_key = config.api_key
    if not api_key:
        return ComposioToolsetBundle(
            active=False,
            status="inactive",
            reason="missing_api_key",
        )

    try:
        if config.mcp_url_override:
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
        return ComposioToolsetBundle(
            active=True,
            status="ready",
            toolsets=(toolset,),
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
            lastErrorPreview=_sanitize_error_preview(exc, api_key),
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
    ) -> None:
        self._inner = inner
        self._arbiter = arbiter
        self._mode = mode
        self._context_factory = context_factory
        self.name = getattr(inner, "name", "composio-tool")

    def __getattr__(self, item: str) -> Any:
        # Proxy everything we don't explicitly override to the wrapped tool so
        # ADK introspection (declaration, is_long_running, ...) keeps working.
        return getattr(self._inner, item)

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
        if decision.action == "deny":
            return {
                "status": "blocked",
                "error": "permission_denied",
                "tool": self.name,
                "reason": decision.reason,
                "metadata": dict(decision.metadata),
            }
        return await self._inner.run_async(args=args, tool_context=tool_context)


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
    ) -> None:
        self._inner = inner
        self._arbiter = arbiter
        self._mode = mode
        self._context_factory = context_factory

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
) -> bool:
    """Attach composio toolsets to *runner* with dispatcher hard-safety guards.

    Behaves like :func:`attach_composio_toolsets_to_runner` (idempotent agent
    tools append) but each attached toolset is wrapped in
    :class:`_DispatcherGuardedToolset` so its tool calls pass through the
    :class:`RuntimePermissionArbiter` (secret / sealed / workspace-escape
    invariants) before the MCP body executes. Inactive / empty bundles are a
    no-op (returns ``False``), identical to the legacy attach.
    """
    if runner is None or not bundle.active or not bundle.toolsets:
        return False

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
            )
            for toolset in bundle.toolsets
        ),
        mcpServerLabel=bundle.mcp_server_label,
    )
    return attach_composio_toolsets_to_runner(runner, guarded)


def _default_composio_client(api_key: str) -> _ComposioClient:
    from composio import Composio

    return Composio(api_key=api_key)


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


def _sanitize_error_preview(exc: Exception, api_key: str) -> str:
    preview = redact_composio_text(str(exc))
    preview = preview.replace(api_key, "[redacted-composio-secret]")
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
