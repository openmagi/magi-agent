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

  PLUGIN ORDERING (intentional pre-emption): the live runner attaches the
  edit-retry-reflection plugin BEFORE this one
  (``local_runner.py``: ``[edit_retry_plugin, resilience_plugin]``). ADK's
  ``PluginManager._run_callbacks`` early-exits on the FIRST plugin whose
  ``after_tool_callback`` returns non-``None``. So on an edit-tool FAILURE the
  edit-retry plugin runs first and, while it has retry budget, returns its
  corrective hidden message — which means THIS loop guard's
  ``after_tool_callback`` does NOT run for that call, and the detector does not
  count it. This is deliberate: edit-retry "wins" on edit tools (a failed edit
  is a recoverable, distinct event, not a doom-loop). Once the edit-retry budget
  is exhausted it returns ``None`` and the loop guard resumes seeing those tool
  results, so repeated post-budget identical edits still trip the guard.
* ``on_model_error_callback`` — fires in ``base_llm_flow.py`` when the model
  call raises. **This is a substitute-the-response seam, NOT a retry seam.**
  Returning an ``LlmResponse`` here does NOT re-issue the model call: ADK treats
  a content-less ``LlmResponse`` as the (final) step result, so the turn ENDS
  with that response — no second model call happens. Genuine recovery therefore
  CANNOT live here; it lives at the run-invocation boundary
  (``magi_agent.cli.engine.MagiEngineDriver``), which catches a classified-
  retryable error, applies backoff, and RE-INVOKES a fresh ``run_async``.
  This callback is kept ONLY for classification/telemetry: it classifies the
  error (recording the kind) and otherwise returns ``None`` so the error
  PROPAGATES to the genuine retry seam. It deliberately does NOT fabricate a
  recovery ``LlmResponse`` (which would dishonestly end the turn while pretending
  to have recovered). For prompt_too_long it consults the
  ``context_overflow_hook`` seam (PR13 compaction territory) if one is wired,
  else propagates.
* ``after_run_callback`` — sweeps per-invocation state so detector/recovery
  state never grows unbounded across turns. The sweep is ALSO size-bounded
  (LRU-style cap) so detectors cannot leak across many turns whose
  ``after_run_callback`` never fires (e.g. a turn that raises).

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
from collections.abc import Awaitable, Callable, Iterator, Mapping, MutableMapping
from typing import Any

from google.adk.models import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.packs.context import PerInvocationState
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

# Marker on the (legacy) recovery LlmResponse custom_metadata. The genuine
# recovery seam no longer fabricates this response (see on_model_error_callback);
# kept for telemetry/back-compat and for callers that build a recovery response
# themselves.
RECOVERY_METADATA_KEY = "magi_error_recovery"

# Hard cap on per-turn state dicts. ``after_run_callback`` sweeps on the normal
# path, but does not fire when a turn raises; this cap is the backstop so
# detectors/recovery-state cannot leak across many failing turns.
_MAX_TRACKED_SCOPES = 256

# Hook signature for PR13 context-overflow / compaction wiring. Given the
# classified overflow error it may return an LlmResponse to substitute, else
# None (propagate / fail-open).
ContextOverflowHook = Callable[
    [RecoverableError, Any], "LlmResponse | None | Awaitable[LlmResponse | None]"
]


