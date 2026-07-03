from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Literal

from magi_agent.adk_bridge.event_adapter import EventProjection
from magi_agent.runtime.error_taxonomy import (
    ErrorCategory,
    classify_adk_runtime_failure,
)
from magi_agent.transport.sse_buffer import SseEventBuffer


# I-4: routed through the typed flag registry. The bool migration
# widens the truthy convention from strict ``=="1"`` to the canonical
# ``flag_bool`` set (``1``/``true``/``yes``/``on``), bringing this
# knob in line with every other Magi bool flag.
from magi_agent.config.flags import flag_profile_bool, flag_int  #  # noqa: E402

STREAM_WITHHOLDING_ENABLED = flag_profile_bool("MAGI_STREAM_WITHHOLDING_ENABLED")
STREAM_WITHHOLDING_MAX_RETRIES = flag_int("MAGI_STREAM_WITHHOLDING_MAX_RETRIES") or 2

_RECOVERABLE_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        "context_overflow",
        "truncation",
        "empty_output",
        "provider_error",
        "timeout",
    }
)

WithholdingAction = Literal["buffer", "suppress_and_retry", "emit_error"]


@dataclass(frozen=True)
class StreamWithholdingEvidence:
    category: ErrorCategory
    action: WithholdingAction
    error_code: str
    error_message: str
    retry_attempt: int
    timestamp: float = field(default_factory=time.time)


@dataclass
class StreamWithholdingState:
    enabled: bool = field(default_factory=lambda: STREAM_WITHHOLDING_ENABLED)
    retry_count: int = 0
    max_retries: int = field(default_factory=lambda: STREAM_WITHHOLDING_MAX_RETRIES)
    evidence: list[StreamWithholdingEvidence] = field(default_factory=list)
    suppressed_error_count: int = 0
    total_events_withheld: int = 0


class StreamWithholdingFilter:
    def __init__(
        self,
        *,
        turn_id: str,
        buffer: SseEventBuffer,
        enabled: bool | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._turn_id = turn_id
        self._buffer = buffer
        self._state = StreamWithholdingState(
            enabled=enabled if enabled is not None else STREAM_WITHHOLDING_ENABLED,
            max_retries=max_retries if max_retries is not None else STREAM_WITHHOLDING_MAX_RETRIES,
        )

    @property
    def state(self) -> StreamWithholdingState:
        return self._state

    @property
    def should_retry(self) -> bool:
        return self._state.retry_count < self._state.max_retries

    def inspect_projection(self, projection: EventProjection) -> WithholdingAction:
        if not self._state.enabled:
            return "buffer"

        error_info = _extract_error_from_projection(projection)
        if error_info is None:
            return "buffer"

        code, message = error_info
        classification = classify_adk_runtime_failure(code=code, message=message)

        if classification.category not in _RECOVERABLE_CATEGORIES:
            self._record_evidence(classification.category, code, message, "emit_error")
            return "emit_error"

        if not self.should_retry:
            self._record_evidence(classification.category, code, message, "emit_error")
            return "emit_error"

        self._state.suppressed_error_count += 1
        self._state.total_events_withheld += self._buffer.buffered_count
        self._record_evidence(classification.category, code, message, "suppress_and_retry")
        return "suppress_and_retry"

    def prepare_retry(self) -> int:
        discarded = self._buffer.discard()
        self._state.retry_count += 1
        return discarded

    def _record_evidence(
        self,
        category: ErrorCategory,
        code: str,
        message: str,
        action: WithholdingAction,
    ) -> None:
        self._state.evidence.append(
            StreamWithholdingEvidence(
                category=category,
                action=action,
                error_code=code,
                error_message=message,
                retry_attempt=self._state.retry_count,
            )
        )


def _extract_error_from_projection(
    projection: EventProjection,
) -> tuple[str, str] | None:
    error_result: tuple[str, str] | None = None
    trace_result: tuple[str, str] | None = None
    for event in projection.agent_events:
        event_type = event.get("type")
        if event_type == "error" and error_result is None:
            code = str(event.get("code", ""))
            message = str(event.get("message", ""))
            if code or message:
                error_result = code, message
        elif event_type == "runtime_trace" and trace_result is None:
            severity = event.get("severity")
            if severity == "error":
                detail = str(event.get("detail", ""))
                reason_code = str(event.get("reasonCode", ""))
                if detail or reason_code:
                    trace_result = reason_code or "runtime_error", detail
    return error_result or trace_result


__all__ = [
    "StreamWithholdingEvidence",
    "StreamWithholdingFilter",
    "StreamWithholdingState",
    "WithholdingAction",
]
