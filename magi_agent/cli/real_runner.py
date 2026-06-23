"""A real, model-backed runner for the local ``magi`` CLI.

:class:`CliModelRunner` wraps a genuine ADK ``Runner`` so it drops into the same
seam the stub :class:`~magi_agent.cli.local_runner.LocalCliRunner` occupies:

* it exposes ``.agent`` (the permission gate attaches a ``before_tool_callback``
  to ``runner.agent``), and
* its ``run_async(**kwargs)`` accepts the adapter's
  ``user_id / session_id / invocation_id / new_message`` kwargs.

Unlike a bare ADK ``Runner``, the wrapper lazily creates the session before the
first turn (``Runner.run_async`` requires an existing session), so the engine and
adapter need no change.

The model is built via ADK's ``LiteLlm`` so all four supported providers
(``openai`` / ``anthropic`` / ``gemini`` / ``fireworks``) share one path. ``LiteLlm``
needs the optional ``litellm`` dependency; if it is missing we raise
:class:`CliProviderDependencyError` with an actionable install hint.
"""

from __future__ import annotations

import os
from collections.abc import Coroutine, Mapping, Sequence
from contextvars import ContextVar, Token
from datetime import datetime
from typing import Any, AsyncGenerator, Callable

from magi_agent.adk_bridge.anthropic_cache_model import build_cache_aware_claude
from magi_agent.cli.engine import RunnerPolicyAssembly
from magi_agent.cli.providers import ProviderConfig
from magi_agent.config.env import is_message_cache_enabled
from magi_agent.runtime.session_identity import MemoryMode

# Type of the model-construction hook (injectable for tests).
ModelFactory = Callable[[ProviderConfig], object]


_DEFAULT_FIRST_PARTY_TASK_PROFILE: dict[str, object] = {
    "taskTypes": [
        "coding",
        "research",
        "web-acquisition",
        "browser-automation",
        "document",
        "office",
        "spreadsheet",
        "mission",
        "scheduled-work",
        "artifact-delivery",
        "telegram",
        "learning",
        "self-improvement",
        "superpowers",
        "workflow",
        "automation",
    ],
}


class CliProviderDependencyError(RuntimeError):
    """A provider is configured but its runtime dependency is not installed."""


class CliModelRunner:
    """Adapter exposing a real ADK ``Runner`` through the CLI runner contract."""

    def __init__(
        self,
        *,
        runner: object,
        agent: object,
        session_service: object,
        app_name: str,
        user_id: str = "cli-user",
        session_id: str = "cli-session",
        model_provider: str | None = None,
        model_label: str | None = None,
        runner_policy_assembly: RunnerPolicyAssembly | None = None,
        general_automation_receipts: object | None = None,
        local_tool_evidence_collector: object | None = None,
    ) -> None:
        self._runner = runner
        self._agent = agent
        self._session_service = session_service
        self._app_name = app_name
        self._default_user_id = user_id
        self._default_session_id = session_id
        self._model_provider = model_provider
        self._model_label = model_label
        self._runner_policy_assembly = runner_policy_assembly
        self._general_automation_receipts = general_automation_receipts
        self._local_tool_evidence_collector = local_tool_evidence_collector

    @property
    def agent(self) -> object:
        return self._agent

    @property
    def model_provider(self) -> str | None:
        return self._model_provider

    @property
    def model_label(self) -> str | None:
        return self._model_label

    @property
    def runner_policy_assembly(self) -> RunnerPolicyAssembly | None:
        return self._runner_policy_assembly

    @property
    def general_automation_receipts(self) -> object | None:
        return self._general_automation_receipts

    @property
    def local_tool_evidence_collector(self) -> object | None:
        return self._local_tool_evidence_collector

    async def run_async(self, **kwargs: object) -> AsyncGenerator[object, None]:
        user_id = _as_str(kwargs.get("user_id"), self._default_user_id)
        session_id = _as_str(kwargs.get("session_id"), self._default_session_id)
        await self._ensure_session(user_id=user_id, session_id=session_id)
        # Stream tokens. The event bridge intentionally keeps the FINAL
        # consolidated text on the (governed) transcript channel only and emits
        # redacted ``partial`` deltas on the public stream — which the CLI
        # surfaces. Without streaming a non-streaming model returns its whole
        # reply as a single final event, so the public stream gets no text and
        # the user sees nothing. Default to SSE streaming so deltas flow.
        if "run_config" not in kwargs:
            kwargs["run_config"] = _default_run_config()
        async for event in self._runner.run_async(**kwargs):  # type: ignore[attr-defined]
            yield event

    async def _ensure_session(self, *, user_id: str, session_id: str) -> None:
        existing = await self._session_service.get_session(  # type: ignore[attr-defined]
            app_name=self._app_name, user_id=user_id, session_id=session_id
        )
        if existing is None:
            await self._session_service.create_session(  # type: ignore[attr-defined]
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )


