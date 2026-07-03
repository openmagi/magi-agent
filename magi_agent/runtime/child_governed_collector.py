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

Response-block boundaries (PR-T)
--------------------------------
A single child turn can contain multiple ADK response blocks: the model may
emit a preliminary text_delta, request a tool, receive the result, and then
emit a fresh text_delta as the final answer. ADK signals the boundary with a
``response_clear`` payload. Both text_delta events survive to the stream, so
a flat accumulator would concatenate ``"prelim" + "final"`` instead of
returning the final answer.

``_BookendCollector`` in :mod:`magi_agent.runtime.governed_turn` handles this
correctly by resetting its in-progress text on ``response_clear``. This module
mirrors the same semantic:

- ``response_clear`` clears the in-progress ``text_chunks`` accumulator so the
  summary reflects only the final response block.
- ``response_clear`` does NOT touch ``raw_refs``: evidence refs are collected
  across the whole stream (tool receipts gathered mid-attempt remain valid
  evidence for the terminal answer).
- ``response_clear`` still counts toward ``items_yielded`` so the PR-K
  ``terminal_consumed`` trace continues to detect silent-empty dispatches
  honestly.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Mapping
from typing import Union

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
) -> tuple[str, tuple[str, ...], str]:
    """Consume *stream* and return ``(summary, evidence_refs, status)``.

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

    Raises
    ------
    ValueError
        If the stream ends without yielding an ``EngineResult`` terminal.
    """
    text_chunks: list[str] = []
    raw_refs: set[str] = set()
    terminal: EngineResult | None = None
    # PR-K: count non-terminal events drained from the stream so the
    # terminal_consumed trace can surface the silent-empty-dispatch
    # signature (items_yielded == 0).
    items_yielded = 0

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
            # New ADK response block: drop the in-progress text_chunks so the
            # summary reflects only the final block, not the concatenation of
            # every intermediate one. Evidence refs are intentionally
            # preserved (see module docstring).
            text_chunks.clear()
            continue
        if payload_type == "text_delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                text_chunks.append(delta)

    if terminal is None:
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

    return summary, evidence_refs, status
