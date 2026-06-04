"""Live ADK resilience plugin — loop guard + multi-strategy error recovery.

PR12: magi-agent already ships two resilience subsystems that were built but
left DORMANT (no live callers):

* ``magi_agent.runtime.loop_detectors.ToolCallLoopDetector`` — OpenCode-style
  "doom loop" detection (N identical consecutive tool calls / per-name
  frequency), and
* ``magi_agent.runtime.error_recovery`` — a ``RecoveryEngine`` plus six
  strategies (RateLimit, OutputEscalation, CollapseDrain, ReactiveCompact,
  MediaRemoval, RecoveryMessage), with an ``ErrorClassifier`` taxonomy.

This module *activates* both on the live Google ADK turn engine by exposing
them as a single Google ADK ``BasePlugin``: :class:`MagiResiliencePlugin`. It is
the common, reusable plugin shim that PR13 (compaction) and PR14 will extend.

Live integration points (confirmed against the installed ``google.adk``)
------------------------------------------------------------------------
``Runner.run_async`` owns the multi-step model/tool loop. The plugin manager
fires plugin callbacks on the *live* path:

* ``after_tool_callback`` — runs once per tool result inside
  ``functions.py:_run_with_trace``. Returning a dict **replaces** the tool
  result fed to the model. The loop guard uses this: it feeds the call into the
  existing ``ToolCallLoopDetector`` and, on a soft trigger, *augments* the real
  result with a model-visible nudge (the tool still ran); on a hard trigger it
  *replaces* the result with a stop directive so the model stops re-issuing the
  identical call.
* ``on_model_error_callback`` — fires in ``base_llm_flow.py`` when the model
  call raises. Returning an ``LlmResponse`` substitutes for the error
  (recovery); returning ``None`` lets the original error propagate (terminal /
  fail-open to the TS runtime). The error recovery path uses this: classify via
  ``ErrorClassifier`` and run ``RecoveryEngine.attempt_recovery``; a successful
  strategy (e.g. RateLimit honoring Retry-After) yields a recovery
  ``LlmResponse`` and the run continues; a terminal error yields ``None`` with
  no retry.
* ``after_run_callback`` — sweeps per-invocation state so detector/recovery
  state never grows unbounded across turns.

ContextOverflow (prompt_too_long) hook
---------------------------------------
Per PR12 scope, prompt_too_long is *classified* but not infinitely retried at
the model-error boundary: rewriting the in-flight request's messages is PR13
(compaction) territory. The plugin records the classification and exposes a
``context_overflow_hook`` seam that PR13 can wire to a real compaction pass.
Today it returns ``None`` (propagate / fail-open) so we never loop forever on a
prompt that is simply too long.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from google.adk.models import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.runtime.error_recovery import (
    ErrorClassifier,
    ErrorKind,
    ErrorRecoveryConfig,
    LLMCompactCaller,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryEngine,
)
from magi_agent.runtime.error_recovery.engine import DEFAULT_STRATEGIES
from magi_agent.runtime.error_recovery.strategies import (
    CollapseDrainStrategy,
    MediaRemovalStrategy,
    OutputEscalationStrategy,
    RateLimitStrategy,
    ReactiveCompactStrategy,
    RecoveryMessageStrategy,
)
from magi_agent.runtime.loop_detectors import (
    LoopCheckResult,
    ToolCallLoopDetector,
)

RESILIENCE_PLUGIN_NAME = "magi_resilience_plugin"

# Marker placed on loop-guard tool responses so downstream telemetry recognises
# the injected nudge/stop and never mistakes it for a real tool success/error.
LOOP_GUARD_RESPONSE_TYPE = "MAGI_LOOP_GUARD"
LOOP_GUARD_HARD_STATUS = "blocked"
LOOP_GUARD_SOFT_KEY = "magi_loop_guard_nudge"

# Marker on the recovery LlmResponse custom_metadata so callers can see a
# recovery happened (and which strategy) without exposing internals.
RECOVERY_METADATA_KEY = "magi_error_recovery"

# Hook signature for PR13 context-overflow / compaction wiring. Given the
# classified overflow error it may return an LlmResponse to substitute, else
# None (propagate / fail-open).
ContextOverflowHook = Callable[
    [RecoverableError, Any], "LlmResponse | None | Awaitable[LlmResponse | None]"
]


class MagiResiliencePlugin(BasePlugin):
    """ADK plugin activating loop guard + error recovery on the live runner.

    Both subsystems are independently flag-gated by the builder: passing
    ``loop_detector_factory=None`` disables the loop guard, ``recovery_engine=
    None`` disables recovery. When both are ``None`` the plugin is inert and the
    builder returns ``None`` instead of attaching it (zero regression).
    """

    def __init__(
        self,
        *,
        name: str = RESILIENCE_PLUGIN_NAME,
        loop_detector_factory: Callable[[], ToolCallLoopDetector] | None = None,
        recovery_engine: RecoveryEngine | None = None,
        recovery_max_attempts: int = 3,
        context_overflow_hook: ContextOverflowHook | None = None,
    ) -> None:
        super().__init__(name)
        self._loop_detector_factory = loop_detector_factory
        self._recovery_engine = recovery_engine
        self._recovery_max_attempts = recovery_max_attempts
        self._context_overflow_hook = context_overflow_hook
        # One detector per invocation id (the turn scope). Detectors are stateful
        # (consecutive count) so they must not be shared across turns.
        self._detectors: dict[str, ToolCallLoopDetector] = {}
        # Recovery attempt state keyed by invocation id so a turn's per-strategy
        # budget is enforced and never grows unbounded.
        self._recovery_state: dict[str, RecoveryAttemptState] = {}

    # -- loop guard (after_tool) -----------------------------------------

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        if self._loop_detector_factory is None:
            return None
        # Never recurse on our own injected hard-stop response.
        if (
            isinstance(result, Mapping)
            and result.get("response_type") == LOOP_GUARD_RESPONSE_TYPE
        ):
            return None

        scope = _scope_key(tool_context)
        detector = self._detectors.get(scope)
        if detector is None:
            detector = self._loop_detector_factory()
            self._detectors[scope] = detector

        check = detector.check(_tool_name(tool), tool_args)
        if check.action == "ok":
            return None
        if check.action == "hard_escalation":
            # Replace the result with a stop directive. The real tool already
            # ran (after_tool fires post-execution); we surface a terminal,
            # model-visible message so the model stops re-issuing the identical
            # call instead of looping forever.
            return _hard_stop_response(_tool_name(tool), check)
        # soft_warning: preserve the real tool result and append a nudge so the
        # model is warned but the work is not discarded.
        return _soft_nudge_response(result, _tool_name(tool), check)

    # -- error recovery (on_model_error) ---------------------------------

    async def on_model_error_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
        error: Exception,
    ) -> LlmResponse | None:
        if self._recovery_engine is None:
            return None

        classified = ErrorClassifier.classify(error)
        if not isinstance(classified, RecoverableError):
            # Terminal error -> no retry; propagate (fail-open to TS runtime).
            return None

        # ContextOverflow (prompt_too_long) is classified but NOT retried here:
        # rewriting the in-flight request is PR13 (compaction) territory. Expose
        # a clean hook and otherwise propagate so we never infinite-retry an
        # over-long prompt at the model-error boundary.
        if classified.kind == ErrorKind.PROMPT_TOO_LONG:
            return await self._handle_context_overflow(classified, callback_context)

        scope = _scope_key(callback_context)
        state = self._recovery_state.get(scope)
        if state is not None and state.attempt_number >= self._recovery_max_attempts:
            # Recovery budget exhausted for this turn -> stop retrying.
            return None

        messages = _messages_from_request(llm_request)
        result, new_state = await self._recovery_engine.attempt_recovery(
            error=classified,
            messages=messages,
            session_key=_invocation_id(callback_context) or scope,
            turn_id=scope,
            state=state,
        )
        self._recovery_state[scope] = new_state
        if not result.success:
            return None

        # A strategy succeeded (e.g. RateLimit slept the Retry-After delay). Hand
        # the live flow a recovery LlmResponse so the run continues rather than
        # raising. We do not surface modified messages here (the ADK error
        # boundary substitutes a response, it does not rewrite the request); the
        # strategy's side effect (the backoff sleep) plus the continue signal is
        # the live behavior.
        return _recovery_response(result.strategy_name, classified.kind)

    async def _handle_context_overflow(
        self,
        classified: RecoverableError,
        callback_context: Any,
    ) -> LlmResponse | None:
        if self._context_overflow_hook is None:
            return None
        outcome = self._context_overflow_hook(classified, callback_context)
        if asyncio.iscoroutine(outcome) or isinstance(outcome, Awaitable):
            outcome = await outcome  # type: ignore[assignment]
        return outcome  # type: ignore[return-value]

    # -- cleanup ----------------------------------------------------------

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._detectors.pop(inv, None)
            self._recovery_state.pop(inv, None)


# -- helpers --------------------------------------------------------------


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else ""


def _scope_key(context: Any) -> str:
    inv = _invocation_id(context)
    return inv if inv else "__magi_resilience_global__"


def _invocation_id(context: Any) -> str | None:
    inv = getattr(context, "invocation_id", None)
    if isinstance(inv, str) and inv:
        return inv
    return None


def _hard_stop_response(tool_name: str, check: LoopCheckResult) -> dict[str, Any]:
    return {
        "response_type": LOOP_GUARD_RESPONSE_TYPE,
        "status": LOOP_GUARD_HARD_STATUS,
        "loop_action": "hard_escalation",
        "tool_name": tool_name,
        "consecutive_count": check.count,
        "frequency_count": check.frequency_count,
        "stop_directive": (
            f"Loop guard: the tool '{tool_name}' has been called with identical "
            f"arguments {check.count} times in a row. Stop repeating this call. "
            "Re-evaluate your approach or ask the user for clarification."
        ),
    }


def _soft_nudge_response(
    result: Any,
    tool_name: str,
    check: LoopCheckResult,
) -> dict[str, Any]:
    nudge = (
        f"Loop guard warning: '{tool_name}' has now been called with the same "
        f"arguments {check.count} times. If this is not making progress, change "
        "your approach instead of repeating the identical call."
    )
    # Preserve the real result. ADK after_tool requires a dict replacement; wrap
    # non-dict results under a stable key while keeping the nudge model-visible.
    if isinstance(result, Mapping):
        merged: dict[str, Any] = dict(result)
    else:
        merged = {"result": result}
    merged[LOOP_GUARD_SOFT_KEY] = nudge
    merged["loop_action"] = "soft_warning"
    return merged


def _messages_from_request(llm_request: Any) -> list[dict[str, object]]:
    """Best-effort extraction of message dicts from an ADK ``LlmRequest``.

    Recovery strategies operate on a list of ``{"role", "content"}`` dicts. The
    live ADK request carries ``contents`` (``google.genai`` ``Content``); we
    project a minimal text view. Strategies that need richer structure
    degrade gracefully (e.g. RateLimit ignores the body entirely).
    """
    contents = getattr(llm_request, "contents", None)
    if not contents:
        return []
    messages: list[dict[str, object]] = []
    for content in contents:
        role = getattr(content, "role", None) or "user"
        parts = getattr(content, "parts", None) or ()
        text_chunks: list[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                text_chunks.append(text)
        messages.append({"role": role, "content": "".join(text_chunks)})
    return messages


def _recovery_response(strategy_name: str, kind: ErrorKind) -> LlmResponse:
    return LlmResponse(
        custom_metadata={
            RECOVERY_METADATA_KEY: {
                "recovered": True,
                "strategy": strategy_name,
                "error_kind": kind.value,
            }
        },
    )


def build_resilience_plugin(
    *,
    loop_guard_enabled: bool,
    loop_guard_soft_threshold: int = 3,
    loop_guard_hard_threshold: int = 5,
    loop_guard_frequency_soft_threshold: int = 15,
    loop_guard_frequency_hard_threshold: int = 30,
    error_recovery_enabled: bool,
    recovery_max_attempts: int = 3,
    recovery_engine: RecoveryEngine | None = None,
    compact_llm_caller: LLMCompactCaller | None = None,
    context_overflow_hook: ContextOverflowHook | None = None,
) -> MagiResiliencePlugin | None:
    """Build the resilience plugin, or ``None`` when both features are OFF.

    Flag/threshold values are owned by ``magi_agent.config.env`` (single
    source); callers pass resolved values here so this module stays
    env-parsing-free. The loop guard wires the EXISTING ``ToolCallLoopDetector``
    and the recovery path wires the EXISTING ``RecoveryEngine`` (activation, not
    reimplementation).
    """
    if not loop_guard_enabled and not error_recovery_enabled:
        return None

    detector_factory: Callable[[], ToolCallLoopDetector] | None = None
    if loop_guard_enabled:
        def detector_factory() -> ToolCallLoopDetector:
            return ToolCallLoopDetector(
                soft_threshold=loop_guard_soft_threshold,
                hard_threshold=loop_guard_hard_threshold,
                frequency_soft_threshold=loop_guard_frequency_soft_threshold,
                frequency_hard_threshold=loop_guard_frequency_hard_threshold,
            )

    engine: RecoveryEngine | None = None
    if error_recovery_enabled:
        engine = recovery_engine or _default_recovery_engine(
            max_attempts=recovery_max_attempts,
            compact_llm_caller=compact_llm_caller,
        )

    return MagiResiliencePlugin(
        loop_detector_factory=detector_factory,
        recovery_engine=engine,
        recovery_max_attempts=recovery_max_attempts,
        context_overflow_hook=context_overflow_hook,
    )


def _default_recovery_engine(
    *,
    max_attempts: int,
    compact_llm_caller: LLMCompactCaller | None,
) -> RecoveryEngine:
    config = ErrorRecoveryConfig(
        recovery_enabled=True,
        max_recovery_attempts=max_attempts,
    )
    # Mirror engine.DEFAULT_STRATEGIES order but bind to the live config and the
    # real classifier-tier compaction caller when one is reachable (else the
    # ReactiveCompact stub is kept).
    strategies = (
        RateLimitStrategy(config),
        OutputEscalationStrategy(config),
        CollapseDrainStrategy(config),
        ReactiveCompactStrategy(config, llm_caller=compact_llm_caller),
        MediaRemovalStrategy(config),
        RecoveryMessageStrategy(config),
    )
    return RecoveryEngine(config, strategies=strategies)


__all__ = [
    "ContextOverflowHook",
    "LOOP_GUARD_HARD_STATUS",
    "LOOP_GUARD_RESPONSE_TYPE",
    "LOOP_GUARD_SOFT_KEY",
    "MagiResiliencePlugin",
    "RECOVERY_METADATA_KEY",
    "RESILIENCE_PLUGIN_NAME",
    "build_resilience_plugin",
    "DEFAULT_STRATEGIES",
]
