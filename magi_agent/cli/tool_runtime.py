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


_LEGACY_FULL_TOOLHOST_SCOPE: dict[str, object] = {
    "mode": "selected_full_toolhost",
    "source": "selected_full_toolhost",
}


def build_cli_tool_runtime(
    *,
    workspace_root: str,
    session_id: str = "cli-session",
    memory_mode: "MemoryMode | str" = "normal",
    permission_mode: str = "default",
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

    # Optional persistent Python code-execution tool (MAGI_CODE_ACTION_ENABLED=true).
    # Strict opt-in default-OFF: when unset the module is never imported and the
    # registry is byte-identical to before.
    from magi_agent.config.env import code_action_enabled  # noqa: PLC0415

    if code_action_enabled():
        from magi_agent.tools.python_exec import (  # noqa: PLC0415
            bind_python_exec_handler,
            register_python_exec_manifest,
        )

        register_python_exec_manifest(registry)
        bind_python_exec_handler(registry)

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
            permission_scope=_resolve_cli_permission_scope(
                adk_tool_context,
                registry=registry,
                permission_mode=permission_mode,
            ),
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
    permission_mode: str = "default",
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
        permission_mode=permission_mode,
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
    # Fast direct web tools auto-activate on provider-key presence (the builder
    # is key-gated and returns [] without BRAVE+FIRECRAWL keys, so keyless
    # installs are byte-identical). These previously existed with zero
    # consumers — a fresh install with keys still had no live web capability.
    from magi_agent.tools.web_search_tools import build_web_search_tools  # noqa: PLC0415

    tools = [*tools, *build_web_search_tools()]
    return wrap_cli_adk_tools_with_evidence_collector(
        tools,
        collector=local_tool_evidence_collector,
        session_id=session_id,
    )


def _resolve_cli_permission_scope(
    adk_tool_context: object,
    *,
    registry: "ToolRegistry",
    permission_mode: str,
) -> dict[str, object]:
    """Return the ``permission_scope`` for a CLI tool call.

    When ``MAGI_PERMISSION_SCOPE_FROM_MODE`` is OFF (default) this returns the
    legacy hardcoded ``selected_full_toolhost`` scope — byte-identical to the
    pre-PR1 behavior, so the regression surface is zero. When ON, the scope is
    derived from ``permission_mode`` + the called tool's manifest via
    :class:`~magi_agent.tools.permission_scope.PermissionScopeResolver`:
    ``default``/``smartApprove`` get no preapproval (arbiter ``ask`` reaches),
    ``acceptEdits`` preapproves only edit-class tools, ``bypassPermissions`` gets
    a ``bypass`` scope (hard-safety still enforced).

    Fail-open: any error (gate lookup, manifest resolution) collapses back to the
    legacy scope so a malformed runtime never breaks tool dispatch.
    """
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            permission_scope_from_mode_enabled,
        )

        if not permission_scope_from_mode_enabled():
            return dict(_LEGACY_FULL_TOOLHOST_SCOPE)

        manifest = _manifest_for_adk_context(adk_tool_context, registry=registry)
        if manifest is None:
            # No manifest in hand -> mode-only resolution: bypass stays bypass,
            # everything else stays strict (no preapproval) so the arbiter ask
            # branch can reach.
            if str(permission_mode).strip() == "bypassPermissions":
                return {"mode": "bypass", "source": "bypass"}
            return {"mode": "default", "source": "builtin"}

        from magi_agent.tools.permission_scope import (  # noqa: PLC0415
            PermissionScopeResolver,
        )

        return PermissionScopeResolver().resolve(
            permission_mode=permission_mode,
            manifest=manifest,
            channel="cli",
        )
    except Exception:
        return dict(_LEGACY_FULL_TOOLHOST_SCOPE)


