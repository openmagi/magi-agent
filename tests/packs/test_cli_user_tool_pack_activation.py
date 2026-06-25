"""PR1 — a user-authored TOOL pack reaches the CLI agent, default-OFF.

``build_cli_tool_runtime`` historically built its own ``ToolRegistry`` and only
registered hardcoded first-party sources, so a user tool pack scaffolded into
``~/.magi/packs`` / ``<cwd>/.magi/packs`` (or any discovery base) was loaded and
projected into ``registries.tools`` but never merged into the CLI runtime
registry — the agent could never see or dispatch it.

These tests pin the gate contract:

- Flag OFF (default): the user pack tool is ABSENT from the CLI runtime registry
  and the advertised ADK tool set (byte-identical to before).
- Flag ON (``MAGI_USER_TOOL_PACKS_ENABLED=1``): the user pack tool is PRESENT in
  the registry with a bound, enabled handler, the ADK tool list exposes it, and
  dispatching it actually runs the user pack's workspace handler and returns its
  output.

The user pack authors a dispatchable tool the way the live C1 seam supports
today: ``provide()`` registers a ``ToolManifest`` AND binds a workspace handler
``(args, WorkspaceHostView) -> output`` via ``register_workspace_handler``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from magi_agent.cli.tool_runtime import (
    build_cli_adk_tools,
    build_cli_tool_runtime,
)

_USER_TOOL_NAME = "UserEchoTool"

_PACK_TOML = """\
packId = "user.user-echo-pack"
displayName = "User Echo Pack"
version = "0.1.0"
description = "User-authored tool pack for the CLI activation test."

[[provides]]
type = "tool"
ref = "UserEchoTool"
impl = "user_echo_pack.impl:provide"
"""

_IMPL_PY = '''\
"""User tool provider that registers a manifest AND a workspace handler."""
from __future__ import annotations

from collections.abc import Mapping

from magi_agent.packs.context import ToolProvideContext, WorkspaceHostView
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _handle(args: Mapping[str, object], view: WorkspaceHostView) -> dict[str, object]:
    return {"echoed": str(args.get("text", ""))}


def provide(context: ToolProvideContext) -> None:
    context.register(
        ToolManifest(
            name="UserEchoTool",
            description="Echo the provided text back to the caller.",
            kind="external",
            source=ToolSource(kind="external", package="user.user-echo-pack"),
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
    )
    if context.register_workspace_handler is not None:
        context.register_workspace_handler("UserEchoTool", _handle)
'''


def _write_user_tool_pack(packs_base: Path) -> None:
    pack_dir = packs_base / "user_echo_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(_PACK_TOML)
    (pack_dir / "impl.py").write_text(_IMPL_PY)


def test_user_tool_pack_absent_when_flag_off(tmp_path: Path, monkeypatch) -> None:
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


def test_user_tool_pack_present_and_dispatchable_when_flag_on(
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
    assert registration is not None, "user tool manifest not merged into registry"
    assert registration.enabled is True
    assert registration.handler is not None, "user tool handler not bound"

    # The tool is offered to the agent in act mode.
    act_names = {m.name for m in runtime.registry.list_available(mode="act")}
    assert _USER_TOOL_NAME in act_names

    # The ADK tool list exposes it too.
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    tool_names = {getattr(tool, "name", None) for tool in tools}
    assert _USER_TOOL_NAME in tool_names

    # Dispatching it actually invokes the user pack's workspace handler.
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
    assert result.output == {"echoed": "hello"}


def test_user_tool_pack_never_overrides_core_tool(tmp_path: Path, monkeypatch) -> None:
    """A user pack tool whose name collides with an ungated core tool is skipped:
    the core ``FileRead`` handler must remain the bound one."""
    packs_base = tmp_path / "packs"
    pack_dir = packs_base / "shadow_pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "__init__.py").write_text("")
    (pack_dir / "pack.toml").write_text(
        _PACK_TOML.replace("user.user-echo-pack", "user.shadow-pack")
        .replace("user_echo_pack", "shadow_pack")
        .replace('ref = "UserEchoTool"', 'ref = "FileRead"')
    )
    (pack_dir / "impl.py").write_text(
        _IMPL_PY.replace('"UserEchoTool"', '"FileRead"')
    )
    monkeypatch.setattr(
        "magi_agent.packs.discovery.default_search_bases",
        lambda: [packs_base],
    )
    monkeypatch.setenv("MAGI_USER_TOOL_PACKS_ENABLED", "1")

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    registration = runtime.registry.resolve_registration("FileRead")
    assert registration is not None
    # The core FileRead manifest (kind="core") must survive — not the user
    # external manifest.
    assert registration.manifest.kind == "core"
