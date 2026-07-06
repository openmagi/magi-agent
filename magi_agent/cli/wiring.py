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
from collections.abc import Callable, Mapping, Sequence
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
    # Reads through the canonical registry (I-2 PR A) so the truthy
    # convention lives in one place. Default-OFF preserved; any explicit
    # ``"1"/"true"/"yes"/"on"`` enables, unset and unknown values keep OFF.
    from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415

    return flag_bool(_RUNNER_POLICY_ROUTING_ENV)


def _build_user_hook_bus_for_headless(*, workspace_root: str) -> object | None:
    from magi_agent.config.env import is_user_hooks_enabled

    if not is_user_hooks_enabled():
        return None

    from magi_agent.cli.hook_wiring import build_user_hook_bus

    return build_user_hook_bus(workspace_root=workspace_root)


def _build_goal_loop_judge_factory() -> object | None:
    """Default ``goal_loop_judge_factory`` for the engine.

    The engine asks the returned factory for a :class:`JudgeCaller` ONLY when
    PR-B publishes a :class:`GoalLoopPolicy` on the per-turn ContextVar (i.e.
    the user opted into the composer's Goal-mission toggle AND
    ``MAGI_GOAL_LOOP_ENABLED`` is on). Absent that, this factory is never
    invoked and the engine remains byte-identical to pre-PR-C.

    The returned factory resolves the judge's provider/model via the policy's
    explicit ``judge_provider`` / ``judge_model`` overrides when present, else
    falls back to the deployment's resolved provider config — the same key
    discovery the main runner uses, so a fireworks-only bot uses Kimi to judge
    its own turn (no extra key requirement).

    Fail-soft: any error inside the factory (no keys, litellm import fails,
    network blocked) returns ``None`` and the engine emits
    ``goal_loop_judge_unavailable`` + terminates the turn — never crashes.
    """
    # The factory itself is a pure-Python closure; litellm is lazy-imported
    # inside the judge caller so this wiring stays cold-clean.
    def _factory(policy: object) -> object | None:
        import os as _os  # noqa: PLC0415

        try:
            from magi_agent.cli.providers import (  # noqa: PLC0415
                ProviderConfig,
                resolve_provider_config,
            )
        except Exception:
            return None

        policy_provider = getattr(policy, "judge_provider", None)
        policy_model = getattr(policy, "judge_model", None)

        config: ProviderConfig | None = None
        try:
            if policy_provider and policy_model:
                overlay = {**_os.environ, "MAGI_PROVIDER": str(policy_provider)}
                config = resolve_provider_config(
                    model_override=str(policy_model), env=overlay
                )
            else:
                config = resolve_provider_config()
        except Exception:
            config = None
        if config is None or not getattr(config, "api_key", None):
            return None

        async def _caller(prompt: str) -> str:
            import litellm  # noqa: PLC0415

            response = await litellm.acompletion(  # type: ignore[attr-defined]
                model=config.litellm_model,
                api_key=config.api_key,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.0,
            )
            try:
                choice = response.choices[0]
                content = choice.message.content
            except (AttributeError, IndexError, TypeError):
                return ""
            return content or ""

        return _caller

    return _factory


