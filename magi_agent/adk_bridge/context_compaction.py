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
        # Per-before-model stash of the real prompt-token count + model id, read
        # off ``callback_context.state`` and threaded to ``_trim_request`` without
        # touching the ``CompactionCapability.trim(llm_request)`` signature. Set at
        # the start of ``before_model_callback`` and cleared in ``_trim_request``'s
        # finally, so there is no cross-turn instance leakage.
        self._pending_real_prompt_tokens: int | None = None
        self._pending_model: str | None = None
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

        ctx = ControlPlaneContext.minimal(compaction=CompactionCapability(self))
        return await self.apply_before_model(ctx, llm_request=llm_request)

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
        return None

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
                return None

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
        """
        keep = min(self.tail_events, len(contents))
        split_index = len(contents) - keep
        split_index = _adjust_split_to_avoid_orphan_response(contents, split_index)
        if split_index <= 0:
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
        if self._summarize_enabled and split_index > 0:
            dropped = contents[:split_index]
            if dropped:
                head = await self._build_summary_head(llm_request, dropped)
                if head is not None:
                    llm_request.contents = head + contents[split_index:]
                    return None
        # Fall through to the EXISTING pure tail-drop (byte-identical to today).
        llm_request.contents = contents[split_index:]
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

            transcript = _render_dropped_transcript(dropped)
            model_id = self._summary_model_override or _resolve_model_id(llm_request)
            summary = await _summarize_dropped_prefix(
                model_id, transcript, timeout=self._summary_timeout
            )
            if not summary:
                return None
            summary_content = types.Content(
                role="user",
                parts=[
                    types.Part(text="[Previous conversation summary]\n\n" + summary)
                ],
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


def _render_dropped_transcript(dropped: list[Any]) -> str:
    """Render the dropped prefix Contents to a bounded plain-text transcript.

    Modeled on ``AutoCompactionEngine._format_conversation`` but adapted to ADK
    ``types.Content`` / ``Part``: text parts contribute their text; function_call
    parts render a concise ``[tool_call <name>]`` descriptor; function_response
    parts render ``[tool_result <name>]: <payload>``. Each part segment and each
    Content line is capped, and the whole transcript is capped to
    :data:`_SUMMARY_TRANSCRIPT_MAX_CHARS` (with a truncation marker) so the
    summary prompt stays sane regardless of how large the dropped prefix is.

    Fully duck-typed and fail-soft: a malformed part contributes nothing rather
    than raising (the outer summarize path is fail-open anyway).
    """
    lines: list[str] = []
    used = 0
    truncated = False
    for content in dropped:
        try:
            role = getattr(content, "role", None) or "unknown"
            parts = getattr(content, "parts", None) or []
            segments: list[str] = []
            for part in parts:
                try:
                    text = getattr(part, "text", None)
                    if isinstance(text, str) and text:
                        segments.append(text[:_SUMMARY_SEGMENT_MAX_CHARS])
                        continue
                    fc = getattr(part, "function_call", None)
                    if fc is not None:
                        name = getattr(fc, "name", None) or "?"
                        args = getattr(fc, "args", None)
                        if args:
                            segments.append(
                                f"[tool_call {name} "
                                f"{str(args)[:_SUMMARY_CALL_ARGS_MAX_CHARS]}]"
                            )
                        else:
                            segments.append(f"[tool_call {name}]")
                        continue
                    fr = getattr(part, "function_response", None)
                    if fr is not None:
                        name = getattr(fr, "name", None) or "?"
                        payload = _serialize_response_payload(
                            getattr(fr, "response", None)
                        )
                        segments.append(
                            f"[tool_result {name}]: "
                            f"{payload[:_SUMMARY_RESULT_PAYLOAD_MAX_CHARS]}"
                        )
                except Exception:
                    continue
            if not segments:
                continue
            line = f"[{role}]: {' '.join(segments)}"
        except Exception:
            continue
        if used + len(line) > _SUMMARY_TRANSCRIPT_MAX_CHARS:
            truncated = True
            break
        lines.append(line)
        used += len(line)
    transcript = "\n\n".join(lines)
    if truncated:
        transcript += "\n…[older context truncated]"
    return transcript


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
    model_id: str | None, transcript: str, *, timeout: float
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
        from magi_agent.cli.providers import resolve_provider_config

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
        prompt = AutoCompactionEngine.SUMMARY_PROMPT.format(conversation=transcript)
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
    )


__all__ = [
    "CONTEXT_COMPACTION_PLUGIN_NAME",
    "REAL_PROMPT_TOKENS_STATE_KEY",
    "CompactionCapability",
    "MagiContextCompactionPlugin",
    "build_context_compaction_plugin",
]

# Exported for tests asserting the placeholder shape; not part of the public API.
__all__.append("_PRUNED_TOOL_OUTPUT_PLACEHOLDER")