def build_cli_model_runner(
    config: ProviderConfig,
    *,
    app_name: str = "magi-cli",
    agent_name: str = "magi_cli_agent",
    instruction: str | None = None,
    tools: list[object] | None = None,
    model_factory: ModelFactory | None = None,
    user_id: str = "cli-user",
    session_id: str = "cli-session",
    workspace_root: str | None = None,
    memory_mode: "MemoryMode | str" = "normal",
    recall_query: str | None = None,
    bot_id: str = "local",
    owner_user_id: str = "local",
    learning_live_readiness: object | None = None,
    task_profile: Mapping[str, object] | None = None,
    general_automation_receipts: object | None = None,
    local_tool_evidence_collector: object | None = None,
    self_review_fork_runner: object | None = None,
    self_review_candidate_sink: object | None = None,
    self_review_config: object | None = None,
    self_review_now: datetime | None = None,
    self_review_scheduler: Callable[[Coroutine[Any, Any, None]], None] | None = None,
    pinned_recipe_pack_ids: Sequence[str] = (),
    agent_event_emitter: Callable[..., object] | None = None,
) -> CliModelRunner:
    """Build a real, model-backed CLI runner from a resolved provider config.

    By default the agent is wired with the genuine core tools (FileRead/Write/
    Edit, PatchApply, Glob, Grep, Bash, ...) rooted at ``workspace_root`` (the CLI
    cwd) and the real system prompt. ``tools`` / ``instruction`` may be supplied
    to override these (tests pre-build a fake LLM; production callers rely on the
    defaults).
    """

    from google.adk.agents import Agent  # noqa: PLC0415
    from google.adk.apps.app import App  # noqa: PLC0415
    from google.adk.artifacts import InMemoryArtifactService  # noqa: PLC0415
    from google.adk.memory import InMemoryMemoryService  # noqa: PLC0415
    from google.adk.runners import Runner  # noqa: PLC0415

    from magi_agent.adk_bridge.control_plane import build_default_plugin  # noqa: PLC0415
    from magi_agent.adk_bridge.session_service import (  # noqa: PLC0415
        WorkspaceSessionService,
    )
    from magi_agent.cli.tool_runtime import (  # noqa: PLC0415
        build_cli_adk_tools,
        build_cli_instruction,
    )
    from magi_agent.harness.general_automation.live_gate import (  # noqa: PLC0415
        GeneralAutomationReceiptLedgerStore,
    )
    from magi_agent.evidence.local_tool_collector import (  # noqa: PLC0415
        LocalToolEvidenceCollector,
    )

    build_model = model_factory or _build_litellm_model
    model = build_model(config)
    receipt_store = general_automation_receipts or GeneralAutomationReceiptLedgerStore()
    tool_evidence_collector = (
        local_tool_evidence_collector
        or LocalToolEvidenceCollector(general_automation_receipts=receipt_store)
    )

    effective_workspace_root = workspace_root if workspace_root is not None else os.getcwd()
    effective_tools = (
        tools
        if tools is not None
        else build_cli_adk_tools(
            workspace_root=effective_workspace_root,
            session_id=session_id,
            general_automation_receipts=receipt_store,
            local_tool_evidence_collector=tool_evidence_collector,
            agent_event_emitter=agent_event_emitter,
        )
    )
    effective_instruction = (
        instruction
        if instruction is not None
        else build_cli_instruction(
            session_id=session_id,
            model=config.litellm_model,
            workspace_root=effective_workspace_root,
            memory_mode=memory_mode,
            recall_query=recall_query,
            # Thread REAL identity (issue 3): the learning-live readiness ladder
            # matches the selected-canary digest against these — the previous
            # literal "local" default could only ever target the literal "local"
            # scope, so the live recall/write seam never resolved on the real
            # serve path. ``owner_user_id`` is the bot-OWNER identity used for the
            # canary digest (distinct from the ADK session ``user_id`` above); the
            # serve caller passes runtime.config.bot_id / runtime.config.user_id.
            bot_id=bot_id,
            user_id=owner_user_id,
            learning_live_readiness=learning_live_readiness,
        )
    )

    agent = Agent(
        name=agent_name,
        model=model,
        instruction=effective_instruction,
        tools=list(effective_tools),
    )
    runner_policy_assembly = _build_default_runner_policy_assembly(
        model_provider=config.provider,
        model_label=config.litellm_model,
        live_policy_callback_attached=True,
        task_profile=task_profile,
        pinned_recipe_pack_ids=pinned_recipe_pack_ids,
    )
    _attach_first_party_policy_callback(agent, runner_policy_assembly)
    session_service = WorkspaceSessionService(app_name=app_name)
    # Build the control plane via the shared helper (same as local_runner) so
    # both runners cannot drift. The full runtime profile enables first-party
    # controls by default; safe/minimal profiles or explicit false env values
    # leave the plane present but behaviorally empty.
    plane_plugin = build_default_plugin(
        general_automation_receipts=receipt_store,
        contract_required=_required_deliverable_evidence_from_assembly(
            runner_policy_assembly
        ),
        agent_role="general",
        self_review_fork_runner=self_review_fork_runner,
        self_review_candidate_sink=self_review_candidate_sink,
        self_review_config=self_review_config,
        self_review_now=self_review_now,
        self_review_scheduler=self_review_scheduler,
        # Default-OFF tool-synthesis nudge gate: flag + frontier-tier resolution
        # happen inside build_default_plane; passing the label alone changes
        # nothing while MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED is unset.
        tool_synthesis_model_label=config.litellm_model,
        # Customize after-tool ingestion gate (P4). Empty list (byte-identical)
        # unless the customize custom-rules flags are on; registers after the
        # bundled controls so it only rides on results no other control replaced.
        extra_controls=[
            *_build_customize_after_tool_controls(),
            *_build_dashboard_producer_controls(tool_evidence_collector),
        ],
    )
    app = App(name=_app_identifier(app_name), root_agent=agent, plugins=[plane_plugin])
    runner = Runner(
        app=app,
        app_name=app_name,
        session_service=session_service,
        memory_service=InMemoryMemoryService(),
        artifact_service=InMemoryArtifactService(),
    )
    return CliModelRunner(
        runner=runner,
        agent=agent,
        session_service=session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        model_provider=config.provider,
        model_label=config.litellm_model,
        runner_policy_assembly=runner_policy_assembly,
        general_automation_receipts=receipt_store,
        local_tool_evidence_collector=tool_evidence_collector,
    )


# Transient provider failures (5xx, connection drops, "Server disconnected",
# overloaded) otherwise abort a whole run. litellm retries retryable errors when
# ``num_retries`` is set; ``timeout`` bounds a single hung request.
_DEFAULT_NUM_RETRIES = 4
_DEFAULT_TIMEOUT_S = 600


def _model_retry_kwargs(env: Mapping[str, str] | None = None) -> dict[str, int]:
    """LiteLlm retry/timeout kwargs (E-15: registry-backed).

    Knobs come from the typed flag registry (``flag_int``); each falls back
    to its registered default when unset/invalid. Non-positive values
    (``0``/negative) also fall back to the default rather than producing a
    ``num_retries=0`` build that retries zero times — preserving the
    pre-E-15 parse semantics byte-identically.
    """

    from magi_agent.config.flags import flag_int  # noqa: PLC0415

    source = os.environ if env is None else env

    def _positive(name: str, default: int) -> int:
        value = flag_int(name, env=source)
        if not isinstance(value, int) or value < 1:
            return default
        return value

    return {
        "num_retries": _positive("MAGI_MODEL_NUM_RETRIES", _DEFAULT_NUM_RETRIES),
        "timeout": _positive("MAGI_MODEL_TIMEOUT_S", _DEFAULT_TIMEOUT_S),
    }


# Per-provider normalization of a literal ``reasoning_effort`` value before it
# reaches litellm. The string ``max`` is provider-specific:
#   * Anthropic accepts ``max`` (litellm maps it to adaptive thinking).
#   * OpenAI rejects ``max`` ("Supported values are: 'none', 'low', 'medium',
#     'high', 'xhigh'."); OpenRouter proxies the OpenAI API and rejects likewise.
#   * Gemini rejects ``max`` ("Invalid reasoning effort: max").
# Map ``max`` → the strongest accepted value per provider (``xhigh`` for
# OpenAI/OpenRouter, ``high`` for Gemini) so the lab-overlay default
# (``MAGI_MODEL_REASONING_EFFORT=max``) does not silently break every turn
# (litellm retries 4× then returns BadRequest, surfacing as "최종 답변 텍스트가
# 도착하지 않았습니다" in the dashboard).
_REASONING_EFFORT_VALUE_MAP_BY_PROVIDER: dict[str, dict[str, str]] = {
    "openai": {"max": "xhigh"},
    "openrouter": {"max": "xhigh"},
    "gemini": {"max": "high"},
}