def _build_criterion_model_factory() -> object | None:
    """Model factory for custom llm_criterion rules (P3 pre-final judge).

    None unless BOTH ``MAGI_EGRESS_GATE_ENABLED`` and
    ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`` are set → llm rules stay inert and the
    engine is byte-identical. Reuses the egress critic's provider-resolved
    Haiku-class factory (``resolve_provider_config`` → ``_build_litellm_for_config``).
    Fail-soft to None.
    """
    from magi_agent.config.flags import flag_bool, flag_profile_bool


    if not (
        flag_bool("MAGI_EGRESS_GATE_ENABLED")
        and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
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


def _agent_mode_excluded_tool_names() -> frozenset[str]:
    """Tool names the active agent MODE excludes from the turn's toolset.

    Resolved consistently with the system-prompt block: an explicit per-turn
    selection wins over the operator's stored sticky default. Returns an empty
    set when no mode is active, the mode is unknown, or on any error (fail-soft
    ⇒ byte-identical). A mode may NARROW the toolset via ``exclude`` (inherently
    safe — it can only remove) and WIDEN it via ``include`` within the
    property-based hard-safety cap (see ``_agent_mode_included_tool_names``);
    ``exclude`` wins over ``include`` for the same name.
    """
    try:
        from magi_agent.customize.modes import active_mode_id, get_mode
        from magi_agent.runtime.per_turn_agent_mode_context import (
            current_per_turn_agent_mode,
        )

        mode_id = current_per_turn_agent_mode() or active_mode_id()
        if not mode_id:
            return frozenset()
        mode = get_mode(mode_id)
        if mode is None:
            return frozenset()
        return frozenset(mode.tool_delta.exclude)
    except Exception:
        return frozenset()


# Permission + side-effect classes a mode's ``include`` MAY re-enable. Modelled
# as ALLOWLISTS (not denylists) so a future permission/side-effect class fails
# closed — a mode can only ever widen the toolset toward low-blast-radius tools.
# ``read`` (inspect) and ``write`` (local-workspace mutation) are admitted;
# ``execute`` (Bash/TestRun/PythonExec), ``net`` (outbound egress), ``computer``
# (ComputerTask), and ``meta`` (tool/runtime management — unbounded blast radius)
# are NOT. Side effects are capped at ``none`` / ``local_workspace``; anything
# that spawns a process (``local_process``) or reaches outside the workspace
# (``external`` / ``local_and_external``) is refused. The runtime approval gate
# still governs these at call time, so this cap is defense-in-depth that keeps
# them out of the advertised set entirely rather than the only guard.
_MODE_INCLUDE_ALLOWED_PERMISSIONS: frozenset[str] = frozenset({"read", "write"})
_MODE_INCLUDE_ALLOWED_SIDE_EFFECTS: frozenset[str] = frozenset(
    {"none", "local_workspace"}
)


def _mode_include_allows_manifest(manifest: object, *, mode: "RuntimeMode") -> bool:
    """Property-based hard-safety cap for ``tool_delta.include`` (PR-A).

    A mode may re-enable a default-OFF tool only when it is genuinely
    low-blast-radius: available in the current runtime mode, not ``dangerous``,
    and in the permission + side-effect ALLOWLISTS above. Bash / PythonExec /
    ComputerTask / network-egress / process-spawning / meta tools are refused no
    matter what a mode declares. Fail-CLOSED: a manifest missing an expected
    attribute (or any error) is refused, so an odd/partial manifest — or a tool
    carrying a permission/side-effect class introduced after this code — never
    widens the toolset.
    """
    try:
        if mode not in tuple(getattr(manifest, "available_in_modes", ()) or ()):
            return False
        if bool(getattr(manifest, "dangerous", True)):
            return False
        if getattr(manifest, "permission", "execute") not in _MODE_INCLUDE_ALLOWED_PERMISSIONS:
            return False
        if (
            getattr(manifest, "side_effect_class", "external")
            not in _MODE_INCLUDE_ALLOWED_SIDE_EFFECTS
        ):
            return False
        return True
    except Exception:
        return False


def _agent_mode_included_tool_names(registry: object, *, mode: "RuntimeMode") -> frozenset[str]:
    """Tool names the active agent MODE re-enables for this turn (PR-A).

    Applies the property-based hard-safety cap (:func:`_mode_include_allows_manifest`):
    a mode may only widen the toolset toward low-blast-radius tools; the
    execute/net/computer/dangerous classes are refused. ``exclude`` wins over
    ``include`` for the same name. Only registered tools with a bound handler
    are admitted (an include naming an unknown/handler-less tool is dropped).
    Resolved like the exclude / system-prompt seams (per-turn selection wins
    over the sticky default). Fail-soft empty on any error (byte-identical).
    """
    try:
        from magi_agent.customize.modes import active_mode_id, get_mode
        from magi_agent.runtime.per_turn_agent_mode_context import (
            current_per_turn_agent_mode,
        )

        mode_id = current_per_turn_agent_mode() or active_mode_id()
        if not mode_id:
            return frozenset()
        agent_mode = get_mode(mode_id)
        if agent_mode is None:
            return frozenset()
        excluded = set(agent_mode.tool_delta.exclude)
        allowed: set[str] = set()
        resolve = getattr(registry, "resolve_registration", None)
        if not callable(resolve):
            return frozenset()
        for name in agent_mode.tool_delta.include:
            if name in excluded:
                continue  # exclude wins over include for the same name
            registration = resolve(name)
            if registration is None or getattr(registration, "handler", None) is None:
                continue
            if _mode_include_allows_manifest(registration.manifest, mode=mode):
                allowed.add(name)
        return frozenset(allowed)
    except Exception:
        return frozenset()


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
    agent_event_emitter: Callable[..., object] | None = None,
    session_service_factory: "Callable[[str], object] | None" = None,
    auto_continue_allowed: bool = True,
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
        Optional model override forwarded into the provider config used to
        build the default runner. When set, it reaches
        ``cli.providers.resolve_provider_config(model_override=model)``,
        flowing through ``_build_default_runner`` (and
        ``_build_runner_policy_assembly``) to the per-turn LiteLlm build.
        ``None`` keeps the provider's catalog default. Slug-prefixed forms
        (e.g. ``"anthropic/claude-sonnet-4-6"``) also switch the resolved
        provider.
    mode:
        ``"act"`` (default) exposes the full tool set; ``"plan"`` exposes only
        read-only tools (mutating tools are excluded) for plan-mode turns.
    tools:
        Optional explicit tool list forwarded to ``build_cli_model_runner``
        when building the default runner (i.e. when ``runner`` is ``None``).
        When ``None`` (the default) the full first-party toolset is built as
        normal - behavior is byte-identical to pre-patch callers.  Pass an
        explicit list (including ``[]``) to restrict the toolset; the primary
        use-case is child-agent privilege containment.
    auto_continue_allowed:
        Whether ledger-first auto-continue (SEAM 2 re-invocation, #1329) may be
        enabled for the engine built here. ``True`` (default) = top-level
        (parent) turn: auto-continue follows ``MAGI_GOAL_LOOP_ENABLED``. ``False``
        is set by the child-runner build path so a SpawnAgent child never
        auto-continues / self-checks-goal regardless of the env flag; the child
        must answer its delegated subtask once and return.

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
            agent_event_emitter=agent_event_emitter,
            session_service_factory=session_service_factory,
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
    # WS3 PR3b: evidence-first goal-completion DI. Each value resolves to its
    # byte-identical OFF state when the flags are unset, so the engine is
    # constructed exactly as pre-WS3.
    from magi_agent.config.env import (  # noqa: PLC0415
        is_goal_completion_evidence_first_enabled,
        is_goal_loop_enabled,
        is_plan_ledger_durable_enabled,
        read_goal_required_evidence,
    )

    evidence_first = is_goal_completion_evidence_first_enabled()
    # Ledger-first auto-continue authority (profile-aware default-ON). Resolved
    # here so the engine ctor stays env-pure. When ON, SEAM 2's already-computed
    # "continue" verdict re-invokes (bounded by the measurable-progress gate)
    # instead of degrading to a bare break; OFF keeps the historic behaviour.
    #
    # Child gate (#1329 regression fix): auto-continue is a TOP-LEVEL (parent)
    # turn concern only. A SpawnAgent child is a bounded, single-objective
    # delegated execution the parent orchestrates; it must answer its subtask
    # once and return, NOT self-check-goal / re-invoke. ``auto_continue_allowed``
    # (default True) is forced False by the child-runner build path
    # (``child_runner_live`` governed collector; ``governed_turn._build_runtime``
    # for depth>0), so the env flag ALONE can never re-enable auto-continue for a
    # child. Parent builds keep the default True -> unchanged behaviour.
    auto_continue_enabled = is_goal_loop_enabled() and auto_continue_allowed
    # U5 ambient goal-loop synthesis factory (design 5.1 / 6.3, KD-1). Built ONCE
    # here so the engine ctor stays env-pure. The engine synthesizes an ambient
    # GoalLoopPolicy at the clean break (finish-the-job baseline) ONLY when the
    # toggle published no policy; the factory reads the live env each call. It is
    # ``None`` for the exact configurations where auto-continue is off (safe /
    # eval / explicit flag 0, and SpawnAgent children / depth>0 via
    # ``auto_continue_allowed``), so ambient synthesis is structurally impossible
    # there and those paths are byte-identical to pre-U5.
    from magi_agent.runtime.goal_loop_policy import (  # noqa: PLC0415
        build_ambient_goal_loop_policy,
    )

    ambient_goal_policy_factory: Callable[[str], object | None] | None = (
        (
            lambda objective: build_ambient_goal_loop_policy(
                objective=objective, env=os.environ
            )
        )
        if auto_continue_enabled
        else None
    )
    # plan_ledger_reader reads the durable todo snapshot off the runner-attribute
    # handler set surfaced by PR3a (section 5.1). ``None`` for stub /
    # caller-supplied-tools / child-containment runners and when the durable
    # flag is OFF (degrade-to-OFF, byte-identical).
    plan_ledger_handler_set = (
        _plan_ledger_handler_set(effective_runner)
        if is_plan_ledger_durable_enabled()
        else None
    )
    plan_ledger_reader: Callable[[str], Sequence[object]] | None = (
        plan_ledger_handler_set.snapshot_for
        if plan_ledger_handler_set is not None
        else None
    )
    # Reader 2 (section 4.5): the INDEPENDENT required-evidence reader, gated
    # ONLY on is_goal_completion_evidence_first_enabled, NEVER on the nudge gate,
    # so the SEAM-2 evidence ``pause`` is reachable under the "full" profile.
    goal_required_evidence = (
        read_goal_required_evidence() if evidence_first else ()
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
        #
        # U5 goal_nudge supersession (design 6.5, KD-6): whenever the goal loop
        # resolves ON (profile-aware MAGI_GOAL_LOOP_ENABLED), the unified ambient
        # ladder OWNS the turn, so the legacy per-turn self-check nudge is passed
        # ``None`` and its ``_drive`` branch is structurally dead (no double-drive,
        # and children no longer receive profile-ON nudges). It stays live ONLY as
        # the escape hatch for an operator who explicitly disables the goal loop
        # (``MAGI_GOAL_LOOP_ENABLED=0``) while keeping ``MAGI_GOAL_NUDGE_ENABLED``.
        # Uses the ENV-level master (NOT ``auto_continue_enabled``) so a contained
        # child under a goal-loop-ON deployment still gets ``None`` here.
        goal_nudge=None if is_goal_loop_enabled() else build_goal_nudge_from_env(),
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
        # PR-C goal-loop judge factory (clean-break "is the objective complete?"
        # check). The factory only fires when PR-B published a GoalLoopPolicy
        # for the turn; absent that, this argument is dormant and streaming
        # behavior is byte-identical to pre-PR-C.
        goal_loop_judge_factory=_build_goal_loop_judge_factory(),
        # U5 ambient synthesis DI (design 6.3). ``None`` when auto-continue is off
        # -> the engine never synthesizes an ambient policy and is byte-identical
        # to pre-U5 on every OFF path.
        ambient_goal_policy_factory=ambient_goal_policy_factory,
        # WS3 PR3b: evidence-first goal completion. OFF (default) -> evidence_first
        # False + reader None + required_evidence () -> all three _drive seams are
        # inert and streaming is byte-identical to pre-WS3.
        evidence_first=evidence_first,
        plan_ledger_reader=plan_ledger_reader,
        required_evidence=goal_required_evidence,
        # Ledger-first auto-continue authority. ON (profile-aware default) gives
        # SEAM 2 re-invocation authority; the per-turn composer Goal-mission
        # toggle raises the budget ceiling (read from the intensity ContextVar).
        auto_continue_enabled=auto_continue_enabled,
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
    agent_event_emitter: Callable[..., object] | None = None,
    session_service_factory: "Callable[[str], object] | None" = None,
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
    # WS3 PR3a: capture the per-turn TodoWriteHandlerSet (with its durable
    # ledger sink already attached + restored inside the build below) so it can
    # be threaded onto the runner as an attribute, mirroring
    # ``local_tool_evidence_collector``. Empty (so ``None`` below) when the
    # durable-ledger flag is OFF or when a caller supplies ``tools`` directly
    # (child-agent containment), keeping those paths byte-identical.
    plan_ledger_capture: list[object] = []
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
            agent_event_emitter=agent_event_emitter,
            plan_ledger_capture=plan_ledger_capture,
        )
    )
    plan_ledger_handler_set = plan_ledger_capture[0] if plan_ledger_capture else None
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
            plan_ledger_handler_set=plan_ledger_handler_set,
            pinned_recipe_pack_ids=pinned_recipe_pack_ids,
            agent_event_emitter=agent_event_emitter,
            session_service_factory=session_service_factory,
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
    agent_event_emitter: Callable[..., object] | None = None,
    plan_ledger_capture: list[object] | None = None,
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

    # WS3 PR3a: attach the durable plan-ledger sink to the freshly-bound
    # TodoWriteHandlerSet AND re-seed its in-memory todos from the JSONL last
    # line (the Critical #1 cross-turn restore), on the LIVE per-turn build.
    # ``session_id`` is a parameter and ``workspace_root`` is in local scope, so
    # both identifiers the restore needs are already here. Default-OFF: when the
    # flag is unset this whole block is skipped and the handler set is
    # byte-identical to today (empty ``_todos``, no sink, no JSONL read). The
    # handler set is surfaced to ``_build_default_runner`` via the capture list
    # so it can be threaded onto the runner as an attribute (section 5.1/5.2).
    from magi_agent.config.env import is_plan_ledger_durable_enabled  # noqa: PLC0415

    if is_plan_ledger_durable_enabled():
        from magi_agent.runtime.plan_ledger import PlanLedgerStore  # noqa: PLC0415
        from magi_agent.tools.todo_toolhost import (  # noqa: PLC0415
            get_todo_write_handler_set,
        )

        plan_ledger_handler_set = get_todo_write_handler_set(registry)
        if plan_ledger_handler_set is not None:
            plan_ledger_handler_set.set_ledger_sink(PlanLedgerStore(workspace_root))
            plan_ledger_handler_set.restore_into(session_id)
            if plan_ledger_capture is not None:
                plan_ledger_capture.append(plan_ledger_handler_set)

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

    # Direct Brave/SerpAPI + Firecrawl web tools, routed THROUGH the dispatcher
    # (A-2). Key-gated; keyless installs byte-identical. Replaces the previous
    # bare-FunctionTool append that bypassed URL policy/egress/receipts/redaction.
    from magi_agent.plugins.native.web import (  # noqa: PLC0415
        bind_direct_web_handlers,
        register_direct_web_tools,
    )

    if register_direct_web_tools(registry):
        bind_direct_web_handlers(registry)

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

    dispatcher = ToolDispatcher(
        registry,
        general_automation_receipts=receipt_store,
        first_party_activity_collector=local_tool_evidence_collector,
        first_party_evidence_refs=enabled_first_party_activity_refs(),
    )
    # PR-A: an active agent mode may WIDEN the toolset by re-enabling a
    # default-OFF tool via ``tool_delta.include`` — but only within the
    # property-based hard-safety cap (never execute/net/computer/dangerous).
    # Enable the admitted tools on this per-build registry BEFORE the advertised
    # set is computed so they flow through the normal ``list_available`` path and
    # get real ADK tools built; the exclude filter below still wins for any name
    # in both halves. The registry is freshly built per turn (serve rebuilds with
    # the per-turn ContextVar set), so this mutation is turn-local. Byte-identical
    # when no mode is active / the mode has no admissible includes.
    for _include_name in _agent_mode_included_tool_names(registry, mode=mode):
        try:
            registry.enable(_include_name)
        except Exception:
            pass

    # Only advertise tools that actually have an execution handler bound. A
    # manifest with no handler can never be dispatched, so exposing it would
    # advertise a capability the runtime cannot deliver. (Handler-less catalog
    # manifests were removed in doc 12 PR5 — see tools/catalog.py — so this
    # filter is now a guard rather than a routine drop-list.) The direct web
    # tools (web_search/web_fetch/research_fact) are now registered as
    # dispatcher-backed manifests above (A-2), so they flow through this filter
    # normally — no out-of-dispatcher append, no native-web hiding machinery.
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

    # PR-4d: an active agent mode may NARROW the toolset (exclude default-ON
    # tools — e.g. a review mode = read-only). Exclude-only is inherently safe
    # (a mode can only REMOVE tools, never enable one); include (re-enabling a
    # default-off tool) needs the universal hard-safety cap and is a follow-up.
    # Applied BEFORE the snapshot + spawn cap below so they reflect the narrowed
    # set. Byte-identical when no mode is active / the mode has no exclusions.
    # Resolved once per runtime build: on the serve path each turn rebuilds (the
    # per-turn ContextVar is set before build), so it is effectively per-turn; a
    # persistent TUI would re-narrow only on rebuild (tool binding is build-time).
    _mode_excluded_tools = _agent_mode_excluded_tool_names()
    if _mode_excluded_tools:
        exposed_tool_names = tuple(
            name for name in exposed_tool_names if name not in _mode_excluded_tools
        )

    # Capture parent tool names once at factory-build time (mirrors spawn_depth
    # threading: a stable value known at construction is closed over and threaded
    # into every ToolContext built by this factory).  ``exposed_tool_names`` is
    # the set of tools the parent agent actually advertises — the right source for
    # the tighten-only producer (Task 2B.2).
    parent_tool_names_snapshot: tuple[str, ...] = tuple(sorted(exposed_tool_names))

    # Seam 1b (CLI): when MAGI_MAIN_AGENT_PROFILE=orchestrator, pre-compute the
    # spawn_cap (full bundle names) to close over into every ToolContext so
    # spawned children receive the correct grant ceiling.  When the flag is unset
    # (default), spawn_cap_for_factory is None — byte-identical to before.
    from magi_agent.config.env import main_agent_profile as _main_agent_profile  # noqa: PLC0415
    from magi_agent.runtime.main_agent_profile import (  # noqa: PLC0415
        apply_orchestrator_filter as _apply_filter,
    )

    # The ceiling is derived from ``exposed_tool_names``, which now includes the
    # direct web tools (they are dispatcher-backed manifests in the registry —
    # A-2), so it reflects the complete final tool list. Inert today (spawn_cap
    # has no consumer), but kept consistent for when Seam 4 enforces the ceiling.
    _spawn_cap_for_factory: tuple[str, ...] | None = None
    if _main_agent_profile() == "orchestrator":
        _, _spawn_cap_for_factory = _apply_filter(exposed_tool_names)

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
            spawn_cap=_spawn_cap_for_factory,
            # Local-serve child-lifecycle plumbing (SpawnAgent → dashboard
            # Work pane). Default ``None`` keeps every emit-site in
            # ``subagents.py`` no-op via its ``callable(emitter)`` guard.
            emit_agent_event=agent_event_emitter,
        )

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode=mode,
        tool_context_factory=tool_context_factory,
        attach_enabled=True,
        exposed_tool_names=exposed_tool_names,
    )
    # Seam 1b (CLI): apply orchestrator profile filter AFTER building the full
    # toolset.  When the flag is unset this is a no-op (same list, None cap).
    tools, _ = _apply_orchestrator_profile(tools)
    return wrap_cli_adk_tools_with_evidence_collector(
        tools,
        collector=local_tool_evidence_collector,
        session_id=session_id,
    )


