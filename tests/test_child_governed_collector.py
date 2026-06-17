"""Tests for the governed-stream → child-envelope adapter.

TDD: written before the implementation exists (RED → GREEN).
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.runtime.events import RuntimeEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _stream_hello():
    """Two token events → 'hello', then a completed EngineResult."""
    yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "he"})
    yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "llo"})
    yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0,
                       session_id="child-abc", turn_id="child-abc-t1")


async def _stream_failed():
    """Single token + an error terminal."""
    yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "oops"})
    yield EngineResult(terminal=Terminal.error, usage={}, cost_usd=0.0)


async def _stream_with_evidence():
    """Token + a tool-call event carrying an evidence ref, then terminal."""
    yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "ok"})
    yield RuntimeEvent(
        type="tool",
        payload={
            "type": "tool_call",
            "evidence_ref": "evidence:abc123",
            "receipt": "receipt:sha256:deadbeef",
        },
    )
    yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)


async def _stream_no_token():
    """No text events — only terminal. Summary should be empty string."""
    yield EngineResult(terminal=Terminal.aborted, usage={}, cost_usd=0.0)


async def _stream_non_text_delta():
    """Token events with a different payload type — should NOT accumulate."""
    yield RuntimeEvent(type="status", payload={"type": "turn.started"})
    yield RuntimeEvent(type="token", payload={"type": "input_delta", "delta": "ignored"})
    yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_collector_aggregates_text_and_maps_status_completed():
    """Basic happy-path: text accumulates, status == 'completed'."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    summary, refs, status = asyncio.run(collect_governed_child_turn(_stream_hello()))
    assert summary == "hello"
    assert status == "completed"
    assert isinstance(refs, tuple)


def test_collector_maps_non_completed_to_failed():
    """Any terminal that is not 'completed' maps to 'failed'."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    _, _, status = asyncio.run(collect_governed_child_turn(_stream_failed()))
    assert status == "failed"


def test_collector_maps_aborted_to_failed():
    """Terminal.aborted → 'failed'."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    _, _, status = asyncio.run(collect_governed_child_turn(_stream_no_token()))
    assert status == "failed"


def test_collector_empty_summary_when_no_text():
    """No text_delta events → empty string summary."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    summary, _, _ = asyncio.run(collect_governed_child_turn(_stream_no_token()))
    assert summary == ""


def test_collector_ignores_non_text_delta_payloads():
    """Events with payload.type != 'text_delta' do not contribute to summary."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    summary, _, _ = asyncio.run(
        collect_governed_child_turn(_stream_non_text_delta())
    )
    assert summary == ""


def test_collector_evidence_refs_are_filtered():
    """Only evidence: refs survive; receipt: prefix is dropped after collection."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    _, refs, _ = asyncio.run(collect_governed_child_turn(_stream_with_evidence()))
    assert isinstance(refs, tuple)
    assert "evidence:abc123" in refs
    # receipt:sha256: is collected by _collect_public_refs but filtered by
    # _public_evidence_refs (keeps only evidence: namespace).
    for ref in refs:
        assert ref.startswith("evidence:"), f"unexpected ref: {ref!r}"


def test_collector_refs_is_always_tuple():
    """Return type contract: refs is always a tuple, even when empty."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    _, refs, _ = asyncio.run(collect_governed_child_turn(_stream_hello()))
    assert isinstance(refs, tuple)


def test_collector_raises_on_stream_with_no_terminal():
    """A stream that ends without yielding an EngineResult should raise."""
    from magi_agent.runtime.child_governed_collector import collect_governed_child_turn

    async def _no_terminal():
        yield RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "x"})

    with pytest.raises(ValueError, match="no terminal"):
        asyncio.run(collect_governed_child_turn(_no_terminal()))


def test_collector_trims_summary_to_max_chars():
    """Summary is trimmed to _MAX_SUMMARY_CHARS."""
    from magi_agent.runtime.child_governed_collector import (
        collect_governed_child_turn,
        _MAX_SUMMARY_CHARS,
    )

    async def _long_stream():
        chunk = "x" * 500
        for _ in range(10):  # 5000 chars total > _MAX_SUMMARY_CHARS (2000)
            yield RuntimeEvent(
                type="token", payload={"type": "text_delta", "delta": chunk}
            )
        yield EngineResult(terminal=Terminal.completed, usage={}, cost_usd=0.0)

    summary, _, _ = asyncio.run(collect_governed_child_turn(_long_stream()))
    assert len(summary) == _MAX_SUMMARY_CHARS