# Providers that reject the ``reasoning_effort`` parameter outright (for ANY
# value). litellm surfaces:
#   "fireworks_ai does not support parameters: ['reasoning_effort'], for
#    model=kimi-k2p6. LiteLLM Retried: 4 times"
# Unlike the per-value map above (which substitutes a supported value), these
# providers don't accept the parameter at all — drop it entirely so the lab
# default (max) AND any per-turn picker value (medium/high/etc) silently
# disappear instead of breaking every Kimi/Minimax turn.
_PROVIDERS_THAT_REJECT_REASONING_EFFORT: frozenset[str] = frozenset({"fireworks"})


def _normalize_reasoning_effort(value: str, provider: str | None) -> str:
    if provider is None:
        return value
    return _REASONING_EFFORT_VALUE_MAP_BY_PROVIDER.get(provider, {}).get(value, value)


# Per-turn reasoning_effort override (PR2c).
#
# The wire protocol carries an optional ``reasoningEffort`` field on the chat
# completions request (set by the dashboard's per-turn picker). The transport
# layer pushes that value into this ContextVar around the turn run; any LiteLlm
# build that happens inside the same async task (the top-level model build AND
# any child/subagent ``build_cli_model_runner`` spawned during the turn)
# observes the override via ``_model_reasoning_kwargs``. Each request runs in
# its own asyncio task with its own context, so the override is request-scoped
# without any explicit propagation through the wiring chain.
#
# Default is ``None`` ⇒ no override ⇒ behavior is byte-identical to the prior
# env-only code path.
_per_turn_reasoning_effort: ContextVar[str | None] = ContextVar(
    "magi_per_turn_reasoning_effort", default=None
)


def _normalize_per_turn_value(value: str | None) -> str | None:
    """Trim + lowercase a per-turn ``reasoning_effort`` payload value.

    Returns ``None`` for ``None`` / empty / explicitly-disabled values so the
    override path falls back to the env path (parity with the env knob, which
    treats those same tokens as "unset" in ``_model_reasoning_kwargs``).
    Normalization is value-shape only — provider-specific value rewrites still
    happen via ``_normalize_reasoning_effort`` at consumption time.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"off", "none", "0", "false", "disable", "disabled"}:
        return None
    return text


def set_per_turn_reasoning_effort(value: str | None) -> Token[str | None]:
    """Set the per-turn ``reasoning_effort`` override for the current async task.

    Returns the ``ContextVar`` reset token; callers MUST pass it back to
    :func:`reset_per_turn_reasoning_effort` (typically in a ``finally`` block)
    to restore the prior value so concurrent or sequential turns do not leak
    state across requests.
    """
    return _per_turn_reasoning_effort.set(_normalize_per_turn_value(value))


def reset_per_turn_reasoning_effort(token: Token[str | None]) -> None:
    """Restore the per-turn reasoning override to its prior value."""
    _per_turn_reasoning_effort.reset(token)


def _model_reasoning_kwargs(
    env: Mapping[str, str] | None = None,
    *,
    provider: str | None = None,
    config: ProviderConfig | None = None,
) -> dict[str, object]:
    """Optional extended-thinking / reasoning kwargs for the LiteLlm build.

    Published frontier coding-benchmark numbers are measured with adaptive
    thinking at high effort and thinking blocks preserved across tool turns
    (the bundled ADK LiteLlm round-trips Anthropic ``thinking_blocks`` with
    signatures). Without these kwargs the runtime benchmarks the model in a
    strictly weaker mode.

    Precedence (highest first):

    * ``MAGI_MODEL_THINKING_TYPE=adaptive`` — send ``thinking={"type":
      "adaptive"}`` directly; highest precedence. Escape hatch for adaptive-only
      models when bypassing litellm's effort mapping.
    * ``MAGI_MODEL_THINKING_BUDGET_TOKENS`` (int > 0) — explicit Anthropic-style
      ``{"type": "enabled", "budget_tokens": N}``. ONLY for models that support
      budgeted thinking (e.g. Sonnet 4.5). Adaptive-only models (Opus 4.7/4.8)
      REJECT this shape with a 400 — use ``MAGI_MODEL_REASONING_EFFORT`` (or
      ``MAGI_MODEL_THINKING_TYPE=adaptive``) for those.
    * Per-turn ContextVar override (chat-completions ``reasoningEffort``).
    * ``MAGI_MODEL_REASONING_EFFORT`` — litellm's cross-provider
      ``reasoning_effort`` (``minimal``/``low``/``medium``/``high``/``xhigh``/
      ``max``); ``off``/``none`` disable. RECOMMENDED knob: litellm maps it
      per-model — adaptive models get ``thinking={"type": "adaptive"}`` plus
      ``output_config.effort``, budget models get an enabled budget. When
      ``provider`` is supplied, values are also normalized per-provider
      (notably ``max`` → ``xhigh`` for OpenAI/OpenRouter, which reject ``max``).
    * Catalog default (E-6, gated by ``MAGI_MODEL_REASONING_DEFAULT_ON``) —
      lowest precedence; sourced from
      :meth:`magi_agent.models.ModelCatalog.reasoning_default` so a fresh
      install benchmarks Opus / GPT-5.5 / Gemini 3.1 Pro the way published
      numbers were measured. Requires ``config`` to know which (provider,
      model) row to consult. Default-OFF for soak — when the flag is OFF
      this function returns ``{}`` from this branch, byte-identical to today.

    ``MAGI_MODEL_REASONING_EFFORT=off`` (or ``none`` / ``0`` / etc.) is an
    explicit disable that returns ``{}`` even when the catalog default would
    otherwise apply — operators retain a hard kill switch.
    """

    from magi_agent.config.flags import flag_int, flag_str  # noqa: PLC0415

    source = os.environ if env is None else env
    thinking_type = (flag_str("MAGI_MODEL_THINKING_TYPE", env=source) or "").strip().lower()
    if thinking_type == "adaptive":
        return {"thinking": {"type": "adaptive"}}
    budget = flag_int("MAGI_MODEL_THINKING_BUDGET_TOKENS", env=source)
    if isinstance(budget, int) and budget > 0:
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    # Per-turn override (ContextVar) wins over env for the current async task.
    # The transport layer sets this from the chat-completions payload's
    # ``reasoningEffort`` field (PR2c). ``_normalize_per_turn_value`` already
    # filtered ``None`` / empty / disabled values, so any truthy value here is
    # an explicit per-turn pick that should beat the env knob.
    override = _per_turn_reasoning_effort.get()
    if override:
        if provider in _PROVIDERS_THAT_REJECT_REASONING_EFFORT:
            return {}
        return {"reasoning_effort": _normalize_reasoning_effort(override, provider)}
    effort_raw = (flag_str("MAGI_MODEL_REASONING_EFFORT", env=source) or "").strip().lower()
    _disable_tokens = {"off", "none", "0", "false", "disable", "disabled"}
    if effort_raw in _disable_tokens:
        # Explicit operator kill switch wins over the catalog default even when
        # the default-on flag is set — preserves a hard escape hatch.
        return {}
    if effort_raw:
        if provider in _PROVIDERS_THAT_REJECT_REASONING_EFFORT:
            return {}
        return {"reasoning_effort": _normalize_reasoning_effort(effort_raw, provider)}
    # E-6: catalog-sourced default reasoning kwargs. Gated default-OFF for soak
    # per AGENTS.md flag-promotion-verification — OFF stays byte-identical to
    # today (no env set + no override + no flag ⇒ ``{}``). Local import keeps
    # the catalog off the cold-start path until the flag flips.
    if config is None:
        return {}
    from magi_agent.config.flags import flag_bool
    from magi_agent.models import ModelCatalog

    if not flag_bool("MAGI_MODEL_REASONING_DEFAULT_ON", env=source):
        return {}
    default_kwargs = dict(
        ModelCatalog.builtin().reasoning_default(config.provider, config.model)
    )
    if not default_kwargs:
        return {}
    # The per-provider reject-list applies to catalog defaults too — fireworks
    # rejects ``reasoning_effort`` at any value, so drop a catalog ``effort``
    # default rather than ship a guaranteed-400 turn. (Catalog records for
    # fireworks already carry ``reasoning_style="none"`` so this is belt and
    # suspenders, but it guards a future authoring slip.)
    if (
        "reasoning_effort" in default_kwargs
        and (provider or config.provider) in _PROVIDERS_THAT_REJECT_REASONING_EFFORT
    ):
        return {}
    return default_kwargs


def _model_api_base_kwargs(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Optional LiteLlm kwargs that route generation through a gateway base URL.

    When ``MAGI_LLM_API_BASE`` is set, every model the runtime builds (the main
    turn and forked child/subagent models, which share this builder) targets that
    base instead of the provider's public endpoint — letting one in-cluster
    api-proxy hold all provider keys, smart-route by model string, and meter
    spend. ``MAGI_LLM_API_KEY`` becomes the litellm ``api_key`` AND an explicit
    auth header (``MAGI_LLM_API_HEADER``, default ``x-api-key``) so OpenAI-prefixed
    models — which would otherwise send ``Authorization: Bearer`` — still present
    the token the gateway checks. Unset ⇒ ``{}`` ⇒ unchanged direct-to-provider.
    """

    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    source = os.environ if env is None else env
    base = (flag_str("MAGI_LLM_API_BASE", env=source) or "").strip()
    if not base:
        return {}
    kwargs: dict[str, object] = {"api_base": base}
    token = (flag_str("MAGI_LLM_API_KEY", env=source) or "").strip()
    if token:
        header = (flag_str("MAGI_LLM_API_HEADER", env=source) or "").strip() or "x-api-key"
        kwargs["api_key"] = token
        kwargs["extra_headers"] = {header: token}
    return kwargs


