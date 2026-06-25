"""PR6 — a user tool pack can ship a plain INLINE handler dispatchable directly.

PR1 only activated user tools that bound a gate5b WORKSPACE handler
(``register_workspace_handler(name, handler)`` whose handler is
``(args, WorkspaceHostView) -> output``). A plain third-party tool (call an API,
compute something) does NOT need the WorkspaceHostView (path safety / read ledger
/ bounded shell are workspace-file concerns), and the ``magi pack new tool``
scaffold template registered a MANIFEST ONLY, so a vanilla scaffolded tool was
not dispatchable.

PR6 adds an INLINE tool-handler ABI: ``ctx.register_handler(manifest, handler)``
where ``handler`` is ``(args: Mapping[str, object], tool_ctx: ToolCtx) -> output``
(sync or async). These tests pin:

- Flag OFF (default): the inline-handler user tool is ABSENT.
- Flag ON: the inline-handler user tool is PRESENT in ``build_cli_adk_tools`` AND
  ``dispatcher.dispatch(...)`` actually runs the inline handler and returns its
  output (built over a ``ToolCtx`` — no WorkspaceHostView).
- A tool scaffolded via ``scaffold_pack`` is now dispatchable end-to-end.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from magi_agent.cli.tool_runtime import (
    build_cli_adk_tools,
    build_cli_tool_runtime,
)
from magi_agent.packs.scaffold import scaffold_pack

_USER_TOOL_NAME = "UserInlineTool"

_PACK_TOML = """\
packId = "user.user-inline-pack"
displayName = "User Inline Pack"
version = "0.1.0"
description = "User-authored inline-handler tool pack for the PR6 activation test."

[[provides]]
type = "tool"
ref = "UserInlineTool"
impl = "user_inline_pack.impl:provide"
"""

_IMPL_PY = '''\
"""User tool provider that ships a plain INLINE handler (no WorkspaceHostView)."""
from __future__ import annotations

from collections.abc import Mapping

from magi_agent.packs.context import ToolCtx, ToolProvideContext
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _handle(args: Mapping[str, object], ctx: ToolCtx) -> dict[str, object]:
    return {"shouted": str(args.get("text", "")).upper(), "tool": ctx.tool_name}


def provide(context: ToolProvideContext) -> None:
    manifest = ToolManifest(
        name="UserInlineTool",
        description="Shout the provided text back to the caller.",
        kind="external",
        source=ToolSource(kind="external", package="user.user-inline-pack"),
        permission="read",
        input_schema=_INPUT_SCHEMA,
        timeout_ms=30_000,
        budget=Budget(max_calls_per_turn=10, max_parallel=1),
        dangerous=False,
        is_concurrency_safe=True,
        mutates_workspace=False,
        parallel_safety="readonly",
        available_in_modes=("plan", "act"),
        tags=("user",),
        enabled_by_default=True,
        opt_out=True,
    )
    if context.register_handler is not None:
        context.register_handler(manifest, _handle)
    else:  # pragma: no cover - projector predates PR6
        context.register(manifest)
'''


def _write_user_tool_pack(packs_base: Path) -> None:
    pack_dir = packs_base / "user_inline_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(_PACK_TOML)
    (pack_dir / "impl.py").write_text(_IMPL_PY)


def test_inline_handler_tool_absent_when_flag_off(tmp_path: Path, monkeypatch) -> None:
    packs_base = tmp_path / "packs"
    _write_user_tool_pack(packs_base)
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases",
        lambda: [packs_base],
    )
    monkeypatch.delenv("MAGI_USER_TOOL_PACKS_ENABLED", raising=False)

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    assert runtime.registry.resolve_registration(_USER_TOOL_NAME) is None

    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    tool_names = {getattr(tool, "name", None) for tool in tools}
    assert _USER_TOOL_NAME not in tool_names


def test_inline_handler_tool_present_and_dispatchable_when_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    packs_base = tmp_path / "packs"
    _write_user_tool_pack(packs_base)
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases",
        lambda: [packs_base],
    )
    monkeypatch.setenv("MAGI_USER_TOOL_PACKS_ENABLED", "1")

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    registration = runtime.registry.resolve_registration(_USER_TOOL_NAME)
    if registration is None:
        import sys as _sys

        from magi_agent.config.env import user_tool_packs_enabled
        from magi_agent.packs.registries import load_into_registries

        _pr, _rep = load_into_registries([packs_base])
        raise AssertionError(
            "inline-handler tool not merged into registry. DIAGNOSTIC: "
            f"flag_on={user_tool_packs_enabled()}, "
            f"impl_imported={'user_inline_pack.impl' in _sys.modules}, "
            f"loaded_tools={[m.name for m in _pr.tools.list_all()]}, "
            f"inline_handler_refs={_pr.tool_inline_handlers.list_refs()}, "
            f"report_registered={_rep.registered}"
        )
    assert registration.enabled is True
    assert registration.handler is not None, "inline handler not bound"

    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    tool_names = {getattr(tool, "name", None) for tool in tools}
    assert _USER_TOOL_NAME in tool_names

    context = runtime.tool_context_factory(None)
    result = asyncio.run(
        runtime.dispatcher.dispatch(
            _USER_TOOL_NAME,
            {"text": "hello"},
            context,
            mode="act",
        )
    )
    assert result.status == "ok", result
    assert result.output == {"shouted": "HELLO", "tool": _USER_TOOL_NAME}


def test_scaffolded_tool_is_dispatchable(tmp_path: Path, monkeypatch) -> None:
    """`magi pack new tool` now yields a runnable tool: scaffold it, then prove
    the generated handler dispatches through the CLI runtime."""
    packs_base = tmp_path / "packs"
    meta = scaffold_pack("tool", "my-greeter", packs_base)
    tool_name = meta.ref  # ToolManifest.name == the scaffold ref (PascalCase)

    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases",
        lambda: [packs_base],
    )
    monkeypatch.setenv("MAGI_USER_TOOL_PACKS_ENABLED", "1")

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    registration = runtime.registry.resolve_registration(tool_name)
    assert registration is not None, "scaffolded tool not merged into registry"
    assert registration.handler is not None, "scaffolded tool handler not bound"

    context = runtime.tool_context_factory(None)
    result = asyncio.run(
        runtime.dispatcher.dispatch(
            tool_name,
            {"text": "world"},
            context,
            mode="act",
        )
    )
    assert result.status == "ok", result
    assert isinstance(result.output, dict)
