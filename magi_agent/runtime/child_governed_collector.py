"""Governed-stream to child-envelope adapter.

Consumes the ``run_governed_turn`` async-generator and produces the
``(summary, evidence_refs, status)`` tuple the child boundary expects.

Design notes
------------
- ``_collect_public_refs`` is re-implemented locally (identical logic to
  ``MagiEngineDriver._collect_public_refs`` in ``cli.engine``) to avoid
  importing the heavyweight engine module from a ``runtime`` submodule, which
  would introduce an unwanted ``runtime`` -> ``cli.engine`` dependency.
- ``_public_evidence_refs`` is imported from ``child_runner_live`` because it
  lives in the same ``runtime`` package and carries no circular-import risk.
- ``_MAX_SUMMARY_CHARS`` is re-exported from ``child_runner_live`` so the
  constant stays single-source.

Response-block boundaries (PR-T + PR-U)
---------------------------------------
A single child turn can contain multiple ADK response blocks: the model may
emit a preliminary text_delta, request a tool, receive the result, and then
emit a fresh text_delta as the final answer. Also, the engine's outer
re-invocation loop (recovery / grace / nudge) drives multiple attempts under
one ``turn_id`` and each attempt drains a fresh ADK stream. A flat
accumulator would concatenate ``"prelim" + "final"`` (or, on Kevin's real
0.1.110 traces, ``"22"`` and ``"2\\n2"``) instead of returning the final
answer.

PR-T introduced ``response_clear`` handling, which is CORRECT for the ADK
rewind path (``adk_bridge/event_adapter.py:1050-1063``:
``actions.rewind_before_invocation_id`` or explicit
``custom_metadata["response_clear"]``). But normal engine re-invocations and
tool_use loops never emit ``response_clear``, so PR-T never fired on the
shipped traces.

PR-U adds the deferred-boundary path that DOES fire on the real trace:

- ``turn_end`` (primary boundary) is emitted for every completed ADK
  response block on this path (the driver constructs the bridge with
  ``live_compatible=True`` unconditionally, ``driver.py:1523``, and
  ``event_adapter.py:779-790`` projects one on every
  ``_event_is_final_response`` event). Kevin's trace has this event twice,
  once between the ``"2"`` blocks and once at the end.
- ``turn_end`` is DELIBERATELY SUPPRESSED by the engine when a follow-up
  attempt will continue (output-cap continuation,
  ``driver.py:1746-1748``). That is exactly the multi-attempt case where
  text concatenation IS correct. So ``turn_end`` is a semantically precise
  "this response block is complete; any later text is a NEW answer" signal,
  not a heuristic.
- ``tool_end`` (secondary boundary) closes the classic
  preliminary-text-before-tool shape: preliminary prose, function_call
  (which is not final-response so no ``turn_end`` fires), function_response,
  then final prose.

Deferred reset (clear on the NEXT non-empty text_delta, not on the boundary
event itself) is what makes this fail-soft:

- Turns whose last event family is a boundary WITH no trailing text keep
  their earlier text (empty follow-up attempts, tool-end-terminated turns).
- Single-response turns stay byte-identical: the only ``turn_end`` is the
  last one, no text follows it, the flag is set and never consumed.

Untouched by the boundary:

- ``raw_refs`` accumulation: evidence refs are collected across the whole
  stream. Tool receipts gathered mid-attempt remain valid evidence for the
  terminal answer.
- ``items_yielded`` counting: every non-terminal event counts, including
  boundaries. The PR-K ``terminal_consumed`` trace continues to detect
  silent-empty dispatches honestly.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Mapping
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    import asyncio

from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.runtime.child_runner_live import (
    _MAX_SUMMARY_CHARS as _MAX_SUMMARY_CHARS,
    _maybe_log_trace_governed_collector_terminal,
    _public_evidence_refs,
)
from magi_agent.runtime.events import RuntimeEvent

__all__ = [
    "collect_governed_child_turn",
    "_MAX_SUMMARY_CHARS",
]


def _collect_public_refs(value: object, refs: set[str]) -> None:
    """Recursively harvest public ref strings from an event payload.

    Mirrors ``MagiEngineDriver._collect_public_refs`` exactly; kept local to
    avoid a heavy ``cli.engine`` import from within the ``runtime`` package.
    """
    if isinstance(value, str):
        if value.startswith(
            ("evidence:", "verifier:", "receipt:sha256:", "sha256:")
        ):
            refs.add(value)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_public_refs(nested, refs)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _collect_public_refs(nested, refs)


async def collect_governed_child_turn(
    stream: AsyncGenerator[Union[RuntimeEvent, EngineResult], None],
    *,
    missing_tool_streak_cap: int = 0,
    cancel: "asyncio.Event | None" = None,
) -> tuple[str, tuple[str, ...], str, str | None]:
    """Consume *stream* and return ``(summary, evidence_refs, status, trip_reason)``.

    Parameters
    ----------
    stream:
        Async-generator yielding :class:`~magi_agent.runtime.events.RuntimeEvent`
        objects followed by a terminal :class:`~magi_agent.cli.contracts.EngineResult`
        as its final item.  This matches the ``EngineDriver.run_turn_stream``
        consumption convention defined in ``cli.contracts``.

    Returns
    -------
    summary:
        Concatenation of all ``event.payload["delta"]`` values for every
        ``RuntimeEvent`` whose ``payload["type"] == "text_delta"``, trimmed to
        ``_MAX_SUMMARY_CHARS``.
    evidence_refs:
        Tuple of deduplicated ``evidence:``-namespaced ref strings harvested
        from every event payload.
    status:
        ``"completed"`` when the terminal ``EngineResult`` carries
        ``Terminal.completed``; ``"failed"`` for any other terminal value.
    trip_reason:
        ``None`` normally; the typed missing-tool-streak reason when
        ``missing_tool_streak_cap`` (Fix F) is reached (the child spiraled on
        tools it does not have). When it trips, ``cancel`` is signalled so the
        driver winds the turn down, and the caller raises the typed
        ``_ChildLlmTurnError`` with this reason to preserve it (the terminal
        would otherwise map to a generic ``failed``). ``cap <= 0`` disables the
        guard (byte-identical collection: this stays ``None``).

    Raises
    ------
    ValueError
        If the stream ends without yielding an ``EngineResult`` terminal.
    """
    from magi_agent.runtime.child_missing_tool_guard import (  # noqa: PLC0415
        MISSING_TOOL_ERROR_CODES,
        MISSING_TOOL_STREAK_REASON,
        MissingToolStreak,
    )

    text_chunks: list[str] = []
    raw_refs: set[str] = set()
    terminal: EngineResult | None = None
    _streak = MissingToolStreak(missing_tool_streak_cap)
    trip_reason: str | None = None
    # PR-K: count non-terminal events drained from the stream so the
    # terminal_consumed trace can surface the silent-empty-dispatch
    # signature (items_yielded == 0).
    items_yielded = 0
    # PR-U: deferred response-block boundary. Set on ``turn_end`` (primary)
    # and ``tool_end`` (secondary); consumed by clearing ``text_chunks`` on
    # the NEXT non-empty ``text_delta``. Deferred clearing is fail-soft: a
    # boundary without following text (single-response turns, tool-end
    # terminated turns, empty follow-up attempts) leaves the accumulated
    # text intact. See module docstring for the full rationale.
    boundary_pending = False

    async for item in stream:
        if isinstance(item, EngineResult):
            terminal = item
            # Convention: EngineResult is the FINAL item — stop consuming.
            break

        # item is a RuntimeEvent
        items_yielded += 1
        payload = item.payload
        _collect_public_refs(payload, raw_refs)

        payload_type = payload.get("type")
        if payload_type == "response_clear":
            # PR-T: ADK rewind or explicit clear. Immediate reset (not
            # deferred): the rewind semantic says the prior text no longer
            # exists in the invocation. Evidence refs are intentionally
            # preserved (see module docstring).
            text_chunks.clear()
            boundary_pending = False
            continue
        if payload_type == "tool_end":
            # Fix F: fold this tool result into the missing-tool streak. A
            # missing-tool result (status=error + errorCode in the missing set)
            # increments; any other tool_end resets. A non-error tool_end (ok)
            # also resets. cap<=0 => _streak.update always returns False.
            _err_code = payload.get("errorCode") or payload.get("error_code")
            _is_missing = (
                payload.get("status") == "error"
                and isinstance(_err_code, str)
                and _err_code in MISSING_TOOL_ERROR_CODES
            )
            if _streak.update(True if _is_missing else False):
                trip_reason = MISSING_TOOL_STREAK_REASON
                if cancel is not None:
                    cancel.set()
                # Keep consuming until the terminal EngineResult so the
                # "terminal is the final item" convention holds; the caller
                # raises the typed error to preserve trip_reason.
        if payload_type == "turn_end" or payload_type == "tool_end":
            # PR-U: real-trace response-block boundary. Do NOT clear here;
            # arm the flag so the NEXT non-empty ``text_delta`` clears
            # first and starts a fresh block. Deferred so a turn that ends
            # on this event keeps its accumulated text (fail-soft).
            boundary_pending = True
            continue
        if payload_type == "text_delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                if boundary_pending:
                    text_chunks.clear()
                    boundary_pending = False
                text_chunks.append(delta)

    if terminal is None:
        # Fix F defensive: if the missing-tool guard tripped and signalled
        # ``cancel``, the engine may end the stream WITHOUT a terminal
        # EngineResult. Preserve the typed trip reason (a failed turn) rather
        # than raising the generic ValueError that the caller would flatten to
        # ``child_turn_error``, losing the whole point of the guard.
        if trip_reason is not None:
            summary = "".join(text_chunks)[:_MAX_SUMMARY_CHARS]
            evidence_refs = _public_evidence_refs(list(raw_refs))
            return summary, evidence_refs, "failed", trip_reason
        raise ValueError(
            "collect_governed_child_turn: stream ended with no terminal EngineResult"
        )

    summary = "".join(text_chunks)[:_MAX_SUMMARY_CHARS]
    evidence_refs = _public_evidence_refs(list(raw_refs))
    status = "completed" if terminal.terminal is Terminal.completed else "failed"

    # PR-K: deeper terminal-consumed trace. Surfaces the actual Terminal
    # enum NAME (vs the binary completed / failed status the collector
    # returns), the counts, and any error_code / reason / error fields
    # the EngineResult carries. Default-OFF (MAGI_CHILD_RUNNER_EMPTY_DEBUG);
    # fail-safe inside the helper so trace bookkeeping never breaks a turn.
    _maybe_log_trace_governed_collector_terminal(
        os.environ,
        terminal=terminal,
        status=status,
        summary_len=len(summary),
        evidence_refs_count=len(evidence_refs),
        items_yielded=items_yielded,
    )

    return summary, evidence_refs, status, trip_reason
