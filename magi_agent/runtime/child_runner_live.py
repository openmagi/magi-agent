"""A REAL, model-backed local child runner for the Child Runner boundary.

This module supplies :class:`RealLocalChildRunner` — the genuine, model-backed
child-execution surface that ``LocalChildRunnerBoundary``'s *live* branch
(``liveChildRunnerEnabled`` + a trusted ``openmagi_live_provider`` runner)
admits and drives via ``run_child(request)``.

It reuses the existing in-process turn-execution machinery
(``build_cli_model_runner`` / ``CliModelRunner`` from
``magi_agent.cli.real_runner``) to run ONE sub-agent turn — the SAME seam the
GAIA/discovery harnesses reuse (see ``discovery/orchestrator.drive_runner_once``,
the precedent followed here, including the injectable ``model_factory`` test
seam so tests pass a fake ``BaseLlm`` and NO real model call / API key is made).

Default OFF
-----------
The boundary's ``live_child_runner_enabled`` config flag is the authority gate;
this module additionally exposes a parallel call-time env gate
(``is_live_child_runner_enabled``) mirroring ``file_delivery_live`` so a caller
(Task C ``spawn_agent`` wiring) can decide whether to construct/attach a real
runner at all. Default OFF; the kill-switch wins.

Safety
------
* The boundary (Task A) owns spawn-depth / total-agents / output-ref caps; this
  runner just executes one turn.
* v1 scope: TEXT-ONLY child turn — NO workspace-mutating tools are passed
  (an empty toolset). Tool-enabled children are a follow-up.
* ``run_child`` NEVER raises: every failure path returns a degraded mapping
  (``status="blocked"`` / ``"failed"``) that the boundary then sanitises through
  ``_envelope_from_output`` (so no secrets/paths/raw transcript leak).
* Unknown model route (not in ``ModelTierRegistry``) → blocked.
* No provider key resolvable → blocked (``child_provider_key_missing``); the
  runner is NOT executed.

Import-clean by design
----------------------
No module-top imports of ``litellm`` / ``google.adk`` / heavy runner internals.
``build_cli_model_runner`` / ``resolve_provider_config`` are imported lazily
INSIDE the methods so importing this module stays light and the Task C tool
wiring (``subagents.py``) keeps an import-clean surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
from collections.abc import Callable, Mapping
from typing import Any


# ---------------------------------------------------------------------------
# Env-gate constants and helper (mirrors artifacts/file_delivery_live.py)
# ---------------------------------------------------------------------------

LIVE_CHILD_RUNNER_ENABLED_ENV = "MAGI_CHILD_RUNNER_LIVE_ENABLED"
LIVE_CHILD_RUNNER_KILL_SWITCH_ENV = "MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH"

_TRUTHY = {"1", "true", "yes", "on"}


def is_live_child_runner_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return True iff the live child runner is enabled and not kill-switched.

    Evaluated at call time (not import time) so tests can patch ``os.environ``
    without a module reload. Both flags use explicit allowlisting against
    ``_TRUTHY`` (case-insensitive after strip); any other value (including the
    empty string) is treated as false. The kill-switch wins over enabled.

    :param env: Optional explicit env mapping; defaults to ``os.environ``.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    enabled_raw = source.get(LIVE_CHILD_RUNNER_ENABLED_ENV, "")
    kill_raw = source.get(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, "")
    enabled = str(enabled_raw).strip().lower() in _TRUTHY
    killed = str(kill_raw).strip().lower() in _TRUTHY
    return enabled and not killed


# ---------------------------------------------------------------------------
# Default child-route fallback (only used when neither the request nor an
# injected provider_config carries a provider/model).
# ---------------------------------------------------------------------------

_DEFAULT_CHILD_PROVIDER = "anthropic"
_DEFAULT_CHILD_MODEL = "claude-sonnet-4-6"

#: Max chars of final text we forward as the envelope ``summary``. The boundary
#: re-sanitises and re-trims to 512, so this is just a pre-trim guard against
#: pushing a megabyte of text through the seam.
_MAX_SUMMARY_CHARS = 2000

#: Provider-alias normalisation applied BEFORE delegating to
#: ``cli.providers.resolve_provider_config``. The ``ModelTierRegistry`` records
#: the gemini model under the ``"google"`` provider (and ``ChildRunnerConfig``
#: defaults ``child_provider="google"``), but the litellm/provider name in
#: ``cli.providers.SUPPORTED_PROVIDERS`` is ``"gemini"``. Without this alias a
#: default-routed child would be silently blocked (``child_provider_key_missing``)
#: even with a Gemini key present. Tier validation still runs against the
#: registry's OWN provider name (unaliased) so the vetted route is unchanged.
_PROVIDER_ALIAS: dict[str, str] = {"google": "gemini"}

#: Minimal child instruction so a TEXT-ONLY (tools=[]) child is NOT handed the
#: full filesystem-tool system prompt that ``build_cli_model_runner`` would
#: otherwise synthesise for a tool-enabled agent.
_CHILD_INSTRUCTION = "Complete the following delegated subtask. Respond with the answer only."

# Degrade-reason tokens (fixed, non-leaking). Used by the degrade returns below
# and referenced by tests, so they live as module constants in ONE place.
_DEGRADE_ROUTE_UNKNOWN = "child_model_route_unknown"
_DEGRADE_KEY_MISSING = "child_provider_key_missing"
_DEGRADE_TURN_ERROR = "child_turn_error"
_DEGRADE_TIMEOUT = "child_turn_timeout"


#: Hard ceiling for a single child turn (seconds), regardless of the request's
#: ``budget_ms``. Keeps a runaway/huge budget from blocking indefinitely.
_MAX_TURN_TIMEOUT_S = 600.0


class RealLocalChildRunner:
    """REAL, model-backed local child runner driving ONE sub-agent turn.

    Satisfies the boundary's live contract:
      * ``openmagi_live_provider = True`` (trusted-live marker), and
      * ``async def run_child(request) -> Mapping`` returning the output keys
        ``_envelope_from_output`` consumes (``childExecutionId``, ``status``,
        ``summary``, ``evidenceRefs``, ``artifactRefs``, ``auditEventRefs``).

    The genuine model runner is built via ``build_cli_model_runner`` (text-only
    toolset). For tests, an injected ``model_factory`` (a ``ProviderConfig ->
    BaseLlm`` callable yielding canned events) OR a fully-injected ``runner``
    (anything exposing ``run_async(**kwargs)``) avoids any network/API key.
    """

    openmagi_live_provider = True

    def __init__(
        self,
        *,
        provider_config: object | None = None,
        model_factory: Callable[[object], object] | None = None,
        runner: object | None = None,
        tools: list[object] | None = None,
        toolset_profile: str = "none",
        evidence_collector: object | None = None,
        workspace_root: str | None = None,
        progress_sink: Callable[[Mapping[str, object]], object] | None = None,
        env: Mapping[str, str] | None = None,
        spawn_cap: tuple[str, ...] | None = None,
    ) -> None:
        #: Optional pre-resolved provider config (a ``ProviderConfig``). When
        #: supplied AND it carries a key, it short-circuits key resolution.
        self._provider_config = provider_config
        #: Test seam: a ``ProviderConfig -> BaseLlm`` factory. Forwarded to
        #: ``build_cli_model_runner`` so tests inject a fake LLM (no network).
        self._model_factory = model_factory
        #: Test seam: a fully pre-built runner (exposing ``run_async``). When
        #: supplied it is used directly, bypassing ``build_cli_model_runner``.
        self._injected_runner = runner
        #: Explicit caller-supplied toolset override. When ``None`` the toolset
        #: is derived from ``toolset_profile`` (PR1); an EMPTY/``none`` profile
        #: keeps the historical text-only (``tools=[]``) behaviour byte-for-byte.
        self._tools: list[object] | None = list(tools) if tools is not None else None
        #: PR1 (doc 07): the resolved toolset profile — ``"none"`` (default,
        #: text-only, byte-identical to v1), ``"readonly"`` (FileRead/Glob/Grep/
        #: GitDiff only), or ``"full"`` (whole core toolset; gated upstream by
        #: doc 09 permissions). The profile drives toolset construction inside
        #: ``_collect_turn_text`` ONLY when no toolset/runner is injected.
        self._toolset_profile = toolset_profile
        #: PR1: optional tool-call evidence collector. When supplied it is wired
        #: into the built toolset so each tool-call records a public
        #: ``evidence:`` ref that is promoted onto the child's ``evidenceRefs``.
        self._evidence_collector = evidence_collector
        self._workspace_root = workspace_root
        self._progress_sink = progress_sink
        self._env: Mapping[str, str] = os.environ if env is None else env
        #: Orchestrator-imposed tool-name ceiling (Seam 2b). Stored for a future
        #: task (Seam 4) that will intersect the child's toolset against it.
        #: ``None`` means no ceiling — default behaviour is byte-identical.
        self._spawn_cap: tuple[str, ...] | None = spawn_cap

    async def run_child(self, request: object) -> Mapping[str, object]:
        """Drive ONE model-backed child turn; NEVER raise.

        Returns a mapping with exactly the keys ``_envelope_from_output``
        consumes. Any failure (unknown route, missing key, model/turn error)
        degrades to a ``blocked``/``failed`` mapping with a clear, non-leaking
        reason; the boundary re-sanitises the output.
        """
        child_execution_id = self._child_execution_id(request)
        try:
            # --- Resolve + validate the child's model route -------------------
            provider, model = self._resolve_route(request)
            route = self._validate_route(provider, model)
            if route is None:
                return self._blocked(
                    child_execution_id,
                    reason=_DEGRADE_ROUTE_UNKNOWN,
                )

            # Thread the VALIDATED/normalised route (canonical casefolded
            # provider/model from the registry) into provider-config resolution
            # and the litellm re-pin, so the vetted route and the litellm route
            # string always agree (no mixed-case drift).
            route_provider = _clean_str(getattr(route, "provider", None)) or provider
            route_model = _clean_str(getattr(route, "model", None)) or model

            # --- Resolve the provider key (degrade if absent) -----------------
            config = self._resolve_provider_config(route_provider, route_model)
            if config is None:
                return self._blocked(
                    child_execution_id,
                    reason=_DEGRADE_KEY_MISSING,
                )

            # --- Drive ONE turn and collect the final text + evidence ---------
            final_text, evidence_refs = await self._drive_one_turn(config, request)
        except asyncio.TimeoutError:
            # Hung/slow model exceeded the turn budget — degrade (never raise).
            return self._failed(
                child_execution_id,
                reason=_DEGRADE_TIMEOUT,
            )
        except asyncio.CancelledError:
            # Cooperative cancellation MUST propagate — never convert it to a
            # failed mapping (it is BaseException in 3.11 so the broad ``except
            # Exception`` below won't catch it; this is explicit for robustness).
            raise
        except Exception:  # noqa: BLE001 — NEVER raise across the seam.
            return self._failed(
                child_execution_id,
                reason=_DEGRADE_TURN_ERROR,
            )

        summary = (final_text or "").strip()[:_MAX_SUMMARY_CHARS]
        return {
            "childExecutionId": child_execution_id,
            "status": "completed",
            # PR1: tool-call receipts collected during the turn are promoted to
            # the child's evidenceRefs (empty when text-only / no toolset).
            "evidenceRefs": evidence_refs,
            "summary": summary,
            "artifactRefs": (),
            "auditEventRefs": (),
        }

    # ------------------------------------------------------------------ #
    # Route resolution / validation                                       #
    # ------------------------------------------------------------------ #

    def _resolve_route(self, request: object) -> tuple[str, str]:
        """A per-task override wins, then an injected provider_config, then the
        historical default child route."""
        req_provider = _clean_str(getattr(request, "provider", None))
        req_model = _clean_str(getattr(request, "model", None))
        cfg_provider = _clean_str(getattr(self._provider_config, "provider", None))
        cfg_model = _clean_str(getattr(self._provider_config, "model", None))
        provider = req_provider or cfg_provider or _DEFAULT_CHILD_PROVIDER
        model = req_model or cfg_model or _DEFAULT_CHILD_MODEL
        return provider, model

    def _validate_route(self, provider: str, model: str) -> object | None:
        """Accept the route via the canonical ``resolve_child_route`` authority.

        Delegates to :func:`magi_agent.runtime.model_tiers.resolve_child_route`,
        the SINGLE source the route-listing (SpawnAgent guidance / system-prompt
        block via ``available_child_model_routes``) is also bound to — so what the
        model is told it can use can never drift from what the runner accepts. A
        route is accepted iff it resolves in the built-in registry (returned
        normalised) OR is in the operator deployment allowlist; else ``None`` and
        the caller blocks.
        """
        from magi_agent.runtime.model_tiers import (  # noqa: PLC0415
            resolve_child_route,
        )

        return resolve_child_route(provider, model, os.environ)

    def _resolve_provider_config(self, provider: str, model: str) -> object | None:
        """Return a ``ProviderConfig`` with a usable key, or ``None``.

        ``provider``/``model`` here are the VALIDATED/normalised route from the
        ``ModelTierRegistry`` (canonical casefolded). The registry records the
        gemini model under ``"google"`` while ``cli.providers`` knows it as
        ``"gemini"``; we normalise via ``_PROVIDER_ALIAS`` at THIS seam so a
        default-routed (``provider="google"``) child resolves a Gemini key
        instead of being silently blocked. Tier validation upstream still ran
        against the registry's own (unaliased) provider name.

        Prefers an injected ``provider_config`` that already carries a key
        (tests / explicit callers). Otherwise delegates to
        ``resolve_provider_config`` (config file + env). NO key → ``None``
        (the caller degrades to blocked; never crashes).
        """
        # Map the registry-name provider to the litellm/provider name used by
        # ``cli.providers`` (e.g. ``"google"`` -> ``"gemini"``).
        provider_key = _PROVIDER_ALIAS.get(provider, provider)

        injected_key = _clean_str(getattr(self._provider_config, "api_key", None))
        injected_provider = _clean_str(getattr(self._provider_config, "provider", None))
        # An injected config may carry either the registry name or the litellm
        # name; accept a match against either form.
        if injected_key and injected_provider in {provider, provider_key}:
            return self._provider_config

        from magi_agent.cli.providers import (  # noqa: PLC0415
            ProviderConfig,
            SUPPORTED_PROVIDERS,
            UnknownProviderError,
            resolve_provider_config,
        )

        # ``resolve_provider_config`` honours MAGI_PROVIDER/config; force the
        # child's chosen provider via an env overlay so the resolved key matches
        # the route we validated.
        overlay = dict(self._env)
        overlay["MAGI_PROVIDER"] = provider_key
        try:
            resolved = resolve_provider_config(model_override=model, env=overlay)
        except UnknownProviderError:
            return None
        if resolved is None:
            return None
        # ``resolve_provider_config`` uses provider-default models when no
        # override resolves; re-pin the validated model + ensure the supported
        # provider so the litellm route is exactly what we vetted.
        if resolved.provider not in SUPPORTED_PROVIDERS:
            return None
        return ProviderConfig(
            provider=resolved.provider,
            model=model,
            api_key=resolved.api_key,
        )

    # ------------------------------------------------------------------ #
    # Turn drive (mirrors discovery/orchestrator.drive_runner_once)       #
    # ------------------------------------------------------------------ #

    async def _drive_one_turn(self, config: object, request: object) -> tuple[str, tuple[str, ...]]:
        """Build/reuse a ``CliModelRunner`` and drive ONE turn.

        Returns ``(final_text, evidence_refs)`` — the collected tool-call
        receipt refs (``evidence:...``) are empty for a text-only child.

        Heavy ADK imports are LOCAL so importing this module never triggers
        them. Mirrors the discovery orchestrator's message construction +
        event-text collection.

        The turn is ALWAYS bounded (by ``request.budget_ms`` when set, else the
        default ceiling); on expiry ``asyncio.wait_for`` raises
        ``asyncio.TimeoutError`` which the caller maps to a degraded
        ``child_turn_timeout`` result. ``asyncio.CancelledError`` is NEVER
        swallowed — it propagates.
        """
        return await asyncio.wait_for(
            self._collect_turn_text(config, request),
            timeout=self._turn_timeout_s(request),
        )

    async def _collect_turn_text(
        self, config: object, request: object
    ) -> tuple[str, tuple[str, ...]]:
        # Task 2A.6: when MAGI_SUBAGENT_GOVERNED_TURN_ENABLED is ON, drive the
        # governed-turn primitive instead of the bare run_async loop.  When OFF
        # the existing path runs unchanged (byte-identical).
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415

        if flag_bool("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", env=self._env):
            return await self._collect_turn_text_governed(config, request)
        return await self._collect_turn_text_legacy(config, request)

    async def _collect_turn_text_governed(
        self, config: object, request: object
    ) -> tuple[str, tuple[str, ...]]:
        """Governed-turn branch (MAGI_SUBAGENT_GOVERNED_TURN_ENABLED=1).

        Security invariant: the child's RESTRICTED toolset (from
        ``_resolve_turn_toolset``) is always forwarded to
        ``build_headless_runtime(tools=...)`` so the governed runner never
        receives the full default toolset.

        The 600s wait_for ceiling is preserved: this method is called from
        ``_collect_turn_text`` which is in turn called from ``_drive_one_turn``
        which wraps the whole call in ``asyncio.wait_for``.
        """
        import tempfile  # noqa: PLC0415

        from magi_agent.cli.wiring import build_headless_runtime  # noqa: PLC0415
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415
        from magi_agent.runtime.child_derive import derive  # noqa: PLC0415
        from magi_agent.runtime.child_governed_collector import (  # noqa: PLC0415
            collect_governed_child_turn,
        )
        from magi_agent.runtime.governed_turn import run_governed_turn  # noqa: PLC0415

        session_id = self._child_session_id(request)

        # --- Restricted toolset (security invariant) -------------------------
        # _resolve_turn_toolset returns the SAME restricted toolset the legacy
        # path builds; we pass it directly to build_headless_runtime so the
        # governed runner is also restricted to the child's profile.
        # request is forwarded for Task 2B.3 tighten-only parent_cap filtering.
        tools, _evidence_collector = self._resolve_turn_toolset(session_id, request=request)

        # --- Resolve memory mode + depth from request metadata ---------------
        # parent_memory_mode: read from request.metadata["parentMemoryMode"]
        # (written by the producer in subagents.py, Task F1).  Absent or falsy
        # ⇒ "incognito" (safe default; byte-identical to today when the producer
        # did not set it, i.e. gate-OFF spawn paths).
        memory_inherit_enabled = flag_bool(
            "MAGI_CHILD_MEMORY_INHERIT_ENABLED", env=self._env
        )

        # spawnDepth in request.metadata becomes parent_depth for derive().
        metadata = getattr(request, "metadata", None) or {}
        raw_depth = metadata.get("spawnDepth") if isinstance(metadata, dict) else None
        parent_depth = int(raw_depth) if isinstance(raw_depth, int) and not isinstance(raw_depth, bool) else 0
        parent_memory_mode: str = str(
            metadata.get("parentMemoryMode") or "incognito"
        ) if isinstance(metadata, dict) else "incognito"

        # --- Derive the child TurnContext FIRST (single source of memory_mode) -
        # derive() → _child_memory_mode() is the canonical authority for the
        # child's memory_mode.  We call it before build_headless_runtime so the
        # runtime receives the SAME value the TurnContext carries — eliminating
        # any divergence (e.g. the old "normal" expression when inherit is ON).
        ctx = derive(
            request,
            parent_memory_mode=parent_memory_mode,
            parent_depth=parent_depth,
            memory_inherit_enabled=memory_inherit_enabled,
            child_session_id=session_id,
        )

        # --- Build the child's governed runtime (restricted toolset) ---------
        workspace = self._workspace_root or tempfile.mkdtemp()
        # ``config`` carries the resolved model string so we extract it for the
        # ``model`` kwarg; build_headless_runtime accepts it for future
        # model-selection wiring.
        route_model = _clean_str(getattr(config, "model", None))
        rt = build_headless_runtime(
            cwd=workspace,
            session_id=session_id,
            model=route_model,
            tools=tools,
            memory_mode=ctx.memory_mode,  # single source: derived TurnContext
            permission_mode="bypassPermissions",
        )

        # --- Drive the governed turn + collect summary + evidence_refs -------
        cancel = asyncio.Event()
        summary, evidence_refs, _status = await collect_governed_child_turn(
            run_governed_turn(ctx, runtime=rt, cancel=cancel)
        )
        return summary, evidence_refs

    async def _collect_turn_text_legacy(
        self, config: object, request: object
    ) -> tuple[str, tuple[str, ...]]:
        """Legacy bare run_async path (flag OFF — byte-identical to pre-2A.6)."""
        import tempfile  # noqa: PLC0415

        from google.genai import types  # noqa: PLC0415

        from magi_agent.cli.real_runner import (  # noqa: PLC0415
            build_cli_model_runner,
        )

        # m-2: compute the child session id ONCE and reuse it.
        session_id = self._child_session_id(request)
        runner = self._injected_runner
        # PR1: resolve the toolset (and tool-call evidence collector) ONCE so the
        # same collector instance is wired into the builder and queried after.
        # request is forwarded for Task 2B.3 tighten-only parent_cap filtering.
        tools, evidence_collector = self._resolve_turn_toolset(session_id, request=request)
        if runner is None:
            workspace = self._workspace_root or tempfile.mkdtemp()
            runner = build_cli_model_runner(
                config,  # type: ignore[arg-type]
                tools=tools,
                # m-3: a tools=[] child should NOT get the full filesystem-tool
                # system prompt — give it a minimal delegated-subtask
                # instruction. A tool-enabled child keeps the default tool
                # system prompt so it knows how to use the forwarded tools.
                instruction=_CHILD_INSTRUCTION if not tools else None,
                model_factory=self._model_factory,
                workspace_root=workspace,
                # Child runners may intentionally share the parent workspace for
                # read-only tool access, but they must not build memory
                # snapshots from production-mounted workspace paths. The parent
                # prompt already carries the delegation context.
                memory_mode="incognito",
                session_id=session_id,
                local_tool_evidence_collector=evidence_collector,
            )

        prompt = _child_prompt(request)
        new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
        texts: list[str] = []
        async for event in runner.run_async(
            user_id=self._child_user_id(request),
            session_id=session_id,
            new_message=new_message,
        ):
            content = getattr(event, "content", None)
            event_texts: list[str] = []
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    texts.append(text)
                    event_texts.append(text)
            if event_texts:
                await self._emit_progress(
                    {
                        "type": "child_progress",
                        "detail": _child_stream_progress_detail("".join(event_texts)),
                    }
                )
        evidence_refs = self._collect_evidence_refs(evidence_collector, session_id)
        return "\n".join(texts), evidence_refs

    async def _emit_progress(self, event: Mapping[str, object]) -> None:
        if self._progress_sink is None:
            return
        try:
            result = self._progress_sink(dict(event))
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    # ------------------------------------------------------------------ #
    # PR1: toolset resolution + tool-call evidence promotion              #
    # ------------------------------------------------------------------ #

    def _resolve_turn_toolset(
        self, session_id: str, request: object = None
    ) -> tuple[list[object], object | None]:
        """Resolve the child's toolset + evidence collector for this turn.

        Precedence:
        1. An explicit caller-supplied ``tools`` override (``self._tools``) wins
           and is used verbatim (with the supplied/derived collector).
        2. Otherwise the resolved ``toolset_profile`` decides:
           * ``none`` → empty toolset (byte-identical text-only v1; NO collector).
           * ``readonly`` → core toolset filtered to the read-only allowlist.
           * ``full`` → the whole core toolset (authorisation is upstream's job).

        For tool-enabled profiles a ``LocalToolEvidenceCollector`` is created (or
        the injected one reused) and threaded into ``build_cli_adk_tools`` so
        each tool-call records a public ``evidence:`` ref.

        Task 2B.3 — tighten-only intersection (MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED):
        When the flag is ON and ``request.metadata["parentToolNames"]`` is non-empty,
        the resolved profile tools are filtered to those whose name is in parent_cap.
        When the flag is OFF or parent_cap is empty, the profile tools are returned
        UNCHANGED (byte-identical to pre-2B.3).
        # NOTE: this governs FIRST-PARTY tools only; Composio MCP is a separate
        # default-OFF attachment seam and is out of scope here.
        """
        from magi_agent.config.flags import flag_bool  # noqa: PLC0415
        from magi_agent.runtime.child_toolset import (  # noqa: PLC0415
            toolset_allowlist,
        )

        # Explicit override (tests / advanced callers) — use verbatim.
        if self._tools is not None:
            collector = self._evidence_collector if self._tools else None
            return list(self._tools), collector

        allowlist = toolset_allowlist(self._toolset_profile)  # () | names | None
        if allowlist == ():
            # ``none`` profile — historical text-only child (empty toolset). No
            # collector is built so the no-toolset path stays byte-identical.
            return [], None

        # A real toolset is requested: build (or reuse) the evidence collector
        # FIRST so it can be wired into the tools and queried after the turn.
        collector = self._evidence_collector or self._build_evidence_collector()
        tools = self._build_core_tools(session_id, collector)
        if allowlist is None:
            # ``full`` profile — forward the whole toolset.
            profile_tools: list[object] = list(tools)
        else:
            # ``readonly`` profile — filter to the read-only allowlist by tool name.
            allowed = set(allowlist)
            profile_tools = [tool for tool in tools if _tool_name(tool) in allowed]

        # Task 2B.3: tighten-only intersection — apply AFTER profile filtering.
        # When the flag is ON and parent_cap is non-empty, intersect with the
        # parent's tool names so the child never exceeds the parent's capability.
        # When the flag is OFF or parent_cap is empty, return profile_tools unchanged.
        if flag_bool("MAGI_SUBAGENT_TOOL_TIGHTEN_ONLY_ENABLED", env=self._env):
            metadata = getattr(request, "metadata", None) or {}
            raw_cap = metadata.get("parentToolNames") if isinstance(metadata, dict) else None
            parent_cap = frozenset(raw_cap) if raw_cap else frozenset()
            if parent_cap:
                profile_tools = [t for t in profile_tools if _tool_name(t) in parent_cap]

        # Seam P2-T3: allowedTools is the orchestrator's explicit per-task grant.
        # Apply after parent-cap, before spawn_cap. Gated by same default-OFF flag.
        if flag_bool("MAGI_SPAWN_RECIPE_CAP_ENABLED", env=self._env):
            metadata = getattr(request, "metadata", None) or {}
            raw_allowed = metadata.get("allowedTools") if isinstance(metadata, dict) else None
            allowed = frozenset(raw_allowed) if raw_allowed else frozenset()
            if allowed:
                profile_tools = [t for t in profile_tools if _tool_name(t) in allowed]

        # Seam 4: spawn_cap is the orchestrator's hard grant ceiling. Apply as the
        # innermost cap, after profile and parent-cap. Gated default-OFF.
        if self._spawn_cap and flag_bool("MAGI_SPAWN_RECIPE_CAP_ENABLED", env=self._env):
            cap = frozenset(self._spawn_cap)
            profile_tools = [t for t in profile_tools if _tool_name(t) in cap]

        return profile_tools, collector

    @staticmethod
    def _build_evidence_collector() -> object | None:
        try:
            from magi_agent.evidence.local_tool_collector import (  # noqa: PLC0415
                LocalToolEvidenceCollector,
            )

            return LocalToolEvidenceCollector()
        except Exception:  # noqa: BLE001 — evidence is best-effort, never fatal.
            return None

    def _build_core_tools(self, session_id: str, collector: object | None) -> list[object]:
        import tempfile  # noqa: PLC0415

        from magi_agent.cli.tool_runtime import (  # noqa: PLC0415
            build_cli_adk_tools,
        )

        workspace = self._workspace_root or tempfile.mkdtemp()
        return list(
            build_cli_adk_tools(
                workspace_root=workspace,
                session_id=session_id,
                local_tool_evidence_collector=collector,
                include_local_full_handlers=self._toolset_profile != "readonly",
            )
        )

    @staticmethod
    def _collect_evidence_refs(collector: object | None, session_id: str) -> tuple[str, ...]:
        """Project the collector's recorded tool-call receipts to public
        ``evidence:`` refs for the child envelope. Best-effort: any failure
        yields an empty tuple (never breaks the turn)."""
        if collector is None:
            return ()
        # Preferred lightweight accessor (test fakes implement this directly).
        accessor = getattr(collector, "evidence_refs_for_session", None)
        if callable(accessor):
            try:
                refs = accessor(session_id)
            except Exception:  # noqa: BLE001 — evidence is best-effort.
                return ()
            return _public_evidence_refs(refs)
        # Fall back to the real collector's per-session evidence ledgers.
        ledgers_accessor = getattr(collector, "evidence_ledgers_for_session", None)
        if not callable(ledgers_accessor):
            return ()
        try:
            ledgers = ledgers_accessor(session_id)
        except Exception:  # noqa: BLE001
            return ()
        refs: list[str] = []
        for ledger in ledgers or ():
            for entry in getattr(ledger, "entries", ()) or ():
                ref = getattr(entry, "evidence_ref", None)
                if isinstance(ref, str) and ref:
                    refs.append(ref)
        return _public_evidence_refs(refs)

    def _turn_timeout_s(self, request: object) -> float:
        """Resolve the per-turn timeout (seconds) from ``request.budget_ms``.

        EVERY child turn is bounded — a turn that never finishes would otherwise
        hang the parent turn forever (the spawn_agent tool awaits the child
        boundary inline on the dispatch loop with no outer bound). When no
        positive ``budget_ms`` is present the bound falls back to the ceiling
        (``_MAX_TURN_TIMEOUT_S``, lowered by ``MAGI_MODEL_TIMEOUT_S`` when set)
        rather than returning ``None`` (which would skip the bound entirely). A
        positive ``budget_ms`` is clamped to ``[0, ceiling]``.
        """
        ceiling = _MAX_TURN_TIMEOUT_S
        env_ceiling = _clean_str(self._env.get("MAGI_MODEL_TIMEOUT_S"))
        if env_ceiling is not None:
            try:
                parsed = float(env_ceiling)
            except ValueError:
                parsed = 0.0
            if parsed > 0:
                ceiling = min(ceiling, parsed)
        raw = getattr(request, "budget_ms", None)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            return ceiling
        return min(raw / 1000.0, ceiling)

    # ------------------------------------------------------------------ #
    # Degraded-output builders + id helpers                               #
    # ------------------------------------------------------------------ #

    def _blocked(self, child_execution_id: str, *, reason: str) -> dict[str, object]:
        return self._degraded(child_execution_id, status="blocked", reason=reason)

    def _failed(self, child_execution_id: str, *, reason: str) -> dict[str, object]:
        return self._degraded(child_execution_id, status="failed", reason=reason)

    @staticmethod
    def _degraded(child_execution_id: str, *, status: str, reason: str) -> dict[str, object]:
        return {
            "childExecutionId": child_execution_id,
            "status": status,
            # The reason is a safe, fixed token (no raw error text) — the
            # boundary sanitises ``summary`` regardless.
            "summary": reason,
            "evidenceRefs": (),
            "artifactRefs": (),
            "auditEventRefs": (),
        }

    @staticmethod
    def _child_execution_id(request: object) -> str:
        seed = (
            f"{_clean_str(getattr(request, 'parent_execution_id', None)) or 'parent'}:"
            f"{_clean_str(getattr(request, 'task_id', None)) or 'task'}"
        )
        return f"child-exec-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _child_session_id(request: object) -> str:
        seed = (
            f"{_clean_str(getattr(request, 'parent_execution_id', None)) or 'parent'}:"
            f"{_clean_str(getattr(request, 'turn_id', None)) or 'turn'}:"
            f"{_clean_str(getattr(request, 'task_id', None)) or 'task'}"
        )
        return f"child-session-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _child_user_id(request: object) -> str:
        seed = _clean_str(getattr(request, "parent_execution_id", None)) or "parent"
        return f"child-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _child_prompt(request: object) -> str:
    """Form the child's user message from the request's objective.

    Falls back to a neutral instruction if no objective is present. Role is
    included as light context.
    """
    objective = _clean_str(getattr(request, "objective", None)) or "Complete the delegated subtask."
    role = _clean_str(getattr(request, "role", None)) or "general"
    return f"[child role: {role}]\n{objective}"


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _child_stream_progress_detail(text: str) -> str:
    return f"Child model streamed output chunk ({len(text)} chars)"


def _tool_name(tool: object) -> str | None:
    """Return an ADK tool's ``name`` attribute, or ``None`` when absent."""
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else None


def _public_evidence_refs(refs: object) -> tuple[str, ...]:
    """Filter ``refs`` to deduplicated public ``evidence:`` ref strings.

    The boundary only accepts child output refs in the ``evidence:<token>``
    namespace; anything else is dropped here so a malformed receipt can never
    poison the envelope.
    """
    if not isinstance(refs, (list, tuple)):
        return ()
    out: list[str] = []
    for ref in refs:
        if isinstance(ref, str) and ref.startswith("evidence:") and ref not in out:
            out.append(ref)
    return tuple(out)


__all__ = [
    "LIVE_CHILD_RUNNER_ENABLED_ENV",
    "LIVE_CHILD_RUNNER_KILL_SWITCH_ENV",
    "RealLocalChildRunner",
    "is_live_child_runner_enabled",
]
