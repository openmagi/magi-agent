from __future__ import annotations

import pytest

from openmagi_core_agent.adk_bridge.event_adapter import EventProjection
from openmagi_core_agent.transport.sse import InMemorySseWriter
from openmagi_core_agent.transport.sse_buffer import SseEventBuffer


def _text_projection(text: str) -> EventProjection:
    return EventProjection(
        agent_events=[{"type": "text_delta", "delta": text}],
        legacy_deltas=[text],
    )


def _tool_projection(tool_id: str, name: str) -> EventProjection:
    return EventProjection(
        agent_events=[
            {"type": "tool_start", "id": tool_id, "name": name, "input_preview": "{}"},
        ],
    )


def _error_projection(code: str, message: str) -> EventProjection:
    return EventProjection(
        agent_events=[{"type": "error", "code": code, "message": message}],
    )


class TestSseEventBufferAccumulate:
    def test_accumulate_increments_count(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("hello"))
        assert buf.buffered_count == 1
        assert buf.stats.buffered_count == 1

    def test_accumulate_multiple(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("a"))
        buf.accumulate(_text_projection("b"))
        buf.accumulate(_text_projection("c"))
        assert buf.buffered_count == 3

    def test_empty_buffer(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        assert buf.buffered_count == 0
        assert buf.has_flushed_content is False


class TestSseEventBufferFlush:
    def test_flush_writes_to_writer(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("hello"))
        buf.accumulate(_text_projection(" world"))

        writer = InMemorySseWriter()
        writer.start()
        count = buf.flush(writer)

        assert count == 4  # 2 agent events + 2 legacy deltas
        assert buf.buffered_count == 0
        body = writer.body
        assert "hello" in body
        assert " world" in body

    def test_flush_marks_as_flushed(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("x"))
        writer = InMemorySseWriter()
        buf.flush(writer)
        assert buf.has_flushed_content is True

    def test_flush_empty_buffer(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        writer = InMemorySseWriter()
        count = buf.flush(writer)
        assert count == 0
        assert buf.has_flushed_content is False

    def test_flush_stats_tracked(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_tool_projection("tool-1", "Read"))
        writer = InMemorySseWriter()
        buf.flush(writer)
        assert buf.stats.flushed_count == 1


class TestSseEventBufferDiscard:
    def test_discard_clears_buffer(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("a"))
        buf.accumulate(_text_projection("b"))
        count = buf.discard()
        assert count == 2
        assert buf.buffered_count == 0

    def test_discard_does_not_affect_flushed(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("flushed"))
        writer = InMemorySseWriter()
        buf.flush(writer)
        buf.accumulate(_text_projection("discarded"))
        buf.discard()
        assert buf.has_flushed_content is True

    def test_discard_stats_tracked(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("x"))
        buf.discard()
        assert buf.stats.discarded_count == 1

    def test_discard_empty(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        count = buf.discard()
        assert count == 0


class TestSseEventBufferTombstones:
    def test_tombstone_emits_response_clear(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("partial"))
        writer = InMemorySseWriter()
        writer.start()
        buf.flush(writer)

        count = buf.flush_tombstones(writer, reason="stream_withholding_recovery")
        assert count == 1
        body = writer.body
        assert "response_clear" in body
        assert "stream_withholding_recovery" in body

    def test_tombstone_clears_flushed_state(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("x"))
        writer = InMemorySseWriter()
        buf.flush(writer)
        buf.flush_tombstones(writer, reason="recovery")
        assert buf.has_flushed_content is False

    def test_tombstone_noop_when_nothing_flushed(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        writer = InMemorySseWriter()
        count = buf.flush_tombstones(writer, reason="recovery")
        assert count == 0

    def test_tombstone_stats_tracked(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(_text_projection("x"))
        writer = InMemorySseWriter()
        buf.flush(writer)
        buf.flush_tombstones(writer, reason="test")
        assert buf.stats.tombstone_count == 1


class TestSseEventBufferTranscript:
    def test_collect_transcript_entries_from_buffer(self) -> None:
        from openmagi_core_agent.runtime.transcript import AssistantTextEntry

        projection = EventProjection(
            agent_events=[{"type": "text_delta", "delta": "hi"}],
            transcript_entries=[
                AssistantTextEntry(ts=1.0, turn_id="t1", text="hi"),
            ],
        )
        buf = SseEventBuffer(turn_id="t1")
        buf.accumulate(projection)
        entries = buf.collect_transcript_entries()
        assert len(entries) == 1

    def test_collect_empty(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        assert buf.collect_transcript_entries() == []
