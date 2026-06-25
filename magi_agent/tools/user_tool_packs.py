"""Merge user-authored TOOL packs into the CLI tool runtime registry.

This is the activation half of PR1. User tool packs (scaffolded via
``magi pack new tool ...`` into ``~/.magi/packs`` or ``<cwd>/.magi/packs``) are
discovered, loaded and projected through the EXISTING pack pipeline
(:func:`magi_agent.packs.registries.load_into_registries`). That projection lands
each user ``ToolManifest`` in a :class:`~magi_agent.tools.registry.ToolRegistry`
(handler-less, via ``_provide_tool``) and any workspace handler the pack bound in
``registries.workspace_tool_handlers`` keyed by tool name.

The CLI dispatch boundary (:class:`~magi_agent.tools.dispatcher.ToolDispatcher`)
needs each registration to carry an executable
``ToolHandler = (ToolArguments, ToolContext) -> ToolResult``. A user tool pack
has two authoring seams to ship an executable handler:

- ``register_handler(manifest, handler)`` (PR6): a PLAIN inline handler
  ``(args: Mapping[str, object], tool_ctx: ToolCtx) -> output`` that needs no
  WorkspaceHostView (a vanilla third-party tool: call an API, compute something).
  This is the seam ``magi pack new tool`` scaffolds.
- ``register_workspace_handler(tool_name, handler)`` (the C1 gate5b seam), whose
  handler is ``(args, WorkspaceHostView) -> output`` and DOES want the kernel
  workspace-file services (path safety, read ledger, bounded shell).

So this module:

1. discovers + loads user tool packs through the real pipeline;
2. for every user tool manifest, PREFERS an inline handler if present — wraps it
   into a CLI ``ToolHandler`` over a narrow :class:`~magi_agent.packs.context.ToolCtx`
   — otherwise falls back to wrapping a workspace handler over a lazily-built
   :class:`~magi_agent.gates.gate5b_full_toolhost.Gate5BFullToolHost` (mirroring
   ``CoreToolhostHandlerSet._host_for``), then registers + binds it onto the CLI
   registry;
3. SKIPS any user tool whose name collides with an already-registered tool (the
   core/first-party tools are merged first), so a user pack can never override an
   ungated core tool.

A user tool manifest with NEITHER an inline nor a workspace handler is
manifest-only and cannot be dispatched on the CLI path, so it is skipped (it
would otherwise dispatch to ``tool_handler_missing``).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .context import ToolContext
from .manifest import ToolManifest
from .registry import ToolRegistry
from .result import ToolResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.packs.context import ToolCtx, WorkspaceHostView

_LOGGER = logging.getLogger(__name__)

WorkspaceHandler = Callable[
    [Mapping[str, object], "WorkspaceHostView"], object
]
InlineHandler = Callable[
    [Mapping[str, object], "ToolCtx"], object
]


class _UserToolWorkspaceHostSet:
    """Lazily-built per-(workspace, turn) gate5b hosts backing user tool handlers.

    Mirrors :class:`magi_agent.tools.core_toolhost.CoreToolhostHandlerSet`'s host
    keying so a user tool handler that uses the :class:`WorkspaceHostView` kernel
    services (path safety, read ledger, bounded shell) gets the same host shape a
    first-party workspace handler would. The set is created once per CLI runtime
    build, so the host map survives across calls within a session.
    """

    def __init__(self, *, exposed_tool_names: tuple[str, ...]) -> None:
        self._exposed_tool_names = exposed_tool_names
        self._hosts: dict[tuple[str, str], Any] = {}

    def _host_for(self, context: ToolContext) -> Any:
        from magi_agent.gates.gate5b_full_toolhost import (  # noqa: PLC0415
            Gate5BFullToolHost,
            Gate5BFullToolHostConfig,
        )

        workspace_root = Path(context.workspace_root or ".").resolve()
        scope = context.turn_id or context.session_id or "local-turn"
        key = (str(workspace_root), str(scope))
        host = self._hosts.get(key)
        if host is None:
            host = Gate5BFullToolHost(
                config=Gate5BFullToolHostConfig(),
                workspace_root=workspace_root,
                exposed_tool_names=self._exposed_tool_names,
                now_ms=lambda: 0,
            )
            self._hosts[key] = host
        return host

    def handler_for(
        self,
        tool_name: str,
        workspace_handler: WorkspaceHandler,
    ) -> Callable[[dict[str, object], ToolContext], ToolResult]:
        """Adapt a ``(args, WorkspaceHostView) -> output`` handler to a ToolHandler."""

        async def handler(
            arguments: dict[str, object], context: ToolContext
        ) -> ToolResult:
            from magi_agent.packs.context import WorkspaceHostView  # noqa: PLC0415

            try:
                host = self._host_for(context)
                output = workspace_handler(arguments, WorkspaceHostView(host=host))
                from inspect import isawaitable  # noqa: PLC0415

                if isawaitable(output):
                    output = await output
            except Exception as exc:  # noqa: BLE001 - handlers must NEVER raise
                return ToolResult(
                    status="error",
                    error_code="user_tool_pack_error",
                    error_message=str(exc),
                    metadata={"toolName": tool_name},
                )
            return ToolResult(
                status="ok",
                output=output if isinstance(output, Mapping) else {"value": output},
                metadata={"toolName": tool_name},
            )

        return handler


def _inline_handler_for(
    tool_name: str,
    inline_handler: InlineHandler,
) -> Callable[[dict[str, object], ToolContext], ToolResult]:
    """Adapt a plain ``(args, ToolCtx) -> output`` handler to a CLI ToolHandler.

    Builds a narrow :class:`~magi_agent.packs.context.ToolCtx` over the dispatch
    :class:`ToolContext` (no WorkspaceHostView): ``tool_name``, read-only
    ``tool_args``, a session read-view, and an optional progress sink wired to the
    context's ``emit_progress``. Awaits a coroutine result, wraps the return into a
    ``ToolResult`` (Mapping -> output as-is; scalar -> ``{"value": ...}``), and
    NEVER raises (an impl exception collapses to a ``ToolResult`` error)."""

    async def handler(
        arguments: dict[str, object], context: ToolContext
    ) -> ToolResult:
        from magi_agent.packs.context import ToolCtx, SessionReadView  # noqa: PLC0415

        try:
            emit = context.emit_progress
            tool_ctx = ToolCtx(
                tool_name=tool_name,
                tool_args=arguments,
                session=SessionReadView(
                    invocation_id=context.turn_id or context.session_id or "local-turn",
                    agent_name=context.bot_id or "local",
                    turn_index=0,
                ),
                emit_progress=(lambda msg: emit(msg)) if emit is not None else None,
            )
            output = inline_handler(arguments, tool_ctx)
            from inspect import isawaitable  # noqa: PLC0415

            if isawaitable(output):
                output = await output
        except Exception as exc:  # noqa: BLE001 - handlers must NEVER raise
            return ToolResult(
                status="error",
                error_code="user_tool_pack_error",
                error_message=str(exc),
                metadata={"toolName": tool_name},
            )
        return ToolResult(
            status="ok",
            output=output if isinstance(output, Mapping) else {"value": output},
            metadata={"toolName": tool_name},
        )

    return handler


def merge_user_tool_packs(
    registry: ToolRegistry,
    *,
    bases: "list[Path] | None" = None,
) -> tuple[str, ...]:
    """Discover + merge dispatchable user tool packs into ``registry``.

    Returns the tuple of tool names that were merged. The caller is expected to
    have already registered the first-party + optional first-party tools, so any
    user tool whose name collides with an existing registration is skipped (a
    user pack never overrides a core/first-party tool).

    Additive + fail-open: discovery/load errors collapse to merging nothing so
    the runtime stays usable even if a user pack is malformed.
    """
    from magi_agent.packs.discovery import default_search_bases  # noqa: PLC0415
    from magi_agent.packs.registries import load_into_registries  # noqa: PLC0415

    search_bases = bases if bases is not None else default_search_bases()
    try:
        pack_registries, _report = load_into_registries(list(search_bases))
    except Exception:  # noqa: BLE001 - a malformed pack must not break the runtime
        _LOGGER.warning("user tool pack discovery failed; merging none", exc_info=True)
        return ()

    manifests = pack_registries.tools.list_all()
    if not manifests:
        return ()

    workspace_handler_names = set(
        pack_registries.workspace_tool_handlers.list_refs()
    )
    # The exposed tool names the gate5b host advertises = the mergeable user tools
    # that fall back to a workspace handler (no inline handler). Snapshot before
    # binding so each host knows the full user-tool surface.
    inline_handler_names = set(
        pack_registries.tool_inline_handlers.list_refs()
    )
    exposed = tuple(
        sorted(
            manifest.name
            for manifest in manifests
            if manifest.name in workspace_handler_names
            and manifest.name not in inline_handler_names
            and registry.resolve_registration(manifest.name) is None
        )
    )

    host_set = _UserToolWorkspaceHostSet(exposed_tool_names=exposed)
    merged: list[str] = []
    for manifest in manifests:
        name = manifest.name
        if registry.resolve_registration(name) is not None:
            # Collides with an already-registered (core / first-party) tool.
            _LOGGER.info(
                "user tool pack tool %r collides with an existing tool; skipping",
                name,
            )
            continue
        # Prefer a plain inline handler (no WorkspaceHostView); fall back to a
        # workspace handler when only that seam was authored.
        inline_handler = pack_registries.tool_inline_handlers.resolve(name)
        if inline_handler is not None:
            registry.register(manifest)
            registry.bind_handler(
                name,
                _inline_handler_for(name, inline_handler),
                enabled_by_registry_policy=True,
            )
            merged.append(name)
            continue
        workspace_handler = pack_registries.workspace_tool_handlers.resolve(name)
        if workspace_handler is None:
            # Manifest-only: cannot be dispatched on the CLI path.
            _LOGGER.info(
                "user tool pack tool %r registered no handler; skipping",
                name,
            )
            continue
        _register_user_tool(registry, manifest, host_set, workspace_handler)
        merged.append(name)
    return tuple(merged)


def _register_user_tool(
    registry: ToolRegistry,
    manifest: ToolManifest,
    host_set: _UserToolWorkspaceHostSet,
    workspace_handler: WorkspaceHandler,
) -> None:
    registry.register(manifest)
    registry.bind_handler(
        manifest.name,
        host_set.handler_for(manifest.name, workspace_handler),
        enabled_by_registry_policy=True,
    )


__all__ = ["merge_user_tool_packs"]
