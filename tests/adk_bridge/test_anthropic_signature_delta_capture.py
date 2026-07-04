"""Tests for the SignatureDelta streaming-capture companion fix.

ADK 1.33.0's ``AnthropicLlm._generate_content_streaming`` handles
``ThinkingDelta`` / ``TextDelta`` / ``InputJSONDelta`` but silently drops
``anthropic.types.SignatureDelta`` (present in anthropic 0.116.0). A streamed
thinking block therefore aggregates to ``Part(text=<thinking>, thought=True)``
with NO signature. When the thinking text is empty (signature-only interleaved
thinking block), that collapses to the empty-thinking shape ADK's
``part_to_message_block`` raises on the next turn.

The cache-aware mixin overrides ``_generate_content_streaming`` to add a
``SignatureDelta`` branch (accumulate the signature) plus an unknown-block
warning. This test drives a synthetic event stream through
``generate_content_async(stream=True)`` and asserts the final aggregated part
carries ``thought_signature``, and that an empty-thinking + signature stream
yields a part that is convertible by ADK (no fall-through raise).

Needs the optional ``anthropic`` package; skips otherwise.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest


def _model_module():
    return importlib.import_module("magi_agent.adk_bridge.anthropic_cache_model")


def _build_llm_request():
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types as genai_types

    contents = [
        genai_types.Content(
            role="user", parts=[genai_types.Part.from_text(text="hi")]
        )
    ]
    config = genai_types.GenerateContentConfig(system_instruction="sys")
    return LlmRequest(model="claude-sonnet-5", contents=contents, config=config)


class _FakeStream:
    """Async-iterable stream that yields a fixed list of anthropic events."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __aiter__(self):
        async def _gen():
            for event in self._events:
                yield event

        return _gen()


class _FakeMessages:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def create(self, **kwargs: Any):
        assert kwargs.get("stream") is True
        return _FakeStream(self._events)


class _FakeClient:
    def __init__(self, events: list[Any]) -> None:
        self.messages = _FakeMessages(events)


def _thinking_stream_events(thinking_text: str, signature: str) -> list[Any]:
    from anthropic import types as at

    message = at.Message(
        id="msg",
        type="message",
        role="assistant",
        model="claude-sonnet-5",
        content=[],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=at.Usage(input_tokens=3, output_tokens=5),
    )
    return [
        at.RawMessageStartEvent(type="message_start", message=message),
        at.RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=at.ThinkingBlock(
                type="thinking", thinking="", signature=""
            ),
        ),
        at.RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=at.ThinkingDelta(type="thinking_delta", thinking=thinking_text),
        ),
        at.RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=at.SignatureDelta(type="signature_delta", signature=signature),
        ),
        at.RawContentBlockStopEvent(type="content_block_stop", index=0),
    ]


def _drive_stream(model, llm_request) -> list[Any]:
    responses: list[Any] = []

    async def _run() -> None:
        async for resp in model.generate_content_async(llm_request, stream=True):
            responses.append(resp)

    asyncio.run(_run())
    return responses


def _final_parts(responses: list[Any]) -> list[Any]:
    final = [r for r in responses if getattr(r, "partial", None) is False]
    assert final, "expected a final (partial=False) aggregated response"
    return list(final[-1].content.parts)


class TestSignatureDeltaCapture:
    def test_signature_accumulated_onto_final_thinking_part(self) -> None:
        pytest.importorskip("anthropic")
        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-5")
        model = cls(model="claude-sonnet-5")
        events = _thinking_stream_events("some reasoning", "SIG123")
        object.__setattr__(model, "_anthropic_client", _FakeClient(events))

        responses = _drive_stream(model, _build_llm_request())
        parts = _final_parts(responses)

        thinking_parts = [p for p in parts if getattr(p, "thought", None)]
        assert len(thinking_parts) == 1
        part = thinking_parts[0]
        assert part.text == "some reasoning"
        # The fix: the dropped-by-ADK signature is now captured.
        assert part.thought_signature == b"SIG123"

    def test_empty_thinking_plus_signature_is_convertible(self) -> None:
        """Signature-only thinking block -> Shape C (redacted_thinking), no raise.

        Without the fix this streamed block aggregates to Part(text="",
        thought=True) with no signature (Shape B), which ADK's
        part_to_message_block raises on. With SignatureDelta captured, the part
        becomes Part(text="", thought=True, thought_signature=...), which ADK
        converts to a redacted_thinking block.
        """
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import part_to_message_block

        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-5")
        model = cls(model="claude-sonnet-5")
        events = _thinking_stream_events("", "SIGONLY")
        object.__setattr__(model, "_anthropic_client", _FakeClient(events))

        responses = _drive_stream(model, _build_llm_request())
        parts = _final_parts(responses)
        thinking_parts = [p for p in parts if getattr(p, "thought", None)]
        assert len(thinking_parts) == 1
        part = thinking_parts[0]
        assert part.thought_signature == b"SIGONLY"
        # ADK now converts it instead of raising.
        block = part_to_message_block(part)
        assert block["type"] == "redacted_thinking"
        assert block["data"] == "SIGONLY"

    def test_unknown_content_block_start_warns(self, caplog) -> None:
        pytest.importorskip("anthropic")
        from anthropic import types as at

        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-5")
        model = cls(model="claude-sonnet-5")
        message = at.Message(
            id="msg",
            type="message",
            role="assistant",
            model="claude-sonnet-5",
            content=[],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=at.Usage(input_tokens=1, output_tokens=1),
        )

        class _Weird:
            """An unknown content block type the override does not recognise."""

        events = [
            at.RawMessageStartEvent(type="message_start", message=message),
            at.RawContentBlockStartEvent.model_construct(
                type="content_block_start", index=0, content_block=_Weird()
            ),
        ]
        object.__setattr__(model, "_anthropic_client", _FakeClient(events))
        with caplog.at_level("WARNING"):
            _drive_stream(model, _build_llm_request())
        assert any(
            "unknown_content_block" in r.getMessage() for r in caplog.records
        )

    def test_plain_text_stream_unchanged(self) -> None:
        """A non-thinking text stream aggregates to a single text part."""
        pytest.importorskip("anthropic")
        from anthropic import types as at

        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-5")
        model = cls(model="claude-sonnet-5")
        message = at.Message(
            id="msg",
            type="message",
            role="assistant",
            model="claude-sonnet-5",
            content=[],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=at.Usage(input_tokens=2, output_tokens=2),
        )
        events = [
            at.RawMessageStartEvent(type="message_start", message=message),
            at.RawContentBlockStartEvent(
                type="content_block_start",
                index=0,
                content_block=at.TextBlock(type="text", text="", citations=None),
            ),
            at.RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=at.TextDelta(type="text_delta", text="hello world"),
            ),
        ]
        object.__setattr__(model, "_anthropic_client", _FakeClient(events))
        responses = _drive_stream(model, _build_llm_request())
        parts = _final_parts(responses)
        assert len(parts) == 1
        assert parts[0].text == "hello world"
        assert not getattr(parts[0], "thought", None)
