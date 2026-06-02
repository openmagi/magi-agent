from __future__ import annotations

from typing import TYPE_CHECKING

from magi_agent.adk_bridge.primitives import AdkPrimitiveBoundary
from magi_agent.config.models import RuntimeConfig
from magi_agent.harness.profiles import RuntimeProfile, build_default_profile

if TYPE_CHECKING:
    from magi_agent.plugins.manager import ResolvedPluginState
    from magi_agent.tools.registry import ToolRegistry


def _build_core_tool_registry() -> ToolRegistry:
    from magi_agent.tools.catalog import register_core_tool_manifests
    from magi_agent.tools.registry import ToolRegistry

    tool_registry = ToolRegistry()
    register_core_tool_manifests(tool_registry)
    return tool_registry


def _build_default_plugin_state() -> ResolvedPluginState:
    from magi_agent.plugins.manager import resolve_plugin_state
    from magi_agent.plugins.native_catalog import native_plugin_manifests

    return resolve_plugin_state(native_plugin_manifests())


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
        if tool_registry is None:
            tool_registry = _build_core_tool_registry()
        self.tool_registry = tool_registry
        self.plugin_state = (
            plugin_state if plugin_state is not None else _build_default_plugin_state()
        )

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
