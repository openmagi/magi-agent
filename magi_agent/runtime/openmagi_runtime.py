from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from magi_agent.adk_bridge.primitives import AdkPrimitiveBoundary
from magi_agent.config.models import RuntimeConfig
from magi_agent.harness.profiles import RuntimeProfile, build_default_profile

_SYNTHETIC_NATIVE_TOOL_HANDLERS: dict[tuple[str, str], str] = {
    (
        "openmagi.documents",
        "FileDeliver",
    ): "magi_agent.plugins.native.documents:file_deliver",
    (
        "openmagi.documents",
        "FileSend",
    ): "magi_agent.plugins.native.documents:file_send",
}

if TYPE_CHECKING:
    from magi_agent.plugins.manager import ResolvedPluginState
    from magi_agent.tools.base import ToolHandler
    from magi_agent.tools.registry import ToolRegistry


def _build_core_tool_registry(plugin_state: ResolvedPluginState | None = None) -> ToolRegistry:
    from magi_agent.tools.catalog import register_core_tool_manifests
    from magi_agent.tools.core_toolhost import bind_core_toolhost_handlers
    from magi_agent.tools.registry import ToolRegistry

    tool_registry = ToolRegistry()
    register_core_tool_manifests(tool_registry)
    bind_core_toolhost_handlers(tool_registry)
    if plugin_state is not None:
        _register_native_plugin_tool_manifests(tool_registry, plugin_state)
        _bind_native_plugin_tool_handlers(tool_registry, plugin_state)
    return tool_registry


def _build_default_plugin_state() -> ResolvedPluginState:
    from magi_agent.plugins.manager import resolve_plugin_state
    from magi_agent.plugins.native_catalog import native_plugin_manifests

    return resolve_plugin_state(native_plugin_manifests())


def _register_native_plugin_tool_manifests(
    tool_registry: ToolRegistry,
    plugin_state: ResolvedPluginState,
) -> None:
    from magi_agent.plugins.tool_projection import project_native_plugin_tool_manifests

    for manifest in project_native_plugin_tool_manifests(plugin_state):
        # Core/builtin names retain ownership when a native plugin declares the
        # same historical surface, e.g. CronList or TaskGet.
        if tool_registry.resolve(manifest.name) is not None:
            continue
        tool_registry.register(manifest)


def _bind_native_plugin_tool_handlers(
    tool_registry: ToolRegistry,
    plugin_state: ResolvedPluginState,
) -> None:
    for plugin in plugin_state.plugins:
        if not plugin.enabled:
            continue
        for tool_ref in plugin.tools:
            registration = tool_registry.resolve_registration(tool_ref.name)
            if registration is None or registration.handler is not None:
                continue
            tool_registry.bind_handler(
                tool_ref.name,
                _load_tool_handler(tool_ref.entrypoint),
                enabled_by_registry_policy=True,
            )
        for (plugin_id, tool_name), entrypoint in _SYNTHETIC_NATIVE_TOOL_HANDLERS.items():
            if plugin.plugin_id != plugin_id:
                continue
            registration = tool_registry.resolve_registration(tool_name)
            if registration is None or registration.handler is not None:
                continue
            tool_registry.bind_handler(
                tool_name,
                _load_tool_handler(entrypoint),
                enabled_by_registry_policy=True,
            )


def _load_tool_handler(entrypoint: str) -> ToolHandler:
    module_name, _, attr_name = entrypoint.partition(":")
    if not module_name or not attr_name:
        raise ValueError(f"invalid native tool entrypoint: {entrypoint}")
    module = import_module(module_name)
    value = getattr(module, attr_name)
    if not callable(value):
        raise TypeError(f"native tool entrypoint is not callable: {entrypoint}")
    return cast("ToolHandler", value)


class OpenMagiRuntime:
    """Product-owned runtime shell around future ADK primitive adapters."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        profile: RuntimeProfile | None = None,
        adk_boundary: AdkPrimitiveBoundary | None = None,
        tool_registry: ToolRegistry | None = None,
        plugin_state: ResolvedPluginState | None = None,
    ) -> None:
        self.config = config
        self.profile = profile or build_default_profile()
        self.adk_boundary = adk_boundary or AdkPrimitiveBoundary.declared()
        self.adk_invocation_enabled = False
        self.plugin_state = (
            plugin_state if plugin_state is not None else _build_default_plugin_state()
        )
        if tool_registry is None:
            tool_registry = _build_core_tool_registry(self.plugin_state)
        self.tool_registry = tool_registry

    def list_active_tools(self) -> list[str]:
        return [tool.name for tool in self.tool_registry.list_available(mode="act")]

    def status(self) -> dict[str, object]:
        return {
            "ok": self.adk_boundary.available,
            "botId": self.config.bot_id,
            "runtime": self.config.runtime,
            "runtimeEngine": self.config.runtime_engine,
            "version": self.config.build.version,
            "buildSha": self.config.build.build_sha,
            "profile": self.profile.name,
            "adk": {
                "available": self.adk_boundary.available,
                "invoked": self.adk_boundary.invoked,
            },
            "contextContinuity": self.config.context_continuity.health_metadata,
            "activeTools": self.list_active_tools(),
        }
