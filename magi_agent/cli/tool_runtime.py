"""Real tool runtime for the local ``magi`` CLI agent.

The CLI engine is runner-agnostic: the tool set and system prompt are baked into
the ADK ``Agent`` at build time. This module assembles the genuine Magi Agent
tool runtime so the CLI agent reads/edits/greps files in an agentic loop instead
of running with ``tools=[]`` and a hand-written instruction.

The 9 first-party core tools (FileRead/FileWrite/FileEdit/PatchApply, Glob, Grep,
Bash, Clock, Calculation) are wired through the deliberately-ungated
``core_toolhost`` path: ``register_core_tool_manifests`` registers the metadata
and ``bind_core_toolhost_handlers`` binds the local Gate 5B toolhost handlers and
enables them via registry policy (no feature flag flip required).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from magi_agent.tools.context import ToolContext
    from magi_agent.tools.dispatcher import ToolDispatcher
    from magi_agent.tools.manifest import RuntimeMode
    from magi_agent.tools.registry import ToolRegistry

CLI_BOT_ID = "magi-cli"


@dataclass
class CliToolRuntime:
    """The assembled real tool runtime for the CLI agent."""

    registry: "ToolRegistry"
    dispatcher: "ToolDispatcher"
    tool_context_factory: "Callable[[object], ToolContext]"


def build_cli_tool_runtime(
    *,
    workspace_root: str,
    session_id: str = "cli-session",
) -> CliToolRuntime:
    """Assemble the registry, dispatcher, and tool-context factory.

    The factory does not derive identity from the ADK tool context; it forwards
    that context but stamps the CLI ``workspace_root`` (its cwd) plus session/turn
    identity onto every dispatched
    :class:`~magi_agent.tools.context.ToolContext`.
    """

    from magi_agent.tools.context import ToolContext  # noqa: PLC0415
    from magi_agent.tools.core_toolhost import (  # noqa: PLC0415
        bind_core_toolhost_handlers,
    )
    from magi_agent.tools.dispatcher import ToolDispatcher  # noqa: PLC0415
    from magi_agent.tools.registry import ToolRegistry  # noqa: PLC0415
    from magi_agent.tools import register_core_tool_manifests  # noqa: PLC0415

    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    bind_core_toolhost_handlers(registry)

    # Optional file & multimodal tools (MAGI_FILE_TOOLS_ENABLED=true).
    # Guarded here so the gate is evaluated at build time, not import time.
    from magi_agent.config.env import file_tools_enabled  # noqa: PLC0415

    if file_tools_enabled():
        from magi_agent.tools.file_tool_manifests import (  # noqa: PLC0415
            register_file_tool_manifests,
        )
        from magi_agent.tools.file_toolhost import (  # noqa: PLC0415
            bind_file_toolhost_handlers,
        )

        register_file_tool_manifests(registry)
        bind_file_toolhost_handlers(registry)

    dispatcher = ToolDispatcher(registry)

    def tool_context_factory(adk_tool_context: object) -> ToolContext:
        return ToolContext(
            bot_id=CLI_BOT_ID,
            session_id=session_id,
            turn_id="cli",
            workspace_root=workspace_root,
            adk_tool_context=adk_tool_context,
        )

    return CliToolRuntime(
        registry=registry,
        dispatcher=dispatcher,
        tool_context_factory=tool_context_factory,
    )


def build_cli_adk_tools(
    *,
    workspace_root: str,
    session_id: str = "cli-session",
    mode: "RuntimeMode" = "act",
) -> list[object]:
    """Build the ADK FunctionTools exposing the real core tools for the CLI."""

    from magi_agent.adk_bridge.tool_adapter import (  # noqa: PLC0415
        build_adk_function_tools_for_registry,
    )

    runtime = build_cli_tool_runtime(
        workspace_root=workspace_root,
        session_id=session_id,
    )
    return build_adk_function_tools_for_registry(
        runtime.registry,
        runtime.dispatcher,
        mode=mode,
        tool_context_factory=runtime.tool_context_factory,
        attach_enabled=True,
    )


def build_cli_instruction(
    *,
    session_id: str,
    model: str = "",
    workspace_root: str | None = None,
) -> str:
    """Build the real system prompt for the CLI agent (coding-agent path).

    When ``workspace_root`` is supplied, optional project instruction files
    (``AGENTS.md`` / ``SOUL.md`` / ``TOOLS.md`` / ``CLAUDE.md``) found in that cwd
    (and its ``.magi/`` subdir) are loaded and rendered into the system prompt so
    the CLI agent picks up repo conventions, matching Claude Code / OpenCode.
    """

    from magi_agent.runtime.message_builder import build_system_prompt  # noqa: PLC0415

    identity = None
    if workspace_root is not None:
        from magi_agent.cli.identity import load_identity  # noqa: PLC0415

        identity = load_identity(workspace_root)

    prompt = build_system_prompt(
        session_key=session_id,
        turn_id="cli",
        identity=identity,
        coding_agent=True,
        model=model,
    )
    return (
        f"{prompt}\n\n"
        "<skills>\n"
        "Bundled first-party skills, including superpowers-style workflows, are "
        "available through the SkillLoader tool. Before specialized work such "
        "as debugging, planning, code review, research, writing, or UI work, "
        "load the relevant skill and follow its instructions.\n"
        "</skills>"
    )


__all__ = [
    "CLI_BOT_ID",
    "CliToolRuntime",
    "build_cli_adk_tools",
    "build_cli_instruction",
    "build_cli_tool_runtime",
]