def _apply_orchestrator_profile(
    full_tools: list[object],
    env: Mapping[str, str] | None = None,
) -> tuple[list[object], tuple[str, ...] | None]:
    """Apply the orchestrator main-agent profile filter to a tools list.

    When ``MAGI_MAIN_AGENT_PROFILE`` is unset (default): returns
    ``(full_tools, None)`` — the SAME list object, byte-identical path.

    When ``"orchestrator"``: returns ``(restricted_tools, spawn_cap_names)``
    where ``restricted_tools`` keeps only orchestrator-allowed names (in the
    original order) and ``spawn_cap_names`` is the full bundle tuple — the
    grant ceiling the orchestrator may pass to spawned children.
    """
    from magi_agent.config.env import main_agent_profile  # noqa: PLC0415
    from magi_agent.runtime.main_agent_profile import (  # noqa: PLC0415
        apply_orchestrator_filter,
    )

    if main_agent_profile(env) != "orchestrator":
        return full_tools, None
    full_names = tuple(
        n
        for n in (getattr(t, "name", None) for t in full_tools)
        if isinstance(n, str) and n
    )
    restricted_names, spawn_cap = apply_orchestrator_filter(full_names)
    allowed = frozenset(restricted_names)
    restricted_tools = [t for t in full_tools if getattr(t, "name", None) in allowed]
    return restricted_tools, spawn_cap


