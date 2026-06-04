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

# Lower bound on the event-count threshold so a small tail does not force a
# spurious event-count breach. The token threshold is the primary signal at the
# before-model seam; the event-count threshold mirrors the boundary default.
_EVENT_COUNT_THRESHOLD_DEFAULT = 128


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
    ) -> None:
        super().__init__(name)
        if token_threshold < 1:
            raise ValueError("token_threshold must be >= 1")
        if tail_events < 1:
            raise ValueError("tail_events must be >= 1")
        if event_count_threshold < 1:
            raise ValueError("event_count_threshold must be >= 1")
        self.token_threshold = token_threshold
        self.tail_events = tail_events
        self.event_count_threshold = event_count_threshold
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
        """
        try:
            contents = list(getattr(llm_request, "contents", None) or [])
            if len(contents) <= self.tail_events:
                # Nothing to trim even in the worst case; skip the boundary call.
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
            llm_request.contents = contents[split_index:]
        except Exception:
            # Fail-open: compaction must never break a live model turn. Leaving
            # contents untouched is the no-plugin behaviour.
            return None
        return None

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
) -> MagiContextCompactionPlugin | None:
    """Return a configured plugin, or ``None`` when the feature is disabled.

    The flag/budget are owned by ``magi_agent.config.env`` (single source);
    callers pass the resolved values here so this module stays import-light and
    free of env-parsing concerns.
    """
    if not enabled:
        return None
    return MagiContextCompactionPlugin(
        token_threshold=token_threshold,
        tail_events=tail_events,
    )


__all__ = [
    "CONTEXT_COMPACTION_PLUGIN_NAME",
    "MagiContextCompactionPlugin",
    "build_context_compaction_plugin",
]
