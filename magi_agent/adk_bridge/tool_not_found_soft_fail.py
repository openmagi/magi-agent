"""Soft-fail unknown-tool as a tool_result so the model can retry (PR-R).

Kevin 0.1.97 direct-debug: a SOTA-spawn child (Gemini 3.1 Pro) requested
``Bash`` which is not in the readonly child toolset. Google ADK's
``google.adk.flows.llm_flows.functions._get_tool`` raises
``ValueError("Tool '<name>' not found.\\nAvailable tools: ...")`` and the child
TURN terminated with ``llm_call_exception`` because no
``on_tool_error_callback`` converted the raise into a corrective tool_result.

Kevin's architectural note: "tool 한번 실패했다고 다른 방법을 안찾고 바로
포기하는거 자체가 이상한 거 아닌가." Claude Code / OpenAI Agents SDK /
OpenCode all soft-fail unknown-tool: the model sees a tool_result with the
error text plus the list of available tools, and the next iteration picks a
valid tool from that list.

Live integration point
----------------------
Same seam as :mod:`magi_agent.adk_bridge.tool_exception_reflection` and
:mod:`magi_agent.adk_bridge.edit_retry_reflection`. ADK's
``_execute_single_function_call_async`` builds a placeholder
``BaseTool(name=function_call.name, description="Tool not found")`` when
``_get_tool`` raises, then invokes ``PluginManager.run_on_tool_error_callback``.
If a plugin returns a dict, that dict replaces the raise and is fed to the
model as the tool's function_response on the next LLM call.
``_ExtendedControlPlanePlugin`` in :mod:`magi_agent.adk_bridge.control_plane`
already fans out ``on_tool_error_callback`` to every registered adapter's
``._plugin`` with first-non-None-wins semantics, so this plugin attaches
purely via plane registration; zero loop-internal edits.

Behavior contract
-----------------
* ONLY handles the ADK unknown-tool shape: matched on the placeholder
  ``tool.description == "Tool not found"`` marker (or on the ``ValueError``
  message shape as a defensive fallback). Every other raise returns ``None``
  so the generic tool-exception reflection plugin keeps first crack on real
  raises.
* Under budget: returns a marker dict with ``error_code="tool_not_found"``,
  the requested tool name, and the available-tools list parsed from ADK's
  own error text. The model reads the dict on the next LLM call and can
  pick a valid tool from the list.
* At/over budget (attempt > ``max_attempts``): returns ``None`` so ADK
  re-raises the original ``ValueError``. Today's abort behavior is
  restored, bounding how many hallucination-loop iterations a stuck model
  can consume. The child-runner containment (PR-3 governed collector) then
  surfaces the terminal event with the distinct
  :data:`TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE`.
* Fail-open: any internal exception in the callback returns ``None``
  (original behavior). Only ``Exception`` is caught. ``BaseException``
  (e.g. ``asyncio.CancelledError``) always propagates.

Flag ownership: ``MAGI_TOOL_NOT_FOUND_SOFT_FAIL`` (default-ON, strict
truthy, profile-independent) and ``MAGI_TOOL_NOT_FOUND_SOFT_FAIL_MAX_ATTEMPTS``
are parsed in :mod:`magi_agent.config.env`; callers pass resolved values to
:func:`build_tool_not_found_soft_fail_plugin`.

Why default-ON is safe
----------------------
The retry pool is bounded by the toolset the runtime already advertises to
the model (unknown tool -> corrective dict lists the exposed tools; model
must pick one of THOSE, so it cannot escalate authority). The corrective
error text is identical in information to what ADK already raises today, so
no new public information is surfaced.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.adk_bridge.edit_retry_reflection import (
    _ScopedScalarView,
    scoped_state_name,
)
from magi_agent.packs.context import PerInvocationState


TOOL_NOT_FOUND_SOFT_FAIL_PLUGIN_NAME = "magi_tool_not_found_soft_fail_plugin"

# Per-control namespace for the shared PerInvocationState scalar key (S-C), so
# this control's per-invocation counters never collide with the other S-C
# controls' counters for the same tool in the same invocation when they share
# one PerInvocationState.
TOOL_NOT_FOUND_STATE_NAMESPACE = "tool_not_found_soft_fail"

# Marker placed on the replacement tool response so downstream
# evidence/telemetry never mistakes the injected corrective message for a real
# tool success (same convention as TOOL_EXCEPTION_REFLECTION_RESPONSE_TYPE).
TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE = "MAGI_TOOL_NOT_FOUND_SOFT_FAIL"

# Public error_code the model / caller / trace sees on the corrective dict.
TOOL_NOT_FOUND_ERROR_CODE = "tool_not_found"

# Terminal error_code the child-runner containment surfaces when the plugin
# returns None (budget exhausted) and ADK re-raises. Constant exposed here so
# the child-runner boundary can label the terminal engine_trace event
# distinctly instead of a generic ``llm_call_exception``.
TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE = "child_llm_unknown_tool_retry_exhausted"

_GLOBAL_SCOPE_KEY = "__magi_tool_not_found_soft_fail_global__"

# ADK marker: google/adk/flows/llm_flows/functions.py
# _execute_single_function_call_async builds
# ``BaseTool(name=function_call.name, description='Tool not found')`` when
# ``_get_tool`` raises. That literal is our reliable classifier; no other ADK
# path sets that exact description string.
_ADK_UNKNOWN_TOOL_DESCRIPTION = "Tool not found"

# ADK error text: functions._get_tool raises
# ``Tool '<name>' not found.\nAvailable tools: a, b, c\n\nPossible causes: ...``
# The regex parses the requested name and the comma-separated tools list.
_ADK_UNKNOWN_TOOL_ERROR_RE = re.compile(
    r"^Tool '([^']+)' not found\.\s*\nAvailable tools:\s*([^\n]*)",
)

_GUIDANCE_TEMPLATE = (
    "Tool {requested!r} does not exist in this turn's toolset. "
    "Available tools: {available_text}. "
    "Pick one of the available tools instead of retrying the same name."
)


class MagiToolNotFoundSoftFailPlugin(BasePlugin):
    """ADK plugin that converts the unknown-tool ValueError into a corrective
    tool_result so the model can pick a valid tool on the next iteration.

    Attempt counters are keyed by ``(scope_key, tool_name)`` (the scope key
    is the ADK invocation id) so the budget is enforced per turn and never
    grows unbounded across turns (swept by :meth:`after_run_callback`).

    Phase 5 / S-C: the per-invocation attempt counters live in a runtime-owned
    :class:`PerInvocationState` rather than a private dict, mirroring the
    edit-retry and generic tool-exception reflection plugins.
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        name: str = TOOL_NOT_FOUND_SOFT_FAIL_PLUGIN_NAME,
    ) -> None:
        super().__init__(name)
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        # Runtime-owned per-invocation state (the ONE owner of the mutable
        # attempt counters). Same pattern as the sibling reflection plugins.
        self._default_state = PerInvocationState()

    @property
    def _attempts(self) -> MutableMapping[tuple[str, str], Any]:
        """Live ``(scope_key, tool_name) -> attempt`` view over the runtime
        state (namespaced so counters stay disjoint from other S-C controls'
        counters that share one PerInvocationState)."""
        return _ScopedScalarView(self._default_state, TOOL_NOT_FOUND_STATE_NAMESPACE)

    # -- ADK callbacks ----------------------------------------------------

    async def on_tool_error_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> dict[str, Any] | None:
        """A tool call failed. Return a corrective dict for the ADK
        unknown-tool shape, or None to pass through (generic reflection then
        gets its turn)."""
        return self.reflect_with_state(
            state=self._default_state,
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            error=error,
        )

    # -- core (S-C typed-context decision) --------------------------------

    def reflect_with_state(
        self,
        *,
        state: PerInvocationState,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        error: Exception,
    ) -> dict[str, Any] | None:
        """Pure decision over a runtime-owned :class:`PerInvocationState`
        (S-C). Fail-open: any internal failure returns ``None`` so the
        original raise propagates unchanged."""
        _ = tool_args
        try:
            if not _is_adk_unknown_tool_error(tool, error):
                return None
            requested, available = _extract_requested_and_available(tool, error)
            scope_key = _scope_key(tool_context)
            state_name = scoped_state_name(TOOL_NOT_FOUND_STATE_NAMESPACE, requested)
            attempt = state.get_scoped(scope_key, state_name, default=0) + 1
            state.set_scoped(scope_key, state_name, attempt)
            if attempt > self.max_attempts:
                # Budget exhausted -> None so ADK re-raises the original
                # ValueError. PR-3's child-runner containment then surfaces
                # the terminal event distinctly via
                # TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE.
                return None
            available_text = ", ".join(available) if available else "(none)"
            guidance = _GUIDANCE_TEMPLATE.format(
                requested=requested,
                available_text=available_text,
            )
            return {
                "response_type": TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE,
                "status": "error",
                "error_code": TOOL_NOT_FOUND_ERROR_CODE,
                "error_message": guidance,
                "requested_tool": requested,
                "available_tools": list(available),
                "retry_attempt": attempt,
                "max_attempts": self.max_attempts,
            }
        except Exception:  # noqa: BLE001 fail-open
            return None

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        """Sweep attempt counters for the invocation that just finished, so
        the plugin state never grows unbounded across turns."""
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._default_state.clear_invocation(inv)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _is_adk_unknown_tool_error(tool: Any, error: Exception) -> bool:
    """Classify an on_tool_error_callback invocation as ADK's unknown-tool
    raise.

    Primary marker: ADK builds a placeholder ``BaseTool`` with
    ``description == "Tool not found"`` for this case, so the description
    literal is the strongest signal. Fallback: match the ValueError's
    canonical text shape for callers that reuse the same error shape without
    the placeholder description.
    """
    if not isinstance(error, ValueError):
        return False
    description = getattr(tool, "description", None)
    if isinstance(description, str) and description == _ADK_UNKNOWN_TOOL_DESCRIPTION:
        return True
    # Defensive fallback: parse the error text directly.
    return bool(_ADK_UNKNOWN_TOOL_ERROR_RE.match(str(error)))


