"""Live context-compaction wiring for the ADK Runner (PR13).

magi-agent already ships a fully-built context-lifecycle boundary in
``magi_agent.runtime.context_lifecycle`` — ``ContextLifecycleBoundary``
implements a dual-threshold (token-estimate OR event-count) compaction decision
that keeps the recent tail of events and records a ref/digest-based provenance
trail. That logic was, until now, *dormant*: it had no live caller on the model
loop, so a real over-budget context was sent to the model unmodified.

This module *activates* it on the live ADK turn engine.

Live integration point
-----------------------
ADK's ``Runner.run_async`` builds an ``LlmRequest`` (with ``.contents`` — the
list of ``google.genai.types.Content`` sent to the model) and, just before
calling ``llm.generate_content_async``, runs every plugin's
``before_model_callback`` against that *same* request object
(``flows/llm_flows/base_llm_flow.py:_handle_before_model_callback`` ->
``PluginManager.run_before_model_callback``). A plugin that mutates
``llm_request.contents`` in place and returns ``None`` therefore changes exactly
what the model receives — this is the idiomatic ADK seam (ADK's own
``ContextFilterPlugin`` reduces context the same way).

How compaction reduces what the model sees
-------------------------------------------
On each model call this plugin:

1. estimates the token cost of each ``Content`` (improved best-effort tokenizer
   from ``magi_agent.shared.token_estimation`` with a char/4 fallback),
2. maps the contents to ``ContextLifecycleEvent`` refs and feeds them to
   ``ContextLifecycleBoundary.compact_if_needed`` — *reusing* the existing
   boundary as the authoritative threshold + tail-keep decision engine,
3. when the boundary returns ``status == "compacted"``, trims
   ``llm_request.contents`` down to the recent tail the boundary preserved
   (last ``tail_events``), with split-point adjustment so a tail that begins
   with an orphaned tool/function response is widened to include its call.

Under threshold -> the boundary returns ``unchanged`` and contents are left
exactly as-is. Flag OFF -> the plugin is never attached (see
``build_context_compaction_plugin``) so there is zero behavioural change.

Boundary store note
-------------------
``compact_if_needed`` requires a *local-fake* session service
(``openmagi_local_fake_provider is True``) and a ``QueryState`` whose
``session_id`` matches the session, and it appends a provenance event. We give
the boundary its own in-process :class:`WorkspaceSessionService` (the same
local-fake provider the live local runner already constructs) and a synthesized
matching ``QueryState`` so the decision path runs end-to-end without needing an
external ref/digest store. The boundary's ref/digest provenance is used purely
as the *decision* signal here; the actual reduction applied to the model request
is the tail-keep the boundary computed. Restoration of behind-the-boundary refs
(``restore_context``) is out of scope for the before-model seam.

PR12 note
---------
A sibling effort (PR12, not in this branch) introduces a ``MagiResiliencePlugin``
shim with a ``context_overflow_hook``. This module implements compaction via its
own ``before_model_callback`` plugin so it does not block on PR12; when PR12
lands, that shared shim can absorb this plugin's ``before_model_callback``.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from magi_agent.runtime.context_lifecycle import (
    ContextLifecycleBoundary,
    ContextLifecycleConfig,
    ContextLifecycleEvent,
)

logger = logging.getLogger(__name__)

CONTEXT_COMPACTION_PLUGIN_NAME = "magi_context_compaction_plugin"

# A safe, deterministic summary ref/digest the boundary uses for its decision.
# These satisfy ``validate_safe_ref`` / ``validate_digest`` and never carry any
# real transcript content (the before-model seam compacts what the model sees;
# it does not synthesise a model-visible summary string).
_COMPACTION_SUMMARY_REF = "summary:compact:before-model"
_COMPACTION_SUMMARY_DIGEST = "sha256:" + "0" * 64

# Stable session-state key under which the after-model capture stashes the REAL
# prompt-token count of the just-completed model call (G2). ADK CallbackContext
# state is delta-backed by session.state, so a write here in after_model_callback
# (turn N) is readable by the NEXT turn's before_model_callback. The value
# therefore lags by one turn (it is the prompt size that was actually sent to the
# model), which is the intended budget signal — NOT the current pre-call size.
REAL_PROMPT_TOKENS_STATE_KEY = "magi_compaction_real_prompt_tokens"

# G5/G6 cross-turn session-state keys (same delta-backed seam as
# REAL_PROMPT_TOKENS_STATE_KEY). ``ANCHOR_SUMMARY_STATE_KEY`` carries the
# marker-stripped body of the LAST injected summary so the NEXT compaction can
# feed it back as a previous-summary anchor (anchored/incremental refinement).
# ``SUMMARY_FAILURE_COUNT_STATE_KEY`` carries the consecutive summary-failure
# count for the circuit breaker (reset to 0 on any successful summary).
ANCHOR_SUMMARY_STATE_KEY = "magi_compaction_anchor_summary"
SUMMARY_FAILURE_COUNT_STATE_KEY = "magi_compaction_summary_failure_count"

# WS4: the PROACTIVE recovery (tiers 6-7) tier-7 summarizer-failure count. NOTE:
# unlike the G6 counter it does NOT reset on a successful summary, so it is
# CUMULATIVE per session (a reset-on-success to fully mirror G6 is a follow-up for
# when a real summarizer replaces the default hermetic stub, which never raises).
# This is INDEPENDENT of ``SUMMARY_FAILURE_COUNT_STATE_KEY`` (G6): the G6
# counter is gated by ``_summarize_enabled`` and stays 0 when summarize is OFF,
# which the proactive path requires (it does not co-enable G1/G6). Persisted on
# the same delta-backed ``callback_context.state`` seam so a tier-7 failure on one
# turn trips the breaker on a later turn.
PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY = (
    "magi_compaction_proactive_summary_failure_count"
)

# G5: leading marker on the injected summary Content (built in
# ``_build_summary_head``). Shared so the injection and the dropped-prefix
# anchor-recognition stay in sync; any change here updates both sites.
_SUMMARY_MARKER = "[Previous conversation summary]\n\n"

# G5: hard cap on the stored anchor body length so incremental refinement cannot
# let the anchor grow without bound turn-over-turn (the anchored prompt asks the
# model to "keep it concise" but does not guarantee a length). Purely defensive.
_ANCHOR_SUMMARY_MAX_CHARS = 24_000

# Lower bound on the event-count threshold so a small tail does not force a
# spurious event-count breach. The token threshold is the primary signal at the
# before-model seam; the event-count threshold mirrors the boundary default.
_EVENT_COUNT_THRESHOLD_DEFAULT = 128

# G4: deterministic tool-output prune pre-tier. The compact placeholder that
# replaces an OLD ``function_response`` payload when it is cleared to save
# context (mirrors OpenCode / claude-code content-clear semantics). The part is
# never deleted — only its ``response`` payload is swapped for this — so the
# function_call/function_response pairing stays valid.
_PRUNED_TOOL_OUTPUT_PLACEHOLDER: dict[str, str] = {
    "pruned": "[old tool output cleared to save context]"
}

# G4 default knobs (additive, default-OFF). PRUNE_PROTECT is the most-recent
# tool-output tokens to protect (Layer-2 token tail); PRUNE_MINIMUM is the
# minimum total freed tokens to commit a prune (no churn for tiny savings).
_PRUNE_PROTECT_DEFAULT = 40_000
_PRUNE_MINIMUM_DEFAULT = 20_000

# G1 default summarize timeout (seconds) for the session-model summary call.
_SUMMARY_TIMEOUT_DEFAULT = 30.0

# G1 transcript cap: the rendered dropped-prefix transcript is bounded to this
# many chars before being formatted into the summary prompt, so the summary call
# stays sane regardless of how large the dropped prefix is.
_SUMMARY_TRANSCRIPT_MAX_CHARS = 24_000
# Per-Content segment cap (mirrors AutoCompactionEngine._format_conversation's
# per-message 2000-char cap).
_SUMMARY_SEGMENT_MAX_CHARS = 2_000
# Per-result payload cap inside a rendered [tool_result ...] descriptor.
_SUMMARY_RESULT_PAYLOAD_MAX_CHARS = 500
# Per-call args cap inside a rendered [tool_call ...] descriptor.
_SUMMARY_CALL_ARGS_MAX_CHARS = 200

# Per-result minimum (LOWER clear threshold): only OLD function_response
# payloads larger than this (in estimated tokens) are worth clearing — clearing
# a trivial output frees nothing once the placeholder is counted, so they are
# left untouched. (Distinct from content_replacement's MAX_RESULT_TOKENS, which
# is an UPPER snip threshold for a different, dict-based engine.)
_PRUNE_PER_RESULT_MINIMUM = 1_000


class MagiContextCompactionPlugin(BasePlugin):
    """ADK plugin that compacts the outgoing context when over budget.

    Reuses :class:`ContextLifecycleBoundary` as the threshold/tail decision
    engine. The plugin only adapts the ADK ``LlmRequest`` boundary to it and
    applies the resulting tail-keep to ``llm_request.contents``.
    """

    def __init__(
        self,
        *,
        token_threshold: int,
        tail_events: int,
        event_count_threshold: int = _EVENT_COUNT_THRESHOLD_DEFAULT,
        name: str = CONTEXT_COMPACTION_PLUGIN_NAME,
        real_tokens_enabled: bool = False,
        real_tokens_pct: float = 0.75,
        output_reserve: int = 8_000,
        tool_prune_enabled: bool = False,
        prune_protect: int = _PRUNE_PROTECT_DEFAULT,
        prune_minimum: int = _PRUNE_MINIMUM_DEFAULT,
        summarize_enabled: bool = False,
        summary_model: str | None = None,
        summary_timeout: float = _SUMMARY_TIMEOUT_DEFAULT,
        anchored_summary_enabled: bool = False,
        summary_max_failures: int = 3,
        manual_enabled: bool = False,
        proactive_recovery_enabled: bool = False,
        proactive_critical_pct: float = 0.90,
    ) -> None:
        super().__init__(name)
        if token_threshold < 1:
            raise ValueError("token_threshold must be >= 1")
        if tail_events < 1:
            raise ValueError("tail_events must be >= 1")
        if event_count_threshold < 1:
            raise ValueError("event_count_threshold must be >= 1")
        if real_tokens_enabled and not (0.0 < real_tokens_pct <= 1.0):
            raise ValueError("real_tokens_pct must be in the range (0, 1]")
        if output_reserve < 0:
            raise ValueError("output_reserve must be >= 0")
        if prune_protect < 1:
            raise ValueError("prune_protect must be >= 1")
        if prune_minimum < 1:
            raise ValueError("prune_minimum must be >= 1")
        if summarize_enabled and summary_timeout <= 0:
            raise ValueError("summary_timeout must be > 0")
        if summary_max_failures < 0:
            raise ValueError("summary_max_failures must be >= 0")
        if proactive_recovery_enabled and not (0.0 < proactive_critical_pct <= 1.0):
            raise ValueError("proactive_critical_pct must be in the range (0, 1]")
        self.token_threshold = token_threshold
        self.tail_events = tail_events
        self.event_count_threshold = event_count_threshold
        # G4 deterministic tool-output prune pre-tier (default-OFF). When OFF the
        # prune is never attempted and ``_trim_request`` is byte-identical to the
        # Phase-1 path (no contents mutation).
        self._tool_prune_enabled = bool(tool_prune_enabled)
        self._prune_protect = prune_protect
        self._prune_minimum = prune_minimum
        # G2 real-token accounting (default-OFF). When OFF every code path below
        # is byte-identical to the estimate + fixed-threshold behaviour.
        self._real_tokens_enabled = bool(real_tokens_enabled)
        self._real_tokens_pct = real_tokens_pct
        self._output_reserve = output_reserve
        # G1 LLM summary injection on the tail-drop (default-OFF). When OFF the
        # new summary block in ``_apply_tail_trim`` is skipped entirely and the
        # existing pure tail-drop runs verbatim (no provider/model resolution, no
        # LLM call).
        self._summarize_enabled = bool(summarize_enabled)
        self._summary_model_override = summary_model or None
        self._summary_timeout = summary_timeout
        # G5/G6 anchored summary + circuit breaker (default-OFF / default-3).
        # Anchoring is effective only when BOTH summarize and anchored are ON; the
        # breaker is folded under summarize (active whenever summarize is ON and
        # max > 0). OFF / default => Phase-3 behaviour byte-identical.
        self._anchored_enabled = bool(anchored_summary_enabled)
        self._summary_breaker_max = summary_max_failures
        # G7 manual /compact force (default-OFF). When OFF ``_trim_request`` never
        # imports/calls ``consume_manual_compaction`` and is byte-identical to the
        # Phase-4 path. When ON, a pending one-shot signal forces a tail-drop on
        # this model turn regardless of threshold.
        self._manual_enabled = bool(manual_enabled)
        # READ-side stash (mirrors the G2 real-token stash): the prior anchor body
        # + consecutive-failure count read off ``callback_context.state`` at the
        # start of ``before_model_callback`` and consumed by ``_trim_request``.
        # Cleared in ``_trim_request``'s finally to avoid cross-turn leakage.
        self._pending_anchor_summary: str | None = None
        self._pending_summary_failures: int = 0
        # PRODUCE-side fields: ``_build_summary_head`` records what it produced this
        # turn (a freshly generated, marker-stripped anchor) or whether the summary
        # attempt failed; ``before_model_callback`` writes them to state AFTER
        # ``apply_before_model`` returns (it has the callback_context handle;
        # ``_trim_request`` does not). Reset at the start of the next turn's READ.
        self._produced_summary: str | None = None
        self._produced_summary_failed: bool = False
        # Per-before-model stash of the real prompt-token count + model id, read
        # off ``callback_context.state`` and threaded to ``_trim_request`` without
        # touching the ``CompactionCapability.trim(llm_request)`` signature. Set at
        # the start of ``before_model_callback`` and cleared in ``_trim_request``'s
        # finally, so there is no cross-turn instance leakage.
        self._pending_real_prompt_tokens: int | None = None
        self._pending_model: str | None = None
        # WS4: proactive recovery (tiers 6-7). Default-OFF; when OFF the
        # ``apply_before_model`` gate is never called and ``before_model_callback``
        # never reads/writes the proactive breaker state (byte-identical).
        self._proactive_recovery_enabled = bool(proactive_recovery_enabled)
        self._proactive_critical_pct = proactive_critical_pct
        # READ-side stash of the prior turn's proactive failure count (mirrors the
        # G6 ``_pending_summary_failures`` split). PRODUCE-side flag is set when the
        # tier-7 summarizer raises this turn; ``before_model_callback`` persists the
        # incremented count AFTER ``apply_before_model`` returns (it holds the
        # callback_context handle; the gate does not).
        self._pending_proactive_summary_failures: int = 0
        self._produced_proactive_summary_failed: bool = False
        # Optional injectable tier-7 caller (tests inject a raising/counting stub).
        # When None the proactive path builds a deterministic ``StubLLMCompactCaller``
        # (no network) so the ON path is hermetic; a real summarizer model is a
        # separate sign-off-gated activation, not part of this default-OFF wiring.
        self._proactive_llm_caller: Any | None = None
        # SC-7 telemetry: the last proactive tier that fired (collapse/compact/
        # failsafe) plus before/after token counts, exposed for tests/observability.
        self._last_proactive_record: dict[str, Any] | None = None
        self._boundary = ContextLifecycleBoundary()
        # Lazily-built, then cached: the boundary only needs a constant local-fake
        # session_service + session + QueryState for its decision path. Building
        # them fresh on every over-budget model call (potentially many per turn)
        # is wasteful, so we cache them on the instance. See ``_decision_inputs``
        # for how unbounded provenance-event growth on the reused session is
        # prevented.
        self._decision_cache: tuple[Any, Any, Any] | None = None
        self._config = ContextLifecycleConfig(
            enabled=True,
            localFakeCompactionEnabled=True,
            tokenEstimateThreshold=token_threshold,
            eventCountThreshold=event_count_threshold,
            recentEventCount=tail_events,
        )

    async def before_model_callback(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        """Trim ``llm_request.contents`` to the recent tail when over budget.

        Mutates the request in place and returns ``None`` so the (possibly
        reduced) request proceeds to the model. Never raises into the model loop:
        any unexpected failure leaves the contents untouched (fail-open, no
        regression vs. the no-plugin path).

        Legacy ADK seam (S-D): builds a typed context carrying this plugin's own
        :class:`CompactionCapability` and delegates to ``apply_before_model`` so
        every path (ADK callback, typed-context dispatcher, user pack) flows
        through the one shared decision body. Behavior is byte-identical to the
        pre-migration callback.
        """
        from magi_agent.packs.context import ControlPlaneContext

        # G2: when the real-token path is ON, read the prior turn's real
        # prompt-token count off the (delta-backed) session state and resolve the
        # model id from the outgoing request, stashing both on the instance for
        # ``_trim_request``. Guarded so flag-OFF never reads/writes state and the
        # stash stays None (estimate path verbatim). Fail-open on any read error.
        if self._real_tokens_enabled:
            self._pending_real_prompt_tokens = _read_real_prompt_tokens(
                callback_context
            )
            self._pending_model = _resolve_model_id(llm_request)
        else:
            self._pending_real_prompt_tokens = None
            self._pending_model = None

        # G5/G6 READ: when summarize is ON, read the prior anchor + consecutive
        # failure count off the (delta-backed) session state and reset the
        # PRODUCE-side fields. Guarded by ``_summarize_enabled`` so a flag-OFF
        # (no-summary) plugin NEVER touches state — the no-regression contract.
        # The anchor is only meaningful when anchoring is also ON, but reading it
        # here keeps the seam in one place; ``_build_summary_head`` consults
        # ``_anchored_enabled`` before using it. Fail-open via the helpers.
        if self._summarize_enabled:
            # Anchor is read ONLY when anchoring is on (the anchored-OFF path must
            # not read/write the anchor key); the failure count is always read
            # because the circuit breaker is folded under ``_summarize_enabled``.
            self._pending_anchor_summary = (
                _read_anchor_summary(callback_context)
                if self._anchored_enabled
                else None
            )
            self._pending_summary_failures = _read_summary_failures(callback_context)
            self._produced_summary = None
            self._produced_summary_failed = False
        else:
            self._pending_anchor_summary = None
            self._pending_summary_failures = 0
            self._produced_summary = None
            self._produced_summary_failed = False

        # WS4 READ: when proactive recovery is ON, read the prior turn's proactive
        # tier-7 failure count off the (delta-backed) state and reset the
        # PRODUCE-side flag. This mirrors the G6 split exactly and is the ONLY frame
        # with the ``callback_context`` handle (``apply_before_model`` has none).
        # Guarded so flag-OFF NEVER touches state (SC-5 byte-identity).
        if self._proactive_recovery_enabled:
            self._pending_proactive_summary_failures = (
                _read_proactive_summary_failures(callback_context)
            )
            self._produced_proactive_summary_failed = False
        else:
            self._pending_proactive_summary_failures = 0
            self._produced_proactive_summary_failed = False

        ctx = ControlPlaneContext.minimal(compaction=CompactionCapability(self))
        result = await self.apply_before_model(ctx, llm_request=llm_request)

        # G5/G6 WRITE: persist what this turn produced to the (delta-backed) state
        # so the next turn's READ sees the updated anchor / failure count. Only the
        # summarize path produces anything; flag-OFF leaves state untouched. All
        # writes are fail-open and never alter the model-loop return contract.
        if self._summarize_enabled:
            if self._produced_summary is not None:
                # Success always resets the breaker; the anchor is persisted ONLY
                # when anchoring is on, so the anchored-OFF path never writes it.
                updates: dict[str, object] = {SUMMARY_FAILURE_COUNT_STATE_KEY: 0}
                if self._anchored_enabled:
                    updates[ANCHOR_SUMMARY_STATE_KEY] = self._produced_summary
                _write_compaction_state(callback_context, updates)
            elif self._produced_summary_failed:
                _write_compaction_state(
                    callback_context,
                    {
                        SUMMARY_FAILURE_COUNT_STATE_KEY: (
                            self._pending_summary_failures + 1
                        )
                    },
                )

        # WS4 WRITE: persist the proactive tier-7 failure increment so the next
        # turn's READ trips the breaker after ``summary_max_failures`` consecutive
        # failures. Under the SAME default-OFF guard as the G6 WRITE above, so
        # flag-OFF leaves state untouched (SC-5).
        if (
            self._proactive_recovery_enabled
            and self._produced_proactive_summary_failed
        ):
            _write_compaction_state(
                callback_context,
                {
                    PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY: (
                        self._pending_proactive_summary_failures + 1
                    )
                },
            )
        return result

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: Any,
    ) -> None:
        """Capture the REAL prompt-token count of the just-completed model call.

        Only active when the real-token path is ON. Reads
        ``llm_response.usage_metadata.prompt_token_count`` via the shared
        duck-typed extractor and stashes it on ``callback_context.state`` under
        :data:`REAL_PROMPT_TOKENS_STATE_KEY`, where the next turn's
        ``before_model_callback`` reads it. Pure observer: never mutates the
        response, always returns ``None``, fully fail-open (a missing/odd
        usage-metadata or state simply records nothing — the decision then falls
        back to the estimate path). Flag-OFF: writes NOTHING to state.
        """
        if not self._real_tokens_enabled:
            return None
        try:
            from magi_agent.shared.usage_metadata import prompt_tokens_from_response

            tokens = prompt_tokens_from_response(llm_response)
            if tokens is None:
                return None
            state = getattr(callback_context, "state", None)
            if state is None:
                return None
            state[REAL_PROMPT_TOKENS_STATE_KEY] = tokens
        except Exception:
            # Observer must never break the model loop.
            return None
        return None

    async def apply_before_model(self, ctx: Any, *, llm_request: Any) -> None:
        """Typed-context entry point (S-D): apply the compaction decision exposed
        on ``ctx.compaction`` (a :class:`CompactionCapability`). Falls back to this
        plugin's own capability when the context carries none (pre-dispatcher call
        sites). The control receives only this narrow capability — the
        ``ContextLifecycleBoundary`` + ``WorkspaceSessionService`` stay encapsulated
        behind it, so a user pack can supply an equivalent decision with no
        privileged service plumbing.
        """
        cap = getattr(ctx, "compaction", None) or CompactionCapability(self)
        await cap.trim(llm_request)
        # WS4 gate: runs on EVERY ``_trim_request`` exit path (including the
        # ``<= tail_events`` early-return and the two no-compact ``return None``
        # paths), because it sees the FINAL ``llm_request.contents`` here at the
        # apply_before_model seam. Flag-OFF skips it entirely (byte-identical).
        if self._proactive_recovery_enabled:
            await self._maybe_proactive_recover(llm_request)
        return None

    async def _maybe_proactive_recover(self, llm_request: Any) -> None:
        """Token gate: escalate when the final contents still exceed ``crit*W``.

        Owns its OWN try/except because it runs OUTSIDE ``_trim_request``'s
        fail-open try (the gate is one frame up, in ``apply_before_model``). On any
        unexpected failure the contents are left at the post-``cap.trim()`` state
        (fail-open, no worse than today). Resolves the model DIRECTLY (never reads
        ``self._pending_model``, which is None here: it is cleared in
        ``_trim_request``'s finally and only ever set under the real-token path).
        """
        try:
            contents = list(getattr(llm_request, "contents", None) or [])
            if not contents:
                return None
            model = _resolve_model_id(llm_request)
            budget = int(_window_for_model(model) * self._proactive_critical_pct)
            if budget < 1:
                return None
            if _estimate_contents_tokens(contents) <= budget:
                # Under CRITICAL: no escalation, no strategy construction (SC-6).
                return None
            await self._proactive_recover(llm_request, budget, model)
        except Exception:
            # The new seam is outside ``_trim_request``'s try, so guard it here.
            return None
        return None

    async def _proactive_recover(
        self, llm_request: Any, budget: int, model: str | None
    ) -> None:
        """Tiers 6-7 escalation: collapse-drain -> reactive-compact -> fail-safe.

        Each strategy is invoked AT MOST ONCE per model turn by construction (one
        call each in this body); that single-invocation is what bounds cost (SC-6),
        not ``RecoveryAttemptState`` (dormant here because no ``state`` is passed).
        Decoupled from ``MAGI_ERROR_RECOVERY_ENABLED`` on purpose: that flag governs
        the REACTIVE error-recovery plugin; the proactive path owns its own config.
        """
        from magi_agent.runtime.error_recovery.strategies.collapse_drain import (
            CollapseDrainStrategy,
        )
        from magi_agent.runtime.error_recovery.strategies.reactive_compact import (
            ReactiveCompactStrategy,
            StubLLMCompactCaller,
        )
        from magi_agent.runtime.error_recovery.types import ErrorRecoveryConfig

        before = _estimate_contents_tokens(list(llm_request.contents))
        cfg = ErrorRecoveryConfig(recovery_enabled=True, max_collapse_fraction=0.2)
        contents: list[Any] = list(llm_request.contents)

        # Tier 6: collapse-drain (drop oldest MIDDLE rounds, keep first + last).
        res6 = await CollapseDrainStrategy(cfg).recover(
            _make_proactive_recovery_context(contents_to_msgs(contents))
        )
        if res6.success and res6.modified_messages is not None:
            cand = msgs_to_contents(res6.modified_messages, original=contents)
            if _estimate_contents_tokens(cand) <= budget:
                llm_request.contents = cand
                self._record_proactive_tier("collapse", before, cand)
                return None
            contents = cand  # keep the partial reduction, continue to tier 7

        # Tier 7: reactive-compact (synthetic summary + last message). The breaker
        # is checked FIRST: if the prior-turn count (stashed by the READ seam) is at
        # the cap, skip the LLM call entirely and go straight to the fail-safe (E7).
        if self._pending_proactive_summary_failures < self._summary_breaker_max:
            raised = {"value": False}

            class _RecordingCaller:
                def __init__(self, inner: Any) -> None:
                    self._inner = inner

                async def compact(self, messages_text: str, prompt: str) -> str:
                    try:
                        return await self._inner.compact(messages_text, prompt)
                    except Exception:
                        raised["value"] = True
                        raise

            inner_caller = self._proactive_llm_caller or StubLLMCompactCaller()
            caller = _RecordingCaller(inner_caller)
            res7 = await ReactiveCompactStrategy(cfg, caller).recover(
                _make_proactive_recovery_context(contents_to_msgs(contents))
            )
            if res7.success and res7.modified_messages is not None:
                cand = msgs_to_contents(res7.modified_messages, original=contents)
                if _estimate_contents_tokens(cand) <= budget:
                    llm_request.contents = cand
                    self._record_proactive_tier("compact", before, cand)
                    return None
                contents = cand
            elif raised["value"]:
                # A real summarizer FAILURE (not a <2-msg no-op): set the
                # PRODUCE-side flag; ``before_model_callback`` persists the
                # incremented count after ``apply_before_model`` returns.
                self._produced_proactive_summary_failed = True

        # Fail-safe: deterministic truncation with continuity (always reduces or is
        # provably minimal, never raises).
        cand = deterministic_truncate(contents, budget, model)
        llm_request.contents = cand
        self._record_proactive_tier("failsafe", before, cand)
        return None

    def _record_proactive_tier(
        self, tier: str, tokens_before: int, contents: list[Any]
    ) -> None:
        """Record which proactive tier fired + tokens freed (SC-7).

        Plain string keys (``proactive_collapse_applied`` / ``proactive_compact_
        applied`` / ``proactive_failsafe_applied``) for continuity with the dormant
        hook's vocabulary; no ``PipelineResult`` object is constructed on the live
        path. Exposed on the instance and logged at debug for observability.
        """
        tokens_after = _estimate_contents_tokens(contents)
        self._last_proactive_record = {
            "tier": tier,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "tokens_freed": max(0, tokens_before - tokens_after),
        }
        logger.debug(
            "proactive_%s_applied tokens_before=%d tokens_after=%d",
            tier,
            tokens_before,
            tokens_after,
        )

    async def _trim_request(self, llm_request: Any) -> None:
        """Decision + tail-trim body, lifted verbatim from the pre-migration
        ``before_model_callback`` (contents-build -> ``compact_if_needed`` ->
        orphan-adjusted tail-trim -> fail-open). Encapsulated here so the boundary
        and session services never leak to the control; :class:`CompactionCapability`
        is the only handle exposed on the context.
        """
        try:
            contents = list(getattr(llm_request, "contents", None) or [])
            if len(contents) <= self.tail_events:
                # Nothing to trim even in the worst case; skip the boundary call.
                # NOTE (G7): this early-return stays BEFORE the manual consume()
                # below, so a forced /compact on a trivially-small context does
                # NOT burn the one-shot — the pending request survives until a
                # later turn that actually has something to compact.
                return None

            # G7: manual /compact force. When the manual path is ON and a one-shot
            # request is pending, force the tail-drop for THIS model turn regardless
            # of the token threshold (consume() returns True at most once, so the
            # force applies to exactly one model call). When OFF, ``consume_manual_
            # compaction`` is never imported/called and this is byte-identical to
            # Phase-4. Placed AFTER the ``<= tail_events`` no-op return (above) so a
            # tiny context does not consume the pending request.
            forced = False
            if self._manual_enabled:
                from magi_agent.runtime.manual_compaction_context import (
                    consume_manual_compaction,
                )

                forced = consume_manual_compaction()

            # G4: deterministic tool-output prune pre-tier (default-OFF). When ON,
            # content-clear OLD function_response payloads (cheaper, lower-loss
            # than dropping whole turns) BEFORE the Phase-1 decision. The clear is
            # an IN-PLACE mutation of the shared ``FunctionResponse`` objects (the
            # ``contents`` list is a shallow copy of ``llm_request.contents``, so
            # the two share Part objects), which is why no rebind is needed here
            # and why — like OpenCode's prune — the reduction PERSISTS into later
            # turns rather than being recomputed each call. OFF / no-op => zero
            # mutation, so ``llm_request.contents`` stays byte-identical.
            #
            # CACHE TRADEOFF (documented, not solved here): clearing OLD prefix
            # payloads rewrites bytes the model already saw, so when
            # ``MAGI_MESSAGE_CACHE_ENABLED`` is ON the provider prompt-cache prefix
            # is invalidated from the first pruned Content forward — the same class
            # of cost as the tail-drop, but gentler (message count + function
            # call/response pairing preserved). Note this can introduce a cache
            # miss even in the prune-avoids-tail-drop case where Phase-1 alone
            # would have left contents intact. True cache-preserving compaction
            # (compacting only beyond the last cache breakpoint) is deferred to G5.
            if self._tool_prune_enabled:
                self._maybe_prune_tool_outputs(contents)

            # G7: a pending manual /compact forces the tail-drop here, bypassing
            # BOTH the G2 real-token short-circuit and the boundary threshold
            # decision below. The G4 prune (above) still runs as an orthogonal
            # pre-tier. ``_apply_tail_trim`` reuses the existing G1 summary / G5
            # anchored / G8 protected-tool machinery; a split_index <= 0 inside it
            # is a safe no-op. Placed BEFORE ``_real_token_decision`` so a
            # non-breach ``decided is False`` cannot early-return and cancel the
            # forced compaction.
            if forced:
                return await self._apply_tail_trim(llm_request, contents)

            # G2: real-token pre-check. When the real-token path is ON and a real
            # prompt-token count is present (stashed by the prior turn's
            # after_model capture), compare it against a %-of-window threshold
            # computed from the model's effective context window. A breach
            # short-circuits straight to the tail-trim below; a non-breach returns
            # without trimming (the real signal overrides the char-estimate). When
            # real tokens are absent, the effective window is non-positive, or the
            # path is OFF, this is a no-op and the existing estimate ->
            # compact_if_needed path runs verbatim (fail-open). The stash is
            # cleared in the finally so it never leaks to a later call.
            decided = self._real_token_decision()
            if decided is True:
                return await self._apply_tail_trim(llm_request, contents)
            if decided is False:
                return None

            events = tuple(
                ContextLifecycleEvent(
                    eventRef=_content_event_ref(index),
                    tokenEstimate=_content_token_estimate(content),
                )
                for index, content in enumerate(contents)
            )

            session_service, session, state = await self._decision_inputs()
            decision = await self._boundary.compact_if_needed(
                session_service=session_service,
                session=session,
                state=state,
                events=events,
                approvedSummaryRef=_COMPACTION_SUMMARY_REF,
                approvedSummaryDigest=_COMPACTION_SUMMARY_DIGEST,
                config=self._config,
            )
            if decision.status != "compacted":
                return None

            return await self._apply_tail_trim(llm_request, contents)
        except Exception:
            # Fail-open: compaction must never break a live model turn. Leaving
            # contents untouched is the no-plugin behaviour.
            return None
        finally:
            # Clear the per-call real-token stash so it never leaks across calls.
            self._pending_real_prompt_tokens = None
            self._pending_model = None
            # Clear the G5 anchor stash (consumed by ``_build_summary_head``).
            # NOTE: ``_pending_summary_failures`` is intentionally NOT cleared here
            # — the WRITE step in before_model_callback (which runs AFTER this
            # finally) needs it to compute the incremented count. It is reset at
            # the next turn's READ instead, alongside the PRODUCE-side fields.
            self._pending_anchor_summary = None

    def _real_token_decision(self) -> bool | None:
        """Return the real-token compaction decision, or ``None`` to defer.

        ``True``  -> real prompt tokens breach the %-of-window threshold: trim.
        ``False`` -> real prompt tokens are under the threshold: leave untouched.
        ``None``  -> the real-token path does not apply (OFF / no stashed tokens /
                     non-positive effective window): fall through to the existing
                     estimate + fixed-threshold path (fail-open).
        """
        if not self._real_tokens_enabled:
            return None
        real_tokens = self._pending_real_prompt_tokens
        if real_tokens is None or real_tokens < 0:
            return None
        effective = _window_for_model(self._pending_model) - self._output_reserve
        if effective < 1:
            # Degenerate window/reserve: fall back to the fixed threshold path.
            return None
        threshold = int(effective * self._real_tokens_pct)
        if threshold < 1:
            return None
        if real_tokens >= threshold:
            return True
        return False

    async def _apply_tail_trim(self, llm_request: Any, contents: list[Any]) -> None:
        """Reduce ``llm_request.contents`` to the orphan-adjusted recent tail.

        Lifted from the post-decision body so both the real-token short-circuit
        and the boundary-decision path share one tail-trim implementation. The
        caller wraps this in the fail-open try/except, so this body may assume
        well-formed inputs.

        G1: when ``_summarize_enabled`` is ON and a real tail-drop is about to
        occur (split_index > 0, non-empty dropped prefix), the dropped prefix is
        replaced by a session-model summary head (plus G8 protected-tool-output
        text) instead of being silently dropped. Fail-open: any failure resolving
        the provider/model, generating, or timing out falls back to the EXISTING
        pure tail-drop below, so behaviour never regresses below today.

        CACHE TRADEOFF (documented, not solved here): injecting a fresh summary
        head changes the prompt prefix, so when ``MAGI_MESSAGE_CACHE_ENABLED`` is
        ON the provider prompt-cache is invalidated from the first content forward
        — the same class of cost as today's tail-drop (and the G4 prune). True
        cache-preserving (anchored / stable-prefix) summarization is deferred to
        G5.

        PR-F-LIFE3: emits the ``before_compaction`` / ``after_compaction``
        custom_rule audit fan-outs around the tail-drop. The emit is gated by
        :func:`lifecycle_extra_emitters_enabled` so the OFF contract is
        byte-identical (no policy load, no critic factory build). Each emit
        is wrapped in its own try/except so an audit failure cannot break a
        live compaction call.
        """
        # PR-F-LIFE3: before_compaction emit — fires for BOTH the automatic
        # threshold/real-token decision path AND the manual /compact force
        # path (every caller into this method goes through this single
        # entry). Bounded text summary keeps the critic frame small.
        try:
            await self._maybe_emit_compaction_audit(
                slot="before_compaction",
                contents=contents,
                llm_request=llm_request,
            )
        except Exception:
            # Audit failure must never break a live model turn.
            pass

        keep = min(self.tail_events, len(contents))
        split_index = len(contents) - keep
        split_index = _adjust_split_to_avoid_orphan_response(contents, split_index)
        if split_index <= 0:
            # No-op compaction (orphan widening kept everything). Emit a
            # paired after_compaction with dropped_count=0 so audit ledgers
            # show matched before/after pairs — orphan-only emit would
            # mislead operators authoring rules at both slots.
            try:
                await self._maybe_emit_compaction_audit(
                    slot="after_compaction",
                    contents=contents,
                    llm_request=llm_request,
                    dropped_count=0,
                )
            except Exception:
                pass
            return None
        kept = len(contents) - split_index
        if kept > 2 * self.tail_events:
            # Orphan widening re-included far more than ``tail_events`` (the
            # tail is a long unbroken run of function responses), so this
            # compaction reduced almost nothing. Functionally safe — purely
            # an observability signal that the call became a near no-op.
            logger.debug(
                "context compaction near no-op: orphan widening kept %d contents "
                "(tail_events=%d) from %d total",
                kept,
                self.tail_events,
                len(contents),
            )
        dropped_count = split_index
        if self._summarize_enabled and split_index > 0:
            dropped = contents[:split_index]
            if dropped:
                head = await self._build_summary_head(llm_request, dropped)
                if head is not None:
                    llm_request.contents = head + contents[split_index:]
                    # PR-F-LIFE3: after_compaction emit — fires on successful
                    # summary-head injection path.
                    try:
                        await self._maybe_emit_compaction_audit(
                            slot="after_compaction",
                            contents=llm_request.contents,
                            llm_request=llm_request,
                            dropped_count=dropped_count,
                        )
                    except Exception:
                        pass
                    return None
        # Fall through to the EXISTING pure tail-drop (byte-identical to today).
        llm_request.contents = contents[split_index:]
        # PR-F-LIFE3: after_compaction emit — fires on the pure tail-drop path.
        try:
            await self._maybe_emit_compaction_audit(
                slot="after_compaction",
                contents=llm_request.contents,
                llm_request=llm_request,
                dropped_count=dropped_count,
            )
        except Exception:
            pass
        return None

    async def _maybe_emit_compaction_audit(
        self,
        *,
        slot: str,
        contents: list[Any],
        llm_request: Any,
        dropped_count: int | None = None,
    ) -> None:
        """PR-F-LIFE3 helper — fire the before/after compaction audit fan-out.

        Fast OFF-path: triple-gate check FIRST so the OFF cost is one helper
        call + one comparison; the policy load + critic factory build only
        happen when the master flag resolves ON. The audit fan-out itself
        is fail-open (see :mod:`magi_agent.customize.lifecycle_audit`), so a
        misbehaving rule cannot wedge this call site.

        PR-F-EXEC1: this helper ALSO fires the sibling shell_command fan-out
        at the same slot (gated independently by ``shell_command_enabled``)
        so an operator-authored ``shell_command`` rule with
        ``firesAt=before_compaction`` or ``after_compaction`` runs from this
        same chokepoint. The shell fan-out is dispatched after the
        llm_criterion audit so both surfaces see consistent ordering across
        all 4 compaction slots (before / after × pure-drop / summary-head).
        """
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            lifecycle_extra_emitters_enabled,
            run_after_compaction_audit,
            run_before_compaction_audit,
        )

        try:
            model_id = _resolve_model_id(llm_request) or "(unknown)"
        except Exception:
            model_id = "(unknown)"
        size = len(contents) if isinstance(contents, list) else 0
        if slot == "before_compaction":
            frame = (
                f"pre_compaction: contents={size}, model={model_id[:64]}"
            )
            if lifecycle_extra_emitters_enabled():
                # Lazy critic factory build (mirrors lifecycle_llm_call_control).
                factory = self._build_lifecycle_critic_factory()
                await run_before_compaction_audit(
                    pre_compaction_text=frame,
                    model_factory=factory,
                )
            await self._maybe_emit_shell_command_compaction(
                slot="before_compaction", frame_text=frame
            )
        else:
            dc = dropped_count if isinstance(dropped_count, int) else -1
            frame = (
                f"post_compaction: kept={size}, dropped={dc}, "
                f"model={model_id[:64]}"
            )
            if lifecycle_extra_emitters_enabled():
                factory = self._build_lifecycle_critic_factory()
                await run_after_compaction_audit(
                    summary_text=frame,
                    model_factory=factory,
                )
            await self._maybe_emit_shell_command_compaction(
                slot="after_compaction", frame_text=frame
            )
        return None

    async def _maybe_emit_shell_command_compaction(
        self, *, slot: str, frame_text: str
    ) -> None:
        """PR-F-EXEC1 — fire ``shell_command`` fan-out at before/after_compaction.

        Audit-only at both slots (compaction is mid-decision and the
        shell hook is not a deterministic gate). Triple-gated +
        fail-open by the sibling helper; OFF path is byte-identical.
        Threads the shared per-(session, turn) budget from the
        ``_ACTIVE_TURN_IDENTITY`` ContextVar published by
        ``run_governed_turn``.
        """
        try:
            from magi_agent.adk_bridge.lifecycle_shell_command_control import (  # noqa: PLC0415
                shell_budget_for,
            )
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                run_shell_command_at_after_compaction,
                run_shell_command_at_before_compaction,
                shell_command_enabled,
            )

            if not shell_command_enabled():
                return
            remaining, decrement_fn = shell_budget_for()
            if slot == "before_compaction":
                await run_shell_command_at_before_compaction(
                    pre_compaction_text=frame_text,
                    remaining_budget=remaining,
                    decrement_fn=decrement_fn,
                )
            else:
                await run_shell_command_at_after_compaction(
                    summary_text=frame_text,
                    remaining_budget=remaining,
                    decrement_fn=decrement_fn,
                )
        except Exception:
            return

    async def _maybe_compaction_blocked(
        self,
        *,
        contents: list[Any],
        llm_request: Any,
    ) -> bool:
        """PR-F-LIFE4a — consult the ``before_compaction`` gate; return True on block.

        Fast OFF-path: the triple-gate inside the helper short-circuits when
        the master flag is OFF, so this call is one helper roundtrip + one
        comparison on the OFF path. Fail-open: any exception returns False
        (proceed with the tail-drop).
        """
        try:
            from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
                lifecycle_extra_emitters_enabled,
                run_before_compaction_gate,
            )

            if not lifecycle_extra_emitters_enabled():
                return False
            try:
                model_id = _resolve_model_id(llm_request) or "(unknown)"
            except Exception:
                model_id = "(unknown)"
            size = len(contents) if isinstance(contents, list) else 0
            frame = f"pre_compaction: contents={size}, model={model_id[:64]}"
            factory = self._build_lifecycle_critic_factory()
            verdict = await run_before_compaction_gate(
                pre_compaction_text=frame,
                model_factory=factory,
            )
            return verdict == "block"
        except Exception:
            return False

    @staticmethod
    def _build_lifecycle_critic_factory() -> Any | None:
        """Build the Haiku-class critic factory used by the F-LIFE3 emits.

        Mirrors
        :func:`magi_agent.adk_bridge.lifecycle_llm_call_control._build_critic_factory`
        so all lifecycle audit fan-outs share the same critic resolution
        path. Returns ``None`` on any import / build failure; the audit
        helper then records ``status="skipped"`` with reason ``no critic
        model available`` rather than invoking against ``None``.
        """
        try:
            from magi_agent.cli.wiring import (  # noqa: PLC0415
                _build_criterion_model_factory,
            )

            return _build_criterion_model_factory()
        except Exception:
            return None

    async def _build_summary_head(
        self, llm_request: Any, dropped: list[Any]
    ) -> list[Any] | None:
        """Build the injected head for a tail-drop: ``[summary_content, *protected]``.

        Renders the dropped prefix to a bounded transcript, summarizes it via the
        session model (G1), and converts any protected-tool results in the dropped
        region to plain text user Contents (G8). Returns ``None`` (caller falls
        through to the pure drop) when the summary cannot be produced. Fail-open:
        any error returns ``None``.
        """
        try:
            from google.genai import types

            # G6 circuit breaker: once the consecutive-failure count has reached
            # the configured max, skip the summarizer entirely (no provider/model
            # resolution, no LLM call) so the caller falls through to pure
            # tail-drop. The breaker is already tripped, so do NOT mark another
            # failure here (no further increment). max == 0 disables the breaker.
            if (
                self._summary_breaker_max > 0
                and self._pending_summary_failures >= self._summary_breaker_max
            ):
                return None

            # G5 anchored summary: when anchoring is ON, recognize a prior injected
            # summary inside the dropped prefix (authoritative for what is dropped
            # this turn) and prefer it over the state-stashed anchor; skip it from
            # the raw transcript so an already-summarized head is not re-rendered.
            prior_anchor = None
            if self._anchored_enabled:
                prior_anchor = _anchor_from_dropped(dropped)
                if prior_anchor is None:
                    prior_anchor = self._pending_anchor_summary

            transcript = _render_dropped_transcript(
                dropped, skip_anchor=self._anchored_enabled
            )
            model_id = self._summary_model_override or _resolve_model_id(llm_request)
            summary = await _summarize_dropped_prefix(
                model_id,
                transcript,
                timeout=self._summary_timeout,
                anchor=prior_anchor if self._anchored_enabled else None,
            )
            if not summary:
                # G6: a failed attempt (None/empty). Record it so the WRITE step
                # increments the consecutive-failure counter.
                self._produced_summary_failed = True
                return None
            # G5/G6: success — record the marker-stripped anchor body (capped) so
            # the WRITE step stores it and resets the failure counter to 0.
            self._produced_summary = summary[:_ANCHOR_SUMMARY_MAX_CHARS]
            summary_content = types.Content(
                role="user",
                parts=[types.Part(text=_SUMMARY_MARKER + summary)],
            )
            protected = _protected_text_contents(dropped)
            return [summary_content, *protected]
        except Exception:
            return None

    def _maybe_prune_tool_outputs(self, contents: list[Any]) -> None:
        """G4 deterministic tool-output prune (in-place mutator).

        Content-clear OLD ``function_response`` payloads (replace ``.response``
        with :data:`_PRUNED_TOOL_OUTPUT_PLACEHOLDER`) in the region BEFORE the
        protected tail, committing only when the total freed tokens reach
        ``self._prune_minimum``. Mutates the matching ``FunctionResponse`` objects
        IN PLACE (the objects are shared with ``llm_request.contents``), so the
        reduction is visible to the request without a list rebind and persists
        across turns; returns ``None``. Never deletes a part (keeps the
        function_call/response pairing valid), never touches protected tool
        results, never touches the protected tail. When nothing reaches the
        minimum, no object is mutated (byte-identical no-op). Fail-open: any error
        sizing or mutating leaves ``contents`` untouched.
        """
        from magi_agent.context.protected_tools import PRUNE_PROTECTED_TOOLS

        try:
            boundary = self._prune_protected_boundary(contents)
            if boundary <= 0:
                return None

            # First pass: identify clears + compute freed tokens WITHOUT mutating,
            # so a sub-minimum total leaves contents fully untouched (no churn).
            placeholder_tokens = _response_payload_tokens(
                _PRUNED_TOOL_OUTPUT_PLACEHOLDER
            )
            pending: list[tuple[Any, int]] = []  # (function_response, freed)
            freed_total = 0
            for content in contents[:boundary]:
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    fr = getattr(part, "function_response", None)
                    if fr is None:
                        continue
                    name = getattr(fr, "name", None)
                    if isinstance(name, str) and name in PRUNE_PROTECTED_TOOLS:
                        continue
                    payload = getattr(fr, "response", None)
                    size = _response_payload_tokens(payload)
                    if size < _PRUNE_PER_RESULT_MINIMUM:
                        continue
                    freed = size - placeholder_tokens
                    if freed <= 0:
                        continue
                    pending.append((fr, freed))
                    freed_total += freed

            if freed_total < self._prune_minimum:
                return None

            # Commit: content-clear the identified payloads in place.
            for fr, _freed in pending:
                fr.response = dict(_PRUNED_TOOL_OUTPUT_PLACEHOLDER)
            return None
        except Exception:
            # Fail-open: prune must never break a live model turn.
            return None

    def _prune_protected_boundary(self, contents: list[Any]) -> int:
        """Return the index where the prune-eligible OLD region ends (exclusive).

        Two-layer tail protection over ``contents`` (0..n-1, oldest..newest):

        * Layer 1 (count): protect the last ``min(tail_events, len)`` Contents —
          the SAME tail ``_apply_tail_trim`` keeps.
        * Layer 2 (token): walking backward from the start of the count-protected
          region, keep protecting Contents until the running sum of
          function_response output tokens reaches ``self._prune_protect``.

        The protected boundary = the lower (older) of the two split points.
        Everything at index < boundary is eligible for prune.
        """
        n = len(contents)
        keep = min(self.tail_events, n)
        count_split = n - keep  # indices [count_split, n) are count-protected
        # Walk backward from count_split accumulating output tokens.
        token_split = count_split
        running = 0
        idx = count_split - 1
        while idx >= 0 and running < self._prune_protect:
            running += _content_response_tokens(contents[idx])
            token_split = idx
            idx -= 1
        # Protected boundary is the older (smaller) of the two split points.
        return min(count_split, token_split)

    async def _decision_inputs(self) -> tuple[Any, Any, Any]:
        """Return the cached local-fake session service / session / QueryState the
        boundary requires for its decision path, building them once on first use.

        Imports are local so this module stays import-light when the feature is
        off.

        Bounding provenance growth: ``compact_if_needed`` appends a
        ``compacted_state_provenance`` event to the session on every *compacted*
        decision, and the before-model seam never consumes that event (it reads
        only ``decision.status`` / ``thresholdBreaches``). Reusing one session
        across many model calls would therefore let ``session.events`` grow
        without bound, so we clear it on each call. The decision path does not
        read prior events (it only re-appends one), so clearing is safe and keeps
        the reused session's footprint constant.
        """
        if self._decision_cache is None:
            from magi_agent.adk_bridge.session_service import WorkspaceSessionService
            from magi_agent.runtime.query_state import QueryState

            service = WorkspaceSessionService(app_name="magi-context-compaction")
            session = await service.create_session(
                app_name="magi-context-compaction",
                user_id="magi-context-compaction",
                session_id="magi-context-compaction",
            )
            state = QueryState(
                currentTurnId="turn-context-compaction",
                sessionId=session.id,
            )
            self._decision_cache = (service, session, state)

        service, session, state = self._decision_cache
        # Keep the reused session's provenance log bounded (see docstring).
        events = getattr(session, "events", None)
        if isinstance(events, list):
            events.clear()
        return service, session, state


class CompactionCapability:
    """Narrow context capability wrapping the boundary-backed compaction decision.

    This is the ONLY handle a ``control_plane`` impl needs for compaction (the
    S-D seam). First-party wraps :class:`MagiContextCompactionPlugin`; a user pack
    can supply any object exposing the same ``async def trim(llm_request)`` method
    and author an equivalent compaction control with no privileged access. The
    ``ContextLifecycleBoundary`` + ``WorkspaceSessionService`` + ``QueryState``
    plumbing stays fully encapsulated behind this wrapper — they never reach the
    control. ``trim`` mutates ``llm_request.contents`` in place (the idiomatic ADK
    before-model seam) and is fail-open, identical to the legacy callback.
    """

    def __init__(self, plugin: "MagiContextCompactionPlugin") -> None:
        self._plugin = plugin

    async def trim(self, llm_request: Any) -> None:
        await self._plugin._trim_request(llm_request)


def _read_real_prompt_tokens(callback_context: Any) -> int | None:
    """Read the prior turn's real prompt-token count off ``callback_context.state``.

    Duck-typed and fail-open: any missing state / odd value yields ``None`` so the
    decision falls back to the estimate path. Never raises.
    """
    try:
        state = getattr(callback_context, "state", None)
        if state is None:
            return None
        getter = getattr(state, "get", None)
        value = getter(REAL_PROMPT_TOKENS_STATE_KEY) if callable(getter) else None
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
        return None
    except Exception:
        return None


def _read_anchor_summary(callback_context: Any) -> str | None:
    """Read the prior turn's anchor summary body off ``callback_context.state``.

    Duck-typed and fail-open (mirrors :func:`_read_real_prompt_tokens`): a
    missing state / non-string / empty value yields ``None`` so anchoring degrades
    to a plain (non-anchored) summary. Never raises.
    """
    try:
        state = getattr(callback_context, "state", None)
        if state is None:
            return None
        getter = getattr(state, "get", None)
        value = getter(ANCHOR_SUMMARY_STATE_KEY) if callable(getter) else None
        if isinstance(value, str) and value:
            return value
        return None
    except Exception:
        return None


def _read_summary_failures(callback_context: Any) -> int:
    """Read the consecutive summary-failure count off ``callback_context.state``.

    Duck-typed and fail-open: a missing / odd value yields ``0`` (breaker not yet
    tripped). Never raises. ``bool`` is rejected (an ``int`` subclass).
    """
    try:
        state = getattr(callback_context, "state", None)
        if state is None:
            return 0
        getter = getattr(state, "get", None)
        value = getter(SUMMARY_FAILURE_COUNT_STATE_KEY) if callable(getter) else None
        if isinstance(value, bool):
            return 0
        if isinstance(value, int) and value >= 0:
            return value
        return 0
    except Exception:
        return 0


def _write_compaction_state(callback_context: Any, updates: dict[str, Any]) -> None:
    """Write ``updates`` onto ``callback_context.state`` (fail-open observer).

    Mirrors the after_model_callback state-write guard: duck-typed
    ``state[key] = value`` with a callable-aware fallback, wrapped so a missing
    state or a write error NEVER alters the model-loop return contract. Never
    raises.
    """
    try:
        state = getattr(callback_context, "state", None)
        if state is None:
            return None
        for key, value in updates.items():
            try:
                state[key] = value
            except Exception:
                setter = getattr(state, "__setitem__", None)
                if callable(setter):
                    setter(key, value)
    except Exception:
        return None
    return None


def _anchor_text_from_content(content: Any) -> str | None:
    """Return the marker-stripped anchor body if ``content`` is a summary anchor.

    Duck-types an injected summary Content: its first text Part must start with
    :data:`_SUMMARY_MARKER`; the return is the text with the marker prefix
    stripped (the prior anchor body). Any malformed content -> ``None`` (not an
    anchor). Never raises.
    """
    try:
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.startswith(_SUMMARY_MARKER):
                return text[len(_SUMMARY_MARKER) :]
            # Only the FIRST text part is the anchor marker carrier; a leading
            # non-marker text part means this is not an anchor Content.
            if isinstance(text, str) and text:
                return None
        return None
    except Exception:
        return None


def _anchor_from_dropped(dropped: list[Any]) -> str | None:
    """Find the most-recent prior-summary anchor body within the dropped prefix.

    The dropped-prefix anchor is authoritative for what is actually being dropped
    this turn (the state copy may lag or have moved), so a found anchor is
    preferred over the state-stashed value. Returns the LAST anchor body found
    (the freshest injected head if more than one is present). Fail-soft: ``None``
    when no anchor Content is present.
    """
    found: str | None = None
    for content in dropped:
        body = _anchor_text_from_content(content)
        if body is not None:
            found = body
    return found


def _resolve_model_id(llm_request: Any) -> str | None:
    """Resolve the model id from the outgoing request (``llm_request.model``).

    Mirrors ``anthropic_cache_model.py`` which reads ``llm_request.model`` the same
    way. Returns ``None`` when absent so the window lookup applies its default.
    """
    try:
        model = getattr(llm_request, "model", None)
        if isinstance(model, str) and model:
            return model
        return None
    except Exception:
        return None


def _window_for_model(model: str | None) -> int:
    """Effective context window for ``model`` from the shared per-model table.

    Reuses ``magi_agent.context.token_tracker._KNOWN_TOKEN_LIMITS`` /
    ``_DEFAULT_CONTEXT_WINDOW`` (single source) so the budget signal matches the
    rest of the runtime. Unknown/absent model -> the conservative default window.
    Local import keeps this module import-light when the feature is off.
    """
    from magi_agent.context.token_tracker import (
        _DEFAULT_CONTEXT_WINDOW,
        _KNOWN_TOKEN_LIMITS,
    )

    if not isinstance(model, str) or not model:
        return _DEFAULT_CONTEXT_WINDOW
    return _KNOWN_TOKEN_LIMITS.get(model, _DEFAULT_CONTEXT_WINDOW)


def _serialize_response_payload(payload: Any) -> str:
    """Compactly serialize a ``function_response.response`` payload to text.

    Mirrors ``_response_payload_tokens``'s JSON basis (model_dump_json / json dump
    with ``default=str``) so the rendered descriptor matches the budget basis.
    Returns ``""`` for a ``None`` payload. Never raises (falls back to ``str``).
    """
    if payload is None:
        return ""
    try:
        dump_json = getattr(payload, "model_dump_json", None)
        if callable(dump_json):
            return dump_json()
        import json

        return json.dumps(payload, default=str, sort_keys=True)
    except Exception:
        try:
            return str(payload)
        except Exception:
            return ""


def _render_dropped_transcript(
    dropped: list[Any], *, skip_anchor: bool = False
) -> str:
    """Render the dropped prefix Contents to a bounded plain-text transcript.

    Adapts ADK ``types.Content`` / ``Part`` into the shared
    :func:`magi_agent.context.transcript_render.render_transcript`
    skeleton (D-13): text parts contribute their text; function_call
    parts render a concise ``[tool_call <name>]`` descriptor;
    function_response parts render ``[tool_result <name>]: <payload>``.
    Each piece is pre-capped here (per-piece caps differ across
    providers, so they stay at the adapter side); the renderer applies
    the whole-transcript cap to :data:`_SUMMARY_TRANSCRIPT_MAX_CHARS`
    and appends the ``"\\n…[older context truncated]"`` marker on
    truncation.

    G5: when ``skip_anchor`` is set (anchoring ON), a Content recognized as a
    prior-injected summary anchor (its first text Part starts with
    :data:`_SUMMARY_MARKER`) is SKIPPED — it is fed back as the previous-summary
    anchor instead of being re-rendered as raw transcript (avoids compounding /
    re-summarizing already-summarized text verbatim).

    Fully duck-typed and fail-soft: a malformed part contributes nothing rather
    than raising (the outer summarize path is fail-open anyway).
    """
    from magi_agent.context.transcript_render import (  # noqa: PLC0415
        NormalizedSegment,
        render_transcript,
    )

    segments_out: list[NormalizedSegment] = []
    for content in dropped:
        if skip_anchor and _anchor_text_from_content(content) is not None:
            continue
        try:
            role = getattr(content, "role", None) or "unknown"
            parts = getattr(content, "parts", None) or []
            pieces: list[str] = []
            for part in parts:
                try:
                    text = getattr(part, "text", None)
                    if isinstance(text, str) and text:
                        pieces.append(text[:_SUMMARY_SEGMENT_MAX_CHARS])
                        continue
                    fc = getattr(part, "function_call", None)
                    if fc is not None:
                        name = getattr(fc, "name", None) or "?"
                        args = getattr(fc, "args", None)
                        if args:
                            pieces.append(
                                f"[tool_call {name} "
                                f"{str(args)[:_SUMMARY_CALL_ARGS_MAX_CHARS]}]"
                            )
                        else:
                            pieces.append(f"[tool_call {name}]")
                        continue
                    fr = getattr(part, "function_response", None)
                    if fr is not None:
                        name = getattr(fr, "name", None) or "?"
                        payload = _serialize_response_payload(
                            getattr(fr, "response", None)
                        )
                        pieces.append(
                            f"[tool_result {name}]: "
                            f"{payload[:_SUMMARY_RESULT_PAYLOAD_MAX_CHARS]}"
                        )
                except Exception:
                    continue
            # D-13: a Content whose parts contributed no pieces is
            # filtered HERE (before segment construction) so the shared
            # renderer never sees a bare ``[role]:`` line. Matches the
            # pre-D-13 ``if not segments: continue`` behaviour.
            if not pieces:
                continue
            segments_out.append(
                NormalizedSegment(role=role, pieces=tuple(pieces))
            )
        except Exception:
            continue
    return render_transcript(
        segments_out,
        total_cap=_SUMMARY_TRANSCRIPT_MAX_CHARS,
        truncation_marker="\n…[older context truncated]",
    )


def _protected_text_contents(dropped: list[Any]) -> list[Any]:
    """G8: convert protected-tool results in the dropped region to text Contents.

    For each ``function_response`` whose ``.name`` is in ``PRUNE_PROTECTED_TOOLS``,
    build a plain ``role='user'`` Content carrying a single text Part
    ``[Preserved tool output: <name>]\\n<payload>`` (payload compactly serialized,
    length-capped). Re-attaching the raw ``FunctionResponse`` would create an
    orphan tool_result (no preceding tool_use in the kept window), so the
    user-decided TEXT-CONVERSION removes the function_response part entirely —
    no tool_use/tool_result pairing constraint applies. Fail-soft per part.
    """
    from google.genai import types

    from magi_agent.context.protected_tools import PRUNE_PROTECTED_TOOLS

    out: list[Any] = []
    for content in dropped:
        try:
            parts = getattr(content, "parts", None) or []
            for part in parts:
                fr = getattr(part, "function_response", None)
                if fr is None:
                    continue
                name = getattr(fr, "name", None)
                if not (isinstance(name, str) and name in PRUNE_PROTECTED_TOOLS):
                    continue
                payload = _serialize_response_payload(getattr(fr, "response", None))
                payload = payload[:_SUMMARY_TRANSCRIPT_MAX_CHARS]
                out.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=f"[Preserved tool output: {name}]\n{payload}"
                            )
                        ],
                    )
                )
        except Exception:
            continue
    return out


async def _summarize_dropped_prefix(
    model_id: str | None,
    transcript: str,
    *,
    timeout: float,
    anchor: str | None = None,
) -> str | None:
    """Summarize ``transcript`` via the session model, or return ``None``.

    Reuses the proven shared seam: ``resolve_provider_config()`` discovers the
    active provider/key and ``_build_litellm_for_config(model_override=...)``
    builds the ADK ``LiteLlm`` (EXACTLY the egress-critic factory body). The model
    is invoked via the ADK async-generator contract — MIRRORING
    ``ReadOnlyClassifier._invoke_llm`` (kept a private staticmethod; mirrored here
    rather than imported so the live model-loop module does not couple to the
    classifier's import surface). Fully fail-open: no provider/key, no litellm
    dependency, a generate error, or a timeout all return ``None`` so the caller
    falls back to the pure tail-drop.
    """
    import asyncio

    try:
        from magi_agent.engine.providers import resolve_provider_config

        provider_config = resolve_provider_config()
    except Exception:
        return None
    if provider_config is None:
        return None

    try:
        from magi_agent.cli.readonly_classifier import _build_litellm_for_config
        from magi_agent.context.auto_compact import AutoCompactionEngine

        provider_default = getattr(provider_config, "litellm_model", None)
        candidate = (model_id or "").strip()
        # A bare model id (e.g. the native-Anthropic path's ``claude-sonnet-4-6``,
        # read off ``llm_request.model``) is not litellm-routable without its
        # ``provider/`` prefix. Prefer the provider config's properly-prefixed
        # model (the configured session model) in that case so the summary build
        # actually succeeds; only use ``candidate`` verbatim when it already
        # carries a provider prefix. Still fail-open if neither is usable.
        override = candidate if "/" in candidate else (provider_default or candidate or None)
        model = _build_litellm_for_config(provider_config, model_override=override)
        # G5: an anchored (incremental-refinement) prompt when a prior anchor is
        # present; otherwise the plain Phase-3 path is BYTE-IDENTICAL.
        if isinstance(anchor, str) and anchor:
            prompt = AutoCompactionEngine.ANCHORED_SUMMARY_PROMPT.format(
                anchor=anchor, conversation=transcript
            )
        else:
            prompt = AutoCompactionEngine.SUMMARY_PROMPT.format(
                conversation=transcript
            )
        return await asyncio.wait_for(
            _invoke_summary_model(model, prompt), timeout=timeout
        )
    except Exception:
        return None


async def _invoke_summary_model(model: Any, prompt: str) -> str:
    """Invoke ``model`` via the ADK async-generator contract, collecting text.

    Mirrors ``magi_agent.cli.readonly_classifier.ReadOnlyClassifier._invoke_llm``
    (the canonical reference) — kept private there, so this duplicates the ~15-line
    contract rather than importing the classifier's class machinery onto the live
    model path. If ADK's ``generate_content_async`` contract changes, both sites
    must update.
    """
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    llm_request = LlmRequest(
        config=types.GenerateContentConfig(
            system_instruction="Summarize concisely; reply with prose only.",
        ),
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ],
    )
    collected: list[str] = []
    async for resp in model.generate_content_async(llm_request, stream=False):
        if resp.content and resp.content.parts:
            for part in resp.content.parts:
                if part.text:
                    collected.append(part.text)
    return "".join(collected)


def _content_event_ref(index: int) -> str:
    # Must satisfy ``validate_safe_ref`` (^[A-Za-z][A-Za-z0-9_.:-]{1,220}$).
    return f"event:before-model:{index:06d}"


def _content_token_estimate(content: Any) -> int:
    """Best-effort token estimate for one ADK ``types.Content``.

    Uses the improved shared estimator over the content's text parts, with a
    structural fallback when the content cannot be serialised.
    """
    from magi_agent.shared.token_estimation import count_text_tokens

    total = 0
    parts = getattr(content, "parts", None) or []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            total += count_text_tokens(text)
            continue
        # Non-text parts (function calls/responses, inline data) — approximate by
        # their serialised form so they still contribute to the budget. genai
        # Parts are pydantic models, so prefer ``model_dump_json()``: this aligns
        # with the JSON basis used by ``estimate_message_tokens`` (json.dumps).
        # ``str(part)`` is the pydantic *repr* — materially larger than the JSON
        # form, which would bias the estimate toward over-counting. Fall back to
        # ``str(part)`` only when no JSON serialiser is available.
        try:
            dump_json = getattr(part, "model_dump_json", None)
            serialised = dump_json() if callable(dump_json) else str(part)
            total += count_text_tokens(serialised)
        except Exception:
            total += 1
    role = getattr(content, "role", None)
    if isinstance(role, str):
        total += count_text_tokens(role)
    return total


def _response_payload_tokens(payload: Any) -> int:
    """Estimated token size of a single ``function_response.response`` payload.

    Mirrors ``_content_token_estimate``'s non-text branch (JSON-serialised basis
    via ``count_text_tokens`` over the payload's ``model_dump_json()`` / json dump)
    so the freed-token accounting matches the budget basis used elsewhere. A
    ``None`` payload is zero; a non-serialisable payload raises (the caller's
    fail-open path then leaves the result un-pruned).
    """
    if payload is None:
        return 0
    from magi_agent.shared.token_estimation import count_text_tokens

    dump_json = getattr(payload, "model_dump_json", None)
    if callable(dump_json):
        serialised = dump_json()
    else:
        import json

        serialised = json.dumps(payload, default=str, sort_keys=True)
    return count_text_tokens(serialised)


def _content_response_tokens(content: Any) -> int:
    """Sum of function_response output tokens carried by one ADK ``Content``.

    Used by the G4 token-tail protection walk; only function_response payloads
    count (the prune target). Non-response parts contribute nothing here.
    """
    total = 0
    parts = getattr(content, "parts", None) or []
    for part in parts:
        fr = getattr(part, "function_response", None)
        if fr is None:
            continue
        payload = getattr(fr, "response", None)
        total += _response_payload_tokens(payload)
    return total


def _is_orphan_response(content: Any) -> bool:
    """True when ``content`` begins a turn with a function/tool response that has
    no preceding call in the kept tail (which would be an invalid request).
    """
    parts = getattr(content, "parts", None) or []
    for part in parts:
        if getattr(part, "function_response", None) is not None:
            return True
    return False


def _adjust_split_to_avoid_orphan_response(contents: list[Any], split_index: int) -> int:
    """Widen the kept tail backwards so it never starts with an orphaned tool
    response (mirrors ADK ``ContextFilterPlugin`` orphan handling).

    Safety invariant — why only ONE direction of orphaning is possible
    -----------------------------------------------------------------
    The reduction applied here is **prefix-only**: the caller keeps
    ``contents[split_index:]``, which removes a contiguous *prefix* and never
    touches the tail. A ``function_call`` always *precedes* its matching
    ``function_response`` in ``contents`` (the model emits the call, the response
    is appended after the tool runs). Therefore:

    * Direction (a) — a kept ``function_response`` whose originating
      ``function_call`` was trimmed: POSSIBLE. The call sits at a *lower* index,
      so a prefix cut can drop it while keeping the response. This is the real
      orphan risk, and the backward ``while`` below handles it by walking the
      split back across the run of responses until it reaches their originating
      call(s) (or the head of ``contents``).

    * Direction (b) — a kept ``function_call`` whose matching
      ``function_response`` was trimmed (a dangling *trailing* call): IMPOSSIBLE.
      The response sits at a *higher* index than the call, and a prefix cut only
      removes lower indices, so any kept call necessarily keeps its later
      response. No handling is needed for this direction.

    Because trimming is prefix-only, widening the split *backwards* is sufficient
    to guarantee the kept window has no orphaned ``function_response``.
    """
    idx = split_index
    while 0 < idx < len(contents) and _is_orphan_response(contents[idx]):
        idx -= 1
    return idx


def _read_proactive_summary_failures(callback_context: Any) -> int:
    """Read the proactive tier-7 cumulative-failure count off the state.

    Duck-typed and fail-open (mirrors :func:`_read_summary_failures`): a missing /
    odd value yields ``0`` (breaker not tripped). ``bool`` is rejected (an ``int``
    subclass). Never raises. Reads the INDEPENDENT
    :data:`PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY`, never the G6 key.
    """
    try:
        state = getattr(callback_context, "state", None)
        if state is None:
            return 0
        getter = getattr(state, "get", None)
        value = (
            getter(PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY)
            if callable(getter)
            else None
        )
        if isinstance(value, bool):
            return 0
        if isinstance(value, int) and value >= 0:
            return value
        return 0
    except Exception:
        return 0


def _estimate_contents_tokens(contents: list[Any]) -> int:
    """Sum the best-effort token estimate over a list of ADK ``Content`` objects.

    Reuses :func:`_content_token_estimate` so the proactive gate's utilization
    re-check matches the budget basis used everywhere else in the plugin.
    """
    return sum(_content_token_estimate(content) for content in contents)


def _make_proactive_recovery_context(messages: list[dict[str, Any]]) -> Any:
    """Wrap adapter messages into a ``RecoveryContext`` for the reused strategies.

    Re-implemented inline (8 pure lines) rather than imported from the dormant
    ``context/hook.py`` so the live path does not depend on that module (a future
    hook cleanup must not be able to break this path).
    """
    from magi_agent.runtime.error_recovery.types import (
        ErrorKind,
        RecoverableError,
        RecoveryContext,
    )

    return RecoveryContext(
        error=RecoverableError(
            kind=ErrorKind.PROMPT_TOO_LONG,
            original_error="proactive_context_management",
        ),
        messages=messages,
        session_key="proactive",
        turn_id="proactive",
    )


def _content_strategy_role(content: Any) -> str:
    """Classify an ADK ``Content`` into a strategy round-role (§3.7 Impedance 1).

    A ``function_response`` Content is tagged ``"tool"`` (NOT ``"user"``) so it
    does NOT start a new round and groups with its originating call; a Content with
    a ``function_call`` or ADK ``role == "model"`` is ``"assistant"``; only a real
    user-text turn (ADK ``role == "user"`` with no function parts) is ``"user"``
    (the sole thing that starts a round). This is the convention the strategies'
    ``_partition_into_rounds`` (and the tier67 test fixtures) already expect.
    """
    parts = getattr(content, "parts", None) or []
    if any(getattr(p, "function_response", None) is not None for p in parts):
        return "tool"
    if any(getattr(p, "function_call", None) is not None for p in parts):
        return "assistant"
    if getattr(content, "role", None) == "user":
        return "user"
    return "assistant"


def _render_content_text(content: Any) -> str:
    """Render an ADK ``Content`` to a JSON-native plain string for the strategies.

    Text parts contribute their text; function_call parts render a concise
    ``[tool_call <name>]`` descriptor; function_response parts render
    ``[tool_result <name>]: <payload>``. The result is a plain ``str`` so
    ``json.dumps(..., default=str)`` inside ``ReactiveCompactStrategy`` never has
    to coerce a non-native object (§3.7 Impedance 3). Never raises.
    """
    chunks: list[str] = []
    for part in getattr(content, "parts", None) or []:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            chunks.append(text)
            continue
        fc = getattr(part, "function_call", None)
        if fc is not None:
            chunks.append(f"[tool_call {getattr(fc, 'name', '') or ''}]")
            continue
        fr = getattr(part, "function_response", None)
        if fr is not None:
            name = getattr(fr, "name", "") or ""
            payload = _serialize_response_payload(getattr(fr, "response", None))
            chunks.append(f"[tool_result {name}]: {payload}")
            continue
    return "\n".join(chunks)


def contents_to_msgs(contents: list[Any]) -> list[dict[str, Any]]:
    """Adapt ADK ``Content`` list -> strategy ``list[dict]`` with round-role remap.

    Each emitted dict is ``{"role": <strategy-role>, "content": <rendered str>,
    "_orig_index": i}``. EVERY dict carries ``_orig_index`` (so the only dict
    lacking it downstream is a strategy-synthesized summary), and the role is the
    strategy round-role from :func:`_content_strategy_role`, NOT the raw ADK role,
    so ``_partition_into_rounds`` keeps each call+response inside one round.
    """
    return [
        {
            "role": _content_strategy_role(content),
            "content": _render_content_text(content),
            "_orig_index": index,
        }
        for index, content in enumerate(contents)
    ]


def _function_call_ids(content: Any) -> set[str]:
    ids: set[str] = set()
    for part in getattr(content, "parts", None) or []:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            fc_id = getattr(fc, "id", None)
            if isinstance(fc_id, str) and fc_id:
                ids.add(fc_id)
    return ids


def _function_call_names(content: Any) -> set[str]:
    names: set[str] = set()
    for part in getattr(content, "parts", None) or []:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            name = getattr(fc, "name", None)
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _repair_orphans_nonprefix(kept: list[Any]) -> list[Any]:
    """Full orphan-pair scan over a NON-CONTIGUOUS kept list (§3.7 Impedance 2).

    Unlike :func:`_adjust_split_to_avoid_orphan_response` (sound only for a
    contiguous prefix cut), this drops any kept Content whose ``function_response``
    has no matching ``function_call`` ANYWHERE in the kept list, which is the
    property SC-2 needs once collapse-drain has deleted interior (middle) rounds.
    Matching is id-based with a name-based positional fallback when ids are absent.
    Never raises.
    """
    try:
        call_ids: set[str] = set()
        call_names: set[str] = set()
        for content in kept:
            call_ids |= _function_call_ids(content)
            call_names |= _function_call_names(content)
        out: list[Any] = []
        for content in kept:
            orphan = False
            for part in getattr(content, "parts", None) or []:
                fr = getattr(part, "function_response", None)
                if fr is None:
                    continue
                rid = getattr(fr, "id", None)
                if isinstance(rid, str) and rid:
                    if rid not in call_ids:
                        orphan = True
                        break
                else:
                    rname = getattr(fr, "name", None)
                    if isinstance(rname, str) and rname and rname not in call_names:
                        orphan = True
                        break
            if orphan:
                continue
            out.append(content)
        return out
    except Exception:
        return list(kept)


def msgs_to_contents(msgs: list[dict[str, Any]], *, original: list[Any]) -> list[Any]:
    """Rebuild the ADK ``Content`` list from adapter messages (§3.7 / §3.8).

    A dict WITH ``_orig_index`` reuses the original ``types.Content`` (lossless); a
    dict WITHOUT ``_orig_index`` (only a strategy-synthesized summary) is
    MATERIALIZED into a fresh user-role text Content so the compressed history is
    not discarded. The rebuilt list then runs :func:`_repair_orphans_nonprefix` so
    every kept ``function_response`` has its ``function_call`` present.
    """
    from google.genai import types  # noqa: PLC0415

    kept: list[Any] = []
    for msg in msgs:
        idx = msg.get("_orig_index")
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(
            original
        ):
            kept.append(original[idx])
            continue
        text = msg.get("content")
        kept.append(
            types.Content(
                role="user",
                parts=[types.Part(text=str(text) if text is not None else "")],
            )
        )
    return _repair_orphans_nonprefix(kept)


def deterministic_truncate(
    contents: list[Any], budget: int, model: str | None
) -> list[Any]:
    """Guaranteed-reduction fail-safe: keep the first round + a budgeted tail.

    Keeps the first round intact, walks the tail backward accumulating estimated
    tokens until ``budget`` is reached, drops the middle, and inserts a single
    user-role continuity marker at the drop point (SC-4). The marker is merged/
    skipped when an ADK ``role == "user"`` Content already borders the seam (a real
    user turn OR a function_response, both ADK-user) so it never creates a
    two-consecutive-user shape some providers reject (E16). Pure list slicing, so a
    failure is not expected; wrapped to return the input unchanged on any error
    (SC-3). Always returns ``<= len(contents)``.
    """
    from google.genai import types  # noqa: PLC0415

    try:
        if len(contents) <= 1:
            return list(contents)
        first_round_end = len(contents)
        for index in range(1, len(contents)):
            if _content_strategy_role(contents[index]) == "user":
                first_round_end = index
                break
        head = list(contents[:first_round_end])
        tail_candidates = list(contents[first_round_end:])
        if not tail_candidates:
            return _repair_orphans_nonprefix(head)  # single round: already minimal
        acc = sum(_content_token_estimate(content) for content in head)
        kept_tail: list[Any] = []
        for content in reversed(tail_candidates):
            estimate = _content_token_estimate(content)
            if kept_tail and acc + estimate > budget:
                break
            kept_tail.insert(0, content)
            acc += estimate
        if not kept_tail:
            kept_tail = [tail_candidates[-1]]
        if len(kept_tail) < len(tail_candidates):
            first_kept_user = getattr(kept_tail[0], "role", None) == "user"
            last_head_user = bool(head) and getattr(head[-1], "role", None) == "user"
            if first_kept_user or last_head_user:
                rebuilt = head + kept_tail
            else:
                marker = types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            text="[older context truncated to fit the model window]"
                        )
                    ],
                )
                rebuilt = head + [marker] + kept_tail
        else:
            rebuilt = head + kept_tail
        return _repair_orphans_nonprefix(rebuilt)
    except Exception:
        return list(contents)


def build_context_compaction_plugin(
    *,
    enabled: bool,
    token_threshold: int,
    tail_events: int,
    real_tokens_enabled: bool = False,
    real_tokens_pct: float = 0.75,
    output_reserve: int = 8_000,
    tool_prune_enabled: bool = False,
    prune_protect: int = _PRUNE_PROTECT_DEFAULT,
    prune_minimum: int = _PRUNE_MINIMUM_DEFAULT,
    summarize_enabled: bool = False,
    summary_model: str | None = None,
    summary_timeout: float = _SUMMARY_TIMEOUT_DEFAULT,
    anchored_summary_enabled: bool = False,
    summary_max_failures: int = 3,
    manual_enabled: bool = False,
    proactive_recovery_enabled: bool = False,
    proactive_critical_pct: float = 0.90,
) -> MagiContextCompactionPlugin | None:
    """Return a configured plugin, or ``None`` when the feature is disabled.

    The flag/budget are owned by ``magi_agent.config.env`` (single source);
    callers pass the resolved values here so this module stays import-light and
    free of env-parsing concerns. The real-token and tool-prune args are additive
    and default-OFF so existing callers are byte-identical.
    """
    if not enabled:
        return None
    return MagiContextCompactionPlugin(
        token_threshold=token_threshold,
        tail_events=tail_events,
        real_tokens_enabled=real_tokens_enabled,
        real_tokens_pct=real_tokens_pct,
        output_reserve=output_reserve,
        tool_prune_enabled=tool_prune_enabled,
        prune_protect=prune_protect,
        prune_minimum=prune_minimum,
        summarize_enabled=summarize_enabled,
        summary_model=summary_model,
        summary_timeout=summary_timeout,
        anchored_summary_enabled=anchored_summary_enabled,
        summary_max_failures=summary_max_failures,
        manual_enabled=manual_enabled,
        proactive_recovery_enabled=proactive_recovery_enabled,
        proactive_critical_pct=proactive_critical_pct,
    )


__all__ = [
    "ANCHOR_SUMMARY_STATE_KEY",
    "CONTEXT_COMPACTION_PLUGIN_NAME",
    "PROACTIVE_SUMMARY_FAILURE_COUNT_STATE_KEY",
    "REAL_PROMPT_TOKENS_STATE_KEY",
    "SUMMARY_FAILURE_COUNT_STATE_KEY",
    "CompactionCapability",
    "MagiContextCompactionPlugin",
    "build_context_compaction_plugin",
    "contents_to_msgs",
    "deterministic_truncate",
    "msgs_to_contents",
]

# Exported for tests asserting the placeholder shape; not part of the public API.
__all__.append("_PRUNED_TOOL_OUTPUT_PLACEHOLDER")
