from __future__ import annotations

import pytest

from magi_agent.adk_bridge.event_adapter import EventProjection
from magi_agent.transport.sse_buffer import SseEventBuffer
from magi_agent.runtime.stream_withholding import (
    StreamWithholdingFilter,
    _extract_error_from_projection,
)


def _text_projection(text: str) -> EventProjection:
    return EventProjection(
        agent_events=[{"type": "text_delta", "delta": text}],
        legacy_deltas=[text],
    )


def _error_projection(code: str, message: str) -> EventProjection:
    return EventProjection(
        agent_events=[
            {
                "type": "runtime_trace",
                "phase": "terminal_abort",
                "severity": "error",
                "detail": message,
            },
            {"type": "error", "code": code, "message": message},
        ],
    )


def _trace_error_projection(reason_code: str, detail: str) -> EventProjection:
    return EventProjection(
        agent_events=[
            {
                "type": "runtime_trace",
                "severity": "error",
                "reasonCode": reason_code,
                "detail": detail,
            },
        ],
    )


def _make_filter(
    *,
    enabled: bool = True,
    max_retries: int = 2,
    turn_id: str = "t1",
) -> tuple[StreamWithholdingFilter, SseEventBuffer]:
    buf = SseEventBuffer(turn_id=turn_id)
    filt = StreamWithholdingFilter(
        turn_id=turn_id,
        buffer=buf,
        enabled=enabled,
        max_retries=max_retries,
    )
    return filt, buf


class TestExtractError:
    def test_extract_from_error_event(self) -> None:
        proj = _error_projection("http_413", "prompt is too long")
        result = _extract_error_from_projection(proj)
        assert result == ("http_413", "prompt is too long")

    def test_extract_from_runtime_trace(self) -> None:
        proj = _trace_error_projection("context_overflow", "exceeded context")
        result = _extract_error_from_projection(proj)
        assert result == ("context_overflow", "exceeded context")

    def test_no_error_in_text_projection(self) -> None:
        proj = _text_projection("hello")
        result = _extract_error_from_projection(proj)
        assert result is None

    def test_no_error_in_empty_projection(self) -> None:
        proj = EventProjection()
        result = _extract_error_from_projection(proj)
        assert result is None


class TestWithholdingDisabled:
    def test_disabled_always_buffers(self) -> None:
        filt, buf = _make_filter(enabled=False)
        action = filt.inspect_projection(
            _error_projection("http_413", "prompt is too long")
        )
        assert action == "buffer"

    def test_disabled_no_evidence(self) -> None:
        filt, _ = _make_filter(enabled=False)
        filt.inspect_projection(_error_projection("http_413", "too long"))
        assert len(filt.state.evidence) == 0


class TestWithholdingRecoverableErrors:
    def test_context_overflow_triggers_suppress(self) -> None:
        filt, buf = _make_filter()
        buf.accumulate(_text_projection("partial"))
        action = filt.inspect_projection(
            _error_projection("http_413", "prompt is too long")
        )
        assert action == "suppress_and_retry"

    def test_truncation_triggers_suppress(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("max_tokens", "output truncated")
        )
        assert action == "suppress_and_retry"

    def test_empty_output_triggers_suppress(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("empty_response", "no output")
        )
        assert action == "suppress_and_retry"

    def test_provider_error_triggers_suppress(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("http_429", "rate limited")
        )
        assert action == "suppress_and_retry"

    def test_timeout_triggers_suppress(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("timeout", "timed out")
        )
        assert action == "suppress_and_retry"


class TestWithholdingNonRecoverableErrors:
    def test_validator_block_emits_error(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("validator_block", "blocked by verifier")
        )
        assert action == "emit_error"

    def test_budget_exceeded_emits_error(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("budget_exceeded", "no credits")
        )
        assert action == "emit_error"

    def test_kill_switch_emits_error(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("kill_switch", "disabled")
        )
        assert action == "emit_error"

    def test_redaction_failure_emits_error(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(
            _error_projection("redaction_failure", "redaction failed")
        )
        assert action == "emit_error"


class TestWithholdingRetryExhaustion:
    def test_exhaust_retries_then_emit(self) -> None:
        filt, buf = _make_filter(max_retries=1)
        action1 = filt.inspect_projection(
            _error_projection("http_413", "too long")
        )
        assert action1 == "suppress_and_retry"
        filt.prepare_retry()

        action2 = filt.inspect_projection(
            _error_projection("http_413", "still too long")
        )
        assert action2 == "emit_error"

    def test_retry_count_increments(self) -> None:
        filt, _ = _make_filter(max_retries=3)
        filt.inspect_projection(_error_projection("timeout", "timed out"))
        filt.prepare_retry()
        assert filt.state.retry_count == 1
        filt.inspect_projection(_error_projection("timeout", "timed out again"))
        filt.prepare_retry()
        assert filt.state.retry_count == 2

    def test_should_retry_false_when_exhausted(self) -> None:
        filt, _ = _make_filter(max_retries=0)
        assert filt.should_retry is False


class TestPrepareRetry:
    def test_prepare_retry_discards_buffer(self) -> None:
        filt, buf = _make_filter()
        buf.accumulate(_text_projection("a"))
        buf.accumulate(_text_projection("b"))
        discarded = filt.prepare_retry()
        assert discarded == 2
        assert buf.buffered_count == 0

    def test_prepare_retry_increments_count(self) -> None:
        filt, _ = _make_filter()
        filt.prepare_retry()
        assert filt.state.retry_count == 1


class TestEvidence:
    def test_evidence_recorded_on_suppress(self) -> None:
        filt, _ = _make_filter()
        filt.inspect_projection(_error_projection("http_413", "too long"))
        assert len(filt.state.evidence) == 1
        ev = filt.state.evidence[0]
        assert ev.category == "context_overflow"
        assert ev.action == "suppress_and_retry"
        assert ev.error_code == "http_413"

    def test_evidence_recorded_on_emit(self) -> None:
        filt, _ = _make_filter()
        filt.inspect_projection(_error_projection("budget_exceeded", "no credits"))
        assert len(filt.state.evidence) == 1
        assert filt.state.evidence[0].action == "emit_error"

    def test_suppressed_error_count(self) -> None:
        filt, _ = _make_filter(max_retries=3)
        filt.inspect_projection(_error_projection("timeout", "t1"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("timeout", "t2"))
        filt.prepare_retry()
        assert filt.state.suppressed_error_count == 2

    def test_no_text_events_still_buffers(self) -> None:
        filt, _ = _make_filter()
        action = filt.inspect_projection(_text_projection("normal text"))
        assert action == "buffer"
        assert len(filt.state.evidence) == 0
