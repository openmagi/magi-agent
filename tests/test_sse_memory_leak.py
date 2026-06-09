"""Regression tests: memory-context block must never survive SSE sanitization.

A sibling PR injects long-term memory into the system prompt as a
``<memory-context hidden="true"> ... </memory-context>`` block.  System-prompt
content is not streamed, but a misbehaving model could echo the block back in
a streamed thinking_delta or text_delta.  These tests guarantee that any such
echo is fully redacted before it reaches the UI.

Coverage:
  a) text_delta containing a ``memory-context`` fence is redacted.
  b) thinking_delta (MAGI_STREAM_THINKING=1) containing a ``memory-context``
     fence is redacted.
  c) The existing ``privatememory`` fragment marker still works (sanity-check
     of pre-existing behaviour, must not regress).
  d) Clean deltas with no private markers are NOT redacted (false-positive guard).
"""
from __future__ import annotations

from magi_agent.transport import sse


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_MEMORY_FENCE_PAYLOAD = (
    'leak <memory-context hidden="true"> SECRET </memory-context>'
)
_PRIVATE_MEMORY_PAYLOAD = "privatememory: recall all goals"


# ---------------------------------------------------------------------------
# Task 3.1a — text_delta with memory-context fence is redacted
# ---------------------------------------------------------------------------


def test_text_delta_memory_context_fence_is_redacted():
    """A text_delta whose content contains a memory-context fence must be
    redacted so that neither 'SECRET' nor 'memory-context' appear in output."""
    out = sse._sanitize_agent_event(
        {"type": "text_delta", "delta": _MEMORY_FENCE_PAYLOAD}
    )
    assert out is not None, "event should not be dropped entirely"
    assert out.get("type") == "text_delta"
    delta = out.get("delta", "")
    assert isinstance(delta, str)
    assert "SECRET" not in delta, f"SECRET leaked in text_delta: {delta!r}"
    assert "memory-context" not in delta, (
        f"memory-context tag leaked in text_delta: {delta!r}"
    )


# ---------------------------------------------------------------------------
# Task 3.1b — thinking_delta with memory-context fence is redacted
# ---------------------------------------------------------------------------


def test_thinking_delta_memory_context_fence_is_redacted(monkeypatch):
    """A thinking_delta whose content contains a memory-context fence must be
    redacted so that neither 'SECRET' nor 'memory-context' appear in output."""
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    out = sse._sanitize_agent_event(
        {"type": "thinking_delta", "delta": _MEMORY_FENCE_PAYLOAD}
    )
    assert out is not None, "event should not be dropped entirely"
    assert out.get("type") == "thinking_delta"
    delta = out.get("delta", "")
    assert isinstance(delta, str)
    assert "SECRET" not in delta, f"SECRET leaked in thinking_delta: {delta!r}"
    assert "memory-context" not in delta, (
        f"memory-context tag leaked in thinking_delta: {delta!r}"
    )


# ---------------------------------------------------------------------------
# Task 3.1c — privatememory marker is still redacted (pre-existing sanity check)
# ---------------------------------------------------------------------------


def test_text_delta_privatememory_marker_is_redacted():
    """'privatememory' is an existing marker fragment; confirm it still triggers
    redaction so we know the base mechanism works."""
    out = sse._sanitize_agent_event(
        {"type": "text_delta", "delta": _PRIVATE_MEMORY_PAYLOAD}
    )
    assert out is not None
    delta = out.get("delta", "")
    assert delta == "[redacted-private]", (
        f"privatememory marker not redacted: {delta!r}"
    )


def test_thinking_delta_privatememory_marker_is_redacted(monkeypatch):
    """Same sanity check for thinking_delta path."""
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    out = sse._sanitize_agent_event(
        {"type": "thinking_delta", "delta": _PRIVATE_MEMORY_PAYLOAD}
    )
    assert out is not None
    delta = out.get("delta", "")
    assert delta == "[redacted-private]", (
        f"privatememory marker not redacted in thinking_delta: {delta!r}"
    )


# ---------------------------------------------------------------------------
# Task 3.1d — clean deltas are NOT falsely redacted
# ---------------------------------------------------------------------------


def test_clean_text_delta_not_redacted():
    """A text_delta with no private content must pass through unredacted."""
    clean = "Here is a summary of the task results."
    out = sse._sanitize_agent_event({"type": "text_delta", "delta": clean})
    assert out is not None
    assert out.get("delta") == clean, (
        f"clean delta was unexpectedly altered: {out.get('delta')!r}"
    )


def test_clean_thinking_delta_not_redacted(monkeypatch):
    """A thinking_delta with no private content must pass through unredacted."""
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    clean = "Let me reason step by step."
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": clean})
    assert out is not None
    assert out.get("delta") == clean, (
        f"clean thinking delta was unexpectedly altered: {out.get('delta')!r}"
    )


# ---------------------------------------------------------------------------
# Task 3.1e — benign "memory context" prose is NOT falsely redacted
# ---------------------------------------------------------------------------


def test_text_delta_prose_memory_context_not_redacted():
    """The phrase 'memory context' appearing in normal assistant prose must NOT
    be redacted.  Before the regex tightening this would have been a
    false-positive; after tightening it must pass through unchanged."""
    prose = "increase the memory context window of the model"
    out = sse._sanitize_agent_event({"type": "text_delta", "delta": prose})
    assert out is not None, "event should not be dropped entirely"
    assert out.get("delta") == prose, (
        f"benign 'memory context' prose was incorrectly redacted: {out.get('delta')!r}"
    )


def test_thinking_delta_prose_memory_context_not_redacted(monkeypatch):
    """Same false-positive guard for thinking_delta path."""
    monkeypatch.setenv("MAGI_STREAM_THINKING", "1")
    prose = "shared memory context between threads is a common concurrency topic"
    out = sse._sanitize_agent_event({"type": "thinking_delta", "delta": prose})
    assert out is not None, "event should not be dropped entirely"
    assert out.get("delta") == prose, (
        f"benign 'memory context' prose was incorrectly redacted in thinking_delta: {out.get('delta')!r}"
    )
