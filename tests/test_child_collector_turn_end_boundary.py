"""Real-trace multi-attempt boundary semantics for the governed child collector.

PR-U follow-up to PR-T (#1268). Design doc:
``/tmp/pr-u-child-collector-design.md`` in the sending environment; PR body
summarizes the essentials.

Motivation
----------
Kevin's hosted 0.1.110 (which already includes PR-T) still shows the
concat bug:

* Opus 4-8: SpawnAgent output_preview ``"22"`` (no separator concat).
* Opus 4-6 / Gemini 3.1 Pro / GPT-5.5 pro: ``"2\n2"`` (newline-separated).
* OSS local 0.1.110 reproduces the same shape.

Root cause: ``response_clear`` is emitted by
``adk_bridge/event_adapter.py:1050-1063`` ONLY on ADK rewind or an explicit
``custom_metadata["response_clear"] == True`` marker. A normal ADK tool_use
loop, an engine re-invocation (recovery / grace / nudge), or a plain
multi-attempt run emits NO ``response_clear``. PR-T's synthetic tests
covered a shape the real traces never produce.

The ``turn_end`` payload IS emitted for every completed ADK response block
(the driver constructs the bridge with ``live_compatible=True``
unconditionally, ``driver.py:1523``) AND is DELIBERATELY SUPPRESSED during
output-cap continuation (``driver.py:1746-1748``). So ``turn_end`` is a
semantically precise "this response block is complete; any later text is a
NEW answer" signal, not a heuristic. ``tool_end`` closes the classic
preliminary-text-before-tool shape (that turn produces zero ``turn_end``
between preliminary and final text).

Contract pinned here
--------------------
* ``turn_end`` (primary) and ``tool_end`` (secondary) set a deferred
  ``boundary_pending`` flag.
* Clearing happens on the NEXT non-empty ``text_delta``. This is fail-soft:
  turns whose last block has no trailing text keep their prior answer
  intact; single-response turns stay byte-identical (final ``turn_end``
  arrives after the final text, flag set but never consumed).
* PR-T's ``response_clear`` immediate reset is preserved for
  ADK-rewind / explicit clears.
* ``evidence_refs`` accumulation is untouched by the boundary.

TDD: written before the implementation so the RED → GREEN transition is
observable.
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.runtime.child_governed_collector import collect_governed_child_turn
from magi_agent.runtime.events import RuntimeEvent


def _run(stream):
    # Drop the 4th tuple element (trip_reason, Fix F) so the existing
    # 3-tuple unpack call sites below are unchanged.
    summary, refs, status, _trip = asyncio.run(collect_governed_child_turn(stream))
    return summary, refs, status


def test_turn_end_between_two_deltas_keeps_only_second():
    """Kevin's Opus 4-8 ``"22"`` repro: two response blocks separated by
    ``turn_end committed``. Only the second block survives.
    """

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield RuntimeEvent(
            type="status",
            payload={
                "type": "turn_end",
                "turnId": "turn:child-1",
                "status": "committed",
                "stopReason": "end_turn",
                "expectReceipt": False,
            },
        )
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield EngineResult(
            terminal=Terminal.completed,
            usage={},
            cost_usd=0.0,
            session_id="child-opus48",
            turn_id="child-opus48-t1",
        )

    summary, _refs, status = _run(stream())
    assert summary == "2"
    assert status == "completed"


def test_turn_end_between_deltas_with_newline_delta():
    """Kevin's Opus 4-6 / Gemini / GPT ``"2\\n2"`` repro: first block carries
    a trailing newline. Only the second block's ``"2\\n"`` survives.
    """

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2\n"})
        yield RuntimeEvent(
            type="status",
            payload={
                "type": "turn_end",
                "turnId": "turn:child-2",
                "status": "committed",
                "stopReason": "end_turn",
                "expectReceipt": False,
            },
        )
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2\n"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "2\n"
    assert status == "completed"


def test_tool_end_between_deltas_keeps_only_second():
    """Classic preliminary-text-before-tool shape: the model emits
    ``"Let me check."``, requests a tool, receives the result, then emits
    the final answer. ``turn_end`` does NOT fire between preliminary and
    final text (the mixed text+function_call event is not
    ``is_final_response``); ``tool_end`` DOES. Secondary boundary closes it.
    """

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield RuntimeEvent(
            type="tool",
            payload={"type": "tool_start", "name": "Calculation"},
        )
        yield RuntimeEvent(
            type="tool",
            payload={"type": "tool_end", "name": "Calculation"},
        )
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "2"
    assert status == "completed"


def test_turn_end_with_no_following_delta_keeps_prior():
    """Fail-soft: single-response turn ends on ``turn_end`` with no trailing
    text. Deferred reset is never consumed; ``summary`` remains the answer
    text. This case is the dominant shape and must be byte-identical to
    pre-fix behavior.
    """

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "answer"})
        yield RuntimeEvent(
            type="status",
            payload={
                "type": "turn_end",
                "turnId": "turn:only",
                "status": "committed",
                "stopReason": "end_turn",
                "expectReceipt": False,
            },
        )
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "answer"
    assert status == "completed"


def test_response_clear_still_resets_pr_t_semantic_preserved():
    """PR-T back-compat: ``response_clear`` still resets the accumulator
    immediately (ADK rewind / explicit clear). Independent of the new
    deferred-boundary path.
    """

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "A"})
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "B"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "B"
    assert status == "completed"


def test_single_response_block_byte_identical():
    """Trivial single-block turn stays byte-identical: no boundary event,
    no reset, ``summary`` is the concatenation of every delta as before.
    """

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "hi"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "hi"
    assert status == "completed"


def test_turn_end_does_not_touch_evidence_refs():
    """Evidence refs harvested from tool payloads before AND after a
    ``turn_end`` boundary must both survive. The boundary flag is a
    text-only reset; ``_collect_public_refs`` runs on every payload.
    """

    async def stream():
        yield RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_call",
                "evidence_ref": "evidence:before-boundary",
            },
        )
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "prelim"})
        yield RuntimeEvent(
            type="status",
            payload={
                "type": "turn_end",
                "turnId": "turn:evidence",
                "status": "committed",
                "stopReason": "end_turn",
                "expectReceipt": False,
            },
        )
        yield RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_call",
                "evidence_ref": "evidence:after-boundary",
            },
        )
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "final"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, refs, status = _run(stream())
    assert summary == "final"
    assert status == "completed"
    assert "evidence:before-boundary" in refs
    assert "evidence:after-boundary" in refs