def _resolve_first_party_permission_scope(
    tool_name: str | None,
    *,
    registry: object,
    permission_mode: "PermissionMode",
) -> dict[str, object]:
    """Return the ``permission_scope`` for a first-party CLI tool call.

    A-1 fail-closed flip: mode-derived strict scope is now the DEFAULT. The
    scope is derived from ``permission_mode`` + the called tool's manifest via
    :class:`~magi_agent.tools.permission_scope.PermissionScopeResolver`.

    The deprecated rollback hatch ``MAGI_PERMISSION_SCOPE_LEGACY_FULL_TOOLHOST``
    (default OFF) restores the byte-identical legacy ``selected_full_toolhost``
    stamp for one release. Disabling ``MAGI_PERMISSION_SCOPE_FROM_MODE`` without
    that hatch still resolves to the strict builtin scope — never full-toolhost.

    Fail-CLOSED: any error collapses to the least-privilege
    :func:`fail_closed_scope`, NOT the legacy full-toolhost scope.
    """
    from magi_agent.tools.permission_scope import (  # noqa: PLC0415
        LEGACY_FULL_TOOLHOST_SCOPE,
        PermissionScopeResolver,
        fail_closed_scope,
    )

    try:
        from magi_agent.config.env import (  # noqa: PLC0415
            permission_scope_from_mode_enabled,
            permission_scope_legacy_full_toolhost_enabled,
        )

        # Deprecated rollback hatch wins outright (see cli/tool_runtime.py).
        if permission_scope_legacy_full_toolhost_enabled():
            return dict(LEGACY_FULL_TOOLHOST_SCOPE)

        if not permission_scope_from_mode_enabled():
            return fail_closed_scope("mode_derivation_disabled")

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

        return PermissionScopeResolver().resolve(
            permission_mode=permission_mode,
            manifest=manifest,
            channel="cli",
        )
    except Exception:
        return fail_closed_scope("resolver_error")


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


