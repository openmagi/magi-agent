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
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from magi_agent.runtime.session_identity import MemoryMode

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
CLI_USER_ID = "cli"


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
    memory_mode: "MemoryMode | str" = "normal",
    general_automation_receipts: "GeneralAutomationReceiptLedgerStore | None" = None,
    local_tool_evidence_collector: "LocalToolEvidenceCollector | None" = None,
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
    bind_cli_local_full_tool_handlers(
        registry,
        workspace_root=workspace_root,
        bot_id=CLI_BOT_ID,
        user_id=CLI_USER_ID,
    )

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

    # Optional autonomous vision browser tool (MAGI_BROWSER_TOOL_ENABLED=true).
    from magi_agent.config.env import browser_tool_enabled  # noqa: PLC0415

    if browser_tool_enabled():
        from magi_agent.browser.autonomous.tool import (  # noqa: PLC0415
            register_browser_tool_manifest,
            bind_browser_toolhost_handler,
        )

        register_browser_tool_manifest(registry)
        bind_browser_toolhost_handler(registry)

    receipt_store = general_automation_receipts or GeneralAutomationReceiptLedgerStore()
    dispatcher = ToolDispatcher(
        registry,
        general_automation_receipts=receipt_store,
    )
    memory_mode_value = (
        memory_mode.value if isinstance(memory_mode, MemoryMode) else str(memory_mode)
    )

    def tool_context_factory(adk_tool_context: object) -> ToolContext:
        return ToolContext(
            bot_id=CLI_BOT_ID,
            user_id=CLI_USER_ID,
            session_id=session_id,
            session_key=session_id,
            turn_id="cli",
            workspace_root=workspace_root,
            workspace_ref="local-cli-workspace",
            memory_mode=memory_mode_value,
            channel="cli",
            permission_scope={
                "mode": "selected_full_toolhost",
                "source": "selected_full_toolhost",
            },
            execution_contract={"agentRole": "general"},
            source_ledger=_source_ledger_for_session(
                local_tool_evidence_collector,
                session_id,
            ),
            adk_tool_context=adk_tool_context,
            adk_context=adk_tool_context,
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
    memory_mode: "MemoryMode | str" = "normal",
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
        memory_mode=memory_mode,
        general_automation_receipts=general_automation_receipts,
        local_tool_evidence_collector=local_tool_evidence_collector,
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


def bind_cli_local_full_tool_handlers(
    registry: "ToolRegistry",
    *,
    workspace_root: str | Path,
    bot_id: str,
    user_id: str,
) -> None:
    """Bind local-full gated tool hosts into a CLI/dashboard registry."""

    from magi_agent.introspection.tool import (  # noqa: PLC0415
        bind_inspect_self_evidence_handler,
    )
    from magi_agent.runtime.memory_write_wiring import (  # noqa: PLC0415
        build_memory_write_host,
    )

    bind_inspect_self_evidence_handler(registry)
    memory_write_host = build_memory_write_host(
        workspace_root=Path(workspace_root),
        bot_id=bot_id,
        user_id=user_id,
    )
    memory_write_host.bind(registry)


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


def _source_ledger_for_session(
    collector: "LocalToolEvidenceCollector | None",
    session_id: str,
) -> tuple[object, ...]:
    """Thread the collector's per-turn EvidenceLedgers onto ``source_ledger``.

    Flag-gated + fail-open: when ``MAGI_EVIDENCE_LEDGER_LIFECYCLE_ENABLED`` is
    off (default) this returns the empty tuple so the ToolContext is
    byte-identical to today. When on, it returns the collector's
    ``evidence_ledgers_for_session`` so ``InspectSelfEvidence`` can project the
    REAL tool calls recorded so far. Any failure collapses to an empty tuple.
    """
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            is_evidence_ledger_lifecycle_enabled,
        )

        if not is_evidence_ledger_lifecycle_enabled():
            return ()
        ledgers_for_session = getattr(collector, "evidence_ledgers_for_session", None)
        if not callable(ledgers_for_session):
            return ()
        return tuple(ledgers_for_session(session_id))
    except Exception:
        return ()