def _maybe_build_cache_aware_anthropic(
    config: ProviderConfig, env: Mapping[str, str] | None = None
) -> object | None:
    """Cache-aware ADK Anthropic model for the anthropic provider, or None.

    Thin shim around the single E-7 seam
    :func:`magi_agent.runtime.model_factory.maybe_build_cache_aware_anthropic`.
    Kept as a CLI-local function so existing tests that
    ``monkeypatch.setattr(real_runner, "build_cache_aware_claude", ...)`` or
    ``monkeypatch.setattr(real_runner, "is_message_cache_enabled", ...)``
    continue to work — the patches are still consulted because both
    symbols flow through this module's import scope first, then the
    factory reads them via its own bindings.

    The custom-endpoint guard (which is a CLI-only concern — the shadow
    surface never sets ``MAGI_LLM_API_BASE``) is decided here and passed
    to the factory as the ``custom_endpoint`` flag.
    """
    from magi_agent.runtime import model_factory  # noqa: PLC0415

    custom_endpoint = bool(_model_api_base_kwargs(env).get("api_base"))
    return model_factory.maybe_build_cache_aware_anthropic(
        config,
        env=env,
        gate_on_flag=True,
        custom_endpoint=custom_endpoint,
    )


def _build_litellm_model(
    config: ProviderConfig, env: Mapping[str, str] | None = None
) -> object:
    cache_aware = _maybe_build_cache_aware_anthropic(config, env)
    if cache_aware is not None:
        return cache_aware
    try:
        from google.adk.models.lite_llm import LiteLlm  # noqa: PLC0415, F401
    except Exception as exc:  # ImportError or downstream litellm import errors.
        raise CliProviderDependencyError(
            f"Provider '{config.provider}' is configured but the 'litellm' "
            "dependency is not installed. Reinstall magi-agent so its default "
            "runtime dependencies are present."
        ) from exc
    # ADK silently drops the provider's finish_reason when the completion
    # carries no text + no tool calls (lite_llm.py:2418-2445 only finalizes
    # an LlmResponse when ``text or reasoning_parts``). That swallows the
    # signal the operator needs to know WHY anthropic/google returned empty
    # in 100ms (model_id rejection, content_filter, 0-token response, ...).
    # Wrap LiteLlm so a zero-yield stream surfaces a synthetic LlmResponse
    # with ``error_code=EMPTY_PROVIDER_STREAM``; the in-loop child-runner
    # classifier (PR #827) catches it and surfaces a typed
    # ``child_llm_empty_provider_stream`` failure with the model name —
    # actionable on the first repro instead of an opaque empty turn.
    from magi_agent.cli.litellm_empty_observer import (  # noqa: PLC0415
        EmptyProviderStreamObserverLiteLlm,
    )

    # litellm otherwise prints its own "Give Feedback / Get Help" banner and
    # debug info to stdout on errors, which corrupts ``--output text``. Errors
    # are already surfaced through the engine's terminal result.
    try:
        import litellm  # noqa: PLC0415

        litellm.suppress_debug_info = True
    except Exception:  # pragma: no cover - litellm always present alongside LiteLlm
        pass
    api_base_kwargs = _model_api_base_kwargs(env)
    api_key = api_base_kwargs.pop("api_key", config.api_key)
    return EmptyProviderStreamObserverLiteLlm(
        model=config.litellm_model,
        api_key=api_key,
        **_model_retry_kwargs(env),
        **_model_reasoning_kwargs(env, provider=config.provider, config=config),
        **api_base_kwargs,
    )


