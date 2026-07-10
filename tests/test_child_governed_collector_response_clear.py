"""Response-clear reset semantics for the governed child collector (PR-T).

Kevin 0.1.108 SOTA-spawn direct-debug: an Opus/Gemini child ran a normal ADK
tool-use loop (attempt=1 requested a Calculation tool and emitted a preliminary
text_delta "2"; the tool ran; attempt=2 saw the result and emitted a final
text_delta "2"). Both text_delta events survived to the observability stream
and the child collector concatenated them into "22" because it flat-appended
every text_delta with no reset on the response-block boundary.

``_BookendCollector`` in ``magi_agent/runtime/governed_turn.py`` already models
the correct semantic: ``response_clear`` marks a new response block and the
in-progress result text is dropped. This test module pins that same semantic
into ``collect_governed_child_turn`` so multi-attempt turns return the FINAL
response block only, not the concatenation of every intermediate one.

Evidence refs behave differently: they accumulate across the whole stream
(tool receipts collected mid-attempt are still valid evidence for the terminal
answer), so a ``response_clear`` must NOT wipe the ref set.

TDD: written before the implementation exists (RED to GREEN).
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


def test_response_clear_between_two_deltas_keeps_only_the_last():
    """Preliminary "2" is dropped when a response_clear arrives; final "2" wins."""

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield EngineResult(
            terminal=Terminal.completed,
            usage={},
            cost_usd=0.0,
            session_id="child-multi",
            turn_id="child-multi-t1",
        )

    summary, _refs, status = _run(stream())
    assert summary == "2"
    assert status == "completed"


def test_multiple_response_clears_keep_final_response_block():
    """Three attempts, only the last delta contributes to the summary."""

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "A"})
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "B"})
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "C"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "C"
    assert status == "completed"


def test_response_clear_does_not_touch_evidence_refs():
    """Evidence refs accumulated in an earlier response block must survive a clear."""

    async def stream():
        yield RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_call",
                "evidence_ref": "evidence:calc-1",
            },
        )
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "prelim"})
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "final"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, refs, _status = _run(stream())
    assert summary == "final"
    assert "evidence:calc-1" in refs


def test_response_clear_counts_in_items_yielded():
    """PR-K trace surface: non-terminal events (including response_clear) all count.

    Contract: items_yielded is emitted through
    ``_maybe_log_trace_governed_collector_terminal``. We assert it is passed the
    correct total by patching the helper and inspecting the captured kwargs.
    """
    from unittest.mock import patch

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "a"})
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "b"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    captured: dict[str, object] = {}

    def _capture(env, *, terminal, status, summary_len, evidence_refs_count, items_yielded):
        captured["items_yielded"] = items_yielded
        captured["summary_len"] = summary_len
        captured["status"] = status

    with patch(
        "magi_agent.runtime.child_governed_collector._maybe_log_trace_governed_collector_terminal",
        side_effect=_capture,
    ):
        summary, _refs, status = _run(stream())

    assert captured["items_yielded"] == 3
    assert captured["summary_len"] == 1  # only "b" survives
    assert captured["status"] == "completed"
    assert summary == "b"


def test_no_response_clear_preserves_byte_identical_behavior():
    """Back-compat: a stream with no response_clear still concatenates as before."""

    async def stream():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "he"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "llo"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "hello"
    assert status == "completed"


def test_response_clear_with_no_prior_deltas_is_noop():
    """A response_clear on an empty accumulator must not raise."""

    async def stream():
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "hi"})
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _refs, status = _run(stream())
    assert summary == "hi"
    assert status == "completed"


def test_kevin_repro_shape_opus_two_response_blocks():
    """Reproduce Kevin's exact 0.1.108 SOTA-spawn shape.

    attempt=1: model emits preliminary text_delta "2" and requests Calculation.
    Calculation returns value=2.
    attempt=2: model emits final text_delta "2".

    Before the fix the summary was "22". After the fix it must be "2".
    """

    async def stream():
        # attempt 1: preliminary answer + tool call
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_call",
                "name": "Calculation",
                "evidence_ref": "evidence:calc-2",
            },
        )
        # boundary between attempt 1 and attempt 2
        yield RuntimeEvent(type="status", payload={"type": "response_clear"})
        # attempt 2: final answer after seeing tool result
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "2"})
        yield EngineResult(
            terminal=Terminal.completed,
            usage={},
            cost_usd=0.0,
            session_id="child-opus",
            turn_id="child-opus-t1",
        )

    summary, refs, status = _run(stream())
    assert summary == "2"  # NOT "22"
    assert status == "completed"
    assert "evidence:calc-2" in refs
