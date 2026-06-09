"""Composition root for the Magi CLI (PR-F1, Stream F).

This is the ONLY place where the landed Streams (A/B/C/D/E) meet. Two public
functions build the complete dependency graph for each surface:

``build_headless_runtime(...)``
    Constructs the headless dependency set: engine (A), permission gate (C),
    command registry (D), session log (B). MUST NOT import ``cli.tui.*``,
    ``cli.render.*``, ``textual``, or ``rich`` at module top or inside the
    function. This function is the cold-start-clean path.

``build_tui_app(...)``
    Constructs everything ``build_headless_runtime`` does PLUS the
    ``ToolRendererRegistry`` and returns a constructed ``MagiTuiApp``. All
    ``textual`` / ``rich`` / ``cli.tui`` / ``cli.render`` imports are LAZY
    (inside the function body) so importing ``cli.wiring`` does NOT pull
    those in for the headless/version paths.

Cold-start discipline
---------------------
``import magi_agent.cli.wiring`` must succeed without importing
``textual``, ``rich``, ``google-adk``, or ``google-genai`` (all of those are
lazy, exactly as ``cli.engine`` and ``cli.session_log`` already guarantee).
Importing ``cli.wiring`` is therefore safe on any cold path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.runtime.session_identity import MemoryMode
    from magi_agent.tools.manifest import RuntimeMode, ToolManifest
    from magi_agent.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Light, import-clean imports only at module top.
# cli.engine / cli.permissions / cli.session_log / cli.commands are all
# already documented as import-clean (no textual / google-adk at top level).
# ---------------------------------------------------------------------------
from magi_agent.cli.commands import (
    build_registry,
    install_discovery,
)
from magi_agent.cli.contracts import CommandRegistry, PromptSink
from magi_agent.cli.engine import (
    MagiEngineDriver,
    RunnerPolicyAssembly,
    build_engine_recovery_policy,
    build_output_continuation_config,
)
from magi_agent.cli.permissions import HeadlessSink, PermissionMode, RulesPermissionGate
from magi_agent.cli.session_log import SessionLog
from magi_agent.composio.config import resolve_composio_config
from magi_agent.composio.mcp import (
    ComposioToolsetBundle,
    attach_composio_toolsets_to_runner,
    build_composio_toolset_bundle,
)

__all__ = [
    "HeadlessRuntime",
    "build_headless_runtime",
    "build_tui_app",
    "local_runner_policy_routing_enabled_from_env",
]

# Guard so `install_discovery()` is called at most once per process.
_discovery_installed = False
_RUNNER_POLICY_ROUTING_ENV = "MAGI_RUNNER_POLICY_ROUTING_ENABLED"


def _ensure_discovery() -> None:
    global _discovery_installed
    if not _discovery_installed:
        install_discovery()
        _discovery_installed = True


def local_runner_policy_routing_enabled_from_env() -> bool:
    raw = os.environ.get(_RUNNER_POLICY_ROUTING_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass
class HeadlessRuntime:
    """Dependency set for the headless path.

    Attributes
    ----------
    engine:
        The ADK-backed :class:`MagiEngineDriver` (or an injected test stub).
    gate:
        The :class:`RulesPermissionGate` wired with the chosen permission mode.
    commands:
        A :class:`CommandRegistry` built from the discovered project commands
        + builtins for ``cwd``.
    session_log:
        An open :class:`SessionLog` scoped to ``(session_id, cwd)``.
    composio:
        Optional Composio MCP toolset bundle, inactive when not configured or
        when optional packages are unavailable.
    general_automation_receipts:
        Local-only GA dispatch receipt ledger store retained by the active
        first-party tool dispatcher, when present.
    local_tool_evidence:
        Local-only tool evidence collector shared by CLI/dashboard tools and
        the engine verifier bus, when present.
    mcp_servers:
        Labels for active MCP servers surfaced in protocol metadata.
    """

    engine: MagiEngineDriver
    gate: RulesPermissionGate
    commands: CommandRegistry
    session_log: SessionLog
    composio: ComposioToolsetBundle
    general_automation_receipts: object | None = None
    local_tool_evidence: object | None = None
    mcp_servers: tuple[str, ...] = ()


class _NullFrameWriter:
    async def write(self, frame: object) -> None:
        del frame


def build_headless_runtime(
    *,
    cwd: str | os.PathLike[str] | None = None,
    permission_mode: PermissionMode = "default",
    session_id: str = "cli-session",
    runner: object | None = None,
    model: str | None = None,
    mode: "RuntimeMode" = "act",
    event_sink: object | None = None,
    prompt_sink: "PromptSink | None" = None,
    runner_policy_routing_enabled: bool | None = None,
    memory_mode: "MemoryMode | str" = "normal",
) -> HeadlessRuntime:
    """Construct the complete headless dependency set.

    Parameters
    ----------
    cwd:
        Working directory for command discovery + session-log path scoping.
        Defaults to ``os.getcwd()``.
    permission_mode:
        ``"default"`` | ``"acceptEdits"`` | ``"bypassPermissions"``.
    session_id:
        Engine + session-log session id.
    runner:
        Optional explicit ADK runner for ``MagiEngineDriver``. Useful for
        tests (inject a mock) or future production callers that pre-build the
        runner before calling here.
    model:
        Reserved for future model-selection wiring; accepted but not yet
        forwarded (no Stream F model plumbing yet).
    mode:
        ``"act"`` (default) exposes the full tool set; ``"plan"`` exposes only
        read-only tools (mutating tools are excluded) for plan-mode turns.

    Returns
    -------
    HeadlessRuntime
        A small dataclass holding the four constructed dependencies.

    Cold-start guarantee
    --------------------
    This function MUST NOT import ``textual`` / ``rich`` / ``cli.tui`` /
    ``cli.render``. All those are TUI-only; the headless path is cold-clean.
    """

    effective_cwd = str(cwd) if cwd is not None else os.getcwd()
    effective_runner = (
        runner
        if runner is not None
        else _build_default_runner(
            cwd=effective_cwd,
            session_id=session_id,
            model=model,
            mode=mode,
            memory_mode=memory_mode,
        )
    )
    composio_bundle, composio_attached = _build_composio_bundle_for_mode(
        effective_runner,
        mode=mode,
    )
    mcp_servers = (
        (composio_bundle.mcp_server_label,)
        if composio_bundle.active and composio_attached
        else ()
    )

    # (A) Engine — MagiEngineDriver lazy-imports ADK only when a turn is
    #     iterated; construction is free/cheap. The genuine error-recovery
    #     retry wrapper is flag-gated from env (MAGI_ERROR_RECOVERY_ENABLED);
    #     ``None`` (the default OFF) leaves streaming byte-for-byte identical.
    if event_sink is None:
        try:
            from magi_agent.observability.runtime_sink import get_active_sink

            event_sink = get_active_sink()
        except Exception:
            event_sink = None
    local_tool_evidence = _local_tool_evidence_collector(effective_runner)
    evidence_collector = (
        None
        if local_tool_evidence is None
        else getattr(local_tool_evidence, "collect_for_turn", None)
    )
    engine = MagiEngineDriver(
        runner=effective_runner,
        recovery=build_engine_recovery_policy(),
        output_continuation=build_output_continuation_config(),
        runner_policy_assembly=_build_runner_policy_assembly(
            runner=effective_runner,
            model=model,
            mode=mode,
        ),
        runner_policy_routing_enabled=runner_policy_routing_enabled,
        event_sink=event_sink,
        evidence_collector=evidence_collector if callable(evidence_collector) else None,
    )

    # (C) Permission gate — default stays sink-less and therefore fail-safe on
    #     asks. The explicit bypass mode gets a no-op sink that resolves asks to
    #     allow; dispatcher/toolhost hard-safety still runs after the ADK gate.
    # When an external prompt_sink is supplied (e.g. the SSE streaming seam),
    # include it in the sinks list so the gate races it for "ask" verdicts.
    gate_sinks = (
        [HeadlessSink(_NullFrameWriter(), permission_mode=permission_mode)]
        if permission_mode == "bypassPermissions"
        else []
    )
    # prompt_sink drives gate prompting in non-bypass modes; bypass keeps its own no-frame sink.
    if prompt_sink is not None and permission_mode != "bypassPermissions":
        gate_sinks = [prompt_sink]
    # Read-only auto-allow (CC/OpenCode parity): every non-bypass mode gets the
    # manifest-first classifier so genuinely read-only tools (FileRead/Glob/Grep)
    # run WITHOUT a prompt, while mutating/dangerous tools (FileWrite/FileEdit/
    # PatchApply/Bash) still fall through to ``ask``. Without this the default
    # mode sends EVERY tool to ``ask`` → headless safe-denies them and the agent
    # reports "tools restricted". The LLM classification path (for unknown tools)
    # is reserved for the explicit ``smartApprove`` mode; other modes are
    # manifest-only (deterministic, no provider calls). ``bypassPermissions``
    # already auto-allows via its sink, so it needs no classifier.
    smart_approve = (
        None
        if permission_mode == "bypassPermissions"
        else _build_smart_approve_classifier(
            model=model, mode=mode, use_llm=(permission_mode == "smartApprove")
        )
    )
    gate = RulesPermissionGate(sinks=gate_sinks, smart_approve=smart_approve)

    # (D) Command registry — install discovery once (idempotent), then build
    #     the per-cwd registry.
    _ensure_discovery()
    commands = build_registry(effective_cwd)

    # (B) Session log — scoped to (session_id, cwd); never written until the
    #     first ``append`` call (lazy file creation).
    session_log = SessionLog(session_id=session_id, cwd=effective_cwd)

    return HeadlessRuntime(
        engine=engine,
        gate=gate,
        commands=commands,
        session_log=session_log,
        composio=composio_bundle,
        general_automation_receipts=getattr(
            effective_runner,
            "general_automation_receipts",
            None,
        ),
        local_tool_evidence=local_tool_evidence,
        mcp_servers=mcp_servers,
    )


def _build_runner_policy_assembly(
    *,
    runner: object,
    model: str | None,
    mode: "RuntimeMode",
) -> RunnerPolicyAssembly | None:
    assembly = getattr(runner, "runner_policy_assembly", None)
    if isinstance(assembly, RunnerPolicyAssembly):
        return assembly
    if mode == "plan":
        return None
    provider = getattr(runner, "model_provider", None)
    label = getattr(runner, "model_label", None)
    if not isinstance(provider, str) or not provider.strip():
        provider = "local"
    if provider == "local":
        return None
    if not isinstance(label, str) or not label.strip():
        label = model.strip() if isinstance(model, str) and model.strip() else "local-stub"
    try:
        from magi_agent.cli.real_runner import (  # noqa: PLC0415
            _build_default_runner_policy_assembly,
        )
    except Exception:
        return None
    return _build_default_runner_policy_assembly(
        model_provider=provider,
        model_label=label,
        live_policy_callback_attached=False,
    )


def _build_smart_approve_classifier(
    *,
    model: str | None,
    mode: "RuntimeMode",
    use_llm: bool = False,
) -> object:
    from magi_agent.cli.readonly_classifier import ReadOnlyClassifier  # noqa: PLC0415

    # Manifest-first always works deterministically for known tools. The LLM
    # path (provider_config) is wired ONLY for the ``smartApprove`` mode so the
    # default mode never makes a provider call just to classify a tool.
    provider_config = None
    if use_llm:
        from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

        provider_config = resolve_provider_config(model_override=model)

    return ReadOnlyClassifier(
        registry=_build_smart_approve_tool_registry(mode=mode),
        provider_config=provider_config,
    )


def _build_smart_approve_tool_registry(
    *,
    mode: "RuntimeMode",
) -> "ToolRegistry | None":
    _ = mode
    if not _first_party_tools_enabled():
        return None

    from magi_agent.runtime.openmagi_runtime import (  # noqa: PLC0415
        _build_core_tool_registry,
        _build_default_plugin_state,
    )

    return _build_core_tool_registry(_build_default_plugin_state())


def _build_default_runner(
    *,
    cwd: str | os.PathLike[str] | None = None,
    session_id: str = "cli-session",
    model: str | None = None,
    mode: "RuntimeMode" = "act",
    memory_mode: "MemoryMode | str" = "normal",
) -> object:
    """Build the CLI's default runner.

    When a model provider is configured (``~/.magi/config.toml`` or a provider
    env key for openai/anthropic/gemini/fireworks), build a real model-backed
    ADK runner. Otherwise fall back to the model-free stub so ``magi`` still
    launches with no configuration.

    ``mode`` selects which tools the agent exposes: ``"plan"`` exposes only
    read-only tools (the act-only mutating tools — FileWrite/FileEdit/PatchApply/
    Bash — are excluded), ``"act"`` exposes the full set.
    """

    from magi_agent.cli.local_runner import build_local_cli_runner  # noqa: PLC0415
    from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

    config = resolve_provider_config(model_override=model)
    if config is None:
        return build_local_cli_runner(model=model)

    from magi_agent.cli.real_runner import (  # noqa: PLC0415
        CliProviderDependencyError,
        build_cli_model_runner,
    )
    from magi_agent.harness.general_automation.live_gate import (  # noqa: PLC0415
        GeneralAutomationReceiptLedgerStore,
    )
    from magi_agent.evidence.local_tool_collector import (  # noqa: PLC0415
        LocalToolEvidenceCollector,
    )

    # Identity is loaded from the SAME cwd used to root the tools.
    workspace_root = str(cwd) if cwd is not None else os.getcwd()
    general_automation_receipts = GeneralAutomationReceiptLedgerStore()
    local_tool_evidence = LocalToolEvidenceCollector(
        general_automation_receipts=general_automation_receipts,
    )
    try:
        return build_cli_model_runner(
            config,
            tools=_build_first_party_adk_tools(
                cwd=cwd,
                session_id=session_id,
                mode=mode,
                memory_mode=memory_mode,
                general_automation_receipts=general_automation_receipts,
                local_tool_evidence_collector=local_tool_evidence,
            ),
            workspace_root=workspace_root,
            memory_mode=memory_mode,
            general_automation_receipts=general_automation_receipts,
            local_tool_evidence_collector=local_tool_evidence,
        )
    except CliProviderDependencyError as exc:
        # Key configured but the provider dependency is missing: keep the CLI
        # usable and surface the actionable install hint as the turn response.
        return build_local_cli_runner(model=model, notice=str(exc))


def _build_first_party_adk_tools(
    *,
    cwd: str | os.PathLike[str] | None,
    session_id: str,
    mode: "RuntimeMode" = "act",
    memory_mode: "MemoryMode | str" = "normal",
    general_automation_receipts: object | None = None,
    local_tool_evidence_collector: object | None = None,
) -> list[object]:
    """Build default first-party local ADK tools for the CLI real runner.

    The OSS CLI should expose Magi's first-party local tools once a real model
    runner is configured. Keep this lazy so importing ``cli.wiring`` stays
    lightweight, and only expose tools with concrete handlers so metadata-only
    surfaces do not appear as broken callable tools.
    """

    if not _first_party_tools_enabled():
        return []

    from magi_agent.adk_bridge.tool_adapter import (  # noqa: PLC0415
        build_adk_function_tools_for_registry,
    )
    from magi_agent.runtime.openmagi_runtime import (  # noqa: PLC0415
        _build_core_tool_registry,
        _build_default_plugin_state,
    )
    from magi_agent.tools.context import ToolContext  # noqa: PLC0415
    from magi_agent.tools.dispatcher import ToolDispatcher  # noqa: PLC0415
    from magi_agent.harness.general_automation.live_gate import (  # noqa: PLC0415
        GeneralAutomationReceiptLedgerStore,
    )
    from magi_agent.cli.tool_runtime import (  # noqa: PLC0415
        bind_cli_local_full_tool_handlers,
        wrap_cli_adk_tools_with_evidence_collector,
    )

    from magi_agent.tools.memory_mode_guard import (  # noqa: PLC0415
        normalize_memory_mode,
    )

    workspace_root = str(cwd) if cwd is not None else os.getcwd()
    memory_mode_value = normalize_memory_mode(memory_mode)
    registry = _build_core_tool_registry(_build_default_plugin_state())
    bind_cli_local_full_tool_handlers(
        registry,
        workspace_root=workspace_root,
        bot_id="local-cli",
        user_id="cli",
    )

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

    receipt_store = (
        general_automation_receipts
        if isinstance(general_automation_receipts, GeneralAutomationReceiptLedgerStore)
        else GeneralAutomationReceiptLedgerStore()
    )
    dispatcher = ToolDispatcher(
        registry,
        general_automation_receipts=receipt_store,
    )
    exposed_tool_names = tuple(
        registration.manifest.name
        for registration in (
            registry.resolve_registration(manifest.name)
            for manifest in registry.list_available(mode=mode)
        )
        if (
            registration is not None
            and registration.handler is not None
            and _cli_tool_allowed_for_mode(registration.manifest, mode=mode)
        )
    )

    def tool_context_factory(adk_tool_context: object) -> ToolContext:
        function_call = _context_lookup(adk_tool_context, "function_call")
        tool_name = _context_lookup(function_call, "name")
        tool_use_id = _context_lookup(function_call, "id")
        turn_id = _tool_context_turn_id(
            adk_tool_context,
            session_id=session_id,
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
        )
        return ToolContext(
            bot_id="local-cli",
            user_id="cli",
            session_id=session_id,
            session_key=session_id,
            turn_id=turn_id,
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
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
            plugin_id=tool_name if isinstance(tool_name, str) else None,
        )

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode=mode,
        tool_context_factory=tool_context_factory,
        attach_enabled=True,
        exposed_tool_names=exposed_tool_names,
    )
    return wrap_cli_adk_tools_with_evidence_collector(
        tools,
        collector=local_tool_evidence_collector,
        session_id=session_id,
    )


def _source_ledger_for_session(
    collector: object | None,
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


def _local_tool_evidence_collector(runner: object) -> object | None:
    collector = getattr(runner, "local_tool_evidence_collector", None)
    collect_for_turn = getattr(collector, "collect_for_turn", None)
    if callable(collect_for_turn):
        return collector
    store = getattr(runner, "general_automation_receipts", None)
    if store is None:
        return None
    try:
        from magi_agent.evidence.local_tool_collector import (  # noqa: PLC0415
            LocalToolEvidenceCollector,
        )
    except Exception:
        return None
    return LocalToolEvidenceCollector(general_automation_receipts=store)


def _context_lookup(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _build_composio_bundle_for_mode(
    runner: object,
    *,
    mode: "RuntimeMode",
) -> tuple[ComposioToolsetBundle, bool]:
    if mode == "plan":
        return (
            ComposioToolsetBundle(
                active=False,
                status="inactive",
                reason="plan_mode",
            ),
            False,
        )

    composio_config = resolve_composio_config(os.environ)
    composio_bundle = build_composio_toolset_bundle(composio_config)
    composio_attached = attach_composio_toolsets_to_runner(
        runner,
        composio_bundle,
    )
    return composio_bundle, composio_attached


def _cli_tool_allowed_for_mode(
    manifest: "ToolManifest",
    *,
    mode: "RuntimeMode",
) -> bool:
    if mode == "act":
        return True

    if manifest.permission not in {"read", "meta"}:
        return False
    if manifest.dangerous or manifest.mutates_workspace:
        return False
    if manifest.side_effect_class != "none":
        return False

    # Some legacy native meta tools are mode-tagged as plan-compatible but
    # actually reserve or mutate runtime state. Treat only readonly meta tools
    # as plan-safe until their manifests grow precise side-effect metadata.
    if manifest.permission == "meta" and manifest.parallel_safety != "readonly":
        return False

    return True


def _tool_context_turn_id(
    adk_tool_context: object,
    *,
    session_id: str,
    tool_use_id: str | None,
) -> str:
    for value in (
        _context_lookup(adk_tool_context, "invocation_id"),
        _context_lookup(_context_lookup(adk_tool_context, "invocation_context"), "invocation_id"),
        _context_lookup(_context_lookup(adk_tool_context, "event"), "invocation_id"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    if tool_use_id:
        return f"tool:{tool_use_id}"
    return "local-turn"


def _first_party_tools_enabled() -> bool:
    raw = os.environ.get("MAGI_FIRST_PARTY_TOOLS_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def build_tui_app(
    *,
    cwd: str | os.PathLike[str] | None = None,
    permission_mode: PermissionMode = "default",
    session_id: str = "cli-session",
    runner: object | None = None,
    model: str | None = None,
    runtime: object | None = None,
    mode: "RuntimeMode" = "act",
    runner_policy_routing_enabled: bool | None = None,
) -> object:
    """Construct and return a fully-wired :class:`MagiTuiApp`.

    All ``textual`` / ``rich`` / ``cli.tui`` / ``cli.render`` imports are
    LAZY (inside this function body) so importing ``cli.wiring`` does NOT
    pull those in for the headless/version paths.

    Parameters
    ----------
    cwd:
        Working directory for command discovery + session-log path scoping.
    permission_mode:
        ``"default"`` | ``"acceptEdits"`` | ``"bypassPermissions"``.
    session_id:
        Session id forwarded to the engine and TUI app.
    runner:
        Optional explicit ADK runner.
    model:
        Reserved for future model-selection wiring.
    runtime:
        Optional runtime object forwarded to ``MagiTuiApp`` (for tests /
        production callers that pre-build a runtime).
    mode:
        ``"act"`` (default) exposes the full tool set; ``"plan"`` exposes only
        read-only tools (mutating tools are excluded) for plan-mode turns.

    Returns
    -------
    MagiTuiApp
        A constructed TUI app ready to ``.run()``.
    """

    # ------------------------------------------------------------------ #
    # ALL textual / rich / cli.tui / cli.render imports are LAZY here.    #
    # ------------------------------------------------------------------ #
    from magi_agent.cli.tui.app import MagiTuiApp  # noqa: PLC0415
    from magi_agent.cli.tui.tool_render import build_tool_renderers  # noqa: PLC0415

    runtime_runner = getattr(runtime, "runner", None) if runtime is not None else None
    effective_runner = runner if runner is not None else runtime_runner

    # Build the shared headless half (engine / gate / commands / log).
    rt = build_headless_runtime(
        cwd=cwd,
        permission_mode=permission_mode,
        session_id=session_id,
        runner=effective_runner,
        model=model,
        mode=mode,
        runner_policy_routing_enabled=runner_policy_routing_enabled,
    )

    renderers = build_tool_renderers()

    app = MagiTuiApp(
        engine=rt.engine,
        gate=rt.gate,
        commands=rt.commands,
        renderers=renderers,
        runtime=runtime,
        session_id=session_id,
        model=model,
        cwd=str(cwd) if cwd is not None else None,
    )

    # FIX 2 (global review): attach the app's TextualSink to the gate so the
    # gate races the TUI sink. build_headless_runtime constructs the gate with
    # an EMPTY ``sinks`` list; without this wiring any tool needing an ``ask``
    # verdict resolves to safe-deny and the ToolUseConfirm modal never appears.
    # Defensive: only when the gate exposes a ``sinks`` list.
    gate_sinks = getattr(rt.gate, "sinks", None)
    if isinstance(gate_sinks, list):
        gate_sinks.append(app.sink)

    return app