def _app_identifier(app_name: str) -> str:
    """Coerce ``app_name`` into a valid identifier for ``App.name``.

    ``App`` validates ``name.isidentifier()`` (rejecting hyphens), while the
    runner's visible ``app_name`` may contain them.
    """

    sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in app_name)
    if not sanitized or (not sanitized[0].isalpha() and sanitized[0] != "_"):
        sanitized = f"_{sanitized}"
    return sanitized if sanitized.isidentifier() else "magi_cli_agent"


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _default_run_config() -> object:
    """SSE streaming so the model emits partial token deltas (public stream)."""

    from google.adk.agents.run_config import RunConfig, StreamingMode  # noqa: PLC0415

    return RunConfig(streaming_mode=StreamingMode.SSE)


def _required_deliverable_evidence_from_assembly(
    assembly: RunnerPolicyAssembly | None,
) -> object | None:
    """Map a policy assembly's evidence-requirement labels to RequiredDeliverableEvidence.

    ``assembly.evidence_requirements`` is a tuple of public evidence *labels*
    (e.g. ``"artifact_delivery_ref"``, ``"office_preview"``, ``"source_ledger"``),
    not booleans. Delegates to
    :func:`~magi_agent.harness.general_automation.task_completion.required_deliverable_evidence_from_labels`
    (any label mentioning ``"artifact"`` requires an artifact deliverable
    receipt) so this runner and the engine's flag-gated pre-final deliverable
    gate share one mapping. The former forward-compat ``"snapshot"`` label
    mapping was deleted (A4): no first-party label ever used it and snapshot
    enforcement was removed from the verifier.

    Returns ``None`` when no assembly is available, so the constraint control is
    not registered (byte-identical to ``main``).
    """
    if assembly is None:
        return None
    from magi_agent.harness.general_automation.task_completion import (  # noqa: PLC0415
        required_deliverable_evidence_from_labels,
    )

    labels = tuple(getattr(assembly, "evidence_requirements", ()) or ())
    return required_deliverable_evidence_from_labels(labels)


def _merge_pack_validator_refs(
    base: tuple[str, ...],
    pack_validator_refs: tuple[str, ...],
) -> tuple[str, ...]:
    """Append pack-discovered validator refs to the gate's required set (D7 confirm/route).

    Order-stable, dedup-on-merge. ``base`` (recipe-final-gate validators) keeps its
    position; pack refs are appended. This is the ONLY wiring the live gate needs:
    the comparison in ``cli/engine.py`` already enforces ``required_validators``.
    """
    return tuple(dict.fromkeys((*base, *pack_validator_refs)))


def _loaded_pack_validator_refs() -> tuple[str, ...]:
    """Validator refs from disk-discovered packs (first-party + user).

    Validator refs are STATIC manifest data (``provides`` entries of type
    ``validator``), so they are read directly from the parsed manifests — NO impl
    import. This matters for a safety gate: the previous implementation called
    ``load_packs`` (which lazily imports EVERY enabled pack's impl) and swallowed
    any failure with ``except Exception: return ()``, so a single unrelated pack
    with an import-time error (e.g. a tool pack importing a missing package)
    silently dropped ALL pack validator refs and fail-OPENed the enforcement gate.

    Only manifest discovery/parse is wrapped in a narrow guard so a genuinely
    missing/empty packs tree returns () (byte-identical to pre-Phase-3 behavior);
    an unrelated pack's *import* error can no longer reach here at all.
    """
    try:
        from magi_agent.packs.discovery import (  # noqa: PLC0415
            default_search_bases,
            discover_pack_files,
            load_packs_config,
            resolve_enabled_packs,
        )
    except Exception:
        return ()
    try:
        discovered = discover_pack_files(default_search_bases())
        enabled = resolve_enabled_packs(discovered, load_packs_config())
    except Exception:
        return ()
    refs: list[str] = []
    seen: set[str] = set()
    for disc in enabled:
        for entry in disc.manifest.provides:
            if entry.type == "validator" and entry.ref not in seen:
                seen.add(entry.ref)
                refs.append(entry.ref)
    return tuple(refs)


_CODING_TASK_TYPES_FOR_LOCAL_TRUST = frozenset(
    {"coding", "code", "dev-coding", "developer", "software", "workspace", "file-edit", "patch"}
)


def _profile_has_coding_signal(task_profile: Mapping[str, object]) -> bool:
    """True iff ``taskTypes`` declares at least one coding-scope task type."""
    raw = task_profile.get("taskTypes") or task_profile.get("task_types") or ()
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return False
    for value in raw:
        if isinstance(value, str) and value.strip().lower() in _CODING_TASK_TYPES_FOR_LOCAL_TRUST:
            return True
    return False


def _local_trust_missing_evidence_action(
    materialized_action: str,
    *,
    env: Mapping[str, str] | None = None,
    task_profile: Mapping[str, object] | None = None,
) -> str:
    """Local full-trust enforces authored rules by default (drop hosted staging).

    Hosted runs stage authority — a missing authored evidence requirement only
    *audits*. For OSS local full-trust the author IS the operator, so a missing
    requirement should enforce (``repair_required``) by default. Safe/eval/minimal
    profiles (the same set that gates every other full-runtime feature via
    ``_runtime_profile_default_enabled``) keep the conservative hosted ``audit``
    posture. An explicit ``repair_required`` is never downgraded; any non-``audit``
    materialized action is passed through unchanged (only the conservative hosted
    ``audit`` default is flipped).

    Phase 0 lab fix: when an explicit ``task_profile`` is provided, the upgrade
    only fires for **coding-scope** profiles (any of ``_CODING_TASK_TYPES`` in
    ``taskTypes``). A non-coding profile (e.g. ``chat`` only) keeps the
    conservative audit posture so a chat turn cannot escalate to a hard block
    that triggers the repair-loop preamble. ``task_profile=None`` preserves the
    historic upgrade so existing call sites stay byte-identical.
    """
    from magi_agent.config.env import (  # noqa: PLC0415
        _runtime_profile_default_enabled,
    )

    source = os.environ if env is None else env
    if materialized_action == "repair_required":
        return "repair_required"
    if materialized_action == "audit" and _runtime_profile_default_enabled(source):
        if task_profile is not None and not _profile_has_coding_signal(task_profile):
            return "audit"
        return "repair_required"
    return materialized_action


