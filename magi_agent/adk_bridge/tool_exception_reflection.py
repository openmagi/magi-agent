"""Generic tool-exception reflection for the live ADK Runner.

Hermes-agent parity (mechanism 1, raise path): a tool handler that raises —
any tool except ``FileEdit``/``PatchApply`` — currently propagates through
ADK ``google.adk.flows.llm_flows.functions`` and kills the whole turn
(``engine_error``) unless the ErrorClassifier calls it Recoverable. This
plugin converts the exception into a model-visible corrective tool_result
with retry guidance and a per-invocation attempt budget, so the model
self-corrects and the turn continues.

Live integration point
-----------------------
Same seam as :mod:`magi_agent.adk_bridge.edit_retry_reflection`: when a tool
raises, ADK invokes ``PluginManager.run_on_tool_error_callback``; if a plugin
returns a ``dict``, that dict **replaces** the raise and is fed to the model
as the tool's function_response on the next LLM call. The
``_ExtendedControlPlanePlugin`` fan-out in ``control_plane.py`` forwards
``on_tool_error_callback`` to every registered control's ``._plugin`` with
first-non-None-wins semantics, so this plugin attaches purely via plane
registration — zero loop-internal edits.

Behavior contract
-----------------
* Tools in ``edit_retry_reflection._EDIT_TOOL_NAMES`` (``FileEdit``/
  ``PatchApply``) are hard-skipped: they keep their specialized fail-closed
  handler, and an exhausted edit-retry budget must never be extended by this
  generic plugin (documented import-time coupling on that frozenset).
* Under budget: returns a marker dict (``response_type`` =
  :data:`TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE`, ``status="error"``) with
  the error type/message and retry guidance.
* At/over budget (attempt > ``max_attempts``): returns ``None`` so ADK
  re-raises — today's abort behavior is restored, bounding how many model
  retries a persistent infra failure can consume.
* Fail-open: any internal exception in the callback returns ``None``
  (original behavior). Only ``Exception`` is caught — ``BaseException``
  (e.g. ``asyncio.CancelledError``) always propagates.

Flag ownership: ``MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED`` (default OFF,
strict truthy, profile-independent) and ``MAGI_TOOL_EXCEPTION_MAX_ATTEMPTS``
are parsed in ``magi_agent.config.env``; callers pass resolved values to
:func:`build_tool_exception_reflection_plugin`.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

# Documented import-time coupling: the specialized edit-retry handler owns
# these tools (fail-closed at its own budget); the generic plugin must skip
# them so an exhausted edit-retry budget is never granted extra attempts.
# ``_ScopedScalarView`` is the shared S-C write-through mapping view (one
# owner of the mutable counters: the runtime-owned PerInvocationState).
from magi_agent.adk_bridge.edit_retry_reflection import (
    _EDIT_TOOL_NAMES,
    _ScopedScalarView,
)
from magi_agent.packs.context import PerInvocationState


TOOL_EXCEPTION_REFLECTION_PLUGIN_NAME = "magi_tool_exception_reflection_plugin"

# Marker placed on the replacement tool response so downstream
# evidence/telemetry never mistakes the injected corrective message for a real
# tool success (same convention as EDIT_RETRY_REFLECTION_RESPONSE_TYPE).
TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE = "MAGI_TOOL_EXCEPTION_REFLECTION"

_ERROR_MESSAGE_MAX_CHARS = 500

_GLOBAL_SCOPE_KEY = "__magi_tool_exception_reflection_global__"

_GUIDANCE_TEMPLATE = (
    "The {tool_name} tool call raised an unexpected error. Review the error "
    "message and your arguments, correct the call, and retry. If it fails "
    "again, choose a different approach instead of repeating the same call."
)


class MagiToolExceptionReflectionPlugin(BasePlugin):
    """ADK plugin that converts generic tool raises into corrective results.

    Attempt counters are keyed by ``(scope_key, tool_name)`` — the scope key
    is the ADK invocation id — so the budget is enforced per turn and never
    grows unbounded across turns (swept by :meth:`after_run_callback`).

    Phase 5 / S-C: the per-invocation attempt counters live in a runtime-owned
    :class:`PerInvocationState` (``self._default_state``) rather than a private
    dict, so a user-authored equivalent control gets the same state struct off
    the typed context. The legacy ``self._attempts`` mapping is preserved as a
    live write-through view over that state (the ADK callback below feeds the
    default state; the typed-context adapter supplies a context-owned state).
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        name: str = TOOL_EXCEPTION_REFLECTION_PLUGIN_NAME,
    ) -> None:
        super().__init__(name)
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        # Runtime-owned per-invocation state (the ONE owner of the mutable
        # attempt counters). Replaces the old plugin-private ``self._attempts``
        # dict; the legacy attribute is now a live write-through view.
        self._default_state = PerInvocationState()

    @property
    def _attempts(self) -> MutableMapping[tuple[str, str], Any]:
        """Live ``(scope_key, tool_name) -> attempt`` view over the runtime state.

        Backward-compatible surface: reads, writes, and sweeps behave exactly
        like the old dict while the storage is the runtime-owned struct (same
        LRU/sweep semantics as the other S-C migrations).
        """
        return _ScopedScalarView(self._default_state)

    # -- ADK callbacks ----------------------------------------------------

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> dict[str, Any] | None:
        """A tool *raised*. Return a corrective dict, or None to re-raise.

        ADK-callback path: feed the plugin's runtime-owned default state. The
        typed-context path supplies a context-owned state instead.
        """
        return self.reflect_with_state(
            state=self._default_state,
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            error=error,
        )

    # -- core (S-C typed-context decision) ---------------------------------

    def reflect_with_state(
        self,
        *,
        state: PerInvocationState,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> dict[str, Any] | None:
        """Pure decision over a runtime-owned :class:`PerInvocationState` (S-C).

        Replaces the instance-private ``self._attempts`` mutation: the caller
        (the ADK callback above, or the typed-context control adapter) supplies
        the shared state. The attempt counter is read/incremented on ``state``;
        everything else (edit-tool hard-skip, budget fail-closed, corrective
        dict shape) is byte-identical to the pre-migration body.

        Fail-open: any internal failure returns ``None`` so the original raise
        propagates exactly as today. Only ``Exception`` is caught —
        ``BaseException`` (``asyncio.CancelledError`` etc.) must propagate.
        """
        _ = tool_args
        try:
            tool_name = getattr(tool, "name", "")
            if not isinstance(tool_name, str) or not tool_name:
                tool_name = "unknown_tool"
            if tool_name in _EDIT_TOOL_NAMES:
                # FileEdit/PatchApply keep their specialized fail-closed
                # edit-retry handler; never grant them extra attempts here.
                return None

            scope_key = _scope_key(tool_context)
            attempt = state.get_scoped(scope_key, tool_name, default=0) + 1
            state.set_scoped(scope_key, tool_name, attempt)
            if attempt > self.max_attempts:
                # Budget exhausted -> None so ADK re-raises (original abort).
                return None

            return {
                "response_type": TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE,
                "status": "error",
                "error_type": type(error).__name__,
                "error_message": str(error)[:_ERROR_MESSAGE_MAX_CHARS],
                "retry_attempt": attempt,
                "max_attempts": self.max_attempts,
                "guidance": _GUIDANCE_TEMPLATE.format(tool_name=tool_name),
            }
        except Exception:
            return None

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        """Sweep attempt counters for the invocation that just finished.

        The plugin instance lives for the whole process, so without this
        cleanup ``_attempts`` would grow unbounded. The invocation id is the
        scope key, so on run completion we drop every counter belonging to
        that invocation via the runtime state's clear-on-turn-complete hook.
        """
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._default_state.clear_invocation(inv)


# -- helpers --------------------------------------------------------------


def _scope_key(tool_context: Any) -> str:
    invocation_id = getattr(tool_context, "invocation_id", None)
    if isinstance(invocation_id, str) and invocation_id:
        return invocation_id
    return _GLOBAL_SCOPE_KEY


def build_tool_exception_reflection_plugin(
    *,
    enabled: bool,
    max_attempts: int,
) -> MagiToolExceptionReflectionPlugin | None:
    """Return a configured plugin, or ``None`` when the feature is disabled.

    The flag/budget are owned by ``magi_agent.config.env`` (single source);
    callers pass the resolved values here so the plugin module stays
    import-light and free of env-parsing concerns.
    """
    if not enabled:
        return None
    return MagiToolExceptionReflectionPlugin(max_attempts=max_attempts)


__all__ = [
    "TOOL_EXCEPTION_REFLECTION_PLUGIN_NAME",
    "TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE",
    "MagiToolExceptionReflectionPlugin",
    "build_tool_exception_reflection_plugin",
]
