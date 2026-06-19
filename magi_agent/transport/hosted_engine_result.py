"""Async collector: engine event stream → Gate5B4C3LiveRunnerBoundaryResult.

PR3 of the flip series.  Consumes the ``AsyncGenerator`` produced by
``run_governed_turn`` (via the engine's ``run_turn_stream``) and assembles a
fully-populated :class:`Gate5B4C3LiveRunnerBoundaryResult` that is
wire-compatible with what ``Gate5B4C3LiveRunnerBoundary`` produces today.
Downstream code in ``chat_routes`` (counter finish, report_digest, response
JSON building) works unchanged when PR4 substitutes this collector for the
legacy boundary.

PR4 integration (compose in chat_routes):
    ctx = hosted_request_to_turn_context(generation)
    rt  = build_hosted_runtime(...)
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=config,
        diagnostic=diagnostic,
        event_stream=run_governed_turn(ctx, runtime=rt),
        started_at_monotonic=time.monotonic(),
        timeout_ms=generation.budgets.python_runner_timeout_ms,
    )

Design notes
------------
* ``headless.drain`` is reused verbatim — it consumes the ``AsyncGenerator``
  and captures the terminal ``EngineResult`` as the final yielded item.
* ``event_count`` counts only ``RuntimeEvent`` items (not the terminal
  ``EngineResult``).  This matches gate5b4c3's convention where its runner
  loop counts events from the ADK stream, not the synthetic terminal.
* ``usage_internal``: the engine stores usage in snake_case keys
  (``input_tokens``, ``output_tokens``, ``cache_read_tokens``); gate5b4c3
  stores camelCase (``inputTokens``, ``outputTokens``, ``cacheReadTokens``).
  This module translates on the way out so the field shape matches existing
  gate5b4c3 logic.
* ``user_visible_output``: the spec says to mirror ``output_text_internal``,
  but ``Gate5B4C3LiveRunnerBoundaryResult``'s ``@model_validator(mode='before')``
  hard-overrides ``userVisibleOutput`` to ``None`` unconditionally (line ~409 of
  gate5b4c3_live_runner_boundary.py).  We pass the value anyway so the field is
  explicitly set; the validator will force it to ``None`` as intended.
* Status/reason mapping (no "client_aborted" literal exists):
    Terminal.completed → status="completed", reason="runner_completed"
    Terminal.aborted   → status="error",     reason="runner_error"
    Terminal.max_turns → status="error",     reason="runner_incomplete"
    Terminal.error     → status="error",     reason="runner_error"
  ``asyncio.CancelledError`` is re-raised — PR4's chat_routes handler is
  responsible for aborting the response.
* ``adk_invoked``, ``runner_attempted``, ``model_call_via_adk_runner_attempted``
  are all set to ``True`` because the engine did invoke the ADK runner.
  ``fail_open`` is ``True`` (gate5b4c3 default for all live-runner results).
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.headless import drain
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundaryResult,
)

if TYPE_CHECKING:
    from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
        Gate5B4C3ShadowGenerationConfig,
        Gate5B4C3ShadowGenerationDiagnostic,
        Gate5B4C3ShadowGenerationRequest,
    )

# ---------------------------------------------------------------------------
# Status / reason mapping
# ---------------------------------------------------------------------------

_TERMINAL_TO_STATUS_REASON: dict[Terminal, tuple[str, str]] = {
    Terminal.completed: ("completed", "runner_completed"),
    Terminal.aborted:   ("error",     "runner_error"),
    Terminal.max_turns: ("error",     "runner_incomplete"),
    Terminal.error:     ("error",     "runner_error"),
}


def _map_terminal(
    terminal: EngineResult,
) -> tuple[str, str]:
    """Map a terminal EngineResult to (status, reason) literals."""
    return _TERMINAL_TO_STATUS_REASON.get(
        terminal.terminal,
        ("error", "runner_error"),  # safe fallback for unknown terminals
    )


# ---------------------------------------------------------------------------
# Usage key translation (engine snake_case → gate5b4c3 camelCase)
# ---------------------------------------------------------------------------

_USAGE_KEY_MAP: dict[str, str] = {
    "input_tokens":       "inputTokens",
    "output_tokens":      "outputTokens",
    "cache_read_tokens":  "cacheReadTokens",
    "total_tokens":       "totalTokens",
}


def _translate_usage(raw: dict[str, object]) -> dict[str, int] | None:
    """Convert engine snake_case usage keys to gate5b4c3 camelCase.

    Returns ``None`` when the dict is empty or contains no known keys.
    The engine's ``_fold_usage`` already coerces values to int; we guard
    with ``int(v)`` for defensiveness.
    """
    out: dict[str, int] = {}
    for src_key, dst_key in _USAGE_KEY_MAP.items():
        value = raw.get(src_key)
        if value is not None:
            try:
                out[dst_key] = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
    return out if out else None


# ---------------------------------------------------------------------------
# Public collector
# ---------------------------------------------------------------------------


async def collect_engine_to_boundary_result(
    *,
    generation: "Gate5B4C3ShadowGenerationRequest",
    config: "Gate5B4C3ShadowGenerationConfig",
    diagnostic: "Gate5B4C3ShadowGenerationDiagnostic",
    event_stream: AsyncGenerator[object, None],  # type: ignore[type-arg]
    started_at_monotonic: float,
    timeout_ms: int = 0,
) -> Gate5B4C3LiveRunnerBoundaryResult:
    """Consume the engine event stream and return a boundary result.

    Parameters
    ----------
    generation:
        The validated shadow-generation request (used for provider/model/
        routing_source/timeout_ms fields).
    config:
        Shadow-generation config (currently carried for caller completeness;
        PR4 may use it for gate-enforcement logic).
    diagnostic:
        The pre-built ``Gate5B4C3ShadowGenerationDiagnostic`` for this turn.
    event_stream:
        ``AsyncGenerator`` as returned by ``run_governed_turn``.
        Consumed exactly once; the generator is closed on return.
    started_at_monotonic:
        ``time.monotonic()`` snapshot taken by the caller before the turn was
        initiated.  Used to compute ``latency_ms``.
    timeout_ms:
        Informational timeout budget passed into the result (does not enforce
        a timeout here — PR4/middleware is responsible).  Defaults to 0 when
        not supplied.

    Returns
    -------
    Gate5B4C3LiveRunnerBoundaryResult
        Fully-populated result, compatible with the shape produced by the
        legacy ``Gate5B4C3LiveRunnerBoundary`` for the success/error paths.

    Raises
    ------
    asyncio.CancelledError
        Re-raised as-is so PR4's chat_routes handler can abort the response.
        All other exceptions from the engine are captured into the terminal
        ``EngineResult`` by the engine itself; ``drain`` synthesises an error
        terminal when the generator completes without one.
    """
    # ``drain`` is the canonical consumer from headless.py: collects
    # RuntimeEvent items, captures the terminal EngineResult as the final
    # yielded item, and closes the generator.
    events, terminal = await drain(event_stream)  # type: ignore[arg-type]

    # Aggregate text from text_delta events only.
    text_chunks: list[str] = []
    for evt in events:
        if isinstance(evt, dict) and evt.get("type") == "text_delta":
            delta = evt.get("delta")
            if isinstance(delta, str):
                text_chunks.append(delta)

    output_text = "".join(text_chunks) or None
    status, reason = _map_terminal(terminal)
    usage = _translate_usage(terminal.usage) if terminal.usage else None
    latency_ms = int((time.monotonic() - started_at_monotonic) * 1000)

    return Gate5B4C3LiveRunnerBoundaryResult(
        # --- identity ---
        diagnostic=diagnostic.model_dump(by_alias=True, mode="python", warnings=False),
        status=status,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        # --- routing ---
        selectedProvider=generation.model_routing.provider_label,
        selectedModel=generation.model_routing.model_label,
        routingSource=generation.model_routing.routing_source,
        # --- timing ---
        latencyMs=latency_ms,
        timeoutMs=timeout_ms,
        # --- engine flags ---
        adkInvoked=True,
        runnerAttempted=True,
        modelCallViaAdkRunnerAttempted=True,
        failOpen=True,
        # --- events ---
        eventCount=len(events),
        # --- kwargs keys (engine constructs internally) ---
        agentKwargsKeys=(),
        runnerKwargsKeys=(),
        runAsyncKwargsKeys=(),
        # --- errors ---
        errorClass=None,
        errorPreview=None,
        runnerErrorDiagnostic=None,
        # --- output ---
        outputTextInternal=output_text,
        usageInternal=usage,
        # Note: userVisibleOutput is hard-overridden to None by pydantic
        # model_validator in Gate5B4C3LiveRunnerBoundaryResult; we pass None
        # explicitly to document intent.
        userVisibleOutput=None,
    )


__all__ = ["collect_engine_to_boundary_result"]
