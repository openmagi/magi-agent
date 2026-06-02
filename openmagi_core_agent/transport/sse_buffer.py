from __future__ import annotations

import time
from dataclasses import dataclass, field

from openmagi_core_agent.adk_bridge.event_adapter import EventProjection
from openmagi_core_agent.transport.sse import InMemorySseWriter


@dataclass
class BufferStats:
    buffered_count: int = 0
    flushed_count: int = 0
    discarded_count: int = 0
    tombstone_count: int = 0


class SseEventBuffer:
    def __init__(self, *, turn_id: str) -> None:
        self._turn_id = turn_id
        self._projections: list[EventProjection] = []
        self._flushed_projections: list[EventProjection] = []
        self._stats = BufferStats()

    @property
    def stats(self) -> BufferStats:
        return self._stats

    @property
    def buffered_count(self) -> int:
        return len(self._projections)

    @property
    def has_flushed_content(self) -> bool:
        return len(self._flushed_projections) > 0

    def accumulate(self, projection: EventProjection) -> None:
        self._projections.append(projection)
        self._stats.buffered_count += 1

    def flush(self, writer: InMemorySseWriter) -> int:
        count = 0
        for projection in self._projections:
            for event in projection.agent_events:
                writer.agent(event)
                count += 1
            for delta in projection.legacy_deltas:
                writer.legacy_delta(delta)
                count += 1
        self._flushed_projections.extend(self._projections)
        self._projections.clear()
        self._stats.flushed_count += count
        return count

    def discard(self) -> int:
        count = len(self._projections)
        self._projections.clear()
        self._stats.discarded_count += count
        return count

    def flush_tombstones(self, writer: InMemorySseWriter, *, reason: str) -> int:
        if not self._flushed_projections:
            return 0
        tombstone: dict[str, object] = {
            "type": "response_clear",
            "turnId": self._turn_id,
            "reason": reason,
        }
        writer.agent(tombstone)
        self._flushed_projections.clear()
        self._stats.tombstone_count += 1
        return 1

    def collect_transcript_entries(self) -> list[object]:
        entries: list[object] = []
        for projection in self._projections:
            entries.extend(projection.transcript_entries)
        return entries


__all__ = [
    "BufferStats",
    "SseEventBuffer",
]