def _apply_customize_verification(required_validators: list[str]) -> list[str]:
    """Apply persisted Customize verification overrides to the required validators.

    Flag-gated: byte-identical to baseline unless
    ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` is on. For each wired preset
    (``customize.preset_map.PRESET_SEAMS``), the preset's controlled refs are
    removed when it resolves disabled (opt-out of a default-on gate) and ensured
    present when it resolves enabled. Fail-open: any error returns the input
    unchanged so the live gate is never wedged by a bad overrides file.
    """
    from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

    if not flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED"):
        return required_validators
    try:
        from magi_agent.customize.preset_map import PRESET_SEAMS  # noqa: PLC0415
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        result = list(required_validators)
        for preset_id, seam in PRESET_SEAMS.items():
            # Only opt-out seams are assembly-layer refs. opt-in seams are
            # activated at the engine-satisfier layer
            # (customize.runtime_gate.preset_enabled), not here. This pass owns the
            # required_validators list; evidence-kind opt-out seams subtract from
            # required_evidence in _apply_customize_evidence_overrides instead.
            if seam.wiring != "opt_out" or seam.controls_kind != "validator":
                continue
            # opt_out is REMOVE-ONLY: an enabled (default) seam is a no-op because
            # its ref is already contributed by the recipe/pack path for the turns
            # it applies to (e.g. coding turns add verifier:dev-coding:test-evidence
            # in _build_default_runner_policy_assembly). Adding it here would inject
            # the ref into UNRELATED turns (e.g. a non-coding chat answer), so the
            # gate would block on a ref no producer can satisfy. Only an explicit
            # opt-OUT subtracts.
            enabled = policy.resolve_enabled(
                preset_id, default=seam.runtime_default_on
            )
            if not enabled:
                result = [r for r in result if r not in seam.controls_refs]
        # Custom deterministic_ref rules (P1) compile as opt-out adds: an enabled
        # rule REQUIRES its ref in the pre-final gate. Separate flag so it stays
        # byte-identical until explicitly enabled. A rule's ref is added only when
        # its producer is currently active (``what_menu.is_known_ref`` reflects the
        # live config-gated set): a rule persisted while a config-gated producer
        # was ON must go inert — not unconditionally block — once that producer is
        # turned off again. No fake toggles.
        if flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED"):
            from magi_agent.customize.what_menu import is_known_ref  # noqa: PLC0415

            for ref in policy.enabled_deterministic_refs():
                if ref not in result and is_known_ref(ref):
                    result.append(ref)
        # Phase 3 — recipe opt-out: when the user supplies an explicit
        # ``enabled_recipes`` allowlist, drop validator_refs contributed by recipe
        # packs the user did not opt into. Empty allowlist ⇒ no-op (byte-
        # identical). Curated mapping in ``customize.catalog.RECIPE_ID_TO_PACK_IDS``
        # leaves security-critical packs unmapped so a user cannot disable them
        # through this seam.
        disabled_validator_refs, _disabled_evidence_refs = (
            _disabled_recipe_pack_refs(policy)
        )
        if disabled_validator_refs:
            result = [r for r in result if r not in disabled_validator_refs]
        return result
    except Exception:
        return required_validators


def _disabled_recipe_pack_refs(policy: object) -> tuple[frozenset[str], frozenset[str]]:
    """Return (disabled_validator_refs, disabled_evidence_refs) the user opted out via ``enabled_recipes``.

    Allowlist semantics (deliberate, conservative): an empty ``enabled_recipes``
    set means "no user override" → no refs are dropped (byte-identical). When the
    set is non-empty it is an explicit allowlist — recipe ids NOT in the set are
    treated as disabled and their mapped pack's evidence_refs are returned for
    removal from the assembled requirements.

    Curated ``RECIPE_ID_TO_PACK_IDS`` maps the UI label to one or more real
    ``RecipePackManifest`` ids; unmapped UI ids (and security-critical packs
    that are intentionally not mapped) cannot be opted out through this seam.

    Returns ``(frozenset(), frozenset())`` on any error (fail-open).
    """
    try:
        from magi_agent.customize.catalog import (  # noqa: PLC0415
            RECIPES,
            pack_ids_for_recipe,
        )
        from magi_agent.recipes.compiler import PackRegistry  # noqa: PLC0415

        enabled_recipes = getattr(policy, "enabled_recipes", frozenset())
        if not enabled_recipes:
            return (frozenset(), frozenset())
        registry = PackRegistry.with_first_party_packs()
        disabled_validator_refs: set[str] = set()
        disabled_evidence_refs: set[str] = set()
        for recipe in RECIPES:
            rid = recipe.get("id")
            if not isinstance(rid, str) or rid in enabled_recipes:
                continue
            for pack_id in pack_ids_for_recipe(rid):
                try:
                    pack = registry.get(pack_id)
                except Exception:  # noqa: BLE001 — unknown pack ⇒ skip
                    continue
                disabled_validator_refs.update(
                    getattr(pack, "validator_refs", ()) or ()
                )
                disabled_evidence_refs.update(
                    getattr(pack, "evidence_refs", ()) or ()
                )
        return (frozenset(disabled_validator_refs), frozenset(disabled_evidence_refs))
    except Exception:  # noqa: BLE001 — never wedge the assembly build
        return (frozenset(), frozenset())


def _apply_customize_evidence_overrides(required_evidence: list[str]) -> list[str]:
    """Apply evidence-kind opt-out seams to the assembled ``required_evidence``.

    Mirror of the validator opt-out pass in ``_apply_customize_verification`` but
    for ``controls_kind == "evidence"`` seams (e.g. ``deterministic-evidence``,
    which subtracts the dev-coding ``evidence:git-diff`` / ``evidence:test-run``
    requirements when disabled). REMOVE-ONLY: an enabled (default) seam is a
    no-op, so with no overrides the evidence list is byte-identical. Flag-gated by
    ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` and fail-open.
    """
    from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

    if not flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED"):
        return required_evidence
    try:
        from magi_agent.customize.preset_map import PRESET_SEAMS  # noqa: PLC0415
        from magi_agent.customize.store import load_overrides  # noqa: PLC0415
        from magi_agent.customize.verification_policy import (  # noqa: PLC0415
            CustomizeVerificationPolicy,
        )

        policy = CustomizeVerificationPolicy.from_overrides(load_overrides())
        result = list(required_evidence)
        for preset_id, seam in PRESET_SEAMS.items():
            if seam.wiring != "opt_out" or seam.controls_kind != "evidence":
                continue
            if not policy.resolve_enabled(preset_id, default=seam.runtime_default_on):
                result = [r for r in result if r not in seam.controls_refs]
        # Phase 3 — recipe opt-out: same allowlist semantics as the validator
        # pass above. Empty ``enabled_recipes`` ⇒ no-op (byte-identical).
        _disabled_validator_refs, disabled_evidence_refs = (
            _disabled_recipe_pack_refs(policy)
        )
        if disabled_evidence_refs:
            result = [r for r in result if r not in disabled_evidence_refs]
        return result
    except Exception:
        return required_evidence


