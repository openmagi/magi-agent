from __future__ import annotations

from importlib import import_module
from pathlib import Path
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
    from magi_agent.tools.todo_toolhost import bind_todo_write_handler

    from magi_agent.introspection.tool import bind_inspect_self_evidence_handler

    tool_registry = ToolRegistry()
    register_core_tool_manifests(tool_registry)
    bind_core_toolhost_handlers(tool_registry)
    # Optional persistent-namespace Python tool from the neutral
    # ``tools_persistent_python`` pack (MAGI_PERSISTENT_PYTHON_ENABLED=true).
    # Additive + removable: default-OFF so the registry is byte-identical when
    # the gate is unset; the manifest is sourced from the pack provider (no
    # hardcode) and the additive first-party binder attaches its handler.
    _maybe_bind_persistent_python(tool_registry)
    # Self-introspection (pull) tool — bound always, advertised only when the
    # MAGI_SELF_INTROSPECTION_ENABLED env gate is truthy (default OFF).
    bind_inspect_self_evidence_handler(tool_registry)
    # TodoWrite is not part of the core toolhost's direct tool set, so bind its
    # per-session handler explicitly. The handler set lives for the life of this
    # registry (one per CLI session), keeping each session's todo list in memory.
    bind_todo_write_handler(tool_registry)
    if plugin_state is not None:
        _register_native_plugin_tool_manifests(tool_registry, plugin_state)
        _bind_native_plugin_tool_handlers(tool_registry, plugin_state)
    return tool_registry


def _maybe_bind_persistent_python(tool_registry: ToolRegistry) -> None:
    """Register + bind the neutral ``PersistentPython`` pack tool when gated ON.

    Default-OFF (``MAGI_PERSISTENT_PYTHON_ENABLED``): when unset this is a no-op
    and the registry is byte-identical to before. Additive and removable; the
    manifest is sourced from the pack provider (no hardcode).
    """
    from magi_agent.config.env import persistent_python_enabled

    if not persistent_python_enabled():
        return
    from magi_agent.tools.persistent_python_toolhost import (
        bind_persistent_python_handler,
        register_persistent_python_manifest,
    )

    register_persistent_python_manifest(tool_registry)
    bind_persistent_python_handler(tool_registry)


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


def _bind_memory_write_host(
    tool_registry: "ToolRegistry",
    config: RuntimeConfig,
) -> None:
    """Bind the gate-aware MemoryWrite handler to the runtime registry.

    This is called exactly once during ``OpenMagiRuntime.__init__`` after the
    core tool registry is built.  The handler is always bound (so
    ``resolve_registration("MemoryWrite").handler`` is never None) but the
    host config's ``enabled`` flag controls whether calls flow through or are
    blocked — matching the gate state at startup.

    Workspace root resolution order:
      1. ``config.memory.workspace_root`` (set by the CLI/dashboard from cwd)
      2. ``Path.cwd()`` fallback for direct construction in tests
    """
    from magi_agent.runtime.memory_write_wiring import build_memory_write_host

    workspace_root_str = config.memory.workspace_root
    workspace_root = Path(workspace_root_str) if workspace_root_str else Path.cwd()

    host = build_memory_write_host(
        workspace_root=workspace_root,
        bot_id=config.bot_id,
        user_id=config.user_id,
    )
    host.bind(tool_registry)


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
        self.plugin_state = (
            plugin_state if plugin_state is not None else _build_default_plugin_state()
        )
        if tool_registry is None:
            tool_registry = _build_core_tool_registry(self.plugin_state)
        self.tool_registry = tool_registry
        # Apply persisted tool enable/disable overrides (~/.magi/customize.json;
        # path honors MAGI_CUSTOMIZE/MAGI_CONFIG). Never let this crash construction.
        try:
            from magi_agent.customize.apply import (
                apply_tool_overrides,
                apply_verification_overrides,
            )
            from magi_agent.customize.store import load_overrides

            _overrides = load_overrides()
            apply_tool_overrides(self, _overrides)
            apply_verification_overrides(self, _overrides)
        except Exception:
            pass
        # TODO(memory): wire hosted ADK prompt assembly to
        # project_memory_snapshot — see docs/plans. The live local-dashboard
        # chat turn (transport.chat._local_adk_chat_sse) already gets the frozen
        # snapshot for free because it builds its runner via
        # cli.wiring.build_headless_runtime -> cli.real_runner.build_cli_model_runner
        # -> cli.tool_runtime.build_cli_instruction, which threads the
        # MemorySnapshotCache block into the Agent instruction. The production
        # multi-tenant path (transport.chat._run_live_chat_runner) instead routes
        # through the Gate 5B-4c-3 shadow generation boundary, whose policy
        # contract forbids memory injection, so it is intentionally NOT wired
        # here. A runtime-owned snapshot cache attribute would be dead until a
        # non-shadow hosted prompt-assembly site exists, so it is omitted.
        _bind_memory_write_host(self.tool_registry, config)

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
