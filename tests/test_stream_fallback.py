from __future__ import annotations

import pytest

from magi_agent.adk_bridge.event_adapter import EventProjection
from magi_agent.transport.sse import InMemorySseWriter
from magi_agent.transport.sse_buffer import SseEventBuffer
from magi_agent.runtime.stream_withholding import StreamWithholdingFilter
from magi_agent.runtime.stream_fallback import (
    StreamFallbackController,
)


def _text_projection(text: str) -> EventProjection:
    return EventProjection(
        agent_events=[{"type": "text_delta", "delta": text}],
        legacy_deltas=[text],
    )


def _error_projection(code: str, message: str) -> EventProjection:
    return EventProjection(
        agent_events=[
            {"type": "error", "code": code, "message": message},
        ],
    )


def _make_controller(
    *,
    original_model: str = "claude-sonnet-4-6",
    fallback_model: str = "claude-haiku-4-5-20251001",
    max_retries: int = 1,
) -> tuple[StreamFallbackController, SseEventBuffer, StreamWithholdingFilter]:
    buf = SseEventBuffer(turn_id="t1")
    filt = StreamWithholdingFilter(
        turn_id="t1", buffer=buf, enabled=True, max_retries=max_retries,
    )
    ctrl = StreamFallbackController(
        turn_id="t1",
        buffer=buf,
        withholding_filter=filt,
        original_model=original_model,
        fallback_model=fallback_model,
    )
    return ctrl, buf, filt


class TestFallbackDecisionNoFallback:
    def test_no_errors_no_fallback(self) -> None:
        ctrl, _, _ = _make_controller()
        assert ctrl.evaluate_fallback() == "no_fallback"

    def test_retries_remaining_no_fallback(self) -> None:
        ctrl, buf, filt = _make_controller(max_retries=2)
        buf.accumulate(_text_projection("partial"))
        filt.inspect_projection(_error_projection("http_429", "rate limited"))
        filt.prepare_retry()
        assert ctrl.evaluate_fallback() == "no_fallback"

    def test_non_triggering_category_no_fallback(self) -> None:
        ctrl, _, filt = _make_controller(max_retries=0)
        filt.inspect_projection(_error_projection("empty_response", "no output"))
        assert ctrl.evaluate_fallback() == "no_fallback"