def _build_customize_after_tool_controls() -> list:
    """After-tool ingestion-gate control for enabled customize ``after_tool_use``
    rules (P4). Returns an EMPTY list (byte-identical control plane) unless
    ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``
    are both set. The LLM sub-mode reuses the P3 criterion model factory (which is
    ``None`` while ``MAGI_EGRESS_GATE_ENABLED`` is off, so ``criterion`` rules stay
    deterministic-only/inert). Fail-soft to ``[]``.
    """
    from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

    if not (
        flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED")
        and flag_profile_bool("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED")
    ):
        return []
    try:
        from magi_agent.cli.wiring import _build_criterion_model_factory  # noqa: PLC0415
        from magi_agent.customize.after_tool_gate import (  # noqa: PLC0415
            CustomizeAfterToolControl,
        )

        return [
            CustomizeAfterToolControl(model_factory=_build_criterion_model_factory())
        ]
    except Exception:
        return []


def _build_dashboard_producer_controls(collector: object) -> list:
    """After-tool deny-on-present producer for dashboard-authored custom checks.

    Returns an EMPTY list (byte-identical control plane) unless
    ``MAGI_DASHBOARD_PACK_AUTHORING_ENABLED`` is set. Registers after the bundled
    controls (and after the customize gate) so it only rides on results no other
    control replaced. Fail-soft to ``[]`` — a failed import or construction can
    never break the runner build.
    """
    from magi_agent.config.env import (  # noqa: PLC0415
        is_dashboard_pack_authoring_enabled,
    )

    if not is_dashboard_pack_authoring_enabled():
        return []
    try:
        from magi_agent.adk_bridge.dashboard_producer_control import (  # noqa: PLC0415
            DashboardProducerControl,
        )

        return [DashboardProducerControl(collector=collector)]
    except Exception:
        return []


def _build_default_runner_policy_assembly(
    *,
    model_provider: str,
    model_label: str,
    live_policy_callback_attached: bool,
    task_profile: Mapping[str, object] | None = None,
    pinned_recipe_pack_ids: Sequence[str] = (),
) -> RunnerPolicyAssembly | None:
    from magi_agent.config.env import parse_evidence_completion_gate_enabled  # noqa: PLC0415

    if not parse_evidence_completion_gate_enabled(os.environ):
        return None

    try:
        from magi_agent.recipes.compiler import (  # noqa: PLC0415
            AgentRecipeCompiler,
            PackRegistry,
            ProfileResolutionRequest,
        )
        from magi_agent.recipes.materializer import RecipeMaterializer  # noqa: PLC0415
    except Exception:
        return None

    materializer_provider, materializer_model = _materializer_model(
        provider=model_provider,
        model_label=model_label,
    )
    effective_task_profile = dict(task_profile or _DEFAULT_FIRST_PARTY_TASK_PROFILE)
    runtime_context: dict[str, object] = {"channel": "cli"}
    # MAGI_FORCE_RECIPE pins which compiler recipe a live CLI turn selects by
    # reusing the compiler's existing explicit-selection path (the same
    # ``explicitRecipeSelection`` block the hosted surface uses). Unset/blank ⇒
    # no key added ⇒ automatic selection is byte-identical to today.
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    forced_recipe = (flag_str("MAGI_FORCE_RECIPE") or "").strip()
    from magi_agent.recipes.kernel_recipe_packs import (  # noqa: PLC0415
        build_runtime_pack_registry,
    )
    _pin_registry = build_runtime_pack_registry()
    from magi_agent.recipes.recipe_routing import (  # noqa: PLC0415
        normalize_pinned_recipe_pack_ids,
    )
    validated_pins = normalize_pinned_recipe_pack_ids(
        pinned_recipe_pack_ids, _pin_registry
    )
    required_refs: list[dict[str, str]] = []
    if forced_recipe:
        required_refs.append({"recipeId": forced_recipe})
    required_refs.extend(
        {"recipeId": pid} for pid in validated_pins if pid != forced_recipe
    )
    if required_refs:
        runtime_context["explicitRecipeSelection"] = {
            "mode": "this_turn",
            "requiredRecipeRefs": required_refs,
            # MAGI_FORCE_RECIPE keeps "only these" (False); a user pin ADDS to
            # auto-selection (True). When both are present, forced dominates.
            "allowAdditionalAutoRecipes": not forced_recipe,
        }
    try:
        snapshot = AgentRecipeCompiler(_pin_registry).compile(
            ProfileResolutionRequest(
                taskProfile=effective_task_profile,
                runtimeContext=runtime_context,
                recipePackConfig={},
            )
        )
        plan = RecipeMaterializer.with_reliability_defaults().materialize(
            snapshot,
            modelProvider=materializer_provider,
            modelLabel=materializer_model,
        )
    except Exception:
        return None

    required_validators = list(plan.final_gate_policy.required_validators)
    if "openmagi.dev-coding" in plan.selected_pack_ids:
        required_validators.append("verifier:dev-coding:test-evidence")
    # D7 confirm/route: a validator authored in any loaded disk pack (first-party
    # bundled or user ~/.magi/packs) reaches the existing required_validators gate
    # the same way the recipe final-gate validators do. Fail-open to () keeps the
    # no-packs path byte-identical to pre-Phase-3 behavior.
    required_validators = list(
        _merge_pack_validator_refs(
            tuple(required_validators),
            _loaded_pack_validator_refs(),
        )
    )
    # Customize verification opt-out/opt-in (flag-gated; no-op when off).
    required_validators = _apply_customize_verification(required_validators)
    required_evidence = _apply_customize_evidence_overrides(
        list(plan.final_gate_policy.required_evidence)
    )
    missing_action = _local_trust_missing_evidence_action(
        plan.final_gate_policy.missing_evidence_action,
        task_profile=effective_task_profile,
    )
    attachment_flags = dict(plan.attachment_flags)
    attachment_flags["livePolicyCallbackAttached"] = live_policy_callback_attached
    return RunnerPolicyAssembly(
        modelProvider=model_provider,
        modelLabel=model_label,
        selectedPackIds=plan.selected_pack_ids,
        evidenceRequirements=tuple(dict.fromkeys(required_evidence)),
        requiredValidators=tuple(dict.fromkeys(required_validators)),
        missingEvidenceAction=missing_action,
        repairPolicy={
            "action": missing_action,
            "source": "recipe-materializer",
            "retryable": missing_action == "repair_required",
        },
        attachmentFlags=attachment_flags,
        taskProfile=effective_task_profile,
        phaseRouting=plan.phase_routing.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        providerIntents=plan.provider_intents,
        toolIntents=plan.tool_intents,
        channelIntents=plan.channel_intents,
        artifactIntents=plan.artifact_intents,
        schedulerIntents=plan.scheduler_intents,
    )


