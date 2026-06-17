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
from collections.abc import Sequence
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
    build_empty_response_recovery_config,
    build_engine_recovery_policy,
    build_output_continuation_config,
)
from magi_agent.cli.goal_nudge_wiring import build_goal_nudge_from_env
from magi_agent.cli.permissions import HeadlessSink, PermissionMode, RulesPermissionGate
from magi_agent.cli.session_log import SessionLog
from magi_agent.composio.config import resolve_composio_config
from magi_agent.composio.mcp import (
    ComposioToolsetBundle,
    attach_composio_toolsets_through_dispatcher,
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


def _build_user_hook_bus_for_headless(*, workspace_root: str) -> object | None:
    from magi_agent.config.env import is_user_hooks_enabled

    if not is_user_hooks_enabled():
        return None

    from magi_agent.cli.hook_wiring import build_user_hook_bus

    return build_user_hook_bus(workspace_root=workspace_root)


def _build_criterion_model_factory() -> object | None:
    """Model factory for custom llm_criterion rules (P3 pre-final judge).

    None unless BOTH ``MAGI_EGRESS_GATE_ENABLED`` and
    ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` are set → llm rules stay inert and the
    engine is byte-identical. Reuses the egress critic's provider-resolved
    Haiku-class factory (``resolve_provider_config`` → ``_build_litellm_for_config``).
    Fail-soft to None.
    """
    from magi_agent.config.flags import flag_bool

    if not (
        flag_bool("MAGI_EGRESS_GATE_ENABLED")
        and flag_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
    ):
        return None
    try:
        from magi_agent.transport.egress_critic import (
            _production_egress_critic_model_factory,
        )

        return _production_egress_critic_model_factory()
    except Exception:
        return None


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
    recall_query: str | None = None,
    bot_id: str = "local",
    owner_user_id: str = "local",
    learning_live_readiness: object | None = None,
    tools: list[object] | None = None,
    pinned_recipe_pack_ids: Sequence[str] = (),
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
    tools:
        Optional explicit tool list forwarded to ``build_cli_model_runner``
        when building the default runner (i.e. when ``runner`` is ``None``).
        When ``None`` (the default) the full first-party toolset is built as
        normal — behavior is byte-identical to pre-patch callers.  Pass an
        explicit list (including ``[]``) to restrict the toolset; the primary
        use-case is child-agent privilege containment.

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
            recall_query=recall_query,
            bot_id=bot_id,
            owner_user_id=owner_user_id,
            learning_live_readiness=learning_live_readiness,
            permission_mode=permission_mode,
            tools=tools,
            pinned_recipe_pack_ids=pinned_recipe_pack_ids,
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
            from magi_agent.observability.runtime_sink import (
                combine_sinks,
                get_active_sink,
            )
            from magi_agent.observability.transcript import (
                get_active_transcript_sink,
            )

            event_sink = combine_sinks(
                [get_active_sink(), get_active_transcript_sink()]
            )
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
        # R2 (hermes mechanism 3): empty-response recovery + budget grace.
        # Default OFF (MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED, strict truthy
        # opt-in) → build returns None → engine control flow is byte-identical.
        empty_response_recovery=build_empty_response_recovery_config(),
        runner_policy_assembly=_build_runner_policy_assembly(
            runner=effective_runner,
            model=model,
            mode=mode,
        ),
        runner_policy_routing_enabled=runner_policy_routing_enabled,
        event_sink=event_sink,
        evidence_collector=evidence_collector if callable(evidence_collector) else None,
        # PR4 (cluster 03 C4): production goal-nudge wiring. Default OFF
        # (MAGI_GOAL_NUDGE_ENABLED) → build_goal_nudge_from_env returns None →
        # engine streaming is byte-identical to pre-PR4. When ON, a clean stop
        # short of the goal triggers a bounded continuation (default mode "goal").
        goal_nudge=build_goal_nudge_from_env(),
        # PR2 (cluster 11): production user-hook wiring. Default OFF
        # (MAGI_USER_HOOKS_ENABLED) → build_user_hook_bus returns None → engine
        # never attaches the HookBus tool-callback bridge and streaming is
        # byte-identical. When ON (self-host / local CLI only), CC-style
        # ~/.magi/settings.json + <cwd>/.magi/settings.json command hooks are
        # bridged onto the before/after-tool callbacks.
        user_hook_bus=_build_user_hook_bus_for_headless(workspace_root=effective_cwd),
        # P3: LLM criterion judge model for custom llm_criterion rules at
        # pre-final. None unless MAGI_EGRESS_GATE_ENABLED + custom-rules flag →
        # llm rules stay inert (fail-open) and the turn is byte-identical.
        criterion_model_factory=_build_criterion_model_factory(),
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
    recall_query: str | None = None,
    bot_id: str = "local",
    owner_user_id: str = "local",
    learning_live_readiness: object | None = None,
    permission_mode: "PermissionMode" = "default",
    tools: list[object] | None = None,
    pinned_recipe_pack_ids: Sequence[str] = (),
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
    effective_tools = (
        tools
        if tools is not None
        else _build_first_party_adk_tools(
            cwd=cwd,
            session_id=session_id,
            mode=mode,
            memory_mode=memory_mode,
            permission_mode=permission_mode,
            general_automation_receipts=general_automation_receipts,
            local_tool_evidence_collector=local_tool_evidence,
        )
    )
    try:
        return build_cli_model_runner(
            config,
            tools=effective_tools,
            workspace_root=workspace_root,
            memory_mode=memory_mode,
            recall_query=recall_query,
            # Thread REAL identity + the caller-provided learning-live readiness
            # config down to prompt assembly (issue 3 end-to-end). owner_user_id
            # is the bot-owner identity used for the canary digest match (distinct
            # from the ADK session user).
            bot_id=bot_id,
            owner_user_id=owner_user_id,
            learning_live_readiness=learning_live_readiness,
            general_automation_receipts=general_automation_receipts,
            local_tool_evidence_collector=local_tool_evidence,
            pinned_recipe_pack_ids=pinned_recipe_pack_ids,
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
    permission_mode: PermissionMode = "default",
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

    from magi_agent.config.env import browser_tool_enabled  # noqa: PLC0415

    if browser_tool_enabled():
        from magi_agent.browser.autonomous.tool import (  # noqa: PLC0415
            bind_browser_toolhost_handler,
            register_browser_tool_manifest,
        )

        register_browser_tool_manifest(registry)
        bind_browser_toolhost_handler(registry)

    receipt_store = (
        general_automation_receipts
        if isinstance(general_automation_receipts, GeneralAutomationReceiptLedgerStore)
        else GeneralAutomationReceiptLedgerStore()
    )
    # First-party activity capture: thread the bundled producer pack's static
    # refs (computed ONCE here, never per-dispatch) plus the caller-provided
    # ``local_tool_evidence`` collector so this local-dashboard/TUI dispatcher
    # shares the SAME collector the runner built. Returns () when no producer
    # pack is enabled or it is [packs]-disabled, leaving capture inert.
    from magi_agent.evidence.first_party_gate import (  # noqa: PLC0415
        enabled_first_party_activity_refs,
    )
    from magi_agent.tools.web_search_tools import build_web_search_tools  # noqa: PLC0415

    dispatcher = ToolDispatcher(
        registry,
        general_automation_receipts=receipt_store,
        first_party_activity_collector=local_tool_evidence_collector,
        first_party_evidence_refs=enabled_first_party_activity_refs(),
    )
    try:
        direct_web_tools = build_web_search_tools()
    except Exception:
        direct_web_tools = []
    direct_web_replaces_native = bool(direct_web_tools)
    native_web_tool_names = frozenset({"WebSearch", "WebFetch", "web-search", "web_search"})
    # Only advertise tools that actually have an execution handler bound. A
    # manifest with no handler can never be dispatched, so exposing it would
    # advertise a capability the runtime cannot deliver. (Handler-less catalog
    # manifests were removed in doc 12 PR5 — see tools/catalog.py — so this
    # filter is now a guard rather than a routine drop-list.)
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
            and not (
                direct_web_replaces_native
                and registration.manifest.name in native_web_tool_names
            )
        )
    )

    # Capture parent tool names once at factory-build time (mirrors spawn_depth
    # threading: a stable value known at construction is closed over and threaded
    # into every ToolContext built by this factory).  ``exposed_tool_names`` is
    # the set of tools the parent agent actually advertises — the right source for
    # the tighten-only producer (Task 2B.2).
    parent_tool_names_snapshot: tuple[str, ...] = tuple(sorted(exposed_tool_names))

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
            permission_scope=_resolve_first_party_permission_scope(
                tool_name if isinstance(tool_name, str) else None,
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
            tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
            plugin_id=tool_name if isinstance(tool_name, str) else None,
            parent_tool_names=parent_tool_names_snapshot,
        )

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode=mode,
        tool_context_factory=tool_context_factory,
        attach_enabled=True,
        exposed_tool_names=exposed_tool_names,
    )
    if direct_web_tools:
        tools = [*tools, *direct_web_tools]
    return wrap_cli_adk_tools_with_evidence_collector(
        tools,
        collector=local_tool_evidence_collector,
        session_id=session_id,
    )


