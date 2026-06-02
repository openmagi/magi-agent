from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Literal

from magi_agent.runtime.error_taxonomy import ErrorCategory
from magi_agent.runtime.stream_withholding import (
    StreamWithholdingEvidence,
    StreamWithholdingFilter,
)
from magi_agent.transport.sse import InMemorySseWriter
from magi_agent.transport.sse_buffer import SseEventBuffer


STREAM_FALLBACK_MODEL = os.environ.get(
    "MAGI_STREAM_FALLBACK_MODEL", "claude-haiku-4-5-20251001"
)

_FALLBACK_TRIGGERING_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        "provider_error",
        "timeout",
        "context_overflow",
    }
)

FallbackDecision = Literal["no_fallback", "switch_model", "fail"]


@dataclass(frozen=True)
class StreamFallbackEvidence:
    original_model: str
    fallback_model: str
    reason: str
    category: ErrorCategory
    timestamp: float = field(default_factory=time.time)


@dataclass
class StreamFallbackState:
    attempted: bool = False
    succeeded: bool = False
    original_model: str | None = None
    fallback_model: str = field(default_factory=lambda: STREAM_FALLBACK_MODEL)
    evidence: list[StreamFallbackEvidence] = field(default_factory=list)


class StreamFallbackController:
    def __init__(
        self,
        *,
        turn_id: str,
        buffer: SseEventBuffer,
        withholding_filter: StreamWithholdingFilter,
        original_model: str,
        fallback_model: str | None = None,
    ) -> None:
        self._turn_id = turn_id
        self._buffer = buffer
        self._withholding_filter = withholding_filter
        self._state = StreamFallbackState(
            original_model=original_model,
            fallback_model=fallback_model or STREAM_FALLBACK_MODEL,
        )

    @property
    def state(self) -> StreamFallbackState:
        return self._state

    @property
    def fallback_model(self) -> str:
        return self._state.fallback_model

    def evaluate_fallback(self) -> FallbackDecision:
        if self._state.attempted:
            return "fail"

        if not self._withholding_filter.state.evidence:
            return "no_fallback"

        last_evidence = self._withholding_filter.state.evidence[-1]
        if last_evidence.category not in _FALLBACK_TRIGGERING_CATEGORIES:
            return "no_fallback"

        if self._withholding_filter.should_retry:
            return "no_fallback"

        return "switch_model"

    def prepare_fallback(self, writer: InMemorySseWriter) -> str:
        self._state.attempted = True

        self._buffer.discard()
        tombstone_count = self._buffer.flush_tombstones(
            writer, reason="stream_fallback_model_switch"
        )

        model_fallback_event: dict[str, object] = {
            "type": "model_fallback",
            "turnId": self._turn_id,
            "fromModel": self._state.original_model or "unknown",
            "toModel": self._state.fallback_model,
            "reason": "stream_fallback_after_withholding_exhausted",
        }
        writer.agent(model_fallback_event)

        reason = _build_fallback_reason(self._withholding_filter.state.evidence)
        self._state.evidence.append(
            StreamFallbackEvidence(
                original_model=self._state.original_model or "unknown",
                fallback_model=self._state.fallback_model,
                reason=reason,
                category=self._withholding_filter.state.evidence[-1].category
                if self._withholding_filter.state.evidence
                else "provider_error",
            )
        )

        return self._state.fallback_model

    def mark_succeeded(self) -> None:
        self._state.succeeded = True


def _build_fallback_reason(evidence: list[StreamWithholdingEvidence]) -> str:
    if not evidence:
        return "unknown_error"
    categories = {e.category for e in evidence}
    return f"withholding_exhausted:{','.join(sorted(categories))}"


__all__ = [
    "FallbackDecision",
    "StreamFallbackController",
    "StreamFallbackEvidence",
    "StreamFallbackState",
]