def _attach_first_party_policy_callback(
    agent: object,
    assembly: RunnerPolicyAssembly | None,
    *,
    pack_registry: object | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    if assembly is None:
        return
    original = getattr(agent, "before_tool_callback", None)
    original_as_list = (
        []
        if original is None
        else list(original)
        if isinstance(original, list)
        else [original]
    )

    # Recipe-scoped tool enforcement (HB-3) is gated behind the recipe-routing
    # flag and computed ONCE here so the per-call callback stays cheap. When the
    # flag is OFF this whole block is skipped and the callback path is
    # byte-identical to ``main`` (no recipe import, ``scope`` stays None). The
    # recipe imports are kept local so the OFF path never pulls the recipe stack.
    scope = None
    state_key = ""
    try:
        from magi_agent.config.env import recipe_routing_llm_enabled  # noqa: PLC0415

        if recipe_routing_llm_enabled(env):
            from magi_agent.recipes.kernel_recipe_packs import (  # noqa: PLC0415
                build_runtime_pack_registry,
            )
            from magi_agent.recipes.recipe_routing import (  # noqa: PLC0415
                SELECTED_RECIPE_PACK_IDS_STATE_KEY,
                build_recipe_tool_scope,
            )

            resolved_registry = pack_registry or build_runtime_pack_registry()
            scope = build_recipe_tool_scope(resolved_registry)
            state_key = SELECTED_RECIPE_PACK_IDS_STATE_KEY
    except Exception:
        # Enforcement must never break attachment: a bad import / registry build
        # leaves ``scope`` None so the callback behaves exactly as flag-OFF.
        scope = None

    async def magi_first_party_policy_before_tool(*, tool, args, tool_context=None):
        tool_name = getattr(tool, "name", "tool")
        # Production-authority block FIRST, unchanged.
        if _contains_forbidden_production_authority(args):
            return {
                "status": "blocked",
                "error": "production_authority_denied",
                "tool": tool_name,
                "feedback": "Local OSS first-party policy cannot grant production mutation authority.",
                "runnerPolicyAssembly": assembly.to_public_payload(),
            }
        # Recipe-scoped enforcement (flag ON only). Fail-safe: ANY error → allow.
        if scope is not None:
            try:
                selected = _read_selected_recipe_pack_ids(tool_context, state_key)
                if not scope.is_allowed(tool_name, selected_pack_ids=selected):
                    owners = scope.owning_packs.get(tool_name, ())
                    owner_text = ", ".join(owners) if owners else "the owning recipe"
                    return {
                        "status": "blocked",
                        "error": "recipe_tool_not_selected",
                        "tool": tool_name,
                        "feedback": (
                            f"Tool '{tool_name}' is scoped to recipe(s): {owner_text}. "
                            f"Call select_recipe with one of those pack ids "
                            f"(e.g. {owners[0] if owners else 'the recipe pack id'}) "
                            "before using this tool."
                        ),
                        "runnerPolicyAssembly": assembly.to_public_payload(),
                    }
            except Exception:
                # Enforcement must NEVER raise and NEVER block on its own errors.
                return None
        return None

    agent.before_tool_callback = [
        magi_first_party_policy_before_tool,
        *original_as_list,
    ]


def _read_selected_recipe_pack_ids(
    tool_context: object, state_key: str
) -> tuple[str, ...]:
    """Read accumulated recipe-pack selections from the RAW ADK tool context.

    The before_tool_callback receives ADK's own tool context, which exposes a
    mutable mapping-like ``state`` — the same object ``select_recipe_handler``
    reaches via ``ToolContext.adk_tool_context.state``. Returns ``()`` when no
    state / no selection (restriction inactive). Never raises here; callers also
    guard, but this stays defensive so a missing/odd state degrades to ``()``.
    """
    state = getattr(tool_context, "state", None)
    if state is None or not hasattr(state, "get"):
        return ()
    existing = state.get(state_key)
    if isinstance(existing, (tuple, list)):
        return tuple(str(item) for item in existing)
    return ()


def _contains_forbidden_production_authority(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            if key_text in {
                "productionWriteAllowed",
                "productionBlockEnabled",
                "productionAuthority",
            } and nested is True:
                return True
            if _contains_forbidden_production_authority(nested):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(_contains_forbidden_production_authority(item) for item in value)
    return False


def _materializer_model(*, provider: str, model_label: str) -> tuple[str, str]:
    """Pick the cheap-tier (provider, model) the recipe materializer uses.

    Sourced from the single ``ModelCatalog`` (E-1). Unknown providers fall back
    to ``("google", "<gemini cheap default>")`` so the legacy "else→google"
    behavior is preserved without re-introducing a hand table.
    """
    # ``model_label`` previously normalized for the legacy implementation; the
    # catalog lookup is by canonical provider so the label parsing is unused
    # post-refactor. Kept in the signature for callers that still pass it.
    _ = model_label  # noqa: F841 — preserve signature for callers
    from magi_agent.models.catalog import ModelCatalog, UnknownModelError  # noqa: PLC0415

    catalog = ModelCatalog.builtin()
    try:
        record = catalog.cheap_model_for(provider)
    except UnknownModelError:
        # Preserve the legacy "else: google fallback" behavior — the gemini
        # canonical record is reachable via the ``google`` alias.
        record = catalog.cheap_model_for("google")
        return "google", record.model
    # Legacy returned ``("google", ...)`` for the gemini cheap fallback even
    # though the catalog canonicalizes to ``gemini``; surface the legacy alias
    # when a caller passed ``provider="google"`` directly so the materializer
    # call sites stay byte-identical.
    if provider == "google":
        return "google", record.model
    return record.provider, record.model


__all__ = [
    "CliModelRunner",
    "CliProviderDependencyError",
    "build_cli_model_runner",
    "set_per_turn_reasoning_effort",
    "reset_per_turn_reasoning_effort",
]