_LEGACY_FULL_TOOLHOST_SCOPE: dict[str, object] = {
    "mode": "selected_full_toolhost",
    "source": "selected_full_toolhost",
}


def _resolve_first_party_permission_scope(
    tool_name: str | None,
    *,
    registry: object,
    permission_mode: "PermissionMode",
) -> dict[str, object]:
    """Return the ``permission_scope`` for a first-party CLI tool call.

    When ``MAGI_PERMISSION_SCOPE_FROM_MODE`` is OFF (default) this returns the
    legacy hardcoded ``selected_full_toolhost`` scope — byte-identical to the
    pre-PR1 behavior. When ON, the scope is derived from ``permission_mode`` +
    the called tool's manifest via
    :class:`~magi_agent.tools.permission_scope.PermissionScopeResolver`. Fail-open:
    any error collapses back to the legacy scope.
    """
    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            permission_scope_from_mode_enabled,
        )

        if not permission_scope_from_mode_enabled():
            return dict(_LEGACY_FULL_TOOLHOST_SCOPE)

        manifest = None
        if tool_name:
            resolve_registration = getattr(registry, "resolve_registration", None)
            registration = (
                resolve_registration(tool_name) if callable(resolve_registration) else None
            )
            manifest = getattr(registration, "manifest", None) if registration else None

        if manifest is None:
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

    from magi_agent.config.env import composio_dispatch_enforced  # noqa: PLC0415

    if composio_dispatch_enforced(os.environ):
        # Hard-safety enforced path (PR2): route composio MCP calls through the
        # RuntimePermissionArbiter so secret / sealed / workspace-escape
        # arguments are blocked before the MCP body runs, AND record a receipt
        # for every guarded call (the MCP-path analogue of ToolDispatcher
        # appending receipts for native tools).
        from magi_agent.composio.mcp import ComposioReceiptLedger  # noqa: PLC0415
        from magi_agent.tools.context import ToolContext  # noqa: PLC0415
        from magi_agent.tools.safety import (  # noqa: PLC0415
            RuntimePermissionArbiter,
        )

        def _composio_context_factory(**_kwargs: object) -> ToolContext:
            return ToolContext(botId="composio", channel="composio")

        composio_attached = attach_composio_toolsets_through_dispatcher(
            runner,
            composio_bundle,
            arbiter=RuntimePermissionArbiter(),
            mode=mode if mode != "plan" else "act",
            context_factory=_composio_context_factory,
            receipt_ledger=ComposioReceiptLedger(),
        )
        return composio_bundle, composio_attached

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

    # Resolve a DISPLAY model so the topbar/footer show the model the session
    # will actually use (not the raw, possibly-None ``--model`` flag). This is a
    # SECOND, independent ``resolve_provider_config`` read (display-only); it
    # mirrors but does not literally share the runner's resolve in
    # ``_build_default_runner``. Honesty holds because both read the same
    # ``~/.magi/config.toml`` + ``model_override``. When nothing is configured
    # (``resolve_provider_config`` -> ``None``, runner is the model-free stub),
    # ``display_model`` stays ``None`` so the App's ``or "no model"`` fallback
    # remains honest.
    from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

    _display = resolve_provider_config(model_override=model)
    display_model = (_display.model if _display is not None else None) or model

    # Default cwd BEFORE constructing the @-file provider so it never walks
    # ``None`` regardless of caller (belt-and-suspenders: the interactive caller
    # also threads ``cwd=os.getcwd()``). The new capability — typing ``@`` lists
    # workspace files — is behavior-changing, so it is gated behind
    # ``MAGI_TUI_FILE_MENTIONS`` (default OFF, matching the ``MAGI_TUI_*ENABLED``
    # ``== "1"`` opt-in idiom). When OFF, ``@`` stays dead-but-silent (status
    # quo). ``#`` is intentionally left without a provider (no local channel
    # concept). The provider import is LAZY to keep the cold-start contract.
    effective_cwd = str(cwd) if cwd is not None else os.getcwd()
    file_provider: object | None = None
    if os.environ.get("MAGI_TUI_FILE_MENTIONS", "") == "1":
        from magi_agent.cli.tui.file_provider import (  # noqa: PLC0415
            WorkspaceFileProvider,
        )

        file_provider = WorkspaceFileProvider(effective_cwd)

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
        model=display_model,
        mode=mode,
        file_provider=file_provider,
        cwd=str(cwd) if cwd is not None else None,
    )

    # Attach the app's TextualSink to the gate so non-bypass modes can prompt.
    # bypassPermissions already installs a no-frame HeadlessSink; racing the TUI
    # sink there can still push ToolUseConfirm before cancellation reaches it.
    gate_sinks = getattr(rt.gate, "sinks", None)
    if permission_mode != "bypassPermissions" and isinstance(gate_sinks, list):
        gate_sinks.append(app.sink)

    return app