def build_cli_instruction(
    *,
    session_id: str,
    model: str = "",
    workspace_root: str | None = None,
    memory_mode: "MemoryMode | str" = "normal",
) -> str:
    """Build the real system prompt for the CLI agent (coding-agent path).

    When ``workspace_root`` is supplied, the agent's self identity is loaded
    from the magi-owned ``.magi`` namespace (``~/.magi`` + ``<cwd>/.magi``),
    while repo-root ``AGENTS.md`` / ``CLAUDE.md`` are loaded as project context
    (NOT identity). See :func:`magi_agent.cli.identity.load_identity`.

    ``memory_mode`` defaults to ``"normal"`` (byte-identical to before): only a
    ``read_only`` / ``incognito`` mode injects the corresponding memory-mode block
    via the ``channel`` passed to :func:`build_system_prompt`.
    """
    from pathlib import Path  # noqa: PLC0415

    from magi_agent.runtime.memory_snapshot_cache import MemorySnapshotCache  # noqa: PLC0415
    from magi_agent.runtime.message_builder import build_system_prompt  # noqa: PLC0415

    identity = None
    if workspace_root is not None:
        from magi_agent.cli.identity import load_identity  # noqa: PLC0415

        identity = load_identity(workspace_root)

    memory_mode_value = (
        memory_mode.value if isinstance(memory_mode, MemoryMode) else str(memory_mode)
    )

    # Compute the frozen memory snapshot once for this session.
    # Falls back to "" when workspace_root is not provided, gate is off, or
    # memory_mode is incognito.
    memory_snapshot_block = ""
    if workspace_root is not None:
        _snapshot_cache = MemorySnapshotCache(workspace_root=Path(workspace_root))
        memory_snapshot_block = _snapshot_cache.get(
            session_id,
            memory_mode=memory_mode_value,
        )

    # Append active learnings from the local store (default-OFF gate:
    # MAGI_LEARNING_INJECTION_ENABLED).  Returns "" when gate is off,
    # memory_mode is incognito, no db exists, or any error — so the combined
    # block is byte-identical to pre-wiring when the gate is off.
    # Scope note: only task_kind="general" learnings surface here today (all
    # labeler-written items).  A future per-task-kind labeler would need to
    # thread the current task kind into build_cli_learning_recall_block.
    from magi_agent.cli.learning_recall import build_cli_learning_recall_block  # noqa: PLC0415

    _learning_block = build_cli_learning_recall_block(
        workspace_root=workspace_root,
        memory_mode=memory_mode_value,
    )
    if _learning_block:
        memory_snapshot_block = "\n\n".join(
            part for part in (memory_snapshot_block, _learning_block) if part
        )

    prompt = build_system_prompt(
        session_key=session_id,
        turn_id="cli",
        identity=identity,
        channel={"memoryMode": memory_mode_value},
        coding_agent=True,
        model=model,
        memory_snapshot_block=memory_snapshot_block,
    )
    return (
        f"{prompt}\n\n"
        "<file_tools>\n"
        "When the task involves an image, document, spreadsheet, or other "
        "attached file:\n"
        "- Use ImageUnderstand(path=..., prompt=...) for image files "
        "(.png/.jpg/.jpeg/.gif/.webp/.bmp).\n"
        "- Use DocumentRead(path=...) for document files "
        "(.pdf/.docx/.pptx/.xml/.csv/.txt/.md/.rst).\n"
        "- Use XLSXRead(path=...) for spreadsheet files (.xlsx/.xls).\n"
        "- If a tool returns status='blocked' or status='needs_approval', "
        "attempt an alternative approach: read the file with Bash (e.g. "
        "`cat`, `python3`) before concluding the file is inaccessible.\n"
        "- Never conclude 'unable to determine' solely because a tool returned "
        "an error; try at least one alternative access path first.\n"
        "</file_tools>\n\n"
        "<skills>\n"
        "Bundled first-party skills, including superpowers-style workflows, are "
        "available through the SkillLoader tool. Before specialized work such "
        "as debugging, planning, code review, research, writing, or UI work, "
        "load the relevant skill and follow its instructions.\n"
        "</skills>"
    )


__all__ = [
    "CLI_BOT_ID",
    "CLI_USER_ID",
    "CliToolRuntime",
    "build_cli_adk_tools",
    "build_cli_instruction",
    "build_cli_tool_runtime",
    "bind_cli_local_full_tool_handlers",
    "wrap_cli_adk_tools_with_evidence_collector",
]
