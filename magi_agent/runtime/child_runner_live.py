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

import hashlib
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
        workspace_root: str | None = None,
        env: Mapping[str, str] | None = None,
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
        #: v1 scope: TEXT-ONLY — default to an EMPTY toolset (no workspace
        #: mutation). A caller MAY override, but production wiring keeps this
        #: empty until tool-enabled children land as a follow-up.
        self._tools: list[object] = list(tools) if tools is not None else []
        self._workspace_root = workspace_root
        self._env: Mapping[str, str] = os.environ if env is None else env

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
                    reason="child_model_route_unknown",
                )

            # --- Resolve the provider key (degrade if absent) -----------------
            config = self._resolve_provider_config(provider, model)
            if config is None:
                return self._blocked(
                    child_execution_id,
                    reason="child_provider_key_missing",
                )

            # --- Drive ONE turn and collect the final text --------------------
            final_text = await self._drive_one_turn(config, request)
        except Exception:  # noqa: BLE001 — NEVER raise across the seam.
            return self._failed(
                child_execution_id,
                reason="child_turn_error",
            )

        summary = (final_text or "").strip()[:_MAX_SUMMARY_CHARS]
        return {
            "childExecutionId": child_execution_id,
            "status": "completed",
            "summary": summary,
            "evidenceRefs": (),
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
        """Resolve the route against the local ``ModelTierRegistry``.

        Returns the resolved record on a KNOWN route, else ``None`` (the caller
        blocks). An unknown model resolves to a sentinel ``standard`` tier with
        the ``unknown_model_*`` reason code — we treat that as a rejection so a
        child can never route to an unvetted model.
        """
        from magi_agent.runtime.model_tiers import (  # noqa: PLC0415
            ModelTierRegistry,
        )

        try:
            resolved = ModelTierRegistry.with_defaults().resolve(
                provider=provider,
                model=model,
            )
        except Exception:  # noqa: BLE001 — label-validation failure → reject.
            return None
        reason_codes = tuple(getattr(resolved, "reason_codes", ()) or ())
        if any("unknown_model" in code for code in reason_codes):
            return None
        return resolved

    def _resolve_provider_config(self, provider: str, model: str) -> object | None:
        """Return a ``ProviderConfig`` with a usable key, or ``None``.

        Prefers an injected ``provider_config`` that already carries a key
        (tests / explicit callers). Otherwise delegates to
        ``resolve_provider_config`` (config file + env). NO key → ``None``
        (the caller degrades to blocked; never crashes).
        """
        injected_key = _clean_str(getattr(self._provider_config, "api_key", None))
        injected_provider = _clean_str(getattr(self._provider_config, "provider", None))
        if injected_key and injected_provider == provider:
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
        overlay["MAGI_PROVIDER"] = provider
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

    async def _drive_one_turn(self, config: object, request: object) -> str:
        """Build/reuse a ``CliModelRunner`` and drive ONE turn; return final text.

        Heavy ADK imports are LOCAL so importing this module never triggers
        them. Mirrors the discovery orchestrator's message construction +
        event-text collection.
        """
        import tempfile  # noqa: PLC0415

        from google.genai import types  # noqa: PLC0415

        from magi_agent.cli.real_runner import (  # noqa: PLC0415
            build_cli_model_runner,
        )

        runner = self._injected_runner
        if runner is None:
            workspace = self._workspace_root or tempfile.mkdtemp()
            runner = build_cli_model_runner(
                config,  # type: ignore[arg-type]
                # v1 TEXT-ONLY: empty toolset → no workspace mutation.
                tools=list(self._tools),
                model_factory=self._model_factory,
                workspace_root=workspace,
                session_id=self._child_session_id(request),
            )

        prompt = _child_prompt(request)
        new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
        texts: list[str] = []
        async for event in runner.run_async(
            user_id=self._child_user_id(request),
            session_id=self._child_session_id(request),
            new_message=new_message,
        ):
            content = getattr(event, "content", None)
            for part in getattr(content, "parts", None) or []:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    texts.append(text)
        return "\n".join(texts)

    # ------------------------------------------------------------------ #
    # Degraded-output builders + id helpers                               #
    # ------------------------------------------------------------------ #

    def _blocked(self, child_execution_id: str, *, reason: str) -> dict[str, object]:
        return self._degraded(child_execution_id, status="blocked", reason=reason)

    def _failed(self, child_execution_id: str, *, reason: str) -> dict[str, object]:
        return self._degraded(child_execution_id, status="failed", reason=reason)

    @staticmethod
    def _degraded(
        child_execution_id: str, *, status: str, reason: str
    ) -> dict[str, object]:
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


__all__ = [
    "LIVE_CHILD_RUNNER_ENABLED_ENV",
    "LIVE_CHILD_RUNNER_KILL_SWITCH_ENV",
    "RealLocalChildRunner",
    "is_live_child_runner_enabled",
]
