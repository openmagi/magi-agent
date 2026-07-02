"""Gate5b dispatch policies (no privilege; BeforeToolCtx/AfterToolCtx only).

``memory_mode_policy`` is the MOVED body of Gate5BFullToolHost._enforce_memory_mode
(deny path, hasattr(ctx, "decide")) + _filter_memory_mode_output (filter path,
hasattr(ctx, "override")). It reads the channel memory mode off the typed
session view — never the host. ``permission_preflight_policy`` is the moved
_preflight_legacy_tool. Library predicates are imported from the same module
gate5b imports them from (magi_agent.tools.memory_mode_guard — verified against
the gate5b top-of-file import block).
"""
from __future__ import annotations

from typing import Any

from magi_agent.tools.memory_mode_guard import (
    MEMORY_READ_TOOL_NAMES as _MEMORY_READ_TOOL_NAMES,
    MEMORY_WRITE_TOOL_NAMES as _MEMORY_WRITE_TOOL_NAMES,
    command_may_write_protected_memory,
    command_mentions_protected_memory,
    filter_protected_memory_matches as _filter_protected_memory_matches,
    grep_glob_may_include_protected_memory as _grep_glob_may_include_protected_memory,
    is_incognito_memory_mode,
    is_long_term_memory_read_disabled,
    is_long_term_memory_write_disabled,
    is_protected_memory_path,
    memory_read_target_paths as _memory_read_target_paths,
    memory_write_target_paths,
    normalize_memory_mode,
)


def _deny(ctx: Any) -> None:
    ctx.decide("deny", reason="memory_mode_blocked")


def memory_mode_policy(ctx) -> None:  # noqa: ANN001 — duck-typed ctx-callable:
    # receives BeforeToolCtx (decide path) and AfterToolCtx (override path); the
    # ContextDispatcher contract calls every impl at every hook and impls no-op
    # on contexts they do not handle. Param intentionally unannotated (the §1
    # typed-context guard maps control_plane impls to a single provide-context).
    # Library reuse: the memory-mode read-half helpers are now imported at
    # module top from their single home (magi_agent.tools.memory_mode_guard),
    # the same module gate5b imports them from; no gate5b private import needed.
    mode = normalize_memory_mode(str(ctx.session.get_state("memoryMode", "normal")))
    if hasattr(ctx, "decide"):  # before_tool: block path (moved _enforce_memory_mode)
        tool_name = ctx.tool_name
        args = dict(ctx.tool_args)
        if tool_name in _MEMORY_WRITE_TOOL_NAMES:
            if not is_long_term_memory_write_disabled(mode):
                return
            for path in memory_write_target_paths(tool_name, args):
                if is_protected_memory_path(path):
                    _deny(ctx)
                    return
            return
        if tool_name == "Bash":
            command = args.get("command")
            command_text = command if isinstance(command, str) else ""
            if (
                is_incognito_memory_mode(mode)
                and command_mentions_protected_memory(command_text)
            ) or (
                is_long_term_memory_write_disabled(mode)
                and command_may_write_protected_memory(command_text)
            ):
                _deny(ctx)
            return
        if tool_name in _MEMORY_READ_TOOL_NAMES:
            if not is_long_term_memory_read_disabled(mode):
                return
            for path in _memory_read_target_paths(tool_name, args):
                if is_protected_memory_path(path):
                    _deny(ctx)
                    return
            if tool_name == "Grep" and _grep_glob_may_include_protected_memory(args):
                _deny(ctx)
        return
    if hasattr(ctx, "override"):  # after_tool: filter path (moved _filter_memory_mode_output)
        if ctx.tool_name not in {"Glob", "Grep"}:
            return
        if not is_long_term_memory_read_disabled(mode):
            return
        filtered = _filter_protected_memory_matches(ctx.result)
        if filtered is not ctx.result:
            ctx.override(filtered)


def permission_preflight_policy(ctx) -> None:  # noqa: ANN001 — duck-typed ctx-callable
    """Moved _preflight_legacy_tool: ToolPermissionPolicy over the legacy
    manifest (D6 — the permission gate itself is a removable pack)."""
    if not hasattr(ctx, "decide"):
        return
    from magi_agent.gates.gate5b_full_toolhost import (
        _GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES,
        _legacy_tool_manifest,
        _permission_reason_code,
    )
    from magi_agent.tools.context import ToolContext
    from magi_agent.tools.permission import ToolPermissionPolicy

    tool_name = ctx.tool_name
    if tool_name not in _GATE5B_LEGACY_FULL_TOOLHOST_TOOL_NAMES:
        return
    args = dict(ctx.tool_args)
    preflight_tool_name = tool_name
    if (
        tool_name == "PatchApply"
        and "content" in args and "patch" not in args and "diff" not in args
    ):
        preflight_tool_name = "FileWrite"
    manifest = _legacy_tool_manifest(preflight_tool_name)
    mode = "act" if "act" in manifest.available_in_modes else "plan"
    decision = ToolPermissionPolicy().decide(
        manifest,
        args,
        ToolContext(
            botId="gate5b-selected-full-toolhost",
            turnId=f"gate5b-full-toolhost:{ctx.session.invocation_id}",
            workspaceRoot=str(ctx.session.get_state("workspaceRoot", "")),
            memoryMode=str(ctx.session.get_state("memoryMode", "normal")),
            permissionScope={
                "mode": "selected_full_toolhost",
                "source": "selected_full_toolhost",
            },
        ),
        mode=mode,
    )
    if decision.action == "allow":
        return
    ctx.decide("deny", reason=_permission_reason_code(decision.metadata))
