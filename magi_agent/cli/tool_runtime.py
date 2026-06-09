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

from collections.abc import Mapping
from dataclasses import dataclass
from inspect import isawaitable, signature
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.harness.general_automation.live_gate import (
        GeneralAutomationReceiptLedgerStore,
    )
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
    general_automation_receipts: "GeneralAutomationReceiptLedgerStore"


def build_cli_tool_runtime(
    *,
    workspace_root: str,
    session_id: str = "cli-session",
    general_automation_receipts: "GeneralAutomationReceiptLedgerStore | None" = None,
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
    from magi_agent.harness.general_automation.live_gate import (  # noqa: PLC0415
        GeneralAutomationReceiptLedgerStore,
    )

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

    receipt_store = general_automation_receipts or GeneralAutomationReceiptLedgerStore()
    dispatcher = ToolDispatcher(
        registry,
        general_automation_receipts=receipt_store,
    )

    def tool_context_factory(adk_tool_context: object) -> ToolContext:
        return ToolContext(
            bot_id=CLI_BOT_ID,
            session_id=session_id,
            turn_id="cli",
            workspace_root=workspace_root,
            execution_contract={"agentRole": "general"},
            adk_tool_context=adk_tool_context,
        )

    return CliToolRuntime(
        registry=registry,
        dispatcher=dispatcher,
        tool_context_factory=tool_context_factory,
        general_automation_receipts=receipt_store,
    )


def build_cli_adk_tools(
    *,
    workspace_root: str,
    session_id: str = "cli-session",
    mode: "RuntimeMode" = "act",
    general_automation_receipts: "GeneralAutomationReceiptLedgerStore | None" = None,
    local_tool_evidence_collector: "LocalToolEvidenceCollector | None" = None,
) -> list[object]:
    """Build the ADK FunctionTools exposing the real core tools for the CLI."""

    from magi_agent.adk_bridge.tool_adapter import (  # noqa: PLC0415
        build_adk_function_tools_for_registry,
    )

    runtime = build_cli_tool_runtime(
        workspace_root=workspace_root,
        session_id=session_id,
        general_automation_receipts=general_automation_receipts,
    )
    tools = build_adk_function_tools_for_registry(
        runtime.registry,
        runtime.dispatcher,
        mode=mode,
        tool_context_factory=runtime.tool_context_factory,
        attach_enabled=True,
    )
    return wrap_cli_adk_tools_with_evidence_collector(
        tools,
        collector=local_tool_evidence_collector,
        session_id=session_id,
    )


def wrap_cli_adk_tools_with_evidence_collector(
    tools: list[object],
    *,
    collector: "LocalToolEvidenceCollector | None",
    session_id: str,
) -> list[object]:
    """Record local ADK tool results into the shared CLI evidence collector."""

    if collector is None:
        return tools
    record_tool_result = getattr(collector, "record_tool_result", None)
    if not callable(record_tool_result):
        return tools

    for tool in tools:
        original = getattr(tool, "func", None)
        if not callable(original) or getattr(tool, "_magi_evidence_collector_wrapped", False):
            continue

        async def _wrapped_func(
            arguments: dict[str, object],
            tool_context: object,
            *,
            _original: Callable[[dict[str, object], object], object] = original,
            _tool: object = tool,
        ) -> object:
            result = _original(arguments, tool_context)
            if isawaitable(result):
                result = await result
            try:
                record_tool_result(
                    session_id=session_id,
                    turn_id=_adk_tool_context_turn_id(tool_context),
                    tool_call_id=_tool_call_id(tool_context, result),
                    tool_name=_tool_name(_tool, result),
                    result=result,
                    arguments=arguments,
                )
            except Exception:
                pass
            return result

        _wrapped_func.__name__ = getattr(original, "__name__", "invoke_openmagi_tool")
        _wrapped_func.__doc__ = getattr(original, "__doc__", None)
        try:
            setattr(_wrapped_func, "__signature__", signature(original))
        except (TypeError, ValueError):
            pass
        try:
            setattr(tool, "func", _wrapped_func)
            setattr(tool, "_magi_evidence_collector_wrapped", True)
        except Exception:
            continue
    return tools


def _adk_tool_context_turn_id(tool_context: object) -> str:
    for value in (
        _context_lookup(tool_context, "invocation_id"),
        _context_lookup(_context_lookup(tool_context, "invocation_context"), "invocation_id"),
        _context_lookup(_context_lookup(tool_context, "event"), "invocation_id"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "local-turn"


def _tool_call_id(tool_context: object, result: object) -> str:
    metadata = _result_metadata(result)
    value = metadata.get("toolCallId") or metadata.get("tool_call_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    function_call = _context_lookup(tool_context, "function_call")
    value = _context_lookup(function_call, "id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "local-tool-call"


def _tool_name(tool: object, result: object) -> str:
    metadata = _result_metadata(result)
    value = metadata.get("toolName") or metadata.get("tool_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    value = getattr(tool, "name", None)
    return value if isinstance(value, str) and value.strip() else "unknown_tool"


def _result_metadata(result: object) -> Mapping[str, object]:
    if isinstance(result, Mapping):
        metadata = result.get("metadata")
        return metadata if isinstance(metadata, Mapping) else {}
    metadata = getattr(result, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _context_lookup(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def build_cli_instruction(
    *,
    session_id: str,
    model: str = "",
    workspace_root: str | None = None,
) -> str:
    """Build the real system prompt for the CLI agent (coding-agent path).

    When ``workspace_root`` is supplied, the agent's self identity is loaded
    from the magi-owned ``.magi`` namespace (``~/.magi`` + ``<cwd>/.magi``),
    while repo-root ``AGENTS.md`` / ``CLAUDE.md`` are loaded as project context
    (NOT identity). See :func:`magi_agent.cli.identity.load_identity`.
    """
    from pathlib import Path  # noqa: PLC0415

    from magi_agent.runtime.memory_snapshot_cache import MemorySnapshotCache  # noqa: PLC0415
    from magi_agent.runtime.message_builder import build_system_prompt  # noqa: PLC0415

    identity = None
    if workspace_root is not None:
        from magi_agent.cli.identity import load_identity  # noqa: PLC0415

        identity = load_identity(workspace_root)

    # Compute the frozen memory snapshot once for this session.
    # Falls back to "" when workspace_root is not provided, gate is off, or
    # memory_mode is incognito.
    memory_snapshot_block = ""
    if workspace_root is not None:
        _snapshot_cache = MemorySnapshotCache(workspace_root=Path(workspace_root))
        memory_snapshot_block = _snapshot_cache.get(session_id, memory_mode="normal")

    prompt = build_system_prompt(
        session_key=session_id,
        turn_id="cli",
        identity=identity,
        coding_agent=True,
        model=model,
        memory_snapshot_block=memory_snapshot_block,
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
    "wrap_cli_adk_tools_with_evidence_collector",
]
