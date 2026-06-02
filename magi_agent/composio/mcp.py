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