def _manifest_for_adk_context(
    adk_tool_context: object,
    *,
    registry: "ToolRegistry",
) -> object | None:
    """Best-effort resolution of the called tool's manifest from the ADK ctx."""
    function_call = _context_lookup(adk_tool_context, "function_call")
    tool_name = _context_lookup(function_call, "name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return None
    registration = registry.resolve_registration(tool_name.strip())
    if registration is None:
        return None
    return getattr(registration, "manifest", None)


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
    from magi_agent.tools.ask_user_question_toolhost import (  # noqa: PLC0415
        bind_ask_user_question_handler,
    )
    from magi_agent.tools.plan_mode_toolhost import (  # noqa: PLC0415
        bind_plan_mode_handlers,
    )

    bind_inspect_self_evidence_handler(registry)
    # Route the catalog AskUserQuestion / Enter/ExitPlanMode manifests to their
    # existing General-Automation implementations (doc 12 PR2 / B14). Both
    # binders read the strict default-OFF MAGI_PLAN_MODE_TOOLS_ENABLED gate: when
    # OFF the tools are bound-but-disabled (hidden, byte-identical to main); when
    # ON they are advertised and dispatch to the GA question / plan-act flow.
    bind_ask_user_question_handler(registry)
    bind_plan_mode_handlers(registry)
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


def build_tool_advertisement_block(*, workspace_root: str | None = None) -> str:
    """Build an ``<available_tools>`` XML block from the currently-enabled tool set.

    Assembles a throw-away local runtime using the same env-gated registration
    and bind-time policy path as :func:`build_cli_tool_runtime`. This keeps the
    advertised catalog aligned with tools that become enabled only when their
    host binds a handler (for example ``BrowserTask`` and
    ``InspectSelfEvidence``).
    Each enabled tool is emitted as one line::

        ToolName [permission] — one-line description

    The block is wrapped in ``<available_tools>`` / ``</available_tools>`` tags
    so the model can identify the catalog section unambiguously.  A newly-
    registered tool group (file tools, browser tool, …) automatically becomes
    visible as soon as its env gate is turned on — no manual prompt edits needed.

    Fail-open: any error returns an empty string so prompt assembly never breaks.
    """
    try:
        registry = build_cli_tool_runtime(
            workspace_root=str(Path(workspace_root or ".").resolve()),
            session_id="tool-advertisement",
        ).registry

        lines: list[str] = []
        for tool_name in sorted(registry._tools):  # noqa: SLF001
            registration = registry._tools[tool_name]  # noqa: SLF001
            if not registration.enabled:
                continue
            manifest = registration.manifest
            # Emit: ToolName [permission] — first sentence of description
            desc = manifest.description.split("\n")[0].rstrip()
            lines.append(f"  {manifest.name} [{manifest.permission}] — {desc}")

        if not lines:
            return ""

        tool_list = "\n".join(lines)
        return (
            "<available_tools>\n"
            "The following tools are attached to this session. "
            "Use the right tool for the task — newly-added tools are listed here "
            "and may not be mentioned elsewhere in this prompt.\n"
            f"{tool_list}\n"
            "</available_tools>"
        )
    except Exception:
        return ""


def eval_autonomy_block(env: Mapping[str, str] | None = None) -> str:
    """Return the eval-mode autonomy + self-verify system-prompt block.

    Returns an empty string when ``MAGI_EVAL_AUTONOMY_ENABLED`` is falsy (or
    when the env mapping explicitly disables it), so the caller's prompt is
    byte-identical to the non-eval path when the flag is off.

    Imported lazily inside to keep ``import cli.tool_runtime`` cold-clean.
    """
    import os as _os  # noqa: PLC0415

    from magi_agent.config.env import parse_eval_autonomy_enabled  # noqa: PLC0415

    source = env if env is not None else _os.environ
    if not parse_eval_autonomy_enabled(source):
        return ""
    return (
        "\n\n<autonomous_execution>\n"
        "You are operating autonomously with write access and no human in the loop.\n"
        "- Apply every fix by editing/creating files now. Never ask for confirmation "
        "and never end by only describing the change — make it.\n"
        "- Workflow: explore the codebase, then write a small reproduction script "
        "for the reported issue FIRST and confirm it fails; only then edit source. "
        "Fix the root cause, not the surface symptom, and cover edge cases.\n"
        "- Before you finish, VERIFY: run the project's existing tests for the code you "
        "changed and your reproduction. If anything fails, fix it and "
        "re-run until green. Do not conclude until your change is test-verified.\n"
        "- Do not modify existing test files or project configuration to make tests "
        "pass — change the source under test.\n"
        "- Before concluding, check `git diff`: the change set must contain ONLY the "
        "intended source edits. Delete reproduction/debug scripts and revert any "
        "scratch changes so they do not appear in the diff.\n"
        "- Be thorough: use as many tool calls as you need (dozens are normal); "
        "do not stop early because the work feels long.\n"
        "- Verification must cover more than your own reproduction: for EVERY function "
        "or module you modified, grep the test suite for existing tests that exercise "
        "it and run those test files COMPLETELY (all parametrized variants), not just "
        "the tests you wrote. Sibling code paths (masked/alternate-type overloads, "
        "False-parameter variants) break most often.\n"
        "- Derive expected behavior from the spec, docs, or reference implementation — "
        "never anchor expected values on possibly-buggy pre-existing tests.\n"
        "- After creating or moving a module, run a cold-interpreter import of the "
        "package (fresh `python -c \"import <pkg>\"` or a fresh pytest collection) to "
        "catch collection-time circular imports.\n"
        "- Before deleting any file, grep for references to it (tests, fixtures, "
        "imports) and update or avoid the deletion if anything still references it.\n"
        "- Prefer behavior-based assertions (resulting data/state) over artifact "
        "string-matching (e.g. generated SQL fragments) when writing verification.\n"
        "</autonomous_execution>"
    )


def compute_via_code_block(env: Mapping[str, str] | None = None) -> str:
    """Return the compute-via-code directive system-prompt block.

    Returns an empty string when ``MAGI_COMPUTE_VIA_CODE_ENABLED`` is falsy (or
    when the env mapping explicitly disables it), so the caller's prompt is
    byte-identical to the non-directive path when the flag is off.

    The directive is a GENERAL agent-hygiene capability — it carries no
    benchmark-specific text — instructing the agent to compute numeric results
    by writing and running code rather than computing them mentally. Imported
    lazily inside to keep ``import cli.tool_runtime`` cold-clean.
    """
    import os as _os  # noqa: PLC0415

    from magi_agent.config.env import compute_via_code_enabled  # noqa: PLC0415

    source = env if env is not None else _os.environ
    if not compute_via_code_enabled(source):
        return ""
    return (
        "<compute_via_code>\n"
        "For ANY arithmetic, unit conversion, statistics (mean/median/sum), or "
        "checksum/validation computation, WRITE AND RUN code via the Bash or "
        "Calculation tool and report the value the tool returned. Do NOT compute "
        "such values in your head — even simple-looking ones — because mental "
        "arithmetic is a frequent source of wrong answers.\n"
        "- Bigger multi-step math, conversions, or aggregates: write a short "
        "Python snippet and run it with Bash (`python3 -c ...`).\n"
        "- Checksum/validation (e.g. ISBN-like check digits): implement the "
        "exact algorithm in code rather than estimating.\n"
        "This applies only to NUMERIC computation. It does NOT change how you "
        "extract source values: keep using the appropriate file/vision/web tool "
        "(e.g. structured image extraction) to obtain the inputs, then compute "
        "with code.\n"
        "</compute_via_code>"
    )


def output_format_adherence_block(env: Mapping[str, str] | None = None) -> str:
    """Return the output-format-adherence guidance block.

    Returns an empty string when ``MAGI_FORMAT_ADHERENCE_ENABLED`` is falsy (the
    default) so the caller's prompt is byte-identical to the non-gated path: the
    ``<output_format_adherence>`` marker is simply absent. When on, returns a
    GENERAL guidance block (no benchmark-specific text) telling the agent to
    conform to the question's explicit output requirements before finalizing.

    Imported lazily inside to keep ``import cli.tool_runtime`` cold-clean.
    """
    import os as _os  # noqa: PLC0415

    from magi_agent.config.env import parse_format_adherence_enabled  # noqa: PLC0415

    source = env if env is not None else _os.environ
    if not parse_format_adherence_enabled(source):
        return ""
    return (
        "<output_format_adherence>\n"
        "Before you give your final answer, re-read the question's explicit "
        "output requirements and conform to them exactly:\n"
        "- Units & scale: report the value in the units and at the scale the "
        "question asks for (e.g. thousands vs the raw count, kilometers vs "
        "meters). Convert if necessary.\n"
        "- Rounding precision: round to the precision the question requests "
        "(e.g. nearest picometer, two decimal places) — no more, no fewer "
        "significant figures than asked.\n"
        "- Name & format: use the canonical name or format requested (full "
        "name vs abbreviation, the exact character/symbol asked for, the "
        "requested ordering or separators).\n"
        "- Do not add units, words, articles, or explanation that the question "
        "did not request; answer with exactly what was asked and nothing more.\n"
        "</output_format_adherence>"
    )


def step_decomposition_block(env: Mapping[str, str] | None = None) -> str:
    """Return the multi-step decomposition guidance system-prompt block.

    Returns an empty string when ``MAGI_STEP_DECOMPOSITION_ENABLED`` is falsy (or
    when the env mapping explicitly disables it), so the caller's prompt is
    byte-identical to the non-decomposition path when the flag is off. When on it
    returns a leading-``\\n\\n`` block (matching ``eval_autonomy_block``) so it
    appends cleanly into the ``"\\n\\n".join(parts)`` assembly.

    This is a *light*, prompt-only nudge — it asks the model to plan the
    dependent sub-steps of a multi-hop question and confirm each before
    proceeding, reusing the existing planning/TodoWrite seams. It is a GENERAL
    agent capability with no benchmark-specific text; GAIA advertisement lives in
    the benchmark prompt layer only.

    Imported lazily inside to keep ``import cli.tool_runtime`` cold-clean.
    """
    import os as _os  # noqa: PLC0415

    from magi_agent.config.env import is_step_decomposition_enabled  # noqa: PLC0415

    source = env if env is not None else _os.environ
    if not is_step_decomposition_enabled(source):
        return ""
    return (
        "\n\n<step_decomposition>\n"
        "For a multi-step question whose answer depends on a chain of "
        "intermediate facts (A leads to B leads to C ...), do not jump straight "
        "to the final answer.\n"
        "- First enumerate the ordered, dependent sub-steps you must resolve "
        "(use your planning/TodoWrite seam if available).\n"
        "- Resolve and explicitly confirm each sub-step's result before using it "
        "as input to the next; if a link is uncertain, verify it before "
        "proceeding rather than guessing onward.\n"
        "- Carry the confirmed intermediate result forward verbatim so a wrong "
        "or paraphrased link does not silently corrupt the final answer.\n"
        "Keep this lightweight — it is a planning discipline, not a reason to add "
        "extra tool calls beyond what each sub-step needs.\n"
        "</step_decomposition>"
    )


def multi_file_join_block(env: Mapping[str, str] | None = None) -> str:
    """Return the multi-file cross-reference robustness block (default-OFF).

    Returns an empty string when ``MAGI_MULTI_FILE_JOIN_ENABLED`` is falsy (the
    default) so the caller's prompt is byte-identical to the non-flagged path.
    When ON, returns a SINGLE domain-neutral guidance block instructing the
    agent, after ``ArchiveExtract``, to: (1) exhaustively enumerate ALL
    extracted files, (2) read structured data (XLSX/XML) in full, and (3)
    perform any cross-file join / dedup PROGRAMMATICALLY via Bash rather than by
    eye.

    Anti-overfit: the text names no benchmark and no benchmark-specific entity
    — it is general multi-file-reasoning hygiene. The IDENTICAL string is reused
    by the GAIA bench harness so the A/B arm exercises this exact lever.

    Imported lazily inside to keep ``import cli.tool_runtime`` cold-clean.
    """
    import os as _os  # noqa: PLC0415

    from magi_agent.config.env import multi_file_join_enabled  # noqa: PLC0415

    source = env if env is not None else _os.environ
    if not multi_file_join_enabled(source):
        return ""
    return (
        "<multi_file_join>\n"
        "When a task provides multiple files — especially an archive you opened "
        "with ArchiveExtract — do NOT reason about the relationship between files "
        "by eye. Eyeballing a cross-reference across files is the most common "
        "source of join/dedup errors.\n"
        "1. After ArchiveExtract, exhaustively enumerate ALL extracted files "
        "(e.g. `ls -R` via Bash). Do not assume there are only one or two.\n"
        "2. Read every structured file in FULL: use XLSXInfo + XLSXRead for "
        "spreadsheets and DocumentRead for XML/CSV — read all rows/sheets, not a "
        "sampled excerpt.\n"
        "3. Perform any cross-file join, lookup, dedup, or "
        "'find the item that appears only once / under a different name' step "
        "PROGRAMMATICALLY via Bash (python3/awk/grep) over the extracted data. "
        "Normalize keys (case, whitespace, synonyms) explicitly in code, then let "
        "the program report the matched/unmatched rows.\n"
        "4. Base your answer on the program's output, not on a visual scan.\n"
        "</multi_file_join>"
    )


def build_cli_instruction(
    *,
    session_id: str,
    model: str = "",
    workspace_root: str | None = None,
    memory_mode: "MemoryMode | str" = "normal",
    recall_query: str | None = None,
    bot_id: str = "local",
    user_id: str = "local",
    learning_live_readiness: object | None = None,
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

    # Per-turn query-based recall (PR-E item 3): when recall_enabled AND
    # prefer_local_search are on, search the workspace memory tree for the
    # current user message and prepend the top hits as a <memory-recall> block,
    # ALONGSIDE the static <memory-context> snapshot above.  Returns "" when any
    # gate is off, memory_mode is incognito, no workspace, no query, no hits, or
    # any error — so the combined block is byte-identical to pre-wiring when off.
    if recall_query is not None and workspace_root is not None:
        from magi_agent.cli.memory_recall_block import (  # noqa: PLC0415
            build_cli_memory_recall_block,
        )

        _recall_block = build_cli_memory_recall_block(
            workspace_root=workspace_root,
            query=recall_query,
            memory_mode=memory_mode_value,
        )
        if _recall_block:
            # Lead with the query-relevant recall, then the frozen snapshot.
            memory_snapshot_block = "\n\n".join(
                part for part in (_recall_block, memory_snapshot_block) if part
            )

    # 01-PR4 (C2): consult the gated-live learning-recall + write harnesses on
    # the SERVE path. This resolves the unified-rag B1 gap where BOTH
    # build_gated_live_learning_recall_harness AND
    # build_gated_live_learning_write_harness had ZERO serve consumers. Gated by
    # the existing learning-live readiness ladder (MAGI_LEARNING_LIVE_ENABLED +
    # the caller-PROVIDED selected-scope canary readiness config — no net-new
    # flags) and incognito-aware. The real bot_id/user_id are threaded from the
    # serve caller so the canary digest match resolves against the genuine
    # identity, not the literal "local" default. Returns ""/None when the ladder
    # is off (default: learning_live_readiness is None), in shadow mode, with no
    # live binding, or on any error — so the combined block is byte-identical to
    # pre-wiring when off. Appended AFTER the snapshot/recall blocks so it never
    # reorders them.
    if (
        recall_query is not None
        and workspace_root is not None
        and learning_live_readiness is not None
    ):
        from magi_agent.cli.learning_recall import (  # noqa: PLC0415
            build_serve_live_learning_recall_block,
            build_serve_live_learning_write_audit,
        )

        _live_learning_block = build_serve_live_learning_recall_block(
            workspace_root=workspace_root,
            recall_query=recall_query,
            memory_mode=memory_mode_value,
            bot_id=bot_id,
            user_id=user_id,
            readiness=learning_live_readiness,
        )
        # Write symmetry (spec PR4 file-map "write 대칭"): on the live path also
        # run the gated WRITE harness for a symmetric audit record. The audit is
        # observe-only here — every Literal[False] authority flag stays frozen —
        # so it never mutates the prompt; it only proves the write seam is wired
        # (the builder logs the audit dict at debug). Returns None off the live
        # path, keeping prompt assembly byte-identical.
        build_serve_live_learning_write_audit(
            workspace_root=workspace_root,
            memory_mode=memory_mode_value,
            bot_id=bot_id,
            user_id=user_id,
            readiness=learning_live_readiness,
        )
        if _live_learning_block:
            memory_snapshot_block = "\n\n".join(
                part
                for part in (memory_snapshot_block, _live_learning_block)
                if part
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

    # Registry-driven tool advertisement (Principle P2: "built ≠ used").
    # Dynamically reflects which tools are attached — file/browser tools only
    # appear when their env gate is on.  Fail-open: empty string when unavailable.
    _tool_ad_block = build_tool_advertisement_block(workspace_root=workspace_root)

    # File-tool usage guidance — only injected when file tools are actually
    # enabled so the model is not directed to use unavailable tools.
    from magi_agent.config.env import file_tools_enabled  # noqa: PLC0415

    _file_tools_block = ""
    if file_tools_enabled():
        _file_tools_block = (
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
            "</file_tools>"
        )

    # Web-research cross-check guidance — gated on
    # MAGI_RESEARCH_FACT_GUIDANCE_ENABLED AND both provider keys
    # (BRAVE_API_KEY + FIRECRAWL_API_KEY) so the model is never directed to a
    # tool that is not registered. Returns "" when off/unavailable/on error,
    # keeping the default prompt byte-identical (same pattern as
    # _file_tools_block / build_tool_advertisement_block fail-open).
    from magi_agent.tools.web_search_tools import web_research_guidance_block  # noqa: PLC0415

    _web_research_block = web_research_guidance_block()

    # Multi-step decomposition guidance — gated on MAGI_STEP_DECOMPOSITION_ENABLED.
    # Returns "" when off (default), keeping the prompt byte-identical to baseline
    # (same fail-off pattern as _file_tools_block / _web_research_block). The
    # leading "\n\n" is stripped below so the join spacing stays uniform.
    _decomposition_block = step_decomposition_block()

    _skills_block = (
        "<skills>\n"
        "Bundled first-party skills, including superpowers-style workflows, are "
        "available through the SkillLoader tool. Before specialized work such "
        "as debugging, planning, code review, research, writing, or UI work, "
        "load the relevant skill and follow its instructions.\n"
        "</skills>"
        + eval_autonomy_block()
    )

    # Live-SWE-style "creating your own tools" recipe block. Default-OFF
    # (MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED) and frontier-tier gated; returns ""
    # when inactive so prompt assembly stays byte-identical.
    from magi_agent.runtime.tool_synthesis import (  # noqa: PLC0415
        build_tool_synthesis_instruction_block,
    )

    _tool_synthesis_block = build_tool_synthesis_instruction_block(model_label=model)

    # Fable-pattern guidance blocks — default-OFF; each returns "" when
    # inactive so prompt assembly stays byte-identical (same contract as the
    # blocks above).
    from magi_agent.runtime.prompt_guidance import (  # noqa: PLC0415
        action_discipline_examples_block,
        anti_rationalization_block,
        search_decision_block,
    )

    _examples_block = action_discipline_examples_block()
    _search_rules_block = search_decision_block()
    _redflags_block = anti_rationalization_block()

    # Compute-via-code directive (default-OFF: MAGI_COMPUTE_VIA_CODE_ENABLED).
    # Returns "" when the gate is off, so the assembled prompt is byte-identical
    # to pre-wiring. Only appended when non-empty so no extra "\n\n" separator
    # is emitted in the off path (byte-identity guard).
    _compute_block = compute_via_code_block()

    # Output-format-adherence guidance (default-OFF: MAGI_FORMAT_ADHERENCE_ENABLED).
    # Returns "" when the gate is off so the joined prompt is byte-identical to
    # pre-wiring (no <output_format_adherence> marker). General capability — the
    # block carries no benchmark-specific text.
    _format_adherence_block = output_format_adherence_block()

    # Multi-file cross-reference robustness (MAGI_MULTI_FILE_JOIN_ENABLED).
    # Default-OFF separate concatenated string ("" when the flag is unset), so
    # the returned prompt is byte-identical to pre-change behavior off the flag.
    _multi_file_join_block = multi_file_join_block()

    parts = [prompt]
    if _tool_ad_block:
        parts.append(_tool_ad_block)
    if _file_tools_block:
        parts.append(_file_tools_block)
    if _web_research_block:
        parts.append(_web_research_block)
    if _decomposition_block:
        # Strip the leading "\n\n" separator the helper carries (so it composes
        # standalone, like eval_autonomy_block) before appending into the
        # "\n\n".join(parts) assembly — avoids a doubled blank line.
        parts.append(_decomposition_block.lstrip("\n"))
    parts.append(_skills_block)
    if _tool_synthesis_block:
        parts.append(_tool_synthesis_block)
    if _examples_block:
        parts.append(_examples_block)
    if _search_rules_block:
        parts.append(_search_rules_block)
    if _redflags_block:
        parts.append(_redflags_block)
    if _compute_block:
        parts.append(_compute_block)
    if _format_adherence_block:
        parts.append(_format_adherence_block)
    if _multi_file_join_block:
        parts.append(_multi_file_join_block)
    return "\n\n".join(parts)


__all__ = [
    "CLI_BOT_ID",
    "CLI_USER_ID",
    "CliToolRuntime",
    "build_cli_adk_tools",
    "build_cli_instruction",
    "build_cli_tool_runtime",
    "build_tool_advertisement_block",
    "compute_via_code_block",
    "multi_file_join_block",
    "bind_cli_local_full_tool_handlers",
    "output_format_adherence_block",
    "step_decomposition_block",
    "wrap_cli_adk_tools_with_evidence_collector",
]