def _extract_requested_and_available(tool: Any, error: Exception) -> tuple[str, tuple[str, ...]]:
    """Best-effort parse of ADK's canonical Tool-not-found error string.

    Falls back to ``tool.name`` (the placeholder tool ADK constructs
    preserves the requested name) if the message shape drifts.
    """
    requested = ""
    name = getattr(tool, "name", None)
    if isinstance(name, str) and name:
        requested = name
    available: tuple[str, ...] = ()
    match = _ADK_UNKNOWN_TOOL_ERROR_RE.match(str(error))
    if match:
        if not requested:
            requested = match.group(1)
        raw = match.group(2).strip()
        if raw:
            available = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not requested:
        requested = "<unknown>"
    return requested, available


def _scope_key(tool_context: Any) -> str:
    invocation_id = getattr(tool_context, "invocation_id", None)
    if isinstance(invocation_id, str) and invocation_id:
        return invocation_id
    return _GLOBAL_SCOPE_KEY


def build_tool_not_found_soft_fail_plugin(
    *,
    enabled: bool,
    max_attempts: int,
) -> MagiToolNotFoundSoftFailPlugin | None:
    """Return a configured plugin, or ``None`` when the feature is disabled.

    The flag / budget are owned by :mod:`magi_agent.config.env` (single
    source of truth); callers pass the resolved values here so this module
    stays import-light and free of env-parsing concerns.
    """
    if not enabled:
        return None
    return MagiToolNotFoundSoftFailPlugin(max_attempts=max_attempts)


__all__ = [
    "TOOL_NOT_FOUND_SOFT_FAIL_PLUGIN_NAME",
    "TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE",
    "TOOL_NOT_FOUND_STATE_NAMESPACE",
    "TOOL_NOT_FOUND_ERROR_CODE",
    "TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE",
    "MagiToolNotFoundSoftFailPlugin",
    "build_tool_not_found_soft_fail_plugin",
]
