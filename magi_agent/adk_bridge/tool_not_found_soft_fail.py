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

Retry policy: delegated, not hard-coded
---------------------------------------
Kevin's follow-up direction (post 0.1.97):

    "단순히 툴 실패시 n회 재시도 이런식의 하드룰보다는 모델에게
    flexibility를 보장하는 근본적인 솔루션으로 하는게 좋을듯"

So this plugin no longer imposes a per-invocation "n retries then hard-fail"
cap. Every unknown-tool call is surfaced to the model as a corrective
tool_result carrying the available-tools list. Retry policy is delegated to:

* the MODEL (it can try a different tool, generate text, or ask a
  follow-up on the next iteration), and
* the runtime's TURN-LEVEL iteration cap (``max_iterations`` / turn budget /
  ``max_tokens`` etc.), which is the existing runaway backstop.

Reference parity: Claude Code, OpenAI Agents SDK, and OpenCode do not impose
a per-tool retry cap either; they rely on the same turn-level backstop and
let the model decide when to stop trying.

Operator escape hatch (opt-in, not default-behavior)
----------------------------------------------------
An operator who wants a self-imposed limit can set
``MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP=N`` (N >= 1). When and only when that env
is explicitly set, the plugin enforces a per-invocation cap of N. Once the
cap is exceeded the plugin returns ``None`` and ADK re-raises the original
``ValueError``; PR-3's child-runner containment then surfaces the terminal
event with :data:`TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE`. The default is
``cap == 0``, interpreted as UNLIMITED: the plugin never returns ``None``
for the ADK unknown-tool shape unless the operator has explicitly opted in.

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
* Default (``attempt_cap == 0`` = unlimited): every unknown-tool call
  becomes a corrective dict with ``error_code="tool_not_found"``, the
  requested tool name, and the available-tools list parsed from ADK's own
  error text. The plugin never returns ``None`` for the ADK unknown-tool
  shape; retry decisions belong to the model + the turn-level cap.
* Operator opt-in (``attempt_cap >= 1``): under budget returns the
  corrective dict; at/over budget returns ``None`` so ADK re-raises. Only
  in this explicitly opted-in path does
  :data:`TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE` surface as the terminal
  error_code.
* Fail-open: any internal exception in the callback returns ``None``
  (original behavior). Only ``Exception`` is caught. ``BaseException``
  (e.g. ``asyncio.CancelledError``) always propagates.

Flag ownership: ``MAGI_TOOL_NOT_FOUND_SOFT_FAIL`` (default-ON, profile-aware)
is the on/off switch; the numeric ``MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP`` is an
opt-in escape hatch and defaults to ``0`` (unlimited). Both are parsed in
:mod:`magi_agent.config.env`; callers pass resolved values to
:func:`build_tool_not_found_soft_fail_plugin`.

Why default-ON is safe
----------------------
The retry pool is bounded by the toolset the runtime already advertises to
the model (unknown tool -> corrective dict lists the exposed tools; model
must pick one of THOSE, so it cannot escalate authority). The corrective
error text is identical in information to what ADK already raises today, so
no new public information is surfaced. Runaway iteration is bounded by the
runtime's existing turn-level cap, not by this plugin.
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

# Terminal error_code the child-runner containment surfaces when an operator
# has explicitly opted in to a numeric cap via ``MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP``
# and the cap has been exhausted (plugin returns None, ADK re-raises). Not
# reachable in the default (unlimited) configuration; kept as a distinct label
# so a wrapping trace can distinguish operator-imposed exhaustion from a
# generic ``llm_call_exception``.
TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE = "child_llm_unknown_tool_retry_exhausted"

# Sentinel used by :func:`build_tool_not_found_soft_fail_plugin` to represent
# "operator did not opt in to a numeric cap"; the plugin then never terminates
# the corrective path itself.
_UNLIMITED_ATTEMPT_CAP = 0

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

    Retry policy is delegated to the model + the turn-level iteration cap the
    runtime already enforces. This plugin does NOT impose a per-tool retry
    cap by default. An operator may opt in to a per-invocation cap via
    ``MAGI_TOOL_NOT_FOUND_ATTEMPT_CAP=N`` (surfaced here as ``attempt_cap=N``,
    N >= 1); ``attempt_cap == 0`` means unlimited (the default).

    Attempt counters are keyed by ``(scope_key, tool_name)`` (the scope key
    is the ADK invocation id) so an opt-in cap is enforced per turn and
    never grows unbounded across turns (swept by :meth:`after_run_callback`).

    Phase 5 / S-C: the per-invocation attempt counters live in a runtime-owned
    :class:`PerInvocationState` rather than a private dict, mirroring the
    edit-retry and generic tool-exception reflection plugins.
    """

    def __init__(
        self,
        *,
        attempt_cap: int = _UNLIMITED_ATTEMPT_CAP,
        name: str = TOOL_NOT_FOUND_SOFT_FAIL_PLUGIN_NAME,
    ) -> None:
        super().__init__(name)
        if attempt_cap < 0:
            raise ValueError("attempt_cap must be >= 0 (0 = unlimited)")
        self.attempt_cap = attempt_cap
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
            # Operator-opt-in cap (``attempt_cap >= 1``). In the default
            # unlimited config (``attempt_cap == 0``) the plugin never
            # terminates the corrective path; the runtime's turn-level
            # iteration cap is the runaway backstop.
            if self.attempt_cap > 0 and attempt > self.attempt_cap:
                # Operator-imposed budget exhausted -> None so ADK re-raises
                # the original ValueError. PR-3's child-runner containment
                # then surfaces the terminal event distinctly via
                # TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE.
                return None
            available_text = ", ".join(available) if available else "(none)"
            guidance = _GUIDANCE_TEMPLATE.format(
                requested=requested,
                available_text=available_text,
            )
            payload: dict[str, Any] = {
                "response_type": TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE,
                "status": "error",
                "error_code": TOOL_NOT_FOUND_ERROR_CODE,
                "error_message": guidance,
                "requested_tool": requested,
                "available_tools": list(available),
                "retry_attempt": attempt,
            }
            # Only advertise ``attempt_cap`` on the payload when the operator
            # explicitly opted in; the default unlimited path stays clean.
            if self.attempt_cap > 0:
                payload["attempt_cap"] = self.attempt_cap
            return payload
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
    attempt_cap: int = _UNLIMITED_ATTEMPT_CAP,
) -> MagiToolNotFoundSoftFailPlugin | None:
    """Return a configured plugin, or ``None`` when the feature is disabled.

    ``attempt_cap == 0`` (the default) means unlimited: the plugin never
    hard-fails the corrective path; retry policy is delegated to the model
    plus the runtime's turn-level iteration cap.

    ``attempt_cap >= 1`` opts in to an operator-imposed per-invocation
    cap; once exceeded the plugin returns ``None`` and ADK re-raises the
    original ``ValueError``.

    The flag / cap are owned by :mod:`magi_agent.config.env` (single source
    of truth); callers pass the resolved values here so this module stays
    import-light and free of env-parsing concerns.
    """
    if not enabled:
        return None
    return MagiToolNotFoundSoftFailPlugin(attempt_cap=attempt_cap)


__all__ = [
    "TOOL_NOT_FOUND_SOFT_FAIL_PLUGIN_NAME",
    "TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE",
    "TOOL_NOT_FOUND_STATE_NAMESPACE",
    "TOOL_NOT_FOUND_ERROR_CODE",
    "TOOL_NOT_FOUND_RETRY_EXHAUSTED_CODE",
    "MagiToolNotFoundSoftFailPlugin",
    "build_tool_not_found_soft_fail_plugin",
]