class _ScopedObjectView(MutableMapping[str, Any]):
    """Live write-through ``scope -> object`` view over a PerInvocationState bucket.

    Phase 5 / S-C moves the resilience per-invocation objects (the loop detector
    and the recovery-attempt state) out of plugin-private dicts and into the
    runtime-owned :class:`PerInvocationState` opaque-object store. Each underlying
    object is keyed ``(scope, name)`` within that store; this view fixes ``name``
    and exposes the SAME ``scope -> object`` mapping surface the legacy
    ``self._detectors`` / ``self._recovery_state`` dicts had — item assignment,
    membership, iteration, pop — so existing callers/tests operate unchanged while
    there is exactly one owner of the mutable state. Writes route through
    ``set_object`` so the LRU bound is enforced on insert.
    """

    def __init__(self, state: PerInvocationState, name: str) -> None:
        self._state = state
        self._name = name

    def __getitem__(self, scope: str) -> Any:
        sentinel = object()
        value = self._state.peek_object(scope, self._name, default=sentinel)
        if value is sentinel:
            raise KeyError(scope)
        return value

    def __setitem__(self, scope: str, value: Any) -> None:
        self._state.set_object(scope, self._name, value)

    def __delitem__(self, scope: str) -> None:
        if scope not in self:
            raise KeyError(scope)
        bucket = self._state._objects.get(scope)
        if bucket is not None:
            bucket.pop(self._name, None)
            if not bucket:
                self._state._objects.pop(scope, None)

    def __contains__(self, scope: object) -> bool:
        if not isinstance(scope, str):
            return False
        return self._state.peek_object(scope, self._name) is not None

    def __iter__(self) -> Iterator[str]:
        return iter(
            [
                scope
                for scope, bucket in dict(self._state._objects).items()
                if self._name in bucket
            ]
        )

    def __len__(self) -> int:
        return sum(
            1 for bucket in self._state._objects.values() if self._name in bucket
        )

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"_ScopedObjectView({dict(self)!r})"