def _plan_ledger_handler_set(runner: object) -> object | None:
    """Read the per-turn ``TodoWriteHandlerSet`` back off the runner.

    Mirrors ``_local_tool_evidence_collector``: the handler set (with its
    durable ledger sink attached + restored) is surfaced as a runner attribute
    by ``build_cli_model_runner`` (Design: WS3 PR3a, section 5.1). Returns
    ``None`` for stub / caller-supplied-tools / child-containment runners and
    when the durable-ledger flag is OFF (degrade-to-OFF, byte-identical).
    """
    return getattr(runner, "plan_ledger_handler_set", None)


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

        # WS9 PR9a-2: resolve the MCP resilience policy (strict default-OFF; only
        # ON when MAGI_MCP_RESILIENCE_ENABLED=1, which no profile sets today) and
        # thread it + a process-wide breaker registry into the guarded toolsets.
        # The breaker is keyed on the bundle's per-endpoint sha256(mcp_url) digest.
        from magi_agent.config.env import parse_mcp_resilience_env  # noqa: PLC0415

        composio_attached = attach_composio_toolsets_through_dispatcher(
            runner,
            composio_bundle,
            arbiter=RuntimePermissionArbiter(),
            mode=mode if mode != "plan" else "act",
            context_factory=_composio_context_factory,
            receipt_ledger=ComposioReceiptLedger(),
            resilience=parse_mcp_resilience_env(os.environ),
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
    # Reads through the canonical registry (I-2 PR A); default-ON preserved.
    # Strict allowlist semantics: unset → True (registry default), explicit
    # ``"1"/"true"/"yes"/"on"`` → True, any other value → False.
    from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415

    return flag_bool("MAGI_FIRST_PARTY_TOOLS_ENABLED")


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
        Optional model override forwarded into the provider config used to
        build the default runner (same plumbing as
        ``build_headless_runtime``). ``None`` keeps the provider's catalog
        default.
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
    # I-4: routed through the typed flag registry. Pre-I-4 strict
    # ``=="1"`` widens to canonical ``flag_bool`` truthy set.
    from magi_agent.config.flags import flag_bool, flag_profile_bool  #  # noqa: PLC0415

    if flag_profile_bool("MAGI_TUI_FILE_MENTIONS"):
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