class TestFallbackDecisionSwitchModel:
    def test_provider_error_exhausted_triggers_switch(self) -> None:
        ctrl, _, filt = _make_controller(max_retries=1)
        filt.inspect_projection(_error_projection("http_429", "rate limited"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("http_429", "still rate limited"))
        assert ctrl.evaluate_fallback() == "switch_model"

    def test_context_overflow_exhausted_triggers_switch(self) -> None:
        ctrl, _, filt = _make_controller(max_retries=1)
        filt.inspect_projection(_error_projection("http_413", "prompt too long"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("http_413", "still too long"))
        assert ctrl.evaluate_fallback() == "switch_model"

    def test_timeout_exhausted_triggers_switch(self) -> None:
        ctrl, _, filt = _make_controller(max_retries=1)
        filt.inspect_projection(_error_projection("timeout", "timed out"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("timeout", "again"))
        assert ctrl.evaluate_fallback() == "switch_model"


class TestFallbackDecisionFail:
    def test_already_attempted_returns_fail(self) -> None:
        ctrl, buf, filt = _make_controller(max_retries=1)
        filt.inspect_projection(_error_projection("http_429", "rate limited"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("http_429", "still"))
        writer = InMemorySseWriter()
        writer.start()
        ctrl.prepare_fallback(writer)
        assert ctrl.evaluate_fallback() == "fail"


class TestPrepareFallback:
    def test_emits_tombstone_and_model_fallback(self) -> None:
        ctrl, buf, filt = _make_controller(max_retries=1)
        buf.accumulate(_text_projection("partial content"))
        writer = InMemorySseWriter()
        writer.start()
        buf.flush(writer)

        filt.inspect_projection(_error_projection("http_429", "rate limited"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("http_429", "still"))

        model = ctrl.prepare_fallback(writer)
        assert model == "claude-haiku-4-5-20251001"

        body = writer.body
        assert "response_clear" in body
        assert "stream_fallback_model_switch" in body
        assert "model_fallback" in body
        assert "claude-sonnet-4-6" in body
        assert "claude-haiku-4-5-20251001" in body

    def test_discard_pending_events(self) -> None:
        ctrl, buf, filt = _make_controller(max_retries=1)
        buf.accumulate(_text_projection("pending"))
        filt.inspect_projection(_error_projection("timeout", "t1"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("timeout", "t2"))

        writer = InMemorySseWriter()
        ctrl.prepare_fallback(writer)
        assert buf.buffered_count == 0

    def test_marks_attempted(self) -> None:
        ctrl, _, filt = _make_controller(max_retries=1)
        filt.inspect_projection(_error_projection("http_429", "rl"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("http_429", "rl"))
        writer = InMemorySseWriter()
        ctrl.prepare_fallback(writer)
        assert ctrl.state.attempted is True


class TestFallbackEvidence:
    def test_evidence_recorded(self) -> None:
        ctrl, _, filt = _make_controller(max_retries=1)
        filt.inspect_projection(_error_projection("http_413", "too long"))
        filt.prepare_retry()
        filt.inspect_projection(_error_projection("http_413", "still"))
        writer = InMemorySseWriter()
        ctrl.prepare_fallback(writer)

        assert len(ctrl.state.evidence) == 1
        ev = ctrl.state.evidence[0]
        assert ev.original_model == "claude-sonnet-4-6"
        assert ev.fallback_model == "claude-haiku-4-5-20251001"
        assert "context_overflow" in ev.reason
        assert ev.category == "context_overflow"

    def test_mark_succeeded(self) -> None:
        ctrl, _, _ = _make_controller()
        assert ctrl.state.succeeded is False
        ctrl.mark_succeeded()
        assert ctrl.state.succeeded is True


class TestFallbackModelConfig:
    def test_custom_fallback_model(self) -> None:
        ctrl, _, _ = _make_controller(fallback_model="gemini-2.5-flash")
        assert ctrl.fallback_model == "gemini-2.5-flash"

    def test_default_fallback_model(self) -> None:
        buf = SseEventBuffer(turn_id="t1")
        filt = StreamWithholdingFilter(turn_id="t1", buffer=buf, enabled=True)
        ctrl = StreamFallbackController(
            turn_id="t1",
            buffer=buf,
            withholding_filter=filt,
            original_model="claude-sonnet-4-6",
        )
        assert ctrl.fallback_model == "claude-haiku-4-5-20251001"


class TestIntegrationWithholdThenFallback:
    def test_full_flow_withhold_retry_fallback(self) -> None:
        ctrl, buf, filt = _make_controller(max_retries=2)
        writer = InMemorySseWriter()
        writer.start()

        buf.accumulate(_text_projection("attempt 1 text"))
        action1 = filt.inspect_projection(_error_projection("http_429", "rl"))
        assert action1 == "suppress_and_retry"
        filt.prepare_retry()

        buf.accumulate(_text_projection("attempt 2 text"))
        action2 = filt.inspect_projection(_error_projection("http_429", "rl"))
        assert action2 == "suppress_and_retry"
        filt.prepare_retry()

        buf.accumulate(_text_projection("attempt 3 text"))
        action3 = filt.inspect_projection(_error_projection("http_429", "rl"))
        assert action3 == "emit_error"

        decision = ctrl.evaluate_fallback()
        assert decision == "switch_model"

        model = ctrl.prepare_fallback(writer)
        assert model == "claude-haiku-4-5-20251001"

        buf.accumulate(_text_projection("fallback response"))
        buf.flush(writer)
        ctrl.mark_succeeded()

        assert ctrl.state.succeeded is True
        assert filt.state.suppressed_error_count == 2
        body = writer.body
        assert "fallback response" in body
        assert "model_fallback" in body