class MagiResiliencePlugin(BasePlugin):
    """ADK plugin activating loop guard + error recovery on the live runner.

    Both subsystems are independently flag-gated by the builder: passing
    ``loop_detector_factory=None`` disables the loop guard, ``recovery_engine=
    None`` disables recovery. When both are ``None`` the plugin is inert and the
    builder returns ``None`` instead of attaching it (zero regression).

    Phase 5 / S-C: the per-invocation loop detectors and recovery-attempt state
    live in a runtime-owned :class:`PerInvocationState` (``self._default_state``)
    rather than private dicts, so a user-authored equivalent loop guard gets the
    same state struct off the typed context. The legacy ``self._detectors`` /
    ``self._recovery_state`` mappings are preserved as live write-through views
    over that state. The ADK callbacks below feed the default state; the
    dispatcher supplies a context-owned state in Phase 6.
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
        # Runtime-owned per-invocation state (the ONE owner of the mutable loop
        # detectors + recovery-attempt state). Replaces the old plugin-private
        # ``self._detectors`` / ``self._recovery_state`` dicts; the legacy
        # attributes are now live write-through views over this struct. The LRU
        # bound (max_scopes) preserves the old ``_MAX_TRACKED_SCOPES`` backstop:
        # the sweep in ``after_run_callback`` is the normal path, but it does NOT
        # fire if a turn RAISES, so the bound prevents an unbounded leak across
        # many failing turns.
        self._default_state = PerInvocationState(max_scopes=_MAX_TRACKED_SCOPES)

    @property
    def _detectors(self) -> MutableMapping[str, ToolCallLoopDetector]:
        """Live ``scope -> ToolCallLoopDetector`` view over the runtime state."""
        return _ScopedObjectView(self._default_state, "loop_detector")

    @property
    def _recovery_state(self) -> MutableMapping[str, RecoveryAttemptState]:
        """Live ``scope -> RecoveryAttemptState`` view over the runtime state."""
        return _ScopedObjectView(self._default_state, "recovery_state")

    # -- loop guard (after_tool) -----------------------------------------

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        # ADK-callback path: drive the loop guard against the plugin's runtime-
        # owned default state. The typed-context path supplies a context-owned
        # state instead (Phase 6).
        return self.guard_with_state(
            state=self._default_state,
            tool=tool,
            tool_args=tool_args,
            tool_context=tool_context,
            result=result,
        )

    def guard_with_state(
        self,
        *,
        state: PerInvocationState,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> dict[str, Any] | None:
        """Pure loop-guard decision over a runtime-owned :class:`PerInvocationState`.

        Replaces the instance-private ``self._detectors`` mutation: the per-turn
        ``ToolCallLoopDetector`` is stored as an opaque object on ``state`` (keyed
        by the invocation scope), created lazily via the factory. Escalation is
        byte-identical to the pre-migration ``after_tool_callback``."""
        if self._loop_detector_factory is None:
            return None
        # Never recurse on our own injected hard-stop response.
        if (
            isinstance(result, Mapping)
            and result.get("response_type") == LOOP_GUARD_RESPONSE_TYPE
        ):
            return None

        scope = _scope_key(tool_context)
        detector = state.get_object(scope, "loop_detector", self._loop_detector_factory)

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
        # HONESTY NOTE: this is a substitute-the-response seam, not a retry seam.
        # Returning a content-less LlmResponse here would END the turn (ADK
        # treats it as the final step) while pretending recovery happened — no
        # second model call. So this callback NEVER fabricates a recovery
        # response. Genuine retry (backoff + re-invoke run_async) lives at the
        # run-invocation boundary in ``cli.engine.MagiEngineDriver``. Here we
        # only CLASSIFY (telemetry) and, for prompt_too_long, consult the PR13
        # context-overflow hook. Everything else returns None so the error
        # propagates to the genuine retry seam.
        if self._recovery_engine is None:
            return None

        classified = ErrorClassifier.classify(error)
        if not isinstance(classified, RecoverableError):
            # Terminal error -> propagate to the outer runtime boundary.
            return None

        # ContextOverflow (prompt_too_long): classified but NOT retried here.
        # Rewriting the in-flight request is PR13 (compaction) territory; expose
        # the hook seam and otherwise propagate.
        if classified.kind == ErrorKind.PROMPT_TOO_LONG:
            return await self._handle_context_overflow(classified, callback_context)

        # Retryable (e.g. rate_limit): record the classification for telemetry,
        # then PROPAGATE (return None). The run-invocation retry wrapper applies
        # the backoff and re-invokes the model — doing it here would silently end
        # the turn. We touch _recovery_state only to keep the keyed dict bounded.
        scope = _scope_key(callback_context)
        self._note_recovery_classification(scope, classified.kind)
        return None

    def _note_recovery_classification(self, scope: str, kind: ErrorKind) -> None:
        """Record that ``scope`` saw a retryable model error (telemetry only).

        No recovery is performed here (see ``on_model_error_callback``). We keep a
        per-scope RecoveryAttemptState as an opaque object on the runtime-owned
        state so it stays bounded (the sweep in ``after_run_callback`` / the
        PerInvocationState LRU cap evicts it).
        """
        prev = self._default_state.peek_object(scope, "recovery_state")
        new_state = (prev or RecoveryAttemptState()).model_copy(
            update={"attempt_number": (prev.attempt_number if prev else 0) + 1}
        )
        self._default_state.set_object(scope, "recovery_state", new_state)

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
        # Normal-path sweep. NOTE: this callback does NOT fire if the turn
        # RAISES (the flow propagates the exception before after_run), so it
        # cannot be the only defense against state leak — the LRU bound enforced
        # by ``PerInvocationState`` (on every insert) is the backstop. Clearing
        # the invocation drops BOTH the loop detector and the recovery state for
        # that scope, equivalent to the old paired ``pop`` of both dicts.
        inv = getattr(invocation_context, "invocation_id", None)
        if isinstance(inv, str) and inv:
            self._default_state.clear_invocation(inv)


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


def _recovery_response(strategy_name: str, kind: ErrorKind) -> LlmResponse:
    """Build a recovery-marked LlmResponse.

    LEGACY / NOT used by the live path: the genuine recovery seam lives at the
    run invocation (``cli.engine.MagiEngineDriver``), and
    ``on_model_error_callback`` deliberately no longer fabricates this response
    (it would dishonestly end the turn). Retained for telemetry/back-compat and
    for callers (e.g. a future PR13 hook) that explicitly want to substitute a
    recovery response.
    """
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
    # Mirror engine.DEFAULT_STRATEGIES order but bind to the live config.
    # HONESTY NOTE: ``compact_llm_caller`` is NOT wired by the live builder
    # (``build_resilience_plugin`` is called from ``local_runner`` without it),
    # so ``ReactiveCompactStrategy`` falls back to ``StubLLMCompactCaller`` in
    # production — i.e. NO real LLM-backed compaction yet. This is acceptable
    # today because prompt_too_long / context-overflow is NOT routed through the
    # run-invocation retry wrapper (it is deferred to the PR13 compaction seam),
    # so ReactiveCompact does not actually run on the live retry path. A real
    # classifier-tier compaction caller is PR13 work.
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
